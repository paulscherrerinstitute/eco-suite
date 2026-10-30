"""Tests for eco.widgets.item_menu, the toolkit-independent part of the
per-item "⋯" menu of the property grids; the fakes here are shared with
test_item_menu_qt / test_item_menu_ipy."""

import enum
import time

import pytest

pd = pytest.importorskip("pandas")

from eco.widgets import item_menu


def wait_for(condition, seconds=3.0, pump=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if pump is not None:
            pump()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class Detector:
    def __init__(self, name="det", value=1.0):
        self.name = name
        self.value = value

    def get_current_value(self):
        return self.value


class Adjustable(Detector):
    def __init__(self, name="adj", value=1.0):
        super().__init__(name, value)
        self.targets = []

    def set_target_value(self, value):
        self.targets.append(value)
        self.value = value


class Archived(Detector):
    """Has the archiver mixin's methods (eco.epics_utils.get_from_archive)."""

    def __init__(self, data=None):
        super().__init__("archived", 2.0)
        self.data = data
        self.requests = []

    def get_archiver_time_range(self, start=None, plot=True, **kwargs):
        self.requests.append((start, plot, kwargs))
        return self.data

    def strip_plot(self):
        pass


class WithViews(Adjustable):
    _default_widget = "_widget_viewer"

    def _widget_assembly(self):
        return "grid"

    def _widget_viewer(self):
        return "viewer"

    def _widget_tweak(self, backend=None):
        return ("tweak", backend)


def history_frame():
    index = pd.date_range("2026-10-30 08:00", periods=5, freq="min")
    return pd.DataFrame({"archived (SARES20:X)": [1.0, 2.0, 2.0, 3.0, 1.5]}, index=index)


@pytest.fixture
def simple_plot(monkeypatch):
    """plot_history without eco.dbase.archiver (needs datahub)."""

    def plot(ax, data):
        for column in data.columns:
            ax.step(data.index.to_pydatetime(), data[column], where="post")
        return True

    monkeypatch.setattr(item_menu, "plot_history", plot)


# -- toolkit-independent ---------------------------------------------------------


def test_widget_methods_lists_the_default_view_first():
    views = item_menu.widget_methods(WithViews())
    assert views[0] == ("Viewer (default)", "_widget_viewer")
    assert ("Property grid", "_widget_assembly") in views
    assert ("Tweak panel", "_widget_tweak") in views
    assert item_menu.widget_methods(Detector()) == []


def test_call_widget_method_passes_the_backend_where_taken():
    item = WithViews()
    assert item_menu.call_widget_method(item, "_widget_tweak", "qt") == ("tweak", "qt")
    assert item_menu.call_widget_method(item, "_widget_viewer", "qt") == "viewer"


def test_capabilities_are_looked_up_on_the_class():
    assert item_menu.is_readable(Detector())
    assert not item_menu.has_history(Detector())
    assert item_menu.has_history(Archived())
    assert item_menu.has_strip_plot(Archived())


def test_numeric_and_series_buffer():
    class State(enum.Enum):
        OPEN = 1
        CLOSED = 0

    assert item_menu.numeric(True) == 1.0
    assert item_menu.numeric(State.OPEN) == 1.0
    assert item_menu.numeric("B", ["A", "B"]) == 1.0
    assert item_menu.numeric("text") is None

    buffer = item_menu.SeriesBuffer(Detector())
    now = time.time()
    buffer.add(1.0, t=now - 100)
    buffer.add(2.0, t=now - 10)
    buffer.add(3.0, t=now - 1)
    times, values = buffer.snapshot(30)
    # the sample from before the window is kept, so the line starts at its edge
    assert values == [1.0, 2.0, 3.0]
    assert not buffer.add("text")
    assert buffer.last_text == "text"


def test_describe_item():
    rows = dict(item_menu.describe_item(Adjustable(value=4.0)))
    assert rows["name"] == "adj"
    assert rows["kind"] == "Adjustable"
    assert rows["value"] == "4.0"
    text = item_menu.details_text([("channels", "A:1\nA:2"), ("x", "y")])
    assert text.splitlines() == ["channels  A:1", "          A:2", "x         y"]


def test_fetch_history_asks_the_archiver_without_plotting():
    item = Archived(history_frame())
    assert item_menu.fetch_history(item, 3600) is item.data
    start, plot, kwargs = item.requests[0]
    assert start == -3600 and plot is False and kwargs["verbose"] is False
