"""
Small, live-updating "gadget" widgets for one Adjustable/Detector each --
an LED, a bar/analog gauge, a tiny strip chart, a numeric KPI tile, a
dial/knob, a slider -- meant to be pulled out of an assembly's normal
property-grid view (eco.widgets.display_qt) and dropped onto a
eco.widgets.dashboard_qt.Dashboard to build a custom live-value board,
rather than living permanently in the property grid itself.

Discovery is deliberately minimal-footprint: right-click a parameter's
name in an assembly's Qt widget (see attach_indicator_menu, wired into
eco.widgets.display_qt.DisplayQt._build_row with a two-line hook) for an
"Add indicator" submenu -- no permanently-visible button clutters the
normal view.

All gadgets share _IndicatorBase: a bold title label (so a gadget
dragged out onto a dashboard full of others still says what it is), a
background poll thread reading item.get_current_value() (same
convention eco.widgets.display_qt.DisplayQt uses) marshaled to the GUI
thread via a Qt signal (same thread-safety pattern used throughout eco's
other Qt widgets this round -- see e.g. camserver_stream_qt._StreamBridge),
and a stop() that's called automatically when the gadget's window closes.

Read-only widgets (BarGauge, AnalogGauge, StripChart, NumericTile) work
for both Detectors and Adjustables (they just never write). The
interactive ones (Dial, Slider) write via item.set_target_value(...) --
same convention as eco.widgets.display_qt -- only if the item actually
has that method; otherwise they render read-only/disabled automatically,
so the same "Add indicator" menu works uniformly regardless of what kind
of item was right-clicked.

vmin/vmax for the range-based gadgets (BarGauge/AnalogGauge/Dial/Slider)
are guessed from a handful of common limit-attribute names (see
_guess_range) when created via the menu, falling back to 0..100 -- a
reasonable default, not a promise; pass explicit vmin/vmax when creating
one directly in code for anything that matters.

WHY `parent` COMES FIRST AND `item` IS OPTIONAL: every gadget class here
is directly usable as a Qt Designer "promoted widget" (Designer's real
mechanism for putting custom Python widgets in its palette -- see
eco.widgets.dashboard_qt.Dashboard.export_to_designer). Designer always
constructs a promoted widget as ``ClassName(parent)`` -- one positional
argument -- so `parent` has to be the first parameter and every other
argument, including `item`, needs a working default. A gadget built with
no `item` renders inert ("--", no polling) until set_item(item) attaches
a live one -- which is exactly what happens when a Designer-arranged
layout is loaded back via Dashboard.load_custom_panel: the .ui file
carries each gadget's accessibleName (its item's alias path, e.g.
"bernina.cam_west.intensity"), which is resolved back through the
namespace and fed to set_item.
"""
import logging
import math
import re
import threading
from collections import deque

from qtpy import QtCore, QtGui, QtWidgets

logger = logging.getLogger(__name__)


def _format_value(value):
    import enum

    if isinstance(value, enum.Enum):
        return value.name
    try:
        return f"{float(value):.4g}"
    except (TypeError, ValueError):
        return str(value)


def _guess_range(item):
    """Best-effort (vmin, vmax) from a handful of common limit-attribute
    names; (0.0, 100.0) if none are found or usable. A heuristic, not a
    guarantee -- pass explicit vmin/vmax to any gadget's constructor to
    override."""
    for lo_attr, hi_attr in (("low_limit", "high_limit"), ("llm", "hlm"), ("lolim", "hilim")):
        try:
            lo, hi = getattr(item, lo_attr, None), getattr(item, hi_attr, None)
            if lo is not None and hi is not None and float(hi) > float(lo):
                return float(lo), float(hi)
        except Exception:
            pass
    try:
        limits = getattr(item, "limits", None)
        if limits and len(limits) == 2 and float(limits[1]) > float(limits[0]):
            return float(limits[0]), float(limits[1])
    except Exception:
        pass
    return 0.0, 100.0


def item_path(item, fallback=None):
    """A re-resolvable dotted path for `item` (e.g.
    "bernina.cam_west.intensity"), via its eco.aliases.Alias if it has
    one; `fallback` (or "") if not. Used to identify a gadget's live item
    across save/reload -- both the plain JSON workspace and the Qt
    Designer round-trip (see item_path_from_string_ns)."""
    alias = getattr(item, "alias", None)
    get_full_name = getattr(alias, "get_full_name", None)
    if callable(get_full_name):
        try:
            path = get_full_name()
            if path:
                return path
        except Exception:
            pass
    return fallback or ""


def resolve_item_path(namespace, path):
    """Inverse of item_path: resolve a dotted path against a namespace
    object via plain attribute access. Returns None if any segment is
    missing rather than raising, so a stale/renamed reference in a saved
    workspace degrades to "just don't reattach this one" instead of
    blowing up the whole reload."""
    if not path:
        return None
    obj = namespace
    # a full alias path is typically "<namespace name>.<rest>" -- if the
    # first segment matches the namespace's own name, skip it, since
    # `namespace` here already *is* that root object
    segments = path.split(".")
    root_name = getattr(getattr(namespace, "alias", None), "get_full_name", lambda: None)()
    if segments and root_name and segments[0] == root_name.split(".")[0]:
        segments = segments[1:]
    for seg in segments:
        try:
            obj = getattr(obj, seg)
        except Exception:
            return None
    return obj


def _sanitize_object_name(path):
    return "eco_" + re.sub(r"[^0-9a-zA-Z_]", "_", path or "unnamed")


class _IndicatorPollBridge(QtCore.QObject):
    updated = QtCore.Signal(object)
    error = QtCore.Signal(str)


class _IndicatorBase(QtWidgets.QWidget):
    """Shared chrome + polling for every gadget below. Subclasses
    implement _build_body() (returns the widget under the title, built
    once, item-independent) and _render(value) (called on the GUI thread
    with each new reading); optionally _on_item_attached() if they need
    to react to set_item() (e.g. Dial/Slider re-checking settability)."""

    #: (kind label from INDICATOR_TYPES, set by create_indicator/attach_indicator_menu
    #: so a gadget can describe itself for export_startup_script)
    indicator_kind = None

    #: a gadget is meant to stay small and legible at a glance, not
    #: stretch to fill whatever dock space eco.widgets.dashboard_qt.Dashboard
    #: happens to give it -- (width, height) cap, applied in __init__;
    #: subclasses whose content genuinely needs more room in one dimension
    #: (a wide strip chart, a roughly-square dial) override this.
    MAX_SIZE = (200, 130)

    def __init__(self, parent=None, item=None, title=None, poll_interval=0.5):
        super().__init__(parent)
        self.item = None
        self.poll_interval = poll_interval
        self.export_kwargs = {}  # extra constructor kwargs, for script export
        self._stop_event = threading.Event()
        self._thread = None
        self._bridge = _IndicatorPollBridge()
        self._bridge.updated.connect(self._on_value)
        self._bridge.error.connect(self._on_error)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._title_label = QtWidgets.QLabel("")
        self._title_label.setAlignment(QtCore.Qt.AlignCenter)
        self._title_label.setWordWrap(True)
        font = self._title_label.font()
        font.setBold(True)
        font.setPointSize(max(font.pointSize() - 1, 7))
        self._title_label.setFont(font)
        layout.addWidget(self._title_label)

        layout.addWidget(self._build_body(), 1)

        max_w, max_h = self.MAX_SIZE
        self.setMaximumSize(max_w, max_h)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)

        if item is not None:
            self.set_item(item, title=title)
        else:
            self.setWindowTitle(title or "(unattached)")
            self._title_label.setText(self.windowTitle())

    def _build_body(self):
        raise NotImplementedError

    def _render(self, value):
        raise NotImplementedError

    def _on_item_attached(self):
        """Optional hook for subclasses that need to react when set_item
        gives them a (possibly different-settability) item."""

    def set_item(self, item, title=None):
        """(Re)attach a live item and (re)start polling. Used both at
        normal construction time (item passed to __init__) and to bring
        a Designer-loaded placeholder (item=None until now) to life --
        see Dashboard.load_custom_panel. Safe to call more than once;
        stops any previous polling first (each poll thread captures its
        own item/stop-event at start, so an in-flight old poll can never
        clobber a newly-attached item)."""
        self._stop_event.set()  # tell any in-flight poll thread to stop
        self._stop_event = threading.Event()
        self.item = item
        if title is not None or not self.windowTitle() or self.windowTitle() == "(unattached)":
            self.setWindowTitle(title or getattr(item, "name", None) or repr(item))
            self._title_label.setText(self.windowTitle())
        self._on_item_attached()
        if item is not None:
            self._thread = threading.Thread(
                target=self._poll_loop, args=(item, self._stop_event), daemon=True
            )
            self._thread.start()

    def _poll_loop(self, item, stop_event):
        while not stop_event.is_set():
            try:
                value = item.get_current_value()
            except Exception as exc:
                self._bridge.error.emit(str(exc))
            else:
                self._bridge.updated.emit(value)
            stop_event.wait(self.poll_interval)

    def _on_value(self, value):
        try:
            self._render(value)
        except Exception:
            logger.exception("rendering %r failed", self.windowTitle())

    def _on_error(self, message):
        self.setToolTip(f"error reading value: {message}")

    def stop(self):
        self._stop_event.set()

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)


class LEDIndicator(_IndicatorBase):
    """A small colored light: green = "on", red = "off", gray = unknown.
    "On" means value == on_value (default True), or, if `threshold` is
    given, value >= threshold -- handy for e.g. "beam intensity above
    some floor". Click to toggle if the item is settable (writes
    on_value/off_value via set_target_value)."""

    MAX_SIZE = (140, 100)

    def __init__(
        self, parent=None, item=None, title=None, on_value=True, off_value=False,
        threshold=None, poll_interval=0.3,
    ):
        self.on_value = on_value
        self.off_value = off_value
        self.threshold = threshold
        self._is_on = None
        super().__init__(parent, item=item, title=title, poll_interval=poll_interval)

    def _build_body(self):
        self._led = QtWidgets.QLabel()
        self._led.setFixedSize(30, 30)
        self._led.setCursor(QtCore.Qt.PointingHandCursor)
        self._led.mousePressEvent = self._on_click
        self._paint_led(None)

        wrap = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(wrap)
        lay.addStretch(1)
        lay.addWidget(self._led)
        lay.addStretch(1)
        return wrap

    def _render(self, value):
        if self.threshold is not None:
            try:
                is_on = float(value) >= self.threshold
            except (TypeError, ValueError):
                is_on = None
        else:
            is_on = value == self.on_value
        self._is_on = is_on
        self._paint_led(is_on)

    def _paint_led(self, is_on):
        color = "#9aa0a6" if is_on is None else ("#2ecc71" if is_on else "#e74c3c")
        self._led.setStyleSheet(
            f"background-color: {color}; border-radius: 15px; border: 2px solid #2c2c2c;"
        )

    def _on_click(self, event):
        if self.item is None or not hasattr(self.item, "set_target_value"):
            return
        new_value = self.off_value if self._is_on else self.on_value
        threading.Thread(target=lambda: self.item.set_target_value(new_value), daemon=True).start()


class BarGauge(_IndicatorBase):
    """A vertical (default) or horizontal fill bar against a fixed
    [vmin, vmax] range -- read-only (use Slider for a settable bar-like
    control)."""

    def __init__(
        self, parent=None, item=None, title=None, vmin=0.0, vmax=100.0,
        orientation="vertical", poll_interval=0.5,
    ):
        self.vmin = vmin
        self.vmax = vmax
        self.orientation = orientation
        # orientation-dependent, so set as an instance override before
        # _IndicatorBase.__init__ applies MAX_SIZE
        self.MAX_SIZE = (110, 230) if orientation == "vertical" else (230, 90)
        super().__init__(parent, item=item, title=title, poll_interval=poll_interval)

    def _build_body(self):
        self._bar = QtWidgets.QProgressBar()
        self._bar.setOrientation(
            QtCore.Qt.Vertical if self.orientation == "vertical" else QtCore.Qt.Horizontal
        )
        self._bar.setRange(0, 1000)
        self._bar.setTextVisible(True)
        if self.orientation == "vertical":
            self._bar.setMinimumSize(28, 100)
        else:
            self._bar.setMinimumSize(120, 24)
        return self._bar

    def _render(self, value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            self._bar.setFormat(_format_value(value))
            return
        span = self.vmax - self.vmin
        frac = 0.0 if span == 0 else (v - self.vmin) / span
        frac = max(0.0, min(1.0, frac))
        self._bar.setValue(int(round(frac * 1000)))
        self._bar.setFormat(f"{v:.3g}")


class AnalogGauge(_IndicatorBase):
    """A semicircular analog-style gauge with a sweeping needle -- a
    small "instrument panel" look. Read-only/decorative (see Dial for an
    interactive rotary control)."""

    MAX_SIZE = (190, 130)

    def __init__(self, parent=None, item=None, title=None, vmin=0.0, vmax=100.0, poll_interval=0.5):
        self.vmin = vmin
        self.vmax = vmax
        self._value = None
        super().__init__(parent, item=item, title=title, poll_interval=poll_interval)

    def _build_body(self):
        canvas = QtWidgets.QWidget()
        canvas.setMinimumSize(110, 70)
        canvas.paintEvent = self._paint
        self._canvas = canvas
        return canvas

    def _render(self, value):
        try:
            self._value = float(value)
        except (TypeError, ValueError):
            self._value = None
        self._canvas.update()

    def _paint(self, event):
        painter = QtGui.QPainter(self._canvas)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self._canvas.width(), self._canvas.height()
        cx, cy = w / 2.0, h - 10.0
        r = max(10.0, min(w / 2.0, h) - 12.0)

        pen = QtGui.QPen(QtGui.QColor(120, 124, 130), 5)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(pen)
        rect = QtCore.QRectF(cx - r, cy - r, 2 * r, 2 * r)
        painter.drawArc(rect, 0, 180 * 16)

        if self._value is not None:
            span = self.vmax - self.vmin
            frac = 0.0 if span == 0 else (self._value - self.vmin) / span
            frac = max(0.0, min(1.0, frac))
            angle_rad = math.radians(180 - frac * 180)
            nx = cx + r * 0.85 * math.cos(angle_rad)
            ny = cy - r * 0.85 * math.sin(angle_rad)

            needle_pen = QtGui.QPen(QtGui.QColor(230, 70, 70), 3)
            needle_pen.setCapStyle(QtCore.Qt.RoundCap)
            painter.setPen(needle_pen)
            painter.drawLine(QtCore.QPointF(cx, cy), QtCore.QPointF(nx, ny))
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(230, 70, 70))
            painter.drawEllipse(QtCore.QPointF(cx, cy), 4, 4)

            painter.setPen(QtGui.QColor(210, 210, 214))
            painter.drawText(
                QtCore.QRectF(0, max(0, cy - r - 20), w, 16),
                QtCore.Qt.AlignCenter,
                f"{self._value:.3g}",
            )


class StripChart(_IndicatorBase):
    """A tiny rolling line plot of the last `window_points` readings --
    for a quick "is this trending" glance, not a real strip-chart
    replacement (see eco.dbase.archiver.DataHub.strip_plot for that)."""

    MAX_SIZE = (260, 110)

    def __init__(self, parent=None, item=None, title=None, window_points=100, poll_interval=0.3):
        self.window_points = max(2, window_points)
        self._values = deque(maxlen=self.window_points)
        super().__init__(parent, item=item, title=title, poll_interval=poll_interval)

    def _build_body(self):
        canvas = QtWidgets.QWidget()
        canvas.setMinimumSize(130, 55)
        canvas.paintEvent = self._paint
        self._canvas = canvas
        return canvas

    def _render(self, value):
        try:
            self._values.append(float(value))
        except (TypeError, ValueError):
            return
        self._canvas.update()

    def _paint(self, event):
        painter = QtGui.QPainter(self._canvas)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self._canvas.width(), self._canvas.height()
        painter.fillRect(0, 0, w, h, QtGui.QColor(28, 30, 34))
        if len(self._values) < 2:
            return
        lo, hi = min(self._values), max(self._values)
        if hi <= lo:
            hi = lo + 1.0
        pts = [
            QtCore.QPointF(
                i / (self.window_points - 1) * w, h - (v - lo) / (hi - lo) * (h - 6) - 3
            )
            for i, v in enumerate(self._values)
        ]
        pen = QtGui.QPen(QtGui.QColor(0, 191, 165), 1.6)
        painter.setPen(pen)
        painter.drawPolyline(QtGui.QPolygonF(pts))
        painter.setPen(QtGui.QColor(190, 190, 194))
        painter.drawText(4, h - 4, f"{self._values[-1]:.3g}")


class NumericTile(_IndicatorBase):
    """A compact "KPI tile": a large numeric readout with a small
    sparkline underneath -- an information-dense, minimal-footprint
    combination of a precise number and a trend, common in modern
    dashboards but not on the original wishlist -- pairs well next to
    the more purely visual gauges."""

    MAX_SIZE = (180, 110)

    def __init__(self, parent=None, item=None, title=None, window_points=40, fmt="{:.4g}", poll_interval=0.5):
        self.window_points = max(2, window_points)
        self.fmt = fmt
        self._values = deque(maxlen=self.window_points)
        super().__init__(parent, item=item, title=title, poll_interval=poll_interval)

    def _build_body(self):
        wrap = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)

        self._number_label = QtWidgets.QLabel("--")
        self._number_label.setAlignment(QtCore.Qt.AlignCenter)
        font = self._number_label.font()
        font.setPointSize(font.pointSize() + 8)
        font.setBold(True)
        self._number_label.setFont(font)

        spark = QtWidgets.QWidget()
        spark.setFixedHeight(26)
        spark.paintEvent = self._paint_spark
        self._spark = spark

        lay.addWidget(self._number_label)
        lay.addWidget(spark)
        return wrap

    def _render(self, value):
        try:
            self._number_label.setText(self.fmt.format(value))
        except Exception:
            self._number_label.setText(_format_value(value))
        try:
            self._values.append(float(value))
            self._spark.update()
        except (TypeError, ValueError):
            pass

    def _paint_spark(self, event):
        painter = QtGui.QPainter(self._spark)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self._spark.width(), self._spark.height()
        if len(self._values) < 2:
            return
        lo, hi = min(self._values), max(self._values)
        if hi <= lo:
            hi = lo + 1.0
        pts = [
            QtCore.QPointF(
                i / (self.window_points - 1) * w, h - (v - lo) / (hi - lo) * (h - 2) - 1
            )
            for i, v in enumerate(self._values)
        ]
        pen = QtGui.QPen(QtGui.QColor(0, 191, 165), 1.5)
        painter.setPen(pen)
        painter.drawPolyline(QtGui.QPolygonF(pts))


def _enum_index(value, enum_strs):
    """Best-effort index of `value` within `enum_strs` -- a polled value
    may come back as an int index, an enum.Enum member (compared by
    .name), or a plain string already matching one of enum_strs. None if
    it doesn't resolve to any of them."""
    import enum as _enum_mod

    if isinstance(value, _enum_mod.Enum):
        value = value.name
    if isinstance(value, str):
        try:
            return enum_strs.index(value)
        except ValueError:
            return None
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return None
    return idx if 0 <= idx < len(enum_strs) else None


class _EnumDialFace(QtWidgets.QWidget):
    """Dial's "show every choice at once" rotary face for enum-valued
    items: a ring with one tick + label per choice and a pointer to the
    current selection -- the mode-select-knob look of real lab equipment
    (a trigger-source or channel selector switch), where every position
    is always visible rather than hidden behind a dropdown until opened.
    Click a label's wedge to select it, if interactive.

    Sweeps 270 degrees starting at 225 degrees going clockwise (standard
    math convention: 0 degrees = 3 o'clock, counterclockwise positive) --
    the same start/span QDial itself defaults to, so it points the same
    way anyone used to a QDial would expect."""

    position_chosen = QtCore.Signal(int)

    _START_ANGLE = 225.0
    _SPAN_ANGLE = 270.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(130, 130)
        self._choices = []
        self._current = None
        self._interactive = False

    def set_choices(self, choices):
        self._choices = list(choices)
        self._current = None
        self.update()

    def set_current(self, index):
        self._current = index
        self.update()

    def set_interactive(self, interactive):
        self._interactive = interactive
        self.setCursor(QtCore.Qt.PointingHandCursor if interactive else QtCore.Qt.ArrowCursor)

    def _angle_for(self, index):
        n = len(self._choices)
        if n <= 1:
            return self._START_ANGLE - self._SPAN_ANGLE / 2
        return self._START_ANGLE - index * (self._SPAN_ANGLE / (n - 1))

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r = max(14.0, min(w, h) / 2.0 - 34.0)  # leave room around the ring for labels

        painter.setPen(QtGui.QPen(QtGui.QColor(120, 124, 130), 2))
        painter.setBrush(QtGui.QColor(45, 50, 56))
        painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)

        for i, label in enumerate(self._choices):
            angle = math.radians(self._angle_for(i))
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            is_current = i == self._current
            color = QtGui.QColor(0, 191, 165) if is_current else QtGui.QColor(150, 154, 160)

            tick_pen = QtGui.QPen(color, 3)
            painter.setPen(tick_pen)
            painter.drawLine(
                QtCore.QPointF(cx + (r - 6) * cos_a, cy - (r - 6) * sin_a),
                QtCore.QPointF(cx + r * cos_a, cy - r * sin_a),
            )

            lx, ly = cx + (r + 8) * cos_a, cy - (r + 8) * sin_a
            if cos_a > 0.3:
                align, text_rect = QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, QtCore.QRectF(lx, ly - 8, 60, 16)
            elif cos_a < -0.3:
                align, text_rect = QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, QtCore.QRectF(lx - 60, ly - 8, 60, 16)
            else:
                align, text_rect = QtCore.Qt.AlignCenter, QtCore.QRectF(lx - 40, ly - 8, 80, 16)
            font = painter.font()
            font.setBold(is_current)
            painter.setFont(font)
            painter.setPen(QtGui.QColor(0, 191, 165) if is_current else QtGui.QColor(190, 194, 198))
            painter.drawText(text_rect, align, label)

        if self._current is not None and 0 <= self._current < len(self._choices):
            angle = math.radians(self._angle_for(self._current))
            px = cx + (r - 10) * math.cos(angle)
            py = cy - (r - 10) * math.sin(angle)
            pointer_pen = QtGui.QPen(QtGui.QColor(0, 191, 165), 3)
            pointer_pen.setCapStyle(QtCore.Qt.RoundCap)
            painter.setPen(pointer_pen)
            painter.drawLine(QtCore.QPointF(cx, cy), QtCore.QPointF(px, py))
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(0, 191, 165))
            painter.drawEllipse(QtCore.QPointF(cx, cy), 4, 4)

    def mouseReleaseEvent(self, event):
        if not self._interactive or not self._choices:
            return
        cx, cy = self.width() / 2.0, self.height() / 2.0
        pos = event.pos()
        clicked_angle = math.degrees(math.atan2(cy - pos.y(), pos.x() - cx))
        angles = [self._angle_for(i) for i in range(len(self._choices))]
        best_i = _nearest_enum_position(clicked_angle, angles)
        if best_i is not None:
            self.position_chosen.emit(best_i)


def _nearest_enum_position(clicked_angle, angles):
    """Index into `angles` (degrees, e.g. from _EnumDialFace._angle_for)
    closest to `clicked_angle` (degrees), by angular distance around the
    circle -- split out from mouseReleaseEvent so this geometry is
    unit-testable against plain numbers, without needing a real
    constructed widget or a synthesized QMouseEvent."""
    best_i, best_dist = None, None
    for i, a in enumerate(angles):
        d = abs(((clicked_angle - a + 180) % 360) - 180)
        if best_dist is None or d < best_dist:
            best_i, best_dist = i, d
    return best_i


class Dial(_IndicatorBase):
    """A rotary knob: continuous over [vmin, vmax] for a plain numeric
    item, or -- when the attached item exposes `enum_strs` (the
    AdjustableEnum/DetectorEnum protocol convention eco already uses
    elsewhere, e.g. AdjustablePvEnum) -- a discrete mode-select switch
    (see _EnumDialFace) with every choice's name shown around the dial
    face at once, rather than hidden behind a pulldown. Interactive
    (writes via set_target_value) if the item is settable; otherwise
    read-only/disabled, just following the polled value -- the same "Add
    indicator" menu entry works either way, for either flavour."""

    MAX_SIZE = (210, 220)

    def __init__(self, parent=None, item=None, title=None, vmin=0.0, vmax=100.0, step=None, poll_interval=0.5):
        self.vmin = vmin
        self.vmax = vmax
        self.step = step or max((vmax - vmin) / 200.0, 1e-9)
        self.settable = False
        self.enum_strs = None  # set in _on_item_attached when the item is enum-like
        super().__init__(parent, item=item, title=title, poll_interval=poll_interval)

    def _build_body(self):
        wrap = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(wrap)
        lay.setSpacing(2)

        self._stack = QtWidgets.QStackedWidget()

        self._numeric_page = QtWidgets.QWidget()
        numeric_lay = QtWidgets.QVBoxLayout(self._numeric_page)
        numeric_lay.setContentsMargins(0, 0, 0, 0)
        self._dial = QtWidgets.QDial()
        self._dial.setNotchesVisible(True)
        n_steps = max(1, int(round((self.vmax - self.vmin) / self.step)))
        self._dial.setRange(0, n_steps)
        self._dial.setEnabled(False)
        numeric_lay.addWidget(self._dial)
        self._stack.addWidget(self._numeric_page)

        self._enum_face = _EnumDialFace()
        self._enum_face.position_chosen.connect(self._on_enum_chosen)
        self._stack.addWidget(self._enum_face)

        lay.addWidget(self._stack, 1)
        self._value_label = QtWidgets.QLabel("--")
        self._value_label.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self._value_label)
        return wrap

    def _on_item_attached(self):
        self.settable = self.item is not None and hasattr(self.item, "set_target_value")
        enum_strs = getattr(self.item, "enum_strs", None) if self.item is not None else None
        self.enum_strs = list(enum_strs) if enum_strs else None

        try:
            self._dial.sliderReleased.disconnect(self._on_release)
        except (TypeError, RuntimeError):
            pass

        if self.enum_strs:
            self._stack.setCurrentWidget(self._enum_face)
            self._enum_face.set_choices(self.enum_strs)
            self._enum_face.set_interactive(self.settable)
        else:
            self._stack.setCurrentWidget(self._numeric_page)
            self._dial.setEnabled(self.settable)
            if self.settable:
                self._dial.sliderReleased.connect(self._on_release)

    def _pos_to_value(self, pos):
        return self.vmin + pos * self.step

    def _value_to_pos(self, value):
        return int(round((value - self.vmin) / self.step))

    def _render(self, value):
        if self.enum_strs:
            index = _enum_index(value, self.enum_strs)
            self._value_label.setText(self.enum_strs[index] if index is not None else _format_value(value))
            self._enum_face.set_current(index)
            return
        self._value_label.setText(_format_value(value))
        if self._dial.isSliderDown():
            return
        try:
            pos = self._value_to_pos(float(value))
        except (TypeError, ValueError):
            return
        self._dial.blockSignals(True)
        self._dial.setValue(max(self._dial.minimum(), min(self._dial.maximum(), pos)))
        self._dial.blockSignals(False)

    def _on_release(self):
        new_value = self._pos_to_value(self._dial.value())
        threading.Thread(target=lambda: self.item.set_target_value(new_value), daemon=True).start()

    def _on_enum_chosen(self, index):
        if not self.settable or self.enum_strs is None:
            return
        new_value = self.enum_strs[index]
        threading.Thread(target=lambda: self.item.set_target_value(new_value), daemon=True).start()


class Slider(_IndicatorBase):
    """A horizontal slider over [vmin, vmax] with a numeric readout.
    Interactive (writes via set_target_value on release) if the item is
    settable; otherwise read-only/disabled."""

    MAX_SIZE = (280, 80)

    def __init__(self, parent=None, item=None, title=None, vmin=0.0, vmax=100.0, step=None, poll_interval=0.5):
        self.vmin = vmin
        self.vmax = vmax
        self.step = step or max((vmax - vmin) / 200.0, 1e-9)
        self.settable = False
        super().__init__(parent, item=item, title=title, poll_interval=poll_interval)

    def _build_body(self):
        wrap = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(wrap)
        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        n_steps = max(1, int(round((self.vmax - self.vmin) / self.step)))
        self._slider.setRange(0, n_steps)
        self._slider.setEnabled(False)
        self._value_label = QtWidgets.QLabel("--")
        self._value_label.setFixedWidth(56)
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._value_label)
        return wrap

    def _on_item_attached(self):
        self.settable = self.item is not None and hasattr(self.item, "set_target_value")
        self._slider.setEnabled(self.settable)
        try:
            self._slider.sliderReleased.disconnect(self._on_release)
        except (TypeError, RuntimeError):
            pass
        if self.settable:
            self._slider.sliderReleased.connect(self._on_release)

    def _pos_to_value(self, pos):
        return self.vmin + pos * self.step

    def _value_to_pos(self, value):
        return int(round((value - self.vmin) / self.step))

    def _render(self, value):
        self._value_label.setText(_format_value(value))
        if self._slider.isSliderDown():
            return
        try:
            pos = self._value_to_pos(float(value))
        except (TypeError, ValueError):
            return
        self._slider.blockSignals(True)
        self._slider.setValue(max(self._slider.minimum(), min(self._slider.maximum(), pos)))
        self._slider.blockSignals(False)

    def _on_release(self):
        new_value = self._pos_to_value(self._slider.value())
        threading.Thread(target=lambda: self.item.set_target_value(new_value), daemon=True).start()


# name shown in the "Add indicator" submenu -> (factory, needs_range)
INDICATOR_TYPES = (
    ("LED", LEDIndicator, False),
    ("Bar gauge", BarGauge, True),
    ("Analog gauge", AnalogGauge, True),
    ("Strip chart", StripChart, False),
    ("Numeric tile (KPI)", NumericTile, False),
    ("Dial / knob", Dial, True),
    ("Slider", Slider, True),
)

_FACTORY_BY_KIND = {label: factory for label, factory, _needs_range in INDICATOR_TYPES}


def create_indicator(kind, item, title=None, vmin=None, vmax=None, **kwargs):
    """Build one indicator gadget by name (one of INDICATOR_TYPES' first
    elements, e.g. "LED"/"Bar gauge"/...). vmin/vmax are guessed (see
    _guess_range) if not given and the chosen kind needs a range.

    The returned widget is fully tagged for round-tripping: .indicator_kind/
    .export_kwargs (for Dashboard.export_startup_script) and
    accessibleName/objectName (item_path(item), for Dashboard's JSON
    save/reload and the Qt Designer round-trip) -- set here, not only in
    the "Add indicator" menu path (_create_and_add), so a dashboard built
    entirely in code (no menu involved) still round-trips correctly."""
    for label, factory, needs_range in INDICATOR_TYPES:
        if label == kind:
            export_kwargs = dict(kwargs)
            if needs_range:
                lo, hi = (vmin, vmax) if vmin is not None and vmax is not None else _guess_range(item)
                export_kwargs.update(vmin=lo, vmax=hi)
                widget = factory(item=item, title=title, vmin=lo, vmax=hi, **kwargs)
            else:
                widget = factory(item=item, title=title, **kwargs)
            widget.indicator_kind = kind
            widget.export_kwargs = export_kwargs
            path = item_path(item, fallback=title)
            widget.setAccessibleName(path)
            widget.setObjectName(_sanitize_object_name(path))
            return widget
    raise ValueError(f"unknown indicator kind {kind!r}; choose one of {[t[0] for t in INDICATOR_TYPES]}")


def attach_indicator_menu(label_widget, item, name, dashboard=None):
    """Right-click `label_widget` (a parameter's name label in an
    assembly's Qt widget) for an "Add indicator" submenu that creates a
    gadget for `item` and drops it onto `dashboard` (default: the shared
    default dashboard -- see eco.widgets.dashboard_qt.get_default_dashboard,
    created on first use). Deliberately a context menu, not a visible
    button -- see the module docstring."""
    label_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

    def _show_menu(pos):
        menu = QtWidgets.QMenu(label_widget)
        submenu = menu.addMenu("Add indicator")
        for label, factory, needs_range in INDICATOR_TYPES:
            action = submenu.addAction(label)
            action.triggered.connect(
                lambda checked=False, k=label: _create_and_add(item, name, k, dashboard)
            )
        menu.exec_(label_widget.mapToGlobal(pos))

    label_widget.customContextMenuRequested.connect(_show_menu)


def _create_and_add(item, name, kind, dashboard):
    try:
        widget = create_indicator(kind, item, title=name)  # already tags accessibleName/objectName
    except Exception:
        logger.exception("failed to create a %r indicator for %r", kind, name)
        return
    if dashboard is None:
        from eco.widgets.dashboard_qt import get_default_dashboard

        dashboard = get_default_dashboard()
    dashboard.add_widget(widget, title=name)
