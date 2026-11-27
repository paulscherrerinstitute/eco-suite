"""PrepumpSystem sequences, their abort handling, the cross-process lock and
the command line used by the caQtDM panel -- all against fake valves and
gauges, no EPICS."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import eco.devices_general.vacuum.prepump as pp
from eco.devices_general.vacuum import prepump_cli as cli

REPO = Path(__file__).resolve().parents[1]


class FakeChanger:
    def wait(self, timeout=None):
        pass


class FakeValve:
    def __init__(self, name):
        self.name = name
        self.log = []
        self.state = "CLOSED"

    def open(self):
        self.log.append("open")
        self.state = "OPEN"
        return FakeChanger()

    def close(self):
        self.log.append("close")
        self.state = "CLOSED"
        return FakeChanger()

    def get_current_value(self):
        return self.state


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setattr(pp.time, "sleep", lambda s: None)
    s = pp.PrepumpSystem(name="pp", lines={"a": {}, "b": {}, "c": {}},
                         lock_file=str(tmp_path / "prepump.lock"))
    for n in ("a", "b"):
        line = getattr(s, n)
        line.__dict__["valve_prevac"] = FakeValve(f"{n}_P")
        line.__dict__["valve_vent"] = FakeValve(f"{n}_V")
    # "c" is gauge-only (valves not connected), like Bernina's access 5
    # no real waiting in tests, but still the abort check every _sleep does
    s.slept = []
    s._sleep = lambda t: (s._check_abort(), s.slept.append(t))
    s.pressures = {"gp": 0.01, "a": 0.01, "b": 0.01, "c": 0.01}
    s.gp_pressure = lambda: s.pressures["gp"]
    for n in ("a", "b", "c"):
        getattr(s, n).pressure = (lambda n=n: s.pressures[n])
    return s


def test_pump_down_skips_gauge_only_line_and_restores_pumped_ones(system):
    system.b.valve_prevac.state = "OPEN"
    system.line_state = lambda l: "pumped" if l is system.b else "vented"
    assert system.pump_down("a", timeout=5, poll=0) is True
    assert system.a.valve_vent.log == ["close"]
    assert system.a.valve_prevac.log == ["open"]
    assert system.b.valve_prevac.log == ["close", "open"]  # isolated, then restored


def test_gauge_only_line_refuses_sequences(system):
    with pytest.raises(RuntimeError, match="no valve_prevac / valve_vent"):
        system.pump_down("c")
    with pytest.raises(RuntimeError, match="no valve_prevac / valve_vent"):
        system.vent("c")


def test_interrupted_pump_down_closes_pump_valve_and_releases_lock(system):
    calls = []

    def gp():
        calls.append(1)
        if len(calls) > 2:
            raise KeyboardInterrupt
        return 10.0  # not pumped yet

    system.gp_pressure = gp
    with pytest.raises(KeyboardInterrupt):
        system.pump_down("a", timeout=100, poll=0)
    assert system.a.valve_prevac.log == ["open", "close"]
    assert system.running() is None


def test_interrupted_vent_closes_vent_valve(system):
    system.pressures["a"] = 1.0  # not vented

    def stop(*a):
        raise KeyboardInterrupt

    system.a.is_vented = stop
    with pytest.raises(KeyboardInterrupt):
        system.vent("a", timeout=100, poll=0)
    assert system.a.valve_vent.log == ["open", "close"]


def test_second_sequence_is_refused_while_one_runs(system):
    started, release = threading.Event(), threading.Event()

    def gp():
        started.set()
        release.wait(5)
        return 0.01

    system.gp_pressure = gp
    t = threading.Thread(target=system.pump_down, args=("a",), kwargs={"timeout": 10, "poll": 0})
    t.start()
    try:
        assert started.wait(5)
        assert "pump_down('a')" in system.running()
        with pytest.raises(RuntimeError, match="prepump busy"):
            system.vent("b")
    finally:
        release.set()
        t.join(5)
    assert system.running() is None


def test_cli_abort_file_stops_a_running_sequence(system):
    system.gp_pressure = lambda: 10.0  # never pumped
    threading.Timer(0.5, lambda: Path(system.lock_file + ".abort").write_text("x")).start()
    rc = cli.run_sequence(system, "pump_down", "a", delay=0, timeout=30)
    assert rc == 130
    assert system.a.valve_prevac.log[-1] == "close"
    assert system.running() is None
    assert not os.path.exists(system.lock_file + ".abort")


def test_cli_resolves_lines_by_number_name_and_part(system):
    assert cli._resolve_line(system, "2") == "b"
    assert cli._resolve_line(system, "c") == "c"
    with pytest.raises(SystemExit):
        cli._resolve_line(system, "9")


def test_cli_accepts_caqtdm_single_argument(monkeypatch):
    """caQtDM passes "status " (whole parameter, trailing space) as one arg."""
    seen = []
    monkeypatch.setattr(pp, "make_bernina_prepump_system", lambda: "sys")
    monkeypatch.setattr(cli, "print_status", seen.append)
    assert cli.main(["status "]) == 0
    assert seen == ["sys"]
    monkeypatch.setattr(cli, "_resolve_line", lambda s, k: seen.append(k) or "x")
    monkeypatch.setattr(cli, "run_sequence", lambda *a: seen.append(a[1:3]) or 0)
    assert cli.main(["pump_down 1 "]) == 0
    assert seen[1:] == ["1", "sys", ("pump_down", "x")]  # resolve, status, run


def test_bernina_config_is_complete():
    cfg = pp.BERNINA_PREPUMP_CONFIG
    assert cfg["gp"] and cfg["roots_pump"] and cfg["lock_file"]
    for name, line in cfg["lines"].items():
        assert line["gauge"].startswith("SARES21-"), name


def test_import_stays_light():
    """The panel's buttons import only the prepump module: it must not pull
    in escape/pandas/matplotlib (seconds each on the consoles' NFS)."""
    code = ("import sys, eco.devices_general.vacuum.prepump_cli, eco.devices_general.vacuum.prepump;"
            "print(sorted(m for m in ('escape','pandas','matplotlib','dask','IPython') if m in sys.modules))")
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "[]", out.stderr


def test_thresholds_and_timings_are_settings_children(system):
    assert system.effective_p_target() == pp.P_TARGET
    assert system.a.effective_p_target() == pp.P_TARGET  # inherits
    system.p_target.set_target_value(0.2).wait()
    assert system.a.effective_p_target() == 0.2
    system.a.p_target.set_target_value(0.05).wait()  # per-line override
    assert system.a.effective_p_target() == 0.05
    assert system.b.effective_p_target() == 0.2
    def names(asm, selection):
        return {c.alias.get_full_name(base=asm) for c in asm.status_collection.get_list(selection=selection)}

    for n in ("p_target", "p_vent_target", "pump_down_timeout", "vent_timeout", "vent_settle_time"):
        assert n in names(system, "settings"), n
    assert {"p_target", "p_vent_target"} <= names(system.a, "settings")
    # shown in the overview (lines are is_display="recursive")
    assert {"p_target", "a.p_target"} <= names(system, "display")


def test_settings_dir_makes_file_backed_settings_shared_between_instances(tmp_path):
    cfg = {"p_target": 0.4, "settings_dir": str(tmp_path / "settings"),
           "lines": {"a": {}, "b": {"p_vent_target": 900.0}}}
    s1 = pp.make_prepump_system(cfg, name="pp")
    assert (tmp_path / "settings" / "p_target.json").exists()
    assert (tmp_path / "settings" / "a_p_target.json").exists()
    assert s1.effective_p_target() == 0.4
    assert s1.b.effective_p_vent_target() == 900.0
    s1.p_target.set_target_value(0.3).wait()
    s1.vent_timeout.set_target_value(12.0).wait()
    # a second session (or the panel's buttons) sees the stored value, and the
    # config value only seeded the file
    s2 = pp.make_prepump_system(cfg, name="pp")
    assert s2.effective_p_target() == 0.3
    assert s2.vent_timeout.get_current_value() == 12.0


def test_pump_down_timeout_comes_from_setting(system):
    system.gp_pressure = lambda: 10.0  # never pumped
    system.pump_down_timeout.set_target_value(0.0).wait()
    t = [0.0]

    def fake_time():
        t[0] += 1.0
        return t[0]

    import unittest.mock as um
    with um.patch.object(pp.time, "time", fake_time):
        assert system.pump_down("a", poll=0) is False
    assert system.a.valve_prevac.log == ["open", "close"]


def test_vent_settle_time_comes_from_setting(system):
    system.vent_settle_time.set_target_value(7.0).wait()
    system.a.is_vented = lambda: True
    assert system.vent("a", poll=0) is True
    assert 7.0 in system.slept


def test_abort_stops_a_sequence_running_in_another_thread(system):
    """What the panel's ABORT button does: the pump-down runs on the panel's
    worker thread, where Ctrl-C at the prompt does not reach it."""
    system.gp_pressure = lambda: 10.0  # never pumped
    system._sleep = pp.PrepumpSystem._sleep.__get__(system)  # real, interruptible sleep
    errors = []

    def run():
        try:
            system.pump_down("a", timeout=100, poll=0.05)
        except BaseException as e:
            errors.append(e)

    t = threading.Thread(target=run)
    t.start()
    t0 = time.time()
    while "open" not in system.a.valve_prevac.log and time.time() - t0 < 5:
        time.sleep(0.01)
    assert system.abort(wait=5) is True
    t.join(5)
    assert len(errors) == 1 and isinstance(errors[0], pp.PrepumpAborted)
    assert system.a.valve_prevac.log == ["open", "close"]
    assert system.running() is None
    assert not os.path.exists(system.abort_file)
    # a stale request does not abort the next sequence
    system.gp_pressure = lambda: 0.01
    system._sleep = lambda t: None
    assert system.pump_down("a", timeout=5, poll=0) is True


def test_abort_with_nothing_running(system):
    assert system.abort() is True
    assert not os.path.exists(system.abort_file)


def test_panel_has_abort_button_and_declares_long_commands(system):
    from eco.utilities.svg_interactor import is_long_command

    assert 'onclick="// eco: abort()"' in pp.build_prepump_svg(system, live=False)
    long = system._svg_long_commands
    assert is_long_command("prepump.pump_down('line1_usd')", long)
    assert is_long_command("prepump.vent('line3')", long)
    assert not is_long_command("prepump.abort()", long)
    assert not is_long_command("prepump.line3.valve_vent.close()", long)
    assert not is_long_command("prepump.line3.gauge", long)
