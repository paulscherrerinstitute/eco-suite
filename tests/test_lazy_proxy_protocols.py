"""Regression tests for protocol checks against still-lazy namespace proxies.

`eco.utilities.config.Proxy` hides its `__class__`/`__dir__` while unresolved
so that introspection cannot initialize a device. A `runtime_checkable`
Protocol check reads exactly those, so `isinstance(cold_proxy, Adjustable)` is
False for a real adjustable that simply hasn't been built yet. That silently
broke three things: `Daq.pgroup` leaked the proxy into `json.dumps` ("Object of
type LazyComponent is not JSON serializable"), `Scans.scan` dropped or
misclassified a lazy axis, and `CounterValue.append_detectors` rejected a lazy
detector as "not a Detector".
"""

import json

import pytest

from eco.elements.protocols import (
    Adjustable,
    Detector,
    is_adjustable,
    is_detector,
    resolve_lazy,
)
from eco.utilities.config import Proxy


class FakeAdjustable:
    def __init__(self, value="p12345"):
        self.value = value

    def get_current_value(self):
        return self.value

    def set_target_value(self, value):
        self.value = value
        return _DoneChanger()


class _DoneChanger:
    def wait(self):
        return "done"


class FakeDetector:
    def get_current_value(self):
        return 1.0


def test_cold_proxy_stays_shy_to_plain_isinstance():
    """The shyness itself is deliberate and must not regress -- it is what
    keeps tab-completion and the namespace browser from building devices."""
    built = []
    proxy = Proxy(lambda: built.append(1) or FakeAdjustable())

    assert isinstance(proxy, Adjustable) is False
    assert built == []


def test_is_adjustable_resolves_and_answers_truthfully():
    built = []
    proxy = Proxy(lambda: built.append(1) or FakeAdjustable())

    assert is_adjustable(proxy) is True
    assert built == [1]
    assert isinstance(proxy, Adjustable) is True  # shy only until first use


def test_is_detector_resolves_and_answers_truthfully():
    proxy = Proxy(FakeDetector)

    assert is_detector(proxy) is True
    assert is_adjustable(proxy) is False  # no set_target_value


def test_resolve_lazy_returns_the_wrapped_object():
    adj = FakeAdjustable()
    assert resolve_lazy(Proxy(lambda: adj)) is adj


@pytest.mark.parametrize("value", ["p12345", None, 42, object()])
def test_resolve_lazy_passes_non_proxies_through_unchanged(value):
    assert resolve_lazy(value) is value


def _daq_like(pgroup):
    """Bind the real `Daq.pgroup` property onto a stub, so the property under
    test is the shipped one without constructing a Daq (which would talk to
    EPICS and the broker)."""
    from eco.acquisition.daq_client import Daq

    class DaqLike:
        pass

    DaqLike.pgroup = Daq.pgroup
    obj = DaqLike()
    obj._pgroup = pgroup
    return obj


def test_daq_pgroup_from_cold_proxy_is_the_value_not_the_proxy():
    daq = _daq_like(Proxy(lambda: FakeAdjustable("p12345")))

    assert daq.pgroup == "p12345"
    # the actual reported failure: this used to raise
    # TypeError: Object of type LazyComponent is not JSON serializable
    assert json.dumps({"pgroup": daq.pgroup}) == '{"pgroup": "p12345"}'


def test_daq_pgroup_setter_moves_the_adjustable_behind_a_cold_proxy():
    adj = FakeAdjustable("p12345")
    daq = _daq_like(Proxy(lambda: adj))

    daq.pgroup = "p99999"

    assert adj.value == "p99999"  # not overwritten by a bare string
    assert daq.pgroup == "p99999"


def test_daq_pgroup_still_supports_a_plain_string():
    daq = _daq_like("p11111")
    assert daq.pgroup == "p11111"

    daq.pgroup = "p22222"
    assert daq.pgroup == "p22222"


def test_counters_accept_a_still_lazy_detector():
    from eco.acquisition.counters import CounterValue

    counters = CounterValue.__new__(CounterValue)
    counters.detectors = []
    counters.monitorables = []
    det = FakeDetector()

    counters.append_detectors(Proxy(lambda: det))

    assert counters.detectors == [det]


def test_counters_still_reject_a_non_detector():
    from eco.acquisition.counters import CounterValue

    counters = CounterValue.__new__(CounterValue)
    counters.detectors = []
    counters.monitorables = []

    with pytest.raises(TypeError):
        counters.append_detectors(object())
