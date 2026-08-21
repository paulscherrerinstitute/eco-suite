"""
A lightweight QMainWindow that hosts indicator gadgets
(eco.widgets.indicator_widgets) as freely draggable/tabbable/tileable
QDockWidgets -- the same native Qt docking used by
eco.widgets.camserver_panel_qt (drag a dock's title bar onto another to
tab them together, or drop it on an edge to tile them side by side).

Normal usage is indirect: right-click a parameter in an assembly's Qt
widget (eco.widgets.display_qt) and choose "Add indicator" -- that calls
get_default_dashboard(), creating one on first use and reusing it for
every subsequent "Add indicator" until it's closed, so gadgets from
different assemblies/devices accumulate onto one shared board. Build a
second, separate one directly (``Dashboard(title="...")``) if you want
more than one board at a time.

    from eco.widgets.dashboard_qt import Dashboard
    from eco.widgets.indicator_widgets import LEDIndicator
    board = Dashboard(title="Beamline overview", theme="dark")
    board.add_widget(LEDIndicator(item=namespace.shutter, title="Shutter"))

WORKSPACE MENU: Save/Reload/New, same JSON-file pattern as
eco.widgets.desktop_app.EcoDesktopApp -- reload reattaches each gadget's
live item by resolving its saved alias path against a namespace you
provide (see load_workspace's `namespace` argument).

"Export startup script" writes the same information as a plain, editable
Python script instead of opaque JSON -- run it (with `namespace` already
in scope) to recreate this dashboard; check it into version control,
hand it to someone else, or hand-edit it, all things the JSON file isn't
meant for.

"Edit in Qt Designer" / "Load custom panel" is the more ambitious one:
export_to_designer writes the current gadgets out as a real Qt Designer
.ui file, with each one as a genuinely promoted instance of its actual
eco.widgets.indicator_widgets class (not a mockup -- Designer really
constructs and renders them, inert/unattached, since promoted widgets are
always built as ClassName(parent) with no other arguments -- see that
module's docstring) so you can visually rearrange/resize/group them, then
launches Designer on it. load_custom_panel loads a saved .ui back
(uic.loadUi, which really instantiates the promoted classes again) and
reattaches each gadget's live item the same way load_workspace does,
via the accessibleName Qt property Designer preserves across edits. The
result is added as a single dock, so a Designer-composed arrangement
becomes one reusable named panel.
"""
import json
import logging
import subprocess
from pathlib import Path

from qtpy import QtCore, QtWidgets

logger = logging.getLogger(__name__)

_app_ref = None
_default_dashboard = None  # see get_default_dashboard

DEFAULT_WORKSPACE_FILE = Path.home() / ".eco" / "dashboard_workspace.json"
CUSTOM_PANELS_DIR = Path.home() / ".eco" / "custom_panels"


class Dashboard:
    """See the module docstring."""

    def __init__(self, title="eco dashboard", theme=None, auto_start=True):
        self.title = title
        self.theme = theme
        self.window = None
        self._docks = []
        self._placeholder = None
        if auto_start:
            self.start()

    # -- window construction -------------------------------------------------

    def _build_window(self):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            _app_ref = QtWidgets.QApplication([])
        from eco.widgets.qt_theme import apply_modern_theme

        apply_modern_theme(self.theme)

        self.window = _DashboardMainWindow(on_close=self._on_window_closing)
        self.window.setWindowTitle(self.title)
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        # so the destroyed hook below actually fires on a native close (X
        # button) -- without it, .close() just hides the window, it's
        # never truly destroyed; see eco.widgets.qt_lifecycle's module
        # docstring for the fuller why
        self.window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.window.setDockNestingEnabled(True)
        self.window.setDockOptions(
            QtWidgets.QMainWindow.AllowNestedDocks
            | QtWidgets.QMainWindow.AllowTabbedDocks
            | QtWidgets.QMainWindow.AnimatedDocks
        )

        placeholder = QtWidgets.QLabel(
            'Empty dashboard -- right-click a parameter in an assembly\'s Qt '
            'widget and choose "Add indicator" to add live gadgets here. New '
            "gadgets tile automatically; drag a gadget's title bar onto "
            "another any time to tab them together instead."
        )
        placeholder.setAlignment(QtCore.Qt.AlignCenter)
        placeholder.setWordWrap(True)
        self.window.setCentralWidget(placeholder)
        self._placeholder = placeholder  # cleared (see add_widget) once real content exists

        self._build_workspace_menu()

        self.window.destroyed.connect(lambda *a: setattr(self, "window", None))
        self.window.resize(900, 650)
        self.window.show()

    def _build_workspace_menu(self):
        menu = self.window.menuBar().addMenu("&Workspace")

        new_action = menu.addAction("New (Empty) Workspace")
        new_action.triggered.connect(self._on_new_workspace)

        reload_action = menu.addAction("Reload Last Workspace...")
        reload_action.setToolTip("Restore the layout and reattach gadgets to items in a namespace you provide")
        reload_action.triggered.connect(self._on_reload_clicked)

        save_action = menu.addAction("Save Workspace Now")
        save_action.triggered.connect(lambda: self.save_workspace())

        menu.addSeparator()

        script_action = menu.addAction("Export Startup Script...")
        script_action.setToolTip("Write a plain, editable Python script that recreates this dashboard")
        script_action.triggered.connect(self._on_export_script_clicked)

        menu.addSeparator()

        designer_action = menu.addAction("Edit Layout in Qt Designer")
        designer_action.setToolTip(
            "Export the current gadgets as a real, promoted-widget .ui file and open Qt Designer on it"
        )
        designer_action.triggered.connect(self._on_export_to_designer_clicked)

        load_panel_action = menu.addAction("Load Custom Panel (.ui)...")
        load_panel_action.triggered.connect(self._on_load_custom_panel_clicked)

    def _on_new_workspace(self):
        for dock in list(self._docks):
            self.remove_widget(dock)

    def _on_reload_clicked(self):
        namespace = self._prompt_for_namespace()
        if namespace is not None:
            self.load_workspace(namespace=namespace)

    def _on_export_script_clicked(self):
        path, ok = QtWidgets.QInputDialog.getText(
            self.window, "Export Startup Script", "Save script as:",
            text=str(Path.home() / ".eco" / "dashboard_startup.py"),
        )
        if ok and path:
            try:
                self.export_startup_script(path)
                QtWidgets.QMessageBox.information(self.window, "Exported", f"Wrote {path}")
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self.window, "Export failed", str(exc))

    def _on_export_to_designer_clicked(self):
        try:
            self.export_to_designer()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.window, "Qt Designer", str(exc))

    def _on_load_custom_panel_clicked(self):
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self.window, "Load custom panel", str(CUSTOM_PANELS_DIR), "Qt Designer files (*.ui)"
        )
        if not path:
            return
        namespace = self._prompt_for_namespace()
        try:
            self.load_custom_panel(path, namespace=namespace)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.window, "Load failed", str(exc))

    def _prompt_for_namespace(self):
        """Reattaching saved gadgets needs a live namespace object to
        resolve item paths against; there's no reliable way to discover
        "the" namespace from inside the dashboard's own process (it may
        be a separate process from any IPython session -- see
        eco.widgets.desktop_app for the one case where it's directly
        available). Prompt for a dotted import path (e.g.
        "eco.bernina.namespace") and import/resolve it."""
        text, ok = QtWidgets.QInputDialog.getText(
            self.window, "Namespace",
            "Dotted path to the namespace to reattach gadgets to\n(e.g. eco.bernina.namespace):",
        )
        if not ok or not text:
            return None
        try:
            import importlib

            module_path, _, attr = text.rpartition(".")
            module = importlib.import_module(module_path)
            return getattr(module, attr)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.window, "Could not resolve namespace", str(exc))
            return None

    #: new gadgets tile into an N-column grid by default (see add_widget) --
    #: a "dashboard" is meant to show several things at a glance, so tiling
    #: reads better out of the box than stacking everything into one tab
    #: group where only the front gadget is visible. Drag a dock's title
    #: bar onto another any time to tab them together instead, same as
    #: eco.widgets.camserver_panel_qt.
    GRID_COLUMNS = 3

    def add_widget(self, widget, title=None):
        """Add `widget` (typically an eco.widgets.indicator_widgets
        gadget, but any QWidget works) as a new dock, tiled into a
        GRID_COLUMNS-wide grid by default (see the class docstring for
        why tiling, not tabbing, is the default here). Returns the
        QDockWidget."""
        if self.window is None:
            self._build_window()
        if self._placeholder is not None:
            self.window.setCentralWidget(QtWidgets.QWidget())  # drop the "empty dashboard" hint
            self._placeholder = None

        dock_title = title or widget.windowTitle() or ""
        dock = QtWidgets.QDockWidget(dock_title, self.window)
        # Qt's own saveState()/restoreState() match docks up by objectName
        # (and warn -- "'objectName' not set for QDockWidget" -- without
        # one), so give every dock a stable, unique name; prefer the
        # widget's own accessibleName (an indicator gadget's item path, so
        # it's stable across a save/reload cycle even if you add/remove
        # gadgets in between) and fall back to an incrementing counter for
        # anything else so name collisions between same-titled docks can't
        # happen either way.
        accessible = getattr(widget, "accessibleName", lambda: "")()
        object_name = accessible or f"dock_{len(self._docks)}_{dock_title}"
        dock.setObjectName(_dock_object_name(object_name, [d.objectName() for d in self._docks]))
        dock.setWidget(widget)
        dock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetClosable
            | QtWidgets.QDockWidget.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFloatable
        )

        row, col = divmod(len(self._docks), self.GRID_COLUMNS)
        if not self._docks:
            self.window.addDockWidget(QtCore.Qt.TopDockWidgetArea, dock)
        elif col == 0:
            # first dock of a new row -- split the row above vertically
            anchor = self._docks[(row - 1) * self.GRID_COLUMNS]
            self.window.splitDockWidget(anchor, dock, QtCore.Qt.Vertical)
        else:
            # continue the current row -- split the previous dock in it
            self.window.splitDockWidget(self._docks[-1], dock, QtCore.Qt.Horizontal)

        self._docks.append(dock)
        dock.show()
        dock.raise_()
        return dock

    def remove_widget(self, dock):
        """Remove and tear down one previously add_widget()-added dock
        (stopping its poll thread first, if it's an
        eco.widgets.indicator_widgets gadget)."""
        if dock not in self._docks:
            return
        self._docks.remove(dock)
        widget = dock.widget()
        stop = getattr(widget, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                logger.exception("stopping %r failed", dock.windowTitle())
        self.window.removeDockWidget(dock)
        dock.deleteLater()

    # -- workspace persistence (JSON) --------------------------------------

    def _gadget_entries(self):
        """[{"kind", "item_path", "title", "kwargs"}, ...] for every
        currently-docked eco.widgets.indicator_widgets gadget (docks
        holding something else, e.g. a loaded custom panel, are skipped
        here -- see _custom_panel_entries).

        item_path is recomputed fresh from the gadget's live `.item`
        (with no fallback), not read from its accessibleName -- that
        property intentionally falls back to the display title when an
        item has no real alias (harmless there: it's only ever used to
        *attempt* reattachment, which just quietly fails for a bogus
        path), but here a fallback would produce a JSON/script entry
        that looks like a real, reloadable reference and isn't."""
        from eco.widgets.indicator_widgets import item_path

        entries = []
        for dock in self._docks:
            widget = dock.widget()
            kind = getattr(widget, "indicator_kind", None)
            if not kind:
                continue
            path = item_path(getattr(widget, "item", None), fallback="")
            entries.append(
                {
                    "kind": kind,
                    "item_path": path,
                    "title": dock.windowTitle(),
                    "kwargs": dict(getattr(widget, "export_kwargs", {})),
                }
            )
        return entries

    def save_workspace(self, path=None):
        """Write the dock layout plus every gadget's kind/item-path/title
        to `path` (default DEFAULT_WORKSPACE_FILE). Called automatically
        on window close."""
        if self.window is None:
            return False
        path = Path(path) if path else DEFAULT_WORKSPACE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "geometry": _qbytearray_to_str(self.window.saveGeometry()),
            "state": _qbytearray_to_str(self.window.saveState()),
            "gadgets": self._gadget_entries(),
        }
        path.write_text(json.dumps(data, indent=2))
        return True

    def load_workspace(self, path=None, namespace=None):
        """Restore a previously save_workspace-d layout, reattaching each
        gadget to a live item resolved from `namespace` (see
        eco.widgets.indicator_widgets.resolve_item_path) -- entries whose
        item can't be resolved are skipped with a warning rather than
        failing the whole reload."""
        path = Path(path) if path else DEFAULT_WORKSPACE_FILE
        if not path.exists():
            print(f"eco dashboard: no saved workspace at {path}")
            return False
        try:
            data = json.loads(path.read_text())
        except Exception:
            logger.exception("failed to read workspace file %s", path)
            return False

        if self.window is None:
            self._build_window()
        if data.get("geometry"):
            self.window.restoreGeometry(_str_to_qbytearray(data["geometry"]))
        if data.get("state"):
            self.window.restoreState(_str_to_qbytearray(data["state"]))

        from eco.widgets.indicator_widgets import create_indicator, resolve_item_path

        for entry in data.get("gadgets", []):
            item = resolve_item_path(namespace, entry["item_path"]) if namespace is not None else None
            if item is None:
                logger.warning(
                    "eco dashboard: could not resolve %r while reloading the workspace -- skipped",
                    entry["item_path"],
                )
                continue
            widget = create_indicator(entry["kind"], item, title=entry["title"], **entry.get("kwargs", {}))
            widget.setAccessibleName(entry["item_path"])
            self.add_widget(widget, title=entry["title"])
        return True

    def _autosave_workspace(self):
        self.save_workspace()

    # -- human-readable script export --------------------------------------

    def export_startup_script(self, path, namespace_var="namespace"):
        """Write a plain Python script that recreates this dashboard's
        current gadgets when run from a session where `namespace_var`
        already exists -- an editable, version-controllable alternative
        to save_workspace()'s JSON."""
        lines = [
            '"""Auto-generated by Dashboard.export_startup_script -- recreates this',
            f'dashboard. Run from a session where `{namespace_var}` already exists',
            '(the usual case once eco has started)."""',
            "from eco.widgets.dashboard_qt import make_dashboard",
            "from eco.widgets.indicator_widgets import create_indicator",
            "",
            f"board = make_dashboard(title={self.title!r})",
            "",
        ]
        for entry in self._gadget_entries():
            if not entry["item_path"]:
                continue
            kwargs_str = "".join(f", {k}={v!r}" for k, v in entry["kwargs"].items())
            item_ref = f"{namespace_var}.{entry['item_path']}"
            lines.append(
                f"board.add_widget(create_indicator({entry['kind']!r}, {item_ref}, "
                f"title={entry['title']!r}{kwargs_str}), title={entry['title']!r})"
            )
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
        return path

    # -- Qt Designer round-trip --------------------------------------------

    def export_to_designer(self, path=None):
        """Export the current gadgets as a .ui file (each a real,
        promoted eco.widgets.indicator_widgets instance -- see the module
        docstring) and open Qt Designer on it. Save in Designer, then
        load_custom_panel() the result to use the rearranged layout as
        one composite dashboard panel."""
        path = Path(path) if path else CUSTOM_PANELS_DIR / "dashboard_layout.ui"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_promoted_ui(self._gadget_entries(), path)
        try:
            subprocess.Popen(["designer", str(path)])
        except FileNotFoundError as exc:
            raise RuntimeError("Qt Designer ('designer') not found in PATH.") from exc
        return path

    def load_custom_panel(self, path, namespace=None, title=None):
        """Load a .ui file (typically one saved from Qt Designer via
        export_to_designer, possibly hand-rearranged) as a single
        composite dock, reattaching each recognized
        eco.widgets.indicator_widgets gadget inside it to a live item
        resolved from `namespace` via its accessibleName (see
        eco.widgets.indicator_widgets.resolve_item_path). Gadgets whose
        item can't be resolved (no namespace given, or a stale/renamed
        path) stay inert/unattached rather than failing the whole load."""
        from PyQt5 import uic

        from eco.widgets.indicator_widgets import _IndicatorBase, resolve_item_path

        widget = uic.loadUi(str(path))
        for child in widget.findChildren(_IndicatorBase):
            item_path_str = child.accessibleName()
            item = resolve_item_path(namespace, item_path_str) if namespace is not None else None
            if item is not None:
                child.set_item(item, title=child.windowTitle())
        return self.add_widget(widget, title=title or Path(path).stem)

    # -- lifecycle (mirrors camserver_panel_qt.CamServerPanelQt) -----------

    def run(self):
        app = QtWidgets.QApplication.instance()
        created_app = app is None
        if created_app:
            app = QtWidgets.QApplication([])
        if self.window is None:
            self._build_window()
        if created_app:
            app.exec_()

    def start(self):
        if self.window is not None:
            return
        ip = None
        try:
            from IPython import get_ipython

            ip = get_ipython()
        except Exception:
            ip = None

        if ip is None:
            self.run()
            return

        active = getattr(ip, "active_eventloop", None)
        if active in (None,):
            try:
                ip.enable_gui("qt")
            except Exception:
                pass
        elif active not in ("qt", "qt4", "qt5", "qt6"):
            print(
                f"eco dashboard: a different GUI event loop ('{active}') is already "
                "active in this IPython session, so the window can't be pumped "
                "non-blockingly alongside it. Showing it in blocking mode instead."
            )
            self.run()
            return

        self._build_window()

    def _on_window_closing(self):
        """Runs exactly once whenever this window is about to close -- by
        its own native close (X) button (via _DashboardMainWindow.
        closeEvent, which calls this as its `on_close`) or via an explicit
        stop() call below. Autosave first (needs self.window still intact
        -- it reads geometry/state off it), *then* remove_widget() every
        gadget, which is what actually stops each one's poll thread --
        without this, closing via the native close button used to leave
        every gadget quietly polling forever."""
        self._autosave_workspace()
        for dock in list(self._docks):
            self.remove_widget(dock)

    def stop(self):
        self._on_window_closing()
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None


class _DashboardMainWindow(QtWidgets.QMainWindow):
    """Plain QMainWindow, except closeEvent also runs `on_close` (see
    Dashboard._on_window_closing) -- workspace autosave plus stopping
    every gadget's poll thread, so closing via the window's own native
    close (X) button doesn't leak either. Mirrors
    eco.widgets.desktop_app._DesktopMainWindow."""

    def __init__(self, on_close, parent=None):
        super().__init__(parent)
        self._on_close = on_close

    def closeEvent(self, event):
        try:
            self._on_close()
        except Exception:
            logger.exception("workspace autosave on close failed")
        super().closeEvent(event)


def _dock_object_name(candidate, existing_names):
    """`candidate`, de-duplicated against `existing_names` by appending
    "_2", "_3", ... as needed -- QDockWidget objectNames must be unique
    within a QMainWindow for saveState()/restoreState() to reliably tell
    docks apart. Pure function, unit tested independently of Qt."""
    if candidate not in existing_names:
        return candidate
    i = 2
    while f"{candidate}_{i}" in existing_names:
        i += 1
    return f"{candidate}_{i}"


def _qbytearray_to_str(qba):
    import base64

    return base64.b64encode(bytes(qba)).decode("ascii")


def _str_to_qbytearray(s):
    import base64

    return QtCore.QByteArray(base64.b64decode(s.encode("ascii")))


def _write_promoted_ui(gadget_entries, path):
    """Write a minimal Qt Designer .ui file: one promoted-widget
    placeholder per gadget entry, stacked in a simple vertical layout,
    each carrying its item_path as its accessibleName (so
    Dashboard.load_custom_panel can reattach it) -- see the module
    docstring for why this uses real widget promotion rather than a
    static mockup."""
    import xml.etree.ElementTree as ET

    from eco.widgets.indicator_widgets import _FACTORY_BY_KIND, _sanitize_object_name

    ui = ET.Element("ui", version="4.0")
    ET.SubElement(ui, "class").text = "DashboardPanel"
    root_widget = ET.SubElement(ui, "widget", attrib={"class": "QWidget", "name": "DashboardPanel"})
    layout = ET.SubElement(root_widget, "layout", attrib={"class": "QVBoxLayout", "name": "verticalLayout"})

    classes_used = set()
    for entry in gadget_entries:
        kind = entry["kind"]
        factory = _FACTORY_BY_KIND.get(kind)
        if factory is None:
            continue
        class_name = factory.__name__
        classes_used.add(class_name)
        object_name = _sanitize_object_name(entry["item_path"] or entry["title"])

        item_el = ET.SubElement(layout, "item")
        widget_el = ET.SubElement(
            item_el, "widget", attrib={"class": class_name, "name": object_name}
        )
        for prop_name, value in (
            ("accessibleName", entry.get("item_path", "")),
            ("windowTitle", entry.get("title", "")),
        ):
            prop_el = ET.SubElement(widget_el, "property", attrib={"name": prop_name})
            ET.SubElement(prop_el, "string").text = value

    custom_widgets = ET.SubElement(ui, "customwidgets")
    for class_name in sorted(classes_used):
        cw = ET.SubElement(custom_widgets, "customwidget")
        ET.SubElement(cw, "class").text = class_name
        ET.SubElement(cw, "extends").text = "QWidget"
        ET.SubElement(cw, "header").text = "eco.widgets.indicator_widgets"

    ET.SubElement(ui, "resources")
    ET.SubElement(ui, "connections")

    tree = ET.ElementTree(ui)
    ET.indent(tree, space=" ")
    tree.write(path, encoding="UTF-8", xml_declaration=True)


def get_default_dashboard():
    """The shared dashboard eco.widgets.indicator_widgets.attach_indicator_menu's
    "Add indicator" menu action targets by default -- created on first
    use, reused for subsequent calls until its window is closed, at
    which point the next call creates a fresh one."""
    global _default_dashboard
    if _default_dashboard is None or _default_dashboard.window is None:
        _default_dashboard = Dashboard(title="eco dashboard", auto_start=True)
    return _default_dashboard


def make_dashboard(title="eco dashboard", theme=None, auto_start=True):
    """Convenience factory, mirrors the rest of eco's Qt widget factories."""
    return Dashboard(title=title, theme=theme, auto_start=auto_start)
