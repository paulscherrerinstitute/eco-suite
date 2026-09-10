"""Tests for the namespace-hosted status server (eco/status_server/).

Everything here runs against a fake namespace object that mimics the parts
of eco.utilities.config.Namespace the store actually uses - no EPICS, no
eco.bernina import - so the state machine, the REST contract and the daq
client's fallback behaviour are testable without the beamline.
"""

import json
import sys
import threading
import time
import types

import pytest

from eco.status_server import namespace_store as ns_store
from eco.status_server.config import NamespaceServerConfig
from eco.status_server.namespace_server import create_namespace_app
from eco.status_server.namespace_store import NamespaceMonitorStore, NotReady
from eco.status_server.storage import write_status_snapshot


# --------------------------------------------------------------------------
# fake namespace


class FakeAlias:
    def __init__(self, full_name, channel=None):
        self._full_name = full_name
        self.channel = channel

    def get_full_name(self, base=None, joiner="."):
        return self._full_name


class FakeDetector:
    def __init__(self, full_name, value, channel=None):
        self.alias = FakeAlias(full_name, channel)
        self.name = full_name
        self._value = value

    def get_current_value(self):
        return self._value


class FakeStatusCollection:
    def __init__(self, items):
        self._items = items

    def get_list(self, selection=None, **kwargs):
        return list(self._items)


class FakeNamespaceAlias:
    """Enough of eco.aliases.aliases.Alias.get_all() for the /aliases route:
    a flat list built from whatever detectors this fake namespace holds,
    same shape as the real Namespace.alias.get_all()."""

    def __init__(self, detectors):
        self._detectors = detectors

    def get_all(self, joiner=".", channeltypes=None):
        out = []
        for d in self._detectors:
            entry = {
                "alias": d.alias.get_full_name(joiner=joiner),
                "channel": d.alias.channel,
                "channeltype": "CA",
            }
            if (not channeltypes) or (entry["channeltype"] in channeltypes):
                out.append(entry)
        return out


class FakeNamespace:
    """Enough of Namespace for NamespaceMonitorStore."""

    def __init__(self, names=("a", "b", "c"), required=("a", "b"), init_delay=0.0,
                 fail=()):
        self.all_names = set(names)
        self.initialized_names = set()
        self.failed_names = set()
        self.lazy_names = set(names)
        self.failed_items_excpetion = {}
        self._required = list(required)
        self._init_delay = init_delay
        self._fail = set(fail)
        self.init_calls = []
        self.reinit_calls = []
        self.status_collection = FakeStatusCollection(
            [
                FakeDetector(f"fake.{n}", n.upper(), channel=f"PV:{n.upper()}")
                for n in sorted(names)
            ]
        )
        self.alias = FakeNamespaceAlias(self.status_collection._items)

    def required_names(self):
        return list(self._required)

    def init_all(self, required_only=True, exclude_names=(), max_workers=1,
                 background=False, silent=True, **kwargs):
        self.init_calls.append(
            {"exclude_names": sorted(exclude_names), "max_workers": max_workers,
             "background": background}
        )
        targets = self.all_names - set(exclude_names)
        for name in sorted(targets):
            if self._init_delay:
                time.sleep(self._init_delay)
            if name in self._fail:
                self.failed_names.add(name)
                self.failed_items_excpetion[name] = ValueError(f"{name} boom")
            else:
                self.initialized_names.add(name)
            self.lazy_names.discard(name)

    def reinitialize(self, *names, verbose=True, raise_errors=False,
                     reload_modules=False):
        self.reinit_calls.append({"names": list(names), "reload": reload_modules})
        for name in names:
            self.failed_names.discard(name)
            self.failed_items_excpetion.pop(name, None)
            self.lazy_names.add(name)
        return {n: True for n in names}

    def get_status(self, base=None, raise_on_incomplete=True, threads=True,
                   max_workers=20):
        dets = self.status_collection.get_list()
        return {
            "status": {d.alias.get_full_name(): d.get_current_value() for d in dets},
            "status_channels": {
                d.alias.get_full_name(): d.alias.channel for d in dets
            },
            "status_times": {d.alias.get_full_name(): 0.001 for d in dets},
            "selections": {},
        }


@pytest.fixture
def fake_module(request):
    """Register a throwaway module holding a FakeNamespace, so the store's
    importlib.import_module(module_name) path is exercised for real."""
    made = []

    def _make(name="eco_fake_ns_module", **kwargs):
        mod = types.ModuleType(name)
        mod.namespace = FakeNamespace(**kwargs)
        sys.modules[name] = mod
        made.append(name)
        return name, mod

    yield _make
    for name in made:
        sys.modules.pop(name, None)


def _store(module_name, **kwargs):
    kwargs.setdefault("init_required_only", False)
    return NamespaceMonitorStore(module_name, **kwargs)


# --------------------------------------------------------------------------
# store lifecycle


def test_store_construction_does_not_block_and_reaches_ready(fake_module):
    name, mod = fake_module()
    store = _store(name)
    assert store.wait_ready(timeout=10)
    assert store.state == ns_store.READY
    assert store.generation == 1
    assert mod.namespace.initialized_names == {"a", "b", "c"}


def test_store_reports_progress_while_initializing(fake_module):
    name, mod = fake_module(names=[f"n{i}" for i in range(10)], init_delay=0.05)
    store = _store(name)
    seen_initializing = False
    for _ in range(200):
        rep = store.connection_report()
        if rep["state"] == ns_store.INITIALIZING:
            seen_initializing = True
            assert rep["ready"] is False
            assert rep["n_target_names"] == 10
            break
        if rep["state"] == ns_store.READY:
            break
        time.sleep(0.01)
    assert seen_initializing, "never observed the initializing state"
    assert store.wait_ready(timeout=20)
    assert store.connection_report()["n_initialized"] == 10


def test_snapshot_before_ready_raises_not_ready(fake_module):
    name, _ = fake_module(names=[f"n{i}" for i in range(10)], init_delay=0.05)
    store = _store(name)
    with pytest.raises(NotReady) as info:
        store.snapshot()
    assert info.value.state in (ns_store.IMPORTING, ns_store.INITIALIZING)
    assert store.wait_ready(timeout=20)


def test_failed_import_leaves_store_failed_but_alive(fake_module):
    store = NamespaceMonitorStore("eco_module_that_does_not_exist", start=True)
    for _ in range(200):
        if store.state == ns_store.FAILED:
            break
        time.sleep(0.01)
    assert store.state == ns_store.FAILED
    assert "ModuleNotFoundError" in store.last_error
    # /health-equivalent still answers rather than the process dying
    assert store.connection_report()["state"] == ns_store.FAILED


def test_snapshot_matches_get_status_shape(fake_module):
    name, _ = fake_module()
    store = _store(name)
    assert store.wait_ready(timeout=10)
    snap = store.snapshot()
    assert snap["status"] == {"fake.a": "A", "fake.b": "B", "fake.c": "C"}
    assert snap["status_channels"]["fake.a"] == "PV:A"
    assert set(("status", "status_channels", "status_times", "selections")) <= set(snap)
    assert snap["mode"] == "get_status"


def test_target_names_from_required_without_writing_required_names(fake_module):
    name, mod = fake_module()
    store = NamespaceMonitorStore(name, init_required_only=True)
    assert store.wait_ready(timeout=10)
    assert store._target_names == {"a", "b"}
    assert mod.namespace.init_calls[0]["exclude_names"] == ["c"]
    # the shared required_names() list itself must be untouched
    assert mod.namespace.required_names() == ["a", "b"]


def test_explicit_names_and_exclusions(fake_module):
    name, mod = fake_module()
    store = NamespaceMonitorStore(
        name, names=["a", "b", "c", "nonexistent"], exclude_names=["b"]
    )
    assert store.wait_ready(timeout=10)
    assert store._target_names == {"a", "c"}
    assert mod.namespace.init_calls[0]["exclude_names"] == ["b"]


def test_init_workers_are_passed_through(fake_module):
    name, mod = fake_module()
    store = _store(name, init_workers=6)
    assert store.wait_ready(timeout=10)
    assert mod.namespace.init_calls[0]["max_workers"] == 6
    assert mod.namespace.init_calls[0]["background"] is False


# --------------------------------------------------------------------------
# reinit


def test_reinit_failed_only_touches_failed_names(fake_module):
    name, mod = fake_module(fail=["b"])
    store = _store(name)
    assert store.wait_ready(timeout=10)
    assert store.connection_report()["failed_names"] == ["b"]
    store.start_reinit(mode="failed")
    assert store.wait_ready(timeout=10)
    assert mod.namespace.reinit_calls[-1]["names"] == ["b"]
    assert store.generation == 2


def test_reinit_full_rebuilds_target_set(fake_module):
    name, mod = fake_module()
    store = _store(name)
    assert store.wait_ready(timeout=10)
    store.start_reinit(mode="full", reload_modules=True)
    assert store.wait_ready(timeout=10)
    assert mod.namespace.reinit_calls[-1]["names"] == ["a", "b", "c"]
    assert mod.namespace.reinit_calls[-1]["reload"] is True


def test_reinit_rejects_unknown_mode(fake_module):
    name, _ = fake_module()
    store = _store(name)
    assert store.wait_ready(timeout=10)
    store.start_reinit(mode="nonsense")
    # the worker records the failure and returns to a usable state rather
    # than leaving the store stuck busy
    for _ in range(200):
        if store.state == ns_store.READY:
            break
        time.sleep(0.01)
    assert store.state == ns_store.READY
    assert "unknown reinit mode" in store.last_error


def test_reinit_while_busy_raises(fake_module):
    name, _ = fake_module(names=[f"n{i}" for i in range(10)], init_delay=0.05)
    store = _store(name)
    with pytest.raises(NotReady):
        store.start_reinit(mode="failed")
    assert store.wait_ready(timeout=20)


def test_reimport_builds_a_new_namespace_object(fake_module, monkeypatch):
    name, mod = fake_module()
    store = _store(name)
    assert store.wait_ready(timeout=10)
    first = store.namespace

    replacement = FakeNamespace()
    # Patch the whole step: the real one purges every eco.* module from
    # sys.modules, which would leave the rest of this pytest session with a
    # second copy of eco's classes (see purge_eco_modules' docstring - it is
    # tested separately below, against a throwaway module table).
    def _reimport():
        store.namespace = replacement
        store._target_names = store._resolve_target_names(replacement)

    monkeypatch.setattr(store, "_reimport_namespace", _reimport)
    store.start_reinit(mode="reimport")
    assert store.wait_ready(timeout=10)
    assert store.namespace is replacement
    assert store.namespace is not first
    assert store.generation == 2
    assert replacement.initialized_names == {"a", "b", "c"}


def test_new_names_widen_the_target_set_on_reinit(fake_module):
    name, mod = fake_module()
    store = NamespaceMonitorStore(name, names=["a"])
    assert store.wait_ready(timeout=10)
    assert store._target_names == {"a"}
    store.start_reinit(mode="init", new_names=["a", "b", "c"])
    assert store.wait_ready(timeout=10)
    assert store._target_names == {"a", "b", "c"}
    assert mod.namespace.initialized_names == {"a", "b", "c"}


# --------------------------------------------------------------------------
# REST layer


@pytest.fixture
def app_and_store(fake_module):
    name, mod = fake_module()
    store = _store(name)
    assert store.wait_ready(timeout=10)
    config = NamespaceServerConfig(module_name=name)
    app = create_namespace_app(config, store=store)
    app.config["TESTING"] = True
    return app, store, mod


def test_health_reports_ready_state(app_and_store):
    app, store, _ = app_and_store
    body = app.test_client().get("/health").get_json()
    assert body["ready"] is True
    assert body["status"] == "ok"
    assert body["state"] == "ready"
    assert body["generation"] == 1
    assert body["pid"] > 0
    assert body["instance_id"]


def test_snapshot_endpoint_returns_status(app_and_store):
    app, _, _ = app_and_store
    body = app.test_client().post("/status/snapshot", json={}).get_json()
    assert body["status"]["fake.a"] == "A"
    assert body["namespace"]


def test_snapshot_endpoint_503_while_not_ready(fake_module):
    name, _ = fake_module(names=[f"n{i}" for i in range(10)], init_delay=0.05)
    store = _store(name)
    app = create_namespace_app(NamespaceServerConfig(module_name=name), store=store)
    resp = app.test_client().post("/status/snapshot", json={})
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["state"] in (ns_store.IMPORTING, ns_store.INITIALIZING)
    assert store.wait_ready(timeout=20)


def test_snapshot_save_writes_status_file(app_and_store, tmp_path):
    app, _, _ = app_and_store
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    body = app.test_client().post(
        "/status/snapshot",
        json={"save": True, "pgroup": "p12345", "run_number": 7},
    ).get_json()
    path = tmp_path / "p12345" / "run0007" / "aux" / "status.json"
    assert body["saved_to"] == str(path)
    written = json.loads(path.read_text())
    assert set(written) == {"status_run_start"}
    assert written["status_run_start"]["status"]["fake.a"] == "A"
    # server-only bookkeeping must not leak into the on-disk file
    assert "generation" not in written["status_run_start"]
    assert "snapshot_seconds" not in written["status_run_start"]


def test_snapshot_save_merges_run_end_into_the_same_file(app_and_store, tmp_path):
    app, _, _ = app_and_store
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    client = app.test_client()
    for key in ("status_run_start", "status_run_end"):
        client.post(
            "/status/snapshot",
            json={"save": True, "pgroup": "p1", "run_number": 1, "key": key},
        )
    written = json.loads(
        (tmp_path / "p1" / "run0001" / "aux" / "status.json").read_text()
    )
    assert set(written) == {"status_run_start", "status_run_end"}


def test_snapshot_save_requires_pgroup_and_run_number(app_and_store):
    app, _, _ = app_and_store
    resp = app.test_client().post("/status/snapshot", json={"save": True})
    assert resp.status_code == 400


def test_async_write_job_completes(app_and_store, tmp_path):
    app, _, _ = app_and_store
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    client = app.test_client()
    body = client.post(
        "/status/snapshot",
        json={"save": True, "pgroup": "p1", "run_number": 2, "write_async": True},
    ).get_json()
    job_id = body["write_job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert job["state"] == "done"
    assert (tmp_path / "p1" / "run0002" / "aux" / "status.json").exists()


def test_unknown_job_id_is_404(app_and_store):
    app, _, _ = app_and_store
    assert app.test_client().get("/status/job/nope").status_code == 404


def test_names_and_failures_endpoints(fake_module):
    name, _ = fake_module(fail=["b"])
    store = _store(name)
    assert store.wait_ready(timeout=10)
    app = create_namespace_app(NamespaceServerConfig(module_name=name), store=store)
    client = app.test_client()
    names = client.get("/names").get_json()
    assert names["target_names"] == ["a", "b", "c"]
    assert names["failed_names"] == ["b"]
    failures = client.get("/failures").get_json()["failures"]
    assert "b boom" in failures["b"]


def test_aliases_endpoint_returns_the_alias_list(app_and_store):
    app, _, _ = app_and_store
    body = app.test_client().get("/aliases").get_json()
    assert body["n_aliases"] == 3
    assert {"alias": "fake.a", "channel": "PV:A", "channeltype": "CA"} in body["aliases"]


def test_aliases_endpoint_filters_by_channeltype(app_and_store):
    app, _, _ = app_and_store
    body = app.test_client().get("/aliases?channeltype=BS").get_json()
    assert body["aliases"] == []
    assert body["n_aliases"] == 0

    body = app.test_client().get("/aliases?channeltype=CA").get_json()
    assert body["n_aliases"] == 3


def test_aliases_endpoint_503_while_not_ready(fake_module):
    name, _ = fake_module(names=[f"n{i}" for i in range(10)], init_delay=0.05)
    store = _store(name)
    app = create_namespace_app(NamespaceServerConfig(module_name=name), store=store)
    resp = app.test_client().get("/aliases")
    assert resp.status_code == 503
    assert resp.get_json()["status"] == "error"
    assert store.wait_ready(timeout=20)


def test_reinit_endpoint_202_then_409_while_busy(fake_module):
    name, _ = fake_module(names=[f"n{i}" for i in range(20)], init_delay=0.03)
    store = _store(name)
    assert store.wait_ready(timeout=30)
    app = create_namespace_app(NamespaceServerConfig(module_name=name), store=store)
    client = app.test_client()
    first = client.post("/admin/reinit", json={"mode": "full"})
    assert first.status_code == 202
    second = client.post("/admin/reinit", json={"mode": "full"})
    assert second.status_code == 409
    assert store.wait_ready(timeout=60)


# --------------------------------------------------------------------------
# storage


def test_write_status_snapshot_accepts_get_status_shape(tmp_path):
    snap = {"status": {"a": 1}, "status_channels": {"a": "PV:A"},
            "status_times": {"a": 0.1}, "selections": {}}
    path = write_status_snapshot(tmp_path, snap)
    assert json.loads(path.read_text())["status_run_start"]["status"] == {"a": 1}


def test_write_status_snapshot_accepts_flat_registry_shape(tmp_path):
    snap = {"a": {"value": 1, "pvname": "PV:A"}}
    path = write_status_snapshot(tmp_path, snap)
    written = json.loads(path.read_text())["status_run_start"]
    assert written["status"] == {"a": 1}
    assert written["status_channels"] == {"a": "PV:A"}


def test_write_status_snapshot_survives_a_corrupt_existing_file(tmp_path):
    (tmp_path / "status.json").write_text("{not json")
    snap = {"status": {"a": 1}, "status_channels": {}}
    path = write_status_snapshot(tmp_path, snap)
    assert json.loads(path.read_text())["status_run_start"]["status"] == {"a": 1}


# --------------------------------------------------------------------------
# StatusServerClient against a real socket


@pytest.fixture
def live_server(fake_module):
    """Run the Flask app on a real port so StatusServerClient's actual HTTP
    calls (not a test client) are exercised."""
    from werkzeug.serving import make_server

    name, mod = fake_module(names=[f"n{i}" for i in range(6)], init_delay=0.1)
    store = _store(name)
    config = NamespaceServerConfig(module_name=name, port=0)
    app = create_namespace_app(config, store=store)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield url, store, app, mod
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_client_wait_ready_reports_progress_then_succeeds(live_server, capsys):
    from eco.status_server.client import StatusServerClient

    url, store, _, _ = live_server
    client = StatusServerClient(url)
    assert client.is_ready() is False
    health = client.wait_ready(timeout=30, poll=0.05, progress=True)
    assert health["ready"] is True
    out = capsys.readouterr().out
    assert "initializing" in out
    assert "status server ready" in out


def test_client_snapshot_raises_not_ready_before_init_finishes(live_server):
    from eco.status_server.client import StatusServerClient, StatusServerNotReady

    url, store, _, _ = live_server
    client = StatusServerClient(url)
    with pytest.raises(StatusServerNotReady):
        client.get_status()
    client.wait_ready(timeout=30, poll=0.05)
    assert client.get_status()["status"]["fake.n0"] == "N0"


def test_client_reinit_waits_for_the_new_generation(live_server):
    from eco.status_server.client import StatusServerClient

    url, store, _, _ = live_server
    client = StatusServerClient(url)
    client.wait_ready(timeout=30, poll=0.05)
    gen_before = client.health()["generation"]
    health = client.reinit(mode="full", wait=True, timeout=60, progress=False)
    assert health["generation"] == gen_before + 1
    assert health["ready"] is True


def test_client_reinit_wait_does_not_return_on_the_pre_reinit_state(live_server):
    """The bug this guards: a reinit request returns immediately, so a naive
    'poll until ready' can be answered by the still-ready old state before
    the rebuild has even started."""
    from eco.status_server.client import StatusServerClient

    url, store, _, _ = live_server
    client = StatusServerClient(url)
    client.wait_ready(timeout=30, poll=0.05)
    gen_before = client.health()["generation"]
    health = client.reinit(mode="full", wait=True, timeout=60)
    assert health["generation"] > gen_before
    # the fake namespace re-runs its 0.1 s-per-name init, so a return on the
    # stale state would have been visibly too fast
    assert store.last_init_seconds >= 0.5


def test_client_snapshot_save_and_async_job(live_server, tmp_path):
    from eco.status_server.client import StatusServerClient

    url, store, app, _ = live_server
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    client = StatusServerClient(url)
    client.wait_ready(timeout=30, poll=0.05)

    resp = client.snapshot(pgroup="p1", run_number=3, save=True)
    assert json.loads(open(resp["saved_to"]).read())["status_run_start"]["status"]

    resp = client.snapshot(
        pgroup="p1", run_number=4, save=True, key="status_run_end", write_async=True
    )
    job = client.wait_write_job(resp["write_job_id"], timeout=10)
    assert job["state"] == "done"
    assert (tmp_path / "p1" / "run0004" / "aux" / "status.json").exists()


def test_client_push_status_merges_into_a_real_capture_job(live_server, tmp_path):
    from eco.status_server.client import StatusServerClient

    url, store, app, _ = live_server
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    client = StatusServerClient(url)
    client.wait_ready(timeout=30, poll=0.05)

    started = client.capture(pgroup="p1", run_number=5, upload=False,
                             keep_status=True)
    client.wait_write_job(started["job_id"], timeout=10)

    resp = client.push_status(
        "p1", 5, {"scans.acquiring_scan.description": "a scan"}
    )
    assert resp["status"] == "ok"

    job = client.wait_write_job(started["job_id"], timeout=10, include_status=True)
    assert job["status"]["scans.acquiring_scan.description"] == "a scan"
    assert job["status"]["fake.n0"] == "N0"


def test_snapshot_endpoint_serializes_numpy_values(fake_module):
    """A waveform PV or an image stat returns a numpy array; Flask's default
    JSON provider raises TypeError on those, which turned one odd value into
    a 500 for the whole snapshot."""
    import numpy as np

    name, mod = fake_module()
    mod.namespace.status_collection = FakeStatusCollection(
        [
            FakeDetector("fake.arr", np.arange(3)),
            FakeDetector("fake.f", np.float64(1.5)),
            FakeDetector("fake.weird", object()),
        ]
    )
    store = _store(name)
    assert store.wait_ready(timeout=10)
    app = create_namespace_app(NamespaceServerConfig(module_name=name), store=store)
    resp = app.test_client().post("/status/snapshot", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"]["fake.arr"] == [0, 1, 2]
    assert body["status"]["fake.f"] == 1.5
    assert isinstance(body["status"]["fake.weird"], str)


def test_status_file_write_serializes_numpy_values(tmp_path):
    import numpy as np

    snap = {"status": {"a": np.arange(2), "b": np.float32(0.5)},
            "status_channels": {}}
    path = write_status_snapshot(tmp_path, snap)
    assert json.loads(path.read_text())["status_run_start"]["status"]["a"] == [0, 1]


def test_restart_argv_reconstructs_the_dash_m_form(monkeypatch):
    """`python -m eco.status_server` puts __main__.py's *path* in argv[0];
    re-execing that directly dies on the package's relative imports."""
    import types as _types

    from eco.status_server import namespace_server

    fake_main = _types.ModuleType("__main__")
    fake_main.__spec__ = _types.SimpleNamespace(
        name="eco.status_server.__main__", parent="eco.status_server"
    )
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    monkeypatch.setattr(
        namespace_server.sys, "argv",
        ["/path/to/eco/status_server/__main__.py", "--mode", "namespace",
         "--config", "/etc/x.json"],
    )
    argv = namespace_server._restart_argv()
    assert argv[1:] == ["-m", "eco.status_server", "--mode", "namespace",
                        "--config", "/etc/x.json"]


def test_restart_argv_falls_back_for_a_plain_script(monkeypatch):
    import types as _types

    from eco.status_server import namespace_server

    fake_main = _types.ModuleType("__main__")
    fake_main.__spec__ = None
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    monkeypatch.setattr(namespace_server.sys, "argv", ["server.py", "--port", "1"])
    assert namespace_server._restart_argv()[1:] == ["server.py", "--port", "1"]


def test_init_retries_names_that_were_never_really_attempted(fake_module):
    """A component slower than init_timeout ends up in failed_items either
    with an IsInitialisingError or - the case seen on bernina - with no
    exception at all, because init_all's giveup_failed sweeps up whatever is
    still lazy when the retry cap is hit. Both are retried; a genuine
    failure is not."""
    from eco.utilities.config import IsInitialisingError

    name, mod = fake_module()
    ns = mod.namespace
    real_init_all = ns.init_all
    state = {"pass": 0}

    def flaky_init_all(**kwargs):
        state["pass"] += 1
        real_init_all(**kwargs)
        if state["pass"] == 1:
            ns.initialized_names.discard("a")
            ns.failed_names.add("a")
            ns.failed_items_excpetion["a"] = IsInitialisingError("still building")
            ns.initialized_names.discard("b")
            ns.failed_names.add("b")
            ns.failed_items_excpetion["b"] = ValueError("genuinely broken")
            # no recorded exception at all - the giveup_failed sweep
            ns.initialized_names.discard("c")
            ns.failed_names.add("c")

    ns.init_all = flaky_init_all
    ns.move_failed_to_lazy = lambda *names: [
        (ns.failed_names.discard(n), ns.failed_items_excpetion.pop(n, None),
         ns.initialized_names.add(n)) for n in names
    ]

    store = _store(name)
    assert store.wait_ready(timeout=10)
    assert state["pass"] == 2, "did not run a retry pass"
    assert "a" in ns.initialized_names
    assert "c" in ns.initialized_names
    assert "b" in ns.failed_names, "a genuine failure must not be retried"


def test_create_app_can_defer_starting_the_store(fake_module):
    """__main__ binds the port before starting the namespace init, so a port
    clash costs a failed bind instead of a wasted multi-minute init_all()."""
    name, mod = fake_module()
    app = create_namespace_app(
        NamespaceServerConfig(module_name=name), start_store=False
    )
    store = app.config["ECO_STORE"]
    assert store.state == ns_store.IMPORTING
    assert store.namespace is None
    assert mod.namespace.init_calls == []
    store.start()
    assert store.wait_ready(timeout=10)
    assert mod.namespace.init_calls


def test_restart_closes_the_listening_socket_first(app_and_store, monkeypatch):
    """werkzeug's run_simple() marks the listening socket inheritable, so it
    survives os.execv() and the re-exec'd process cannot rebind ("Address
    already in use") - the restart has to hand the port back explicitly."""
    from eco.status_server import namespace_server

    app, _, _ = app_and_store
    closed = []
    execd = []
    app.config["ECO_WSGI_SERVER"] = types.SimpleNamespace(
        server_close=lambda: closed.append(True)
    )
    monkeypatch.setattr(namespace_server.os, "execv",
                        lambda exe, argv: execd.append(argv))
    monkeypatch.setattr(namespace_server.time, "sleep", lambda s: None)

    resp = app.test_client().post("/admin/restart", json={"delay": 0})
    assert resp.status_code == 202
    deadline = time.time() + 5
    while time.time() < deadline and not execd:
        time.sleep(0.01)
    assert closed == [True]
    assert execd, "never reached the exec"


def test_purge_eco_modules_keeps_the_status_server_itself():
    from eco.status_server.namespace_store import purge_eco_modules

    table = {
        "eco": object(),
        "eco.bernina.bernina": object(),
        "eco.elements.assembly": object(),
        "eco.status_server.namespace_store": object(),
        "ecosystem": object(),
        "numpy": object(),
    }
    assert purge_eco_modules(table) == 3
    assert sorted(table) == [
        "eco.status_server.namespace_store",
        "ecosystem",
        "numpy",
    ]


def test_status_file_and_every_directory_level_are_group_writable(tmp_path):
    """Not just the leaf: mkdir(parents=True) creates the intermediate levels
    at 0o755, so the first account to start a run used to lock every other
    account out of creating the next one."""
    directory = tmp_path / "run_data" / "daq" / "run0001" / "aux"
    path = write_status_snapshot(directory, {"status": {"a": 1}, "status_channels": {}})
    assert path.stat().st_mode & 0o664 == 0o664
    for level in (directory, directory.parent, directory.parent.parent):
        assert level.stat().st_mode & 0o770 == 0o770, level


def test_retry_passes_run_serially(fake_module):
    """Workers colliding on a shared dependency is what makes a component a
    straggler, so the retry pass that goes after those uses one worker."""
    name, mod = fake_module()
    ns = mod.namespace
    real_init_all = ns.init_all
    passes = []

    def recording_init_all(**kwargs):
        passes.append(kwargs["max_workers"])
        real_init_all(**kwargs)
        if len(passes) == 1:
            ns.initialized_names.discard("a")
            ns.failed_names.add("a")

    ns.init_all = recording_init_all
    ns.move_failed_to_lazy = lambda *names: [
        (ns.failed_names.discard(n), ns.initialized_names.add(n)) for n in names
    ]

    store = _store(name, init_workers=8, retry_workers=1)
    assert store.wait_ready(timeout=10)
    assert passes == [8, 1]


# --------------------------------------------------------------------------
# recording


class FakePV:
    """Stands in for the pyepics PV a CallbackEpics wraps - just enough for
    the recording session's seed-if-already-monitored decision and the seed
    read itself (pv.get()/pv.timestamp), same shape pyepics gives a real,
    connected, auto_monitor=True PV where get() is a cached-value read with
    no CA traffic."""

    def __init__(self, pvname, value=0.0, auto_monitor=True, connected=True):
        self.pvname = pvname
        self.auto_monitor = auto_monitor
        self.connected = connected
        self.timestamp = time.time()
        self._value = value

    def get(self, **kwargs):
        return self._value


class FakeMonitor:
    """Stands in for CallbackEpics: remembers how it was started and lets a
    test push updates through the callback the store registered."""

    instances = []

    def __init__(self, func, pv=None):
        self.func = func
        self.pv = pv
        self.started_with = None
        self.stopped = False
        FakeMonitor.instances.append(self)

    def start(self, add_current_value=True, with_ctrlvars=True, auto_monitor=True):
        self.started_with = {
            "add_current_value": add_current_value,
            "with_ctrlvars": with_ctrlvars,
            "auto_monitor": auto_monitor,
        }
        # Same order as the real CallbackEpics.start(): seed before the
        # subscription is (would be) registered.
        if add_current_value and self.pv is not None:
            self.func(pvname=self.pv.pvname, value=self.pv.get(),
                      timestamp=self.pv.timestamp)

    def stop(self):
        self.stopped = True

    def push(self, value, timestamp=None):
        self.func(value=value, timestamp=timestamp if timestamp is not None
                  else time.time())


class MonitorableDetector(FakeDetector):
    def __init__(self, full_name, value, channel=None, pv=None):
        super().__init__(full_name, value, channel=channel)
        self._fake_pv = pv

    def set_current_value_callback(self, func="accumulate", **kwargs):
        return FakeMonitor(func, pv=self._fake_pv)


@pytest.fixture
def recording_store(fake_module):
    FakeMonitor.instances = []
    name, mod = fake_module()
    mod.namespace.status_collection = FakeStatusCollection(
        [
            MonitorableDetector("fake.m1", 1.0, channel="PV:M1"),
            MonitorableDetector("fake.m2", 2.0, channel="PV:M2"),
            FakeDetector("fake.plain", 3.0, channel="PV:P"),  # not monitorable
        ]
    )
    store = _store(name)
    assert store.wait_ready(timeout=10)
    return store


def _recording_store_with(fake_module, detectors):
    """Like `recording_store`, but with caller-chosen detectors - for tests
    that need specific FakePV configurations (auto_monitor/connected) per
    channel."""
    FakeMonitor.instances = []
    name, mod = fake_module()
    mod.namespace.status_collection = FakeStatusCollection(detectors)
    store = _store(name)
    assert store.wait_ready(timeout=10)
    return store


def test_only_monitorable_detectors_are_recordable(recording_store):
    assert recording_store.monitorable_names() == ["fake.m1", "fake.m2"]
    assert recording_store.connection_report()["n_monitorable"] == 2


def test_recording_attaches_without_a_ca_get_storm(recording_store):
    recording_store.start_recording("r1")
    assert len(FakeMonitor.instances) == 2
    for mon in FakeMonitor.instances:
        # both would be a blocking CA round trip per channel at start
        assert mon.started_with["add_current_value"] is False
        assert mon.started_with["with_ctrlvars"] is False


def test_recording_mode_all_stores_every_update(recording_store):
    recording_store.start_recording("r1")
    for mon in FakeMonitor.instances:
        for v in range(5):
            mon.push(float(v))
    result = recording_store.stop_recording("r1")
    assert result["n_updates"] == 10
    assert result["n_stored"] == 10
    assert result["data"]["fake.m1"]["values"] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert all(m.stopped for m in FakeMonitor.instances)


def test_recording_throttle_drops_updates_within_the_interval(recording_store):
    recording_store.start_recording("r1", names=["fake.m1"], mode="throttle",
                                    min_interval=10.0)
    mon = FakeMonitor.instances[0]
    for v in range(5):
        mon.push(float(v))
    result = recording_store.stop_recording("r1")
    assert result["n_updates"] == 5
    assert result["n_stored"] == 1
    assert result["n_dropped_throttle"] == 4


def test_recording_sample_mode_uses_a_grid_not_the_update_rate(recording_store):
    recording_store.start_recording("r1", names=["fake.m1"], mode="sample",
                                    sample_interval=0.05)
    mon = FakeMonitor.instances[0]
    for v in range(200):
        mon.push(float(v))
    time.sleep(0.3)
    result = recording_store.stop_recording("r1")
    assert result["n_updates"] == 200
    # a handful of grid points, nowhere near 200
    assert 1 <= result["n_stored"] <= 20
    assert result["data"]["fake.m1"]["values"][-1] == 199.0


# --------------------------------------------------------------------------
# seeding: a channel that is already auto_monitor=True and connected gets a
# free current-value point at attach time (pyepics' PV.get() is a cached
# read with no CA traffic there - see ca_tuning's "Resolved 2026-09-06"
# note), so a channel that never itself updates during the recording is not
# automatically empty.


def test_an_already_monitored_connected_channel_is_seeded_for_free(fake_module):
    store = _recording_store_with(fake_module, [
        MonitorableDetector("fake.m1", 1.0, channel="PV:M1",
                            pv=FakePV("PV:M1", value=42.0)),
    ])
    result = store.start_recording("r1")
    assert result.n_seeded == 1
    assert FakeMonitor.instances[0].started_with["add_current_value"] is True

    stopped = store.stop_recording("r1")
    # seeded, never pushed again - one point, the seed value
    assert stopped["data"]["fake.m1"]["values"] == [42.0]


def test_a_demoted_channel_is_not_seeded(fake_module):
    """auto_monitor=False (ca_tuning demoted it as too fast) means PV.get()
    is a real CA round trip, not a cached read - skip the seed rather than
    pay for thousands of those at once."""
    store = _recording_store_with(fake_module, [
        MonitorableDetector("fake.m1", 1.0, channel="PV:M1",
                            pv=FakePV("PV:M1", auto_monitor=False)),
    ])
    result = store.start_recording("r1")
    assert result.n_seeded == 0
    assert FakeMonitor.instances[0].started_with["add_current_value"] is False


def test_a_disconnected_channel_is_not_seeded(fake_module):
    store = _recording_store_with(fake_module, [
        MonitorableDetector("fake.m1", 1.0, channel="PV:M1",
                            pv=FakePV("PV:M1", connected=False)),
    ])
    result = store.start_recording("r1")
    assert result.n_seeded == 0


def test_a_channel_with_no_pv_reference_is_not_seeded(recording_store):
    """The plain FakeMonitor(pv=None) case - e.g. a Monitorable whose
    CallbackEpics wraps something other than a bare pyepics PV."""
    result = recording_store.start_recording("r1")
    assert result.n_seeded == 0


def test_seeding_still_respects_the_recording_mode(fake_module):
    """The seed goes through the same per-mode callback as any other
    update - throttle/sample apply to it exactly like a real one."""
    store = _recording_store_with(fake_module, [
        MonitorableDetector("fake.m1", 1.0, channel="PV:M1",
                            pv=FakePV("PV:M1", value=7.0)),
    ])
    store.start_recording("r1", mode="throttle", min_interval=10.0)
    mon = FakeMonitor.instances[0]
    mon.push(8.0)   # within min_interval of the seed - dropped
    stopped = store.stop_recording("r1")
    assert stopped["data"]["fake.m1"]["values"] == [7.0]
    assert stopped["n_dropped_throttle"] == 1


def test_a_composed_virtual_channel_is_seeded_for_free(fake_module):
    """DetectorVirtual/AdjustableVirtual (eco.elements) implement
    set_current_value_callback() themselves when every one of their own
    parents does - discovered here via the same isinstance(ts,
    MonitorableValueUpdate) check that finds any other monitorable channel,
    no special-casing needed. Their seed is a recompute from already-
    monitored parents, not a CA get of its own - free the same way a plain
    monitored PV's seed is."""
    from eco.elements.detector import DetectorVirtual

    a = MonitorableDetector("fake.a", 1.0, channel="PV:A")
    b = MonitorableDetector("fake.b", 2.0, channel="PV:B")
    composed = DetectorVirtual(
        [a, b], foo_get_current_value=lambda x, y: x + y, name="fake.composed"
    )

    store = _recording_store_with(fake_module, [composed])
    assert store.monitorable_names() == ["fake.composed"]

    result = store.start_recording("r1")
    assert result.n_seeded == 1

    stopped = store.stop_recording("r1")
    assert stopped["data"]["fake.composed"]["values"] == [3.0]


# --------------------------------------------------------------------------
# backfill: opportunistically fill a still-empty channel from a status
# snapshot that was already being taken for another reason (a scan's own
# status_run_start/status_run_end capture) - never a trigger of new CA
# traffic on its own.


def test_backfill_from_status_fills_empty_channels_only(recording_store):
    recording_store.start_recording("r1")
    mon0 = FakeMonitor.instances[0]  # fake.m1
    mon0.push(5.0)  # fake.m1 already has a real point - must not be touched

    filled = recording_store._recordings["r1"].backfill_from_status(
        {"fake.m1": 999.0, "fake.m2": 2.5, "fake.plain": 3.0}
    )
    assert filled == 1  # only fake.m2 was empty
    stopped = recording_store.stop_recording("r1")
    assert stopped["data"]["fake.m1"]["values"] == [5.0]
    assert stopped["data"]["fake.m2"]["values"] == [2.5]
    assert stopped["n_backfilled"] == 1


def test_backfill_ignores_channels_not_in_the_status_dict(recording_store):
    recording_store.start_recording("r1")
    filled = recording_store._recordings["r1"].backfill_from_status(
        {"fake.m1": 1.0}  # nothing for fake.m2
    )
    assert filled == 1
    stopped = recording_store.stop_recording("r1")
    assert "fake.m2" not in stopped["data"]


def test_backfill_ignores_a_none_value(recording_store):
    recording_store.start_recording("r1")
    filled = recording_store._recordings["r1"].backfill_from_status(
        {"fake.m1": None, "fake.m2": 2.0}
    )
    assert filled == 1
    stopped = recording_store.stop_recording("r1")
    assert "fake.m1" not in stopped["data"]


def test_backfill_running_recordings_finds_the_matching_run(recording_store):
    recording_store.start_recording(
        "r1", pgroup="p1", run_number=5, names=["fake.m1"]
    )
    recording_store.start_recording(
        "r2", pgroup="p1", run_number=6, names=["fake.m2"]
    )
    touched = recording_store.backfill_running_recordings(
        "p1", 5, {"fake.m1": 1.0, "fake.m2": 2.0}
    )
    assert touched == 1  # only r1 matches (p1, 5)
    assert recording_store._recordings["r1"].n_backfilled == 1
    assert recording_store._recordings["r2"].n_backfilled == 0


def test_backfill_running_recordings_ignores_a_stopped_recording(recording_store):
    recording_store.start_recording("r1", pgroup="p1", run_number=5)
    recording_store.stop_recording("r1")
    touched = recording_store.backfill_running_recordings(
        "p1", 5, {"fake.m1": 1.0}
    )
    assert touched == 0


def test_backfill_running_recordings_is_a_noop_without_pgroup_or_run_number(
    recording_store
):
    recording_store.start_recording("r1")  # no pgroup/run_number given
    assert recording_store.backfill_running_recordings(
        "p1", 5, {"fake.m1": 1.0}
    ) == 0


def test_status_capture_backfills_a_running_recording_for_the_same_run(
    recording_store, tmp_path
):
    """The real end-to-end path: /status/capture's own snapshot (already
    happening for other reasons) opportunistically fills in the recording's
    still-empty channels for the same run - no extra CA traffic beyond what
    the status capture was already doing."""
    config = NamespaceServerConfig(module_name=recording_store.module_name)
    config.data_root_pattern = str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    app = create_namespace_app(config, store=recording_store)
    client = app.test_client()

    client.post(
        "/recording/start",
        json={"recording_id": "r1", "pgroup": "p1", "run_number": 12},
    )

    job_id = client.post(
        "/status/capture",
        json={"pgroup": "p1", "run_number": 12, "upload": False},
    ).get_json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert job["state"] == "done"

    live = client.get("/recording/r1").get_json()
    assert live["n_backfilled"] == 2  # fake.m1 and fake.m2, both still empty


def test_status_capture_does_not_backfill_a_recording_from_a_different_run(
    recording_store, tmp_path
):
    config = NamespaceServerConfig(module_name=recording_store.module_name)
    config.data_root_pattern = str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    app = create_namespace_app(config, store=recording_store)
    client = app.test_client()

    client.post(
        "/recording/start",
        json={"recording_id": "r1", "pgroup": "p1", "run_number": 12},
    )
    job_id = client.post(
        "/status/capture",
        json={"pgroup": "p1", "run_number": 13, "upload": False},  # different run
    ).get_json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)

    live = client.get("/recording/r1").get_json()
    assert live["n_backfilled"] == 0


def test_recording_respects_the_point_cap(recording_store):
    recording_store.start_recording("r1", names=["fake.m1"],
                                    max_points_per_channel=3)
    mon = FakeMonitor.instances[0]
    for v in range(10):
        mon.push(float(v))
    result = recording_store.stop_recording("r1")
    assert result["n_stored"] == 3
    assert result["n_dropped_at_cap"] == 7


def test_second_start_with_the_same_id_is_refused(recording_store):
    recording_store.start_recording("r1")
    with pytest.raises(ValueError, match="already running"):
        recording_store.start_recording("r1")
    recording_store.stop_recording("r1")


def test_unknown_recording_name_is_rejected(recording_store):
    with pytest.raises(KeyError):
        recording_store.start_recording("r1", names=["fake.plain"])


def test_reinit_stops_a_running_recording(recording_store):
    recording_store.start_recording("r1")
    recording_store.start_reinit(mode="init")
    assert recording_store.wait_ready(timeout=10)
    assert all(m.stopped for m in FakeMonitor.instances)
    assert recording_store.list_recordings()[0]["running"] is False


def test_recording_endpoints_round_trip(recording_store, tmp_path):
    config = NamespaceServerConfig(module_name=recording_store.module_name)
    config.data_root_pattern = str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    app = create_namespace_app(config, store=recording_store)
    client = app.test_client()

    assert client.get("/recording").get_json()["n_monitorable"] == 2

    started = client.post("/recording/start", json={"recording_id": "r1"})
    assert started.status_code == 202
    assert started.get_json()["n_channels_attached"] == 2

    for mon in FakeMonitor.instances:
        for v in range(4):
            mon.push(float(v))

    live = client.get("/recording/r1").get_json()
    assert live["running"] is True and live["n_updates"] == 8

    stopped = client.post(
        "/recording/stop",
        json={"recording_id": "r1", "save": True, "pgroup": "p1", "run_number": 5},
    ).get_json()
    assert stopped["status"] == "ok"
    assert stopped["n_written"] == 2
    path = tmp_path / "p1" / "run0005" / "aux" / "monitors.esc.h5"
    assert stopped["saved_to"] == str(path)
    assert path.exists()
    # dropped by default, so the buffers are not held for the life of the
    # process
    assert client.get("/recording/r1").status_code == 404


def test_recording_stop_save_requires_pgroup(recording_store):
    app = create_namespace_app(
        NamespaceServerConfig(module_name=recording_store.module_name),
        store=recording_store,
    )
    client = app.test_client()
    client.post("/recording/start", json={"recording_id": "r1"})
    resp = client.post("/recording/stop", json={"recording_id": "r1", "save": True})
    assert resp.status_code == 400


def test_subscription_mask_log_maps_to_dbe_log(recording_store):
    from epics.dbr import DBE_LOG

    app = create_namespace_app(
        NamespaceServerConfig(module_name=recording_store.module_name),
        store=recording_store,
    )
    app.test_client().post(
        "/recording/start",
        json={"recording_id": "r1", "subscription_mask": "log"},
    )
    assert FakeMonitor.instances[0].started_with["auto_monitor"] == DBE_LOG


def test_monitor_recording_file_holds_one_arraytimestamps_per_channel(tmp_path):
    import h5py

    from eco.status_server.storage import write_monitor_recording

    t0 = time.time()
    rec = {
        "started_at": t0,
        "stopped_at": t0 + 1,
        "data": {
            "bernina.a": {"values": [1.0, 2.0], "timestamps": [t0, t0 + 0.5]},
            "bernina.enum": {"values": ["Open", "Closed"], "timestamps": [t0, t0 + 0.5]},
        },
    }
    path = write_monitor_recording(tmp_path, rec)
    assert rec["write_report"]["n_written"] == 2
    with h5py.File(path) as f:
        assert f["bernina.a/data_0000"][()].tolist() == [1.0, 2.0]
        assert f["bernina.a/timestamps_0000"].shape == (2,)
        assert f["bernina.a/scan/timestamp_intervals"].shape == (1, 2)


def test_one_unstorable_channel_does_not_lose_the_file(tmp_path):
    import numpy as np

    from eco.status_server.storage import write_monitor_recording

    t0 = time.time()
    rec = {
        "started_at": t0,
        "stopped_at": t0 + 1,
        "data": {
            "bernina.ok": {"values": [1.0], "timestamps": [t0]},
            # a waveform PV whose length changed between updates
            "bernina.wave": {
                "values": [np.arange(3), np.arange(4)],
                "timestamps": [t0, t0 + 0.5],
            },
        },
    }
    path = write_monitor_recording(tmp_path, rec)
    assert path.exists()
    assert rec["write_report"]["n_written"] == 1
    assert "bernina.wave" in rec["write_report"]["skipped"]


def test_health_reports_process_cost(app_and_store):
    app, _, _ = app_and_store
    body = app.test_client().get("/health").get_json()
    # Linux-only, but that is where this runs; assert it is actually there
    # rather than silently losing the one measurement that answers "is
    # monitoring costing anything?"
    assert body["cpu_seconds"] > 0
    assert body["rss_mb"] > 0
    assert body["n_threads"] >= 1


def test_stop_truncates_a_half_written_point(recording_store):
    """A callback caught between its value append and its timestamp append
    leaves the two lists one apart; pairing them then shifts every
    timestamp."""
    recording_store.start_recording("r1", names=["fake.m1"])
    mon = FakeMonitor.instances[0]
    mon.push(1.0)
    session = recording_store._recordings["r1"]
    session._buffers["fake.m1"]["values"].append(2.0)  # timestamp not yet appended
    data = recording_store.stop_recording("r1")["data"]
    assert len(data["fake.m1"]["values"]) == len(data["fake.m1"]["timestamps"]) == 1


def test_sample_mode_does_not_upsample_a_slow_channel(recording_store):
    """Sampling every channel on the grid turns a namespace of mostly-idle
    channels into far more data than recording every update: measured on
    bernina, 15x more. A slow channel must contribute points only when it
    actually updates."""
    recording_store.start_recording("r1", names=["fake.m1", "fake.m2"],
                                    mode="sample", sample_interval=0.02)
    fast, slow = FakeMonitor.instances
    slow.push(1.0, timestamp=1.0)          # one update, then silence
    for i in range(100):
        fast.push(float(i), timestamp=100.0 + i)
        time.sleep(0.002)
    time.sleep(0.2)
    result = recording_store.stop_recording("r1")
    data = result["data"]
    assert data["fake.m2"]["values"] == [1.0], "idle channel was upsampled"
    n_fast = len(data["fake.m1"]["values"])
    assert 1 < n_fast < 100, f"fast channel not decimated to the grid ({n_fast})"


def test_recording_file_uses_the_compact_hdf5_layout(tmp_path):
    """A namespace recording is thousands of tiny datasets, where HDF5's
    default object-header layout costs far more than the data itself."""
    import h5py

    from eco.status_server.storage import write_monitor_recording

    t0 = time.time()
    rec = {
        "started_at": t0,
        "stopped_at": t0 + 1,
        "data": {f"bernina.dev{i}.value": {"values": [1.0], "timestamps": [t0]}
                 for i in range(200)},
    }
    compact = write_monitor_recording(tmp_path / "a", dict(rec))
    legacy = write_monitor_recording(tmp_path / "b", dict(rec), libver=None)
    assert compact.stat().st_size < legacy.stat().st_size
    with h5py.File(compact) as f:
        assert len(f.keys()) == 200


def test_max_value_elements_keeps_waveforms_out(recording_store):
    """A handful of waveform channels dominate a recording's size by an
    order of magnitude over every scalar channel combined."""
    import numpy as np

    recording_store.start_recording("r1", names=["fake.m1"], max_value_elements=16)
    mon = FakeMonitor.instances[0]
    mon.push(1.0)
    mon.push(np.arange(8000))     # a digitizer waveform
    mon.push(np.arange(4))        # small array, still fine
    result = recording_store.stop_recording("r1")
    assert result["n_stored"] == 2
    assert result["n_dropped_too_large"] == 1


# --------------------------------------------------------------------------
# /recording/capture: stop + write + upload, all off the client's clock -
# the recording equivalent of /status/capture and /aliases/capture. Stopping
# itself (detaching callbacks) happens synchronously before the response;
# only the write and upload are backgrounded.


def test_recording_capture_returns_immediately_and_does_the_work_in_the_background(
    recording_store, tmp_path, monkeypatch
):
    config = NamespaceServerConfig(module_name=recording_store.module_name)
    config.data_root_pattern = str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    app = create_namespace_app(config, store=recording_store)
    client = app.test_client()

    posted = []
    import requests

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "ok", "message": "copying user file(s) finished"}

    monkeypatch.setattr(
        requests, "post", lambda url, json=None, timeout=None: (
            posted.append((url, json)), _Resp)[1]
    )

    client.post("/recording/start", json={"recording_id": "r1"})
    for mon in FakeMonitor.instances:
        for v in range(4):
            mon.push(float(v))

    started = client.post(
        "/recording/capture",
        json={"recording_id": "r1", "pgroup": "p1", "run_number": 9},
    )
    assert started.status_code == 202
    job_id = started.get_json()["job_id"]
    # stopping (detaching callbacks) already happened, synchronously, before
    # the response - only the write+upload are still pending
    assert client.get("/recording/r1").get_json()["running"] is False

    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert job["state"] == "done", job
    path = tmp_path / "p1" / "run0009" / "aux" / "monitors.esc.h5"
    assert path.exists()
    assert job["n_written"] == 2

    url, body = posted[0]
    assert url.endswith("/copy_user_files")
    assert body["pgroup"] == "p1" and body["run_number"] == 9
    assert body["files"] == [str(path)]
    assert job["upload"]["status"] == "ok"
    # dropped by default once the job is done
    assert client.get("/recording/r1").status_code == 404


def test_recording_capture_can_skip_the_upload(recording_store, tmp_path, monkeypatch):
    config = NamespaceServerConfig(module_name=recording_store.module_name)
    config.data_root_pattern = str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    app = create_namespace_app(config, store=recording_store)
    client = app.test_client()
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **kw: pytest.fail("uploaded"))
    client.post("/recording/start", json={"recording_id": "r1"})
    job_id = client.post(
        "/recording/capture",
        json={"recording_id": "r1", "pgroup": "p1", "run_number": 10,
              "upload": False},
    ).get_json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert job["state"] == "done"
    assert "upload" not in job


def test_recording_capture_can_keep_the_recording_instead_of_dropping_it(
    recording_store, tmp_path
):
    config = NamespaceServerConfig(module_name=recording_store.module_name)
    config.data_root_pattern = str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    app = create_namespace_app(config, store=recording_store)
    client = app.test_client()
    client.post("/recording/start", json={"recording_id": "r1"})
    job_id = client.post(
        "/recording/capture",
        json={"recording_id": "r1", "pgroup": "p1", "run_number": 11,
              "upload": False, "drop": False},
    ).get_json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert client.get("/recording/r1").status_code == 200


def test_recording_capture_requires_pgroup_and_run_number(recording_store):
    app = create_namespace_app(
        NamespaceServerConfig(module_name=recording_store.module_name),
        store=recording_store,
    )
    client = app.test_client()
    client.post("/recording/start", json={"recording_id": "r1"})
    resp = client.post("/recording/capture", json={"recording_id": "r1"})
    assert resp.status_code == 400


def test_recording_capture_unknown_recording_id_is_404(recording_store):
    app = create_namespace_app(
        NamespaceServerConfig(module_name=recording_store.module_name),
        store=recording_store,
    )
    resp = app.test_client().post(
        "/recording/capture",
        json={"recording_id": "nope", "pgroup": "p1", "run_number": 1},
    )
    assert resp.status_code == 404


def test_recording_capture_of_an_already_stopped_recording_is_409(recording_store):
    app = create_namespace_app(
        NamespaceServerConfig(module_name=recording_store.module_name),
        store=recording_store,
    )
    client = app.test_client()
    client.post("/recording/start", json={"recording_id": "r1"})
    client.post(
        "/recording/capture",
        json={"recording_id": "r1", "pgroup": "p1", "run_number": 1,
              "upload": False, "drop": False},
    )
    resp = client.post(
        "/recording/capture",
        json={"recording_id": "r1", "pgroup": "p1", "run_number": 1,
              "upload": False},
    )
    assert resp.status_code == 409


# --------------------------------------------------------------------------
# /status/capture: snapshot + write + upload, all off the client's clock


def test_capture_returns_immediately_and_does_the_work_in_the_background(
    app_and_store, tmp_path, monkeypatch
):
    app, _, _ = app_and_store
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    posted = []
    import requests

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "ok", "message": "copying user file(s) finished"}

    monkeypatch.setattr(
        requests, "post", lambda url, json=None, timeout=None: (
            posted.append((url, json)), _Resp)[1]
    )

    client = app.test_client()
    started = client.post(
        "/status/capture",
        json={"pgroup": "p1", "run_number": 3, "key": "status_run_start"},
    )
    assert started.status_code == 202
    job_id = started.get_json()["job_id"]

    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert job["state"] == "done", job
    path = tmp_path / "p1" / "run0003" / "aux" / "status.json"
    assert path.exists()
    assert json.loads(path.read_text())["status_run_start"]["status"]

    # the server, not the client, handed the file to the broker
    url, body = posted[0]
    assert url.endswith("/copy_user_files")
    assert body["pgroup"] == "p1" and body["run_number"] == 3
    assert body["files"] == [str(path)]
    assert job["upload"]["status"] == "ok"


def test_capture_can_skip_the_upload(app_and_store, tmp_path, monkeypatch):
    app, _, _ = app_and_store
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **kw: pytest.fail("uploaded"))
    client = app.test_client()
    job_id = client.post(
        "/status/capture",
        json={"pgroup": "p1", "run_number": 4, "upload": False},
    ).get_json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert job["state"] == "done"
    assert "upload" not in job


def test_capture_requires_pgroup_and_run_number(app_and_store):
    app, _, _ = app_and_store
    assert app.test_client().post("/status/capture", json={}).status_code == 400


def test_capture_is_refused_while_not_ready(fake_module):
    name, _ = fake_module(names=[f"n{i}" for i in range(10)], init_delay=0.05)
    store = _store(name)
    app = create_namespace_app(NamespaceServerConfig(module_name=name), store=store)
    resp = app.test_client().post(
        "/status/capture", json={"pgroup": "p1", "run_number": 1}
    )
    assert resp.status_code == 503
    assert store.wait_ready(timeout=20)


# --------------------------------------------------------------------------
# /status/push: values the store can never poll itself, merged into the next
# /status/job read for the matching (pgroup, run_number, key)


def test_status_push_requires_pgroup_run_number_and_values(app_and_store):
    app, _, _ = app_and_store
    client = app.test_client()
    assert client.post("/status/push", json={}).status_code == 400
    assert client.post(
        "/status/push", json={"pgroup": "p1", "run_number": 1}
    ).status_code == 400


def test_status_push_rejects_non_dict_values(app_and_store):
    app, _, _ = app_and_store
    resp = app.test_client().post(
        "/status/push",
        json={"pgroup": "p1", "run_number": 1, "values": ["not", "a", "dict"]},
    )
    assert resp.status_code == 400


def test_status_push_records_on_the_store(app_and_store):
    app, store, _ = app_and_store
    resp = app.test_client().post(
        "/status/push",
        json={"pgroup": "p1", "run_number": 5, "values": {"scans.acquiring_scan.a": 1}},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"status": "ok", "pgroup": "p1", "run_number": 5,
                    "key": "status_run_start", "n_values": 1}
    assert store.pop_pushed_status("p1", 5, "status_run_start") == {
        "scans.acquiring_scan.a": 1
    }


def test_status_push_merges_into_the_next_capture_job_read(app_and_store, tmp_path):
    """This is the real flow: capture (server-pollable values) runs first,
    the client pushes what it alone knows a moment later, and the *next*
    read with include_status=1 sees both merged into one status dict."""
    app, _, _ = app_and_store
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    client = app.test_client()
    job_id = client.post(
        "/status/capture",
        json={"pgroup": "p1", "run_number": 6, "upload": False, "keep_status": True},
    ).get_json()["job_id"]

    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert job["state"] == "done"

    client.post(
        "/status/push",
        json={"pgroup": "p1", "run_number": 6,
              "values": {"scans.acquiring_scan.description": "a scan"}},
    )

    body = client.get(f"/status/job/{job_id}?include_status=1").get_json()["job"]
    assert body["status"]["fake.a"] == "A"  # the server's own polled value
    assert body["status"]["scans.acquiring_scan.description"] == "a scan"
    assert body["n_pushed"] == 1

    # handed over once, same as the job's own status
    body_again = client.get(f"/status/job/{job_id}?include_status=1").get_json()["job"]
    assert "status" not in body_again


def test_status_push_arriving_after_the_capture_write_still_lands(
    app_and_store, tmp_path
):
    """The push does not have to race the server's own (potentially
    multi-second) snapshot - only the client's later job read."""
    app, _, _ = app_and_store
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    client = app.test_client()
    job_id = client.post(
        "/status/capture",
        json={"pgroup": "p1", "run_number": 8, "upload": False, "keep_status": True},
    ).get_json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)

    # a push for an unrelated run must not show up here
    client.post(
        "/status/push",
        json={"pgroup": "p1", "run_number": 999, "values": {"x": 1}},
    )
    body = client.get(f"/status/job/{job_id}?include_status=1").get_json()["job"]
    assert "x" not in body["status"]
    assert "n_pushed" not in body


def test_a_pending_push_is_not_stolen_by_an_unrelated_job_kind(
    app_and_store, tmp_path
):
    """aliases-capture (and recording-capture) jobs carry pgroup/run_number
    for their own reporting but no "key" - the merge in /status/job must not
    default one, or a push meant for the real status_run_start job of the
    same run could be consumed by whichever job happens to be read first
    with include_status=1."""
    app, _, _ = app_and_store
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    client = app.test_client()

    client.post(
        "/status/push",
        json={"pgroup": "p1", "run_number": 20,
              "values": {"scans.acquiring_scan.description": "a scan"}},
    )

    alias_job_id = client.post(
        "/aliases/capture",
        json={"pgroup": "p1", "run_number": 20, "upload": False},
    ).get_json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{alias_job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)

    # reading the aliases job with include_status=1 must not consume the
    # push meant for the run's real status_run_start job
    body = client.get(
        f"/status/job/{alias_job_id}?include_status=1"
    ).get_json()["job"]
    assert "status" not in body
    assert "n_pushed" not in body

    # it is still there for the job it was actually meant for
    status_job_id = client.post(
        "/status/capture",
        json={"pgroup": "p1", "run_number": 20, "upload": False,
              "keep_status": True},
    ).get_json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{status_job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    body = client.get(
        f"/status/job/{status_job_id}?include_status=1"
    ).get_json()["job"]
    assert body["status"]["scans.acquiring_scan.description"] == "a scan"


# --------------------------------------------------------------------------
# NamespaceMonitorStore.push_status/pop_pushed_status directly: things with
# no CA channel (e.g. scans.acquiring_scan.*) that snapshot() can never see
# on its own -- see push_status's docstring. (Deliberately placed late in
# this file, not next to the other store-lifecycle tests up top: those run
# store._build_worker() on a background thread and are sensitive to overall
# process load, and extra tests immediately ahead of them was enough to
# perturb timing in test_target_names_from_required_without_writing_required_names.)


def test_push_status_and_pop_pushed_status_round_trip(fake_module):
    name, _ = fake_module()
    store = _store(name)
    assert store.wait_ready(timeout=10)
    store.push_status("p1", 3, "status_run_start", {"a.b": 1})
    assert store.pop_pushed_status("p1", 3, "status_run_start") == {"a.b": 1}


def test_pop_pushed_status_consumes_the_value(fake_module):
    """Handed over once, same as a job's own status - not re-readable."""
    name, _ = fake_module()
    store = _store(name)
    store.push_status("p1", 3, "status_run_start", {"a.b": 1})
    assert store.pop_pushed_status("p1", 3, "status_run_start") == {"a.b": 1}
    assert store.pop_pushed_status("p1", 3, "status_run_start") == {}


def test_pop_pushed_status_ignores_run_number_type(fake_module):
    """The client sends run_number as an int; a job dict may carry it as
    whatever JSON gave it back - both must resolve to the same key."""
    name, _ = fake_module()
    store = _store(name)
    store.push_status("p1", 3, "status_run_start", {"a.b": 1})
    assert store.pop_pushed_status("p1", "3", "status_run_start") == {"a.b": 1}


def test_pop_pushed_status_is_scoped_by_pgroup_run_number_and_key(fake_module):
    name, _ = fake_module()
    store = _store(name)
    store.push_status("p1", 3, "status_run_start", {"a.b": 1})
    assert store.pop_pushed_status("p2", 3, "status_run_start") == {}
    assert store.pop_pushed_status("p1", 4, "status_run_start") == {}
    assert store.pop_pushed_status("p1", 3, "status_run_end") == {}
    assert store.pop_pushed_status("p1", 3, "status_run_start") == {"a.b": 1}


def test_push_status_replaces_not_merges(fake_module):
    """The caller always sends its full current view, not a delta."""
    name, _ = fake_module()
    store = _store(name)
    store.push_status("p1", 3, "status_run_start", {"a.b": 1, "a.c": 2})
    store.push_status("p1", 3, "status_run_start", {"a.b": 9})
    assert store.pop_pushed_status("p1", 3, "status_run_start") == {"a.b": 9}


def test_pop_pushed_status_drops_a_push_older_than_max_age(fake_module, monkeypatch):
    name, _ = fake_module()
    store = _store(name)
    t = [1000.0]
    monkeypatch.setattr(ns_store.time, "time", lambda: t[0])
    store.push_status("p1", 3, "status_run_start", {"a.b": 1})
    t[0] += 400
    assert store.pop_pushed_status("p1", 3, "status_run_start", max_age=300) == {}


def test_push_status_rejects_non_dict_values(fake_module):
    name, _ = fake_module()
    store = _store(name)
    with pytest.raises(TypeError):
        store.push_status("p1", 3, "status_run_start", ["not", "a", "dict"])


# --------------------------------------------------------------------------
# /aliases/capture: compute + write + upload, all off the client's clock


def test_aliases_capture_returns_immediately_and_does_the_work_in_the_background(
    app_and_store, tmp_path, monkeypatch
):
    app, _, _ = app_and_store
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    posted = []
    import requests

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "ok", "message": "copying user file(s) finished"}

    monkeypatch.setattr(
        requests, "post", lambda url, json=None, timeout=None: (
            posted.append((url, json)), _Resp)[1]
    )

    client = app.test_client()
    started = client.post(
        "/aliases/capture", json={"pgroup": "p1", "run_number": 3}
    )
    assert started.status_code == 202
    job_id = started.get_json()["job_id"]

    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert job["state"] == "done", job
    path = tmp_path / "p1" / "run0003" / "aux" / "aliases.json"
    assert path.exists()
    written = json.loads(path.read_text())
    assert {"alias": "fake.a", "channel": "PV:A", "channeltype": "CA"} in written

    # the server, not the client, handed the file to the broker
    url, body = posted[0]
    assert url.endswith("/copy_user_files")
    assert body["pgroup"] == "p1" and body["run_number"] == 3
    assert body["files"] == [str(path)]
    assert job["upload"]["status"] == "ok"


def test_aliases_capture_can_skip_the_upload(app_and_store, tmp_path, monkeypatch):
    app, _, _ = app_and_store
    app.config["ECO_CONFIG"].data_root_pattern = (
        str(tmp_path) + "/{pgroup}/run{run_number:04d}/aux"
    )
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **kw: pytest.fail("uploaded"))
    client = app.test_client()
    job_id = client.post(
        "/aliases/capture", json={"pgroup": "p1", "run_number": 4, "upload": False}
    ).get_json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert job["state"] == "done"
    assert "upload" not in job


def test_aliases_capture_requires_pgroup_and_run_number(app_and_store):
    app, _, _ = app_and_store
    assert app.test_client().post("/aliases/capture", json={}).status_code == 400


def test_aliases_capture_is_refused_while_not_ready(fake_module):
    name, _ = fake_module(names=[f"n{i}" for i in range(10)], init_delay=0.05)
    store = _store(name)
    app = create_namespace_app(NamespaceServerConfig(module_name=name), store=store)
    resp = app.test_client().post(
        "/aliases/capture", json={"pgroup": "p1", "run_number": 1}
    )
    assert resp.status_code == 503
    assert store.wait_ready(timeout=20)


def test_health_reports_failed_required_components_separately(fake_module):
    """A failed component that is in required_names() is the one a client has
    to be told about loudly - the setup is not supposed to fail those."""
    name, mod = fake_module(names=["a", "b", "c"], required=["a", "b"], fail=["b", "c"])
    store = _store(name, init_required_only=False)
    assert store.wait_ready(timeout=10)

    report = store.connection_report()
    assert report["failed_names"] == ["b", "c"]
    assert report["failed_required"] == ["b"], "c is not required, b is"
    assert report["n_failed_required"] == 1

    app = create_namespace_app(NamespaceServerConfig(module_name=name), store=store)
    body = app.test_client().get("/health").get_json()
    assert body["failed_required"] == ["b"]


def test_no_failed_required_is_reported_as_empty(fake_module):
    name, _ = fake_module(names=["a", "b"], required=["a"], fail=["b"])
    store = _store(name, init_required_only=False)
    assert store.wait_ready(timeout=10)
    report = store.connection_report()
    assert report["failed_names"] == ["b"]
    assert report["failed_required"] == []


def test_the_client_warns_in_red_about_failed_required(capsys):
    from eco.status_server.client import warn_failed_required

    warned = warn_failed_required({"failed_required": ["mon_und", "scilog"]})
    out = capsys.readouterr().out
    assert warned == ["mon_und", "scilog"]
    assert "REQUIRED" in out and "mon_und" in out and "scilog" in out
    assert "\x1b[" in out, "should be colourised"


def test_the_client_stays_quiet_when_nothing_required_failed(capsys):
    from eco.status_server.client import warn_failed_required

    assert warn_failed_required({"failed_required": [], "failed_names": ["x"]}) == []
    assert warn_failed_required({}) == []
    assert capsys.readouterr().out == ""
