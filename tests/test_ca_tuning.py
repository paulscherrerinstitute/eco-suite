"""Silent-None diagnostics and the CA connection-timeout trial."""
import logging
import time

import pytest


class FakePV:
    def __init__(self, values, pvname="FAKE:PV", connected=True):
        self.values = list(values)
        self.pvname = pvname
        self.connected = connected
        self.connection_timeout = 1.0
        self.timeouts = []

    def get(self, timeout=None):
        self.timeouts.append(timeout)
        return self.values.pop(0) if self.values else None


@pytest.fixture(autouse=True)
def _clean_state():
    from eco.epics_utils import ca_tuning

    ca_tuning._last_ok.clear()
    ca_tuning._last_report.clear()
    ca_tuning._none_counts.clear()
    yield
    ca_tuning._last_ok.clear()
    ca_tuning._last_report.clear()
    ca_tuning._none_counts.clear()


def test_a_none_read_is_reported_and_the_value_still_returned(caplog):
    """The None must still be returned unchanged - this is diagnostics, not
    a behaviour change - but it must no longer be silent."""
    from eco.epics_utils.adjustable import _read_pv

    pv = FakePV([], pvname="SOME:MISSING:PV")
    with caplog.at_level(logging.WARNING):
        assert _read_pv(pv, name="thing") is None
    assert "SOME:MISSING:PV" in caplog.text
    assert "never returned a value" in caplog.text


def test_reporting_is_rate_limited_per_pv(caplog):
    """A dead PV in a get_status() fan-out is read thousands of times per
    scan; it must not produce thousands of log lines."""
    from eco.epics_utils.adjustable import _read_pv

    pv = FakePV([], pvname="SOME:MISSING:PV")
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            _read_pv(pv, name="thing")
    assert caplog.text.count("silent None") == 1


def test_a_previously_working_pv_is_reported_as_a_transient(caplog):
    from eco.epics_utils.adjustable import _read_pv

    pv = FakePV([1.5], pvname="SOME:FLAKY:PV")
    assert _read_pv(pv, name="flaky") == 1.5
    with caplog.at_level(logging.WARNING):
        assert _read_pv(pv, name="flaky") is None
    assert "last returned a value" in caplog.text
    assert "never returned" not in caplog.text


def test_never_connected_pv_keeps_the_short_budget():
    """The raised CA_CONNECTION_TIMEOUT must not make absent PVs expensive:
    bernina's namespace has plenty and they are read once per scan step."""
    from eco.epics_utils.adjustable import _read_pv
    from eco.epics_utils.ca_tuning import CA_INIT_CONNECTION_TIMEOUT

    pv = FakePV([], pvname="SOME:ABSENT:PV", connected=False)
    _read_pv(pv, name="absent")
    assert pv.timeouts == [CA_INIT_CONNECTION_TIMEOUT]


def test_a_disconnected_but_previously_working_pv_gets_the_full_budget():
    """A dropped virtual circuit is exactly what the longer budget is for -
    and, since this channel demonstrably works, also worth retrying: the
    retry lives in `_read_pv` so no individual caller has to grow its own
    cache the way `Daq.get_pulse_id` and the event-code frequency did."""
    from eco.epics_utils import ca_tuning
    from eco.epics_utils.adjustable import _read_pv

    pv = FakePV([2.0], pvname="SOME:FLAKY:PV", connected=True)
    _read_pv(pv, name="flaky")           # succeeds once -> known good
    pv.connected = False
    pv.timeouts.clear()
    _read_pv(pv, name="flaky")
    assert pv.timeouts, "no read was attempted at all"
    assert set(pv.timeouts) == {None}, "should use the PV's own connection_timeout"
    assert len(pv.timeouts) == 1 + ca_tuning.CA_READ_RETRIES


def test_falsy_values_are_not_treated_as_failures(caplog):
    from eco.epics_utils.adjustable import _read_pv

    pv = FakePV([0], pvname="SOME:ZERO:PV")
    with caplog.at_level(logging.WARNING):
        assert _read_pv(pv, name="zero") == 0
    assert "silent None" not in caplog.text


class FakeConnPV:
    def __init__(self, pvname="FAKE:PV"):
        self.pvname = pvname
        self.waited = []

    def wait_for_connection(self, timeout=None):
        self.waited.append(timeout)
        return True


def test_wait_for_initialisation_covers_the_readback_channel():
    """`_wait_for_initialisation` asked for `_pv_readback`, but the attribute
    is `_pvreadback` - so every guard was silently False and only the
    setpoint PV was ever waited for, never the readback, which is the channel
    `get_current_value()` actually reads."""
    from eco.epics_utils.adjustable import _wait_for_pvs
    from eco.epics_utils.ca_tuning import CA_INIT_CONNECTION_TIMEOUT

    class Obj:
        pass

    o = Obj()
    o._pv = FakeConnPV("X:SP")
    o._pvreadback = FakeConnPV("X:RB")
    o._pvlowlim = FakeConnPV("X:LOW")
    o._pvhighlim = FakeConnPV("X:HIGH")

    _wait_for_pvs(o)

    for pv in (o._pv, o._pvreadback, o._pvlowlim, o._pvhighlim):
        assert pv.waited == [CA_INIT_CONNECTION_TIMEOUT], pv.pvname


def test_both_attribute_spellings_are_accepted():
    """Accept `_pv_readback` as well as `_pvreadback`, so this keeps working
    whichever convention a class (or an older checkout) uses."""
    from eco.epics_utils.adjustable import _wait_for_pvs

    class Obj:
        pass

    o = Obj()
    o._pv = FakeConnPV("X:SP")
    o._pv_readback = FakeConnPV("X:RB")     # the other spelling
    o._pv_lowlim = FakeConnPV("X:LOW")

    _wait_for_pvs(o)

    assert o._pv_readback.waited, "alternative spelling was not waited for"
    assert o._pv_lowlim.waited


def test_each_pv_is_waited_for_only_once():
    """AdjustablePv points _pvreadback at the setpoint name when no separate
    readback is configured; that must not be waited for twice."""
    from eco.epics_utils.adjustable import _wait_for_pvs

    class Obj:
        pass

    shared = FakeConnPV("X:SP")
    o = Obj()
    o._pv = shared
    o._pvreadback = shared
    _wait_for_pvs(o)
    assert len(shared.waited) == 1


def test_wait_never_raises_on_a_broken_pv():
    """A readiness check must not turn into a hard failure of the containing
    assembly."""
    from eco.epics_utils.adjustable import _wait_for_pvs

    class Boom:
        pvname = "X:BOOM"

        def wait_for_connection(self, timeout=None):
            raise RuntimeError("channel access exploded")

    class Obj:
        pass

    o = Obj()
    o._pv = Boom()
    o._pvreadback = None
    _wait_for_pvs(o)          # must not raise
