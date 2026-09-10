"""eco.status_server.namespace_component.StatusServer: the namespace-level
handle for a long-running status server (bernina.status_server, alongside
daq/scans) - control, inspection, and the recording settings as real
AdjustableFS children (filesystem-based, so another process reading the
same shared config tree sees a changed setting with no REST call needed).

The underlying StatusServerClient is monkeypatched out entirely - these
tests are about StatusServer's own delegation/settings/gui-launch logic,
not the HTTP client (already covered in test_status_server.py). The
settings' config_dir is pointed at tmp_path, never the real shared
eco_cnf_bernina tree.
"""

import subprocess

import pytest

from eco.status_server.namespace_component import StatusServer


@pytest.fixture
def server(monkeypatch, tmp_path):
    calls = {}

    class FakeClient:
        def __init__(self, base_url):
            self.base_url = base_url

        def health(self):
            calls.setdefault("health", 0)
            calls["health"] += 1
            return {
                "state": "ready", "n_initialized": 80, "n_target_names": 82,
                "n_failed": 1, "n_monitorable": 9000, "generation": 3,
                "uptime_s": 120.0, "failed_required": [],
            }

        def failures(self):
            return {"x": "boom"}

        def stats(self, limit=None, kind=None):
            calls["stats"] = {"limit": limit, "kind": kind}
            return {"summary": {}, "recent": []}

        def monitor_policy(self):
            return {"max_rate_hz": 10.0, "auto_monitor_default": True}

        def restart(self, wait=True, timeout=1800, progress=True):
            calls["restart"] = {"wait": wait, "timeout": timeout, "progress": progress}
            return {"status": "ok"}

        def reinit(self, mode="failed", wait=True, **kwargs):
            calls["reinit"] = {"mode": mode, "wait": wait, **kwargs}
            return {"status": "ok"}

    s = StatusServer("http://fake:8091", name="status_server", config_dir=tmp_path)
    s._client = FakeClient("http://fake:8091")
    s._calls = calls
    return s


# --------------------------------------------------------------------------
# settings: real AdjustableFS children (filesystem-based), not yet
# consumed by Daq or the server


def test_recording_settings_have_sensible_defaults(server):
    assert server.recording_mode.get_current_value() == "throttle"
    assert server.recording_min_interval.get_current_value() == 0.1
    assert server.recording_sample_interval.get_current_value() == 0.1
    assert server.recording_max_value_elements.get_current_value() is None
    assert server.recording_max_points_per_channel.get_current_value() == 100_000


def test_recording_settings_are_settable(server):
    # set_target_value(hold=False, the default) starts the write on a
    # background thread and returns immediately - wait() before asserting,
    # same as any other Changer-backed Adjustable.
    server.recording_mode.set_target_value("all").wait()
    server.recording_max_value_elements.set_target_value(1024).wait()
    assert server.recording_mode.get_current_value() == "all"
    assert server.recording_max_value_elements.get_current_value() == 1024


def test_a_setting_changed_in_one_process_is_visible_in_another(tmp_path):
    """The actual point of AdjustableFS over AdjustableMemory: a second
    StatusServer instance (standing in for a second session, or the server
    process itself, reading the same shared config tree) sees a value
    changed by the first with no communication between the two at all -
    just the shared filesystem."""
    a = StatusServer("http://fake:8091", name="a", config_dir=tmp_path)
    b = StatusServer("http://fake:8091", name="b", config_dir=tmp_path)

    a.recording_mode.set_target_value("sample").wait()
    assert b.recording_mode.get_current_value() == "sample"

    b.recording_max_value_elements.set_target_value(2048).wait()
    assert a.recording_max_value_elements.get_current_value() == 2048


# --------------------------------------------------------------------------
# control: delegates straight to the client


def test_restart_delegates_to_the_client(server):
    result = server.restart(wait=False, timeout=10, progress=False)
    assert result == {"status": "ok"}
    assert server._calls["restart"] == {"wait": False, "timeout": 10, "progress": False}


def test_reinit_delegates_to_the_client(server):
    result = server.reinit(mode="full")
    assert result == {"status": "ok"}
    assert server._calls["reinit"]["mode"] == "full"


# --------------------------------------------------------------------------
# inspection


def test_status_prints_a_summary_and_returns_health(server, capsys):
    h = server.status()
    assert h["state"] == "ready"
    out = capsys.readouterr().out
    assert "ready" in out
    assert "80/82" in out


def test_status_warns_in_red_about_failed_required(server, capsys):
    server._client.health = lambda: {
        "state": "ready", "n_initialized": 80, "n_target_names": 82,
        "n_failed": 1, "n_monitorable": 9000, "generation": 3,
        "uptime_s": 120.0, "failed_required": ["tt_kb"],
    }
    server.status()
    out = capsys.readouterr().out
    assert "tt_kb" in out
    assert "REQUIRED" in out


def test_stats_and_failures_and_monitor_policy_delegate(server):
    assert server.failures() == {"x": "boom"}
    assert server.monitor_policy()["max_rate_hz"] == 10.0
    server.stats(limit=5, kind="capture")
    assert server._calls["stats"] == {"limit": 5, "kind": "capture"}


def test_repr_shows_state_without_raising(server):
    assert "ready" in repr(server)


def test_repr_also_shows_the_recording_settings(server):
    r = repr(server)
    assert "recording_mode" in r
    assert "throttle" in r
    assert "recording_max_points_per_channel" in r


def test_repr_degrades_when_unreachable(server):
    def boom():
        raise ConnectionError("refused")

    server._client.health = boom
    assert "unreachable" in repr(server)


# --------------------------------------------------------------------------
# gui: a detached subprocess, not an embedded widget


def test_gui_launches_a_detached_subprocess(server, monkeypatch):
    calls = []

    class FakeProc:
        pid = 12345

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    proc = server.gui()
    assert proc.pid == 12345
    assert calls[0][-2:] == ["--url", "http://fake:8091"]
    assert "eco.status_server.gui" in calls[0]
