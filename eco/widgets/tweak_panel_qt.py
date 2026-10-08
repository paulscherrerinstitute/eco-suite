"""
Qt tweak panel for 1-4 axes: per axis neg/pos step, step *2 / /2, editable
step, go-to and reset-to fields; back-to-start and stop for all; and a
"keypress control" checkbox (on by default) that makes the panel take the same
keys as the terminal tweak and shows the key manual. Logic and key bindings
live in eco.widgets.tweak_panel.

Keys only reach the panel while it has keyboard focus (click on it); the
frame turns green while it does. Buttons never take focus, so a key always
means the bound action, except while typing in one of the number fields
(Enter applies the value and hands the keys back to the panel, Esc leaves the
field without applying).

Usage:
    from eco.widgets.tweak_panel import tweak_panel, IocTweakAxis
    tweak_panel([IocTweakAxis(mot)], backend="qt")
or through Tweak.widget() / an adjustable's ._widget_tweak().
"""

import threading

from qtpy import QtCore, QtWidgets

from eco.widgets.tweak_panel import _fmt

_open_panels = set()  # strong refs: a parentless widget is otherwise collected
_app_ref = None

_QT_ARROWS = {
    QtCore.Qt.Key_Left: "Left",
    QtCore.Qt.Key_Up: "Up",
    QtCore.Qt.Key_Down: "Down",
    QtCore.Qt.Key_Right: "Right",
}

_FRAME_ACTIVE = "QFrame#tweakFrame { border: 2px solid #2e9b45; border-radius: 4px; }"
_FRAME_IDLE = "QFrame#tweakFrame { border: 2px solid transparent; border-radius: 4px; }"


def normalize_key_event(event):
    """Qt key event -> eco.widgets.tweak_panel normalized key name, or None."""
    key = event.key()
    if key in _QT_ARROWS:
        name = _QT_ARROWS[key]
        if event.modifiers() & QtCore.Qt.ControlModifier:
            return "Ctrl+" + name
        return name
    if key == QtCore.Qt.Key_Escape:
        return "Escape"
    text = event.text()
    if len(text) == 1 and text.isprintable():
        return text.lower()
    return None


class _Bridge(QtCore.QObject):
    value = QtCore.Signal(int, str)
    message = QtCore.Signal(str)


class _ValueEdit(QtWidgets.QLineEdit):
    """Number field: Enter applies (via `applied`) and returns focus to the
    panel, Esc returns focus without applying."""

    applied = QtCore.Signal(float)

    def __init__(self, panel, width=90):
        super().__init__()
        self._panel = panel
        self.setFixedWidth(width)
        self.returnPressed.connect(self._apply)

    def _apply(self):
        try:
            value = float(self.text())
        except ValueError:
            self._panel._message(f"not a number: {self.text()!r}")
            return
        self.applied.emit(value)
        self._panel.setFocus()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self._panel.setFocus()
            return
        super().keyPressEvent(event)


class TweakPlotQt(QtWidgets.QWidget):
    """Live plot of a TweakRecorder (eco.widgets.tweak_recorder), with the
    matplotlib toolbar; redraws only when the recorder has new data."""

    def __init__(self, recorder, refresh_interval=0.5, parent=None):
        super().__init__(parent)
        from matplotlib.backends.backend_qtagg import (
            FigureCanvasQTAgg,
            NavigationToolbar2QT,
        )
        from matplotlib.figure import Figure

        from eco.widgets.tweak_recorder import figure_size

        self.recorder = recorder
        self.figure = Figure(figsize=figure_size(recorder), dpi=90)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setFocusPolicy(QtCore.Qt.NoFocus)
        toolbar = NavigationToolbar2QT(self.canvas, self)
        toolbar.setFocusPolicy(QtCore.Qt.NoFocus)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(toolbar)
        layout.addWidget(self.canvas)
        self._drawn_version = None
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(int(refresh_interval * 1000))
        self.refresh()

    def refresh(self, force=False):
        if not force and self._drawn_version == self.recorder.version:
            return
        from eco.widgets.tweak_recorder import draw_tweak_plot

        self._drawn_version = self.recorder.version
        draw_tweak_plot(self.figure, self.recorder)
        self.canvas.draw_idle()

    def closeEvent(self, event):
        self._timer.stop()
        _open_panels.discard(self)
        super().closeEvent(event)


class TweakPanelQt(QtWidgets.QFrame):
    def __init__(
        self,
        controller,
        title=None,
        poll_interval=0.2,
        keypress=True,
        recorder=None,
        parent=None,
    ):
        super().__init__(parent)
        self.controller = controller
        self.recorder = recorder
        self.setObjectName("tweakFrame")
        self.setWindowTitle(title or "tweak " + ", ".join(controller.names))
        self.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self._bridge = _Bridge()
        self._bridge.value.connect(self._set_value_label)
        self._bridge.message.connect(self._show_message)
        controller.notify = self._bridge.message.emit

        key_of = {action: key for key, action in controller.bindings.items()}

        main = QtWidgets.QHBoxLayout(self)
        outer = QtWidgets.QVBoxLayout()
        main.addLayout(outer)
        grid = QtWidgets.QGridLayout()
        for col, text in enumerate(
            ("axis", "current", "", "", "", "", "step", "go to", "reset to")
        ):
            if text:
                grid.addWidget(QtWidgets.QLabel(f"<b>{text}</b>"), 0, col)

        self._value_labels = []
        self._step_edits = []
        self._go_edits = []
        self._reset_edits = []
        for i, axis in enumerate(controller.axes):
            r = i + 1
            grid.addWidget(QtWidgets.QLabel(axis.name), r, 0)
            value_label = QtWidgets.QLabel("…")
            value_label.setMinimumWidth(100)
            value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            grid.addWidget(value_label, r, 1)
            self._value_labels.append(value_label)

            buttons = (
                ("◀", "neg dir", ("move", i, -1), lambda _=False, i=i: self._move(i, -1)),
                ("×2", "step*2", ("step", i, 2.0), lambda _=False, i=i: self._scale(i, 2.0)),
                ("÷2", "step/2", ("step", i, 0.5), lambda _=False, i=i: self._scale(i, 0.5)),
                ("▶", "pos dir", ("move", i, +1), lambda _=False, i=i: self._move(i, +1)),
            )
            for col, (text, tip, action, slot) in enumerate(buttons, start=2):
                btn = QtWidgets.QPushButton(text)
                btn.setFixedWidth(36)
                btn.setFocusPolicy(QtCore.Qt.NoFocus)
                key = key_of.get(action)
                btn.setToolTip(f"{tip} (key: {_key_label(key)})" if key else tip)
                btn.clicked.connect(slot)
                grid.addWidget(btn, r, col)

            step_edit = _ValueEdit(self, width=80)
            step_edit.setText(_fmt(axis.step))
            step_edit.applied.connect(lambda v, i=i: self._set_step(i, v))
            go_edit = _ValueEdit(self)
            go_edit.applied.connect(lambda v, i=i: self.controller.go(i, v))
            reset_edit = _ValueEdit(self)
            reset_edit.setToolTip("redefine the current position as this value")
            reset_edit.applied.connect(
                lambda v, i=i: self.controller.reset_current_value_to(i, v)
            )
            for col, edit in ((6, step_edit), (7, go_edit), (8, reset_edit)):
                grid.addWidget(edit, r, col)
            self._step_edits.append(step_edit)
            self._go_edits.append(go_edit)
            self._reset_edits.append(reset_edit)
        outer.addLayout(grid)

        bottom = QtWidgets.QHBoxLayout()
        start_btn = QtWidgets.QPushButton("Back to start")
        start_btn.setToolTip(
            "all axes back to "
            + ", ".join(_fmt(a.start_value) for a in controller.axes)
            + " (key: s)"
        )
        start_btn.clicked.connect(lambda _=False: self.controller.back_to_start())
        stop_btn = QtWidgets.QPushButton("Stop")
        stop_btn.setToolTip("stop all axes (key: Esc)")
        stop_btn.clicked.connect(lambda _=False: self.controller.stop())
        self.keypress_box = QtWidgets.QCheckBox("keypress control")
        self.keypress_box.setChecked(keypress)
        self.keypress_box.toggled.connect(self._keypress_toggled)
        for w in (start_btn, stop_btn, self.keypress_box):
            w.setFocusPolicy(QtCore.Qt.NoFocus)
            bottom.addWidget(w)
        bottom.addStretch(1)
        outer.addLayout(bottom)

        self.manual = QtWidgets.QLabel(_manual_html(controller.manual()))
        self.manual.setTextFormat(QtCore.Qt.RichText)
        outer.addWidget(self.manual)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        outer.addStretch(1)
        self._keypress_toggled(keypress)

        self.plot = None
        if recorder is not None:
            recorder.start()
            self.plot = TweakPlotQt(recorder)
            main.addWidget(self.plot, 1)

        self._stop_event = threading.Event()
        self._poll_interval = poll_interval
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    # --- actions ---
    def _move(self, i, sign):
        self.controller.move(i, sign)

    def _scale(self, i, factor):
        if self.controller.scale_step(i, factor):
            self._step_edits[i].setText(_fmt(self.controller.axes[i].step))

    def _set_step(self, i, value):
        if self.controller.set_step(i, value):
            self._step_edits[i].setText(_fmt(self.controller.axes[i].step))

    # --- keys ---
    def keypress_active(self):
        return self.keypress_box.isChecked()

    def _keypress_toggled(self, on):
        self.manual.setVisible(on)
        self._update_frame()
        if on:
            self.setFocus()

    def _update_frame(self):
        self.setStyleSheet(
            _FRAME_ACTIVE if self.keypress_active() and self.hasFocus() else _FRAME_IDLE
        )

    def focusInEvent(self, event):
        self._update_frame()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._update_frame()
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if not self.keypress_active():
            return super().keyPressEvent(event)
        key = normalize_key_event(event)
        action = self.controller.handle_key(key) if key else None
        if action is None:
            return super().keyPressEvent(event)
        kind = action[0]
        if kind == "step":
            self._step_edits[action[1]].setText(_fmt(self.controller.axes[action[1]].step))
        elif kind == "release":
            self.keypress_box.setChecked(False)
        elif kind in ("focus_go", "focus_reset"):
            edits = self._go_edits if kind == "focus_go" else self._reset_edits
            edit = edits[action[1]]
            edit.setText(self._value_labels[action[1]].text())
            edit.setFocus()
            edit.selectAll()
        event.accept()

    # --- values / messages ---
    def _poll_loop(self):
        while not self._stop_event.is_set():
            for i, axis in enumerate(self.controller.axes):
                try:
                    text = _fmt(axis.get_value())
                except Exception as exc:
                    text = f"error: {exc}"
                self._bridge.value.emit(i, text)
            self._stop_event.wait(self._poll_interval)

    def _set_value_label(self, i, text):
        label = self._value_labels[i]
        if label.text() != text:
            label.setText(text)

    def _message(self, text):
        self._bridge.message.emit(text)

    def _show_message(self, text):
        self.status.setText(text)
        QtCore.QTimer.singleShot(8000, lambda: self.status.setText(""))

    def closeEvent(self, event):
        self._stop_event.set()
        if self.recorder is not None:
            self.recorder.stop()
        _open_panels.discard(self)
        super().closeEvent(event)


def _key_label(key):
    return key.replace("Left", "←").replace("Right", "→").replace("Up", "↑").replace(
        "Down", "↓"
    ).replace("Ctrl+", "ctrl+")


def _manual_html(rows):
    body = "".join(
        f"<tr><td style='padding-right:12px'><b><tt>{keys}</tt></b></td><td>{meaning}</td></tr>"
        for keys, meaning in rows
    )
    return (
        "<table>" + body + "</table>"
        "<i>keys act while this panel has focus (green frame) - click it to give it focus</i>"
    )


def _ensure_qt_app():
    """A QApplication pumped by IPython's event loop integration if there is
    an IPython shell (enabling `%gui qt` if no loop is active yet). Returns
    True if the caller must run the event loop itself (plain python)."""
    global _app_ref
    from eco.widgets.tweak_recorder import display_available

    if QtWidgets.QApplication.instance() is None and not display_available():
        raise RuntimeError("no display available for a Qt window")
    try:
        from IPython import get_ipython

        ip = get_ipython()
    except Exception:
        ip = None
    if QtWidgets.QApplication.instance() is None:
        _app_ref = QtWidgets.QApplication([])
    if ip is None:
        return True
    if getattr(ip, "active_eventloop", None) is None and ip.__class__.__name__ != (
        "InProcessInteractiveShell"
    ):
        try:
            ip.enable_gui("qt")
        except Exception:
            pass
    return False


def show_tweak_plot_qt(recorder, title=None):
    """Standalone live-plot window (used by the terminal tweaks); never
    blocks -- it is pumped by IPython's event loop integration, also while
    the terminal tweak waits for keys."""
    _ensure_qt_app()
    window = TweakPlotQt(recorder)
    window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
    window.setWindowTitle(
        title
        or "tweak "
        + ", ".join(recorder.axis_names)
        + " - "
        + ", ".join(recorder.detector_names)
    )
    _open_panels.add(window)
    window.show()
    return window


def show_tweak_panel_qt(
    controller, title=None, poll_interval=0.2, keypress=True, recorder=None
):
    blocking = _ensure_qt_app()
    panel = TweakPanelQt(
        controller,
        title=title,
        poll_interval=poll_interval,
        keypress=keypress,
        recorder=recorder,
    )
    _open_panels.add(panel)
    panel.show()
    panel.raise_()
    panel.activateWindow()
    panel.setFocus()
    if blocking:
        QtWidgets.QApplication.instance().exec_()
    return panel
