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


# -- failed steps: taken back out, recorded, resumed automatically ---------------


def test_a_resumed_scan_has_each_step_once(sim, monkeypatch):
    scans, s = aborted_scan(sim, monkeypatch, namespace=lambda scans: {"scans": scans})
    assert len(s.scan_info["scan_step_info"]) == len(s.timestamp_intervals) == 2
    s.scan_all()
    assert s.next_step == len(s.scan_info["scan_step_info"]) == len(s.timestamp_intervals) == 5


def test_an_aborted_scan_records_its_error_and_outcome(sim, monkeypatch):
    logged = []
    monkeypatch.setattr(
        "eco.widgets.kernel_registry.log_caught_error",
        lambda ename, evalue, **fields: logged.append((ename, evalue, fields)),
    )
    scans, s = aborted_scan(sim, monkeypatch, namespace=lambda scans: {"scans": scans})
    (error,) = s.scan_info["errors"]
    assert error["type"] == "RuntimeError" and error["step"] == 2 and not error["resumed"]
    assert "lost the beam" in error["traceback"]
    assert s.scan_info["outcome"]["state"] == "aborted"
    assert s.scan_info["outcome"]["steps_done"] == 2
    ((ename, evalue, fields),) = logged
    assert ename == "RuntimeError"
    assert "lost the beam [scan scans.acquiring_scan, step 3 of 5, scan aborted]" == evalue
    assert fields["resume_command"] == "scans.acquiring_scan.scan_all()"
    s.scan_all()
    assert s.scan_info["outcome"]["state"] == "complete"


def test_resume_tries_gets_over_a_failed_step(sim, monkeypatch, capsys):
    monkeypatch.setattr(scan_module, "get_ipython", lambda: None)
    scans = Scans(callbacks_end_step=[failing_once(2)])
    s = scans.ascan(sim.peak.x, 0, 1, 4, 0.05, counters=[sim.peak.signal],
                    return_at_end=False, resume_tries=1)
    out = capsys.readouterr().out
    assert "resuming automatically (1 of 1)" in out and "Scan aborted" not in out
    assert s.next_step == len(s.scan_info["scan_step_info"]) == len(s.timestamp_intervals) == 5
    assert s.scan_info["outcome"] == dict(s.scan_info["outcome"], state="complete", automatic_resumes=1)
    assert [e["resumed"] for e in s.scan_info["errors"]] == [True]


def test_in_a_queue_a_scan_resumes_once_by_default(sim, monkeypatch):
    s = scan_module.StepScan([sim.peak.x], [[0.0]], counters=[sim.peak.signal], Npulses=0.05)
    assert s.effective_resume_tries() == 0
    monkeypatch.setattr(scan_module.task_queue, "in_queue", lambda: True)
    assert s.effective_resume_tries() == 1
    s.resume_tries = 3
    assert s.effective_resume_tries() == 3


def test_a_caught_error_goes_to_the_session_log_with_its_traceback(monkeypatch):
    from eco import logs
    from eco.widgets import kernel_registry

    records = []

    class Session:
        def log_output(self, event, **fields):
            records.append(dict(fields, event=event, t=1.0))

    monkeypatch.setattr("IPython.get_ipython", lambda: SimpleNamespace(_eco_shell_logger_session=Session()))
    tb = "Traceback (most recent call last):\n  File \"x.py\", line 3, in step\nRuntimeError: lost the beam\n"
    assert kernel_registry.log_caught_error("RuntimeError", "lost the beam [scan s]", traceback=tb, step=2)
    entry = logs._kernel_record_to_entry(records[0], "console")
    assert entry.is_error and entry.text.startswith("RuntimeError: lost the beam [scan s]")
    assert 'File "x.py", line 3, in step' in entry.text
