"""Qt windows for alarms: `AlarmTableQt` (a system, panel or group) and
`AlarmDetailQt` (one alarm, with editable settings).

Reached through ``alarms.widget()``, ``alarms.overview.widget()``,
``<alarm>.widget()`` (``_default_widget``). Every read and write runs on a
worker thread; results come back through a queue drained by a timer on the
GUI thread (Qt is not thread-safe), and every delayed callback tolerates a
widget that was closed meanwhile.
"""

from __future__ import annotations

import queue
import threading
import time

from qtpy import QtCore, QtGui, QtWidgets

from eco.alarms import tracker as tr
from eco.alarms.views import STATE_DOT, STATE_TINT, alarm_rows, header_info

REFRESH_MS = 2000

_COLUMNS = ["", "Alarm", "Value", "Range", "Level", "State", "For", "#"]


def _ensure_qt_loop():
    """QApplication + IPython's Qt input hook, like DisplayQt.start()."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    try:
        from IPython import get_ipython

        ip = get_ipython()
    except Exception:
        ip = None
    if ip is not None and getattr(ip, "active_eventloop", None) is None:
        try:
            ip.enable_gui("qt")
        except Exception:
            pass
    return app


class _Worker:
    """Run functions off the GUI thread; deliver (callback, result) on it."""

    def __init__(self, owner):
        self._q = queue.Queue()
        self._timer = QtCore.QTimer(owner)
        self._timer.timeout.connect(self._drain)
        self._timer.start(100)
        self._busy = set()

    def run(self, key, func, callback=None, on_error=None):
        if key is not None and key in self._busy:
            return
        if key is not None:
            self._busy.add(key)

        def target():
            try:
                res, err = func(), None
            except Exception as e:  # reported on the GUI thread
                res, err = None, e
            self._q.put((key, callback, on_error, res, err))

        threading.Thread(target=target, daemon=True, name="eco-alarm-qt").start()

    def _drain(self):
        while True:
            try:
                key, cb, on_err, res, err = self._q.get_nowait()
            except queue.Empty:
                return
            self._busy.discard(key)
            try:
                if err is not None:
                    if on_err:
                        on_err(err)
                elif cb:
                    cb(res)
            except RuntimeError:
                pass  # widget deleted meanwhile

    def stop(self):
        try:
            self._timer.stop()
        except RuntimeError:
            pass


def _dot(color, size=12):
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setBrush(QtGui.QColor(color))
    p.setPen(QtCore.Qt.NoPen)
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QtGui.QIcon(pm)


def _state_text(row):
    st = row["state"]
    txt = st.upper() if st in tr.RAISED_STATES else st
    if row.get("acked_by"):
        txt += f" · {row['acked_by']}"
    return txt


class AlarmTableQt:
    """Table of alarms below `obj` (an AlarmSystem, AlarmPanel or
    AlarmGroup), refreshed every 2 s."""

    def __init__(self, obj, title=None):
        self.obj = obj
        self.title = title or _full_name(obj) or "alarms"
        self.window = None
        self._rows = []
        self._details = []
        self._worker = None
        self._is_system = hasattr(obj, "_panel_names")

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        _ensure_qt_loop()
        self._build()
        self.window.show()
        return self

    def stop(self):
        if self._worker is not None:
            self._worker.stop()
        for d in self._details:
            try:
                d.stop()
            except Exception:
                pass
        if self.window is not None:
            try:
                self.window.close()
            except RuntimeError:
                pass
            self.window = None

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle(f"eco alarms: {self.title}")
        w.resize(980, 640)
        lay = QtWidgets.QVBoxLayout(w)

        if self._is_system:
            top = QtWidgets.QHBoxLayout()
            self._arm_label = QtWidgets.QLabel("…")
            self._arm_label.setTextFormat(QtCore.Qt.RichText)
            top.addWidget(self._arm_label, 1)
            self._arm_btn = QtWidgets.QPushButton("Arm…")
            self._arm_btn.clicked.connect(self._toggle_arm)
            top.addWidget(self._arm_btn)
            lay.addLayout(top)
            self._oncall_label = QtWidgets.QLabel("")
            self._oncall_label.setTextFormat(QtCore.Qt.RichText)
            lay.addWidget(self._oncall_label)
            self._problems_label = QtWidgets.QLabel("")
            self._problems_label.setStyleSheet("color: #8a5a00;")
            self._problems_label.setWordWrap(True)
            lay.addWidget(self._problems_label)

        bar = QtWidgets.QHBoxLayout()
        self._summary = QtWidgets.QLabel("")
        self._summary.setTextFormat(QtCore.Qt.RichText)
        bar.addWidget(self._summary, 1)
        self._only = QtWidgets.QCheckBox("only needing attention")
        self._only.toggled.connect(self._fill)
        bar.addWidget(self._only)
        lay.addLayout(bar)

        t = QtWidgets.QTableWidget(0, len(_COLUMNS))
        t.setHorizontalHeaderLabels(_COLUMNS)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        t.setAlternatingRowColors(False)
        t.setShowGrid(False)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        # the dot column must not size itself to the spanning section rows
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        t.setColumnWidth(0, 26)
        # long status messages must not eat the table: fixed, elided
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        t.setColumnWidth(2, 150)
        t.setTextElideMode(QtCore.Qt.ElideRight)
        t.cellDoubleClicked.connect(lambda r, c: self._open_detail(r))
        self._table = t
        lay.addWidget(t, 1)

        btns = QtWidgets.QHBoxLayout()
        for label, slot in (
            ("Acknowledge", self._ack_selected),
            ("Acknowledge all", self._ack_all),
            ("Shelve…", self._shelve_selected),
            ("Unshelve", self._unshelve_selected),
            ("Details…", lambda: self._open_detail(self._table.currentRow())),
        ):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color: #6b7280;")
        btns.addWidget(self._status)
        lay.addLayout(btns)

        self.window = w
        self._worker = _Worker(w)
        self._timer = QtCore.QTimer(w)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        try:
            from eco.widgets.qt_lifecycle import close_calls_stop

            close_calls_stop(w, self.stop)
        except Exception:
            pass
        self.refresh()

    # -- data ---------------------------------------------------------------
    def refresh(self):
        def fetch():
            rows, from_monitor = alarm_rows(self.obj)
            head = header_info(self.obj) if self._is_system else None
            return rows, from_monitor, head

        self._worker.run("refresh", fetch, self._apply, self._error)

    def _error(self, e):
        self._status.setText(f"refresh failed: {type(e).__name__}: {e}")

    def _apply(self, res):
        rows, from_monitor, head = res
        self._rows = rows
        self._from_monitor = from_monitor
        if head is not None:
            self._apply_header(head)
        counts = {}
        for r in rows:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        parts = []
        raised = sum(counts.get(s, 0) for s in tr.RAISED_STATES)
        if raised:
            parts.append(f'<b style="color:{STATE_DOT[tr.ACTIVE]}">{raised} raised'
                         + (f" ({counts[tr.ACTIVE]} unacknowledged)" if counts.get(tr.ACTIVE) else "") + "</b>")
        for st in (tr.PENDING, tr.STATE_UNKNOWN, tr.INHIBITED, tr.SHELVED, tr.DISABLED, tr.STATE_OK):
            if counts.get(st):
                parts.append(f'<span style="color:{STATE_DOT[st]}">{counts[st]} {st}</span>')
        self._summary.setText(" · ".join(parts))
        src = "monitor" if from_monitor else "evaluated here (no monitor)"
        self._status.setText(f"{time.strftime('%H:%M:%S')} · {src}")
        self._fill()

    def _apply_header(self, h):
        if h["armed"]:
            arm = '<b style="color:#d7263d">● ARMED</b> &nbsp;phones are paged'
            self._arm_btn.setText("Disarm…")
        else:
            arm = '<span style="color:#6b7280">○ disarmed &nbsp;(nothing is sent)</span>'
            self._arm_btn.setText("Arm…")
        if h["monitor"]:
            mon = (f'<span style="color:#2e9e44">monitor ok</span> '
                   f'<span style="color:#6b7280">({h["monitor"]["host"]}, {h["monitor"]["age"]:.0f} s ago)</span>')
        else:
            mon = '<b style="color:#b26a00">no monitor running: nobody is paged</b>'
        self._arm_label.setText(f"{arm} &nbsp;&nbsp; {mon}")
        oc = h.get("oncall")
        if oc:
            prim = ", ".join(oc["primary"]) or '<b style="color:#d7263d">nobody</b>'
            until = f' <span style="color:#6b7280">until {oc["until"]}</span>' if oc.get("until") else ""
            self._oncall_label.setText(
                f'on call: <b>{prim}</b> &nbsp; backup: {", ".join(oc["backup"]) or "—"}{until}')
        else:
            self._oncall_label.setText('<span style="color:#6b7280">no shift plan</span>')
        self._problems_label.setText("\n".join("⚠ " + p for p in h.get("problems", [])))
        self._problems_label.setVisible(bool(h.get("problems")))

    def _visible_rows(self):
        if not self._only.isChecked():
            return self._rows
        return [r for r in self._rows if r["state"] not in (tr.STATE_OK, tr.DISABLED)]

    def _fill(self, *_):
        t = self._table
        keep = {r["id"] for r in self._selected_row_dicts()}
        rows = self._visible_rows()
        # one header line per section, alarms indented below it
        lines, prev = [], object()
        for r in rows:
            if r["section"] and r["section"] != prev:
                lines.append(("section", r["section"]))
            prev = r["section"]
            lines.append(("alarm", r))
        self._lines = lines
        t.clearSpans()
        t.setRowCount(len(lines))
        for i, (kind, r) in enumerate(lines):
            if kind == "section":
                it = QtWidgets.QTableWidgetItem(r)
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                it.setForeground(QtGui.QColor("#374151"))
                it.setBackground(QtGui.QColor("#eef0f4"))
                it.setFlags(QtCore.Qt.ItemIsEnabled)
                t.setItem(i, 0, it)
                for c in range(1, len(_COLUMNS)):
                    t.setItem(i, c, QtWidgets.QTableWidgetItem(""))
                t.setSpan(i, 0, 1, len(_COLUMNS))
                continue
            cells = ["", ("   " if r["section"] else "") + r["label"], r["value"], r["range"],
                     "CRITICAL" if r["level"] == "critical" else r["level"],
                     _state_text(r), r["duration"], str(r["count"] or "")]
            tint = STATE_TINT.get(r["state"])
            for c, text in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(text)
                if c == 0:
                    it.setIcon(_dot(STATE_DOT.get(r["state"], "#999")))
                if c in (2, 6, 7):
                    it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if tint:
                    it.setBackground(QtGui.QColor(tint))
                f = it.font()
                if (c == 5 and r["state"] in tr.RAISED_STATES) or (c == 4 and r["level"] == "critical"):
                    f.setBold(True)
                if r["level"] == "display" and c == 4:
                    it.setForeground(QtGui.QColor("#8a9099"))
                if c == 5:
                    it.setForeground(QtGui.QColor(STATE_DOT.get(r["state"], "#000")))
                it.setFont(f)
                it.setToolTip(f"{r['id']}\n{r['value']}")
                t.setItem(i, c, it)
            if r["id"] in keep:
                t.selectRow(i)

    def _selected_row_dicts(self):
        out = []
        for i in self._selected_rows_idx():
            try:
                kind, r = self._lines[i]
            except (AttributeError, IndexError):
                continue
            if kind == "alarm":
                out.append(r)
        return out

    def _selected_rows_idx(self):
        try:
            return sorted({i.row() for i in self._table.selectedIndexes()})
        except RuntimeError:
            return []

    def _selected_alarms(self):
        return [self._find(r) for r in self._selected_row_dicts()]

    def _find(self, row):
        obj = self.obj
        for part in row["path"].split(".") if row["path"] else []:
            obj = getattr(obj, part)
        return obj

    # -- actions ------------------------------------------------------------
    def _act(self, func, what):
        self._status.setText(f"{what}…")
        self._worker.run(None, func, lambda _r: (self._status.setText(f"{what}: done"), self.refresh()),
                         lambda e: QtWidgets.QMessageBox.warning(self.window, "eco alarms", f"{what} failed:\n{e}"))

    def _ack_selected(self):
        alarms = self._selected_alarms()
        if alarms:
            self._act(lambda: [a.ack() for a in alarms], f"acknowledge {len(alarms)}")

    def _ack_all(self):
        rows = [r for r in self._rows if r["state"] == tr.ACTIVE]
        alarms = [self._find(r) for r in rows]
        self._act(lambda: [a.ack() for a in alarms], f"acknowledge {len(alarms)}")

    def _shelve_selected(self):
        alarms = self._selected_alarms()
        if not alarms:
            return
        minutes, ok = QtWidgets.QInputDialog.getInt(
            self.window, "Shelve", f"Silence {len(alarms)} alarm(s) for minutes:", 60, 1, 7 * 24 * 60)
        if ok:
            self._act(lambda: [a.shelve(minutes) for a in alarms], f"shelve {len(alarms)}")

    def _unshelve_selected(self):
        alarms = self._selected_alarms()
        if alarms:
            self._act(lambda: [a.unshelve() for a in alarms], "unshelve")

    def _toggle_arm(self):
        armed = self._arm_btn.text().startswith("Disarm")
        q = ("Disarm the alarm system? Nothing will be sent to phones."
             if armed else "Arm the alarm system? Raised warning/critical alarms will be sent to the phones of whoever is on call.")
        if QtWidgets.QMessageBox.question(self.window, "eco alarms", q) != QtWidgets.QMessageBox.Yes:
            return
        self._act(self.obj.disarm if armed else self.obj.arm, "disarm" if armed else "arm")

    def _open_detail(self, i):
        lines = getattr(self, "_lines", [])
        if i is None or i < 0 or i >= len(lines) or lines[i][0] != "alarm":
            return
        a = self._find(lines[i][1])
        d = AlarmDetailQt(a).start()
        self._details.append(d)


_SETTING_ORDER = ["enabled", "level", "min", "max", "delay", "clear_delay", "unknown_is_alarm", "inhibit_by", "note"]
_SETTING_HELP = {
    "min": "empty = no lower limit",
    "max": "empty = no upper limit",
    "delay": "s out of range before it is raised",
    "clear_delay": "s back in range before it clears",
    "inhibit_by": "alarm ids, comma separated",
    "level": "display: shown only · warning: one message · critical: repeats, escalates",
}


class AlarmDetailQt:
    """One alarm: live state and timing, editable settings."""

    def __init__(self, alarm):
        self.alarm = alarm
        self.window = None
        self._worker = None
        self._editors = {}
        self._loaded = None

    def start(self):
        _ensure_qt_loop()
        self._build()
        self.window.show()
        return self

    def stop(self):
        if self._worker is not None:
            self._worker.stop()
        if self.window is not None:
            try:
                self.window.close()
            except RuntimeError:
                pass
            self.window = None

    def _build(self):
        a = self.alarm
        w = QtWidgets.QWidget()
        w.setWindowTitle(f"eco alarm: {a.label or a.name}")
        w.resize(560, 600)
        lay = QtWidgets.QVBoxLayout(w)

        self._head = QtWidgets.QLabel("…")
        self._head.setTextFormat(QtCore.Qt.RichText)
        self._head.setStyleSheet("font-size: 15px; padding: 6px; border-radius: 6px;")
        lay.addWidget(self._head)
        self._info = QtWidgets.QLabel("")
        self._info.setTextFormat(QtCore.Qt.RichText)
        self._info.setWordWrap(True)
        lay.addWidget(self._info)

        row = QtWidgets.QHBoxLayout()
        for label, slot in (("Acknowledge", lambda: self._act(a.ack, "acknowledge")),
                            ("Shelve…", self._shelve),
                            ("Unshelve", lambda: self._act(a.unshelve, "unshelve"))):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        box = QtWidgets.QGroupBox("Settings (shared with every session and the monitor)")
        form = QtWidgets.QFormLayout(box)
        for key in _SETTING_ORDER:
            if key in ("enabled", "unknown_is_alarm"):
                ed = QtWidgets.QCheckBox()
            elif key == "level":
                ed = QtWidgets.QComboBox()
                ed.addItems(["display", "warning", "critical"])
            else:
                ed = QtWidgets.QLineEdit()
            if key in _SETTING_HELP:
                ed.setToolTip(_SETTING_HELP[key])
                if isinstance(ed, QtWidgets.QLineEdit):
                    ed.setPlaceholderText(_SETTING_HELP[key])
            self._editors[key] = ed
            form.addRow(key.replace("_", " "), ed)
        lay.addWidget(box)

        row2 = QtWidgets.QHBoxLayout()
        apply_b = QtWidgets.QPushButton("Apply")
        apply_b.clicked.connect(self._apply_settings)
        reset_b = QtWidgets.QPushButton("Reset to defaults")
        reset_b.clicked.connect(lambda: self._act(a.reset_settings, "reset", reload=True))
        row2.addWidget(apply_b)
        row2.addWidget(reset_b)
        row2.addStretch(1)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color: #6b7280;")
        row2.addWidget(self._status)
        lay.addLayout(row2)
        lay.addStretch(1)

        self.window = w
        self._worker = _Worker(w)
        self._timer = QtCore.QTimer(w)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        try:
            from eco.widgets.qt_lifecycle import close_calls_stop

            close_calls_stop(w, self.stop)
        except Exception:
            pass
        self.refresh(load_settings=True)

    def refresh(self, load_settings=False):
        a = self.alarm

        def fetch():
            rows, from_monitor = alarm_rows(a)
            info = a.get_state_info(evaluate=False)
            return rows[0], from_monitor, info, a.get_settings(), a.detector_path, getattr(a._detector, "pvname", None)

        self._worker.run("refresh", fetch, lambda res: self._show(res, load_settings),
                         lambda e: self._status.setText(f"refresh failed: {e}"))

    def _show(self, res, load_settings):
        from eco.elements.alarm import fmt_duration

        row, from_monitor, info, settings, path, pv = res
        st = row["state"]
        tint = STATE_TINT.get(st) or "#e7f5ea"
        self._head.setStyleSheet(f"font-size: 15px; padding: 8px; border-radius: 6px; background: {tint};")
        self._head.setText(
            f'<span style="color:{STATE_DOT.get(st)}; font-size:18px">●</span> '
            f"<b>{row['label']}</b> = <b>{row['value']}</b> &nbsp; {row['range']}<br>"
            f'<b style="color:{STATE_DOT.get(st)}">{_state_text(row)}</b> {row["duration"]}'
            f' &nbsp; · level {row["level"]}')
        total = info.get("total_active_s") or 0
        if info.get("active_since"):
            total += time.time() - info["active_since"]
        lines = [
            f"watching: <tt>{pv + ' (PV)' if pv else path}</tt>",
            f"id: <tt>{self.alarm.alarm_id}</tt>",
            f"raised {info.get('count') or 0}× · {fmt_duration(total) or '0s'} in alarm in total",
            "state from " + ("the monitor" if from_monitor else "this session (no monitor running)"),
        ]
        self._info.setText("<br>".join(f'<span style="color:#4b5563">{x}</span>' for x in lines))
        if load_settings or self._loaded is None:
            self._load(settings)

    def _load(self, s):
        self._loaded = dict(s)
        for key, ed in self._editors.items():
            v = s.get(key)
            if isinstance(ed, QtWidgets.QCheckBox):
                ed.setChecked(bool(v))
            elif isinstance(ed, QtWidgets.QComboBox):
                ed.setCurrentText(str(v))
            elif key == "inhibit_by":
                ed.setText(", ".join(v or []))
            else:
                ed.setText("" if v is None else (f"{v:g}" if isinstance(v, float) else str(v)))

    def _apply_settings(self):
        new = {}
        for key, ed in self._editors.items():
            if isinstance(ed, QtWidgets.QCheckBox):
                new[key] = ed.isChecked()
            elif isinstance(ed, QtWidgets.QComboBox):
                new[key] = ed.currentText()
            else:
                new[key] = ed.text()
        a = self.alarm
        loaded = self._loaded or {}

        def write():
            from eco.elements.alarm import _SETTINGS

            changed = []
            for k, v in new.items():
                parsed = _SETTINGS[k](v)
                if parsed != loaded.get(k):
                    # through the adjustable, so access control applies
                    getattr(a, k).set_target_value(parsed).wait()
                    changed.append(k)
            return changed

        self._status.setText("applying…")
        self._worker.run(
            None, write,
            lambda ch: (self._status.setText(f"changed: {', '.join(ch)}" if ch else "nothing changed"),
                        self.refresh(load_settings=True)),
            lambda e: QtWidgets.QMessageBox.warning(self.window, "eco alarm", f"not applied:\n{e}"))

    def _shelve(self):
        minutes, ok = QtWidgets.QInputDialog.getInt(self.window, "Shelve", "Silence for minutes:", 60, 1, 7 * 24 * 60)
        if ok:
            self._act(lambda: self.alarm.shelve(minutes), "shelve")

    def _act(self, func, what, reload=False):
        self._status.setText(f"{what}…")
        self._worker.run(None, func,
                         lambda _r: (self._status.setText(f"{what}: done"), self.refresh(load_settings=reload)),
                         lambda e: QtWidgets.QMessageBox.warning(self.window, "eco alarm", f"{what} failed:\n{e}"))


def _full_name(obj):
    try:
        return obj.alias.get_full_name()
    except Exception:
        return getattr(obj, "name", None)
