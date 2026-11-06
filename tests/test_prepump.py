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
