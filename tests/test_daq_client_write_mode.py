"""`Daq.status_server_write_mode = "client"`: who writes the run's aux files.

The server-write path these tests sit next to is unchanged and covered by
test_daq_status_server.py. What is checked here is only the switch and the
dispatch: that the default is still "server", that "client" reaches
eco.status_server.relay instead of the server's capture routes, that a
server without the /handoff routes degrades to the old path with one
message, and that the run table still gets its values when nobody on the
server side is holding them any more.

Daq.__init__ opens PVs and builds an Assembly, so - like the neighbouring
module - these drive the methods on a bare instance with only the
attributes under test set.
"""

import os
import types

import pytest

from eco.acquisition.daq_client import Daq, resolve_write_mode


class FakeClient:
    base_url = "http://fake:8091"

    def __init__(self, supports=True):
        self._supports = supports
        self.calls = []
        self.released = []
        self.pushed = []

    def supports_handoff(self):
        self.calls.append(("supports_handoff", {}))
        return self._supports

    def handoff_status(self, **kw):
        self.calls.append(("handoff_status", kw))
        return {"handoff_id": "h-status", "job_id": "srv-1"}

    def handoff_aliases(self, **kw):
        self.calls.append(("handoff_aliases", kw))
        return {"handoff_id": "h-aliases", "job_id": "srv-2"}

    def handoff_recording(self, recording_id, **kw):
        self.calls.append(("handoff_recording", dict(kw, recording_id=recording_id)))
        return {"handoff_id": "h-rec", "job_id": "srv-3"}

    def release_handoff(self, handoff_id):
        self.released.append(handoff_id)
        return {"status": "ok"}

    def push_status(self, pgroup, run_number, values, key=None):
        self.pushed.append((pgroup, run_number, values, key))
        return {"status": "ok"}

    # server-write side, so a test can assert it was *not* used
    def capture(self, **kw):
        self.calls.append(("capture", kw))
        return {"job_id": "j", "path": "/srv/status.json"}

    def capture_aliases(self, **kw):
        self.calls.append(("capture_aliases", kw))
        return {"job_id": "j", "path": "/srv/aliases.json"}


class FakeRelayJob:
    def __init__(self, spec, result=None):
        self.spec = spec
        self.job_id = spec["job_id"]
        self.kind = spec["kind"]
        self.path = spec["directory"] + "/" + spec["filename"]
        self.result_path = spec["result_path"]
        self._result = result

    def as_dict(self):
        return {"job_id": self.job_id, "path": self.path, "kind": self.kind,
                "relay": True, "result_path": self.result_path,
                "log_path": None}

    def result(self):
        return self._result

    def wait(self, timeout=None, poll=None):
        return self._result


@pytest.fixture
def fake_relay(monkeypatch):
    """Replace relay.submit so nothing is actually spawned - what is under
    test is the spec the daq builds, not the relay (test_status_relay.py)."""
    from eco.status_server import relay

    submitted = []

    def fake_submit(spec, mode="process"):
        spec = dict(spec)
        spec.setdefault("job_id", f"relay-{len(submitted)}")
        spec.setdefault("result_path", f"/jobs/{spec['job_id']}.json")
        job = FakeRelayJob(spec, result=fake_submit.result)
        submitted.append((spec, mode, job))
        return job

    fake_submit.result = {"state": "done", "status": {"bernina.a": 1},
                          "path": "/aux/status.json"}
    monkeypatch.setattr(relay, "submit", fake_submit)
    return submitted


def _daq(client, mode="client"):
    daq = Daq.__new__(Daq)
    daq.name = "daq"
    daq.instrument = "bernina"
    daq._pgroup = "p12345"
    daq.namespace = None
    daq._status_server = client
    daq._status_server_client = client
    daq.use_running_status_server = True
    daq.status_server_timeout = 5.0
    daq.status_server_snapshot_timeout = 60.0
    daq.status_server_strict = False
    daq.status_server_async = True
    daq.status_server_write_mode = mode
    daq.status_server_relay_mode = "process"
    daq.broker_address_aux = "http://broker:10003"
    daq.scan_monitoring = True
    return daq


# --------------------------------------------------------------------------
# the switch


def test_server_write_is_still_the_default():
    daq = Daq.__new__(Daq)
    assert daq.status_server_write_mode == "server"
    daq._status_server = None
    daq._status_server_client = None
    daq.use_running_status_server = True
    assert daq.use_client_write() is False


def test_the_env_var_is_the_default_when_nothing_was_passed(monkeypatch):
    monkeypatch.setenv("ECO_STATUS_SERVER_WRITE_MODE", "Client")
    assert resolve_write_mode() == "client"
    # an explicit argument still wins over the shell
    assert resolve_write_mode("server") == "server"


def test_nothing_set_anywhere_means_server(monkeypatch):
    monkeypatch.delenv("ECO_STATUS_SERVER_WRITE_MODE", raising=False)
    assert resolve_write_mode() == "server"


def test_a_typo_is_reported_and_falls_back_to_server(monkeypatch, capsys):
    monkeypatch.setenv("ECO_STATUS_SERVER_WRITE_MODE", "clinet")
    assert resolve_write_mode() == "server"
    assert "unknown status_server_write_mode" in capsys.readouterr().out


def test_client_mode_needs_a_server_that_has_the_routes(capsys):
    daq = _daq(FakeClient(supports=False))
    assert daq.use_client_write() is False
    out = capsys.readouterr().out
    assert "no /handoff routes" in out
    # probed once, not once per scan
    daq.use_client_write()
    assert sum(1 for c, _ in daq._status_server_client.calls
               if c == "supports_handoff") == 1


def test_client_mode_is_off_when_no_server_is_in_use():
    daq = _daq(FakeClient())
    daq.use_running_status_server = False
    assert daq.use_client_write() is False


# --------------------------------------------------------------------------
# dispatch


def test_status_capture_goes_to_a_relay_instead_of_the_server(fake_relay):
    client = FakeClient()
    daq = _daq(client)
    job = daq._capture_on_server("status_run_start", 42, "p12345",
                                 keep_status=True)

    assert job["relay"] is True
    assert [c for c, _ in client.calls if c == "capture"] == []
    (spec, mode, _), = fake_relay
    assert mode == "process"
    assert spec["kind"] == "status"
    assert spec["handoff_id"] == "h-status"
    assert spec["keep_status"] is True
    assert spec["directory"].endswith(
        "/sf/bernina/data/p12345/res/run_data/daq/run0042/aux"
    )
    assert spec["broker_address_aux"] == "http://broker:10003"


def test_aliases_go_to_a_relay(fake_relay):
    client = FakeClient()
    daq = _daq(client)
    job = daq._capture_aliases_on_server(7, "p12345")
    assert job["relay"] is True
    assert [c for c, _ in client.calls if c == "capture_aliases"] == []
    (spec, _, _), = fake_relay
    assert spec["kind"] == "aliases"
    assert spec["filename"] == "aliases.json"


def test_recording_goes_to_a_relay_with_the_run_filename(fake_relay):
    daq = _daq(FakeClient())
    job = daq._relay_recording("p12345_run0042", 42, "p12345")
    assert job["recording_id"] == "p12345_run0042"
    (spec, _, _), = fake_relay
    assert spec["kind"] == "recording"
    assert spec["filename"] == "namespace_monitor.ixp.h5"


def test_a_failed_handoff_request_falls_back_instead_of_raising(capsys):
    client = FakeClient()

    def boom(**kw):
        raise ConnectionError("refused")

    client.handoff_status = boom
    daq = _daq(client)
    assert daq._capture_on_server("status_run_start", 1, "p1") is None
    assert "status handoff" in capsys.readouterr().out


def test_a_relay_that_cannot_start_releases_the_slot(monkeypatch, capsys):
    from eco.status_server import relay

    def boom(spec, mode="process"):
        raise OSError("no fork for you")

    monkeypatch.setattr(relay, "submit", boom)
    client = FakeClient()
    daq = _daq(client)
    assert daq._capture_on_server("status_run_start", 1, "p1") is None
    assert client.released == ["h-status"]


def test_strict_mode_raises_on_a_handoff_failure():
    client = FakeClient()

    def boom(**kw):
        raise ConnectionError("refused")

    client.handoff_status = boom
    daq = _daq(client)
    daq.status_server_strict = True
    with pytest.raises(RuntimeError, match="status handoff"):
        daq._capture_on_server("status_run_start", 1, "p1")


# --------------------------------------------------------------------------
# waiting for one, and the run table


def test_waiting_dispatches_to_the_relay_job(fake_relay):
    daq = _daq(FakeClient())
    job = daq._capture_on_server("status_run_start", 42, "p12345")
    done = daq._wait_status_job(job, timeout=1, include_status=True)
    assert done["state"] == "done"
    assert done["status"] == {"bernina.a": 1}


def test_waiting_on_a_relay_job_from_another_session_says_so(fake_relay):
    daq = _daq(FakeClient())
    job = daq._capture_on_server("status_run_start", 42, "p12345")
    daq._status_relay_jobs.clear()
    with pytest.raises(RuntimeError, match="no live relay job"):
        daq._wait_status_job(job, timeout=1)


def test_scan_values_are_merged_locally_in_client_mode(fake_relay):
    """/status/push parks values for a server-side capture job to hand
    back. In client-write mode there is no such job, so the session has to
    put them in the run table row itself."""
    daq = _daq(FakeClient())
    scan = types.SimpleNamespace(
        adjustables=[types.SimpleNamespace(name="dummy")],
        initial_values=types.SimpleNamespace(
            get_current_value=lambda: {"dummy": 0.5}
        ),
    )
    values = daq._push_acquiring_scan_status(
        scan, {"name": "a scan", "scan_command": "ascan(...)", "steps": 2}, 7
    )
    assert values["scans.acquiring_scan.description"] == "a scan"
    assert values["scans.acquiring_scan.initial_values"] == {"dummy": 0.5}
    # nothing was pushed to the server: there is no job there to merge into
    assert daq._status_server_client.pushed == []


def test_server_mode_still_pushes_rather_than_merging_locally():
    daq = _daq(FakeClient(), mode="server")
    scan = types.SimpleNamespace(
        adjustables=[types.SimpleNamespace(name="dummy")],
        initial_values=types.SimpleNamespace(get_current_value=lambda: {}),
    )
    daq._push_acquiring_scan_status(scan, {"name": "n", "steps": 1}, 3)
    assert daq._status_server_client.pushed
    assert daq._status_server_client.pushed[0][3] == "status_run_start"
