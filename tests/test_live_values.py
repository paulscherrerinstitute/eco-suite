"""Tests for eco.widgets.live_values: the toolkit-independent reading/writing
behind the Qt, ipywidgets and Tk property grids."""

import threading
import time

from eco.widgets import live_values


def wait_for(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class Item:
    def __init__(self, name, value=1.0, delay=0.0):
        self.name = name
        self.value = value
        self.delay = delay
        self.reads = 0

    def get_current_value(self):
        self.reads += 1
        time.sleep(self.delay)
        return self.value


class Monitored(Item):
    def __init__(self, name):
        super().__init__(name)
        self.callbacks = {}

    def add_value_callback(self, callback, index=None):
        index = len(self.callbacks) + 1
        self.callbacks[index] = callback
        return index

    def clear_value_callback(self, index=None):
        self.callbacks.pop(index, None)

    def push(self, value):
        self.value = value
        for callback in list(self.callbacks.values()):
            callback(pvname=self.name, value=value)


class CallbackEpicsLike:
    """What set_current_value_callback returns for PV-backed items."""

    def __init__(self, func):
        self.func = func
        self.started = False

    def start(self, add_current_value=True, with_ctrlvars=True, auto_monitor=True):
        self.started = (add_current_value, with_ctrlvars)

    def stop(self):
        self.started = None


class PvMonitored(Item):
    def set_current_value_callback(self, func="accumulate", **kwargs):
        self.monitor = CallbackEpicsLike(func)
        return self.monitor


def collect():
    results = []
    return results, lambda index, ok, payload: results.append((index, ok, payload))


def test_monitored_items_update_at_once_and_unsubscribe_on_stop():
    results, on_result = collect()
    item = Monitored("m")
    reader = live_values.LiveReader(on_result, poll_interval=30.0)
    reader.add(item)
    reader.start()
    try:
        assert wait_for(lambda: item.callbacks)
        assert wait_for(lambda: results)  # the first read
        assert not reader.monitored(0)  # subscribed, but no event yet: not trusted
        item.push(5.0)
        assert wait_for(lambda: results[-1] == (0, True, 5.0), 1.0)  # not after 30 s
        assert reader.monitored(0)
    finally:
        reader.stop()
    assert wait_for(lambda: not item.callbacks)


def test_a_monitor_without_parents_is_not_trusted():
    """e.g. DetectorVirtual([], fn) -- vacuum valve readbacks, the prepump state."""

    class Composed:
        children = []

        def start(self, **kwargs):
            pass

        def stop(self):
            pass

    item = Item("readback")
    item.set_current_value_callback = lambda func=None, **kw: Composed()
    assert live_values.subscribe(item, lambda: None) is None


def test_a_monitor_that_misses_changes_is_demoted(monkeypatch):
    """e.g. PshellMotor: calls back only when a move ends."""
    monkeypatch.setattr(live_values, "MONITORED_POLL_INTERVAL", 0.3)
    results, on_result = collect()
    item = Monitored("pshell")
    reader = live_values.LiveReader(on_result, poll_interval=0.2)
    reader.add(item)
    reader.start()
    try:
        assert wait_for(lambda: item.callbacks)
        item.push(1.0)  # a move ended: an event
        assert wait_for(lambda: reader.monitored(0))
        time.sleep(0.5)  # the read that event asked for is done
        item.value = 2.0  # the next move: no event
        assert wait_for(lambda: not reader.monitored(0), 2.0)
        assert wait_for(lambda: results[-1] == (0, True, 2.0))
    finally:
        reader.stop()


def test_pv_style_monitor_is_started_without_blocking_reads():
    reader = live_values.LiveReader(lambda *a: None, poll_interval=30.0)
    item = PvMonitored("pv")
    reader.add(item)
    reader.start()
    try:
        assert wait_for(lambda: getattr(item, "monitor", None) is not None and item.monitor.started)
        assert item.monitor.started == (False, False)  # no seeding get, no ctrlvars get
    finally:
        reader.stop()
    assert wait_for(lambda: item.monitor.started is None)


def test_a_slow_item_does_not_hold_up_the_others():
    results, on_result = collect()
    slow, fast = Item("slow", delay=2.0), Item("fast", value=3.0)
    reader = live_values.LiveReader(on_result, poll_interval=0.1)
    reader.add(slow)
    reader.add(fast)
    reader.start()
    try:
        assert wait_for(lambda: sum(1 for r in results if r[0] == 1) >= 3, 1.5)
    finally:
        reader.stop()


def test_no_value_and_errors_are_reported():
    results, on_result = collect()

    class Broken:
        def get_current_value(self):
            raise RuntimeError("IOC down")

    dead = Item("dead", value=None)
    reader = live_values.LiveReader(on_result, poll_interval=30.0)
    reader.add(dead)
    reader.add(Broken())
    reader.start()
    try:
        assert wait_for(lambda: {r[0] for r in results} == {0, 1})
    finally:
        reader.stop()
    by_index = {r[0]: r for r in results}
    assert by_index[0] == (0, False, "no value (not connected?)")
    assert by_index[1] == (1, False, "RuntimeError: IOC down")


def test_inactive_items_are_not_read_until_activated_and_restart_works():
    results, on_result = collect()
    item = Item("hidden")
    reader = live_values.LiveReader(on_result, poll_interval=30.0)
    reader.add(item, active=False)
    reader.start()
    time.sleep(0.3)
    assert item.reads == 0
    reader.set_active(0, True)
    assert wait_for(lambda: item.reads == 1)
    reader.stop()
    reader.start()  # e.g. make_assembly_widget's start() after stop()
    try:
        assert wait_for(lambda: item.reads == 2)
    finally:
        reader.stop()


class Changer:
    def __init__(self, seconds):
        self.done = threading.Event()
        self.seconds = seconds
        self.stopped = False

    def wait(self):
        self.done.wait(self.seconds)

    def stop(self):
        self.stopped = True
        self.done.set()


def test_start_write_reports_the_move_and_errors():
    states = []
    finished = threading.Event()
    item = Item("mot")
    holder = {}
    item.set_target_value = lambda value: Changer(0.3)
    live_values.start_write(item, lambda: 2.0, lambda *s: states.append(s), finished.set, holder)
    assert wait_for(finished.is_set)
    assert states == [("moving", "to 2.0"), ("idle", "")]
    assert isinstance(holder["changer"], Changer)

    states.clear()
    finished.clear()

    def blocked(value):
        raise PermissionError("mot is in use by queue 'default', task #1 -- not moved.")

    item.set_target_value = blocked
    live_values.start_write(item, lambda: 2.0, lambda *s: states.append(s), finished.set)
    assert wait_for(finished.is_set)
    assert states == [("error", "mot is in use by queue 'default', task #1 -- not moved.")]


def test_stop_move_stops_the_changer_or_the_item():
    changer = Changer(5)
    live_values.stop_move(None, changer, lambda *s: None)
    assert wait_for(lambda: changer.stopped)

    item = Item("mot")
    item.stopped = False
    item.stop = lambda: setattr(item, "stopped", True)
    live_values.stop_move(item, None, lambda *s: None)
    assert wait_for(lambda: item.stopped)


def test_prefetch_has_a_deadline():
    slow, fast = Item("slow", delay=1.5), Item("fast", value=4.0)
    t0 = time.monotonic()
    values = live_values.prefetch_values([slow, fast], deadline=0.3)
    assert time.monotonic() - t0 < 0.8
    assert values[id(slow)] is live_values.PENDING
    assert values[id(fast)] == 4.0


def test_step_sources():
    item = Item("mot")
    assert live_values.initial_step(item, 1) == 1
    assert live_values.initial_step(item, 1.0) == 0.1
    item._tweak_instance = type("T", (), {"step_sizes": [0.02]})()
    assert live_values.initial_step(item, 1.0) == 0.02

    class Pv:
        def __init__(self):
            self.puts = []

        def get(self, timeout=None):
            return 0.5

        def put(self, value):
            self.puts.append(value)

    class Motor(Item):
        def _tweak_ioc(self):
            pass

    motor = Motor("m")
    pv = Pv()
    motor._motor = type("M", (), {"get_pv": lambda _, field: pv})()
    assert live_values.has_ioc_step(motor) and not live_values.has_ioc_step(item)
    got = []
    holder = live_values.load_ioc_step(motor, got.append)
    assert wait_for(lambda: got == [0.5])
    live_values.store_ioc_step(holder, 0.25)
    assert wait_for(lambda: pv.puts == [0.25])


def test_dot_state():
    assert live_values.dot_state("moving", "to 2", ("polled", ""))[1] == "moving: to 2"
    assert live_values.dot_state("idle", "", ("live", ""))[1] == "connected, updated by monitor"
    assert live_values.dot_state("error", "x", ("live", ""))[0] == "#d93025"
