"""A small Qt window for one status server: state, init progress with a
reinitialize button (progress bar + ETA), and a table of recent
/status/snapshot and /status/capture operations.

Standalone on purpose - it talks to the server over plain HTTP
(StatusServerClient), the same as any other client, so it needs no eco
namespace import and starts in under a second. Launch it with:

    python -m eco.status_server.gui --url http://saresb-cons-04:8091

or `eco-status-server gui`, which does exactly that, detached.

All network calls run on a background QThread (_Poller) and report back via
Qt signals - never on the GUI thread, so a slow or unreachable server makes
the displayed state go stale (and the connection dot go red) instead of
freezing the window.
"""

from __future__ import annotations

import argparse
import sys
import time

from qtpy import QtCore, QtGui, QtWidgets

from .client import StatusServerClient, StatusServerError, StatusServerNotReady

POLL_HEALTH_S = 2.0
POLL_STATS_S = 5.0

# Palette: a small, fixed set of colours reused across state text, the
# connection dot and stats-table error rows, rather than picking new ones ad
# hoc per widget - see eco's dataviz guidance on consistent, limited colour
# use even outside chart contexts.
COLOR_OK = "#2e7d32"
COLOR_WARN = "#b26a00"
COLOR_BAD = "#b71c1c"
COLOR_MUTED = "#757575"

STATE_COLOR = {
    "ready": COLOR_OK,
    "importing": COLOR_WARN,
    "initializing": COLOR_WARN,
    "reinitializing": COLOR_WARN,
    "failed": COLOR_BAD,
}


def _fmt_duration(s):
    if s is None:
        return "?"
    if s < 120:
        return f"{s:.0f}s"
    return f"{s/60:.1f}min"


class _Poller(QtCore.QThread):
    """Background polling loop for one StatusServerClient.

    Two independent intervals in one thread rather than two QTimers calling
    into `requests` on the GUI thread - a hung/slow HTTP call must not freeze
    the window. `health` is polled more often than `stats` since it is what
    drives the progress bar during a reinit.
    """

    health = QtCore.Signal(dict)
    health_failed = QtCore.Signal(str)
    stats = QtCore.Signal(dict)

    def __init__(self, client: StatusServerClient, parent=None):
        super().__init__(parent)
        self.client = client
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        last_stats = 0.0
        while not self._stop:
            try:
                h = self.client.health()
                self.health.emit(h)
            except Exception as exc:  # noqa: BLE001 - reported via signal
                self.health_failed.emit(f"{type(exc).__name__}: {exc}")
            now = time.time()
            if now - last_stats >= POLL_STATS_S:
                last_stats = now
                try:
                    self.stats.emit(self.client.stats(limit=30))
                except Exception:
                    pass  # the health signal already reports connectivity
            for _ in range(int(POLL_HEALTH_S * 10)):
                if self._stop:
                    return
                self.msleep(100)


class _ReinitWorker(QtCore.QThread):
    """Runs one blocking client call (reinit/restart) off the GUI thread."""

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


class StatusServerMonitor(QtWidgets.QWidget):
    def __init__(self, base_url: str, parent=None):
        super().__init__(parent)
        self.base_url = base_url
        self.client = StatusServerClient(base_url, timeout=10.0, snapshot_timeout=60.0)
        self.setWindowTitle(f"eco status server - {base_url}")
        self.resize(720, 560)

        self._last_health = {}
        self._build_ui()

        self._poller = _Poller(self.client, self)
        self._poller.health.connect(self._on_health)
        self._poller.health_failed.connect(self._on_health_failed)
        self._poller.stats.connect(self._on_stats)
        self._poller.start()

        self._reinit_worker = None

    # -- UI construction -----------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        header = QtWidgets.QHBoxLayout()
        self.dot = QtWidgets.QLabel("●")  # filled circle
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

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.eta_label = QtWidgets.QLabel("")
        self.eta_label.setStyleSheet(f"color: {COLOR_MUTED};")
        layout.addWidget(self.eta_label)

        # The banner the whole task is centred on: only visible while a
        # REQUIRED component is missing from initialized_names.
        self.required_banner = QtWidgets.QLabel("")
        self.required_banner.setWordWrap(True)
        self.required_banner.setStyleSheet(
            f"background-color: {COLOR_BAD}; color: white; font-weight: bold; "
            "padding: 6px; border-radius: 3px;"
        )
        self.required_banner.hide()
        layout.addWidget(self.required_banner)

        self.other_failed_label = QtWidgets.QLabel("")
        self.other_failed_label.setWordWrap(True)
        self.other_failed_label.setStyleSheet(f"color: {COLOR_WARN};")
        layout.addWidget(self.other_failed_label)

        grid = QtWidgets.QGridLayout()
        self.detail_labels = {}
        for i, key in enumerate((
            "n_direct_read", "n_monitorable", "n_monitored",
            "rss_mb", "n_threads", "cpu_seconds",
        )):
            title = QtWidgets.QLabel(key.replace("_", " "))
            title.setStyleSheet(f"color: {COLOR_MUTED};")
            value = QtWidgets.QLabel("-")
            grid.addWidget(title, i // 3, (i % 3) * 2)
            grid.addWidget(value, i // 3, (i % 3) * 2 + 1)
            self.detail_labels[key] = value
        layout.addLayout(grid)

        buttons = QtWidgets.QHBoxLayout()
        self.btn_failed = QtWidgets.QPushButton("Reinit failed")
        self.btn_full = QtWidgets.QPushButton("Reinit full")
        self.btn_restart = QtWidgets.QPushButton("Restart process")
        self.btn_failed.clicked.connect(lambda: self._start_reinit("failed"))
        self.btn_full.clicked.connect(lambda: self._start_reinit("full"))
        self.btn_restart.clicked.connect(lambda: self._start_reinit("restart"))
        for b in (self.btn_failed, self.btn_full, self.btn_restart):
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.action_status = QtWidgets.QLabel("")
        self.action_status.setStyleSheet(f"color: {COLOR_MUTED};")
        layout.addWidget(self.action_status)

        layout.addWidget(QtWidgets.QLabel("<b>Recent queries</b>"))
        self.summary_label = QtWidgets.QLabel("no requests served yet")
        self.summary_label.setStyleSheet(f"color: {COLOR_MUTED};")
        layout.addWidget(self.summary_label)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["age", "kind", "duration", "entries", "error"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, stretch=1)

    # -- polling callbacks -----------------------------------------------

    def _on_health(self, h: dict):
        self._last_health = h
        self.updated_label.setText(time.strftime("updated %H:%M:%S"))
        self.dot.setStyleSheet(f"color: {COLOR_OK}; font-size: 16px;")

        state = h.get("state", "?")
        self.state_label.setText(
            f"{state}  (generation {h.get('generation')}, "
            f"up {h.get('uptime_s', 0)/60:.1f} min)"
        )
        self.state_label.setStyleSheet(
            f"color: {STATE_COLOR.get(state, COLOR_MUTED)};"
        )

        n_init, n_target = h.get("n_initialized"), h.get("n_target_names")
        elapsed = h.get("state_seconds") or 0
        if n_target:
            frac = (n_init or 0) / n_target
            self.progress.setRange(0, 100)
            self.progress.setValue(int(round(frac * 100)))
            self.progress.setFormat(f"{n_init}/{n_target} components  %p%")
            busy = state in ("importing", "initializing", "reinitializing")
            if busy and frac > 0:
                eta = elapsed * (1 - frac) / frac
                self.eta_label.setText(
                    f"elapsed {_fmt_duration(elapsed)}, eta ~{_fmt_duration(eta)}"
                )
            elif busy:
                self.eta_label.setText(f"elapsed {_fmt_duration(elapsed)}, eta unknown")
            else:
                self.eta_label.setText("")
        else:
            self.progress.setRange(0, 0)  # indeterminate: nothing to project yet
            self.eta_label.setText(f"elapsed {_fmt_duration(elapsed)}")

        failed_required = h.get("failed_required") or []
        if failed_required:
            self.required_banner.setText(
                f"⚠ {len(failed_required)} REQUIRED component(s) failed to "
                f"initialize: {', '.join(failed_required)}"
            )
            self.required_banner.show()
        else:
            self.required_banner.hide()

        other_failed = [n for n in (h.get("failed_names") or [])
                        if n not in failed_required]
        self.other_failed_label.setText(
            f"failed (not required): {', '.join(other_failed)}" if other_failed else ""
        )

        for key, label in self.detail_labels.items():
            v = h.get(key)
            if key == "rss_mb" and v is not None:
                label.setText(f"{v:.0f} MB")
            elif key == "cpu_seconds" and v is not None:
                label.setText(_fmt_duration(v))
            else:
                label.setText("-" if v is None else str(v))

        busy = state in ("importing", "initializing", "reinitializing")
        for b in (self.btn_failed, self.btn_full, self.btn_restart):
            b.setEnabled(not busy and self._reinit_worker is None)

    def _on_health_failed(self, message: str):
        self.dot.setStyleSheet(f"color: {COLOR_BAD}; font-size: 16px;")
        self.updated_label.setText(f"unreachable: {message}")

    def _on_stats(self, d: dict):
        s = d.get("summary") or {}
        if not s.get("n"):
            self.summary_label.setText("no requests served yet")
        else:
            avg = s.get("avg_duration_s")
            self.summary_label.setText(
                f"{s['n']} operation(s), {s['n_errors']} error(s)"
                + (f", avg {avg:.2f}s" if avg is not None else "")
            )

        rows = list(reversed(d.get("recent") or []))
        self.table.setRowCount(len(rows))
        now = time.time()
        for r, entry in enumerate(rows):
            age = now - entry.get("at", now)
            dur = entry.get("duration_s")
            n = entry.get("n_entries")
            err = entry.get("error") or ""
            values = [
                f"{age:.0f}s ago", entry.get("kind", "?"),
                f"{dur:.2f}s" if dur is not None else "?",
                "" if n is None else str(n), err,
            ]
            for col, val in enumerate(values):
                item = QtWidgets.QTableWidgetItem(val)
                if err:
                    item.setForeground(QtGui.QColor(COLOR_BAD))
                self.table.setItem(r, col, item)
        self.table.resizeColumnsToContents()

    # -- actions -----------------------------------------------------------

    def _start_reinit(self, mode: str):
        if self._reinit_worker is not None:
            return
        for b in (self.btn_failed, self.btn_full, self.btn_restart):
            b.setEnabled(False)
        self.action_status.setText(f"reinit ({mode}) requested ...")
        self.action_status.setStyleSheet(f"color: {COLOR_WARN};")

        def call():
            return self.client.reinit(mode=mode, wait=True, timeout=1800,
                                      progress=False)

        self._reinit_worker = _ReinitWorker(call, self)
        self._reinit_worker.finished_ok.connect(self._on_reinit_ok)
        self._reinit_worker.finished_error.connect(self._on_reinit_error)
        self._reinit_worker.start()

    def _on_reinit_ok(self, health: dict):
        self._reinit_worker = None
        self.action_status.setText(
            f"reinit finished: {health.get('n_initialized')}/"
            f"{health.get('n_target_names')} initialized, "
            f"{health.get('n_failed')} failed"
        )
        self.action_status.setStyleSheet(f"color: {COLOR_OK};")

    def _on_reinit_error(self, message: str):
        self._reinit_worker = None
        self.action_status.setText(f"reinit failed: {message}")
        self.action_status.setStyleSheet(f"color: {COLOR_BAD};")

    def closeEvent(self, event):
        # A reinit is a blocking network call that can run for minutes
        # (client.reinit(wait=True)); destroying the QThread while it is
        # still running is a hard crash (observed directly, not
        # hypothetically), so the window refuses to close under it instead.
        # No modal dialog here on purpose - that would block the whole
        # process on a click while the reinit keeps running regardless;
        # the status label already says why nothing happened.
        if self._reinit_worker is not None and self._reinit_worker.isRunning():
            self.action_status.setText(
                "cannot close: a reinit/restart is still running on the server"
            )
            self.action_status.setStyleSheet(f"color: {COLOR_BAD};")
            event.ignore()
            return
        self._poller.stop()
        self._poller.wait(2000)
        super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Qt status/reinit monitor for one eco.status_server"
    )
    parser.add_argument("--url", required=True,
                        help="server base URL, e.g. http://saresb-cons-04:8091")
    args = parser.parse_args(argv)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StatusServerMonitor(args.url)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
