"""A small Qt window for one eco-box-server: connection state, and buttons
to disconnect (safety), reconnect, or restart the process (to pick up
device code changed since it started).

Standalone on purpose, same reasoning as eco.status_server.gui: it talks to
the server over plain HTTP (BoxServerClient), so it needs no eco import and
starts in under a second, and can run on a different machine than the
server itself (any console that can reach it over the network).

    python -m eco.manual_control.box_server_gui --url http://saresb-cons-05:8092

or ``eco-box-server gui``, which does exactly that, detached.

All network calls run on a background QThread (_Poller / _ActionWorker) and
report back via Qt signals - never on the GUI thread, so a slow or
unreachable server makes the window go stale instead of freezing.
"""

from __future__ import annotations

import argparse
import sys
import time

from qtpy import QtCore, QtWidgets

from .box_server_client import BoxServerClient

POLL_HEALTH_S = 2.0

COLOR_OK = "#2e7d32"
COLOR_WARN = "#b26a00"
COLOR_BAD = "#b71c1c"
COLOR_MUTED = "#757575"

# Mirrors the state strings BoxSession/ControlBox use (remote/serve.py):
# "idle" (never connected / cleanly disconnected), "dialling", "waiting for
# the operator", "connected", "closed" (ended, with a reason).
STATE_COLOR = {
    "connected": COLOR_OK,
    "waiting for the operator": COLOR_WARN,
    "dialling": COLOR_WARN,
    "closed": COLOR_BAD,
    "idle": COLOR_MUTED,
}


def _fmt_duration(s):
    if s is None:
        return "?"
    if s < 120:
        return f"{s:.0f}s"
    return f"{s/60:.1f}min"


class _Poller(QtCore.QThread):
    health = QtCore.Signal(dict)
    health_failed = QtCore.Signal(str)

    def __init__(self, client: BoxServerClient, parent=None):
        super().__init__(parent)
        self.client = client
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        while not self._stop:
            try:
                self.health.emit(self.client.health())
            except Exception as exc:  # noqa: BLE001 - reported via signal
                self.health_failed.emit(f"{type(exc).__name__}: {exc}")
            for _ in range(int(POLL_HEALTH_S * 10)):
                if self._stop:
                    return
                self.msleep(100)


class _ActionWorker(QtCore.QThread):
    """Runs one blocking client call (disconnect/reconnect/restart) off the
    GUI thread - these are safety-relevant actions and must not appear to
    hang the window while the request is in flight."""

    finished_ok = QtCore.Signal(dict)
    finished_error = QtCore.Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - reported via signal
            self.finished_error.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished_ok.emit(result or {})


class BoxServerMonitor(QtWidgets.QWidget):
    def __init__(self, base_url: str, parent=None):
        super().__init__(parent)
        self.base_url = base_url
        self.client = BoxServerClient(base_url, timeout=10.0)
        self.setWindowTitle(f"eco box server - {base_url}")
        self.resize(480, 320)

        self._action_worker = None
        self._build_ui()

        self._poller = _Poller(self.client, self)
        self._poller.health.connect(self._on_health)
        self._poller.health_failed.connect(self._on_health_failed)
        self._poller.start()

    # -- UI construction --------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        header = QtWidgets.QHBoxLayout()
        self.dot = QtWidgets.QLabel("●")
        self.dot.setStyleSheet(f"color: {COLOR_MUTED}; font-size: 16px;")
        header.addWidget(self.dot)
        header.addWidget(QtWidgets.QLabel(f"<b>{self.base_url}</b>"))
        header.addStretch(1)
        self.updated_label = QtWidgets.QLabel("never updated")
        self.updated_label.setStyleSheet(f"color: {COLOR_MUTED};")
        header.addWidget(self.updated_label)
        layout.addLayout(header)

        self.state_label = QtWidgets.QLabel("-")
        f = self.state_label.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        self.state_label.setFont(f)
        layout.addWidget(self.state_label)

        self.box_label = QtWidgets.QLabel("box: -")
        layout.addWidget(self.box_label)

        self.reason_label = QtWidgets.QLabel("")
        self.reason_label.setWordWrap(True)
        self.reason_label.setStyleSheet(f"color: {COLOR_MUTED};")
        layout.addWidget(self.reason_label)

        grid = QtWidgets.QGridLayout()
        self.detail_labels = {}
        for i, key in enumerate(("namespace", "pid", "uptime_s", "rss_mb",
                                 "n_threads", "cpu_seconds")):
            title = QtWidgets.QLabel(key.replace("_", " "))
            title.setStyleSheet(f"color: {COLOR_MUTED};")
            value = QtWidgets.QLabel("-")
            grid.addWidget(title, i // 2, (i % 2) * 2)
            grid.addWidget(value, i // 2, (i % 2) * 2 + 1)
            self.detail_labels[key] = value
        layout.addLayout(grid)

        buttons = QtWidgets.QHBoxLayout()
        self.btn_disconnect = QtWidgets.QPushButton("Disconnect")
        self.btn_disconnect.setToolTip(
            "Release the box now - it falls back to its waiting screen. "
            "Use this to hand it to someone else, or if something looks wrong.")
        self.btn_reconnect = QtWidgets.QPushButton("Reconnect")
        self.btn_reconnect.setToolTip(
            "Offer this session to the box again - the operator standing "
            "at the box still has to accept it, same as any first connection.")
        self.btn_restart = QtWidgets.QPushButton("Restart process")
        self.btn_restart.setToolTip(
            "Re-exec the whole server process, so any device code changed "
            "since it started is picked up. Disconnects the box first.")
        self.btn_disconnect.clicked.connect(self._confirm_disconnect)
        self.btn_reconnect.clicked.connect(lambda: self._run_action(
            "reconnect", self.client.reconnect))
        self.btn_restart.clicked.connect(self._confirm_restart)
        for b in (self.btn_disconnect, self.btn_reconnect, self.btn_restart):
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.action_status = QtWidgets.QLabel("")
        self.action_status.setStyleSheet(f"color: {COLOR_MUTED};")
        layout.addWidget(self.action_status)
        layout.addStretch(1)

    # -- polling callbacks -------------------------------------------------

    def _on_health(self, h: dict):
        self.updated_label.setText(time.strftime("updated %H:%M:%S"))
        self.dot.setStyleSheet(f"color: {COLOR_OK}; font-size: 16px;")

        state = h.get("state", "?")
        connected = h.get("connected")
        label = state + ("  (box connected)" if connected else "")
        self.state_label.setText(label)
        self.state_label.setStyleSheet(f"color: {STATE_COLOR.get(state, COLOR_MUTED)};")
        self.box_label.setText(f"box: {h.get('box', '-')}")
        self.reason_label.setText(h.get("reason") or "")

        for key, w in self.detail_labels.items():
            v = h.get(key)
            if key == "rss_mb" and v is not None:
                w.setText(f"{v:.0f} MB")
            elif key in ("cpu_seconds", "uptime_s") and v is not None:
                w.setText(_fmt_duration(v))
            else:
                w.setText("-" if v is None else str(v))

        busy = self._action_worker is not None
        self.btn_disconnect.setEnabled(not busy and bool(connected))
        self.btn_reconnect.setEnabled(not busy and not connected)
        self.btn_restart.setEnabled(not busy)

    def _on_health_failed(self, message: str):
        self.dot.setStyleSheet(f"color: {COLOR_BAD}; font-size: 16px;")
        self.updated_label.setText(f"unreachable: {message}")

    # -- actions ------------------------------------------------------------

    def _confirm_disconnect(self):
        if QtWidgets.QMessageBox.question(
            self, "Disconnect the box?",
            "This releases the box immediately - it goes back to its "
            "waiting screen and nobody can drive it until a session "
            "connects again.\n\nDisconnect now?",
        ) == QtWidgets.QMessageBox.Yes:
            self._run_action("disconnect", self.client.disconnect)

    def _confirm_restart(self):
        if QtWidgets.QMessageBox.question(
            self, "Restart the box server?",
            "This disconnects the box and restarts the whole server "
            "process to pick up device code changes. The box will show "
            "its waiting screen until this session reconnects and the "
            "operator accepts it again.\n\nRestart now?",
        ) == QtWidgets.QMessageBox.Yes:
            self._run_action("restart", self.client.restart)

    def _run_action(self, name, fn):
        if self._action_worker is not None:
            return
        self.action_status.setText(f"{name} requested ...")
        self.action_status.setStyleSheet(f"color: {COLOR_WARN};")
        self._action_worker = _ActionWorker(fn, self)
        self._action_worker.finished_ok.connect(
            lambda result: self._on_action_done(name, result))
        self._action_worker.finished_error.connect(
            lambda message: self._on_action_error(name, message))
        self._action_worker.start()

    def _on_action_done(self, name, result):
        self._action_worker = None
        self.action_status.setText(result.get("message") or f"{name} done")
        self.action_status.setStyleSheet(f"color: {COLOR_OK};")

    def _on_action_error(self, name, message):
        self._action_worker = None
        self.action_status.setText(f"{name} failed: {message}")
        self.action_status.setStyleSheet(f"color: {COLOR_BAD};")

    def closeEvent(self, event):
        # Same reasoning as StatusServerMonitor.closeEvent: destroying a
        # QThread mid-request is a hard crash, and an action here can be
        # mid-flight (an unreachable server, bounded by client.timeout), so
        # the window briefly refuses to close rather than risk it.
        if self._action_worker is not None and self._action_worker.isRunning():
            self.action_status.setText("cannot close: an action is still in flight")
            self.action_status.setStyleSheet(f"color: {COLOR_BAD};")
            event.ignore()
            return
        self._poller.stop()
        self._poller.wait(2000)
        super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Qt monitor/control panel for one eco.manual_control.box_server"
    )
    parser.add_argument("--url", required=True,
                        help="server base URL, e.g. http://saresb-cons-05:8092")
    args = parser.parse_args(argv)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = BoxServerMonitor(args.url)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
