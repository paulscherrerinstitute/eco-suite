"""Validates the bs-stream-backed StepScan counter mockup
(eco/detector/bs_counter.py) against escape.stream's synthetic local test
stream -- no real beamline/dispatcher needed. See bs_counter.py's module
docstring for the design questions this answers.
"""

import time

import pytest

pytest.importorskip("escape.stream")

from escape.stream.escape_stream import EventWorker, Stream, TestStream
from escape.stream.es_wrappers import LocalEventHandler

from eco.detector.bs_counter import BsStreamCounter, DetectorBsTest
from eco.detector.detectors_psi import DetectorBsStream

PORT = 9894


@pytest.fixture()
def bs_worker():
    ts = TestStream(port=PORT, interval=0.005)
    ts.start()
    time.sleep(0.5)
    worker = EventWorker(LocalEventHandler(host="localhost", port=PORT), make_default=False)
    yield worker
    ts.stop()


class FakeScan:
    """Minimal stand-in for the attributes BsStreamCounter reads off a real
    eco.acquisition.scan.StepScan (see scan.py:312-437)."""

    def __init__(self, n_steps):
        self.next_step = 0
        self._values_done = []
        self._values_todo = list(range(n_steps))


def test_single_channel_scalar(bs_worker):
    ctr = BsStreamCounter("i0", eventworker=bs_worker, timeout=5)
    time.sleep(0.3)
    assert isinstance(ctr.get_current_value(), float)

    val = ctr.acquire(Npulses=20).wait()
    assert isinstance(val, float)
    assert len(ctr.last_pulse_ids["i0"]) == 20
    ctr.close()


def test_timeout_on_dead_channel(bs_worker):
    ctr = BsStreamCounter("does_not_exist", eventworker=bs_worker, timeout=0.5)
    with pytest.raises(TimeoutError):
        ctr.acquire(Npulses=5).wait()
    ctr.close()


def test_multi_source_and_stepscan_binning(bs_worker):
    # "the counter should take either an escape Stream instance or the
    # parent DetectorBsStream, and also multiples of that"
    det = DetectorBsStream("pump_on", cachannel="none", name="pump_on_det")
    det.stream = Stream("pump_on", bs_worker, unit="a.u.")

    ctr = BsStreamCounter([det, "i0", "i"], eventworker=bs_worker, timeout=5)
    assert set(ctr._channels) == {"pump_on", "i0", "i"}

    n_steps = 4
    fake_scan = FakeScan(n_steps)
    for cb in ctr.callbacks_start_scan:
        cb(scan=fake_scan)
    # Bins are open-ended (escape-fel 0.2.7 -- see bs_counter.py's module
    # docstring) and created on demand, so none exist yet right after
    # start; they grow to n_steps once every step has been visited below.
    assert len(ctr._scan._values) == 0

    all_pulse_ids = {name: [] for name in ctr._channels}
    for step in range(n_steps):
        fake_scan.next_step = step
        val = ctr.acquire(scan=fake_scan, Npulses=10).wait()
        assert isinstance(val, dict) and set(val) == {"pump_on", "i0", "i"}
        for name, pids in ctr.last_pulse_ids.items():
            assert len(pids) == 10
            all_pulse_ids[name].extend(pids)

    assert len(ctr._scan._values) == n_steps

    for cb in ctr.callbacks_end_scan:
        cb(scan=fake_scan)

    # Pulse ids strictly increasing and non-overlapping across steps, for
    # every channel -- confirms the per-step bins are a clean partition of
    # the live stream. This is exactly the case that used to silently lose
    # data for a late-joining channel on a shared, open-ended
    # escape.stream.Scan before escape-fel 0.2.7.
    for name, ids in all_pulse_ids.items():
        assert ids == sorted(ids)
        assert len(ids) == len(set(ids))

    ctr.close()


def test_start_stop_mode(bs_worker):
    ctr = BsStreamCounter(["i0", "i"], eventworker=bs_worker, timeout=5)
    fake_scan = FakeScan(2)
    for cb in ctr.callbacks_start_scan:
        cb(scan=fake_scan)

    fake_scan.next_step = 0
    ctr.start(scan=fake_scan)
    time.sleep(0.2)
    resp = ctr.stop(scan=fake_scan)
    assert resp == {"files": []}
    assert set(ctr.last_values) == {"i0", "i"}
    assert all(isinstance(v, float) for v in ctr.last_values.values())
    ctr.close()


def test_detector_bs_stream_force_bsstream_read(bs_worker):
    # DetectorBsStream itself deliberately stays on the plain EPICS-monitor
    # @scannable/CounterValue path (see detectors_psi.py) -- production
    # devices like mon_opt.intensity are not to be repointed at bs-stream
    # scanning here. Only the opt-in force_bsstream=True direct-read path
    # (unrelated to .scans) is this module's concern.
    det = DetectorBsStream("i0", cachannel="none", name="i0")
    det.stream = Stream("i0", bs_worker, unit="a.u.")

    # Default get_current_value() must stay None with no PV mirror -- it
    # must not silently open a live bs subscription for the many existing
    # DetectorBsStream(cachannel="none") instances read on every
    # get_status() fan-out (position/intensity monitors, timing_diag, ...).
    assert det.get_current_value() is None

    # force_bsstream=True is the explicit opt-in, and now actually works
    # (used to be a bare NotImplementedError).
    v = det.get_current_value(force_bsstream=True)
    assert isinstance(v, float)
    assert hasattr(det, "_bs_counter")

    det._bs_counter.close()


def test_detector_bs_test_scans(bs_worker):
    # DetectorBsTest is the dedicated, isolated dev class for exercising
    # bs_scannable/.scans -- not DetectorBsStream (see above).
    det = DetectorBsTest("i0", name="i0_test")
    det.stream = Stream("i0", bs_worker, unit="a.u.")

    v = det.get_current_value()
    assert isinstance(v, float)
    assert hasattr(det, "_bs_counter")

    s = det.scans
    assert s._default_counters == [det._bs_counter]
    assert det.scans is s  # cached, not rebuilt on every access

    det._bs_counter.close()
