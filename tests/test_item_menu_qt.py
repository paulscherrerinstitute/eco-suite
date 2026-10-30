"""Tests for eco.widgets.item_menu_qt and the Qt grid's row order (setpoint
right after the readback, tweak controls after it). Fakes: test_item_menu."""

import time

import pytest

pd = pytest.importorskip("pandas")

from test_item_menu import Adjustable, Archived, Detector, WithViews, history_frame, simple_plot, wait_for  # noqa: F401 (fixture)


# -- Qt ------------------------------------------------------------------------------


@pytest.fixture
def app():
    pytest.importorskip("qtpy")
    from qtpy import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    # closed views are deleted with deleteLater: do it now, not at
    # interpreter exit after the QApplication (a segfault there)
    app.processEvents()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def _layout_order(controls):
    layout = controls.layout()
    return [layout.itemAt(i).widget() for i in range(layout.count())]


def test_qt_setpoint_comes_right_after_the_status_dot(app):
    from qtpy import QtWidgets
    from eco.widgets import display_qt

    controls, _ = display_qt._build_item_controls_qt(Adjustable(), QtWidgets.QLabel("1.0"), 1.0)
    order = [w for w in _layout_order(controls) if w is not None]
    texts = [w.objectName() or getattr(w, "text", lambda: "")() for w in order]
    assert texts[1] == "setpoint"
    assert texts.index("setpoint") < texts.index("step") < texts.index("▲") < texts.index("🛑")


def _actions(menu):
    return {a.text(): a for a in menu.actions() if a.text()}


def test_qt_menu_offers_what_the_item_can_do(app):
    from qtpy import QtWidgets
    from eco.widgets import item_menu_qt

    menu = QtWidgets.QMenu()
    item_menu_qt.populate_menu(menu, Detector(), "det")
    actions = _actions(menu)
    assert "Live strip chart" in actions
    assert "Details…" in actions
    assert "Add indicator" in actions
    assert "History (archiver)…" not in actions
    assert "Open" not in actions  # no views

    item_menu_qt.populate_menu(menu, Archived(), "archived")
    actions = _actions(menu)
    assert "History (archiver)…" in actions
    assert "Strip plot window (archiver)" in actions

    item_menu_qt.populate_menu(menu, WithViews(), "views")
    open_menu = _actions(menu)["Open"].menu()
    assert [a.text() for a in open_menu.actions()][0] == "Viewer (default)"


def test_qt_strip_chart_records_and_stops(app):
    from eco.widgets import item_menu_qt

    item = Detector(value=1.0)
    handle = item_menu_qt.open_strip_chart(item, "det")
    chart = handle.window
    item.value = 2.0
    assert wait_for(lambda: len(chart.buffer.snapshot(60)[1]) >= 2, 3.0, app.processEvents)
    assert wait_for(lambda: chart.value_label.text() == "2.0", 3.0, app.processEvents)
    handle.stop()
    assert handle.window is None
    assert handle not in item_menu_qt._open_handles


def test_qt_history_panel_plots_in_the_background(app, simple_plot):
    from eco.widgets import item_menu_qt

    item = Archived(history_frame())
    handle = item_menu_qt.open_history(item, "archived")
    panel = handle.window
    assert wait_for(lambda: "5 rows" in panel.status.text(), 3.0, app.processEvents)
    assert item.requests[0][0] == -3600  # the default range: 1 h
    panel.range_combo.setCurrentText("10 min")
    assert wait_for(lambda: len(item.requests) == 2, 3.0, app.processEvents)
    assert item.requests[1][0] == -600
    assert wait_for(lambda: panel.status.text() == "last 10 min, 5 rows", 3.0, app.processEvents)
    handle.stop()


def test_qt_history_panel_says_when_there_is_no_data(app, simple_plot):
    from eco.widgets import item_menu_qt

    handle = item_menu_qt.open_history(Archived(None), "archived")
    assert wait_for(lambda: "no archived data" in handle.window.status.text(), 3.0, app.processEvents)
    handle.stop()


def test_qt_grid_rows_end_in_the_menu_button(app):
    from qtpy import QtWidgets
    from eco.widgets import display_qt

    class FakeAssembly:
        name = "fake"

        def __init__(self, items):
            self.items = items
            self.status_collection = type("SC", (), {"get_list": lambda _, selection=None: list(items)})()

        def display_collection(self):
            return list(self.items)

    grid = display_qt.DisplayQt(FakeAssembly([Detector(), Adjustable()]), auto_start=False)
    grid._build_window()
    try:
        buttons = [b for b in grid.window.findChildren(QtWidgets.QToolButton) if b.text() == "⋯"]
        assert len(buttons) == 2
        for entry in grid._entries:
            row = entry["row"]
            assert row.itemAt(row.count() - 1).widget().text() == "⋯"
    finally:
        grid.stop()
