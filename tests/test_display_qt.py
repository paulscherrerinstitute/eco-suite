"""Tests for eco.widgets.display_qt's responsiveness rules: writes off the
GUI thread with a status dot, monitored rows, parallel/bounded reads, and the
tweak step from the IOC."""

import threading
import time

import pytest

pytest.importorskip("qtpy")

from qtpy import QtCore, QtWidgets

from eco.widgets import display_qt


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(app, seconds, until=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else None


class _Changer:
    def __init__(self, duration):
        self.done = threading.Event()
        self.stopped = False
        self._duration = duration

    def wait(self):
        self.done.wait(self._duration)

    def stop(self):
        self.stopped = True
        self.done.set()


class SlowAdjustable:
    name = "slow"

    def __init__(self, value=1.0, duration=1.0, error=None):
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
        self.changer = _Changer(self.duration)
        return self.changer


def dot_of(controls):
    return controls.controller.dot


def test_a_click_returns_at_once_and_the_row_shows_the_move(app):
    item = SlowAdjustable(duration=5.0)
    controls, _ = display_qt._build_item_controls_qt(item, QtWidgets.QLabel("1.0"), 1.0)
    up = next(b for b in controls.findChildren(QtWidgets.QPushButton) if b.text() == "▲")
    stop = next(b for b in controls.findChildren(QtWidgets.QPushButton) if b.text() == "🛑")
    t0 = time.monotonic()
    up.click()
    assert time.monotonic() - t0 < 0.2  # used to block for up to 5 s
    assert pump(app, 2, until=lambda: dot_of(controls).toolTip().startswith("moving"))
    assert item.targets == [pytest.approx(1.1)]
    stop.click()  # clickable during the move
    assert pump(app, 2, until=lambda: item.changer.stopped)
    assert pump(app, 2, until=lambda: not dot_of(controls).toolTip().startswith("moving"))


@pytest.mark.parametrize(
    "error, shown",
    [
        (PermissionError("mot is in use by queue 'default', task #1 -- not moved."), "in use by queue"),
        (RuntimeError("limit switch"), "RuntimeError: limit switch"),
    ],
)
def test_a_failed_or_blocked_write_shows_red_with_the_reason(app, error, shown):
    item = SlowAdjustable(error=error)
    controls, _ = display_qt._build_item_controls_qt(item, QtWidgets.QLabel("1.0"), 1.0)
    entry = next(e for e in controls.findChildren(QtWidgets.QLineEdit) if e.text() == "1.0")
    entry.setText("2.0")
    entry.editingFinished.emit()
    assert pump(app, 2, until=lambda: shown in dot_of(controls).toolTip())
    assert "#d93025" in dot_of(controls).styleSheet()


class FakePV:
    def __init__(self, value):
        self.value = value
        self.puts = []

    def get(self, timeout=None):
        return self.value

    def put(self, value):
        self.puts.append(value)


class FakeMotorRecord(SlowAdjustable):
    """Has the IOC tweak fields, like MotorRecord (see tweak_panel.has_ioc_tweak)."""

    def __init__(self):
        super().__init__(value=3.0, duration=0.0)
        self.twv = FakePV(0.25)
        self._motor = type("M", (), {"get_pv": lambda _, field: {"TWV": self.twv}.get(field, FakePV(0))})()

    def _tweak_ioc(self):
        pass


def test_the_tweak_step_comes_from_the_ioc_and_edits_go_back(app):
    item = FakeMotorRecord()
    controls, _ = display_qt._build_item_controls_qt(item, QtWidgets.QLabel("3.0"), 3.0)
    step = controls.findChildren(QtWidgets.QLineEdit)[0]
    assert pump(app, 2, until=lambda: step.text() == "0.25")
    step.setText("0.5")
    step.editingFinished.emit()
    assert pump(app, 2, until=lambda: item.twv.puts == [0.5])


def test_the_tweak_step_falls_back_to_the_adjustables_own_tweak(app):
    item = SlowAdjustable(value=3.0)
    item._tweak_instance = type("T", (), {"step_sizes": [0.05]})()
    controls, _ = display_qt._build_item_controls_qt(item, QtWidgets.QLabel("3.0"), 3.0)
    assert controls.findChildren(QtWidgets.QLineEdit)[0].text() == "0.05"


# -- the whole window --------------------------------------------------------------


class MonitoredAdjustable(SlowAdjustable):
    """Pushes updates like a motor record (add_value_callback)."""

    def __init__(self, name):
        super().__init__(value=0.0, duration=0.0)
        self.name = name
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


class HangingAdjustable(SlowAdjustable):
    """Answers only after `delay` (a channel connecting slowly)."""

    def __init__(self, name, delay):
        super().__init__(value=7.0)
        self.name = name
        self.delay = delay

    def get_current_value(self):
        time.sleep(self.delay)
        return self.value


class DeadDetector:
    name = "dead"

    def get_current_value(self):
        return None  # how a failed CA read comes back


class FakeAssembly:
    name = "fake"

    def __init__(self, items):
        self.items = items
        self.status_collection = type("SC", (), {"get_list": lambda _, selection=None: list(items)})()

    def display_collection(self):
        return list(self.items)


def test_window_opens_fast_monitors_and_reads_in_parallel(app, monkeypatch):
    from eco.widgets import live_values

    monkeypatch.setattr(live_values, "PREFETCH_DEADLINE", 0.5)
    monitored = MonitoredAdjustable("monitored")
    slow = HangingAdjustable("slow", delay=1.5)
    dead = DeadDetector()
    assembly = FakeAssembly([monitored, slow, dead])
    t0 = time.monotonic()
    gui = display_qt.DisplayQt(assembly, poll_interval=30.0, auto_start=False)
    gui._build_window()
    try:
        assert time.monotonic() - t0 < 1.5  # did not wait for the slow row
        rows = {e["item"].name: e for e in gui._entries}
        assert rows["slow"]["value_label"].text() == "…"
        # monitored: subscribed, and a push shows at once despite the 30 s poll interval
        assert pump(app, 3, until=lambda: monitored.callbacks)
        monitored.push(4.5)
        assert pump(app, 2, until=lambda: rows["monitored"]["value_label"].text() == "4.5")
        assert "updated by monitor" in rows["monitored"]["controller"].dot.toolTip()
        # the slow row gets its value and then its controls
        assert pump(app, 4, until=lambda: rows["slow"]["value_label"].text() == "7.0")
        assert not rows["slow"]["needs_controls"]
        assert any(b.text() == "▲" for b in rows["slow"]["controls"].findChildren(QtWidgets.QPushButton))
        # a channel that returns no value is shown as such
        assert pump(app, 2, until=lambda: "not connected" in rows["dead"]["controller"].dot.toolTip())
    finally:
        gui.stop()
    assert pump(app, 2, until=lambda: not monitored.callbacks)  # unsubscribed on close
