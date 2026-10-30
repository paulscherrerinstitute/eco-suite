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

from eco.widgets.log_timeline_common import bucket_density_sorted, nearest_entry, stream_group

_N_BUCKETS = 140
_MIN_SPAN = 180.0  # seconds

_KIND_COLORS = {
    "input": "#1E7772",
    "widget_control": "#3D5EA8",
    "error": "#AE3A2E",
    "warning": "#A07A10",
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
        self._ts = []  # sorted entry timestamps, see set_data
        self._error_ts = []
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
        self._ts = [e.t for e in entries]
        self._error_ts = [e.t for e in entries if e.is_error]
        self.full_t0, self.full_t1 = full_range
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
        key = (h, self.t0, self.t1, len(self._ts))
        if self._layout is not None and self._layout.get("key") == key:
            return self._layout
        counts, errors = bucket_density_sorted(self._ts, self._error_ts, self.t0, self.t1, _N_BUCKETS)
        weights = [0.14 + math.log1p(c) for c in counts]
        total = sum(weights) or 1.0
        tops = [0.0] * (_N_BUCKETS + 1)
        acc = 0.0
        for i, w in enumerate(weights):
            tops[i] = acc
            acc += h * (w / total)
        tops[_N_BUCKETS] = float(h)
        self._layout = {"tops": tops, "counts": counts, "errors": errors, "key": key}
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
            # one tick per pixel row is all that can be seen anyway --
            # thousands of matches shouldn't mean thousands of drawLine calls
            drawn = set()
            for e in self.matches:
                if e.t < self.t0 or e.t > self.t1:
                    continue
                y = int(self._t_to_y(e.t))
                if y in drawn:
                    continue
                drawn.add(y)
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


#: longest single line shown in the list; the full text is in the tooltip
#: and in the detail pane. A `result` repr of a big array, or a scan's
#: progress output, can be tens of kB -- laying those out per row is slow
#: and unreadable anyway.
_MAX_ROW_CHARS = 240

#: window sizing, see initial_window_size. The window must never grow to fit
#: its contents: it once had a minimum width of >100000 px (one checkbox
#: per session log file ever written, in a single row).
DEFAULT_SIZE = (1000, 680)
PARENT_FRACTION = 0.7
MAX_SCREEN_FRACTION = 0.9
MIN_SIZE = (480, 320)


def _row_text(e):
    first, _, rest = e.text.partition("\n")
    if len(first) > _MAX_ROW_CHARS:
        first = first[:_MAX_ROW_CHARS] + "…"
    elif rest:
        first += "  …"
    tags = f"  [{', '.join(e.tags)}]" if e.tags else ""
    return f"{_time.strftime('%H:%M', _time.localtime(e.t))}  {e.kind:<8} {first}{tags}"


class _TimelineModel(QtCore.QAbstractListModel):
    """Rows are either a day header (a str) or a TimelineEntry -- only the
    *visible* ones, rebuilt in one Python pass by set_rows(). Qt asks for
    text/colour only for rows actually on screen, so this costs the same
    with 400 or 400000 entries, unlike one QListWidgetItem per entry."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.match_ids = set()

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def entry(self, row):
        r = self.rows[row] if 0 <= row < len(self.rows) else None
        return None if isinstance(r, str) else r

    def flags(self, index):
        if isinstance(self.rows[index.row()], str):
            return QtCore.Qt.NoItemFlags
        return QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable

    def data(self, index, role=QtCore.Qt.DisplayRole):
        r = self.rows[index.row()]
        if isinstance(r, str):
            if role == QtCore.Qt.DisplayRole:
                return r
            if role == QtCore.Qt.FontRole:
                f = QtGui.QFont()
                f.setBold(True)
                return f
            if role == QtCore.Qt.BackgroundRole:
                return QtGui.QColor(0, 0, 0, 18)
            return None
        if role == QtCore.Qt.DisplayRole:
            return _row_text(r)
        if role == QtCore.Qt.UserRole:
            return r
        if role == QtCore.Qt.ToolTipRole:
            text = r.text if len(r.text) < 4000 else r.text[:4000] + "\n…"
            return f"{r.session}\n{text}" if r.session else text
        if role == QtCore.Qt.ForegroundRole:
            if r.is_error:
                return QtGui.QColor(_KIND_COLORS["error"])
            if r.kind in _KIND_COLORS:
                return QtGui.QColor(_KIND_COLORS[r.kind])
        if role == QtCore.Qt.BackgroundRole and id(r) in self.match_ids:
            return QtGui.QColor(_MATCH_COLOR).lighter(190)
        return None


class _CheckListPopup(QtWidgets.QWidget):
    """Search box + checkable list + All/None/Only-matching, shown as a
    popup under a toolbar button. Holds any number of values without
    taking any toolbar width -- the replacement for one QCheckBox per
    value laid out in a row."""

    changed = QtCore.Signal()

    def __init__(self, values, colors=None, parent=None):
        super().__init__(parent, QtCore.Qt.Popup)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("filter this list")
        self.search.textChanged.connect(self._filter_list)
        lay.addWidget(self.search)
        self.list = QtWidgets.QListWidget()
        self.list.setMinimumWidth(320)
        self.list.setMaximumHeight(420)
        lay.addWidget(self.list)
        btns = QtWidgets.QHBoxLayout()
        for label, fn in (("All", self._all), ("None", self._none), ("Only listed", self._only_listed)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            btns.addWidget(b)
        lay.addLayout(btns)
        self.items = {}
        for v in values:
            it = QtWidgets.QListWidgetItem(v)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked)
            if colors and v in colors:
                it.setForeground(QtGui.QColor(colors[v]))
            self.list.addItem(it)
            self.items[v] = it
        self.list.itemChanged.connect(lambda _it: self.changed.emit())

    def checked(self):
        return {v for v, it in self.items.items() if it.checkState() == QtCore.Qt.Checked}

    def set_checked(self, value, on):
        self.items[value].setCheckState(QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)

    def _set_many(self, predicate):
        self.list.blockSignals(True)
        for v, it in self.items.items():
            it.setCheckState(QtCore.Qt.Checked if predicate(v, it) else QtCore.Qt.Unchecked)
        self.list.blockSignals(False)
        self.changed.emit()

    def _all(self):
        self._set_many(lambda v, it: True)

    def _none(self):
        self._set_many(lambda v, it: False)

    def _only_listed(self):
        self._set_many(lambda v, it: not it.isHidden())

    def _filter_list(self, text):
        q = text.strip().lower()
        for v, it in self.items.items():
            it.setHidden(bool(q) and q not in v.lower())


class _FilterButton(QtWidgets.QToolButton):
    """"Kinds: 5/8 ▾" -- fixed, small width no matter how many values."""

    def __init__(self, label, values, colors=None, parent=None):
        super().__init__(parent)
        self.label = label
        self.popup = _CheckListPopup(values, colors=colors, parent=self)
        self.popup.changed.connect(self._update_text)
        self.clicked.connect(self._show_popup)
        self._update_text()

    def _show_popup(self):
        self.popup.adjustSize()
        self.popup.move(self.mapToGlobal(QtCore.QPoint(0, self.height())))
        self.popup.show()
        self.popup.search.setFocus()

    def _update_text(self):
        n, total = len(self.popup.checked()), len(self.popup.items)
        shown = "all" if n == total else f"{n}/{total}"
        self.setText(f"{self.label}: {shown} ▾")


class LogTimelineQt(QtWidgets.QWidget):
    """Embeddable widget: pass a list of TimelineEntry (or None and call
    set_entries() later -- the window wrapper loads in a background thread
    and does exactly that). `on_open(entry)`, if given, is called on the
    GUI thread when an entry with a `.ref` is activated (double-click/
    Enter), and should return HTML/plain text for the detail panel -- the
    lazy full-content fetch for sources (like scilog) where `entry.text` is
    only a cheap preview. Entries without a `.ref` show their full text in
    the detail panel on selection.

    Filters are three toolbar buttons, each opening a searchable checklist:
    Kinds (input/result/stream/error/...), Sources (the few "kind:label"
    groups, e.g. "desktop:bernina") and Sessions (one per log file -- can be
    hundreds; that's why they're in a popup, not a row of checkboxes). A row
    is shown if its kind, source and session are all checked (AND).
    """

    def __init__(self, entries=None, title="Log timeline", on_open=None, parent=None):
        super().__init__(parent)
        self.on_open = on_open
        self.title = title
        self.entries = []
        self.matches = []
        self.match_idx = -1
        self._row_of = {}  # id(entry) -> row in the model
        self._search_text = {}  # id(entry) -> lowercased row text, built lazily
        self._build_ui(title)
        self.set_entries(entries or [])

    # -- public API ------------------------------------------------------------

    def set_entries(self, entries, status=None):
        self.entries = sorted(entries, key=lambda e: e.t)
        self._search_text = {}
        self._rebuild_filter_buttons()
        if status is not None:
            self.status.setText(status)
        self._apply_filters()
        self._reset_zoom()

    def set_status(self, text):
        self.status.setText(text)

    def visible_entries(self):
        return [r for r in self.model.rows if not isinstance(r, str)]

    def set_kind_visible(self, kind, on):
        self.kind_filter.popup.set_checked(kind, on)

    def set_session_visible(self, session, on):
        self.session_filter.popup.set_checked(session, on)

    def set_source_visible(self, group, on):
        self.source_filter.popup.set_checked(group, on)

    def select_entries(self, entries):
        sel = self.list.selectionModel()
        sel.clearSelection()
        for e in entries:
            row = self._row_of.get(id(e))
            if row is not None:
                sel.select(self.model.index(row), QtCore.QItemSelectionModel.Select)

    # -- UI --------------------------------------------------------------------

    def _build_ui(self, title):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(10, 8, 10, 4)
        toolbar.addWidget(QtWidgets.QLabel(f"<b>{title}</b>"))
        self.status = QtWidgets.QLabel()
        self.status.setStyleSheet("color: palette(mid);")
        # Ignored: a long status must never widen the window
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        toolbar.addWidget(self.status, 1)
        self.zoom_label = QtWidgets.QLabel()
        self.zoom_label.setStyleSheet("color: palette(mid);")
        toolbar.addWidget(self.zoom_label)
        reset_btn = QtWidgets.QPushButton("Reset zoom")
        reset_btn.clicked.connect(self._reset_zoom)
        toolbar.addWidget(reset_btn)
        root.addLayout(toolbar)

        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(10, 4, 10, 4)
        self._filter_bar = bar
        self.kind_filter = self.source_filter = self.session_filter = None
        self._filter_placeholder = QtWidgets.QWidget()
        bar.addWidget(self._filter_placeholder)
        bar.addWidget(QtWidgets.QLabel("Find"))
        self.find_input = QtWidgets.QLineEdit()
        self.find_input.setPlaceholderText("text, kind, or tag")
        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)  # debounce: don't search per keystroke
        self._search_timer.timeout.connect(lambda: self._run_search(self.find_input.text()))
        self.find_input.textChanged.connect(lambda _t: self._search_timer.start())
        self.find_input.returnPressed.connect(self._on_find_return)
        bar.addWidget(self.find_input, 1)
        self.match_count = QtWidgets.QLabel()
        bar.addWidget(self.match_count)
        prev_btn = QtWidgets.QToolButton()
        prev_btn.setText("↑")
        prev_btn.setToolTip("Previous match")
        prev_btn.clicked.connect(lambda: self._go_to_match(self.match_idx - 1))
        bar.addWidget(prev_btn)
        next_btn = QtWidgets.QToolButton()
        next_btn.setText("↓")
        next_btn.setToolTip("Next match (Enter)")
        next_btn.clicked.connect(lambda: self._go_to_match(self.match_idx + 1))
        bar.addWidget(next_btn)
        root.addLayout(bar)

        root.addLayout(self._build_script_row())

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        self.model = _TimelineModel(self)
        self.list = QtWidgets.QListView()
        self.list.setModel(self.model)
        self.list.setUniformItemSizes(True)
        self.list.setTextElideMode(QtCore.Qt.ElideRight)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        # click+drag / Shift+click / Ctrl+click ranges and multi-select --
        # what "select entries to copy as a script" needs
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.activated.connect(self._open_index)
        self.list.doubleClicked.connect(self._open_index)
        self.list.selectionModel().currentChanged.connect(self._show_current)
        self.list.verticalScrollBar().valueChanged.connect(self._on_scroll)
        top_layout.addWidget(self.list, 1)

        self.sticky = QtWidgets.QLabel(self.list.viewport())
        self.sticky.setStyleSheet("background: palette(alternate-base); padding: 3px 10px; font-weight: 600;")
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

    def _rebuild_filter_buttons(self):
        old = [self.kind_filter, self.source_filter, self.session_filter, self._filter_placeholder]
        kinds = sorted({e.kind for e in self.entries})
        sessions = sorted({e.session for e in self.entries if e.session}, reverse=True)
        groups = sorted({stream_group(s) for s in sessions})
        self.kind_filter = _FilterButton("Kinds", kinds, colors=_KIND_COLORS)
        self.source_filter = _FilterButton("Sources", groups)
        self.session_filter = _FilterButton("Sessions", sessions)
        self.source_filter.setVisible(bool(groups))
        self.session_filter.setVisible(bool(sessions))
        for i, w in enumerate((self.kind_filter, self.source_filter, self.session_filter)):
            w.popup.changed.connect(self._apply_filters)
            self._filter_bar.insertWidget(i, w)
        for w in old:
            if w is not None:
                self._filter_bar.removeWidget(w)
                w.deleteLater()
        self._filter_placeholder = None

    def _build_script_row(self):
        """"Copy Selected as Script": input/widget_control entries in the
        current selection become script lines verbatim (both kinds' .text is
        already runnable Python -- see KernelSession.log_input/
        log_widget_control); anything else selected is kept as a
        `# [kind] ...` comment. "Replicate timing" inserts time.sleep(dt)
        between consecutive lines, dt being the real gap between them."""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(10, 4, 10, 4)
        copy_btn = QtWidgets.QPushButton("Copy Selected as Script")
        copy_btn.setToolTip(
            "Select entries below (click, Shift+click for a range, Ctrl+click "
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

    # -- filtering ----------------------------------------------------------

    def _apply_filters(self):
        kinds = self.kind_filter.popup.checked()
        sessions = self.session_filter.popup.checked()
        groups = self.source_filter.popup.checked()
        all_sessions = len(sessions) == len(self.session_filter.popup.items)
        all_groups = len(groups) == len(self.source_filter.popup.items)
        rows, row_of, day_end = [], {}, float("-inf")
        for e in self.entries:
            if e.kind not in kinds:
                continue
            if e.session:
                if not all_sessions and e.session not in sessions:
                    continue
                if not all_groups and stream_group(e.session) not in groups:
                    continue
            if e.t >= day_end:  # entries are sorted: localtime() once per day, not per entry
                dt = _time.localtime(e.t)
                rows.append(_time.strftime("%A, %B ", dt) + str(dt.tm_mday) + _time.strftime(", %Y", dt))
                day_end = _next_midnight(dt)
            row_of[id(e)] = len(rows)
            rows.append(e)
        self._row_of = row_of
        self.model.set_rows(rows)
        visible = [r for r in rows if not isinstance(r, str)]
        if visible:
            self.minimap.set_data(visible, (visible[0].t, visible[-1].t))
        else:
            self.minimap.set_data([], (0.0, 1.0))
        self._update_zoom_readout()
        if self.find_input.text().strip():
            self._run_search(self.find_input.text())
        self._reposition_sticky()
        self._sync_visible_range()

    # -- copy as script --------------------------------------------------------

    def _selected_entries(self):
        out = []
        for idx in self.list.selectionModel().selectedIndexes():
            e = self.model.entry(idx.row())
            if e is not None:
                out.append(e)
        out.sort(key=lambda e: e.t)
        return out

    def _copy_selected_as_script(self):
        selected = self._selected_entries()
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
        QtWidgets.QApplication.clipboard().setText(header + "\n".join(lines) + "\n")
        self.script_status.setText(f"copied {len(selected)} entries ({len(lines)} lines)")

    # -- zoom / window ---------------------------------------------------------

    def _set_window(self, t0, t1):
        self.minimap.set_window(t0, t1)
        self._update_zoom_readout()

    def _reset_zoom(self):
        self._set_window(self.minimap.full_t0, self.minimap.full_t1)

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
        e = nearest_entry(self.minimap.entries, t)
        if e is not None:
            self._scroll_to_entry(e)

    def _scroll_to_entry(self, entry):
        row = self._row_of.get(id(entry))
        if row is None:
            return
        idx = self.model.index(row)
        self.list.scrollTo(idx, QtWidgets.QAbstractItemView.PositionAtCenter)
        self.list.setCurrentIndex(idx)

    # -- scroll sync / sticky header -----------------------------------------

    def _on_scroll(self, _value):
        self._reposition_sticky()
        self._sync_visible_range()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_sticky()
        self._sync_visible_range()

    def _reposition_sticky(self):
        idx = self.list.indexAt(QtCore.QPoint(2, 2))
        if not idx.isValid():
            self.sticky.hide()
            return
        day_text = None
        for i in range(idx.row(), -1, -1):
            r = self.model.rows[i]
            if isinstance(r, str):
                day_text = r
                break
        if day_text is None:
            self.sticky.hide()
            return
        self.sticky.setGeometry(0, 0, self.list.viewport().width(), 22)
        self.sticky.setText(day_text)
        self.sticky.show()
        self.sticky.raise_()

    def _sync_visible_range(self):
        first = self.list.indexAt(QtCore.QPoint(2, 2))
        last = self.list.indexAt(QtCore.QPoint(2, max(2, self.list.viewport().height() - 2)))
        f = self.model.entry(first.row()) if first.isValid() else None
        if last.isValid():
            l = self.model.entry(last.row())
        else:
            l = self.model.entry(len(self.model.rows) - 1)
        self.minimap.set_visible_range((f.t, l.t) if (f is not None and l is not None) else None)

    # -- find -------------------------------------------------------------------

    def _on_find_return(self):
        if self._search_timer.isActive():
            self._search_timer.stop()
            self._run_search(self.find_input.text())
        else:
            self._go_to_match(self.match_idx + 1)

    def _run_search(self, text):
        q = text.strip().lower()
        matches = []
        if q:
            cache = self._search_text
            for e in self.visible_entries():
                key = id(e)
                hay = cache.get(key)
                if hay is None:
                    hay = cache[key] = f"{e.kind} {e.text} {' '.join(e.tags)}".lower()
                if q in hay:
                    matches.append(e)
        self.matches = matches
        self.model.match_ids = {id(e) for e in matches}
        self.model.dataChanged.emit(self.model.index(0), self.model.index(max(0, len(self.model.rows) - 1)))
        self.match_idx = 0 if matches else -1
        self._update_match_count()
        self.minimap.set_matches(matches)
        if matches:
            self._scroll_to_entry(matches[0])

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

    def _show_current(self, current, _previous):
        e = self.model.entry(current.row()) if current.isValid() else None
        if e is None or e.ref is not None:
            return
        if "\n" in e.text or len(e.text) > _MAX_ROW_CHARS:
            self.detail.show()
            self.detail.setPlainText(e.text)

    def _open_index(self, index):
        e = self.model.entry(index.row())
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


def _next_midnight(dt):
    """Epoch seconds of the local midnight after struct_time `dt` (DST-safe:
    mktime normalises day overflow and resolves the offset itself)."""
    return _time.mktime((dt.tm_year, dt.tm_mon, dt.tm_mday + 1, 0, 0, 0, 0, 0, -1))


def initial_window_size(parent=None, screen_geometry=None):
    """(w, h) for a new viewer window: PARENT_FRACTION of the parent window
    if there is one, else DEFAULT_SIZE; then capped to MAX_SCREEN_FRACTION
    of the screen and floored at MIN_SIZE. Pure function of its inputs
    (anything with width()/height()), so testable without a display."""
    if parent is not None and parent.width() > 0 and parent.height() > 0:
        w, h = int(parent.width() * PARENT_FRACTION), int(parent.height() * PARENT_FRACTION)
    else:
        w, h = DEFAULT_SIZE
    if screen_geometry is not None and screen_geometry.width() > 0 and screen_geometry.height() > 0:
        w = min(w, int(screen_geometry.width() * MAX_SCREEN_FRACTION))
        h = min(h, int(screen_geometry.height() * MAX_SCREEN_FRACTION))
    return max(MIN_SIZE[0], w), max(MIN_SIZE[1], h)


class _Loader(QtCore.QObject):
    """Runs `loader()` on a plain thread and hands its result back to the
    GUI thread through a queued signal: the window appears at once with
    "loading…" instead of the caller's GUI freezing while the logs parse."""

    done = QtCore.Signal(object, object)  # (result, error_text)

    def run_async(self, loader):
        import threading

        def work():
            try:
                result, err = loader(), None
            except BaseException as exc:  # never let a loader kill anything
                result, err = None, f"{type(exc).__name__}: {exc}"
            try:
                self.done.emit(result, err)
            except RuntimeError:
                pass  # receiver already deleted (window closed while loading)

        threading.Thread(target=work, name="eco-log-viewer-loader", daemon=True).start()


_app_ref = None  # keep a strong reference to any QApplication we create ourselves


class LogTimelineQtWindow:
    """Standalone-window wrapper, mirrors IocFinderQtWindow.

    `entries`: a ready list, or None together with `loader`: a callable
    returning `(entries, status_text)`, run off the GUI thread.
    `parent`: a QWidget whose window the initial size is derived from (see
    initial_window_size) -- the viewer still opens as its own top-level
    window, it is not docked or parented to it."""

    def __init__(self, entries, title="Log timeline", on_open=None, auto_start=True, loader=None, parent=None):
        self._entries = entries
        self._loader = loader
        self._title = title
        self._on_open = on_open
        self._parent = parent
        self._loader_obj = None
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
        self.viewer = LogTimelineQt(self._entries or [], title=self._title, on_open=self._on_open)
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle(self._title)
        self.window.setCentralWidget(self.viewer)
        # WA_DeleteOnClose + destroyed: without it, closing via the window's
        # own close (X) button only hides it. See eco.widgets.qt_lifecycle.
        self.window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.window.destroyed.connect(self._on_window_destroyed)
        parent_win = self._parent.window() if self._parent is not None else None
        screen = None
        try:
            scr = (parent_win.screen() if parent_win is not None else None) or app.primaryScreen()
            screen = scr.availableGeometry() if scr is not None else None
        except Exception:
            pass
        self.window.resize(*initial_window_size(parent_win, screen))
        self.window.show()
        if self._loader is not None:
            self.viewer.set_status("loading…")
            self._loader_obj = _Loader(self.viewer)
            self._loader_obj.done.connect(self._on_loaded)
            self._loader_obj.run_async(self._loader)

    def _on_loaded(self, result, error):
        if self.viewer is None:
            return
        try:
            if error is not None:
                self.viewer.set_status(f"loading failed: {error}")
                return
            entries, status = result
            self.viewer.set_entries(entries, status=status)
        except RuntimeError:
            pass  # window closed while loading: the widget is already deleted

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
        if QtWidgets.QApplication.instance() is not None and _qt_loop_running():
            # Already inside a running Qt event loop (eco desktop, or an
            # IPython session with %gui qt): just show the window. Touching
            # the IPython event-loop integration from inside a Qt slot is
            # never needed here and only adds ways to fail.
            self._build_window()
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


def _qt_loop_running():
    """True while QApplication.exec_() (or a nested loop) is pumping events --
    e.g. when called from a menu action in eco desktop."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    try:
        return QtCore.QThread.currentThread().loopLevel() > 0
    except Exception:
        return False


def make_log_timeline_qt_window(entries, title="Log timeline", on_open=None, auto_start=True, loader=None, parent=None):
    return LogTimelineQtWindow(
        entries, title=title, on_open=on_open, auto_start=auto_start, loader=loader, parent=parent
    )
