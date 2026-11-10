"""The "build a simple GUI in eco desktop" workflow of docs/simple_gui.md,
step by step, against a real lazy Namespace with simulated devices
(eco.web.demo) -- open a device, add gadgets from a grid row's "⋯" menu,
close everything else, resize, save a startup script, start again from it;
and the Qt Designer round trip. No hardware, no console kernel."""
import itertools
import json
import re
import sys
import time
import types

import pytest

QtWidgets = pytest.importorskip("qtpy.QtWidgets")
from qtpy import QtCore  # noqa: E402

from eco.widgets import dashboard_qt, desktop_app  # noqa: E402
from eco.widgets.desktop_app import EcoDesktopApp, sorted_namespace_entries  # noqa: E402
from eco.widgets.indicator_widgets import Dial, NumericTile, _IndicatorBase  # noqa: E402

_scope_counter = itertools.count()


@pytest.fixture(autouse=True)
def _no_real_home_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_app, "DEFAULT_WORKSPACE_FILE", tmp_path / "desktop_workspace.json")
    monkeypatch.setattr(dashboard_qt, "CUSTOM_PANELS_DIR", tmp_path / "custom_panels")
    desktop_app._live_desktop_apps.clear()
    yield
    desktop_app._live_desktop_apps.clear()


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _pump(until=None, timeout=10.0, duration=0.2):
    app = _app()
    end = time.time() + (timeout if until else duration)
    while time.time() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until is None


def _demo_namespace():
    """A real eco Namespace (lazy components, background init, alias
    paths) holding simulated devices -- what a beamline scope is, minus
    the beamline."""
    from eco.utilities.config import Namespace

    module_name = f"demo_scope_{next(_scope_counter)}"
    sys.modules[module_name] = types.ModuleType(module_name)
    ns = Namespace(name="demo", root_module=module_name)
    for cls, name in (("Mono", "mono"), ("Slits", "slits"), ("Cryostat", "cryo")):
        ns.append_obj(cls, module_name="eco.web.demo", name=name, lazy=True)
    return ns


def _desktop(ns, **kwargs):
    gui = EcoDesktopApp(namespace=ns, with_console=False, scope="demo", auto_start=False, **kwargs)
    gui._build_window()
    gui.window.resize(1000, 700)
    return gui


def _dock(gui, title):
    return next(d for d in gui._widget_docks if d.windowTitle() == title)


def _row_menu(grid, item_name):
    """The "⋯" menu at the end of `item_name`'s row in a property grid."""
    for button in grid.findChildren(QtWidgets.QToolButton):
        if button.text() != "⋯":
            continue
        menu = button.menu()
        menu.aboutToShow.emit()  # fills it, as opening it does
        for action in menu.actions():
            if action.text().startswith("Copy name") and action.text().rstrip(")").endswith("." + item_name):
                return menu
    raise AssertionError(f"no ⋯ menu for {item_name}")


def _trigger(menu, *path):
    for part in path:
        action = next(a for a in menu.actions() if a.text() == part)
        if action.menu() is not None and part != path[-1]:
            menu = action.menu()
    action.trigger()


def _script_workspace(sh_path):
    text = sh_path.read_text()
    return json.loads(text.split("<<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0])


def _build_mono_panel(gui):
    """Steps 1-4 of the guide: open mono, add a dial for energy and a
    tile for pitch_rbv, close the grid and the Namespace panel."""
    gui._launcher.open_by_name("mono")
    assert _pump(lambda: gui._widget_docks, timeout=30)
    grid = _dock(gui, "mono").widget()
    _trigger(_row_menu(grid, "energy"), "Add indicator", "Dial / knob")
    _trigger(_row_menu(grid, "pitch_rbv"), "Add indicator", "Numeric tile (KPI)")
    _pump()
    _dock(gui, "mono").close()
    gui._launcher_dock.close()
    _pump()


def test_guide_gadgets_dock_in_the_desktop_and_come_back_from_the_startup_script(tmp_path):
    _app()
    gui = _desktop(_demo_namespace())
    try:
        _build_mono_panel(gui)
        # only the two gadgets are left, the closed grid is no longer "open"
        assert [d.windowTitle() for d in gui._widget_docks] == [
            "demo.mono.energy (Dial / knob)",
            "demo.mono.pitch_rbv (Numeric tile (KPI))",
        ]
        assert gui._opened_names == []
        # step 5: resize
        gui.window.resize(520, 300)
        gui.window.resizeDocks(gui._widget_docks, [300, 200], QtCore.Qt.Horizontal)
        _pump()
        before = {d.objectName(): d.geometry().getRect() for d in gui._widget_docks}
        # step 6: save the startup script
        sh_path = gui.save_startup_script(tmp_path / "mono_panel.sh")
    finally:
        gui.stop()

    script = sh_path.read_text()
    assert "-m eco_cli desktop -s demo -l --no-console --no-namespace-panel" in script
    data = _script_workspace(sh_path)
    assert data["opened_names"] == []
    assert [(g["kind"], g["item_path"], g["kwargs"]) for g in data["gadgets"]] == [
        ("Dial / knob", "demo.mono.energy", {"vmin": 4.0, "vmax": 20.0}),
        ("Numeric tile (KPI)", "demo.mono.pitch_rbv", {}),
    ]

    # step 7: start again, the way the script does -- fresh lazy namespace,
    # no Namespace panel, workspace from the script
    ns = _demo_namespace()
    gui2 = _desktop(ns, with_namespace_panel=False)
    try:
        gui2.load_workspace_data(data)
        assert _pump(lambda: len(gui2._gadgets) == 2, timeout=30)
        _pump(duration=0.3)
        after = {d.objectName(): d.geometry().getRect() for d in gui2._widget_docks}
        assert after == before  # same arrangement and sizes
        assert gui2.window.size() == QtCore.QSize(520, 300)
        # efficient: only the device the gadgets need was built, and its
        # full grid was not opened
        assert dict(sorted_namespace_entries(ns)) == {"cryo": "lazy", "mono": "initialized", "slits": "lazy"}
        assert gui2._opened_names == []
        dial = gui2._gadgets[0]["widget"]
        assert isinstance(dial, Dial)
        assert (dial.vmin, dial.vmax, dial.settable) == (4.0, 20.0, True)
        assert _pump(lambda: dial._value_label.text() == "7", timeout=5)  # live
    finally:
        gui2.stop()


def test_guide_designer_round_trip(tmp_path):
    _app()
    gui = _desktop(_demo_namespace())
    try:
        _build_mono_panel(gui)
        launched = []
        ui_path = tmp_path / "mono_panel.ui"
        import unittest.mock as mock

        with mock.patch.object(dashboard_qt, "open_in_designer", launched.append):
            gui.export_gadgets_to_designer(ui_path)
        assert launched == [ui_path]
        # what Form -> Lay Out in a Grid and saving in Designer does to the
        # file: the boxes go into grid cells, side by side
        text = ui_path.read_text().replace('class="QVBoxLayout" name="verticalLayout"', 'class="QGridLayout" name="gridLayout"')
        cells = iter(['<item row="0" column="0">', '<item row="0" column="1">'])
        ui_path.write_text(re.sub(r"<item>", lambda m: next(cells), text))

        dock = gui.load_ui_panel(ui_path)
        _pump(duration=0.3)
        for record in list(gui._gadgets):  # the separate gadgets aren't needed any more
            record["dock"].close()
        _pump()
        assert [d.windowTitle() for d in gui._widget_docks] == ["mono_panel"]
        gadgets = dock.widget().findChildren(_IndicatorBase)
        assert [type(g) for g in gadgets] == [Dial, NumericTile]
        assert all(g.item is not None for g in gadgets)
        assert (gadgets[0].vmin, gadgets[0].vmax) == (4.0, 20.0)  # range survives the .ui file
        layout = dock.widget().layout()
        assert isinstance(layout, QtWidgets.QGridLayout)
        assert layout.getItemPosition(layout.indexOf(gadgets[1]))[:2] == (0, 1)  # same cell
        sh_path = gui.save_startup_script(tmp_path / "panel.sh")
    finally:
        gui.stop()

    data = _script_workspace(sh_path)
    assert data["gadgets"] == []
    assert data["ui_panels"] == [str(ui_path.resolve())]

    ns = _demo_namespace()
    gui2 = _desktop(ns, with_namespace_panel=False)
    try:
        gui2.load_workspace_data(data)
        dock = _dock(gui2, "mono_panel")

        def attached():
            gadgets = dock.widget().findChildren(_IndicatorBase)
            return len(gadgets) == 2 and all(g.item is not None for g in gadgets)

        assert _pump(attached, timeout=30)
        assert dict(sorted_namespace_entries(ns))["slits"] == "lazy"
    finally:
        gui2.stop()


def test_export_gadgets_to_designer_without_gadgets_says_how_to_add_them(tmp_path):
    _app()
    gui = _desktop(_demo_namespace())
    try:
        with pytest.raises(RuntimeError, match="Add indicator"):
            gui.export_gadgets_to_designer(tmp_path / "x.ui", launch=False)
    finally:
        gui.stop()


def test_gadget_for_an_unknown_path_is_skipped_not_fatal():
    _app()
    gui = _desktop(_demo_namespace())
    try:
        gui.load_workspace_data(
            {"gadgets": [{"kind": "LED", "item_path": "demo.nothing.here", "title": "x", "kwargs": {}}]}
        )
        _pump()
        assert gui._gadgets == []
    finally:
        gui.stop()


def test_guide_sub_device_grid_and_strip_chart_are_saved_too(tmp_path):
    """Opening a sub-device's grid from a grid row, or a strip chart from
    a row's "⋯" menu, docks it -- and the startup script reopens it."""
    _app()
    from eco.widgets import item_menu_qt

    ns = _demo_namespace()
    gui = _desktop(ns)
    try:
        gui._launcher.open_by_name("mono")
        assert _pump(lambda: gui._widget_docks, timeout=30)
        grid = _dock(gui, "mono").widget()
        mono = desktop_app._resolve_namespace_item(ns, "mono")
        # what a grid row of a sub-device does (DisplayQt._open_child_window)
        from eco.widgets.display_qt import DisplayQt

        child = DisplayQt(mono, auto_start=False)
        child._build_window()
        gui.host_widget("demo.mono (copy)", child, item=mono, view="grid")
        _trigger(_row_menu(grid, "energy"), "Live strip chart")
        _pump()
        _dock(gui, "mono").close()
        titles = sorted(d.windowTitle() for d in gui._widget_docks)
        assert titles == ["demo.mono (copy)", "strip chart: demo.mono.energy"]
        data = gui._workspace_data()
    finally:
        gui.stop()
    assert sorted(v["view"] for v in data["views"]) == ["grid", "strip_chart"]
    assert data["opened_names"] == []

    gui2 = _desktop(_demo_namespace(), with_namespace_panel=False)
    try:
        gui2.load_workspace_data(data)
        assert _pump(lambda: len(gui2._widget_docks) == 2, timeout=30)
        assert sorted(v["entry"]["view"] for v in gui2._views) == ["grid", "strip_chart"]
    finally:
        gui2.stop()
