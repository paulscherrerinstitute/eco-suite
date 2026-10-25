"""Tests for eco.widgets.display_tk (the Tk fallback property grid): the Tk
thread never reads or writes hardware, monitored rows, rows still connecting
at opening. Needs a display (skipped without one)."""

import threading
import time

import pytest

tk = pytest.importorskip("tkinter")

from eco.widgets import display_tk, live_values


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def pump(root, seconds, until):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        if until():
            return True
        time.sleep(0.01)
    return until()


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
    def __init__(self, name, value=1.0, duration=0.0, delay=0.0):
        self.name = name
        self.value = value
        self.duration = duration
        self.delay = delay
        self.targets = []
        self.changer = None
        self.callbacks = {}

    def get_current_value(self):
        time.sleep(self.delay)
        return self.value

    def set_target_value(self, value):
        self.targets.append(value)
        self.changer = Changer(self.duration)
        return self.changer

    def add_value_callback(self, callback, index=None):
        index = len(self.callbacks) + 1
        self.callbacks[index] = callback
        return index

    def clear_value_callback(self, index=None):
        self.callbacks.pop(index, None)


class FakeAssembly:
    name = "fake"

    def __init__(self, items):
        self.items = items
        self.status_collection = type("SC", (), {"get_list": lambda _, selection=None: list(items)})()

    def display_collection(self):
        return list(self.items)


def find_buttons(widget, text):
    found = []
    for child in widget.winfo_children():
        try:
            if child.cget("text") == text:
                found.append(child)
        except tk.TclError:
            pass
        found += find_buttons(child, text)
    return found


def test_tk_grid_never_blocks_and_monitors(root, monkeypatch):
    monkeypatch.setattr(live_values, "PREFETCH_DEADLINE", 0.5)
    mot = Adjustable("mot", duration=5.0)
    slow = Adjustable("slow", value=7.0, delay=1.5)
    gui = display_tk.DisplayTk(FakeAssembly([mot, slow]), poll_interval=30.0, auto_start=False)
    t0 = time.monotonic()
    gui._build_window(tk.Toplevel(root))
    try:
        assert time.monotonic() - t0 < 1.5
        entries = {e["item"].name: e for e in gui._entries}
        assert entries["slow"]["value_var"].get() == "…"
        # a click returns at once; the dot shows the move; Stop works meanwhile
        up = find_buttons(entries["mot"]["control"], "▲")[0]
        t0 = time.monotonic()
        up.invoke()
        assert time.monotonic() - t0 < 0.2
        assert pump(root, 2, lambda: entries["mot"]["state"].tooltip.startswith("moving"))
        find_buttons(entries["mot"]["control"], "\U0001f6d1")[0].invoke()
        assert pump(root, 2, lambda: mot.changer.stopped)
        # monitored: a push shows at once although the poll interval is 30 s
        assert pump(root, 2, lambda: mot.callbacks)
        mot.value = 2.5
        for callback in list(mot.callbacks.values()):
            callback(pvname="mot", value=2.5)
        assert pump(root, 1, lambda: entries["mot"]["value_var"].get() == "2.5")
        # the slow row gets its value, then its controls
        assert pump(root, 4, lambda: entries["slow"]["value_var"].get() == "7.0")
        assert find_buttons(entries["slow"]["control"], "▲")
    finally:
        gui.stop()
    assert pump(root, 2, lambda: not mot.callbacks)
