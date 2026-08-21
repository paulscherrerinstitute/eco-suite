"""
Qt launcher/manager panel for running several cam_server live viewers
(eco.widgets.camserver_stream_qt.CamServerStreamQt) side by side, started
from an eco session -- a python/Qt-native analogue of pshell's "Screen
Panel", which hosts several CamServerViewer instances in one frame.

Layout: a QMainWindow with each viewer held in a QDockWidget. This is
Qt's native docking system (the same drag-a-title-bar-to-tab-it-alongside-
another, or drop-it-on-an-edge-to-tile-it-beside-others paradigm used by
e.g. VS Code) -- dragging, tabbing, tiling, floating and layout
persistence all come from Qt itself, not from anything hand-rolled here.
New viewers default to arriving as a tab alongside the most recently
added one; drag a tab's title elsewhere to split it into its own tile.

Process model: each viewer runs in its own OS process (``python -m
eco.widgets.camserver_stream_qt ... --embed``), so a bad connection, a
slow/blocked cam_server call, or a Qt/zmq crash in one viewer can never
take down another viewer, this panel, or your eco session. This is the
deliberate difference from pshell's own Screen Panel, which hosts all its
CamServerViewer instances as tabs/panels inside a single JVM/process.

Each viewer subprocess creates its own frameless top-level window and
prints its native window id ("WINID <id>") on stdout once shown; this
panel process picks that up and embeds the foreign window into its
QDockWidget via Qt's ``QWindow.fromWinId`` + ``createWindowContainer``
(X11 window reparenting -- requires a real X11 display). If a subprocess
doesn't report a window id within EMBED_TIMEOUT_S seconds (embedding
unsupported on this platform/WM, or the subprocess is still stuck in the
slow first-import of `eco`/`cam_server`), its dock is left showing a
"running as a separate window" note instead -- the viewer still works,
it just isn't visually merged into this panel.

Usage from an eco/IPython session::

    from eco.widgets.camserver_panel_qt import make_camserver_panel_qt
    panel = make_camserver_panel_qt()   # opens the panel, non-blocking
    # "+ Add viewer" lets you pick a running camera or pipeline by name
    # (cam_server/PipelineClient name lists, Bernina-prefixed names --
    # SARES/SAROP/SLAAR/SARFE -- sorted first); "+ Add demo (color)" adds
    # a synthetic color test stream with no cam_server dependency, useful
    # to try the panel out (e.g. to see several simultaneous "screens"
    # tabbed/tiled together) without occupying real cam_server instances.

TESTING NOTE: the process-spawn/handshake/isolation logic here is
exercised in tests/test_camserver_panel_qt.py using each viewer's --kind
demo source (no cam_server/bsread/network dependency), including killing
one viewer subprocess and confirming the others (and their dock tabs) are
unaffected. The actual visual X11 embedding step (createWindowContainer)
and the drag-to-tab/drag-to-tile *interaction* could only be smoke-tested
under an offscreen Qt platform from the sandboxed environment this was
written in (which itself can't do foreign-window embedding, by design --
Qt reports as much, and the code falls back to the floating-window note,
which is what's actually verified here). Try the drag/drop/tab/tile
behavior and the embedding on a real control-room X11 display before
relying on it day to day.
"""
import logging
import os
import sys

from qtpy import QtCore, QtGui, QtWidgets

import eco
from eco.widgets.camserver_stream_qt import BERNINA_PREFIXES

logger = logging.getLogger(__name__)

_app_ref = None


def _child_process_environment():
    """Environment for a viewer subprocess: this process's own, plus the
    directory containing the *actual* `eco` package this code is running
    from prepended to PYTHONPATH. Without this, a viewer spawned via
    QProcess can fail with "No module named 'eco.widgets'" (or silently
    import a different/older eco) if this session's eco was put on
    sys.path at interpreter-runtime -- e.g. by a dev-checkout IPython
    startup script -- rather than via PYTHONPATH or a .pth file; a freshly
    spawned child only inherits environment variables, never the parent's
    live in-memory sys.path."""
    eco_root = os.path.dirname(os.path.dirname(os.path.abspath(eco.__file__)))
    env = QtCore.QProcessEnvironment.systemEnvironment()
    existing = env.value("PYTHONPATH", "")
    env.insert("PYTHONPATH", os.pathsep.join([eco_root, existing]) if existing else eco_root)
    return env

# Must comfortably exceed eco's own known-slow first import inside the
# subprocess (~10-15s, see feedback_no_full_eco_import in project memory)
# -- a shorter value routinely fires before a perfectly healthy subprocess
# has even finished importing, marking its dock "resolved" as a floating
# window prematurely (harmless on its own since on_finished's
# truly_embedded check still catches a subsequent crash, but pointless
# churn for the common case of nothing being wrong).
EMBED_TIMEOUT_S = 30.0


def sorted_names(names, prefixes=BERNINA_PREFIXES):
    """Bernina-area names (per `prefixes`) first (alphabetical), then
    everything else (alphabetical). Pure list logic, unit tested
    independently of the (network-dependent) REST calls that produce the
    raw name list."""
    bernina = sorted(n for n in names if n.startswith(prefixes))
    other = sorted(n for n in names if not n.startswith(prefixes))
    return bernina + other


def _build_viewer_args(name, kind, pipeline_url=None, camera_url=None, demo_color=False, rate=None):
    """Pure: the argv (after the script name) used to launch one viewer
    subprocess -- factored out so the exact command line is unit
    testable without actually spawning a process."""
    args = [name, "--kind", kind, "--embed"]
    if kind == "demo" and demo_color:
        args.append("--demo-color")
    if pipeline_url:
        args += ["--pipeline-url", pipeline_url]
    if camera_url:
        args += ["--camera-url", camera_url]
    if rate is not None:
        args += ["--rate", str(rate)]
    return args


class _ViewerDock(QtWidgets.QDockWidget):
    """One viewer's dock widget: title bar (Qt-native, draggable to tab or
    tile) plus either the embedded viewer window or a fallback note.
    Closing it (its own [x], or programmatically) terminates the
    associated subprocess."""

    closed = QtCore.Signal(object)  # self

    def __init__(self, title, process, parent=None):
        super().__init__(title, parent)
        self.process = process
        # `embedded`: resolved one way or another (real embed, floating-
        # window fallback, or an error) -- stops on_stdout/check_timeout
        # from acting further. `truly_embedded`: specifically "a real X11
        # embed succeeded" -- kept separate so a subprocess that crashes
        # *after* the floating-window fallback already fired can still
        # have its dock replaced with the actual error instead of being
        # silently removed (see CamServerPanelQt._spawn_viewer.on_finished).
        self.embedded = False
        self.truly_embedded = False
        self.setFeatures(
            QtWidgets.QDockWidget.DockWidgetClosable
            | QtWidgets.QDockWidget.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFloatable
        )
        body = QtWidgets.QWidget()
        self._body_layout = QtWidgets.QVBoxLayout(body)
        self._body_layout.setContentsMargins(2, 2, 2, 2)
        self.setWidget(body)

    def _clear_body(self):
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def embed(self, win_id):
        self._clear_body()
        foreign = QtGui.QWindow.fromWinId(win_id)
        foreign.setFlags(QtCore.Qt.FramelessWindowHint)
        container = QtWidgets.QWidget.createWindowContainer(foreign, self.widget())
        container.setMinimumSize(200, 150)
        self._body_layout.addWidget(container)
        self.embedded = True
        self.truly_embedded = True

    def show_floating_note(self):
        self._clear_body()
        note = QtWidgets.QLabel("running as a separate window\n(embedding unavailable)")
        note.setAlignment(QtCore.Qt.AlignCenter)
        self._body_layout.addWidget(note)
        self.embedded = True

    def show_error(self, message):
        """The subprocess exited before a real embed ever succeeded --
        show why instead of the dock just silently vanishing (which is
        what removing it immediately used to look like), replacing
        whatever placeholder (e.g. the floating-window note) was showing."""
        self._clear_body()
        label = QtWidgets.QLabel(message)
        label.setWordWrap(True)
        label.setStyleSheet("color: #b00020;")
        label.setAlignment(QtCore.Qt.AlignCenter)
        self._body_layout.addWidget(label)
        self.embedded = True  # resolved (unsuccessfully) -- stop waiting on it

    def terminate(self):
        if self.process.state() != QtCore.QProcess.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(2000):
                self.process.kill()
                self.process.waitForFinished(2000)

    def closeEvent(self, event):
        self.closed.emit(self)
        super().closeEvent(event)


class _AddViewerDialog(QtWidgets.QDialog):
    def __init__(self, pipeline_url=None, camera_url=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add viewer")
        self.kind = "pipeline"
        self.name = None
        self._pipeline_url = pipeline_url
        self._camera_url = camera_url
        self._name_cache = {}

        layout = QtWidgets.QVBoxLayout(self)
        kind_row = QtWidgets.QHBoxLayout()
        kind_row.addWidget(QtWidgets.QLabel("Type:"))
        self._kind_combo = QtWidgets.QComboBox()
        self._kind_combo.addItems(["pipeline", "camera", "demo"])
        self._kind_combo.currentTextChanged.connect(self._on_kind_changed)
        kind_row.addWidget(self._kind_combo)
        layout.addLayout(kind_row)

        self._name_combo = QtWidgets.QComboBox()
        self._name_combo.setEditable(True)
        layout.addWidget(self._name_combo)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_kind_changed("pipeline")

    def _on_kind_changed(self, text):
        self.kind = text
        if text == "demo":
            self._name_combo.clear()
            self._name_combo.addItem("demo")
            return
        self._name_combo.clear()
        self._name_combo.addItems(sorted_names(self._get_names(text)))

    def _get_names(self, kind):
        """Names offered in the dropdown: currently *running* instances
        (server_info()["active_instances"]) -- the "screen panel" targets
        you can view immediately with a plain get_instance_stream(), no
        instantiation involved -- not the full config/template list
        (get_pipelines()/get_cameras()), most of which aren't running and
        would get created+started on demand by resolve_stream() the
        moment you picked them (see the module docstring). The combo box
        stays editable, so a config/template name can still be typed in
        by hand if you deliberately want that on-demand-create behavior."""
        if kind in self._name_cache:
            return self._name_cache[kind]
        names = []
        try:
            from cam_server import CamClient, PipelineClient

            if kind == "camera":
                client = CamClient(self._camera_url) if self._camera_url else CamClient()
            else:
                client = PipelineClient(self._pipeline_url) if self._pipeline_url else PipelineClient()
            names = list(client.get_server_info()["active_instances"].keys())
        except Exception as exc:
            logger.warning("could not list active %s instances: %s", kind, exc)
        self._name_cache[kind] = names
        return names

    def _on_accept(self):
        self.name = self._name_combo.currentText().strip()
        if self.name:
            self.accept()
        else:
            self.reject()


class CamServerPanelQt:
    """The panel window. See the module docstring for the architecture."""

    def __init__(self, pipeline_url=None, camera_url=None, theme=None, auto_start=True):
        self.pipeline_url = pipeline_url
        self.camera_url = camera_url
        self.theme = theme  # "dark" | "light" | None -- see eco.widgets.qt_theme
        self.window = None
        self._docks = []
        if auto_start:
            self.start()

    # -- window construction -------------------------------------------------

    def _build_window(self):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            _app_ref = QtWidgets.QApplication([])
        from eco.widgets.qt_theme import apply_modern_theme

        # also records the resolved theme in os.environ[ECO_QT_THEME], so
        # every viewer subprocess this panel spawns picks up the same
        # look automatically (each is a separate process -- see the
        # module docstring)
        apply_modern_theme(self.theme)

        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle("cam_server viewers")
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        self.window.setDockNestingEnabled(True)
        self.window.setDockOptions(
            QtWidgets.QMainWindow.AllowNestedDocks
            | QtWidgets.QMainWindow.AllowTabbedDocks
            | QtWidgets.QMainWindow.AnimatedDocks
        )

        toolbar = QtWidgets.QToolBar("Viewers")
        toolbar.setMovable(False)
        add_action = toolbar.addAction("+ Add viewer")
        add_action.triggered.connect(self._on_add_viewer)
        add_demo_action = toolbar.addAction("+ Add demo (color)")
        add_demo_action.triggered.connect(lambda: self.add_viewer("demo", "demo", demo_color=True))
        toolbar.addSeparator()
        close_all_action = toolbar.addAction("Close all")
        close_all_action.triggered.connect(self._close_all)
        self.window.addToolBar(toolbar)

        placeholder = QtWidgets.QLabel(
            'Use "+ Add viewer" to add a camera/pipeline. Drag a viewer\'s title '
            "bar onto another to tab them together, or drop it on an edge to "
            "tile them side by side -- standard Qt dock-widget behavior."
        )
        placeholder.setAlignment(QtCore.Qt.AlignCenter)
        placeholder.setWordWrap(True)
        self.window.setCentralWidget(placeholder)

        # close_calls_stop, not a plain destroyed.connect(setattr(...)):
        # each viewer dock is a separate subprocess (see the module
        # docstring), which Qt's own child-deletion never terminates on
        # its own -- closing via the window's native close (X) button
        # needs to actually run stop() (which calls dock.terminate() for
        # each), not just clear a reference. See
        # eco.widgets.qt_lifecycle's module docstring for the fuller why.
        from eco.widgets.qt_lifecycle import close_calls_stop

        close_calls_stop(self.window, self.stop)
        self.window.show()

    def _on_add_viewer(self):
        dialog = _AddViewerDialog(self.pipeline_url, self.camera_url, self.window)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.add_viewer(dialog.kind, dialog.name)

    def _close_all(self):
        for dock in list(self._docks):
            self._remove_dock(dock)

    # -- viewer lifecycle ------------------------------------------------

    def add_viewer(self, kind, name, demo_color=False):
        """Spawn one viewer subprocess and add it (or, if embedding
        doesn't pan out, a placeholder note) as a new dock -- tabbed
        alongside the most recently added dock by default. Returns the
        QProcess so callers (and tests) can track/terminate it directly."""
        argv = _build_viewer_args(
            name, kind, pipeline_url=self.pipeline_url, camera_url=self.camera_url, demo_color=demo_color
        )
        return self._spawn_viewer(name, argv)

    def _spawn_viewer(self, name, argv):
        process = QtCore.QProcess(self.window)
        process.setProcessChannelMode(QtCore.QProcess.SeparateChannels)

        dock = _ViewerDock(name, process, parent=self.window)
        dock.closed.connect(self._remove_dock)

        self.window.addDockWidget(QtCore.Qt.TopDockWidgetArea, dock)
        if self._docks:
            self.window.tabifyDockWidget(self._docks[-1], dock)
        self._docks.append(dock)
        dock.show()
        dock.raise_()

        stdout_buf = bytearray()
        stderr_lines = []

        def on_stdout():
            stdout_buf.extend(bytes(process.readAllStandardOutput()))
            while b"\n" in stdout_buf and not dock.embedded:
                line, _, rest = bytes(stdout_buf).partition(b"\n")
                del stdout_buf[: len(line) + 1]
                text = line.decode(errors="ignore").strip()
                if text.startswith("WINID "):
                    try:
                        win_id = int(text.split()[1])
                        dock.embed(win_id)
                    except Exception as exc:
                        logger.warning("failed to embed viewer window for %r: %s", name, exc)
                        if not dock.embedded:
                            dock.show_floating_note()

        def on_stderr():
            # previously discarded entirely -- a subprocess crash before it
            # ever reported a window id was invisible except as its dock
            # silently disappearing. Surface it both to this process's own
            # stderr (visible in the console the panel was started from)
            # and, via on_finished below, in the dock itself.
            chunk = bytes(process.readAllStandardError()).decode(errors="ignore")
            if chunk:
                sys.stderr.write(f"[camserver_panel_qt] viewer {name!r} stderr: {chunk}")
                sys.stderr.flush()
                stderr_lines.extend(chunk.splitlines())
                del stderr_lines[:-20]  # keep only the tail

        def on_finished(exit_code, _exit_status):
            if exit_code == 0 or dock.truly_embedded:
                # a normal exit, or a subprocess that really was embedded
                # (and later closed/crashed after successfully running) --
                # just clean up, same as before
                self._remove_dock(dock)
                return
            # exited with an error and a real embed never happened -- even
            # if the floating-window fallback already fired (eco's own
            # slow first import routinely takes longer than
            # EMBED_TIMEOUT_S, so that fallback can beat a crash that
            # follows shortly after), replace whatever placeholder is
            # showing with the actual reason instead of silently removing
            # the dock, which used to look like an unexplained
            # flash-and-vanish
            non_empty = [ln for ln in stderr_lines if ln.strip()]
            detail = non_empty[-1] if non_empty else f"exited with code {exit_code}, no output captured"
            dock.show_error(f"viewer process exited unexpectedly:\n{detail}")

        process.readyReadStandardOutput.connect(on_stdout)
        process.readyReadStandardError.connect(on_stderr)
        process.finished.connect(on_finished)

        process.setProcessEnvironment(_child_process_environment())
        process.start(sys.executable, ["-m", "eco.widgets.camserver_stream_qt", *argv])

        def check_timeout():
            if not dock.embedded and process.state() != QtCore.QProcess.NotRunning:
                dock.show_floating_note()

        QtCore.QTimer.singleShot(int(EMBED_TIMEOUT_S * 1000), check_timeout)
        return process

    def _remove_dock(self, dock):
        if dock not in self._docks:
            return
        self._docks.remove(dock)
        dock.terminate()
        self.window.removeDockWidget(dock)
        dock.deleteLater()

    # -- generic widget lifecycle (mirrors CamServerStreamQt/AxisPTZStreamQt) --

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
                f"eco widget: a different GUI event loop ('{active}') is already "
                "active in this IPython session, so the window can't be pumped "
                "non-blockingly alongside it. Showing it in blocking mode instead."
            )
            self.run()
            return

        self._build_window()

    def stop(self):
        for dock in list(self._docks):
            dock.terminate()
        self._docks = []
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None


def make_camserver_panel_qt(pipeline_url=None, camera_url=None, theme=None, auto_start=True):
    """Convenience factory, mirrors make_camserver_stream_qt's signature.
    theme: "dark" | "light" | None (native) -- see eco.widgets.qt_theme;
    propagates to every viewer subprocess the panel spawns."""
    return CamServerPanelQt(
        pipeline_url=pipeline_url, camera_url=camera_url, theme=theme, auto_start=auto_start
    )
