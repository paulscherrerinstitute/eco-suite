"""eco.acquisition.counters.as_counter: what a scan makes of the objects in
`counters=` (and `feedback=`) -- counters as they are, a bs detector its kept
live bs counter, any other detector a CounterValue."""

import time

import matplotlib

matplotlib.use("Agg")

import pytest

from eco.acquisition.counters import CounterValue, as_counter, as_counters
from eco.acquisition.scan import Scans
from eco.devices_general.simulated_signals import SimulatedSignals


@pytest.fixture
def sim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the monitor counter stores its data here
    return SimulatedSignals(axis="ideal")


class OwnAcquire:
    """A detector with an acquire() of its own that is not a scan counter."""

    name = "own"

    def get_current_value(self):
        return 1.0

    def acquire(self, hold=False, seconds=None, samples=None, **kwargs):
        raise AssertionError("not a counter: must not be called by a scan")


def test_counter_passes_through(sim):
    ctr = CounterValue(sim.peak.signal)
    assert as_counter(ctr) is ctr


def test_detector_becomes_a_fresh_counter_value(sim):
    a = as_counter(sim.peak.signal)
    assert isinstance(a, CounterValue)
    assert a.name == sim.peak.signal.alias.get_full_name()
    assert a.monitorables + a.detectors == [sim.peak.signal]
    assert as_counter(sim.peak.signal) is not a  # nothing of a scan kept


def test_own_acquire_is_not_a_counter():
    assert isinstance(as_counter(OwnAcquire()), CounterValue)


def test_hook_decides(sim):
    ctr = CounterValue(sim.peak.signal)

    class WithHook:
        def _scan_counter(self):
            return ctr

    assert as_counter(WithHook()) is ctr


def test_neither_counter_nor_detector():
    with pytest.raises(TypeError, match="neither a counter nor a detector"):
        as_counter(object())


def test_epics_fallback_when_dispatcher_lacks_the_channel(capsys):
    class Missing(OwnAcquire):
        name = "missing"
        stream = object()
        _pv = object()

        def bs_avail(self):
            return False

    assert isinstance(as_counter(Missing()), CounterValue)
    assert "not on the dispatcher" in capsys.readouterr().out


def test_scan_takes_a_detector(sim):
    s = Scans().ascan(
        sim.peak.x, 0, 1, 2, 0.1, counters=[sim.peak.signal, sim.peak.signal],
        return_at_end=False,
    )
    (ctr,) = s.counters
    assert isinstance(ctr, CounterValue)
    assert len(s._values_done) == 3


# -- bs detectors, against escape's synthetic test stream ----------------------
PORT = 9895


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


def test_bs_detector_keeps_one_bs_counter(bs_worker):
    from escape.stream.escape_stream import Stream

    from eco.detector.bs_counter import BsStreamCounter
    from eco.detector.detectors_psi import DetectorBsStream

    det = DetectorBsStream("i0", cachannel="none", name="i0_det")
    det.stream = Stream("i0", bs_worker, unit="a.u.")
    ctr = as_counter(det)
    assert isinstance(ctr, BsStreamCounter)
    assert det._bs_counter is ctr
    assert as_counters([det, det, ctr]) == [ctr]

    stream = Stream("i", bs_worker, unit="a.u.")
    assert as_counter(stream) is as_counter(stream)
    as_counter(stream).close()
    ctr.close()


def test_plain_detectors_share_one_counter_value(sim):
    a, b = sim.peak.signal, sim.step.signal
    ctr = CounterValue(sim.gradient.signal)
    counters = as_counters([a, ctr, b, a])
    assert counters[1] is ctr and len(counters) == 2
    assert counters[0].monitorables == [a, b]
    from eco.acquisition.counters import find_counter

    assert find_counter(counters, b) == (counters[0], b.alias.get_full_name())
    assert find_counter(counters, ctr) == (ctr, None)


def test_scan_with_two_detectors(sim):
    s = Scans().ascan(
        sim.peak.x, 0, 1, 2, 0.1, counters=[sim.peak.signal, sim.step.signal],
        return_at_end=False,
    )
    (ctr,) = s.counters
    assert set(s.monitors) == {
        sim.peak.signal.alias.get_full_name(), sim.step.signal.alias.get_full_name()
    }
