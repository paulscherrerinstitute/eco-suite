"""Qt control panel for eco.feedback (under development).

    fb.qt_panel()                                   # in-process, on a manager
    FeedbackPanelQt(url="http://127.0.0.1:8010")    # against a server
    python -m eco.feedback panel URL --qt

One window: a table of all loops (state, value, output, rate), a settings
form and a trend plot for the selected loop, Enable / Disable / Disable all,
interlocks and alarms, and the server log. Matrix loops are listed but are
set up from the ipywidgets panel or the service for now.

Nothing in a slot talks to the client: a worker thread polls and leaves the
latest snapshot in an attribute, a QTimer on the main thread shows it, and
every write runs in its own short thread. That matters because the client
may be HTTP, and the in-process service reads hardware (eco.widgets'
rule: no blocking call in a slot). Follows eco's Qt widget convention
(``.window`` + ``.stop()``).
"""

from __future__ import annotations

import collections
import threading
import time

from qtpy import QtCore, QtWidgets

_app_ref = None  # a QApplication we created ourselves

COLUMNS = ["loop", "detector -> adjustable", "state", "value", "setpoint",
           "output", "Hz", "kp", "ki", "kd"]
SETTINGS = ["setpoint", "rate_hz", "kp", "ki", "kd", "alarm_after_s"]


def _fmt(v, spec=".5g"):
    if v is None:
        return "-"
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return str(v)


def _state(s):
    flags = [f for f, on in (("ON", s.get("enabled")), ("HELD", s.get("held")),
                             ("ALARM", s.get("alarmed")), ("ERROR", s.get("error"))) if on]
    return " ".join(flags) or "off"


class FeedbackPanelQt:
    POLL_MS = 200

    def __init__(self, url=None, api_key=None, client=None, refresh_s=1.0,
                 auto_start=True):
        if client is None:
            from .client import FeedbackClient

            client = FeedbackClient(url or "http://127.0.0.1:8010", api_key=api_key)
        self.client = client
        self.title = f"eco feedback  {url}" if url else "eco feedback"
        self.refresh_s = refresh_s
        self.window = None
        self._selected = None          # loop name shown in form/plot
        self._snapshot = None          # latest poll result, replaced whole
        self._snapshot_shown = None
        self._messages = collections.deque(maxlen=50)  # from write threads
        self._stopped = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        if auto_start:
            self.start()

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        try:
            from IPython import get_ipython

            shell = get_ipython()
            if shell is not None and getattr(shell, "active_eventloop", None) is None:
                shell.enable_gui("qt")
        except Exception:
            pass
        self._build_window()
        self._thread = threading.Thread(target=self._poll, daemon=True,
                                        name="feedback-qt-poll")
        self._thread.start()
        _PANELS.add(self)
        return self

    def stop(self, join=False):
        self._stopped.set()
        self._wake.set()
        if self.window is not None:
            self._timer.stop()
            window, self.window = self.window, None
            try:
                window.close()
            except RuntimeError:
                pass
        if join and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    # -- worker side (no Qt here) ------------------------------------------
    def _poll(self):
        from .core import _thread_init

        _thread_init()
        while not self._stopped.is_set():
            snap = {"t": time.time()}
            try:
                snap["loops"] = self.client.loops()
                snap["matrix_loops"] = self.client.matrix_loops()
                name = self._selected
                if name in snap["loops"]:
                    snap["history_of"] = name
                    snap["history"] = self.client.history(name, n=500)
                snap["interlocks"] = self.client.interlocks()
                snap["alarms"] = self.client.alarms()
                snap["logs"] = self.client.logs(n=200)
            except Exception as exc:
                snap["error"] = f"{type(exc).__name__}: {exc}"
            self._snapshot = snap
            self._wake.wait(self.refresh_s)
            self._wake.clear()

    def _write(self, label, fn, *args, **kwargs):
        def run():
            from .core import _thread_init

            _thread_init()
            try:
                fn(*args, **kwargs)
                self._messages.append(f"{label}: ok")
            except Exception as exc:
                self._messages.append(f"{label}: {type(exc).__name__}: {exc}")
            self._wake.set()  # show the effect at once

        threading.Thread(target=run, daemon=True, name="feedback-qt-write").start()

    # -- building ----------------------------------------------------------
    def _build_window(self):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            _app_ref = QtWidgets.QApplication([])
        from eco.utilities import mainthread
        from eco.widgets.qt_lifecycle import close_calls_stop

        mainthread.install_qt()

        win = self.window = QtWidgets.QWidget()
        win.setWindowTitle(self.title)
        win.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        win.resize(1100, 750)
        top = QtWidgets.QVBoxLayout(win)

        self._banner = QtWidgets.QLabel("connecting ...")
        top.addWidget(self._banner)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_select)

        buttons = QtWidgets.QHBoxLayout()
        self.btn_enable = QtWidgets.QPushButton("Enable")
        self.btn_disable = QtWidgets.QPushButton("Disable")
        self.btn_disable_all = QtWidgets.QPushButton("Disable all")
        self.btn_disable_all.setStyleSheet("font-weight: bold")
        self.btn_enable.clicked.connect(lambda: self._set_selected(enabled=True))
        self.btn_disable.clicked.connect(lambda: self._set_selected(enabled=False))
        self.btn_disable_all.clicked.connect(self._disable_all)
        for b in (self.btn_enable, self.btn_disable, self.btn_disable_all):
            buttons.addWidget(b)
        buttons.addStretch(1)

        upper = QtWidgets.QWidget()
        ul = QtWidgets.QVBoxLayout(upper)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.addWidget(self.table)
        ul.addLayout(buttons)

        # settings form of the selected loop (filled on selection, not on refresh)
        form_box = QtWidgets.QGroupBox("settings")
        form = QtWidgets.QFormLayout(form_box)
        self.fields = {}
        for key in SETTINGS:
            edit = QtWidgets.QLineEdit()
            edit.returnPressed.connect(self._apply)
            self.fields[key] = edit
            form.addRow(key, edit)
        apply = QtWidgets.QPushButton("Apply")
        apply.clicked.connect(self._apply)
        form.addRow(apply)
        self._detail = QtWidgets.QLabel()
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        form.addRow(self._detail)
        form_box.setMaximumWidth(330)

        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        self._fig = Figure(figsize=(6, 3))
        self._canvas = FigureCanvasQTAgg(self._fig)

        middle = QtWidgets.QWidget()
        ml = QtWidgets.QHBoxLayout(middle)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(form_box)
        ml.addWidget(self._canvas, 1)

        self._safety = QtWidgets.QLabel()
        self._safety.setWordWrap(True)
        self._log = QtWidgets.QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(2000)
        lower = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(lower)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self._safety)
        ll.addWidget(self._log)

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        for part in (upper, middle, lower):
            split.addWidget(part)
        split.setSizes([250, 320, 180])
        top.addWidget(split)

        self._last_log = None
        self._timer = QtCore.QTimer(win)
        self._timer.timeout.connect(self._show)
        self._timer.start(self.POLL_MS)
        close_calls_stop(win, self.stop)
        win.show()

    # -- main-thread side --------------------------------------------------
    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        name = self.table.item(rows[0].row(), 0).text()
        if name == self._selected:
            return
        self._selected = name
        snap = self._snapshot or {}
        self._fill_form(snap.get("loops", {}).get(name, {}))
        self._wake.set()

    def _fill_form(self, s):
        for key, edit in self.fields.items():
            edit.setText("" if s.get(key) is None else _fmt(s.get(key), ".10g"))

    def _set_selected(self, **settings):
        if self._selected:
            self._write(f"{self._selected} {settings}", self.client.set,
                        self._selected, **settings)

    def _disable_all(self):
        def all_off():
            snap = self._snapshot or {}
            names = list(snap.get("loops") or self.client.loops())
            for name in names:
                self.client.set(name, enabled=False)
            for name in snap.get("matrix_loops") or {}:
                self.client.set_matrix_loop(name, enabled=False)

        self._write("disable all", all_off)

    def _apply(self):
        if not self._selected:
            return
        settings = {}
        try:
            for key, edit in self.fields.items():
                text = edit.text().strip()
                if text and text != "-":
                    settings[key] = float(text)
        except ValueError as exc:
            self._messages.append(f"settings: {exc}")
            return
        self._set_selected(**settings)

    def _show(self):
        try:
            self._show_snapshot()
        except RuntimeError:  # widget already deleted
            pass

    def _show_snapshot(self):
        while self._messages:
            self._log.appendPlainText(f"[panel] {self._messages.popleft()}")
        snap = self._snapshot
        if snap is None or snap is self._snapshot_shown:
            return
        self._snapshot_shown = snap
        if "error" in snap:
            self._banner.setText(f"<b style='color:#c00'>no contact: {snap['error']}</b>")
            return
        loops = snap["loops"]
        n_on = sum(s["enabled"] for s in loops.values())
        self._banner.setText(f"{len(loops)} loop(s), {n_on} enabled, "
                             f"{len(snap['matrix_loops'])} matrix loop(s)  —  "
                             + time.strftime("%H:%M:%S", time.localtime(snap["t"])))
        self._fill_table(loops, snap["matrix_loops"])
        if self._selected is None and loops:
            self.table.selectRow(0)
        sel = loops.get(self._selected)
        if sel is not None:
            self._detail.setText(
                f"{sel.get('detector_name')} → {sel.get('adjustable_name')}<br>"
                + (f"<span style='color:#c00'>{sel['error']}</span>" if sel.get("error") else ""))
        if snap.get("history_of") == self._selected and sel is not None:
            self._plot(self._selected, snap["history"], sel["setpoint"])
        self._show_safety(snap["interlocks"], snap["alarms"])
        self._show_logs(snap["logs"])

    def _fill_table(self, loops, matrix_loops):
        rows = [(name, s, False) for name, s in loops.items()]
        rows += [(name, s, True) for name, s in matrix_loops.items()]
        resize = self.table.rowCount() != len(rows)
        if resize:
            self.table.setRowCount(len(rows))
        for r, (name, s, is_matrix) in enumerate(rows):
            if is_matrix:
                cells = [name, f"matrix {s.get('matrix_name', '')}", _state(s),
                         "", "", "", _fmt(s.get("actual_rate_hz"), ".1f"),
                         _fmt(s.get("gain")), "", ""]
            else:
                cells = [name, f"{s.get('detector_name')} -> {s.get('adjustable_name')}",
                         _state(s), _fmt(s.get("last_value")), _fmt(s.get("setpoint")),
                         _fmt(s.get("last_output")), _fmt(s.get("actual_rate_hz"), ".1f"),
                         _fmt(s.get("kp")), _fmt(s.get("ki")), _fmt(s.get("kd"))]
            for c, text in enumerate(cells):
                item = self.table.item(r, c)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    self.table.setItem(r, c, item)
                if item.text() != text:
                    item.setText(text)
                if c == 2:
                    bad = s.get("error") or s.get("alarmed") or s.get("held")
                    item.setForeground(QtCore.Qt.red if bad else
                                       (QtCore.Qt.darkGreen if s.get("enabled")
                                        else self.table.palette().text().color()))
        if resize:
            self.table.resizeColumnsToContents()

    def _plot(self, name, hist, setpoint):
        fig = self._fig
        fig.clear()
        ax = fig.add_subplot(1, 1, 1)
        t = hist.get("t") or []
        t0 = t[-1] if t else 0.0
        tt = [x - t0 for x in t]
        values = [float("nan") if v is None else v for v in hist.get("value", [])]
        ax.plot(tt, values, lw=1, label="value")
        ax.axhline(setpoint, ls="--", lw=1, color="grey", label="setpoint")
        outputs = [float("nan") if o is None else o for o in hist.get("output", [])]
        if any(o == o for o in outputs):
            ax2 = ax.twinx()
            ax2.plot(tt, outputs, lw=1, color="tab:orange", alpha=0.7)
            ax2.set_ylabel("output", color="tab:orange")
        ax.set_xlabel("time relative to now [s]")
        ax.set_ylabel("value")
        ax.set_title(name, fontsize=10)
        ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()
        self._canvas.draw_idle()

    def _show_safety(self, interlocks, alarms):
        parts = []
        for name, s in (interlocks or {}).items():
            ok = s.get("ok")
            parts.append(f"<span style='color:{'#080' if ok else '#c00'}'>"
                         f"{name}: {'OK' if ok else 'TRIPPED'}"
                         + (" (forced)" if s.get("forced") is not None else "") + "</span>")
        if alarms:
            parts.append(f"<b style='color:#c00'>alarms: {', '.join(alarms)}</b>")
        self._safety.setText("interlocks: " + ("  ".join(parts) if parts else "none"))

    def _show_logs(self, logs):
        logs = logs or []
        if not logs:
            return
        last = logs[-1]
        if last == self._last_log:
            return
        # append only the entries newer than the last one shown
        start = 0
        if self._last_log in logs:
            start = logs.index(self._last_log) + 1
        for e in logs[start:]:
            ts = e.get("ts")
            ts = time.strftime("%H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else ""
            self._log.appendPlainText(f"{ts} {e.get('level', '')} {e.get('source', '')}: "
                                      f"{e.get('message', e)}")
        self._last_log = last


import atexit  # noqa: E402
import weakref  # noqa: E402

_PANELS = weakref.WeakSet()


@atexit.register
def _stop_panels():
    for panel in list(_PANELS):
        panel._stopped.set()
        panel._wake.set()
        if panel._thread is not None:
            panel._thread.join(timeout=5)
