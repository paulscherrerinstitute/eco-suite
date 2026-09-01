"""Tests for Daq.get_pulse_id / wait_for_pulse_id.

The bug these pin down: `PV.get()` on an `auto_monitor=False` PV returns
**None** when the get times out (pyepics does not raise), so the old
`int(self.pulse_id.get_current_value())` in `Daq.stop()` died mid-scan with
`TypeError: int() argument must be ... not 'NoneType'` whenever channel access
was congested -- which is precisely when a scan step is running.

No EPICS here: `Daq.get_pulse_id` is exercised against a stub carrying only the
monitor-cache attributes `Daq.__init__` sets up, so the tests describe the
contract (never None, times out loudly, no CA traffic) rather than the IOC.
"""

import threading
import time

import pytest

from eco.acquisition.daq_client import Daq


class _FakePv:
    """Stands in for `self.pulse_id._pv` on the no-monitor fallback path."""

    def __init__(self, values=None, timevars=None, pvname="FAKE:PULSE-ID"):
        self.pvname = pvname
        self.values = list(values or [])
        self.timevars = timevars
        self.get_calls = 0

    def get(self, **kwargs):
        self.get_calls += 1
        return self.values.pop(0) if self.values else None

    def get_timevars(self, **kwargs):
        return self.timevars


class _FakePulseId:
    def __init__(self, pv=None, pvname="FAKE:PULSE-ID"):
        self._pv = pv
        self.pvname = pvname


def make_daq(monitored=True, value=None, timestamp=None, pv=None, timeout=0.5):
    """A Daq carrying only what the pulse_id helpers touch."""
    daq = Daq.__new__(Daq)
    daq.timeout = timeout
    daq.pulse_id = _FakePulseId(pv=pv)
    if monitored:
        daq._pulse_id_latest = {"value": value, "timestamp": timestamp}
        daq._pulse_id_latest_lock = threading.Lock()
        daq._pulse_id_updated = threading.Event()
    return daq


# --------------------------------------------------------------------------
# monitor-cache path
# --------------------------------------------------------------------------


def test_returns_the_monitored_value_as_an_int():
    daq = make_daq(value=12345.0, timestamp=time.time())
    assert daq.get_pulse_id() == 12345
    assert isinstance(daq.get_pulse_id(), int)


def test_does_not_issue_any_channel_access_gets():
    """The whole point: the call that used to fail under CA load must not add
    to that load."""
    pv = _FakePv(values=[999])
    daq = make_daq(value=12345.0, timestamp=time.time(), pv=pv)

    daq.get_pulse_id()

    assert pv.get_calls == 0


def test_times_out_loudly_instead_of_returning_none():
    """The reported crash was `int(None)`. Anything is better than that, but a
    TimeoutError naming the PV is what a user can act on."""
    daq = make_daq(value=None, timestamp=None, timeout=0.15)

    with pytest.raises(TimeoutError) as excinfo:
        daq.get_pulse_id()

    assert "pulse_id" in str(excinfo.value)
    assert "FAKE:PULSE-ID" in str(excinfo.value)


def test_newer_than_rejects_a_stale_cached_value():
    """`start()` needs the pulse *now*: a stale start_id would silently widen
    the acquisition window."""
    daq = make_daq(value=100, timestamp=time.time() - 10, timeout=0.15)

    with pytest.raises(TimeoutError):
        daq.get_pulse_id(newer_than=time.time())


def test_newer_than_accepts_a_fresh_value():
    now = time.time()
    daq = make_daq(value=100, timestamp=now)
    assert daq.get_pulse_id(newer_than=now - 0.001) == 100


def test_without_newer_than_a_stale_value_is_fine():
    """`stop()` deliberately accepts the last monitored pulse -- requiring a
    fresh one would raise whenever the beam has stopped advancing."""
    daq = make_daq(value=100, timestamp=time.time() - 10)
    assert daq.get_pulse_id() == 100


def test_waits_for_an_update_that_arrives_late():
    daq = make_daq(value=None, timestamp=None, timeout=5)

    def publish():
        time.sleep(0.1)
        with daq._pulse_id_latest_lock:
            daq._pulse_id_latest.update(value=777, timestamp=time.time())
        daq._pulse_id_updated.set()

    t = threading.Thread(target=publish)
    t.start()
    try:
        assert daq.get_pulse_id() == 777
    finally:
        t.join()


# --------------------------------------------------------------------------
# fallback path (pulse_id_adj given as a pre-built object, no monitor)
# --------------------------------------------------------------------------


def test_fallback_retries_past_a_none_from_a_timed_out_get():
    """A single failed CA get must not end the scan -- this is the exact
    condition that produced the reported traceback."""
    pv = _FakePv(values=[None, None, 4242], timevars={"timestamp": time.time()})
    daq = make_daq(monitored=False, pv=pv, timeout=5)

    assert daq.get_pulse_id() == 4242
    assert pv.get_calls == 3


def test_fallback_times_out_instead_of_returning_none():
    pv = _FakePv(values=[], timevars={"timestamp": time.time()})
    daq = make_daq(monitored=False, pv=pv, timeout=0.2)

    with pytest.raises(TimeoutError):
        daq.get_pulse_id()


def test_fallback_only_checks_timevars_when_freshness_is_required():
    pv = _FakePv(values=[7], timevars=None)
    daq = make_daq(monitored=False, pv=pv, timeout=0.2)

    assert daq.get_pulse_id() == 7  # timevars None would have blocked freshness

    pv2 = _FakePv(values=[7], timevars=None)
    daq2 = make_daq(monitored=False, pv=pv2, timeout=0.2)
    with pytest.raises(TimeoutError):
        daq2.get_pulse_id(newer_than=time.time())


# --------------------------------------------------------------------------
# wait_for_pulse_id
# --------------------------------------------------------------------------


def test_wait_returns_immediately_when_already_past():
    daq = make_daq(value=500, timestamp=time.time())
    assert daq.wait_for_pulse_id(400) == 500


def test_wait_blocks_until_the_counter_reaches_the_target():
    daq = make_daq(value=100, timestamp=time.time(), timeout=5)

    def advance():
        for v in (150, 200, 260):
            time.sleep(0.05)
            with daq._pulse_id_latest_lock:
                daq._pulse_id_latest.update(value=v, timestamp=time.time())
            daq._pulse_id_updated.set()

    t = threading.Thread(target=advance)
    t.start()
    try:
        assert daq.wait_for_pulse_id(250) >= 250
    finally:
        t.join()


def test_wait_survives_a_stretch_with_no_readable_value():
    """A gap in the monitor must not kill the wait: `get_pulse_id` timing out
    inside the loop is a "not yet", not a failure."""
    daq = make_daq(value=None, timestamp=None, timeout=0.05)

    def publish():
        time.sleep(0.15)
        with daq._pulse_id_latest_lock:
            daq._pulse_id_latest.update(value=900, timestamp=time.time())
        daq._pulse_id_updated.set()

    t = threading.Thread(target=publish)
    t.start()
    try:
        assert daq.wait_for_pulse_id(800, poll_interval=0.01, timeout=5) == 900
    finally:
        t.join()


def test_wait_without_timeout_is_the_default():
    """The old loop waited forever on purpose (beam can come back); that must
    not have silently become an exception."""
    import inspect

    assert inspect.signature(Daq.wait_for_pulse_id).parameters["timeout"].default is None


def test_wait_honours_an_explicit_timeout():
    daq = make_daq(value=10, timestamp=time.time(), timeout=0.05)

    with pytest.raises(TimeoutError):
        daq.wait_for_pulse_id(99999, poll_interval=0.01, timeout=0.2)
