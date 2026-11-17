"""BsStreamCounter's step waits fail only when nothing new arrives for
``timeout`` s: a late channel (a Jungfrau ROI arrives ~12 s after its pulse)
then just takes longer, and a stream that stopped still fails. A computed
stream (``a / b``) is subscribed through its real channels."""

import threading
import time
from threading import Event

import pytest

pytest.importorskip("bsread")

from eco.detector import bs_counter
from eco.detector.bs_counter import BsStreamCounter


class FakeBins:
    """One channel whose shots (pulse ids) arrive from a thread."""

    def __init__(self, pulses, first_after, every):
        self.channels = {"ch": None}
        self.new_data = Event()
        self.ids = []
        self.stop = False

        def feed():
            time.sleep(first_after)
            for pid in pulses:
                if self.stop:
                    return
                self.ids.append(pid)
                self.new_data.set()
                time.sleep(every)

        threading.Thread(target=feed, daemon=True).start()

    def eventids(self, name, step_index):
        return list(self.ids)

    def bin(self, name, step_index):
        return list(self.ids)


def counter(timeout):
    c = object.__new__(BsStreamCounter)
    c.name, c.timeout = "ratio", timeout
    return c


def test_late_but_steady_data_does_not_time_out():
    bins = FakeBins(range(100, 130), first_after=0.1, every=0.05)  # 1.5 s to pulse 129
    t0 = time.time()
    counter(timeout=0.5)._wait_for_pulse_id(bins, 0, 129)
    assert time.time() - t0 > 1.0  # longer than the timeout, and no error


def test_a_stream_that_stops_fails_after_timeout():
    bins = FakeBins(range(100, 105), first_after=0.05, every=0.05)  # stops at 104
    t0 = time.time()
    with pytest.raises(TimeoutError, match="nothing new for 0.5s"):
        counter(timeout=0.5)._wait_for_pulse_id(bins, 0, 129)
    assert time.time() - t0 < 2.0


def test_the_known_delay_extends_the_wait_for_the_first_shot(monkeypatch):
    monkeypatch.setattr(bs_counter, "_expected_delay", lambda bins: 1.0)
    bins = FakeBins([129], first_after=1.2, every=0.05)  # nothing for 1.2 s
    counter(timeout=0.5)._wait_for_pulse_id(bins, 0, 129)
    with pytest.raises(TimeoutError, match="arrives ~2 s late"):
        monkeypatch.setattr(bs_counter, "_expected_delay", lambda bins: 2.0)
        counter(timeout=0.2)._wait_for_pulse_id(FakeBins([], 0, 0.05), 0, 129)


def test_n_new_shots_wait_the_same_way():
    bins = FakeBins(range(20), first_after=0.1, every=0.05)  # 1 s for 20 shots
    counter(timeout=0.3)._wait_for_n_new(bins, 0, {"ch": 0}, 20)


def test_a_computed_stream_subscribes_its_real_channels():
    escape_stream = pytest.importorskip("escape.stream.escape_stream")

    class EventWorker:
        def __init__(self):
            self.subs = {}

        def subscribe(self, owner, *channels):
            escape_stream.check_channel_name(channels[0]) if hasattr(escape_stream, "check_channel_name") else None
            self.subs.setdefault(id(owner), set()).update(channels)

        def unsubscribe(self, owner, *channels):
            self.subs.pop(id(owner), None)

    ew = EventWorker()
    a = escape_stream.Stream("JF01T03V01:roi_intensities", ew)
    b = escape_stream.Stream("SAROP21-PBPS133:INTENSITY", ew)
    ctr = BsStreamCounter(a / b, name="ratio", live_plot=False, store=False)
    assert ctr.channels() == ["JF01T03V01:roi_intensities", "SAROP21-PBPS133:INTENSITY"]
    assert ew.subs[id(ctr)] == set(ctr.channels())
    assert ctr.alias.channel == ctr.channels()
    ctr.close()
    assert ew.subs == {}


def test_a_computed_streams_name_makes_a_valid_file_name():
    assert bs_counter._file_safe("(intensity_roi_stream / intensity_stream)") == "intensity_roi_stream_intensity_stream"
    assert bs_counter._file_safe("mon_opt.intensity_stream") == "mon_opt.intensity_stream"
    assert "/" not in bs_counter._file_safe("a/b") and bs_counter._file_safe("()") == "bs_counter"
