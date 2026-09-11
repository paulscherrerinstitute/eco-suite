"""Reusable core for "spawn a Qt app as a separate OS process, capture its
native window id via a stdout handshake, and embed the foreign window into
a local Qt container via X11 reparenting" (QWindow.fromWinId +
createWindowContainer) -- extracted from
eco.widgets.camserver_panel_qt._ViewerDock/_spawn_viewer (the original,
still-primary multi-viewer user, refactored to build on this module) so a
single-viewer caller (eco.devices_general.cameras_swissfel's
separate_process=True camera screen panel, via
_spawn_separate_process_viewer/EmbeddedProcessWindow below) can reuse the
exact same spawn/handshake/embed/timeout/error-fallback logic without
depending on CamServerPanelQt's own "QMainWindow full of QDockWidgets"
structure.

Not camera-specific: nothing here knows about cam_server/pvnames/
pipelines -- callers hand in a full argv (`cmd`) and get a QProcess plus,
for EmbeddedProcessWindow, a plain .window/.stop() wrapper following eco's
usual Qt-widget-wrapper convention (DisplayQt/AxisPTZStreamQt/
CamServerStreamQt/...).
"""
import logging
import sys

from qtpy import QtCore, QtGui, QtWidgets

logger = logging.getLogger(__name__)

_app_ref = None

# Must comfortably exceed eco's own known-slow first import inside the
# subprocess (~10-15s, see feedback_no_full_eco_import in project memory)
# -- a shorter value routinely fires before a perfectly healthy subprocess
# has even finished importing, marking it "resolved" as a floating window
# prematurely. Moved here (from eco.widgets.camserver_panel_qt, which used
# to own this constant) as the one shared source of truth.
EMBED_TIMEOUT_S = 30.0


def child_process_environment():
    """Environment for a viewer subprocess: this process's own, plus the
    directory containing the *actual* `eco` package this code is running
    from prepended to PYTHONPATH. Without this, a viewer spawned via
    QProcess can fail with "No module named 'eco.widgets'" (or silently
    import a different/older eco) if this session's eco was put on
    sys.path at interpreter-runtime -- e.g. by a dev-checkout IPython
    startup script -- rather than via PYTHONPATH or a .pth file; a freshly
    spawned child only inherits environment variables, never the parent's
    live in-memory sys.path.

    Moved here verbatim from eco.widgets.camserver_panel_qt.
    _child_process_environment, which re-exports this function under that
    same name for backward compatibility."""
    import os

    import eco

    eco_root = os.path.dirname(os.path.dirname(os.path.abspath(eco.__file__)))
    env = QtCore.QProcessEnvironment.systemEnvironment()
    existing = env.value("PYTHONPATH", "")
    env.insert("PYTHONPATH", os.pathsep.join([eco_root, existing]) if existing else eco_root)
    return env


def clear_layout(layout):
    """Remove+delete every widget currently in `layout` -- shared by
    eco.widgets.camserver_panel_qt._ViewerDock's body and
    EmbeddedProcessWindow below."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()


def embed_foreign_window(win_id, layout, parent):
    """The actual X11-reparenting step: wrap `win_id` as a QWindow, strip
    its native decorations, and add it to `layout` via
    createWindowContainer. Shared by _ViewerDock.embed and
    EmbeddedProcessWindow.embed below. Returns the container widget."""
    foreign = QtGui.QWindow.fromWinId(win_id)
    foreign.setFlags(QtCore.Qt.FramelessWindowHint)
    container = QtWidgets.QWidget.createWindowContainer(foreign, parent)
    container.setMinimumSize(200, 150)
    layout.addWidget(container)
    return container


def spawn_and_embed(target, cmd, env=None, timeout_s=EMBED_TIMEOUT_S, parent=None, on_clean_finish=None):
    """Spawn `cmd` (a full argv list, e.g. [sys.executable, "-m",
    "eco.widgets.camserver_stream_qt", *args]) as a child QProcess, watch
    its stdout for the "WINID <id>" handshake line (see
    eco.widgets.camserver_stream_qt's --embed flag), and drive `target`
    accordingly:

    - a well-formed "WINID <id>" line within `timeout_s` -> target.embed(win_id)
    - nothing within `timeout_s` (still running) -> target.show_floating_note()
    - the process exits with a non-zero code before ever truly embedding
      -> target.show_error(message)
    - a clean exit (code 0), or exit after target.truly_embedded was
      already set -> on_clean_finish() is called, if given

    `target` must expose `.embedded` (bool), `.truly_embedded` (bool),
    `.embed(win_id)`, `.show_floating_note()`, `.show_error(message)` --
    exactly the small protocol eco.widgets.camserver_panel_qt._ViewerDock
    and EmbeddedProcessWindow (below) both implement.

    Returns the QProcess (so callers can track/terminate it directly)."""
    process = QtCore.QProcess(parent)
    process.setProcessChannelMode(QtCore.QProcess.SeparateChannels)

    stdout_buf = bytearray()
    stderr_lines = []

    def on_stdout():
        stdout_buf.extend(bytes(process.readAllStandardOutput()))
        while b"\n" in stdout_buf and not target.embedded:
            line, _, rest = bytes(stdout_buf).partition(b"\n")
            del stdout_buf[: len(line) + 1]
            text = line.decode(errors="ignore").strip()
            if text.startswith("WINID "):
                try:
                    win_id = int(text.split()[1])
                    target.embed(win_id)
                except Exception as exc:
                    logger.warning("failed to embed viewer window: %s", exc)
                    if not target.embedded:
                        target.show_floating_note()

    def on_stderr():
        # surfaced both to this process's own stderr (visible in the
        # console this was started from) and, via on_finished below, in
        # the target itself -- a subprocess crash before it ever reported
        # a window id would otherwise be invisible except as a silent
        # disappearance.
        chunk = bytes(process.readAllStandardError()).decode(errors="ignore")
        if chunk:
            sys.stderr.write(f"[subprocess_embed] viewer stderr: {chunk}")
            sys.stderr.flush()
            stderr_lines.extend(chunk.splitlines())
            del stderr_lines[:-20]  # keep only the tail

    def on_finished(exit_code, _exit_status):
        if exit_code == 0 or target.truly_embedded:
            if on_clean_finish is not None:
                on_clean_finish()
            return
        non_empty = [ln for ln in stderr_lines if ln.strip()]
        detail = non_empty[-1] if non_empty else f"exited with code {exit_code}, no output captured"
        target.show_error(f"viewer process exited unexpectedly:\n{detail}")

    process.readyReadStandardOutput.connect(on_stdout)
    process.readyReadStandardError.connect(on_stderr)
    process.finished.connect(on_finished)

    process.setProcessEnvironment(env if env is not None else child_process_environment())
    process.start(cmd[0], cmd[1:])

    def check_timeout():
        if not target.embedded and process.state() != QtCore.QProcess.NotRunning:
            target.show_floating_note()

    QtCore.QTimer.singleShot(int(timeout_s * 1000), check_timeout)
    return process


class EmbeddedProcessWindow:
    """.window/.stop()-shaped wrapper (eco's standard Qt-widget-wrapper
    convention -- DisplayQt/AxisPTZStreamQt/CamServerStreamQt/...) around
    ONE spawned+embedded (or floating/errored) subprocess viewer -- for a
    single embeddable window, as opposed to CamServerPanelQt's whole
    panel-of-many. `.window` is a plain QMainWindow (so a toolbar-carrying
    embedded subprocess still lays out sanely) holding either the truly-
    embedded foreign window, a "running separately" note, or an error
    message -- functionally one _ViewerDock's body, minus being a
    QDockWidget itself, so eco.elements.assembly.Assembly._maybe_dock
    (via EcoDesktopApp._dock_widget_object/host_widget) can reparent
    `.window` into ITS OWN QDockWidget when asked to, while a plain
    standalone `.widget()` call still gets a normal top-level window
    (shown here directly)."""

    def __init__(self, cmd, title="", env=None, timeout_s=EMBED_TIMEOUT_S):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            _app_ref = QtWidgets.QApplication([])

        self.embedded = False
        self.truly_embedded = False
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle(title)
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)

        body = QtWidgets.QWidget()
        self._layout = QtWidgets.QVBoxLayout(body)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self.window.setCentralWidget(body)
        self._body_widget = body

        self.process = spawn_and_embed(self, cmd, env=env, timeout_s=timeout_s)

        self.window.resize(780, 620)
        self.window.show()

    def embed(self, win_id):
        clear_layout(self._layout)
        embed_foreign_window(win_id, self._layout, self._body_widget)
        self.embedded = True
        self.truly_embedded = True

    def show_floating_note(self):
        clear_layout(self._layout)
        note = QtWidgets.QLabel("running as a separate window\n(embedding unavailable)")
        note.setAlignment(QtCore.Qt.AlignCenter)
        self._layout.addWidget(note)
        self.embedded = True

    def show_error(self, message):
        clear_layout(self._layout)
        label = QtWidgets.QLabel(message)
        label.setWordWrap(True)
        label.setStyleSheet("color: #b00020;")
        label.setAlignment(QtCore.Qt.AlignCenter)
        self._layout.addWidget(label)
        self.embedded = True

    def stop(self):
        if self.process is not None and self.process.state() != QtCore.QProcess.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(2000):
                self.process.kill()
                self.process.waitForFinished(2000)
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None
