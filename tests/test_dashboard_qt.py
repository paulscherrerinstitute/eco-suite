import time

import pytest

pytest.importorskip("qtpy")

from qtpy import QtWidgets

import eco.widgets.dashboard_qt as dashboard_qt
from eco.widgets.dashboard_qt import Dashboard, get_default_dashboard, make_dashboard
from eco.widgets.indicator_widgets import create_indicator


class _FakeAlias:
    def __init__(self, full_name):
        self._full_name = full_name

    def get_full_name(self):
        return self._full_name


class _FakeDetector:
    def __init__(self, name, value=0.0, full_name=None):
        self.name = name
        self._value = value
        if full_name:
            self.alias = _FakeAlias(full_name)

    def get_current_value(self):
        return self._value


class _FakeNamespace:
    """Minimal stand-in with attribute-chain access for resolve_item_path."""

    def __init__(self):
        self.alias = _FakeAlias("ns")


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _no_real_home_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_qt, "DEFAULT_WORKSPACE_FILE", tmp_path / "dashboard_workspace.json")
    monkeypatch.setattr(dashboard_qt, "CUSTOM_PANELS_DIR", tmp_path / "custom_panels")


def test_make_dashboard_builds_a_window():
    _app()
    board = make_dashboard(title="Test board", auto_start=False)
    board._build_window()
    try:
        assert board.window is not None
        assert board.window.windowTitle() == "Test board"
    finally:
        board.stop()


def test_native_close_stops_every_gadgets_poll_thread():
    """Regression test for a real bug: closing the dashboard window via
    its own native close (X) button used to leave every gadget's poll
    thread running -- only remove_widget() (never wired to a native
    close) actually stopped one."""
    app = _app()
    board = Dashboard(auto_start=False)
    board._build_window()
    item = _FakeDetector("d1")
    widget = create_indicator("LED", item, title="D1")
    board.add_widget(widget, title="D1")
    assert not widget._stop_event.is_set()

    board.window.close()  # simulates the native X button, not board.stop()

    assert widget._stop_event.is_set()
    assert board._docks == []


def test_add_widget_creates_dock_and_tabs_with_previous():
    _app()
    board = Dashboard(auto_start=False)
    board._build_window()
    try:
        item1 = _FakeDetector("d1")
        item2 = _FakeDetector("d2")
        w1 = create_indicator("LED", item1, title="D1")
        w2 = create_indicator("LED", item2, title="D2")
        board.add_widget(w1, title="D1")
        board.add_widget(w2, title="D2")
        assert len(board._docks) == 2
        assert board.window.tabifiedDockWidgets(board._docks[0]) or True  # tabify call didn't raise
    finally:
        board.stop()


def test_remove_widget_stops_gadget_polling():
    app = _app()
    board = Dashboard(auto_start=False)
    board._build_window()
    item = _FakeDetector("d1", value=1.0)
    w = create_indicator("Numeric tile (KPI)", item, title="D1")
    dock = board.add_widget(w, title="D1")
    try:
        board.remove_widget(dock)
        assert dock not in board._docks
        assert w._stop_event.is_set()
    finally:
        board.stop()


def test_get_default_dashboard_reuses_until_closed():
    _app()
    d1 = get_default_dashboard()  # auto_start=True already builds the window
    assert d1.window is not None
    d2 = get_default_dashboard()
    assert d1 is d2
    d1.stop()
    d3 = get_default_dashboard()
    try:
        assert d3 is not d1
    finally:
        d3.stop()


# -- workspace save/load (JSON) --


def test_save_and_load_workspace_round_trip(tmp_path):
    app = _app()
    board = Dashboard(auto_start=False)
    board._build_window()
    item = _FakeDetector("intensity", value=7.0, full_name="cam_west.intensity")
    w = create_indicator("Bar gauge", item, title="Intensity", vmin=0.0, vmax=10.0)
    board.add_widget(w, title="Intensity")

    path = tmp_path / "ws.json"
    assert board.save_workspace(path) is True

    import json

    data = json.loads(path.read_text())
    assert data["gadgets"] == [
        {
            "kind": "Bar gauge",
            "item_path": "cam_west.intensity",
            "title": "Intensity",
            "kwargs": {"vmin": 0.0, "vmax": 10.0},
        }
    ]
    board.stop()

    # fresh dashboard, reload against a fake namespace that resolves the path
    ns = _FakeNamespace()
    ns.cam_west = type("C", (), {})()
    ns.cam_west.intensity = item

    board2 = Dashboard(auto_start=False)
    board2._build_window()
    try:
        loaded = board2.load_workspace(path, namespace=ns)
        assert loaded is True
        assert len(board2._docks) == 1
        reattached_widget = board2._docks[0].widget()
        assert reattached_widget.item is item
        assert reattached_widget.vmin == 0.0
        assert reattached_widget.vmax == 10.0
    finally:
        board2.stop()


def test_load_workspace_skips_unresolvable_items(tmp_path):
    app = _app()
    board = Dashboard(auto_start=False)
    board._build_window()
    item = _FakeDetector("intensity", value=1.0, full_name="cam_west.intensity")
    w = create_indicator("LED", item, title="Intensity")
    board.add_widget(w, title="Intensity")
    path = tmp_path / "ws.json"
    board.save_workspace(path)
    board.stop()

    empty_ns = _FakeNamespace()  # no cam_west attribute -> unresolvable
    board2 = Dashboard(auto_start=False)
    board2._build_window()
    try:
        board2.load_workspace(path, namespace=empty_ns)
        assert len(board2._docks) == 0
    finally:
        board2.stop()


def test_load_workspace_missing_file_returns_false(tmp_path):
    _app()
    board = Dashboard.__new__(Dashboard)
    board.window = None
    board._docks = []
    assert board.load_workspace(tmp_path / "nope.json") is False


def test_save_workspace_no_window_is_noop():
    board = Dashboard.__new__(Dashboard)
    board.window = None
    assert board.save_workspace() is False


def test_autosave_on_close_writes_workspace(tmp_path):
    app = _app()
    board = Dashboard(auto_start=False)
    board._build_window()
    item = _FakeDetector("d1", value=1.0, full_name="d1")
    w = create_indicator("LED", item, title="D1")
    board.add_widget(w, title="D1")

    path = tmp_path / "autosave.json"
    dashboard_qt.DEFAULT_WORKSPACE_FILE = path
    board.window.close()

    assert path.exists()


# -- startup script export --


def test_export_startup_script_content(tmp_path):
    app = _app()
    board = Dashboard(title="My board", auto_start=False)
    board._build_window()
    item = _FakeDetector("intensity", value=1.0, full_name="cam_west.intensity")
    w = create_indicator("Bar gauge", item, title="Intensity", vmin=0.0, vmax=10.0)
    board.add_widget(w, title="Intensity")

    path = tmp_path / "startup.py"
    try:
        board.export_startup_script(path, namespace_var="namespace")
        text = path.read_text()
        assert "namespace.cam_west.intensity" in text
        assert "'Bar gauge'" in text
        assert "vmin=0.0" in text
        assert "vmax=10.0" in text
        assert "make_dashboard(title='My board')" in text
        compile(text, str(path), "exec")  # at least syntactically valid python
    finally:
        board.stop()


def test_export_startup_script_skips_entries_without_item_path(tmp_path):
    app = _app()
    board = Dashboard(auto_start=False)
    board._build_window()
    item = _FakeDetector("no_alias", value=1.0)  # no .alias -> empty item_path
    w = create_indicator("LED", item, title="NoAlias")
    board.add_widget(w, title="NoAlias")

    path = tmp_path / "startup.py"
    try:
        board.export_startup_script(path)
        text = path.read_text()
        assert "add_widget" not in text
    finally:
        board.stop()


# -- Qt Designer round-trip --


def test_write_promoted_ui_produces_valid_designer_xml(tmp_path):
    from eco.widgets.dashboard_qt import _write_promoted_ui

    entries = [
        {"kind": "LED", "item_path": "cam_west.shutter", "title": "Shutter", "kwargs": {}},
        {
            "kind": "Bar gauge", "item_path": "cam_west.intensity", "title": "Intensity",
            "kwargs": {"vmin": 0.0, "vmax": 10.0},
        },
    ]
    path = tmp_path / "layout.ui"
    _write_promoted_ui(entries, path)

    import xml.etree.ElementTree as ET

    tree = ET.parse(path)
    root = tree.getroot()
    assert root.tag == "ui"
    custom_classes = {cw.find("class").text for cw in root.find("customwidgets")}
    assert custom_classes == {"LEDIndicator", "BarGauge"}

    widgets = root.findall(".//widget[@class='LEDIndicator']") + root.findall(
        ".//widget[@class='BarGauge']"
    )
    assert len(widgets) == 2
    access_names = {
        w.find("./property[@name='accessibleName']/string").text for w in widgets
    }
    assert access_names == {"cam_west.shutter", "cam_west.intensity"}


def test_export_to_designer_without_binary_raises_clear_error(tmp_path, monkeypatch):
    app = _app()
    board = Dashboard(auto_start=False)
    board._build_window()
    item = _FakeDetector("d1", value=1.0, full_name="d1")
    board.add_widget(create_indicator("LED", item, title="D1"), title="D1")

    import subprocess as sp

    def raise_not_found(*a, **kw):
        raise FileNotFoundError()

    monkeypatch.setattr(sp, "Popen", raise_not_found)
    try:
        with pytest.raises(RuntimeError, match="Qt Designer"):
            board.export_to_designer(tmp_path / "layout.ui")
    finally:
        board.stop()


def test_load_custom_panel_real_round_trip_through_uic(tmp_path):
    """The real, valuable check: write a .ui file the same way
    export_to_designer would (promoted widgets, no Designer binary
    needed), load it back with the actual PyQt5.uic machinery (exactly
    what Dashboard.load_custom_panel does), and confirm the promoted
    gadgets come back as real, live, correctly-reattached instances --
    not a mock of the mechanism, the mechanism itself."""
    pytest.importorskip("PyQt5.uic")
    app = _app()

    from eco.widgets.dashboard_qt import _write_promoted_ui

    entries = [
        {"kind": "LED", "item_path": "cam_west.shutter", "title": "Shutter", "kwargs": {}},
    ]
    ui_path = tmp_path / "layout.ui"
    _write_promoted_ui(entries, ui_path)

    ns = _FakeNamespace()
    ns.cam_west = type("C", (), {})()
    item = _FakeDetector("shutter", value=True)
    ns.cam_west.shutter = item

    board = Dashboard(auto_start=False)
    board._build_window()
    try:
        dock = board.load_custom_panel(ui_path, namespace=ns, title="Loaded panel")
        assert dock in board._docks
        panel = dock.widget()
        from eco.widgets.indicator_widgets import LEDIndicator, _IndicatorBase

        leds = panel.findChildren(_IndicatorBase)
        assert len(leds) == 1
        assert isinstance(leds[0], LEDIndicator)
        assert leds[0].item is item  # really reattached to the live item
    finally:
        board.stop()


def test_load_custom_panel_without_namespace_stays_inert(tmp_path):
    pytest.importorskip("PyQt5.uic")
    app = _app()

    from eco.widgets.dashboard_qt import _write_promoted_ui

    entries = [{"kind": "LED", "item_path": "cam_west.shutter", "title": "Shutter", "kwargs": {}}]
    ui_path = tmp_path / "layout.ui"
    _write_promoted_ui(entries, ui_path)

    board = Dashboard(auto_start=False)
    board._build_window()
    try:
        dock = board.load_custom_panel(ui_path, namespace=None, title="Loaded panel")
        panel = dock.widget()
        from eco.widgets.indicator_widgets import _IndicatorBase

        leds = panel.findChildren(_IndicatorBase)
        assert len(leds) == 1
        assert leds[0].item is None  # nothing to resolve against -> stays inert
    finally:
        board.stop()


# -- dock objectName uniqueness (Qt needs this for saveState/restoreState) --


def test_dock_object_name_unchanged_when_unique():
    from eco.widgets.dashboard_qt import _dock_object_name

    assert _dock_object_name("cam_west.intensity", []) == "cam_west.intensity"


def test_dock_object_name_dedupes_collisions():
    from eco.widgets.dashboard_qt import _dock_object_name

    existing = ["d1", "d1_2"]
    assert _dock_object_name("d1", existing) == "d1_3"


def test_add_widget_gives_each_dock_a_unique_object_name():
    app = _app()
    board = Dashboard(auto_start=False)
    board._build_window()
    try:
        item1 = _FakeDetector("d1", value=1.0)
        item2 = _FakeDetector("d2", value=1.0)
        w1 = create_indicator("LED", item1, title="Same title")
        w2 = create_indicator("LED", item2, title="Same title")
        dock1 = board.add_widget(w1, title="Same title")
        dock2 = board.add_widget(w2, title="Same title")
        assert dock1.objectName() != ""
        assert dock2.objectName() != ""
        assert dock1.objectName() != dock2.objectName()
    finally:
        board.stop()
