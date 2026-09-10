"""
Qt "desktop" front-end for eco -- a Spyder/MATLAB-like workbench window
embedding a live IPython (Jupyter) console running eco's namespace,
alongside a dockable "Namespace" launcher panel for opening device
widgets. Lengthy/subject-to-taste GUI code lives here on purpose, kept
out of eco.elements.assembly/eco.utilities.config (which change for
functional reasons, not UI ones) -- see those modules for the small,
stable hooks this builds on (Assembly._default_widget, Namespace's
lazy/initialized/failed name tracking).

One of eco's three(+1) front-ends -- see eco_cli.py's ``--ui`` flag:
"shell" (the traditional terminal session, default, completely untouched
by any of this), "lab"/"voila" (JupyterLab / a Voila dashboard, via
eco.widgets.widget_tray.make_namespace_dashboard), and "desktop" (this
module, reachable via ``--ui desktop`` once wired -- see eco_cli.py).

WHY AN IN-PROCESS KERNEL (usually): qtconsole can connect to an external
kernel, or run one in-process, in the very same interpreter as the Qt GUI.
This prefers the latter so the console's ``namespace`` variable *can be*
the exact live object the launcher panel introspects and opens widgets
from -- no IPC, no separate process to keep in sync. BUT: an in-process
kernel needs IPython's ``InProcessInteractiveShell`` to become the
process's one-and-only ``InteractiveShell`` singleton, which is only
possible when nothing else already holds that slot -- calling
``eco.start_desktop()`` from a running ``ipython`` terminal (or any other
existing IPython/Jupyter session) means a ``TerminalInteractiveShell`` (or
``ZMQInteractiveShell``) already does, and ipykernel's own
``InteractiveShell.instance()`` call raises ``MultipleInstanceError``
instead of silently coexisting. See ``eco.widgets.console_kernel`` for the
full explanation and the subprocess-kernel fallback this uses instead
whenever that's the case -- the tradeoff being that a subprocess console
gets an independent-but-equivalent namespace (rebuilt fresh via
``build_namespace``), not literally the same objects as the caller's.

WHY OPENED WIDGETS ARE CALLED DIRECTLY, NOT THROUGH THE CONSOLE: an earlier
version ran ``<name>.widget()`` *as a console command* instead, on the
premise that calling it straight from the launcher's Qt code would get
back an ipywidgets object rather than a QWidget --
``eco.utilities.utilities.is_notebook()`` (which ``Assembly.widget()``/
``_default_widget`` overrides branch on) checks
``get_ipython().__class__.__name__ == "ZMQInteractiveShell"``, and the
worry was that this app's own console kernel would make that true even
from the *launcher's* code. It doesn't: ``get_ipython()`` reflects
whatever IPython shell owns *this process* -- the Namespace launcher runs
on the same thread as the rest of the GUI, and neither an in-process
desktop kernel (a distinct ``InProcessInteractiveShell``, a different
class name) nor a subprocess one (a different process entirely, doesn't
touch this process's IPython state at all) changes what ``get_ipython()``
returns here. So it's exactly the same as calling ``.widget()`` from the
calling terminal's own prompt: ``is_notebook()`` correctly says False,
``.widget()`` returns (and self-shows) a real QWidget. Calling it directly
also sidesteps a startup-code race that could leave a subprocess console's
``namespace`` undefined at all -- doesn't apply here, since nothing routes
through any console. (Separately: fetching the object itself has to go
through ``_resolve_namespace_item``, not a bare ``getattr(namespace,
name)`` -- see ``build_namespace``'s docstring for why. Both the launcher
panel and any console get this right now; see ``build_namespace_vars``.)
Calling it directly also enables real QDockWidget
embedding (see _dock_widget_object): opened from the Namespace panel, a
widget is reparented into a tiled dock in this window instead of staying
its own separate window -- still poppable back out at any time via the
dock's own float button/drag. Calling the exact same `<name>.widget()`
from a plain terminal, with no desktop window around to dock into, is
unaffected and stays a plain standalone window, same as always.

Usage, once the ``namespace`` object exists (see build_namespace, which
replicates startup_inline.py's ``import eco.<scope> as <scope>`` without
needing IPython)::

    from eco.widgets.desktop_app import EcoDesktopApp, build_namespace
    namespace = build_namespace(scope="bernina", lazy=True)
    app = EcoDesktopApp(namespace, theme="dark")   # blocks (see module CLI)

Or from the command line: ``python -m eco.widgets.desktop_app --scope
bernina --lazy --theme dark``.

TESTING NOTE: the pure sorting/labeling logic the launcher panel drives
(sorted_namespace_entries) is tested directly against a fake namespace
object in tests/test_desktop_app.py, without needing a live namespace, a
real Qt display, or a real qtconsole kernel. The qtconsole embedding
itself and the actual dock/console layout could only be smoke-tested
under this sandbox's offscreen Qt platform -- try it for real (a real X11
display) before relying on it day to day.
"""
import argparse
import base64
import json
import logging
import threading
from pathlib import Path

from qtpy import QtCore, QtGui, QtWidgets

logger = logging.getLogger(__name__)

_app_ref = None

# classic terminal "spinner" glyphs -- rotated through while a lazy
# namespace entry is initializing (see _NamespaceLauncher)
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# where EcoDesktopApp.save_workspace/load_workspace read and write by
# default -- dock geometry/state plus which namespace entries were open,
# so "Reload Last Workspace" brings back the same layout *and* widgets
DEFAULT_WORKSPACE_FILE = Path.home() / ".eco" / "desktop_workspace.json"


def _qbytearray_to_str(qba):
    """QByteArray (from QMainWindow.saveGeometry()/saveState()) -> a
    JSON-safe string."""
    return base64.b64encode(bytes(qba)).decode("ascii")


def _str_to_qbytearray(s):
    """Inverse of _qbytearray_to_str."""
    return QtCore.QByteArray(base64.b64decode(s.encode("ascii")))


def build_namespace(scope="bernina", lazy=True):
    """Build (or fetch, if already built) the same `namespace` *tracking*
    object startup_inline.py's ``import eco.<scope> as <scope>; from
    eco.<scope> import *`` produces -- without needing IPython to run it.
    Sets ``eco.ecocnf.startup_lazy`` first if `lazy`, exactly like
    startup_inline.py does, so lazy-registered components
    (``append_obj(..., lazy=True)``) actually defer instantiation.

    NOTE: this ``namespace`` object only tracks name -> state bookkeeping
    (initialized_names/lazy_names/failed_names, init_name(), init_all()).
    It does NOT hold the actual device objects as attributes --
    Namespace.append_obj writes those onto the scope's root module
    (sys.modules[namespace.root_module]) instead, which is what makes
    `from eco.<scope> import *` expose bare names like `mono`/`att` in the
    first place. Use build_namespace_vars (or _resolve_namespace_item) to
    actually fetch a device by name -- see their docstrings."""
    import importlib

    from eco import ecocnf

    if lazy:
        ecocnf.startup_lazy = True
    module = importlib.import_module(f"eco.{scope}")
    return getattr(module, "namespace")


def build_namespace_vars(scope="bernina", lazy=True):
    """Like build_namespace, but returns a dict mirroring exactly what
    `from eco.<scope> import *` binds in eco's shell UI (startup_inline.py)
    -- every public top-level name on the scope module, including each
    device (see build_namespace's note on why they live on the module, not
    the Namespace instance) plus `namespace` itself. Used to seed a
    console's namespace so it matches the shell UI 1:1, e.g.
    ``console.execute`` targets or a kernel's ``push()``/``user_ns``."""
    import importlib

    from eco import ecocnf

    if lazy:
        ecocnf.startup_lazy = True
    module = importlib.import_module(f"eco.{scope}")
    return {k: v for k, v in vars(module).items() if not k.startswith("_")}


def _resolve_namespace_item(namespace, name):
    """The actual object registered as `name` on `namespace` -- see
    build_namespace's docstring for why `getattr(namespace, name)` doesn't
    work. Delegates to Namespace.resolve_item (the canonical
    implementation, shared with eco.widgets.widget_tray); falls back to
    the same lazy/failed/initialized dict chain directly for a fake
    namespace stand-in that has the dicts but not the method (see
    tests/test_desktop_app.py)."""
    resolve = getattr(namespace, "resolve_item", None)
    if callable(resolve):
        return resolve(name)
    return (
        getattr(namespace, "lazy_items", {}).get(name)
        or getattr(namespace, "failed_items", {}).get(name)
        or getattr(namespace, "initialized_items", {}).get(name)
    )


def sorted_namespace_entries(namespace):
    """(name, state) pairs for every name registered on `namespace`,
    state one of "initialized" | "lazy" | "failed", sorted alphabetically
    by name. Pure function over Namespace's own state-tracking properties
    (eco.utilities.config.Namespace.initialized_names/lazy_names/
    failed_names) -- no Qt, so this is unit-tested directly against a
    fake namespace stand-in."""
    entries = []
    for name in getattr(namespace, "initialized_names", set()):
        entries.append((name, "initialized"))
    for name in getattr(namespace, "lazy_names", set()):
        entries.append((name, "lazy"))
    for name in getattr(namespace, "failed_names", set()):
        entries.append((name, "failed"))
    entries.sort(key=lambda t: t[0].lower())
    return entries


class _InitDoneBridge(QtCore.QObject):
    """Cross-thread signal: the background init thread(s) in
    _NamespaceLauncher._start_loading/_init_all_thread emit these (never
    touch Qt widgets themselves), and the connected slots -- running on
    the GUI thread, since Qt auto-queues a signal emitted from a
    different thread than the receiving QObject's own -- do the actual UI
    update. Plain QTimer.singleShot from a worker thread is not a
    reliable way to get back onto the GUI thread; this is the same
    pattern eco.widgets.camserver_stream_qt's _StreamBridge uses for the
    same reason."""

    done = QtCore.Signal(str)  # one name finished initializing (single-click path)
    batch_done = QtCore.Signal(object)  # an Init All run finished -- carries the set of names it covered


class _NamespaceLauncher(QtWidgets.QWidget):
    """Dockable panel: one row per registered namespace name, filterable,
    two columns (Name, Required). Initialized entries are opened
    immediately on click; lazy entries are shown grayed out and, on click,
    trigger initialization (with a spinner on that row) before opening;
    failed entries are shown struck-through/red and are not retried
    automatically (a real retry action is a reasonable follow-up, not
    attempted here). "Init All" initializes every currently-lazy entry in
    the background, same spinner treatment, without auto-opening any of
    them (unlike a single click) -- opening a dozen widgets at once from
    one button would be more surprising than helpful. Each row's icon
    shows what *kind* of thing it is (adjustable/detector/assembly/...),
    reusing eco.widgets.component_selector's KIND_ICONS convention. The
    Required column is a checkbox mirroring/editing
    Namespace.required_names() -- eco's own "which of these must be
    successfully built for init_all(required_only=True) to consider the
    namespace ready" list (see eco.utilities.config.Namespace) -- toggling
    it here calls required_names() the same way any other code would. A
    live-refresh timer keeps all of this current even for changes this
    panel didn't itself trigger -- e.g. something initialized directly
    from a console."""

    LIVE_REFRESH_INTERVAL_MS = 2000
    _COL_NAME = 0
    _COL_REQUIRED = 1

    def __init__(self, namespace, on_open, parent=None):
        super().__init__(parent)
        self.namespace = namespace
        self.on_open = on_open  # (name) -> None
        self._loading = set()  # names currently initializing
        self._spinner_frame = 0
        self._init_bridge = _InitDoneBridge()
        self._init_bridge.done.connect(self._on_init_done)
        self._init_bridge.batch_done.connect(self._on_batch_init_done)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._filter_edit = QtWidgets.QLineEdit()
        self._filter_edit.setPlaceholderText("Filter...")
        self._filter_edit.textChanged.connect(self._refresh)
        layout.addWidget(self._filter_edit)

        self._list = QtWidgets.QTableWidget(0, 2)
        self._list.setHorizontalHeaderLabels(["Name", "Required"])
        self._list.verticalHeader().setVisible(False)
        self._list.horizontalHeader().setSectionResizeMode(
            self._COL_NAME, QtWidgets.QHeaderView.Stretch
        )
        self._list.horizontalHeader().setSectionResizeMode(
            self._COL_REQUIRED, QtWidgets.QHeaderView.ResizeToContents
        )
        self._list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list)

        button_row = QtWidgets.QHBoxLayout()
        refresh_btn = QtWidgets.QPushButton("Refresh")
        refresh_btn.setToolTip("Re-scan the namespace (e.g. after something else initialized a lazy entry)")
        refresh_btn.clicked.connect(self._refresh)
        button_row.addWidget(refresh_btn)

        self._init_all_btn = QtWidgets.QPushButton("Init All")
        self._init_all_btn.setToolTip(
            "Initialize every not-yet-built (lazy) entry in the background -- "
            "does not open any of them"
        )
        self._init_all_btn.clicked.connect(self._on_init_all_clicked)
        button_row.addWidget(self._init_all_btn)
        layout.addLayout(button_row)

        self._spinner_timer = QtCore.QTimer(self)
        self._spinner_timer.timeout.connect(self._advance_spinner)

        # picks up load-state/kind changes this panel didn't itself
        # trigger (a QTimer parented to this widget is torn down with it,
        # so nothing to explicitly stop on close)
        self._live_refresh_timer = QtCore.QTimer(self)
        self._live_refresh_timer.timeout.connect(self._refresh)
        self._live_refresh_timer.start(self.LIVE_REFRESH_INTERVAL_MS)

        self._refresh()

    def _current_states(self):
        return dict(sorted_namespace_entries(self.namespace))

    def _required_names(self):
        """set() of names currently in Namespace.required_names() -- empty
        (not an error) if this namespace has none set, or doesn't support
        the concept at all (e.g. a fake namespace in a test)."""
        try:
            return set(self.namespace.required_names())
        except Exception:
            return set()

    def _refresh(self):
        # Runs every LIVE_REFRESH_INTERVAL_MS (2s) on the GUI thread, and
        # for bernina rebuilds ~120 rows -- BLOCK signals for the duration
        # so setItem()/setCheckState() below don't fire itemChanged (which
        # would otherwise call _on_required_toggled -- a real write to
        # Namespace.required_names()'s backing file -- for every row, on
        # every tick, whether or not anything actually changed).
        self._list.blockSignals(True)
        try:
            entries = sorted_namespace_entries(self.namespace)
            # Reconcile against real state before drawing anything: a name
            # in self._loading (single-click init, or part of an Init All
            # batch) whose state has actually moved past "lazy" is done,
            # whether or not the thread that did it has gotten around to
            # emitting its own completion signal yet. This is what lets
            # entries stop spinning -- and become clickable again, see
            # open_by_name's `name in self._loading` guard -- one by one as
            # Init All works through them, instead of all at once only when
            # the whole batch finishes (init_all() itself doesn't report
            # per-item progress, just a single call that blocks until every
            # name in the batch is done). Only ever removes names, and only
            # ever from here, not from the individual-click path's own
            # _on_init_done bridge signal handler -- both discard() the
            # same set, so whichever notices first wins; harmless either
            # way since discard() on an absent name is a no-op. Deliberately
            # does NOT call on_open here (unlike _on_init_done) -- opening a
            # dozen widgets at once mid-batch would be exactly the
            # surprise _on_init_all_clicked's docstring says to avoid.
            states = dict(entries)
            self._loading -= {name for name in self._loading if states.get(name, "lazy") != "lazy"}

            query = self._filter_edit.text().strip().lower()
            self._list.setRowCount(0)
            lazy_count = 0
            required = self._required_names()
            for name, state in entries:
                if state == "lazy":
                    lazy_count += 1
                if query and query not in name.lower():
                    continue
                row = self._list.rowCount()
                self._list.insertRow(row)

                name_item = QtWidgets.QTableWidgetItem(self._label_for(name, state))
                name_item.setData(QtCore.Qt.UserRole, name)
                name_item.setFlags(name_item.flags() & ~QtCore.Qt.ItemIsEditable)
                if name in self._loading:
                    name_item.setForeground(QtGui.QColor(0, 191, 165))
                elif state == "lazy":
                    name_item.setForeground(QtGui.QColor(140, 140, 140))
                elif state == "failed":
                    name_item.setForeground(QtGui.QColor(220, 80, 80))
                self._list.setItem(row, self._COL_NAME, name_item)

                # A checkable QTableWidgetItem, not a QCheckBox cell widget:
                # this timer rebuilds every row every tick (see above), and
                # constructing/destroying a real QCheckBox+QWidget+QHBoxLayout
                # per row, twice a second times the row count, is real,
                # visible GUI-thread cost at bernina's scale (~120 entries) --
                # a lightweight item carries the same checked/unchecked state
                # without any of that.
                required_item = QtWidgets.QTableWidgetItem()
                required_item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
                required_item.setCheckState(
                    QtCore.Qt.Checked if name in required else QtCore.Qt.Unchecked
                )
                required_item.setData(QtCore.Qt.UserRole, name)
                required_item.setToolTip(
                    "Whether init_all(required_only=True) needs this built to "
                    "consider the namespace ready (Namespace.required_names())"
                )
                self._list.setItem(row, self._COL_REQUIRED, required_item)
            self._init_all_btn.setEnabled(lazy_count > 0)
        finally:
            self._list.blockSignals(False)

    def _on_item_changed(self, item):
        if item.column() != self._COL_REQUIRED:
            return
        name = item.data(QtCore.Qt.UserRole)
        checked = item.checkState() == QtCore.Qt.Checked
        try:
            current = set(self.namespace.required_names())
            if checked:
                current.add(name)
            else:
                current.discard(name)
            self.namespace.required_names(sorted(current))
        except Exception:
            logger.exception("updating required_names for %r failed", name)

    def _label_for(self, name, state):
        if name in self._loading:
            return f"{_SPINNER_FRAMES[self._spinner_frame % len(_SPINNER_FRAMES)]}  {name}"
        if state == "lazy":
            # No icon -- the gray text colour (see _refresh) already says
            # "not built yet"; the animated spinner above is reserved for
            # "actually initializing right now", not just "could be".
            return name
        return f"{self._kind_icon(name, state)} {name}"

    def _kind_icon(self, name, state):
        """A per-row icon showing what *kind* of thing `name` is --
        eco.widgets.component_selector's KIND_ICONS convention, already
        used in every component picker/browser this session added.
        "lazy"/"failed" are shown as their own kind (never resolving a
        lazy proxy just to classify it -- same rule component_selector.py
        itself follows); "initialized" resolves the real object (already
        built, so free) and classifies it properly."""
        from eco.widgets.component_selector import KIND_ICONS, classify

        if state in ("lazy", "failed"):
            return KIND_ICONS[state]
        try:
            obj = _resolve_namespace_item(self.namespace, name)
        except Exception:
            return KIND_ICONS["other"]
        return KIND_ICONS.get(classify(obj), KIND_ICONS["other"])

    def _advance_spinner(self):
        if not self._loading:
            self._spinner_timer.stop()
            return
        self._spinner_frame += 1
        self._refresh()

    def _on_item_clicked(self, item):
        if item.column() != self._COL_NAME:
            return  # a Required-column click toggles its checkbox, not open/init
        self.open_by_name(item.data(QtCore.Qt.UserRole))

    def open_by_name(self, name):
        """Open `name` the same way clicking its row would: immediately if
        already initialized, via the background-init-then-open path if
        lazy, a no-op if failed/unknown. Public so workspace reload
        (EcoDesktopApp.load_workspace) can reopen previously-open entries
        without going through a QListWidgetItem."""
        if name in self._loading:
            return
        state = self._current_states().get(name)
        if state == "initialized":
            self.on_open(name)
            return
        if state != "lazy":
            return  # failed, or not a name on this namespace at all
        self._start_loading(name)

    def _start_loading(self, name):
        self._loading.add(name)
        if not self._spinner_timer.isActive():
            self._spinner_timer.start(150)
        self._refresh()

        def _init():
            try:
                # Every thread that touches Channel Access must attach to
                # the process's one shared "initial context" instead of
                # implicitly creating its own (pyepics's default
                # first-use-per-thread behaviour) -- a second, separate CA
                # context in the same process is a documented segfault/
                # corruption source (confirmed via dmesg against the real
                # bernina namespace -- see eco.utilities.config.Namespace.
                # _init_batch and eco.status_server.parallel_init's module
                # docstrings for the fuller writeup). This background
                # thread is exactly that: the calling terminal's main
                # thread already holds the real "initial" CA context from
                # eco's own startup, so this one has to explicitly attach
                # to it before init_name() makes its first CA call.
                import epics.ca as ca

                ca.use_initial_context()
            except Exception:
                pass  # no pyepics/no CA context yet -- init_name() below may still be a no-op-safe path (e.g. a fake namespace in tests)
            try:
                self.namespace.init_name(name, raise_errors=False)
            except Exception:
                logger.exception("initializing %r failed", name)
            finally:
                self._init_bridge.done.emit(name)

        threading.Thread(target=_init, daemon=True).start()

    def _on_init_done(self, name):
        self._loading.discard(name)
        self._refresh()
        if self._current_states().get(name) == "initialized":
            self.on_open(name)

    def _on_init_all_clicked(self):
        batch = set(getattr(self.namespace, "lazy_names", ())) - self._loading
        if not batch:
            return
        self._loading.update(batch)
        if not self._spinner_timer.isActive():
            self._spinner_timer.start(150)
        self._refresh()

        def _init_all():
            try:
                # init_all()'s own _init_batch already attaches every
                # worker thread (including the synchronous background=False
                # path used here, which runs in *this* thread) to the
                # shared CA context itself -- see _start_loading's _init()
                # above for the fuller explanation of why that matters;
                # unlike a single init_name() call, init_all() doesn't need
                # it done for it here.
                self.namespace.init_all(
                    required_only=False, background=False, raise_errors=False
                )
            except Exception:
                logger.exception("init_all failed")
            finally:
                self._init_bridge.batch_done.emit(frozenset(batch))

        threading.Thread(target=_init_all, daemon=True).start()

    def _on_batch_init_done(self, batch):
        # deliberately does NOT call self.on_open for any of these --
        # opening a dozen widgets at once from one button would be more
        # surprising than helpful (unlike a single click, which does)
        self._loading -= set(batch)
        self._refresh()


class _ManagedDockWidget(QtWidgets.QDockWidget):
    """QDockWidget that runs a cleanup callback when the user closes it via
    its own title-bar close button (X) -- so a docked device widget's
    background polling (and whatever hardware connection it opened)
    actually stops then, rather than quietly continuing forever. Floating
    it out (drag the title bar, or its float button) does NOT go through
    closeEvent -- that's normal QDockWidget behaviour, and exactly how a
    docked widget gets "detached" back into its own window on purpose."""

    def __init__(self, title, on_close, parent=None):
        super().__init__(title, parent)
        self._on_close = on_close

    def closeEvent(self, event):
        try:
            self._on_close()
        except Exception:
            logger.exception("dock close handler failed")
        super().closeEvent(event)


def _dock_object_name(candidate, existing_names):
    """`candidate`, de-duplicated against `existing_names` by appending
    "_2", "_3", ... as needed -- QDockWidget objectNames must be unique
    within a QMainWindow for saveState()/restoreState() to reliably tell
    docks apart (same helper as eco.widgets.dashboard_qt's, duplicated
    rather than cross-imported -- see this file's _qbytearray_to_str for
    the same "small private helper, one per GUI module" convention)."""
    if candidate not in existing_names:
        return candidate
    i = 2
    while f"{candidate}_{i}" in existing_names:
        i += 1
    return f"{candidate}_{i}"


class _DesktopMainWindow(QtWidgets.QMainWindow):
    """Plain QMainWindow, except closeEvent also runs `on_close` (see
    EcoDesktopApp._on_window_closing) -- workspace autosave (so "Reload
    Last Workspace" always has something recent without the user needing
    to remember to save first) plus full resource teardown (docked
    widgets' poll threads, this window's console kernel), so closing via
    the window's own native close (X) button doesn't leak either."""

    def __init__(self, on_close, parent=None):
        super().__init__(parent)
        self._on_close = on_close

    def closeEvent(self, event):
        try:
            self._on_close()
        except Exception:
            logger.exception("workspace autosave on close failed")
        super().closeEvent(event)


class EcoDesktopApp:
    """The desktop workbench window: an embedded IPython console dock plus
    a "Namespace" launcher dock -- both movable/floatable/tabbable, same
    as any opened device widget's dock (see _dock_widget_object). See the
    module docstring for the overall design and its known v1
    simplifications."""

    def __init__(
        self,
        namespace=None,
        theme=None,
        link_terminal=True,
        auto_start=True,
        scope="bernina",
        lazy=True,
        with_console=True,
        with_namespace_panel=True,
    ):
        self.namespace = namespace
        self.theme = theme
        # link_terminal=True (default): if this is being called from
        # inside an already-running IPython session (the normal case for
        # eco.start_desktop() -- see eco.widgets.app_launchers) AND no
        # incompatible IPython shell already owns this process's
        # InteractiveShell singleton (see eco.widgets.console_kernel), the
        # desktop's embedded console shares *that* session's own namespace
        # dict (literally the same object, not a copy) instead of getting
        # a fresh empty one -- so a variable set in either console is
        # immediately visible in the other. Otherwise (a genuinely fresh
        # process, or link_terminal=False) it gets its own independent
        # namespace, built fresh via `scope`/`lazy` if needed.
        self.link_terminal = link_terminal
        self.scope = scope
        self.lazy = lazy
        # with_console=False: skip the embedded console/kernel entirely --
        # no MultipleInstanceError risk, no independent-namespace/PYTHONPATH
        # subtleties (see console_kernel), because there's simply no second
        # kernel involved at all. Opening widgets from the Namespace panel
        # already doesn't need a console (see _open_widget) -- this option
        # is for anyone who'd rather the calling terminal stay the one and
        # only "master" session, full stop, and doesn't want an extra
        # console window/kernel taking memory and a moment to start for
        # nothing.
        self.with_console = with_console
        # with_namespace_panel=False: skip building the dockable "Namespace"
        # launcher panel -- for a startup-script-generated "just these
        # widgets" dashboard (see save_startup_script) where the panel
        # would otherwise permanently reserve screen space nobody needs
        # once its job (opening those widgets once, at startup) is done.
        # Doesn't affect anything else: `namespace` itself is still built
        # and still pushed into the console (see _build_console) and still
        # used to resolve/open widgets from a loaded workspace (see
        # load_workspace) -- only the browsable panel UI is skipped.
        self.with_namespace_panel = with_namespace_panel
        self.window = None
        # True only when run() created its own blocking QApplication.exec_()
        # loop (the plain-script / `eco desktop` CLI case) -- see run()'s
        # comment and _on_window_closing() for why this determines whether
        # closing the window should also end the process.
        self._owns_event_loop = False
        self._console = None
        self._console_dock = None
        self._kernel_manager = None
        self._kernel_client = None
        self._kernel_session = None
        self._launcher_dock = None
        self._launcher = None
        # eco.logs.widget(prefer="qt")'s return value (a LogTimelineQtWindow)
        # -- kept referenced (see _open_log_viewer) so it isn't
        # garbage-collected and closed out from under the user; stopped
        # alongside everything else in _on_window_closing.
        self._log_viewer = None
        # names opened via the launcher this session, in open order,
        # de-duplicated -- what "Reload Last Workspace" reopens; see
        # save_workspace/load_workspace. A fresh window always starts
        # with this empty (nothing auto-restores) -- reloading a past
        # workspace is only ever an explicit menu action.
        self._opened_names = []
        # QDockWidgets currently holding an opened device widget (see
        # _open_widget/_dock_widget_object), tiled into the window rather
        # than left as separate top-level windows -- not persisted across
        # workspace save/reload (only which *names* were open is; see
        # _opened_names), since reopening a name rebuilds this fresh.
        self._widget_docks = []
        # the "no console" central-area placeholder (see
        # _build_no_console_placeholder) -- torn out the moment the first
        # real dock claims that space (see _dock_widget_object), so it
        # never permanently wastes screen real estate; None once with_console
        # is True, or after that first removal
        self._no_console_placeholder = None
        if auto_start:
            self.start()

    # -- window construction -------------------------------------------------

    def _build_window(self):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            _app_ref = QtWidgets.QApplication([])
        from eco.widgets.qt_theme import apply_modern_theme

        apply_modern_theme(self.theme)

        self.window = _DesktopMainWindow(on_close=self._on_window_closing)
        self.window.setWindowTitle("eco desktop")
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        # so the destroyed hook below actually fires on a native close (X
        # button) -- without it, .close() just hides the window, it's
        # never truly destroyed; see eco.widgets.qt_lifecycle's module
        # docstring for the fuller why
        self.window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.window.setDockNestingEnabled(True)

        if self.with_console:
            self._build_console()
            # A QDockWidget, not the central widget -- so it's movable,
            # floatable, and tabbable with the Namespace panel and any
            # opened device widget, same as those. (No central widget is
            # set at all in this branch: with none, QMainWindow gives the
            # whole client area to the dock layout instead of carving out
            # a fixed centre region for it, which is what lets the console
            # dock end up filling that same space by default.)
            # Closable (unlike before) -- closing it really does stop the
            # console's kernel (see _on_console_dock_closed), for a "don't
            # need this anymore" startup-script-style session. Movable dock,
            # not the central widget, exactly like before.
            dock = _ManagedDockWidget("Console", self._on_console_dock_closed, parent=self.window)
            dock.setObjectName("console")
            dock.setWidget(self._console)
            dock.setFeatures(
                QtWidgets.QDockWidget.DockWidgetMovable
                | QtWidgets.QDockWidget.DockWidgetFloatable
                | QtWidgets.QDockWidget.DockWidgetClosable
            )
            self.window.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
            self._console_dock = dock
        else:
            placeholder = self._build_no_console_placeholder()
            self.window.setCentralWidget(placeholder)
            self._no_console_placeholder = placeholder

        if self.namespace is not None:
            # Built regardless of with_namespace_panel -- load_workspace's
            # reopen-by-name (and any other programmatic open_by_name call)
            # needs this object's logic even when its dock isn't shown; see
            # with_namespace_panel's docstring in __init__.
            launcher = _NamespaceLauncher(self.namespace, self._open_widget, parent=self.window)
            self._launcher = launcher
            if self.with_namespace_panel:
                # Closable (unlike before) -- closing it only hides the
                # browsing UI, see _on_namespace_dock_closed: the launcher
                # itself (and its live-refresh timer, and open_by_name) keep
                # running headlessly, so nothing that depends on it --
                # workspace reload included -- breaks.
                dock = _ManagedDockWidget("Namespace", self._on_namespace_dock_closed, parent=self.window)
                # saveState()/restoreState() (see save_workspace/load_workspace
                # below) match docks up by objectName -- Qt warns without one
                dock.setObjectName("namespace_launcher")
                dock.setWidget(launcher)
                dock.setFeatures(
                    QtWidgets.QDockWidget.DockWidgetMovable
                    | QtWidgets.QDockWidget.DockWidgetFloatable
                    | QtWidgets.QDockWidget.DockWidgetClosable
                )
                self.window.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
                self._launcher_dock = dock

        self._build_workspace_menu()
        self._build_tools_menu()

        self.window.destroyed.connect(lambda *a: setattr(self, "window", None))
        self.window.resize(1200, 800)
        self.window.show()

    def _build_workspace_menu(self):
        menu = self.window.menuBar().addMenu("&Workspace")

        new_action = menu.addAction("New (Empty) Workspace")
        new_action.setToolTip("Forget which widgets were open -- this window always starts empty anyway")
        new_action.triggered.connect(self._on_new_workspace)

        reload_action = menu.addAction("Reload Last Workspace")
        reload_action.setToolTip("Restore the dock layout and reopen the widgets from the last save")
        reload_action.triggered.connect(lambda: self.load_workspace())

        save_action = menu.addAction("Save Workspace Now")
        save_action.setToolTip(
            "Also happens automatically when this window closes -- this is for saving mid-session"
        )
        save_action.triggered.connect(lambda: self.save_workspace())

        menu.addSeparator()
        script_action = menu.addAction("Save Startup Script...")
        script_action.setToolTip(
            "Write a standalone .sh that relaunches eco desktop with just this "
            "session's open widgets -- lazy loading keeps everything else "
            "untouched, for a fast, minimal dashboard"
        )
        script_action.triggered.connect(self._on_save_startup_script)

    def _on_save_startup_script(self):
        path = self.save_startup_script()
        if path is not None:
            print(f"eco desktop: wrote startup script {path} (and {path.with_suffix('.json')})")

    def _on_new_workspace(self):
        self._opened_names = []
        print(
            "eco desktop: cleared the tracked open-widget list for this session "
            "(the dock layout itself is unchanged -- close and reopen the window "
            "for a fully default layout). Use 'Save Workspace Now' if you want "
            "this empty state to be what 'Reload Last Workspace' brings back."
        )

    def _build_tools_menu(self):
        menu = self.window.menuBar().addMenu("&Tools")

        log_action = menu.addAction("Log Viewer")
        log_action.setToolTip(
            "Browse kernel console history across sessions on a timeline "
            "(eco.logs.widget) -- reopening replaces the previous viewer"
        )
        log_action.triggered.connect(self._open_log_viewer)

        menu.addSeparator()
        tabify_action = menu.addAction("Combine Panels into Tabs...")
        tabify_action.setToolTip(
            "Stack two or more open panels (Console, Namespace, or any open "
            "device widget) together as tabs in one spot -- the same thing "
            "dragging one panel's title bar onto another's does, just "
            "without needing to land the drop exactly on the target's "
            "title bar. Drag a tab off the tab bar afterwards to split it "
            "back out."
        )
        tabify_action.triggered.connect(self._on_combine_into_tabs)

    def _open_dock_panels(self):
        """name -> QDockWidget for every currently open, dockable panel
        (Console, Namespace launcher, and each opened device widget) --
        the candidates offered by "Combine Panels into Tabs..."."""
        panels = {}
        if self._console_dock is not None:
            panels["Console"] = self._console_dock
        if self._launcher_dock is not None:
            panels["Namespace"] = self._launcher_dock
        for dock in self._widget_docks:
            panels[dock.windowTitle()] = dock
        return panels

    def _on_combine_into_tabs(self):
        panels = self._open_dock_panels()
        if len(panels) < 2:
            QtWidgets.QMessageBox.information(
                self.window,
                "Combine Panels into Tabs",
                "Need at least two open panels (Console, Namespace, or a device "
                "widget) to combine.",
            )
            return

        dialog = QtWidgets.QDialog(self.window)
        dialog.setWindowTitle("Combine Panels into Tabs")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel("Select two or more panels to stack together as tabs:"))
        checkboxes = {}
        for name in panels:
            checkbox = QtWidgets.QCheckBox(name)
            layout.addWidget(checkbox)
            checkboxes[name] = checkbox
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        selected = [panels[name] for name, checkbox in checkboxes.items() if checkbox.isChecked()]
        if len(selected) < 2:
            return
        first, *rest = selected
        for dock in rest:
            self.window.tabifyDockWidget(first, dock)
        first.show()
        first.raise_()

    def _open_log_viewer(self):
        """eco.logs.widget(prefer="qt") -- see that module for what it
        shows (kernel console history merged across sessions, on a
        timeline). Kept referenced on self._log_viewer, both so it isn't
        garbage-collected out from under the user and so a second click
        replaces (rather than piles up alongside) the previous one."""
        if self._log_viewer is not None:
            try:
                self._log_viewer.stop()
            except Exception:
                logger.exception("closing the previous log viewer failed")
            self._log_viewer = None
        import eco.logs

        self._log_viewer = eco.logs.widget(prefer="qt")

    def _build_no_console_placeholder(self):
        """Central widget used instead of a console when with_console=False
        -- just an explanatory label, so the window isn't a confusing blank
        rectangle. The Namespace panel (a dock, added separately) is the
        actually-useful content in this mode. Set as the QMainWindow's
        *central* widget (unlike every dock, this reserves a fixed central
        area no matter what) purely because there's nothing else -- yet --
        to give that space to; the moment a real dock claims it (the first
        opened device widget, see _dock_widget_object), this is torn back
        out via QMainWindow.takeCentralWidget() so the docks get the full
        client area instead, exactly as if with_console had been True and
        no central widget had ever been set (see _build_window's console
        branch for why that's what "no central widget" gets you)."""
        label = QtWidgets.QLabel(
            "No embedded console (with_console=False).\n\n"
            "Use the Namespace panel to browse and open device widgets --\n"
            "it doesn't need a console (see Assembly.widget()).\n\n"
            "Run Python directly in the terminal you started this from."
        )
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet("color: palette(mid); padding: 24px;")
        return label

    def _build_console(self):
        from eco.widgets.console_kernel import (
            build_console_widget,
            build_inprocess_kernel,
            build_subprocess_kernel,
            can_use_inprocess_kernel,
        )

        linked = False
        startup_code = None
        if can_use_inprocess_kernel():
            # in-process is safe here (nothing else in this process holds
            # IPython's InteractiveShell singleton) regardless of
            # link_terminal -- link_terminal only controls whether we
            # *share* a namespace, not which kernel flavour we can use.
            shared_user_ns = self._terminal_user_ns() if self.link_terminal else None
            push_vars = None
            if shared_user_ns is not None:
                linked = True
                if self.namespace is not None and "namespace" not in shared_user_ns:
                    shared_user_ns["namespace"] = self.namespace
            elif self.namespace is not None:
                # Every bare top-level name eco's shell UI would give you
                # (mono, att, ... -- see build_namespace_vars's docstring
                # for why a bare `namespace` variable alone isn't enough),
                # plus `namespace` itself pinned to *this* app's own
                # namespace object (normally the same object anyway, but
                # explicit in case a caller passed a different one in).
                push_vars = build_namespace_vars(scope=self.scope, lazy=self.lazy)
                push_vars["namespace"] = self.namespace
            self._kernel_manager, self._kernel_client, self._kernel_session = build_inprocess_kernel(
                kind="desktop", label=self.scope, shared_user_ns=shared_user_ns, push_vars=push_vars
            )
            banner_extra = (
                "linked to the calling terminal's own namespace -- anything set "
                "here or there is visible in both.\n"
                if linked
                else ""
            )
        else:
            # can_use_inprocess_kernel() said no: this process already has
            # an incompatible IPython shell running (the common real-world
            # case -- eco.start_desktop() called from an `ipython`
            # terminal, which is exactly what raised MultipleInstanceError
            # before this fallback existed). See
            # eco.widgets.console_kernel's module docstring for why an
            # in-process kernel isn't possible here. startup_code is run
            # through the console widget itself, below, once its channels
            # are actually subscribed -- not here (see
            # build_subprocess_kernel's docstring for why).
            # Mirrors startup_inline.py's own two lines exactly (import
            # eco.<scope> as <scope>; from eco.<scope> import *) so a
            # subprocess console's namespace matches the shell UI 1:1 --
            # bare names (mono, att, ...) included, not just a `namespace`
            # variable (see build_namespace_vars's docstring for why the
            # latter alone isn't enough).
            if self.scope:
                lazy_line = "ecocnf.startup_lazy = True\n" if self.lazy else ""
                startup_code = (
                    "from eco import ecocnf\n"
                    + lazy_line
                    + f"import eco.{self.scope} as {self.scope}\n"
                    f"from eco.{self.scope} import *\n"
                )
            # else: no scope given -- plain console, nothing to preload
            # (startup_code stays None, set above)
            self._kernel_manager, self._kernel_client, self._kernel_session = build_subprocess_kernel(
                kind="desktop", label=self.scope
            )
            if self.link_terminal:
                banner_extra = (
                    "wanted to link to your terminal's namespace, but this process "
                    "already has a running IPython shell, so a second in-process "
                    "kernel isn't possible -- opened an independent kernel instead, "
                    "rebuilding the same namespace fresh (a variable you set in one "
                    "console won't appear in the other).\n"
                )
            else:
                banner_extra = "an independent kernel, running in its own process.\n"

        banner = (
            "eco desktop\n"
            + banner_extra
            + "'namespace' is your scope's namespace -- use the Namespace panel "
            "(left) to browse and open device widgets, or address it directly, "
            "e.g. namespace.some_device.widget()\n"
        )
        self._console = build_console_widget(
            self._kernel_manager,
            self._kernel_client,
            self._kernel_session,
            banner=banner,
            startup_code=startup_code,
        )

    def _on_console_dock_closed(self):
        """Runs when the Console dock is closed via its own title-bar X
        (see _ManagedDockWidget/_build_console's dock.setFeatures). Unlike
        the Namespace panel (see _on_namespace_dock_closed), this really
        does stop the kernel -- there's nothing useful left running once
        the console UI that was talking to it is gone, and freeing that
        kernel (a real process or thread) is exactly the point for anyone
        closing it on purpose (e.g. trimming a startup-script dashboard
        down to just its device widgets). Not reopenable afterwards short
        of restarting the desktop -- there's no "new console" action (a
        reasonable follow-up, not attempted here)."""
        from eco.widgets.console_kernel import stop_kernel

        stop_kernel(self._kernel_manager, self._kernel_client, self._kernel_session)
        self._kernel_manager = None
        self._kernel_client = None
        self._kernel_session = None
        self._console = None
        dock = self._console_dock
        self._console_dock = None
        if dock is not None:
            self.window.removeDockWidget(dock)
            dock.setParent(None)
            dock.deleteLater()

    def _on_namespace_dock_closed(self):
        """Runs when the Namespace dock is closed via its own title-bar X.
        Unlike the Console (see _on_console_dock_closed), this only hides
        the browsing UI -- self._launcher itself (its live-refresh timer,
        its open_by_name) is deliberately kept alive, reparented onto the
        main window instead of deleted along with the dock, so nothing that
        depends on it stops working just because the panel isn't visible
        anymore: a device widget already docked keeps polling exactly as
        before, and load_workspace's reopen-by-name (or any other
        programmatic open_by_name call) still works headlessly."""
        dock = self._launcher_dock
        self._launcher_dock = None
        if dock is not None:
            if self._launcher is not None:
                self._launcher.setParent(self.window)
                self._launcher.hide()
            self.window.removeDockWidget(dock)
            dock.setParent(None)
            dock.deleteLater()

    def _terminal_user_ns(self):
        """The calling IPython session's own user namespace dict, if
        there is one -- None if called from a plain script (nothing to
        share) or if no running IPython session was found."""
        try:
            from IPython import get_ipython

            ip = get_ipython()
        except Exception:
            ip = None
        if ip is None or not hasattr(ip, "user_ns"):
            return None
        return ip.user_ns

    #: opened-widget docks are tiled this many across before wrapping to a
    #: new row -- same grid-tiling approach as eco.widgets.dashboard_qt.
    #: Dashboard (see its class docstring for why tiling, not tabbing)
    WIDGET_DOCK_GRID_COLUMNS = 2

    def _open_widget(self, name):
        """Open `namespace.<name>`'s widget directly -- not through the
        console at all. See the module docstring ("WHY OPENED WIDGETS ARE
        CALLED DIRECTLY, NOT THROUGH THE CONSOLE") for why this is both
        simpler and more correct than the console-command route it
        replaced. The result is docked as a new tile in this window (see
        _dock_widget_object) -- calling the same `.widget()` from a plain
        terminal, with no desktop window to dock into, is unaffected and
        stays a plain standalone window, exactly as before."""
        if name not in self._opened_names:
            self._opened_names.append(name)
        try:
            item = _resolve_namespace_item(self.namespace, name)
            widget_obj = item.widget()
        except Exception:
            logger.exception("opening %r's widget failed", name)
            return
        # Record it as a timeline entry alongside console input/output --
        # a bare name, not `namespace.<name>` (which, per build_namespace's
        # own docstring, doesn't actually resolve -- Namespace.append_obj
        # writes devices onto the scope module, not the Namespace
        # instance): `<name>.widget()` is exactly what build_namespace_vars
        # already made available as a bare name in this console, so it's
        # directly runnable if copy-pasted or included via "copy selected
        # as script" (see eco.widgets.log_timeline_qt).
        if self._kernel_session is not None:
            self._kernel_session.log_widget_control(f"{name}.widget()")
        self._dock_widget_object(name, widget_obj)

    def _dock_widget_object(self, name, widget_obj):
        """Reparent `widget_obj`'s already-built top-level window into a
        new dock tile in this desktop window, instead of leaving it as its
        own separate window. Works for anything following eco's own Qt
        widget-wrapper convention -- a `.window` (QWidget/QMainWindow) plus
        a `.stop()` method, which DisplayQt/AxisPTZStreamQt/
        CamServerStreamQt/... all already follow -- and is a deliberate
        no-op (leaves today's plain-standalone-window behaviour alone) for
        anything that doesn't fit that shape, rather than guessing.

        The dock stays movable/floatable: drag it out, or use its title
        bar's own float button, to pop it back into its own window at any
        time -- standard QDockWidget behaviour, nothing special done here
        for it. Closing the dock -- via its title bar's X, or via the
        widget's own internal "Close" button (both routed through the same
        wrapped `widget_obj.stop`, see below) -- stops the underlying
        widget's polling and tears the dock tile down, so nothing keeps
        running (or sits as an empty shell) once it's gone.

        Also tags `widget_obj._eco_container = self` once docked, and is
        reachable under the public alias `host_widget` -- lets anything
        `widget_obj` spawns internally check `getattr(self, "_eco_container",
        None)` and dock alongside it here too, instead of always popping a
        bare standalone window (see DisplayQt._open_child_window,
        AxisPTZStreamQt._open_settings).
        """
        inner = getattr(widget_obj, "window", None)
        if not isinstance(inner, QtWidgets.QWidget):
            return  # doesn't follow the .window convention -- leave as-is

        # hide before the event loop gets a chance to actually paint it as
        # a separate top-level window, so there's no visible flash between
        # "shown standalone" and "reparented into a dock"
        inner.hide()

        # Both ways a docked widget can be closed -- the dock's own
        # title-bar X (via _ManagedDockWidget.closeEvent below) and the
        # widget's own internal "Close" button (which just calls
        # widget_obj.stop(), same as DisplayQt.close_btn -- see
        # display_qt.py) -- need to converge on tearing the *dock* down
        # too, not just stopping the widget's polling. Wrapping
        # widget_obj.stop itself is what makes both paths funnel through
        # one idempotent teardown, since both already call it.
        original_stop = getattr(widget_obj, "stop", None)
        removed = {"done": False}

        def _teardown():
            if removed["done"]:
                return
            removed["done"] = True
            if dock in self._widget_docks:
                self._widget_docks.remove(dock)
            self.window.removeDockWidget(dock)
            # removeDockWidget only detaches it from the dock-area layout
            # and hides it -- per its own docs, it deliberately leaves the
            # widget's Qt parent alone (so callers can reparent it
            # elsewhere). Since nothing else wants it, clear the parent
            # too, so it's actually gone rather than a hidden leftover
            # child of the main window.
            dock.setParent(None)
            dock.deleteLater()

        def _stop_and_teardown():
            try:
                if callable(original_stop):
                    original_stop()
            except Exception:
                logger.exception("stopping %r's widget failed", name)
            finally:
                _teardown()

        if callable(original_stop):
            widget_obj.stop = _stop_and_teardown

        dock = _ManagedDockWidget(name, _stop_and_teardown, parent=self.window)
        dock.setObjectName(
            _dock_object_name(f"widget_{name}", [d.objectName() for d in self._widget_docks])
        )
        dock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetClosable
            | QtWidgets.QDockWidget.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFloatable
        )
        dock.setWidget(inner)

        if self._no_console_placeholder is not None:
            # First real dock ever claiming this window's content area --
            # the "no console" placeholder (see _build_no_console_placeholder)
            # has done its job (saying why the window wasn't blank) and would
            # otherwise sit there forever as dead weight. takeCentralWidget()
            # -- as opposed to swapping in an empty QWidget -- is what
            # actually gives the freed space to the docks instead of merely
            # shrinking a still-reserved central region (see
            # _build_window's console branch, which never sets a central
            # widget at all for exactly this reason).
            self.window.takeCentralWidget()
            self._no_console_placeholder.deleteLater()
            self._no_console_placeholder = None

        row, col = divmod(len(self._widget_docks), self.WIDGET_DOCK_GRID_COLUMNS)
        if not self._widget_docks:
            self.window.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        elif col == 0:
            anchor = self._widget_docks[(row - 1) * self.WIDGET_DOCK_GRID_COLUMNS]
            self.window.splitDockWidget(anchor, dock, QtCore.Qt.Vertical)
        else:
            self.window.splitDockWidget(self._widget_docks[-1], dock, QtCore.Qt.Horizontal)

        self._widget_docks.append(dock)
        inner.show()
        dock.show()
        dock.raise_()

        # Tag the widget with the container that just hosted it, so
        # anything IT spawns internally (e.g. DisplayQt._open_child_window,
        # AxisPTZStreamQt._open_settings) can dock alongside it here too
        # instead of always popping a bare standalone window -- see those
        # call sites and host_widget below.
        widget_obj._eco_container = self

    # public alias: nested call sites (e.g. display_qt.py, camera_stream_qt.py)
    # reach a parent's container via `getattr(parent, "_eco_container", None)`
    # and then call `.host_widget(name, child)` on it -- same method as
    # _open_widget's own top-level docking, just under a name that isn't
    # private to this module.
    host_widget = _dock_widget_object

    # -- workspace persistence --------------------------------------------

    def save_workspace(self, path=None):
        """Write the current dock layout (QMainWindow geometry/state) and
        the list of currently-open namespace entries to `path` (default:
        DEFAULT_WORKSPACE_FILE). Called automatically when the window
        closes; also reachable via the Workspace menu's "Save Workspace
        Now" for a mid-session checkpoint. Returns True if it actually
        wrote a file (False if there's no window yet to save)."""
        if self.window is None:
            return False
        path = Path(path) if path else DEFAULT_WORKSPACE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "geometry": _qbytearray_to_str(self.window.saveGeometry()),
            "state": _qbytearray_to_str(self.window.saveState()),
            "opened_names": list(self._opened_names),
        }
        path.write_text(json.dumps(data))
        return True

    def load_workspace(self, path=None):
        """Restore a previously eco.widgets.desktop_app-saved workspace:
        the dock layout, then reopen every entry that was open (each via
        the same initialized-open-immediately / lazy-init-then-open path
        as clicking it in the Namespace panel would). Only ever called
        explicitly (the Workspace menu's "Reload Last Workspace", or
        directly) -- a fresh window never auto-restores. Returns True if
        a workspace file was found and applied."""
        path = Path(path) if path else DEFAULT_WORKSPACE_FILE
        if not path.exists():
            print(f"eco desktop: no saved workspace at {path}")
            return False
        try:
            data = json.loads(path.read_text())
        except Exception:
            logger.exception("failed to read workspace file %s", path)
            return False

        if self.window is not None:
            if data.get("geometry"):
                self.window.restoreGeometry(_str_to_qbytearray(data["geometry"]))
            if data.get("state"):
                self.window.restoreState(_str_to_qbytearray(data["state"]))

        if self._launcher is not None:
            for name in data.get("opened_names", []):
                self._launcher.open_by_name(name)
        return True

    def save_startup_script(self, sh_path=None):
        """Write a standalone, executable shell script that relaunches
        `eco desktop` with only THIS session's currently-open namespace
        entries reopened -- everything else in the namespace stays
        untouched/lazy (see -l below), so the result is a fast, minimal
        "dashboard" for just those components instead of the full
        namespace. Prompts for where to save (a file dialog) if `sh_path`
        isn't given, same as any other "Save As" action. Writes a
        companion <name>.json workspace file next to the script (the same
        format/mechanism as save_workspace) and points the script at it
        via --workspace. Returns the script's path if written, None if
        the save dialog was cancelled or there's no window yet to save
        from."""
        if self.window is None:
            return None
        if sh_path is None:
            path_str, _ = QtWidgets.QFileDialog.getSaveFileName(
                self.window,
                "Save Startup Script",
                str(Path.home() / "eco_dashboard.sh"),
                "Shell scripts (*.sh)",
            )
            if not path_str:
                return None
            sh_path = Path(path_str)
        else:
            sh_path = Path(sh_path)
        if sh_path.suffix != ".sh":
            sh_path = sh_path.with_suffix(".sh")

        workspace_path = sh_path.with_suffix(".json")
        self.save_workspace(workspace_path)

        scope_arg = "-s {} ".format(self.scope) if self.scope else ""
        lazy_flag = "-l" if self.lazy else "--no-lazy"
        # Reflects this session's CURRENT state, not just the with_console/
        # with_namespace_panel it was constructed with -- e.g. closing the
        # Console dock mid-session (see _on_console_dock_closed) now makes
        # the regenerated script skip it too, matching what "Save Startup
        # Script" is for: a minimal relaunch of exactly what's in front of
        # you right now, not what you started with.
        console_flag = "" if self._console_dock is not None else " --no-console"
        namespace_panel_flag = (
            " --no-namespace-panel"
            if self.namespace is not None and self._launcher_dock is None
            else ""
        )
        script = (
            "#!/bin/bash\n"
            "# Auto-generated by eco desktop's Workspace menu (Save Startup\n"
            "# Script...). Relaunches only the components that were open here --\n"
            "# lazy loading (see -l/--no-lazy below) means nothing else in the\n"
            "# namespace gets touched, so this is a fast, minimal dashboard rather\n"
            "# than the full namespace. Edit freely; re-running the menu action\n"
            "# overwrites both this file and its companion .json workspace file.\n"
            'exec eco desktop {}{}{}{} --workspace "{}" "$@"\n'.format(
                scope_arg, lazy_flag, console_flag, namespace_panel_flag, workspace_path
            )
        )
        sh_path.write_text(script)
        sh_path.chmod(sh_path.stat().st_mode | 0o111)  # +x, keeping existing r/w bits
        return sh_path

    def _autosave_workspace(self):
        self.save_workspace()

    # -- lifecycle (mirrors CamServerPanelQt/CamServerStreamQt) -----------

    def run(self, workspace=None):
        """Build the window (if not already built) and block in
        QApplication.exec_() until something quits the app. `workspace`,
        if given, is loaded (see load_workspace) right after the window
        exists but before exec_() -- callers that need this (the CLI's
        --workspace) must go through here rather than calling
        _build_window() themselves first: doing that separately creates
        the QApplication as a side effect, so by the time run() got to
        its own "did I create the app" check below it would already be
        False, and the exec_() call -- along with everything past it --
        would be skipped entirely. Confirmed for real: `eco desktop`
        opened its window and then exited immediately, before the CLI's
        --workspace support restructured this into two separate calls."""
        app = QtWidgets.QApplication.instance()
        created_app = app is None
        if created_app:
            app = QtWidgets.QApplication([])
        if self.window is None:
            self._build_window()
        if workspace:
            self.load_workspace(workspace)
        if created_app:
            # This call blocks in app.exec_() below until something quits
            # the app -- but WA_QuitOnClose is set False in _build_window
            # (needed so closing the window from *inside* an existing
            # IPython session, via start()'s non-blocking path, doesn't
            # kill that whole session), which also disables Qt's normal
            # "last window closed -> auto quit" behaviour. Without this
            # flag, _on_window_closing() has no way to know it's safe (and
            # necessary) to quit here: closing the window via its native X
            # button would leave app.exec_() blocked forever with no
            # visible window, hanging the whole process/terminal.
            self._owns_event_loop = True
            app.exec_()

    def start(self):
        """Non-blocking when called from an interactive IPython session
        (reuses/enables its Qt event-loop integration, same pattern as
        eco.widgets.camserver_stream_qt.CamServerStreamQt.start -- this is
        what eco.start_desktop() relies on to open the window without
        freezing the calling terminal); otherwise (e.g. the "--ui desktop"
        CLI entry point below, a plain script) falls back to run(),
        blocking until the window closes."""
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
                f"eco desktop: a different GUI event loop ('{active}') is already "
                "active in this IPython session, so the window can't be pumped "
                "non-blockingly alongside it. Showing it in blocking mode instead."
            )
            self.run()
            return

        self._build_window()

    def _on_window_closing(self):
        """Runs exactly once whenever this window is about to close -- by
        its own native close (X) button (via _DesktopMainWindow.closeEvent,
        which calls this as its `on_close`) or via an explicit stop() call
        below. Autosave first (needs self.window still intact -- it reads
        geometry/state off it), *then* tear down everything closing the
        window alone wouldn't: each docked device widget's own poll
        thread/kernel (_ManagedDockWidget.closeEvent only runs when *that*
        dock is closed individually, not when the whole window goes down
        with it still attached), and this window's own console kernel.
        Every step here is idempotent (empty-list iteration, None-safe
        stop_kernel/close), so this being reachable from both a native
        close and stop() calling it doesn't risk double-teardown issues.

        Finally, if run() started its own blocking QApplication.exec_()
        loop for this window (self._owns_event_loop), quit it -- otherwise
        WA_QuitOnClose=False (set in _build_window, needed for the
        embedded-in-an-existing-IPython-session case) means Qt's usual
        "last window closed -> quit" never fires, so exec_() -- and the
        whole `eco desktop` process/terminal -- would hang forever with no
        window left to close it from."""
        self._autosave_workspace()
        for dock in list(self._widget_docks):
            try:
                dock.close()
            except Exception:
                logger.exception("closing dock %r failed", dock.windowTitle())
        self._widget_docks = []

        if self._log_viewer is not None:
            try:
                self._log_viewer.stop()
            except Exception:
                logger.exception("closing the log viewer failed")
            self._log_viewer = None

        from eco.widgets.console_kernel import stop_kernel

        stop_kernel(self._kernel_manager, self._kernel_client, self._kernel_session)
        self._kernel_manager = None
        self._kernel_client = None
        self._kernel_session = None

        if self._owns_event_loop:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.quit()

    def stop(self):
        self._on_window_closing()
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None


def _main(argv=None):
    parser = argparse.ArgumentParser(description="eco desktop workbench")
    # No default: omitting --scope gives a plain console with no Namespace
    # launcher panel (see EcoDesktopApp/_build_window) instead of silently
    # defaulting to bernina -- mirrors eco_cli.py's `eco desktop` subcommand.
    parser.add_argument("--scope", default=None, help="scope name, e.g. bernina (default: none)")
    lazy_grp = parser.add_mutually_exclusive_group()
    lazy_grp.add_argument("--lazy", dest="lazy", action="store_true", default=True)
    lazy_grp.add_argument("--no-lazy", dest="lazy", action="store_false")
    console_grp = parser.add_mutually_exclusive_group()
    console_grp.add_argument("--console", dest="with_console", action="store_true", default=True)
    console_grp.add_argument("--no-console", dest="with_console", action="store_false")
    namespace_panel_grp = parser.add_mutually_exclusive_group()
    namespace_panel_grp.add_argument(
        "--namespace-panel", dest="with_namespace_panel", action="store_true", default=True
    )
    namespace_panel_grp.add_argument(
        "--no-namespace-panel",
        dest="with_namespace_panel",
        action="store_false",
        help="skip the dockable Namespace launcher panel -- the namespace itself "
             "is still built/usable (in the console, and to reopen a --workspace's "
             "widgets), just without the browsable panel taking up screen space",
    )
    parser.add_argument(
        "--theme",
        choices=["dark", "light", "none"],
        default="none",
        help="modern skin, or 'none' for native OS style (default: %(default)s) "
             "-- see eco.widgets.qt_theme",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        metavar="PATH",
        help="load this workspace file on startup (dock layout + which "
             "namespace entries to reopen) -- see the Workspace menu's "
             "'Save Startup Script...', which generates a command exactly "
             "like this one pointed at its own saved workspace file",
    )
    args = parser.parse_args(argv)
    theme = None if args.theme == "none" else args.theme

    namespace = build_namespace(scope=args.scope, lazy=args.lazy) if args.scope else None
    # a fresh top-level process (no calling terminal to link to --
    # link_terminal would no-op here anyway since get_ipython() is None,
    # but False is the honest/explicit statement of intent). auto_start=False
    # since start() would go straight from construction into run()'s
    # blocking exec_() with no way to pass --workspace through -- run()
    # itself takes it instead (see run()'s docstring for why building the
    # window and entering exec_() must stay in that one call, not split
    # across two).
    app = EcoDesktopApp(
        namespace,
        theme=theme,
        link_terminal=False,
        auto_start=False,
        scope=args.scope,
        lazy=args.lazy,
        with_console=args.with_console,
        with_namespace_panel=args.with_namespace_panel,
    )
    app.run(workspace=args.workspace)


if __name__ == "__main__":
    _main()
