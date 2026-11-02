"""Step-scan timing statistics (eco.acquisition.scan_timing) and their
effect on ordinary scans: duty cycle in the step line, a one-time warning,
the step count unchanged for fixed scans."""

import matplotlib

matplotlib.use("Agg")

import pytest

from eco.acquisition import scan_timing
from eco.acquisition.scan_timing import ScanTiming


def test_fractions_and_short_line():
    t = ScanTiming()
    t.add_step(acquire=3, move=1)
    t.book("planning", 0.5)
    assert t.fractions()["acquire"] == pytest.approx(0.75)
    assert t.short() == "acq 75% move 25% settle 0% cb 0% (planning 12%)"
    assert t.mean_step_time() == pytest.approx(4)


def test_duty_warning_once_after_enough_steps():
    t = ScanTiming()
    for _ in range(scan_timing.WARN_AFTER_STEPS - 1):
        t.add_step(acquire=1, move=9)
    assert t.warnings() == []
    t.add_step(acquire=1, move=9)
    (message,) = t.warnings()
    assert "10%" in message and "move" in message
    t.add_step(acquire=1, move=9)
    assert t.warnings() == []


def test_booked_category_warning():
    t = ScanTiming()
    for _ in range(scan_timing.WARN_AFTER_STEPS):
        t.add_step(acquire=1, callbacks=1)
    t.book("planning", 3)
    assert any("planning" in m for m in t.warnings())


def test_ordinary_scan_reports_timing_and_keeps_step_count(tmp_path, monkeypatch, capsys):
    from eco.acquisition.scan import Scans
    from eco.devices_general.simulated_signals import SimulatedSignals

    monkeypatch.chdir(tmp_path)  # the monitor counter stores its data here
    sim = SimulatedSignals(detector="ideal")
    s = sim.peak.signal.scans.ascan(sim.peak.x, 0, 1, 4, 0.05, return_at_end=False)
    out = capsys.readouterr().out
    assert s.number_of_steps() == 5 and s.next_step == 5
    assert "Step 5 of 5" in out
    assert "| acq " in out
    assert "Scan timing: 5 steps" in out
