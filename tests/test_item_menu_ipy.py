"""Tests for eco.widgets.item_menu_ipy and the ipywidgets grid's row order.
Fakes: test_item_menu. Separate from the Qt tests: run together in one
process they hit the Qt+pytest+offscreen exit segfault (see CLAUDE.md)."""

import time

import pytest

pd = pytest.importorskip("pandas")

from test_item_menu import Adjustable, Archived, Detector, history_frame, simple_plot, wait_for  # noqa: F401 (fixture)


# -- ipywidgets -----------------------------------------------------------------------


def test_ipy_setpoint_comes_right_after_the_status_dot():
    widgets = pytest.importorskip("ipywidgets")
    from eco.widgets import display_widget

    box, state = display_widget._build_item_controls_widget(Adjustable(), widgets.Label("1.0"), 1.0)
    assert box.children[1] is state["input_widget"]
    assert isinstance(box.children[2], widgets.FloatText)  # the step
    assert box.children[3].description == "▲"


def _choose(menu, key):
    menu.value = key
    assert menu.value == ""  # reset at once, ready for the next choice


def test_ipy_menu_opens_panels_below_the_row_and_closing_stops_them():
    pytest.importorskip("ipywidgets")
    from eco.widgets import item_menu_ipy

    panels = item_menu_ipy.ItemPanels()
    item = Detector(value=1.0)
    menu = item_menu_ipy.make_menu(item, "det", panels)
    keys = [key for _, key in menu.options]
    assert "strip" in keys and "details" in keys and "history" not in keys
    assert panels.layout.display == "none"

    _choose(menu, "strip")
    assert len(panels.children) == 1 and panels.layout.display is None
    chart = panels.children[0]._eco_body
    item.value = 3.0
    assert wait_for(lambda: chart.buffer.last_text == "3.0")
    chart.redraw()
    assert chart.image.value[:4] == b"\x89PNG"

    _choose(menu, "details")
    details = panels.children[1]._eco_body
    assert wait_for(lambda: "det" in details.value and "reading" not in details.value)

    panels.remove(panels.children[0])
    assert chart._stop.is_set()
    panels.stop()
    assert panels.children == () and panels.layout.display == "none"


def test_ipy_history_panel(simple_plot):
    pytest.importorskip("ipywidgets")
    from eco.widgets import item_menu_ipy

    item = Archived(history_frame())
    panel = item_menu_ipy.HistoryPanelIpy(item, title="archived")
    assert wait_for(lambda: "5 rows" in panel.status.value)
    assert panel.image.value[:4] == b"\x89PNG"
    panel.range_dd.value = "7 days"
    assert wait_for(lambda: len(item.requests) == 2)
    assert item.requests[1][0] == -7 * 86400
    # let that render finish: exiting while a thread is inside matplotlib segfaults
    assert wait_for(lambda: panel.status.value == "last 7 days, 5 rows")


def test_ipy_menu_shows_a_failed_view_instead_of_raising():
    pytest.importorskip("ipywidgets")
    from eco.widgets import item_menu_ipy

    class Broken(Adjustable):
        def _widget_broken(self):
            raise RuntimeError("no hardware")

    panels = item_menu_ipy.ItemPanels()
    menu = item_menu_ipy.make_menu(Broken(), "broken", panels)
    _choose(menu, "open:_widget_broken")
    assert "no hardware" in panels.children[0]._eco_body.value


def test_ipy_grid_rows_carry_the_menu():
    widgets = pytest.importorskip("ipywidgets")
    from eco.widgets import display_widget, item_menu_ipy

    class FakeAssembly:
        name = "fake"

        def __init__(self, items):
            self.items = items
            self.status_collection = type("SC", (), {"get_list": lambda _, selection=None: list(items)})()

        def display_collection(self):
            return list(self.items)

    grid = display_widget.make_assembly_widget(FakeAssembly([Detector(), Adjustable()]), poll_interval=30.0)
    try:
        rows = [w for w in grid.children if isinstance(w, widgets.HBox) and len(w.children) == 5]
        assert len(rows) == 2
        for row in rows:
            assert row.children[3].options[0] == ("⋯", "")
            assert isinstance(row.children[4], item_menu_ipy.ItemPanels)
    finally:
        grid.stop()
