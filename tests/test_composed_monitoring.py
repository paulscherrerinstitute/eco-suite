"""Push-based monitoring for computed values.

AdjustableVirtual and DetectorVirtual's set_current_value_callback() are
both backed by the shared eco.elements.adjustable.CallbackComposedValue,
which subscribes to every parent's own set_current_value_callback() and
recomputes get_current_value() whenever any of them reports an update - so
a value computed from other monitorable values can itself be monitored,
with no polling, independent of whether the chain bottoms out in CA or
something else. Refuses (NotImplementedError) rather than silently falling
back to polling when any parent does not implement
eco.elements.protocols.MonitorableValueUpdate.

No EPICS here: everything below is driven by plain fakes standing in for
the leaf level, so these tests describe the composition contract, not any
particular transport.
"""

import pytest

from eco.elements.adjustable import AdjustableVirtual, CallbackComposedValue
from eco.elements.detector import DetectorVirtual


class FakeMonitor:
    """Stands in for whatever set_current_value_callback() a leaf returns
    (CallbackEpics, or another CallbackComposedValue): remembers the
    callback CallbackComposedValue registered and lets a test fire it.

    `fire()` updates the leaf's own current value before calling the
    callback, mirroring how a real CA update lands in the PV's cache
    *before* any callback fires - CallbackComposedValue deliberately
    ignores the value/timestamp a child callback was fired with and
    re-reads get_current_value() instead (see its _on_parent_update
    docstring), so this side effect is what makes that recompute see the
    fired value at all."""

    def __init__(self, leaf, func, run_once=True):
        self.leaf = leaf
        self.func = func
        self.run_once = run_once
        self.started_with = None
        self.stopped = False

    def start(self, add_current_value=True, with_ctrlvars=True, auto_monitor=True):
        self.started_with = {
            "add_current_value": add_current_value,
            "with_ctrlvars": with_ctrlvars,
            "auto_monitor": auto_monitor,
        }
        if add_current_value:
            self.func(timestamp=0.0)

    def stop(self):
        self.stopped = True

    def fire(self, value=None, timestamp=1.0):
        self.leaf._value = value
        self.func(value=value, timestamp=timestamp)


class FakeLeaf:
    """A monitorable leaf: get_current_value() + set_current_value_callback(),
    matching eco.elements.protocols.MonitorableValueUpdate. Also implements
    set_target_value() so it can stand in as an AdjustableVirtual parent
    too, not just a DetectorVirtual one."""

    def __init__(self, name, value=0.0):
        self.name = name
        self._value = value
        self.monitors = []

    def get_current_value(self):
        return self._value

    def set_target_value(self, value, hold=False):
        self._value = value

        class _Changer:
            def wait(self_inner):
                pass

            def stop(self_inner):
                pass

        return _Changer()

    def set_current_value_callback(self, func="accumulate", run_once=True, **kwargs):
        mon = FakeMonitor(self, func, run_once=run_once)
        self.monitors.append(mon)
        return mon


class NonMonitorableLeaf:
    """The failure case: no set_current_value_callback at all - same shape
    a raw, non-PV-backed value (or one whose monitoring was never
    implemented) would have."""

    def __init__(self, name, value=0.0):
        self.name = name
        self._value = value

    def get_current_value(self):
        return self._value

    def set_target_value(self, value, hold=False):
        self._value = value


# --------------------------------------------------------------------------
# AdjustableVirtual


def test_adjustable_virtual_monitors_when_all_parents_are_monitorable():
    a = FakeLeaf("a", 1.0)
    b = FakeLeaf("b", 2.0)
    v = AdjustableVirtual(
        [a, b],
        foo_get_current_value=lambda x, y: x + y,
        foo_set_target_value_current_value=lambda val: (val, val),
        name="v",
    )
    mon = v.set_current_value_callback(func="latest")
    assert isinstance(mon, CallbackComposedValue)
    mon.start(add_current_value=False)

    a.monitors[0].fire(value=10.0)
    assert mon.data["value"] == 12.0  # recomputed: a=10 (just fired), b=2

    b.monitors[0].fire(value=20.0)
    assert mon.data["value"] == 30.0  # a=10, b=20


def test_adjustable_virtual_seed_recomputes_from_current_values():
    a = FakeLeaf("a", 1.0)
    b = FakeLeaf("b", 2.0)
    v = AdjustableVirtual(
        [a, b],
        foo_get_current_value=lambda x, y: x + y,
        foo_set_target_value_current_value=lambda val: (val, val),
        name="v",
    )
    mon = v.set_current_value_callback(func="latest")
    mon.start(add_current_value=True)
    assert mon.data["value"] == 3.0


def test_start_accepts_and_forwards_ca_style_kwargs():
    """CallbackComposedValue.start() must accept with_ctrlvars/auto_monitor
    for interface compatibility with CallbackEpics.start() - a caller that
    does not know whether a given channel is CA-backed or composed (e.g.
    RecordingSession.start(), which calls .start() the same way on every
    monitor it gets back) must be able to call either uniformly."""
    a = FakeLeaf("a", 1.0)
    v = AdjustableVirtual(
        [a],
        foo_get_current_value=lambda x: x,
        foo_set_target_value_current_value=lambda val: (val,),
        name="v",
    )
    mon = v.set_current_value_callback(func="latest")
    mon.start(add_current_value=True, with_ctrlvars=False, auto_monitor="something")
    assert a.monitors[0].started_with["with_ctrlvars"] is False
    assert a.monitors[0].started_with["auto_monitor"] == "something"


def test_adjustable_virtual_refuses_to_monitor_a_non_monitorable_parent():
    a = FakeLeaf("a", 1.0)
    b = NonMonitorableLeaf("b", 2.0)
    v = AdjustableVirtual(
        [a, b],
        foo_get_current_value=lambda x, y: x + y,
        foo_set_target_value_current_value=lambda val: (val, val),
        name="v",
    )
    with pytest.raises(NotImplementedError, match="b"):
        v.set_current_value_callback()


def test_nested_adjustable_virtual_monitoring():
    """A virtual built from another virtual - recursion works because
    CallbackComposedValue is itself a valid MonitorableValueUpdate parent."""
    a = FakeLeaf("a", 1.0)
    b = FakeLeaf("b", 2.0)
    inner = AdjustableVirtual(
        [a, b],
        foo_get_current_value=lambda x, y: x + y,
        foo_set_target_value_current_value=lambda val: (val, val),
        name="inner",
    )
    c = FakeLeaf("c", 100.0)
    outer = AdjustableVirtual(
        [inner, c],
        foo_get_current_value=lambda x, y: x * y,
        foo_set_target_value_current_value=lambda val: (val, val),
        name="outer",
    )
    mon = outer.set_current_value_callback(func="latest")
    mon.start(add_current_value=True)
    assert mon.data["value"] == 3.0 * 100.0  # (a + b) * c = 3 * 100

    a.monitors[0].fire(value=10.0)
    assert mon.data["value"] == (10.0 + 2.0) * 100.0


# --------------------------------------------------------------------------
# DetectorVirtual - same contract, no set_target_value needed


def test_detector_virtual_monitors_when_all_parents_are_monitorable():
    a = FakeLeaf("a", 1.0)
    b = FakeLeaf("b", 2.0)
    v = DetectorVirtual([a, b], foo_get_current_value=lambda x, y: x + y, name="v")
    mon = v.set_current_value_callback(func="latest")
    assert isinstance(mon, CallbackComposedValue)
    mon.start(add_current_value=False)

    a.monitors[0].fire(value=10.0)
    assert mon.data["value"] == 12.0


def test_detector_virtual_seed_recomputes_from_current_values():
    a = FakeLeaf("a", 1.0)
    b = FakeLeaf("b", 2.0)
    v = DetectorVirtual([a, b], foo_get_current_value=lambda x, y: x + y, name="v")
    mon = v.set_current_value_callback(func="latest")
    mon.start(add_current_value=True)
    assert mon.data["value"] == 3.0


def test_detector_virtual_refuses_to_monitor_a_non_monitorable_parent():
    a = FakeLeaf("a", 1.0)
    b = NonMonitorableLeaf("b", 2.0)
    v = DetectorVirtual([a, b], foo_get_current_value=lambda x, y: x + y, name="v")
    with pytest.raises(NotImplementedError, match="b"):
        v.set_current_value_callback()


def test_detector_virtual_of_adjustable_virtual_monitoring():
    """Mixed nesting: a DetectorVirtual built on top of an AdjustableVirtual
    parent - the protocol check is generic, not type-specific."""
    a = FakeLeaf("a", 1.0)
    b = FakeLeaf("b", 2.0)
    inner = AdjustableVirtual(
        [a, b],
        foo_get_current_value=lambda x, y: x + y,
        foo_set_target_value_current_value=lambda val: (val, val),
        name="inner",
    )
    outer = DetectorVirtual([inner], foo_get_current_value=lambda x: x * 2, name="outer")
    mon = outer.set_current_value_callback(func="latest")
    mon.start(add_current_value=True)
    assert mon.data["value"] == 6.0  # (a + b) * 2 = 3 * 2

    a.monitors[0].fire(value=10.0)
    assert mon.data["value"] == 24.0  # (10 + 2) * 2
