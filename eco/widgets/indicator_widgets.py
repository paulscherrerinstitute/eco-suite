"""
Small, live-updating "gadget" widgets for one Adjustable/Detector each --
an LED, a bar/analog gauge, a tiny strip chart, a numeric KPI tile, a
dial/knob, a slider -- meant to be pulled out of an assembly's normal
property-grid view (eco.widgets.display_qt) and dropped onto a
eco.widgets.dashboard_qt.Dashboard to build a custom live-value board,
rather than living permanently in the property grid itself.

Discovery: the "Add indicator" submenu of each property-grid row's "⋯"
item menu (also on right-click of the name; eco.widgets.item_menu_qt).
attach_indicator_menu below is the older stand-alone right-click hook.

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
_guess_range) when created via the menu -- skipping "unset" limits such
as a motor's +-1e10 -- then the current value +-10 %, then 0..100: a
reasonable default, not a promise. Right-click such a gadget to change
it live: "Set range..." (Min/Max, scientific notation accepted), "Range
around current value" (+-10 %) or "Reset to item limits". The new range
is kept in export_kwargs, so it survives Save Workspace, autosave and
Save Startup Script. Pass explicit vmin/vmax when creating one directly
in code. (Enum-mode Dials have no range and no such menu.)

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


#: limits at or beyond this magnitude mean "not set" (a motor record's
#: soft limits are often +-1e10), not a range anyone wants a gauge over
UNSET_LIMIT = 1e9

_UNREAD = object()


def _usable_range(lo, hi):
    """(float lo, float hi) if they make a sensible display range, else None."""
    try:
        lo, hi = float(lo), float(hi)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return None
    if abs(lo) >= UNSET_LIMIT or abs(hi) >= UNSET_LIMIT:
        return None
    return lo, hi


def range_around(value, fraction=0.1):
    """value +- fraction*|value| as (lo, hi); None if value isn't a
    nonzero number (a zero-width range is no range)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    half = abs(v) * fraction
    if not math.isfinite(v) or half <= 0:
        return None
    return v - half, v + half


def _guess_range(item, current=_UNREAD):
    """Best-effort (vmin, vmax): the first usable pair of a handful of
    common limit-attribute names (skipping inverted, equal or "unset"
    ones, see UNSET_LIMIT), else the current value +-10 % (`current`, or
    read from the item), else (0.0, 100.0). A heuristic, not a guarantee
    -- pass explicit vmin/vmax to any gadget's constructor, or use its
    right-click "Set range...", to override."""
    for lo_attr, hi_attr in (("low_limit", "high_limit"), ("llm", "hlm"), ("lolim", "hilim")):
        try:
            usable = _usable_range(getattr(item, lo_attr, None), getattr(item, hi_attr, None))
        except Exception:
            usable = None
        if usable:
            return usable
    try:
        limits = getattr(item, "limits", None)
        if limits and len(limits) == 2:
            usable = _usable_range(limits[0], limits[1])
            if usable:
                return usable
    except Exception:
        pass
    if current is _UNREAD:
        try:
            current = item.get_current_value()
        except Exception:
            current = None
    around = range_around(current)
    if around:
        return around
    return 0.0, 100.0


class RangeDialog(QtWidgets.QDialog):
    """Min / Max for a gadget's display range. Plain line edits with a
    scientific-notation validator (a QDoubleSpinBox's fixed decimals can't
    hold 1e-6 or 3.2e4 alike); OK only while both parse and Max > Min."""

    def __init__(self, vmin, vmax, parent=None, title="Set range"):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QtWidgets.QFormLayout(self)
        validator = QtGui.QDoubleValidator(self)
        validator.setNotation(QtGui.QDoubleValidator.ScientificNotation)
        # "." always, whatever the desktop's locale says
        validator.setLocale(QtCore.QLocale.c())
        self.min_edit = QtWidgets.QLineEdit(f"{vmin:.6g}")
        self.max_edit = QtWidgets.QLineEdit(f"{vmax:.6g}")
        for edit in (self.min_edit, self.max_edit):
            edit.setValidator(validator)
            edit.textChanged.connect(self._check)
        form.addRow("Min", self.min_edit)
        form.addRow("Max", self.max_edit)
        self.message = QtWidgets.QLabel("")
        form.addRow(self.message)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        self._check()

    def values(self):
        """(vmin, vmax) as floats, or None while the input isn't a valid range."""
        try:
            lo, hi = float(self.min_edit.text()), float(self.max_edit.text())
        except ValueError:
            return None
        if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
            return None
        return lo, hi

    def _check(self, *_):
        ok = self.values() is not None
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(ok)
        self.message.setText("" if ok else "Max must be a number greater than Min")


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

    #: gadgets drawn against a [vmin, vmax] range: these get the
    #: right-click "Set range..." menu and set_range()
    SUPPORTS_RANGE = False

    def __init__(self, parent=None, item=None, title=None, poll_interval=0.5):
        super().__init__(parent)
        self.item = None
        self.poll_interval = poll_interval
        self.export_kwargs = {}  # extra constructor kwargs, for script export
        self._last_value = None  # the latest polled reading, for re-rendering
        if self.SUPPORTS_RANGE:
            self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.customContextMenuRequested.connect(self._show_range_menu)
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
        self._last_value = value
        try:
            self._render(value)
        except Exception:
            logger.exception("rendering %r failed", self.windowTitle())

    # -- display range (SUPPORTS_RANGE gadgets) ------------------------------

    def has_range(self):
        """Whether this gadget currently shows a numeric range -- False for
        the range-less kinds and for an enum-mode Dial."""
        return self.SUPPORTS_RANGE and not getattr(self, "enum_strs", None)

    def set_range(self, vmin, vmax):
        """Show [vmin, vmax] from now on, live: no rebuild, no write to the
        item. Also recorded in export_kwargs, so saved workspaces and
        startup scripts keep it. Raises ValueError for Max <= Min."""
        if not self.SUPPORTS_RANGE:
            raise TypeError(f"{type(self).__name__} has no range")
        vmin, vmax = float(vmin), float(vmax)
        if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmax <= vmin:
            raise ValueError(f"invalid range {vmin!r}..{vmax!r}: max must be greater than min")
        self.vmin, self.vmax = vmin, vmax
        self.export_kwargs["vmin"] = vmin
        self.export_kwargs["vmax"] = vmax
        self._apply_range()
        if self._last_value is not None:
            try:
                self._render(self._last_value)
            except Exception:
                logger.exception("rendering %r failed", self.windowTitle())
        self.update()

    def _apply_range(self):
        """Subclass hook: adapt to a new self.vmin/self.vmax (before the
        last value is re-rendered)."""

    def range_around_current(self, fraction=0.1):
        """Range the gadget to the last polled value +-10 %; False if there
        is no usable (nonzero numeric) value yet."""
        around = range_around(self._last_value, fraction)
        if around is None:
            return False
        self.set_range(*around)
        return True

    def reset_range(self):
        """Back to what the item's limits suggest (_guess_range), using the
        last polled value instead of reading the item again."""
        current = self._last_value if self._last_value is not None else None
        self.set_range(*_guess_range(self.item, current=current))

    def _ask_range(self):
        dialog = RangeDialog(self.vmin, self.vmax, parent=self, title=f"Range of {self.windowTitle()}")
        if dialog.exec_() == QtWidgets.QDialog.Accepted and dialog.values():
            self.set_range(*dialog.values())

    def range_menu(self):
        """The right-click menu (built fresh each time); None when this
        gadget has no range to set."""
        if not self.has_range():
            return None
        menu = QtWidgets.QMenu(self)
        menu.addAction("Set range...").triggered.connect(lambda: self._guarded(self._ask_range))
        around = menu.addAction("Range around current value")
        around.setToolTip("current value +-10 %")
        around.setEnabled(range_around(self._last_value) is not None)
        around.triggered.connect(lambda: self._guarded(self.range_around_current))
        reset = menu.addAction("Reset to item limits")
        reset.setEnabled(self.item is not None)
        reset.triggered.connect(lambda: self._guarded(self.reset_range))
        return menu

    def _show_range_menu(self, pos):
        # a Qt slot: nothing may escape (an exception here aborts PyQt)
        try:
            menu = self.range_menu()
            if menu is not None:
                menu.exec_(self.mapToGlobal(pos))
        except Exception:
            logger.exception("range menu of %r failed", self.windowTitle())

    def _guarded(self, func):
        try:
            func()
        except RuntimeError:  # gadget deleted meanwhile
            pass
        except Exception:
            logger.exception("changing the range of %r failed", self.windowTitle())

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

    SUPPORTS_RANGE = True

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
    SUPPORTS_RANGE = True

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

    def _apply_range(self):
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
    SUPPORTS_RANGE = True

    def __init__(self, parent=None, item=None, title=None, vmin=0.0, vmax=100.0, step=None, poll_interval=0.5):
        self.vmin = vmin
        self.vmax = vmax
        # a step derived from the range is re-derived when the range changes
        self._auto_step = not step
        self.step = step or self._step_for_range()
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
        self._dial.setRange(0, self._n_steps())
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

    def _step_for_range(self):
        return max((self.vmax - self.vmin) / 200.0, 1e-9)

    def _n_steps(self):
        return max(1, int(round((self.vmax - self.vmin) / self.step)))

    def _apply_range(self):
        if self._auto_step:
            self.step = self._step_for_range()
        # the user may be holding the knob: leave the position alone then,
        # and never let the re-range itself look like a move (no write)
        self._dial.blockSignals(True)
        try:
            self._dial.setRange(0, self._n_steps())
        finally:
            self._dial.blockSignals(False)

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
    SUPPORTS_RANGE = True

    def __init__(self, parent=None, item=None, title=None, vmin=0.0, vmax=100.0, step=None, poll_interval=0.5):
        self.vmin = vmin
        self.vmax = vmax
        self._auto_step = not step
        self.step = step or self._step_for_range()
        self.settable = False
        super().__init__(parent, item=item, title=title, poll_interval=poll_interval)

    def _build_body(self):
        wrap = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(wrap)
        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._slider.setRange(0, self._n_steps())
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

    def _step_for_range(self):
        return max((self.vmax - self.vmin) / 200.0, 1e-9)

    def _n_steps(self):
        return max(1, int(round((self.vmax - self.vmin) / self.step)))

    def _apply_range(self):
        if self._auto_step:
            self.step = self._step_for_range()
        # the user may be holding the knob: leave the position alone then,
        # and never let the re-range itself look like a move (no write)
        self._slider.blockSignals(True)
        try:
            self._slider.setRange(0, self._n_steps())
        finally:
            self._slider.blockSignals(False)

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


def _create_and_add(item, name, kind, dashboard, container=None):
    """Build a `kind` gadget for `item` and show it: docked in `container`
    (the eco desktop the grid lives in -- anything with ``add_gadget``,
    which also saves it with the workspace and startup script) if there is
    one and no explicit `dashboard`, else on `dashboard` (default: the
    shared standalone dashboard window)."""
    try:
        widget = create_indicator(kind, item, title=name)  # already tags accessibleName/objectName
    except Exception:
        logger.exception("failed to create a %r indicator for %r", kind, name)
        return
    if dashboard is None and container is not None and hasattr(container, "add_gadget"):
        container.add_gadget(widget, title=name)
        return
    if dashboard is None:
        from eco.widgets.dashboard_qt import get_default_dashboard

        dashboard = get_default_dashboard()
    dashboard.add_widget(widget, title=name)
