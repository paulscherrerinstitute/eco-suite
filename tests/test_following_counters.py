"""The first counter of a scan sets each step's length and records its window
(StepScan.set_step_window); counters that can follow (CounterValue,
BsStreamCounter, the adaptive driver) take no exposure of their own and
collect exactly that window -- a bs counter by the leader's pulse ids."""

import time

import matplotlib

matplotlib.use("Agg")

import pytest

from eco.acquisition.daq_client import Daq
from eco.acquisition.scan import Scans
from eco.acquisition.utilities import Acquisition
from eco.devices_general.simulated_signals import SimulatedSignals


@pytest.fixture
def sim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the monitor counter stores its data here
    return SimulatedSignals(axis="ideal")


class TimedLead:
    """A leading counter taking N_pulses * 10 ms and recording its times."""

    name = "lead"

    def __init__(self):
        self.calls = []

    def acquire(self, scan=None, Npulses=None, **kwargs):
        def run():
            t0 = time.time()
            time.sleep(Npulses * 0.01)
            scan.set_step_window(self, time_start=t0, time_stop=time.time())
            self.calls.append(Npulses)

        return Acquisition(acquire=run, hold=False)


def test_counter_value_follows_the_leader(sim):
    lead = TimedLead()
    t0 = time.time()
    # N_pulses=10: 0.1 s per step for the leader; a CounterValue reading it
    # as seconds would take 10 s per step
    s = Scans().ascan(
        sim.peak.x, 0, 1, 2, 10, counters=[lead, sim.peak.signal], return_at_end=False
    )
    assert time.time() - t0 < 5
    assert lead.calls == [10, 10, 10]
    windows = [step["window"] for step in s.scan_info["scan_step_info"]]
    assert all(w["by"] == "lead" for w in windows)
    assert [tuple(t) for t in s.timestamp_intervals] == [
        (w["time_start"], w["time_stop"]) for w in windows
    ]
    assert len(s.monitor_scan_arrays) == 1


def test_a_counter_value_leading_records_its_window(sim):
    s = Scans().ascan(sim.peak.x, 0, 1, 1, 0.05, counters=[sim.peak.signal], return_at_end=False)
    w = s.scan_info["scan_step_info"][-1]["window"]
    assert w["by"] == sim.peak.signal.alias.get_full_name()
    assert 0.04 < w["time_stop"] - w["time_start"] < 0.5


def test_a_non_following_counter_still_acquires_in_parallel(sim):
    first, second = TimedLead(), TimedLead()
    s = Scans().ascan(sim.peak.x, 0, 1, 1, 5, counters=[first, second], return_at_end=False)
    assert first.calls == second.calls == [5, 5]
    # only the first one's window is kept
    assert len(s.scan_info["scan_step_info"]) == 2


def test_followers_are_stopped_when_the_leader_fails(sim):
    class Failing:
        name = "failing"

        def acquire(self, scan=None, Npulses=None, **kwargs):
            def run():
                raise RuntimeError("daq down")

            return Acquisition(acquire=run, hold=False)

    from eco.acquisition.scan import StepScan

    s = StepScan([sim.peak.x], [[0.0]], counters=[Failing(), sim.peak.signal], Npulses=1)
    stopped = []
    follower = s.counters[1]
    orig_stop = follower.stop
    follower.stop = lambda scan=None, **kw: stopped.append(1) or orig_stop(scan=scan, **kw)
    for cb in follower.callbacks_start_scan:
        cb(scan=s)
    with pytest.raises(RuntimeError, match="daq down"):
        s.do_next_step()
    assert stopped == [1]
    for tm in s.monitors.values():
        tm.stop()


# -- the DAQ's side, without a DAQ --------------------------------------------
class FakeDaqSelf:
    """What Daq.acquire_pulses uses of the Daq instance."""

    pgroup = "p00000"

    def __init__(self):
        self.running = {}
        self.waited = []

    def start(self, label=None, **kwargs):
        self.running[0] = {"start_id": 1000}
        return 0

    def wait_for_pulse_id(self, pulse_id, **kwargs):
        self.waited.append(pulse_id)

    def stop(self, stop_id=None, acq_ix=None, wait=True, pgroup=None):
        return {"stop_id": stop_id}


def test_daq_reports_its_pulses():
    daq, windows = FakeDaqSelf(), []
    response = Daq.acquire_pulses(daq, 100, on_window=lambda **w: windows.append(w))
    assert response == {"stop_id": 1099}
    assert windows[0]["pulse_id_start"] == 1000 and windows[0]["pulse_id_stop"] == 1099
    assert "time_start" in windows[0] and "time_stop" in windows[1]
    assert daq.waited == [1099]


def test_daq_without_a_scan_is_unchanged():
    daq = FakeDaqSelf()
    assert Daq.acquire_pulses(daq, 10) == {"stop_id": 1009}
    assert daq.waited == []  # stop() waits, as before


def test_a_failing_window_callback_does_not_fail_the_acquisition(capsys):
    def broken(**window):
        raise ValueError("nope")

    assert Daq.acquire_pulses(FakeDaqSelf(), 10, on_window=broken) == {"stop_id": 1009}
    assert "could not record the step's pulses" in capsys.readouterr().out


# -- a bs counter following a DAQ-like leader, on escape's test stream ------------
PORT = 9896


@pytest.fixture()
def bs_worker():
    pytest.importorskip("escape.stream")
    from escape.stream.escape_stream import EventWorker, TestStream
    from escape.stream.es_wrappers import LocalEventHandler

    ts = TestStream(port=PORT, interval=0.005)
    ts.start()
    time.sleep(0.5)
    worker = EventWorker(LocalEventHandler(host="localhost", port=PORT), make_default=False)
    yield worker
    ts.stop()


class PulseLead:
    """Like the DAQ: the N pulses after its start, by pulse id."""

    name = "pulse_lead"

    def __init__(self, worker):
        self.worker = worker

    def current(self):
        event = self.worker.event
        return event.getEventId() if event is not None else None

    def acquire(self, scan=None, Npulses=None, **kwargs):
        def run():
            while self.current() is None:
                time.sleep(0.001)
            first = self.current() + 1
            last = first + Npulses - 1
            scan.set_step_window(self, pulse_id_start=first, pulse_id_stop=last)
            while self.current() < last:
                time.sleep(0.001)

        return Acquisition(acquire=run, hold=False)


def test_bs_counter_takes_exactly_the_leaders_pulses(sim, bs_worker):
    from eco.detector.bs_counter import BsStreamCounter

    ctr = BsStreamCounter("i0", eventworker=bs_worker, timeout=5, live_plot=False, store=False)
    seen = []

    def record(scan, **kwargs):
        seen.append((dict(scan.step_window), list(ctr.last_pulse_ids["i0"])))

    Scans(callbacks_end_step=[record]).ascan(
        sim.peak.x, 0, 1, 3, 20, counters=[PulseLead(bs_worker), ctr], return_at_end=False
    )
    assert len(seen) == 4
    for window, ids in seen:
        first, last = window["pulse_id_start"], window["pulse_id_stop"]
        assert ids and min(ids) >= first and max(ids) <= last
        assert sorted(ids) == list(range(first, last + 1))  # all 20, exactly
    ctr.close()
