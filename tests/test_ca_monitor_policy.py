"""The adaptive channel-access monitor policy in eco.epics_utils.ca_tuning.

Background, from pyepics' own `PV.get_with_metadata`::

    if not self.wait_for_connection(timeout=timeout):
        return None
    if ((not use_monitor) or (not self.auto_monitor) or ...):
        metad = ca.get_with_metadata(...)
        if metad is None:
            return

so `auto_monitor=False` - the old eco-wide default - makes every read a
network round trip with two ways to silently return `None`, while
`auto_monitor=True` with a cached value skips that block entirely. The
policy here monitors by default and demotes only channels that prove too
fast, rather than every caller hand-rolling its own monitor cache.
"""

import json
import sys
import time
import types

import pytest

from eco.epics_utils import ca_tuning


class FakePV:
    """Enough of epics.PV for the policy: callbacks, auto_monitor, pvname."""

    def __init__(self, pvname, auto_monitor=False, **kwargs):
        self.pvname = pvname
        self.auto_monitor = auto_monitor
        self.kwargs = kwargs
        self.callbacks = {}
        self._next_index = 0

    def add_callback(self, cb, with_ctrlvars=True, **kw):
        self.with_ctrlvars = with_ctrlvars
        self._next_index += 1
        self.callbacks[self._next_index] = cb
        return self._next_index

    def remove_callback(self, index):
        self.callbacks.pop(index, None)

    def fire(self, n=1):
        for _ in range(n):
            for cb in list(self.callbacks.values()):
                cb()


@pytest.fixture
def policy(tmp_path, monkeypatch):
    """ca_tuning with its module state isolated per test."""
    monkeypatch.setattr(ca_tuning, "AUTO_MONITOR_STATE_FILE",
                        tmp_path / "fast.json")
    monkeypatch.setattr(ca_tuning, "_fast_channels", set())
    monkeypatch.setattr(ca_tuning, "_update_counts", {})
    monkeypatch.setattr(ca_tuning, "_tracked", {})
    monkeypatch.setattr(ca_tuning, "_sensitive_depth", 0)
    monkeypatch.setattr(ca_tuning, "_sweeper", object())  # never start a thread
    fake_epics = types.ModuleType("epics")
    fake_epics.PV = FakePV
    monkeypatch.setitem(__import__("sys").modules, "epics", fake_epics)
    return ca_tuning


# --------------------------------------------------------------------------
# default policy


def test_pvs_are_monitored_by_default(policy):
    pv = policy.make_pv("TEST:SLOW")
    assert pv.auto_monitor is True
    assert "TEST:SLOW" in policy._tracked


def test_the_rate_counter_does_not_trigger_a_ctrlvars_storm(policy):
    """pyepics defaults add_callback(with_ctrlvars=True), which issues a
    blocking get_ctrlvars() per connected PV - across a namespace that is
    the get-storm this policy exists to avoid."""
    pv = policy.make_pv("TEST:SLOW")
    assert pv.with_ctrlvars is False


def test_an_explicit_auto_monitor_still_wins(policy):
    pv = policy.make_pv("TEST:X", auto_monitor=False)
    assert pv.auto_monitor is False
    assert policy._tracked == {}


# --------------------------------------------------------------------------
# learning which channels are too fast


def test_a_fast_channel_is_demoted_and_remembered(policy):
    pv = policy.make_pv("TEST:FAST")
    pv.fire(100)                       # 100 updates ...
    policy._sweep_once(interval=1.0)   # ... in one second = 100 Hz

    assert pv.auto_monitor is False
    assert pv.callbacks == {}, "the rate counter should be detached too"
    assert policy.is_known_fast("TEST:FAST")
    assert "TEST:FAST" not in policy._tracked


def test_a_slow_channel_keeps_its_monitor(policy):
    pv = policy.make_pv("TEST:SLOW")
    pv.fire(3)
    policy._sweep_once(interval=1.0)
    assert pv.auto_monitor is True
    assert not policy.is_known_fast("TEST:SLOW")


def test_a_channel_someone_else_monitors_is_never_demoted(policy):
    """A recording, a Monitor() or a CallbackEpics registers its own
    callback; clearing the subscription under it would silently stop its
    data."""
    pv = policy.make_pv("TEST:FAST")
    pv.add_callback(lambda **kw: None)      # somebody else's monitor
    pv.fire(100)
    policy._sweep_once(interval=1.0)

    assert pv.auto_monitor is True
    assert not policy.is_known_fast("TEST:FAST")


def test_a_known_fast_channel_is_never_subscribed_again(policy):
    pv = policy.make_pv("TEST:FAST")
    pv.fire(100)
    policy._sweep_once(interval=1.0)

    again = policy.make_pv("TEST:FAST")
    assert again.auto_monitor is False
    assert "TEST:FAST" not in policy._tracked


def test_the_learned_list_survives_a_restart(policy, tmp_path):
    pv = policy.make_pv("TEST:FAST")
    pv.fire(100)
    policy._sweep_once(interval=1.0)

    written = json.loads((tmp_path / "fast.json").read_text())
    assert written == ["TEST:FAST"]

    policy._fast_channels.clear()
    policy._load_fast_channels()
    assert policy.is_known_fast("TEST:FAST")


def test_clearing_forgets_everything(policy):
    pv = policy.make_pv("TEST:FAST")
    pv.fire(100)
    policy._sweep_once(interval=1.0)
    assert policy.is_known_fast("TEST:FAST")

    policy.clear_fast_channels()
    assert not policy.is_known_fast("TEST:FAST")


def test_counts_are_zeroed_not_lost_between_sweeps(policy):
    pv = policy.make_pv("TEST:SLOW")
    pv.fire(3)
    policy._sweep_once(interval=1.0)
    assert policy._update_counts["TEST:SLOW"] == 0
    pv.fire(2)
    assert policy._update_counts["TEST:SLOW"] == 2


# --------------------------------------------------------------------------
# sensitive periods


def test_the_sweeper_leaves_subscriptions_alone_during_an_acquisition(policy):
    """Reconfiguring a monitor mid-acquisition is exactly the wrong moment,
    even for a channel that deserves demotion."""
    pv = policy.make_pv("TEST:FAST")
    pv.fire(100)
    with policy.sensitive_period("scan step"):
        policy._sweep_once(interval=1.0)
        assert pv.auto_monitor is True, "demoted mid-acquisition"
    # and it still gets demoted once the window closes
    policy._sweep_once(interval=1.0)
    assert pv.auto_monitor is False


def test_reads_get_a_more_patient_budget_while_sensitive(policy):
    assert policy.read_retries() == policy.CA_READ_RETRIES
    with policy.sensitive_period("scan step"):
        assert policy.read_retries() == policy.CA_READ_RETRIES_SENSITIVE
    assert policy.read_retries() == policy.CA_READ_RETRIES


def test_sensitive_periods_nest(policy):
    with policy.sensitive_period("outer"):
        with policy.sensitive_period("inner"):
            assert policy.in_sensitive_period()
        assert policy.in_sensitive_period(), "the inner exit ended both"
    assert not policy.in_sensitive_period()


def test_a_raising_body_still_ends_the_period(policy):
    with pytest.raises(ValueError):
        with policy.sensitive_period("boom"):
            raise ValueError("boom")
    assert not policy.in_sensitive_period()


# --------------------------------------------------------------------------
# _read_pv: the single chokepoint that stops the next caller needing its own
# private cache


class FlakyPV:
    """A PV that returns None for the first `n_none` reads, then a value."""

    def __init__(self, n_none, value=42.0, connected=True,
                 pvname="TEST:FLAKY"):
        self.pvname = pvname
        self.connected = connected
        self._left = n_none
        self._value = value
        self.reads = 0

    def get(self, timeout=None):
        self.reads += 1
        if self._left > 0:
            self._left -= 1
            return None
        return self._value


@pytest.fixture
def read_pv(monkeypatch):
    from eco.epics_utils import adjustable

    monkeypatch.setattr(ca_tuning, "_last_ok", {})
    monkeypatch.setattr(ca_tuning, "CA_READ_RETRY_DELAY", 0.0)
    monkeypatch.setattr(ca_tuning, "_sensitive_depth", 0)
    return adjustable._read_pv


def test_a_transient_none_is_retried_into_a_real_value(read_pv):
    """The recurring bug: pyepics returns None on a momentary CA hiccup
    instead of raising, and the caller uses it as data (`int(None)`,
    `100 / None`, `event_codes[None]`)."""
    ca_tuning.note_successful_read("TEST:FLAKY")  # it has worked before
    pv = FlakyPV(n_none=1)
    assert read_pv(pv, name="flaky") == 42.0
    assert pv.reads == 2


def test_retries_are_bounded(read_pv):
    ca_tuning.note_successful_read("TEST:FLAKY")
    pv = FlakyPV(n_none=99)
    assert read_pv(pv, name="flaky") is None
    assert pv.reads == 1 + ca_tuning.CA_READ_RETRIES


def test_a_channel_that_never_worked_is_not_retried(read_pv):
    """An absent PV is read on every get_status() fan-out, i.e. once per
    scan step - it must not pay for retries it will never win."""
    pv = FlakyPV(n_none=99, connected=False, pvname="TEST:ABSENT")
    assert read_pv(pv, name="absent") is None
    assert pv.reads == 1


def test_a_sensitive_period_buys_more_attempts(read_pv):
    ca_tuning.note_successful_read("TEST:FLAKY")
    pv = FlakyPV(n_none=3)
    with ca_tuning.sensitive_period("scan step"):
        assert read_pv(pv, name="flaky") == 42.0
    assert pv.reads == 4  # would have given up at 3 outside the period


def test_a_successful_read_needs_no_retry(read_pv):
    ca_tuning.note_successful_read("TEST:FLAKY")
    pv = FlakyPV(n_none=0)
    assert read_pv(pv, name="flaky") == 42.0
    assert pv.reads == 1


# --------------------------------------------------------------------------
# the sweeper thread must attach to the shared CA context
#
# It calls pv.auto_monitor = False / remove_callback on channels it did not
# create - libca's ca_clear_subscription() from a thread that never attached
# to the same CA context as the channel is undefined behaviour, not merely
# "affects only that one subscription". This codebase has already been
# burned by exactly this class of bug once (a real segfault from concurrent
# init_all() workers each implicitly creating their own context - see
# eco.utilities.config._run_init_pass and eco.status_server.namespace_store
# ._ca_thread, both of which attach explicitly for this reason).


def test_sweep_loop_attaches_to_the_shared_ca_context(monkeypatch):
    calls = []
    fake_ca = types.SimpleNamespace(use_initial_context=lambda: calls.append(1))
    fake_epics = types.ModuleType("epics")
    fake_epics.ca = fake_ca
    monkeypatch.setitem(sys.modules, "epics", fake_epics)
    monkeypatch.setitem(sys.modules, "epics.ca", fake_ca)

    # run the loop body once by making time.sleep raise after the first call,
    # so _sweep_loop's infinite `while True` doesn't actually hang the test
    monkeypatch.setattr(ca_tuning.time, "sleep", lambda s: (_ for _ in ()).throw(
        KeyboardInterrupt()))
    try:
        ca_tuning._sweep_loop()
    except KeyboardInterrupt:
        pass

    assert calls, "the sweeper thread never attached to the shared CA context"


def test_sweeper_context_attach_failure_does_not_crash_the_thread(monkeypatch):
    """A context-attach failure must not prevent the loop (and its sweeps)
    from running at all - degrade, don't crash a daemon thread silently."""
    fake_ca = types.SimpleNamespace(
        use_initial_context=lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    fake_epics = types.ModuleType("epics")
    fake_epics.ca = fake_ca
    monkeypatch.setitem(sys.modules, "epics", fake_epics)
    monkeypatch.setitem(sys.modules, "epics.ca", fake_ca)
    monkeypatch.setattr(ca_tuning.time, "sleep", lambda s: (_ for _ in ()).throw(
        KeyboardInterrupt()))
    try:
        ca_tuning._sweep_loop()
    except KeyboardInterrupt:
        pass  # reaching this point at all is the assertion
