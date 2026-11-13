"""A scan that ends before its last step says so, with the command that
resumes it (StepScan.resume_command, checked against the session's
namespace); scan_all() then continues it, and the counters' resume
callbacks restart what the end of the scan stopped (monitors, live plot)."""

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pytest

from eco.acquisition import scan as scan_module
from eco.acquisition.scan import Scans
from eco.devices_general.simulated_signals import SimulatedSignals


@pytest.fixture
def sim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the monitor counter stores its data here
    return SimulatedSignals(axis="ideal")


def failing_once(at_step):
    state = {"failed": False}

    def callback(scan, **kwargs):
        if scan.next_step == at_step and not state["failed"]:
            state["failed"] = True
            raise RuntimeError("lost the beam")

    return callback


def aborted_scan(sim, monkeypatch, namespace=None):
    scans = Scans(callbacks_end_step=[failing_once(2)])
    monkeypatch.setattr(
        scan_module, "get_ipython",
        lambda: None if namespace is None else SimpleNamespace(user_ns=namespace(scans)),
    )
    s = scans.ascan(sim.peak.x, 0, 1, 4, 0.05, counters=[sim.peak.signal],
                    return_at_end=False)
    return scans, s


def test_an_aborted_scan_says_how_to_resume(sim, monkeypatch, capsys):
    scans, s = aborted_scan(sim, monkeypatch, namespace=lambda scans: {"scans": scans})
    out = capsys.readouterr().out
    assert "lost the beam" in out
    assert "Scan aborted or crashed after 2 of 5 steps" in out
    assert "> scans.acquiring_scan.scan_all()" in out


def test_the_command_is_only_given_when_it_reaches_the_scan(sim, monkeypatch, capsys):
    # the session knows a different "scans": no command, a general hint
    aborted_scan(sim, monkeypatch, namespace=lambda scans: {"scans": Scans()})
    out = capsys.readouterr().out
    assert "Scan aborted or crashed" in out and "> " not in out
    assert ".scan_all() on the scan object" in out


def test_a_finished_scan_says_nothing(sim, monkeypatch, capsys):
    monkeypatch.setattr(scan_module, "get_ipython", lambda: None)
    Scans().ascan(sim.peak.x, 0, 1, 2, 0.05, counters=[sim.peak.signal], return_at_end=False)
    assert "Scan aborted" not in capsys.readouterr().out


def test_resuming_continues_monitors_and_plot(sim, monkeypatch, capsys):
    scans, s = aborted_scan(sim, monkeypatch, namespace=lambda scans: {"scans": scans})
    (ctr,) = s.counters
    animation = ctr._state(s).animation
    restarted = []
    original_start = animation.event_source.start
    animation.event_source.start = lambda *a: restarted.append(1) or original_start(*a)
    capsys.readouterr()

    s.scan_all()
    assert restarted == [1]
    assert "Scan aborted" not in capsys.readouterr().out
    assert len(s._values_done) == 5
    (array,) = s.monitor_scan_arrays.values()
    timestamps = np.asarray(array.timestamps)
    # every step, also those after the resume, has monitor values
    for start, stop in s.timestamp_intervals:
        assert ((timestamps >= start) & (timestamps <= stop)).any()
    for tm in s.monitors.values():
        tm.stop()


def test_a_detectors_own_scan_is_resumed_through_the_detector(sim, monkeypatch, capsys):
    monkeypatch.setattr(
        scan_module, "get_ipython",
        lambda: SimpleNamespace(user_ns={"sim": sim, "scans": Scans()}),
    )
    detector = sim.peak.signal
    detector.scans.callbacks_end_step.append(failing_once(2))
    try:
        detector.scans.ascan(sim.peak.x, 0, 1, 3, 0.05, return_at_end=False)
    finally:
        detector.scans.callbacks_end_step.pop()
    assert "> sim.peak.signal.scans.acquiring_scan.scan_all()" in capsys.readouterr().out
