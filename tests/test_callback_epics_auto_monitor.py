"""CallbackEpics.start()/stop()'s auto_monitor handling: reference-counted
per PV, not a per-instance snapshot/restore.

Real bug this fixes: two CallbackEpics on the same PV (e.g. the status
server's own permanent baseline monitor plus a RecordingSession, or two
overlapping RecordingSessions) used to each remember whatever auto_monitor
was at their own start() and blindly restore it at stop() - so whichever
stopped *first* could clobber the auto_monitor state a still-running sibling
needs, silently. A plain reference count (only the first attacher changes
auto_monitor, only the last detacher restores it) makes stop() order-
independent.
"""
from eco.epics_utils.utilities_epics import CallbackEpics


class FakePV:
    def __init__(self, pvname="fake:pv", value=0.0):
        self.pvname = pvname
        self.auto_monitor = False
        self.callbacks = {}
        self.timestamp = 0.0
        self._value = value
        self._next_index = 0

    def get(self):
        return self._value

    def add_callback(self, func, run_once=True, with_ctrlvars=True):
        idx = self._next_index
        self._next_index += 1
        self.callbacks[idx] = func
        return idx

    def remove_callback(self, idx):
        self.callbacks.pop(idx, None)


def _cb(pv):
    return CallbackEpics(pv, func="latest")


def test_single_callback_restores_the_original_auto_monitor_on_stop():
    pv = FakePV()
    pv.auto_monitor = False
    cb = _cb(pv)

    cb.start(add_current_value=False, auto_monitor=True)
    assert pv.auto_monitor is True

    cb.stop()
    assert pv.auto_monitor is False


def test_two_overlapping_callbacks_the_first_to_stop_does_not_clobber_the_second():
    pv = FakePV()
    pv.auto_monitor = False
    first = _cb(pv)
    second = _cb(pv)

    first.start(add_current_value=False, auto_monitor=True)
    second.start(add_current_value=False, auto_monitor=True)
    assert pv.auto_monitor is True

    # first ends (e.g. a recording gets captured) while second is still
    # running (e.g. another recording, or the status server's own baseline
    # monitor) - auto_monitor must stay on for second, not revert to the
    # pre-existing baseline first happened to see at its own start.
    first.stop()
    assert pv.auto_monitor is True
    assert second.is_running()

    second.stop()
    assert pv.auto_monitor is False


def test_stop_order_does_not_matter():
    pv = FakePV()
    pv.auto_monitor = False
    first = _cb(pv)
    second = _cb(pv)
    first.start(add_current_value=False, auto_monitor=True)
    second.start(add_current_value=False, auto_monitor=True)

    # stop the *second*-started one first this time
    second.stop()
    assert pv.auto_monitor is True

    first.stop()
    assert pv.auto_monitor is False


def test_three_overlapping_callbacks_only_the_last_stop_restores_baseline():
    pv = FakePV()
    pv.auto_monitor = False
    cbs = [_cb(pv) for _ in range(3)]
    for cb in cbs:
        cb.start(add_current_value=False, auto_monitor=True)

    cbs[0].stop()
    cbs[1].stop()
    assert pv.auto_monitor is True  # third still running

    cbs[2].stop()
    assert pv.auto_monitor is False


def test_each_callback_still_gets_every_update_independently():
    """The refcount change must not affect pyepics's own fan-out - each
    CallbackEpics still gets its own independent copy of every update."""
    pv = FakePV()
    first = _cb(pv)
    second = _cb(pv)
    first.start(add_current_value=False, auto_monitor=True)
    second.start(add_current_value=False, auto_monitor=True)

    for func in list(pv.callbacks.values()):
        func(pvname=pv.pvname, value=1.23, timestamp=100.0)

    assert first.data["value"] == 1.23
    assert second.data["value"] == 1.23
