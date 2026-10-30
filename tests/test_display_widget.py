"""Tests for eco.widgets.display_widget (the ipywidgets/lab/Voila property
grid): writes off the kernel thread with a status dot, monitored rows, the
IOC tweak step, rows still connecting at opening."""

import threading
import time

import pytest

widgets = pytest.importorskip("ipywidgets")

from eco.widgets import display_widget, live_values


def wait_for(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


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


class Adjustable:
    def __init__(self, name="a", value=1.0, duration=0.0, error=None):
        self.name = name
        self.value = value
        self.duration = duration
        self.error = error
        self.targets = []
        self.changer = None

    def get_current_value(self):
        return self.value

    def set_target_value(self, value):
        if self.error is not None:
            raise self.error
        self.targets.append(value)
        self.value = value
        self.changer = Changer(self.duration)
        return self.changer


def buttons(box):
    found = []

    def walk(w):
        if isinstance(w, widgets.Button):
            found.append(w)
        for child in getattr(w, "children", ()):
            walk(child)

    walk(box)
    return found


def tooltip(row_state):
    return row_state.dot.value


def test_a_click_returns_at_once_and_stop_works_during_the_move():
    item = Adjustable(duration=5.0)
    box, state = display_widget._build_item_controls_widget(item, widgets.Label("1.0"), 1.0)
    up = next(b for b in buttons(box) if b.description == "▲")
    stop = next(b for b in buttons(box) if b.description == "🛑")
    t0 = time.monotonic()
    up.click()
    assert time.monotonic() - t0 < 0.2  # used to block the kernel for up to 5 s
    assert wait_for(lambda: "moving" in tooltip(state["row_state"]))
    assert item.targets == [pytest.approx(1.1)]
    stop.click()
    assert wait_for(lambda: item.changer.stopped)
    assert wait_for(lambda: "moving" not in tooltip(state["row_state"]))


def test_a_blocked_write_shows_red_with_the_reason():
    item = Adjustable(error=PermissionError("mot is in use by queue 'default', task #1 -- not moved."))
    box, state = display_widget._build_item_controls_widget(item, widgets.Label("1.0"), 1.0)
    state["input_widget"].value = 2.0
    assert wait_for(lambda: "in use by queue" in tooltip(state["row_state"]))
    assert "#d93025" in tooltip(state["row_state"])


class FakePV:
    def __init__(self, value):
        self.value = value
        self.puts = []

    def get(self, timeout=None):
        return self.value

    def put(self, value):
        self.puts.append(value)


class MotorRecord(Adjustable):
    def __init__(self):
        super().__init__(value=3.0)
        self.twv = FakePV(0.25)
        self._motor = type("M", (), {"get_pv": lambda _, field: self.twv})()

    def _tweak_ioc(self):
        pass


def test_the_tweak_step_comes_from_the_ioc_and_edits_go_back():
    item = MotorRecord()
    box, state = display_widget._build_item_controls_widget(item, widgets.Label("3.0"), 3.0)
    step = next(w for w in box.children if isinstance(w, widgets.FloatText) and w is not state["input_widget"])
    assert wait_for(lambda: step.value == 0.25)
    assert item.twv.puts == []  # loading it did not write it back
    step.value = 0.5
    assert wait_for(lambda: item.twv.puts == [0.5])


class Monitored(Adjustable):
    def __init__(self, name):
        super().__init__(name=name, value=0.0)
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


class Hanging(Adjustable):
    def __init__(self, name, delay):
        super().__init__(name=name, value=7.0)
        self.delay = delay

    def get_current_value(self):
        time.sleep(self.delay)
        return self.value


class FakeAssembly:
    name = "fake"

    def __init__(self, items):
        self.items = items
        self.status_collection = type("SC", (), {"get_list": lambda _, selection=None: list(items)})()

    def display_collection(self):
        return list(self.items)


def test_grid_opens_fast_monitors_and_builds_late_controls(monkeypatch):
    monkeypatch.setattr(live_values, "PREFETCH_DEADLINE", 0.5)
    monitored = Monitored("monitored")
    slow = Hanging("slow", delay=1.5)
    t0 = time.monotonic()
    grid = display_widget.make_assembly_widget(FakeAssembly([monitored, slow]), poll_interval=30.0)
    try:
        assert time.monotonic() - t0 < 1.5
        rows = [w for w in grid.children if isinstance(w, widgets.HBox) and len(w.children) >= 3]
        slow_row = next(r for r in rows if r.children[0].value == "slow")
        monitored_row = next(r for r in rows if r.children[0].value == "monitored")
        assert slow_row.children[1].value == "…"
        # monitored: a push shows at once although the poll interval is 30 s
        assert wait_for(lambda: monitored.callbacks)
        monitored.push(4.5)
        assert wait_for(lambda: monitored_row.children[1].value == "4.5", 1.0)
        assert "updated by monitor" in monitored_row.children[2].children[0].value
        # the slow row gets its value, then its controls
        assert wait_for(lambda: slow_row.children[1].value == "7.0", 4.0)
        assert any(b.description == "▲" for b in buttons(slow_row.children[2]))
        # and the main ("kernel") thread was free the whole time: updates
        # reach the widgets from the reader threads, as in Voila
    finally:
        grid.stop()
    assert wait_for(lambda: not monitored.callbacks)
