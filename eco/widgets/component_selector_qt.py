"""Qt frontend for eco.widgets.component_selector (scripts/desktop use).

Uses qtpy so it works with whichever Qt binding is installed (PyQt5/PyQt6/
PySide2/PySide6) -- same convention as eco.widgets.display_qt.

Usage (embedded, e.g. inside a larger Qt window):
    from eco.widgets.component_selector_qt import ComponentSelectorQt
    sel = ComponentSelectorQt(eco.bernina.bernina)
    sel.on_select(lambda name, obj: print("picked", name, obj))
    some_layout.addWidget(sel)

Usage (standalone window):
    from eco.widgets.component_selector_qt import make_component_selector_qt_window
    gui = make_component_selector_qt_window(eco.bernina.bernina)
    ...
    name, obj = gui.get_selected()
    gui.stop()
"""
import enum
from typing import Any, Callable, List, Optional

from qtpy import QtCore, QtWidgets

from eco.widgets.component_selector import (
    ComponentBookmarks,
    ComponentNode,
    KIND_ICONS,
    RecentComponents,
    build_tree,
    classify,
    filter_root,
    resolve_path,
)

_OBJ_ROLE = QtCore.Qt.UserRole
_PATH_ROLE = QtCore.Qt.UserRole + 1
_KIND_ROLE = QtCore.Qt.UserRole + 2

_app_ref = None  # keep a strong reference to any QApplication we create ourselves


def _format_value(obj) -> str:
    """One-shot (not polled) snapshot of get_current_value(), for the
    "Value" column -- purely to help identify a component while browsing,
    not a live monitor."""
    getter = getattr(obj, "get_current_value", None)
    if not callable(getter):
        return ""
    try:
        val = getter()
    except Exception:
        return "<error>"
    if isinstance(val, enum.Enum):
        return val.name
    return str(val)


class ComponentSelectorQt(QtWidgets.QWidget):
    """Embeddable QWidget: filter/search row, a QTreeWidget to dig into the
    namespace structure, and a bookmarks row. Not a top-level window on its
    own -- see `make_component_selector_qt_window` for that."""

    def __init__(
        self,
        root: Any,
        kind_filter: str = "All",
        bookmarks: Optional[ComponentBookmarks] = None,
        recent: Optional[RecentComponents] = None,
        on_select: Optional[Callable[[str, Any], None]] = None,
        max_depth: int = 25,
        parent=None,
    ):
        super().__init__(parent)
        self.root = root
        self.max_depth = max_depth
        self.bookmarks = bookmarks or ComponentBookmarks(
            namespace_name=getattr(root, "name", None)
        )
        self.recent = recent or RecentComponents(
            namespace_name=getattr(root, "name", None)
        )
        self._on_select_cbs: List[Callable[[str, Any], None]] = (
            [on_select] if callable(on_select) else []
        )
        self.selected_name: Optional[str] = None
        self.selected_obj: Any = None
        self._tree: Optional[ComponentNode] = None

        layout = QtWidgets.QVBoxLayout(self)

        filter_row = QtWidgets.QHBoxLayout()
        self.type_filter = QtWidgets.QComboBox()
        self.type_filter.addItems(["All", "Adjustable", "Detector"])
        if kind_filter in ("All", "Adjustable", "Detector"):
            self.type_filter.setCurrentText(kind_filter)
        self.search_box = QtWidgets.QLineEdit()
        self.search_box.setPlaceholderText("filter by name...")
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        filter_row.addWidget(QtWidgets.QLabel("Type:"))
        filter_row.addWidget(self.type_filter)
        filter_row.addWidget(QtWidgets.QLabel("Search:"))
        filter_row.addWidget(self.search_box, 1)
        filter_row.addWidget(self.refresh_btn)
        layout.addLayout(filter_row)

        recent_row = QtWidgets.QHBoxLayout()
        self.recent_dropdown = QtWidgets.QComboBox()
        self.recent_goto_btn = QtWidgets.QPushButton("Go")
        recent_row.addWidget(QtWidgets.QLabel("Recent:"))
        recent_row.addWidget(self.recent_dropdown, 1)
        recent_row.addWidget(self.recent_goto_btn)
        layout.addLayout(recent_row)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Type", "Value"])
        self.tree.setColumnWidth(0, 320)
        layout.addWidget(self.tree, 1)

        self.selected_label = QtWidgets.QLabel("nothing selected")
        layout.addWidget(self.selected_label)

        bookmark_row = QtWidgets.QHBoxLayout()
        self.bookmark_dropdown = QtWidgets.QComboBox()
        self.bookmark_goto_btn = QtWidgets.QPushButton("Go")
        self.bookmark_name_box = QtWidgets.QLineEdit()
        self.bookmark_name_box.setPlaceholderText("bookmark name")
        self.bookmark_save_btn = QtWidgets.QPushButton("Save current")
        self.bookmark_remove_btn = QtWidgets.QPushButton("Remove")
        bookmark_row.addWidget(QtWidgets.QLabel("Bookmarks:"))
        bookmark_row.addWidget(self.bookmark_dropdown, 1)
        bookmark_row.addWidget(self.bookmark_goto_btn)
        bookmark_row.addWidget(self.bookmark_name_box)
        bookmark_row.addWidget(self.bookmark_save_btn)
        bookmark_row.addWidget(self.bookmark_remove_btn)
        layout.addLayout(bookmark_row)

        self.type_filter.currentTextChanged.connect(lambda _t: self._render())
        self.search_box.textChanged.connect(lambda _t: self._render())
        self.refresh_btn.clicked.connect(lambda: self.refresh(rebuild=True))
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.recent_goto_btn.clicked.connect(self._goto_recent)
        self.bookmark_goto_btn.clicked.connect(self._goto_bookmark)
        self.bookmark_save_btn.clicked.connect(self._save_bookmark)
        self.bookmark_remove_btn.clicked.connect(self._remove_bookmark)

        self.refresh(rebuild=True)

    # -- tree building/rendering -------------------------------------------------

    def refresh(self, rebuild: bool = False) -> None:
        if rebuild or self._tree is None:
            self._tree = build_tree(self.root, max_depth=self.max_depth)
        self._render()
        self._refresh_bookmark_options()
        self._refresh_recent_options()

    def _render(self) -> None:
        self.tree.clear()
        filtered = filter_root(
            self._tree, kind=self.type_filter.currentText(), search=self.search_box.text()
        )
        for child in filtered.children:
            self.tree.addTopLevelItem(self._build_item(child))
        self.tree.expandToDepth(0)

    def _build_item(self, node: ComponentNode) -> QtWidgets.QTreeWidgetItem:
        icon = KIND_ICONS.get(node.kind, "")
        leaf_name = node.name.rsplit(".", 1)[-1]
        value = _format_value(node.obj) if node.obj is not None else ""
        item = QtWidgets.QTreeWidgetItem([f"{icon} {leaf_name}", node.kind, value])
        item.setData(0, _OBJ_ROLE, node.obj)
        item.setData(0, _PATH_ROLE, node.name)
        item.setData(0, _KIND_ROLE, node.kind)
        if node.kind == "lazy":
            item.setToolTip(0, "not yet initialized -- selecting won't build it")
        for child in node.children:
            item.addChild(self._build_item(child))
        return item

    # -- selection -----------------------------------------------------------

    def _on_item_clicked(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        obj = item.data(0, _OBJ_ROLE)
        path = item.data(0, _PATH_ROLE)
        kind = item.data(0, _KIND_ROLE)
        self._select(path, obj, kind)

    def _select(self, path: str, obj: Any, kind: str) -> None:
        self.selected_name = path
        self.selected_obj = obj
        if kind == "lazy":
            self.selected_label.setText(
                f"Selected: {KIND_ICONS['lazy']} {path} "
                "(not yet initialized -- resolving it will build it)"
            )
        else:
            self.selected_label.setText(f"Selected: {KIND_ICONS.get(kind, '')} {path} ({kind})")
        self.recent.touch(path)
        self._refresh_recent_options()
        self._notify(path, obj)

    def _notify(self, path: str, obj: Any) -> None:
        for cb in list(self._on_select_cbs):
            try:
                cb(path, obj)
            except Exception as e:
                print(f"component selector on_select callback failed: {e}")

    def get_selected(self):
        return self.selected_name, self.selected_obj

    def on_select(self, cb: Callable[[str, Any], None]) -> None:
        if callable(cb):
            self._on_select_cbs.append(cb)

    # -- bookmarks -------------------------------------------------------------

    def _refresh_bookmark_options(self) -> None:
        names = self.bookmarks.names()
        current = self.bookmark_dropdown.currentText()
        self.bookmark_dropdown.blockSignals(True)
        self.bookmark_dropdown.clear()
        self.bookmark_dropdown.addItems(names)
        if current in names:
            self.bookmark_dropdown.setCurrentText(current)
        self.bookmark_dropdown.blockSignals(False)

    def _goto_bookmark(self) -> None:
        name = self.bookmark_dropdown.currentText()
        if not name:
            return
        path = self.bookmarks.get(name)
        try:
            obj = resolve_path(self.root, path)
        except Exception as e:
            self.selected_label.setText(f"Could not resolve bookmark '{name}' ({path}): {e}")
            return
        kind = classify(obj)
        self._select(path, obj, kind)
        self.selected_label.setText(
            f"Selected (bookmark '{name}'): {KIND_ICONS.get(kind, '')} {path} ({kind})"
        )

    def _save_bookmark(self) -> None:
        name = self.bookmark_name_box.text().strip()
        if not name:
            self.selected_label.setText("Enter a bookmark name first")
            return
        if self.selected_name is None:
            self.selected_label.setText("Select a component first")
            return
        self.bookmarks.save(name, self.selected_name)
        self.bookmark_name_box.clear()
        self._refresh_bookmark_options()
        self.bookmark_dropdown.setCurrentText(name)

    def _remove_bookmark(self) -> None:
        name = self.bookmark_dropdown.currentText()
        if not name:
            return
        self.bookmarks.remove(name)
        self._refresh_bookmark_options()

    # -- recent ------------------------------------------------------------

    def _refresh_recent_options(self) -> None:
        items = self.recent.all()
        current = self.recent_dropdown.currentText()
        self.recent_dropdown.blockSignals(True)
        self.recent_dropdown.clear()
        self.recent_dropdown.addItems(items)
        if current in items:
            self.recent_dropdown.setCurrentText(current)
        self.recent_dropdown.blockSignals(False)

    def _goto_recent(self) -> None:
        path = self.recent_dropdown.currentText()
        if not path:
            return
        try:
            obj = resolve_path(self.root, path)
        except Exception as e:
            self.selected_label.setText(f"Could not resolve recent item '{path}': {e}")
            return
        kind = classify(obj)
        self._select(path, obj, kind)
        self.selected_label.setText(
            f"Selected (recent): {KIND_ICONS.get(kind, '')} {path} ({kind})"
        )


class ComponentSelectorQtWindow:
    """Wraps ComponentSelectorQt in its own top-level window, reusing the
    same non-blocking-inside-IPython / blocking-otherwise show() logic as
    eco.widgets.display_qt.DisplayQt (start/run/stop), so opening it from an
    interactive session doesn't block the prompt when Qt's IPython
    event-loop integration is available."""

    def __init__(
        self,
        root: Any,
        kind_filter: str = "All",
        bookmarks: Optional[ComponentBookmarks] = None,
        recent: Optional[RecentComponents] = None,
        on_select: Optional[Callable[[str, Any], None]] = None,
        max_depth: int = 25,
        auto_start: bool = True,
    ):
        self._init_kwargs = dict(
            root=root,
            kind_filter=kind_filter,
            bookmarks=bookmarks,
            recent=recent,
            on_select=on_select,
            max_depth=max_depth,
        )
        self.window: Optional[QtWidgets.QMainWindow] = None
        self.selector: Optional[ComponentSelectorQt] = None
        if auto_start:
            self.start()

    def _build_window(self) -> None:
        global _app_ref
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])
            _app_ref = app
        self.selector = ComponentSelectorQt(**self._init_kwargs)
        root_name = getattr(self._init_kwargs["root"], "name", None) or "namespace"
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle(f"Component selector: {root_name}")
        self.window.setCentralWidget(self.selector)
        self.window.resize(760, 620)
        # WA_DeleteOnClose: without it, closing via the window's own
        # native close (X) button just hides it -- it's never actually
        # destroyed, so the destroyed hook below would never fire there
        # (only on an explicit stop()/.close() that happens to also get
        # garbage-collected). See eco.widgets.qt_lifecycle's module
        # docstring for the fuller why.
        self.window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        # so closing via the window's own native close (X) button tears
        # down the same as stop() does -- otherwise get_selected()/
        # on_select() would keep referencing a selector whose window is
        # already gone, and a caller checking "is it still open" via
        # `.window is not None` would get a stale answer
        self.window.destroyed.connect(self._on_window_destroyed)
        self.window.show()

    def _on_window_destroyed(self, *args) -> None:
        self.window = None
        self.selector = None

    def run(self) -> None:
        """Build and run the window with a blocking Qt event loop. Use this
        when there is no GUI event-loop integration available to pump the
        window for you (e.g. a plain python script)."""
        app = QtWidgets.QApplication.instance()
        created_app = app is None
        if created_app:
            app = QtWidgets.QApplication([])
        if self.window is None:
            self._build_window()
        if created_app:
            app.exec_()

    def start(self) -> None:
        """Show the window without blocking. Inside an IPython terminal
        session this reuses (or enables) IPython's Qt event-loop
        integration. Outside of IPython, or if an incompatible GUI loop is
        already active, falls back to the blocking `run()`."""
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
        if active is None:
            try:
                ip.enable_gui("qt")
            except Exception:
                pass
        elif active not in ("qt", "qt4", "qt5", "qt6"):
            print(
                f"eco component selector: a different GUI event loop ('{active}') "
                "is already active in this IPython session, so the window can't "
                "be pumped non-blockingly alongside it. Showing it in blocking "
                "mode instead (closing the window returns control)."
            )
            self.run()
            return

        self._build_window()

    def stop(self) -> None:
        """Close the window."""
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None
            self.selector = None

    def get_selected(self):
        if self.selector is None:
            return None, None
        return self.selector.get_selected()

    def on_select(self, cb: Callable[[str, Any], None]) -> None:
        if self.selector is not None:
            self.selector.on_select(cb)


def make_component_selector_qt(
    root: Any,
    kind_filter: str = "All",
    bookmarks: Optional[ComponentBookmarks] = None,
    recent: Optional[RecentComponents] = None,
    on_select: Optional[Callable[[str, Any], None]] = None,
    max_depth: int = 25,
) -> ComponentSelectorQt:
    """Build just the embeddable QWidget (no window) -- for embedding inside
    another Qt layout as a sub-component."""
    return ComponentSelectorQt(
        root,
        kind_filter=kind_filter,
        bookmarks=bookmarks,
        recent=recent,
        on_select=on_select,
        max_depth=max_depth,
    )


def make_component_selector_qt_window(
    root: Any,
    kind_filter: str = "All",
    bookmarks: Optional[ComponentBookmarks] = None,
    recent: Optional[RecentComponents] = None,
    on_select: Optional[Callable[[str, Any], None]] = None,
    max_depth: int = 25,
    auto_start: bool = True,
) -> ComponentSelectorQtWindow:
    """Convenience factory, mirrors make_assembly_qt_window's naming."""
    return ComponentSelectorQtWindow(
        root,
        kind_filter=kind_filter,
        bookmarks=bookmarks,
        recent=recent,
        on_select=on_select,
        max_depth=max_depth,
        auto_start=auto_start,
    )
