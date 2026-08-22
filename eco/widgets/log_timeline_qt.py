"""Qt timeline viewer: a scrollable list of TimelineEntry rows grouped
under day headers on the left, and MinimapCanvas -- a density-adaptive
sidebar synced to the list's scroll position -- on the right. Feed it
entries from eco.widgets.kernel_registry (via eco.logs.widget()) or a
cheap scilog metadata query (via eco.logs.scilog()); the widget itself
doesn't know or care which, as long as it gets a list of TimelineEntry.

Mirrors eco.widgets.ioc_finder_qt's standalone-window convention
(LogTimelineQtWindow / make_log_timeline_qt_window): non-blocking if a
Qt event loop is already pumping (a running IPython/eco console), a
normal blocking app.exec_() otherwise.
"""
import math
import time as _time

from qtpy import QtCore, QtGui, QtWidgets

from eco.widgets.log_timeline_common import nearest_entry, bucket_density

_N_BUCKETS = 140
_MIN_SPAN = 180.0  # seconds

_KIND_COLORS = {
    "input": "#1E7772",
    "widget_control": "#3D5EA8",
    "error": "#AE3A2E",
    "session": "#B85E19",
}
#: kinds whose .text is directly runnable Python -- see
#: LogTimelineQt._copy_selected_as_script and KernelSession.log_input /
#: log_widget_control's matching "code-is-the-text" contract.
_CODE_KINDS = ("input", "widget_control")
_MATCH_COLOR = "#7A4FB0"


class MinimapCanvas(QtWidgets.QWidget):
    """Bucket height is log(1+count) (see log_timeline_common.
    bucket_density), so quiet stretches collapse to a hairline and bursts
    get real space. Plain wheel calls `on_scroll` (forward it to whatever
    actually scrolls); Ctrl/Cmd+wheel zooms the time window itself --
    Ctrl is also how Qt reports trackpad pinch, so pinch-to-zoom works
    for free. Drag scrubs, calling `on_scrub(t)` continuously and
    `on_scrub_end()` on release."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(128)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.entries = []
        self.t0 = 0.0
        self.t1 = 1.0
        self.full_t0 = 0.0
        self.full_t1 = 1.0
        self.matches = []
        self.visible_range = None
        self._layout = None
        self._dragging = False
        self.on_zoom = None
        self.on_scroll = None
        self.on_scrub = None
        self.on_scrub_end = None

    def set_data(self, entries, full_range):
        self.entries = entries
        self.full_t0, self.full_t1 = full_range
        if self.t0 == self.t1:
            self.t0, self.t1 = full_range
        self._layout = None
        self.update()

    def set_window(self, t0, t1):
        self.t0, self.t1 = t0, t1
        self._layout = None
        self.update()

    def set_matches(self, matches):
        self.matches = matches
        self.update()

    def set_visible_range(self, rng):
        self.visible_range = rng
        self.update()

    # -- layout: mirrors bucket_density()'s log(1+count) weighting --------

    def _compute_layout(self):
        h = max(1, self.height())
        counts, errors = bucket_density(self.entries, self.t0, self.t1, _N_BUCKETS)
        weights = [0.14 + math.log1p(c) for c in counts]
        total = sum(weights) or 1.0
        tops = [0.0] * (_N_BUCKETS + 1)
        acc = 0.0
        for i, w in enumerate(weights):
            tops[i] = acc
            acc += h * (w / total)
        tops[_N_BUCKETS] = float(h)
        self._layout = {"tops": tops, "counts": counts, "errors": errors}
        return self._layout

    def _bucket_range(self, i):
        span = self.t1 - self.t0
        return self.t0 + span * i / _N_BUCKETS, self.t0 + span * (i + 1) / _N_BUCKETS

    def _y_to_t(self, y):
        layout = self._layout or self._compute_layout()
        tops = layout["tops"]
        for i in range(_N_BUCKETS):
            if tops[i] <= y <= tops[i + 1]:
                frac = (y - tops[i]) / (tops[i + 1] - tops[i]) if tops[i + 1] > tops[i] else 0
                lo, hi = self._bucket_range(i)
                return lo + frac * (hi - lo)
        return self.t0 if y < tops[0] else self.t1

    def _t_to_y(self, t):
        layout = self._layout or self._compute_layout()
        span = self.t1 - self.t0 or 1.0
        bf = (t - self.t0) / span * _N_BUCKETS
        i = max(0, min(_N_BUCKETS - 1, int(bf)))
        frac = bf - i
        tops = layout["tops"]
        return tops[i] + frac * (tops[i + 1] - tops[i])

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        pal = self.palette()
        p.fillRect(0, 0, w, h, pal.color(QtGui.QPalette.Base))

        layout = self._compute_layout()
        tops, counts, errors = layout["tops"], layout["counts"], layout["errors"]
        bar_x, bar_w = 34, w - 34 - 10
        accent = QtGui.QColor(_KIND_COLORS["input"])
        hot = QtGui.QColor("#B85E19")
        err = QtGui.QColor(_KIND_COLORS["error"])
        dim = pal.color(QtGui.QPalette.Mid)
        max_count = max(counts) if counts else 1

        for i in range(_N_BUCKETS):
            y0, y1 = tops[i], tops[i + 1]
            bh = max(1.0, y1 - y0)
            c = counts[i]
            if c == 0:
                p.setOpacity(0.35)
                p.fillRect(QtCore.QRectF(bar_x, y0, 6, max(1.0, bh - 0.6)), dim)
                continue
            norm = min(1.0, math.log1p(c) / (math.log1p(max_count) or 1))
            col = err if errors[i] else _lerp(accent, hot, min(1.0, c / 30))
            p.setOpacity(0.45 + 0.55 * norm)
            bw = max(6.0, bar_w * (0.35 + 0.65 * norm))
            p.fillRect(QtCore.QRectF(bar_x, y0, bw, max(1.0, bh - 0.6)), col)
        p.setOpacity(1.0)

        # date/time tick labels -- skip any that lands too close to the
        # last one drawn, same collision rule as the HTML minimap
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        font.setPointSize(max(7, font.pointSize() - 2))
        p.setFont(font)
        p.setPen(dim)
        span = self.t1 - self.t0
        show_hours = span < 4 * 86400
        last_key, last_y = None, -999
        for i in range(_N_BUCKETS):
            lo, _hi = self._bucket_range(i)
            dt = _time.localtime(lo)
            key = (dt.tm_yday, dt.tm_year, dt.tm_hour) if show_hours else (dt.tm_yday, dt.tm_year)
            y = tops[i]
            if key == last_key or y - last_y < 13:
                continue
            last_key, last_y = key, y
            label = (
                _time.strftime("%H:00", dt)
                if show_hours
                else _time.strftime("%b", dt) + " " + str(dt.tm_mday)
            )
            p.drawText(QtCore.QPointF(2, min(h - 6, max(8, y + 4))), label)
            p.drawLine(QtCore.QPointF(bar_x - 4, y), QtCore.QPointF(bar_x, y))

        # currently-visible band, synced from the log pane's scroll position
        if self.visible_range:
            v0, v1 = self.visible_range
            y0 = self._t_to_y(max(self.t0, v0))
            y1 = self._t_to_y(min(self.t1, v1))
            band = QtGui.QColor(accent)
            band.setAlpha(40)
            p.fillRect(QtCore.QRectF(0, y0, w, max(2.0, y1 - y0)), band)
            pen = QtGui.QPen(accent)
            pen.setWidthF(1.5)
            p.setPen(pen)
            p.drawRect(QtCore.QRectF(0.75, y0, w - 1.5, max(2.0, y1 - y0)))

        # search matches, interleaved with the date ticks in the same gutter
        if self.matches:
            pen = QtGui.QPen(QtGui.QColor(_MATCH_COLOR))
            pen.setWidthF(2.0)
            p.setPen(pen)
            for e in self.matches:
                if e.t < self.t0 or e.t > self.t1:
                    continue
                y = self._t_to_y(e.t)
                p.drawLine(QtCore.QPointF(17, y), QtCore.QPointF(bar_x - 6, y))

    # -- interaction ----------------------------------------------------

    def wheelEvent(self, event):
        is_zoom = bool(event.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier))
        delta = event.angleDelta().y()
        if not is_zoom:
            if self.on_scroll:
                self.on_scroll(-delta)
            event.accept()
            return
        y = event.position().y() if hasattr(event, "position") else event.pos().y()
        t_at_cursor = self._y_to_t(y)
        factor = math.exp(-delta * 0.0016)
        span = (self.t1 - self.t0) * factor
        frac = (t_at_cursor - self.t0) / (self.t1 - self.t0) if self.t1 > self.t0 else 0
        t0 = t_at_cursor - span * frac
        t0, t1 = _clamp_window(t0, t0 + span, self.full_t0, self.full_t1, _MIN_SPAN)
        if self.on_zoom:
            self.on_zoom(t0, t1)
        event.accept()

    def mousePressEvent(self, event):
        self._dragging = True
        self.setCursor(QtCore.Qt.ClosedHandCursor)
        self._scrub(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._scrub(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.setCursor(QtCore.Qt.OpenHandCursor)
            if self.on_scrub_end:
                self.on_scrub_end()

    def _scrub(self, event):
        y = event.position().y() if hasattr(event, "position") else event.pos().y()
        t = self._y_to_t(max(0, min(self.height(), y)))
        if self.on_scrub:
            self.on_scrub(t)


def _lerp(a, b, f):
    r = a.red() + (b.red() - a.red()) * f
    g = a.green() + (b.green() - a.green()) * f
    bl = a.blue() + (b.blue() - a.blue()) * f
    return QtGui.QColor(int(r), int(g), int(bl))


def _clamp_window(t0, t1, full0, full1, min_span):
    span = t1 - t0
    if span < min_span:
        c = (t0 + t1) / 2
        t0, t1 = c - min_span / 2, c + min_span / 2
        span = min_span
    if span > (full1 - full0):
        return full0, full1
    if t0 < full0:
        t1 += full0 - t0
        t0 = full0
    if t1 > full1:
        t0 -= t1 - full1
        t1 = full1
    return t0, min(t1, full1)


class LogTimelineQt(QtWidgets.QWidget):
    """Embeddable widget: pass a list of TimelineEntry. `on_open(entry)`,
    if given, is called on the GUI thread when an entry with a `.ref` is
    activated (double-click/Enter), and should return HTML/plain text to
    show in the detail panel below the list -- the lazy full-content
    fetch for sources (like scilog) where `entry.text` is only a cheap
    preview. Leave `on_open` as None for a source where `entry.text` is
    already the whole thing (kernel logs)."""

    def __init__(self, entries, title="Log timeline", on_open=None, parent=None):
        super().__init__(parent)
        self.entries = sorted(entries, key=lambda e: e.t)
        self.on_open = on_open
        self._kind_checkboxes = {}  # kind -> QCheckBox, see _build_filter_row
        self.matches = []
        self.match_idx = -1

        self._build_ui(title)
        self._populate()
        if self.entries:
            self.minimap.set_data(self.entries, (self.entries[0].t, self.entries[-1].t))
            self._update_zoom_readout()
        self._reposition_sticky()
        self._sync_visible_range()

    def _build_ui(self, title):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(10, 8, 10, 8)
        toolbar.addWidget(QtWidgets.QLabel(f"<b>{title}</b>"))
        toolbar.addStretch(1)
        self.zoom_label = QtWidgets.QLabel()
        self.zoom_label.setStyleSheet("color: palette(mid);")
        toolbar.addWidget(self.zoom_label)
        reset_btn = QtWidgets.QPushButton("Reset zoom")
        reset_btn.clicked.connect(self._reset_zoom)
        toolbar.addWidget(reset_btn)
        root.addLayout(toolbar)

        findbar = QtWidgets.QHBoxLayout()
        findbar.setContentsMargins(10, 4, 10, 4)
        findbar.addWidget(QtWidgets.QLabel("Find"))
        self.find_input = QtWidgets.QLineEdit()
        self.find_input.setPlaceholderText("text, kind, or tag")
        self.find_input.textChanged.connect(self._run_search)
        self.find_input.returnPressed.connect(lambda: self._go_to_match(self.match_idx + 1))
        findbar.addWidget(self.find_input, 1)
        self.match_count = QtWidgets.QLabel()
        findbar.addWidget(self.match_count)
        prev_btn = QtWidgets.QToolButton()
        prev_btn.setText("↑")
        prev_btn.setToolTip("Previous match (Shift+Enter)")
        prev_btn.clicked.connect(lambda: self._go_to_match(self.match_idx - 1))
        findbar.addWidget(prev_btn)
        next_btn = QtWidgets.QToolButton()
        next_btn.setText("↓")
        next_btn.setToolTip("Next match (Enter)")
        next_btn.clicked.connect(lambda: self._go_to_match(self.match_idx + 1))
        findbar.addWidget(next_btn)
        root.addLayout(findbar)

        root.addLayout(self._build_filter_row())
        root.addLayout(self._build_script_row())

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        self.list = QtWidgets.QListWidget()
        self.list.setUniformItemSizes(True)
        # click+drag / Shift+click / Ctrl+click ranges and multi-select --
        # what "select entries to copy as a script" needs; Qt's own
        # built-in behaviour once this is set, nothing else to wire up.
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.itemActivated.connect(self._open_entry)
        self.list.itemDoubleClicked.connect(self._open_entry)
        self.list.verticalScrollBar().valueChanged.connect(self._on_scroll)
        top_layout.addWidget(self.list, 1)

        self.sticky = QtWidgets.QLabel(self.list.viewport())
        self.sticky.setStyleSheet(
            "background: palette(alternate-base); padding: 3px 10px; font-weight: 600;"
        )
        self.sticky.hide()

        self.minimap = MinimapCanvas()
        self.minimap.on_zoom = self._set_window
        self.minimap.on_scroll = self._forward_scroll
        self.minimap.on_scrub = self._scrub_to
        top_layout.addWidget(self.minimap, 0)

        split.addWidget(top)

        self.detail = QtWidgets.QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.detail.hide()
        split.addWidget(self.detail)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 2)

        root.addWidget(split, 1)

    def _build_filter_row(self):
        """One checkbox per distinct `kind` present in self.entries (e.g.
        input/widget_control/result/stream/error/session) -- unticking one
        hides its rows via _apply_filters, same list, no rebuild. All on
        by default."""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(10, 4, 10, 4)
        row.addWidget(QtWidgets.QLabel("Show:"))
        for kind in sorted({e.kind for e in self.entries}):
            cb = QtWidgets.QCheckBox(kind)
            cb.setChecked(True)
            if kind in _KIND_COLORS:
                cb.setStyleSheet(f"color: {_KIND_COLORS[kind]};")
            cb.toggled.connect(self._apply_filters)
            self._kind_checkboxes[kind] = cb
            row.addWidget(cb)
        row.addStretch(1)
        return row

    def _build_script_row(self):
        """"Copy Selected as Script": input/widget_control entries in the
        current selection (see ExtendedSelection above) become script
        lines verbatim (both kinds' .text is already runnable Python --
        see KernelSession.log_input/log_widget_control); anything else
        selected is kept as a `# [kind] ...` comment, for context, not
        executed. "Replicate timing" optionally inserts time.sleep(dt)
        between consecutive lines, dt being the real gap (in seconds)
        between those two entries' timestamps -- so replaying the script
        reproduces the original pacing, not just the sequence."""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(10, 4, 10, 4)
        copy_btn = QtWidgets.QPushButton("Copy Selected as Script")
        copy_btn.setToolTip(
            "Select entries above (click, Shift+click for a range, Ctrl+click "
            "to add) then click this to copy them as Python to the clipboard"
        )
        copy_btn.clicked.connect(self._copy_selected_as_script)
        row.addWidget(copy_btn)
        self.timing_checkbox = QtWidgets.QCheckBox("Replicate timing (sleep calls)")
        row.addWidget(self.timing_checkbox)
        self.script_status = QtWidgets.QLabel()
        self.script_status.setStyleSheet("color: palette(mid);")
        row.addWidget(self.script_status)
        row.addStretch(1)
        return row

    def _apply_filters(self):
        active = {k for k, cb in self._kind_checkboxes.items() if cb.isChecked()}
        for i in range(self.list.count()):
            item = self.list.item(i)
            e = item.data(QtCore.Qt.UserRole)
            if e is None:
                continue  # day-header row -- always visible
            item.setHidden(e.kind not in active)
        self._reposition_sticky()
        self._sync_visible_range()

    def _copy_selected_as_script(self):
        selected = []
        for item in self.list.selectedItems():
            if item.isHidden():
                continue  # filtered out -- selection can include hidden rows
            e = item.data(QtCore.Qt.UserRole)
            if e is not None:
                selected.append(e)
        selected.sort(key=lambda e: e.t)
        if not selected:
            self.script_status.setText("nothing selected")
            return

        include_timing = self.timing_checkbox.isChecked()
        lines = []
        prev_t = None
        for e in selected:
            if include_timing and prev_t is not None:
                dt = e.t - prev_t
                if dt > 0.05:  # skip negligible/near-zero gaps
                    lines.append(f"time.sleep({dt:.3f})")
            if e.kind in _CODE_KINDS:
                lines.append(e.text)
            else:
                comment = e.text.replace("\n", "\n# ")
                lines.append(f"# [{e.kind}] {comment}")
            prev_t = e.t

        header = "import time\n\n" if include_timing else ""
        script = header + "\n".join(lines) + "\n"
        QtWidgets.QApplication.clipboard().setText(script)
        self.script_status.setText(f"copied {len(selected)} entries ({len(lines)} lines)")

    def _populate(self):
        self.list.clear()
        cur_day = None
        for e in self.entries:
            dt = _time.localtime(e.t)
            day_key = (dt.tm_year, dt.tm_yday)
            if day_key != cur_day:
                cur_day = day_key
                label = _time.strftime("%A, %B", dt) + " " + str(dt.tm_mday)
                header = QtWidgets.QListWidgetItem(label)
                header.setFlags(QtCore.Qt.NoItemFlags)
                font = header.font()
                font.setBold(True)
                header.setFont(font)
                header.setBackground(QtGui.QColor(0, 0, 0, 18))
                self.list.addItem(header)
            ts = _time.strftime("%H:%M", dt)
            tags = f"  [{', '.join(e.tags)}]" if e.tags else ""
            item = QtWidgets.QListWidgetItem(f"{ts}  {e.kind:<8} {e.text}{tags}")
            item.setData(QtCore.Qt.UserRole, e)
            if e.is_error:
                item.setForeground(QtGui.QColor(_KIND_COLORS["error"]))
            elif e.kind in _KIND_COLORS:
                item.setForeground(QtGui.QColor(_KIND_COLORS[e.kind]))
            self.list.addItem(item)

    # -- zoom / window -----------------------------------------------------

    def _set_window(self, t0, t1):
        self.minimap.set_window(t0, t1)
        self._update_zoom_readout()

    def _reset_zoom(self):
        if self.entries:
            self._set_window(self.entries[0].t, self.entries[-1].t)

    def _update_zoom_readout(self):
        span = self.minimap.t1 - self.minimap.t0
        if span < 3600:
            text = f"{span/60:.0f} min"
        elif span < 86400:
            text = f"{span/3600:.1f} hr"
        else:
            text = f"{span/86400:.1f} days"
        self.zoom_label.setText(f"window: {text}")

    def _forward_scroll(self, delta):
        bar = self.list.verticalScrollBar()
        bar.setValue(bar.value() + int(delta / 3))

    def _scrub_to(self, t):
        e = nearest_entry(self.entries, t)
        if e is not None:
            self._scroll_to_entry(e)

    def _scroll_to_entry(self, entry):
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(QtCore.Qt.UserRole) is entry:
                self.list.scrollToItem(item, QtWidgets.QAbstractItemView.PositionAtCenter)
                self.list.setCurrentItem(item)
                return

    # -- scroll sync / sticky header ---------------------------------------

    def _on_scroll(self, _value):
        self._reposition_sticky()
        self._sync_visible_range()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_sticky()
        self._sync_visible_range()

    def _reposition_sticky(self):
        top_item = self.list.itemAt(0, 2)
        if top_item is None:
            self.sticky.hide()
            return
        row = self.list.row(top_item)
        day_text = None
        for i in range(row, -1, -1):
            it = self.list.item(i)
            if it.flags() == QtCore.Qt.NoItemFlags:
                day_text = it.text()
                break
        if day_text is None:
            self.sticky.hide()
            return
        self.sticky.setGeometry(0, 0, self.list.viewport().width(), 22)
        self.sticky.setText(day_text)
        self.sticky.show()
        self.sticky.raise_()

    def _sync_visible_range(self):
        first = self.list.itemAt(0, 2)
        last = self.list.itemAt(0, max(2, self.list.viewport().height() - 2))
        f = first.data(QtCore.Qt.UserRole) if first else None
        l = last.data(QtCore.Qt.UserRole) if last else None
        self.minimap.set_visible_range((f.t, l.t) if (f is not None and l is not None) else None)

    # -- find ---------------------------------------------------------------

    def _run_search(self, text):
        q = text.strip().lower()
        self.matches = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            e = item.data(QtCore.Qt.UserRole)
            if e is None:
                continue
            if q and q in item.text().lower():
                item.setBackground(QtGui.QColor(_MATCH_COLOR).lighter(190))
                self.matches.append(e)
            else:
                item.setBackground(QtGui.QBrush())
        self.match_idx = 0 if self.matches else -1
        self._update_match_count()
        self.minimap.set_matches(self.matches)
        if self.matches:
            self._scroll_to_entry(self.matches[0])

    def _update_match_count(self):
        q = self.find_input.text().strip()
        if not q:
            self.match_count.setText("")
        elif self.matches:
            self.match_count.setText(f"{self.match_idx+1} / {len(self.matches)}")
        else:
            self.match_count.setText("no matches")

    def _go_to_match(self, i):
        if not self.matches:
            return
        self.match_idx = i % len(self.matches)
        self._scroll_to_entry(self.matches[self.match_idx])
        self._update_match_count()

    # -- detail / lazy open --------------------------------------------------

    def _open_entry(self, item):
        e = item.data(QtCore.Qt.UserRole)
        if e is None or e.ref is None or self.on_open is None:
            return
        self.detail.show()
        self.detail.setHtml("<i>loading…</i>")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            html = self.on_open(e)
        except Exception as exc:
            html = f"<b>failed to load:</b> {exc}"
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.detail.setHtml(html or "<i>(empty)</i>")


_app_ref = None  # keep a strong reference to any QApplication we create ourselves


class LogTimelineQtWindow:
    """Standalone-window wrapper, mirrors IocFinderQtWindow."""

    def __init__(self, entries, title="Log timeline", on_open=None, auto_start=True):
        self._entries = entries
        self._title = title
        self._on_open = on_open
        self.window = None
        self.viewer = None
        if auto_start:
            self.start()

    def _build_window(self):
        global _app_ref
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])
            _app_ref = app
        self.viewer = LogTimelineQt(self._entries, title=self._title, on_open=self._on_open)
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle(self._title)
        self.window.setCentralWidget(self.viewer)
        # WA_DeleteOnClose + destroyed: without it, closing via the
        # window's own native close (X) button just hides it -- it's
        # never actually destroyed, so .window/.viewer would stay stale
        # non-None references pointing at a closed window. See
        # eco.widgets.qt_lifecycle's module docstring for the fuller why.
        self.window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.window.destroyed.connect(self._on_window_destroyed)
        self.window.resize(900, 640)
        self.window.show()

    def _on_window_destroyed(self, *args):
        self.window = None
        self.viewer = None

    def run(self):
        app = QtWidgets.QApplication.instance()
        created = app is None
        if created:
            app = QtWidgets.QApplication([])
        if self.window is None:
            self._build_window()
        if created:
            app.exec_()

    def start(self):
        if self.window is not None:
            return
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
                "eco log timeline: a different GUI event loop "
                f"('{active}') is already active, so the window can't be "
                "pumped non-blockingly alongside it. Showing it in "
                "blocking mode instead (closing the window returns control)."
            )
            self.run()
            return
        self._build_window()

    def stop(self):
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None
            self.viewer = None


def make_log_timeline_qt_window(entries, title="Log timeline", on_open=None, auto_start=True):
    return LogTimelineQtWindow(entries, title=title, on_open=on_open, auto_start=auto_start)
