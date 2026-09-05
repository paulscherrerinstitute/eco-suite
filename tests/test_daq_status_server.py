"""The `status_server=` switch on eco.acquisition.daq_client.Daq.

Daq.__init__ builds PVs and an Assembly, so these tests drive the three
status-related callbacks on a bare Daq instance with only the attributes
they actually touch set - the point is the branch logic and the fallback,
not the rest of the class.
"""

import time
import types

import pytest

from eco.acquisition.daq_client import Daq


class FakeScan:
    def __init__(self, runno=42):
        self.daq_run_number = types.SimpleNamespace(
            get_current_value=lambda: runno
        )
        self._scratch = {}
        self.scan_parameters = {}
        self._values_done = [1]

    def counter_scratch(self, name):
        return self._scratch.setdefault(name, {})

    def values_done(self):
        return self._values_done

    def set_scan_parameter(self, key, value):
        self.scan_parameters[key] = value


class FakeStatusClient:
    base_url = "http://fake:8091"

    def __init__(self, snapshot=None, health=None, fail_with=None):
        self._snapshot = snapshot or {
            "status": {"bernina.a": 1},
            "status_channels": {"bernina.a": "PV:A"},
            "status_times": {"bernina.a": 0.0},
            "selections": {},
            "saved_to": "/data/p1/run0042/aux/status.json",
            "generation": 3,
            "snapshot_seconds": 0.12,
        }
        self._health = health or {
            "ready": True, "n_initialized": 80, "n_target_names": 82, "n_failed": 2
        }
        self._fail_with = fail_with
        self.calls = []

    def snapshot(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail_with:
            raise self._fail_with
        return dict(self._snapshot)

    def wait_ready(self, timeout=None, progress=False):
        if self._fail_with:
            raise self._fail_with
        return self._health

    def health(self):
        if self._fail_with:
            raise self._fail_with
        return {"ready": True, "state": "ready",
                "last_init_finished": time.time(), **self._health}


def _daq(status_server=None, namespace=None, strict=False):
    daq = Daq.__new__(Daq)
    daq.name = "daq"
    daq._pgroup = "p12345"
    daq.namespace = namespace
    daq._status_server = status_server
    daq._status_server_client = status_server
    daq.status_server_timeout = 5.0
    daq.status_server_snapshot_timeout = 60.0
    daq.status_server_strict = strict
    daq.status_server_wait_ready = 30
    daq.status_server_max_age = 12 * 3600
    daq.status_server_stale_action = "ask"
    daq.status_server_stale_timeout = 0.01
    # the pre-existing tests below cover the synchronous path; the async one
    # has its own set further down
    daq.status_server_async = False
    daq.aux_calls = []
    daq.append_aux = lambda *a, **kw: daq.aux_calls.append((a, kw))
    return daq


class FakeNamespace:
    def __init__(self):
        self.init_calls = []
        self.status_calls = []

    def init_all(self, **kwargs):
        self.init_calls.append(kwargs)

    def get_status(self, **kwargs):
        self.status_calls.append(kwargs)
        return {"status": {"bernina.a": 99}, "status_channels": {}}


def test_status_client_is_built_from_a_url_string():
    daq = Daq.__new__(Daq)
    daq._status_server = "http://host:8091/"
    daq._status_server_client = None
    daq.status_server_timeout = 3.0
    daq.status_server_snapshot_timeout = 60.0
    client = daq.status_client
    assert client.base_url == "http://host:8091"
    assert daq.status_client is client  # cached


def test_no_status_server_means_no_client():
    daq = Daq.__new__(Daq)
    daq._status_server = None
    daq._status_server_client = None
    assert daq.status_client is None


def test_init_namespace_uses_server_and_skips_local_init(capsys):
    ns = FakeNamespace()
    daq = _daq(status_server=FakeStatusClient(), namespace=ns)
    daq.init_namespace()
    assert ns.init_calls == []
    assert "Using status server" in capsys.readouterr().out


def test_init_namespace_falls_back_to_local_init_when_server_is_down(capsys):
    ns = FakeNamespace()
    daq = _daq(
        status_server=FakeStatusClient(fail_with=TimeoutError("no server")),
        namespace=ns,
    )
    daq.init_namespace()
    assert len(ns.init_calls) == 1
    assert ns.init_calls[0]["background"] is False
    out = capsys.readouterr().out
    assert "WARNING" in out and "falling back" in out


def test_init_namespace_strict_mode_raises_instead_of_falling_back():
    ns = FakeNamespace()
    daq = _daq(
        status_server=FakeStatusClient(fail_with=TimeoutError("no server")),
        namespace=ns,
        strict=True,
    )
    with pytest.raises(RuntimeError, match="status server"):
        daq.init_namespace()
    assert ns.init_calls == []


def test_start_status_uses_server_and_uploads_the_server_written_file():
    client = FakeStatusClient()
    daq = _daq(status_server=client, namespace=FakeNamespace())
    scan = FakeScan(runno=42)
    daq.append_start_status_to_scan(scan=scan)

    assert client.calls[0]["save"] is True
    assert client.calls[0]["key"] == "status_run_start"
    assert client.calls[0]["run_number"] == 42
    assert client.calls[0]["pgroup"] == "p12345"

    stat = scan.counter_scratch("daq")["namespace_status"]["status_run_start"]
    assert stat["status"] == {"bernina.a": 1}
    # server-only bookkeeping is stripped before it reaches the run table
    assert "saved_to" not in stat and "generation" not in stat

    (args, kwargs) = daq.aux_calls[0]
    assert args[0] == "/data/p1/run0042/aux/status.json"
    assert kwargs["run_number"] == 42


def test_start_status_falls_back_to_local_namespace_on_server_error(monkeypatch):
    ns = FakeNamespace()
    daq = _daq(
        status_server=FakeStatusClient(fail_with=ConnectionError("refused")),
        namespace=ns,
    )
    scan = FakeScan()
    # stop before the local file write, which targets a real beamline path
    monkeypatch.setattr(daq, "get_last_run_number", lambda: 42, raising=False)
    with pytest.raises(Exception):
        # the local branch will fail writing to /sf/bernina/data/... in a
        # test environment; what matters is that it got that far, i.e. it
        # really did fall back instead of silently doing nothing.
        daq.append_start_status_to_scan(scan=scan)
    assert ns.status_calls, "did not fall back to namespace.get_status()"


def test_end_status_uses_server_and_sets_the_scan_parameter():
    client = FakeStatusClient()
    daq = _daq(status_server=client, namespace=FakeNamespace())
    scan = FakeScan(runno=7)
    daq.append_status_to_scan_and_store(scan)
    assert client.calls[0]["key"] == "status_run_end"
    assert scan.scan_parameters["status"] == "aux/status.json"
    assert (
        "status_run_end" in scan.counter_scratch("daq")["namespace_status"]
    )
    assert daq.aux_calls


def test_end_status_skipped_when_no_steps_were_done():
    client = FakeStatusClient()
    daq = _daq(status_server=client, namespace=FakeNamespace())
    scan = FakeScan()
    scan._values_done = []
    daq.append_status_to_scan_and_store(scan)
    assert client.calls == []


def test_async_write_job_is_joined_before_append_aux():
    class AsyncClient(FakeStatusClient):
        def __init__(self):
            super().__init__()
            self._snapshot["write_job_id"] = "job1"
            self.joined = []

        def wait_write_job(self, job_id, timeout=None):
            self.joined.append(job_id)
            return {"state": "done"}

    client = AsyncClient()
    daq = _daq(status_server=client, namespace=FakeNamespace())
    result = daq._status_from_server("status_run_start", 5, "p1", write_async=True)
    assert client.joined == ["job1"]
    assert result[1] == "/data/p1/run0042/aux/status.json"


def test_snapshot_and_health_get_separate_timeouts():
    """A single 10 s timeout made every real snapshot fail: /health answers
    in milliseconds, but a get_status() fan-out over ~14k bernina channels
    takes 10-20 s."""
    daq = Daq.__new__(Daq)
    daq._status_server = "http://host:8091"
    daq._status_server_client = None
    daq.status_server_timeout = 10.0
    daq.status_server_snapshot_timeout = 180.0
    client = daq.status_client
    assert client.timeout == 10.0
    assert client.snapshot_timeout == 180.0


# --------------------------------------------------------------------------
# freshness gate and the async capture path


def _health(ready=True, age_s=0.0, **extra):
    return {"ready": ready, "state": "ready" if ready else "initializing",
            "last_init_finished": time.time() - age_s,
            "n_initialized": 80, "n_target_names": 82, "n_failed": 2, **extra}


class HealthClient(FakeStatusClient):
    base_url = "http://fake:8091"

    def __init__(self, health=None, capture_fail=None):
        super().__init__()
        self._health_body = health if health is not None else _health()
        self.captures = []
        self.reinits = 0
        self._capture_fail = capture_fail

    def health(self):
        return self._health_body

    def capture(self, **kwargs):
        if self._capture_fail:
            raise self._capture_fail
        self.captures.append(kwargs)
        return {"job_id": "j1", "path": "/data/p1/run0042/aux/status.json"}

    def wait_write_job(self, job_id, timeout=None):
        return {"state": "done", "path": "/data/p1/run0042/aux/status.json"}

    def reinit(self, **kwargs):
        self.reinits += 1
        self._health_body = _health(age_s=0.0)
        return self._health_body


def _daq_fresh(client, **kw):
    daq = _daq(status_server=client, namespace=FakeNamespace())
    daq.status_server_max_age = kw.pop("max_age", 12 * 3600)
    daq.status_server_stale_action = kw.pop("stale_action", "ask")
    daq.status_server_stale_timeout = kw.pop("stale_timeout", 0.01)
    daq.status_server_async = kw.pop("async_", True)
    return daq


def test_fresh_server_is_used():
    daq = _daq_fresh(HealthClient(_health(age_s=3600)))
    assert daq.use_status_server() is True


def test_unready_server_is_not_used(capsys):
    daq = _daq_fresh(HealthClient(_health(ready=False)))
    assert daq.use_status_server(verbose=True) is False
    assert "initializing" in capsys.readouterr().out


def test_unreachable_server_is_not_used(capsys):
    daq = _daq_fresh(HealthClient())
    daq._status_server_client._health_body = None

    def boom():
        raise ConnectionError("refused")

    daq._status_server_client.health = boom
    assert daq.use_status_server(verbose=True) is False
    assert "not reachable" in capsys.readouterr().out


def test_stale_server_falls_back_when_told_to(capsys):
    client = HealthClient(_health(age_s=20 * 3600))
    daq = _daq_fresh(client, stale_action="local")
    assert daq.use_status_server(verbose=True) is False
    assert client.reinits == 0
    assert "13.0 h" not in capsys.readouterr().out  # 20 h, not the limit


def test_stale_server_can_be_trusted_anyway():
    client = HealthClient(_health(age_s=20 * 3600))
    daq = _daq_fresh(client, stale_action="use")
    assert daq.use_status_server() is True
    assert client.reinits == 0


def test_stale_server_is_restarted_and_then_used(capsys):
    client = HealthClient(_health(age_s=20 * 3600))
    daq = _daq_fresh(client, stale_action="restart")
    assert daq.use_status_server() is True
    assert client.reinits == 1
    assert "refreshed" in capsys.readouterr().out


def test_stale_prompt_without_a_tty_takes_the_restart(capsys):
    """A scan started from a script has nobody to answer; the timeout takes
    the option that leaves the next run fast."""
    client = HealthClient(_health(age_s=20 * 3600))
    daq = _daq_fresh(client, stale_action="ask")
    assert daq.use_status_server() is True
    assert client.reinits == 1
    out = capsys.readouterr().out
    assert "20.0 h ago" in out and "restarting" in out


def test_stale_prompt_answered_with_l_uses_the_local_namespace(monkeypatch):
    import inputimeout as _inputimeout

    client = HealthClient(_health(age_s=20 * 3600))
    daq = _daq_fresh(client, stale_action="ask")
    monkeypatch.setattr(_inputimeout, "inputimeout", lambda **kw: "l")
    assert daq.use_status_server() is False
    assert client.reinits == 0


def test_max_age_none_disables_the_check():
    client = HealthClient(_health(age_s=1000 * 3600))
    daq = _daq_fresh(client, max_age=None)
    assert daq.use_status_server() is True
    assert client.reinits == 0


def test_scan_start_delegates_the_whole_capture_to_the_server(capsys):
    client = HealthClient()
    daq = _daq_fresh(client)
    scan = FakeScan(runno=42)
    daq.append_start_status_to_scan(scan=scan)

    assert client.captures == [
        {"pgroup": "p12345", "run_number": 42, "key": "status_run_start",
         "upload": True, "keep_status": True}
    ]
    # the client neither waits for the values nor uploads the file itself
    assert client.calls == []
    assert daq.aux_calls == []
    block = scan.counter_scratch("daq")["namespace_status"]["status_run_start"]
    assert block["written_by_status_server"].endswith("status.json")
    assert "delegated" in capsys.readouterr().out


def test_scan_end_delegates_and_sets_the_scan_parameter():
    client = HealthClient()
    daq = _daq_fresh(client)
    scan = FakeScan(runno=7)
    daq.append_status_to_scan_and_store(scan)
    assert client.captures[0]["key"] == "status_run_end"
    assert scan.scan_parameters["status"] == "aux/status.json"


def test_the_server_decision_is_taken_once_per_scan():
    """Start from the server and end from the local namespace would be a
    quietly inconsistent run."""
    client = HealthClient()
    daq = _daq_fresh(client)
    scan = FakeScan(runno=7)
    calls = []
    real = daq.use_status_server
    daq.use_status_server = lambda **kw: (calls.append(1), real(**kw))[1]
    daq.append_start_status_to_scan(scan=scan)
    daq.append_status_to_scan_and_store(scan)
    assert len(calls) == 1


def test_capture_failure_falls_back_to_the_local_path(monkeypatch):
    client = HealthClient(capture_fail=ConnectionError("refused"))
    ns = FakeNamespace()
    daq = _daq_fresh(client)
    daq.namespace = ns
    scan = FakeScan()
    monkeypatch.setattr(daq, "get_last_run_number", lambda **kw: 42, raising=False)
    with pytest.raises(Exception):
        daq.append_start_status_to_scan(scan=scan)
    assert ns.status_calls, "did not fall back to namespace.get_status()"


def test_write_status_waits_for_the_job_when_called_standalone(monkeypatch):
    client = HealthClient()
    daq = _daq_fresh(client)
    monkeypatch.setattr(daq, "get_last_run_number", lambda **kw: 42, raising=False)
    path = daq.write_status(pgroup="p1", run_number=42)
    assert str(path).endswith("run0042/aux/status.json")
    assert client.captures[0]["run_number"] == 42


# --------------------------------------------------------------------------
# the run table is filled from the server's values, not from its own CA reads


class RunTableSpy:
    def __init__(self):
        self.calls = []

    def append_run(self, runno, metadata=None, d=None, **kw):
        self.calls.append({"runno": runno, "metadata": metadata, "d": d})


class RunTableScan(FakeScan):
    """FakeScan plus the attributes the run-table callback reads."""

    def __init__(self, runno=42):
        super().__init__(runno)
        get = lambda v: types.SimpleNamespace(get_current_value=lambda: v)
        self.description = get("a scan")
        self.values_todo = get([[0.0], [1.0]])
        self.counters_names = get(["daq"])
        self.scan_command = get("ascan(...)")
        self.pulses_per_step = [10, 10]
        self.adjustables = [types.SimpleNamespace(name="dummy", Id="PV:DUMMY")]


def _runtable_daq(client, run_table):
    daq = _daq_fresh(client)
    daq.run_table = run_table
    return daq


def test_runtable_gets_the_flat_status_mapping_not_the_whole_block():
    rt = RunTableSpy()
    daq = _runtable_daq(HealthClient(), rt)
    scan = RunTableScan()
    scan.counter_scratch("daq")["namespace_status"] = {
        "status_run_start": {"status": {"bernina.a": 1}, "status_channels": {}}
    }
    daq._create_runtable_metadata_append_status_to_runtable(scan)
    assert rt.calls[0]["d"] == {"bernina.a": 1}


def test_runtable_append_is_deferred_until_the_capture_finishes():
    """Filling it inline would either block the scan or make the run table do
    the CA fan-out the server exists to remove."""
    rt = RunTableSpy()
    client = HealthClient()
    daq = _runtable_daq(client, rt)
    scan = RunTableScan(runno=7)

    collected = {"job_id": "j1", "state": "done", "status": {"bernina.b": 2}}
    client.wait_write_job = lambda job_id, timeout=None, include_status=False: collected

    daq.append_start_status_to_scan(scan=scan)
    daq._create_runtable_metadata_append_status_to_runtable(scan)

    deadline = time.time() + 5
    while time.time() < deadline and not rt.calls:
        time.sleep(0.02)
    assert rt.calls, "run table row was never appended"
    assert rt.calls[0]["d"] == {"bernina.b": 2}
    assert rt.calls[0]["runno"] == 7


def test_runtable_row_is_still_written_when_the_capture_fails(capsys):
    """A run with no run-table row is worse than one filled the slow way."""
    rt = RunTableSpy()
    client = HealthClient()
    daq = _runtable_daq(client, rt)
    scan = RunTableScan(runno=8)

    def _boom(job_id, timeout=None, include_status=False):
        raise ConnectionError("server went away")

    client.wait_write_job = _boom
    daq.append_start_status_to_scan(scan=scan)
    daq._create_runtable_metadata_append_status_to_runtable(scan)

    deadline = time.time() + 5
    while time.time() < deadline and not rt.calls:
        time.sleep(0.02)
    assert rt.calls and rt.calls[0]["d"] == {}
    assert "could not collect run 8 status" in capsys.readouterr().out


def test_runtable_append_is_inline_when_the_status_is_already_there():
    rt = RunTableSpy()
    daq = _runtable_daq(HealthClient(), rt)
    daq.status_server_async = False
    scan = RunTableScan(runno=9)
    scan.counter_scratch("daq")["namespace_status"] = {
        "status_run_start": {"status": {"bernina.c": 3}}
    }
    daq._create_runtable_metadata_append_status_to_runtable(scan)
    assert rt.calls[0]["d"] == {"bernina.c": 3}   # no thread involved


def test_only_the_start_block_keeps_its_values_on_the_server():
    """Holding a few MB for a block nobody collects is just a leak."""
    client = HealthClient()
    daq = _daq_fresh(client)
    daq.append_start_status_to_scan(scan=RunTableScan(runno=11))
    daq.append_status_to_scan_and_store(RunTableScan(runno=11))
    assert client.captures[0]["keep_status"] is True
    assert client.captures[1]["keep_status"] is False
