"""The `status_server=` switch on eco.acquisition.daq_client.Daq.

Daq.__init__ builds PVs and an Assembly, so these tests drive the three
status-related callbacks on a bare Daq instance with only the attributes
they actually touch set - the point is the branch logic and the fallback,
not the rest of the class.
"""

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
