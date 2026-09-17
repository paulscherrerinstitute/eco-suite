"""Daq.live_parse_cache: launching escape-fel's optional live parse-cache
warmer (`python -m escape.swissfel.live_reduce --mode daq-cache`) as a child
process once per scan - see escape-fel's own "Integrating live parse-cache
warming into the DAQ client" doc for what the subprocess itself does.

These tests drive Daq._launch_live_parse_cache/_live_parse_cache_supported
directly on a bare Daq instance (Daq.__new__), same style as
test_daq_status_server.py - the point is the launch/reuse/feature-gate
logic, never a real subprocess or a real escape-fel install.
"""
import sys
import types

import pytest

from eco.acquisition.daq_client import Daq, _live_parse_cache_supported


class FakeScan:
    def __init__(self, runno=42):
        self.daq_run_number = types.SimpleNamespace(get_current_value=lambda: runno)
        self._scratch = {}

    def counter_scratch(self, name):
        return self._scratch.setdefault(name, {})


class FakeProc:
    def __init__(self, cmd, running=True, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self._running = running

    def poll(self):
        return None if self._running else 0


def _daq(**overrides):
    daq = Daq.__new__(Daq)
    daq.name = "daq"
    daq.instrument = "bernina"
    daq.live_parse_cache = True
    daq.live_parse_cache_python = None
    daq.live_parse_cache_poll_interval = None
    daq.live_parse_cache_idle_polls = None
    daq.live_parse_cache_max_polls = None
    daq.live_parse_cache_parse_version = None
    daq.live_parse_cache_log_dir = None
    for k, v in overrides.items():
        setattr(daq, k, v)
    return daq


@pytest.fixture
def fake_escape_supported(monkeypatch):
    """A minimal escape.swissfel.live_reduce with daq_cache_writer, the
    exact attribute _live_parse_cache_supported checks for."""
    escape_mod = types.ModuleType("escape")
    swissfel_mod = types.ModuleType("escape.swissfel")
    live_reduce_mod = types.ModuleType("escape.swissfel.live_reduce")
    live_reduce_mod.daq_cache_writer = lambda *a, **k: None
    escape_mod.swissfel = swissfel_mod
    swissfel_mod.live_reduce = live_reduce_mod
    monkeypatch.setitem(sys.modules, "escape", escape_mod)
    monkeypatch.setitem(sys.modules, "escape.swissfel", swissfel_mod)
    monkeypatch.setitem(sys.modules, "escape.swissfel.live_reduce", live_reduce_mod)
    return live_reduce_mod


@pytest.fixture
def fake_escape_unsupported(monkeypatch):
    """escape.swissfel.live_reduce exists (e.g. an older escape-fel with
    unrelated live_reduce functionality) but lacks daq_cache_writer."""
    escape_mod = types.ModuleType("escape")
    swissfel_mod = types.ModuleType("escape.swissfel")
    live_reduce_mod = types.ModuleType("escape.swissfel.live_reduce")
    escape_mod.swissfel = swissfel_mod
    swissfel_mod.live_reduce = live_reduce_mod
    monkeypatch.setitem(sys.modules, "escape", escape_mod)
    monkeypatch.setitem(sys.modules, "escape.swissfel", swissfel_mod)
    monkeypatch.setitem(sys.modules, "escape.swissfel.live_reduce", live_reduce_mod)
    return live_reduce_mod


@pytest.fixture
def fake_escape_missing(monkeypatch):
    """No escape-fel at all (or one without a swissfel.live_reduce module)."""
    monkeypatch.delitem(sys.modules, "escape", raising=False)
    monkeypatch.delitem(sys.modules, "escape.swissfel", raising=False)
    monkeypatch.delitem(sys.modules, "escape.swissfel.live_reduce", raising=False)
    import builtins
    real_import = builtins.__import__

    def _blocked_import(name, *a, **k):
        if name.startswith("escape"):
            raise ImportError(f"no module named {name}")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)


def test_supported_is_true_when_daq_cache_writer_exists(fake_escape_supported):
    assert _live_parse_cache_supported() is True


def test_supported_is_false_when_live_reduce_lacks_daq_cache_writer(
    fake_escape_unsupported,
):
    assert _live_parse_cache_supported() is False


def test_supported_is_false_when_escape_is_not_installed(fake_escape_missing):
    assert _live_parse_cache_supported() is False


def test_disabled_never_even_checks_support_or_launches(monkeypatch):
    daq = _daq(live_parse_cache=False)
    monkeypatch.setattr(
        "eco.acquisition.daq_client._live_parse_cache_supported",
        lambda: pytest.fail("should not be called when live_parse_cache is off"),
    )
    scan = FakeScan()
    assert daq._launch_live_parse_cache(scan, "p12345", 42) is None


def test_enabled_but_unsupported_escape_is_a_noop(fake_escape_unsupported):
    daq = _daq()
    scan = FakeScan()
    assert daq._launch_live_parse_cache(scan, "p12345", 42) is None
    assert "live_parse_cache_proc" not in scan.counter_scratch("daq")


def test_launches_with_the_expected_command(fake_escape_supported, monkeypatch):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc(cmd)

    monkeypatch.setattr(
        "eco.acquisition.daq_client.subprocess.Popen", fake_popen
    )
    daq = _daq()
    scan = FakeScan()
    proc = daq._launch_live_parse_cache(scan, "p12345", 42)

    assert proc is not None
    cmd = captured["cmd"]
    assert cmd[0] == sys.executable
    assert cmd[1:5] == ["-m", "escape.swissfel.live_reduce", "--mode", "daq-cache"]
    assert "--run-number" in cmd and "42" in cmd
    assert "--pgroup" in cmd and "p12345" in cmd
    assert "--instrument" in cmd and "bernina" in cmd
    # none of the optional knobs were set -> none of their flags appear
    assert "--poll-interval" not in cmd
    assert "--idle-polls-before-final-pass" not in cmd
    assert "--max-polls" not in cmd
    assert "--parse-version" not in cmd


def test_optional_knobs_are_passed_through_when_set(fake_escape_supported, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "eco.acquisition.daq_client.subprocess.Popen",
        lambda cmd, **kw: captured.setdefault("cmd", cmd) or FakeProc(cmd),
    )
    daq = _daq(
        live_parse_cache_python="/custom/python",
        live_parse_cache_poll_interval=5,
        live_parse_cache_idle_polls=3,
        live_parse_cache_max_polls=100,
        live_parse_cache_parse_version=2,
    )
    daq._launch_live_parse_cache(FakeScan(), "p12345", 42)

    cmd = captured["cmd"]
    assert cmd[0] == "/custom/python"
    assert "--poll-interval" in cmd and "5" in cmd
    assert "--idle-polls-before-final-pass" in cmd and "3" in cmd
    assert "--max-polls" in cmd and "100" in cmd
    assert "--parse-version" in cmd and "2" in cmd


def test_a_second_call_reuses_the_still_running_process(fake_escape_supported, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eco.acquisition.daq_client.subprocess.Popen",
        lambda cmd, **kw: calls.append(cmd) or FakeProc(cmd, running=True),
    )
    daq = _daq()
    scan = FakeScan()

    first = daq._launch_live_parse_cache(scan, "p12345", 42)
    second = daq._launch_live_parse_cache(scan, "p12345", 42)

    assert first is second
    assert len(calls) == 1  # only launched once


def test_relaunches_if_the_previous_process_already_exited(
    fake_escape_supported, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        "eco.acquisition.daq_client.subprocess.Popen",
        lambda cmd, **kw: calls.append(cmd) or FakeProc(cmd, running=False),
    )
    daq = _daq()
    scan = FakeScan()

    daq._launch_live_parse_cache(scan, "p12345", 42)
    daq._launch_live_parse_cache(scan, "p12345", 42)

    assert len(calls) == 2


def test_a_popen_failure_is_swallowed(fake_escape_supported, monkeypatch):
    def _boom(cmd, **kw):
        raise OSError("no such interpreter")

    monkeypatch.setattr("eco.acquisition.daq_client.subprocess.Popen", _boom)
    daq = _daq()
    scan = FakeScan()
    assert daq._launch_live_parse_cache(scan, "p12345", 42) is None
    assert "live_parse_cache_proc" not in scan.counter_scratch("daq")


def test_log_dir_redirects_output_and_is_closed_in_the_parent(
    fake_escape_supported, monkeypatch, tmp_path
):
    captured = {}

    def fake_popen(cmd, stdout=None, stderr=None, **kw):
        # the parent's file object must still be open right here (before
        # Popen "returns"), i.e. usable to dup into a child - closed only
        # afterwards, in the caller's `finally`.
        assert stdout is not None and not stdout.closed
        captured["stdout_name"] = stdout.name
        captured["stderr"] = stderr
        return FakeProc(cmd)

    monkeypatch.setattr("eco.acquisition.daq_client.subprocess.Popen", fake_popen)
    daq = _daq(live_parse_cache_log_dir=str(tmp_path))
    daq._launch_live_parse_cache(FakeScan(runno=7), "p12345", 7)

    assert captured["stdout_name"] == str(tmp_path / "run0007_cache_writer.log")
