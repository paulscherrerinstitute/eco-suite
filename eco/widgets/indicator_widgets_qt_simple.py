"""
Simple, stock-Qt counterparts of eco.widgets.indicator_widgets_ipy's
first-pass ipywidgets gadgets -- built entirely from plain QWidgets
(QProgressBar, QSlider, QPushButton, QLabel, a row of checkable
QPushButtons for the enum "dial") rather than eco.widgets.indicator_widgets'
hand-painted QPainter gauges/dial face. This is the Qt-side half of a
matched, as-simple-as-possible pair with indicator_widgets_ipy -- see
docs/widget_views.md, which documents both side by side. The richer,
hand-drawn eco.widgets.indicator_widgets gadgets (real needle gauge, radial
enum dial, live strip-chart) are unaffected and remain available; this
module is a deliberately lower-effort alternative, not a replacement.

Same function names/signatures/kind strings as
eco.widgets.indicator_widgets_ipy, so the two are interchangeable at a
call site modulo which backend is running -- see
eco.widgets.containers/eco.utilities.utilities.is_notebook for the
existing convention of picking a backend automatically.

Each function builds one live, self-polling QWidget (own background
thread + Qt signal bridge to marshal each reading onto the GUI thread,
own .stop()) for one Adjustable/Detector item -- same
threading-plus-signal-bridge pattern eco.widgets.display_qt and
eco.widgets.indicator_widgets already use (unlike ipywidgets, touching a
QWidget from a non-GUI thread is not safe, so this can't just set
`.value` directly the way indicator_widgets_ipy does). Writes go through
item.set_target_value() on a short-lived thread, same convention
throughout eco's widgets.

Usage:
    from eco.widgets.indicator_widgets_qt_simple import bar_gauge
    gauge = bar_gauge(my_assembly.pressure, vmin=0, vmax=100)
    gauge.show()
"""
import enum
import threading

from qtpy import QtCore, QtWidgets


def _format_value(value):
    if isinstance(value, enum.Enum):
        return value.name
    try:
        return f"{float(value):.4g}"
    except (TypeError, ValueError):
        return str(value)


def _guess_range(item):
    """Best-effort (vmin, vmax) -- same heuristic as
    eco.widgets.indicator_widgets._guess_range/
    eco.widgets.indicator_widgets_ipy._guess_range (duplicated rather than
    imported, same reasoning as the ipywidgets module: keeping each
    backend's module self-contained)."""
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


def _enum_index(value, enum_strs):
    """Best-effort index of `value` within `enum_strs` -- see
    eco.widgets.indicator_widgets_ipy._enum_index, identical logic."""
    if isinstance(value, enum.Enum):
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


def _item_name(item, title):
    if title is not None:
        return title
    alias = getattr(item, "alias", None)
    get_full_name = getattr(alias, "get_full_name", None)
    if callable(get_full_name):
        try:
            name = get_full_name()
            if name:
                return name
        except Exception:
            pass
    return getattr(item, "name", None) or str(item)


class _PollBridge(QtCore.QObject):
    updated = QtCore.Signal(object)


def _start_poll(widget, get_value, render, poll_interval, stop_event):
    """Background thread: read get_value() every poll_interval seconds and
    marshal each reading to render(value) on the GUI thread via a Qt
    signal. The bridge is stashed on `widget` so it survives as long as
    the widget does -- a local-only QObject with a connected signal can
    otherwise be garbage-collected out from under the connection."""
    bridge = _PollBridge()
    bridge.updated.connect(render)
    widget._bridge = bridge

    def _loop():
        while not stop_event.is_set():
            try:
                value = get_value()
            except Exception:
                pass
            else:
                bridge.updated.emit(value)
            if stop_event.wait(poll_interval):
                break

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def led_indicator(item, title=None, on_value=True, off_value=False, threshold=None, poll_interval=0.3):
    """A round on/off indicator: a plain QPushButton painted green/red/gray
    via its stylesheet. "On" means value == on_value, or, if `threshold`
    is given, value >= threshold. Click to toggle if the item is
    settable."""
    name = _item_name(item, title)
    settable = hasattr(item, "set_target_value")
    state = {"is_on": None}

    btn = QtWidgets.QPushButton()
    btn.setFixedSize(28, 28)
    btn.setEnabled(settable)
    btn.setToolTip(str(name))

    def _paint(is_on):
        color = "#9aa0a6" if is_on is None else ("#2ecc71" if is_on else "#e74c3c")
        btn.setStyleSheet(
            f"background-color: {color}; border-radius: 14px; border: 2px solid #2c2c2c;"
        )

    def _render(value):
        if threshold is not None:
            try:
                is_on = float(value) >= threshold
            except (TypeError, ValueError):
                is_on = None
        else:
            is_on = value == on_value
        state["is_on"] = is_on
        _paint(is_on)

    def _on_click():
        if not settable:
            return
        new_value = off_value if state["is_on"] else on_value
        threading.Thread(target=lambda: item.set_target_value(new_value), daemon=True).start()

    btn.clicked.connect(_on_click)
    _paint(None)

    box = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(box)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.addWidget(QtWidgets.QLabel(str(name)))
    lay.addWidget(btn)

    stop_event = threading.Event()
    _start_poll(box, item.get_current_value, _render, poll_interval, stop_event)
    box.stop = stop_event.set
    return box


def bar_gauge(item, title=None, vmin=None, vmax=None, orientation="vertical", poll_interval=0.5):
    """A vertical (default) or horizontal fill bar against [vmin, vmax] --
    stock QProgressBar, read-only (use slider() for a settable bar-like
    control)."""
    name = _item_name(item, title)
    if vmin is None or vmax is None:
        vmin, vmax = _guess_range(item)

    bar = QtWidgets.QProgressBar()
    bar.setOrientation(QtCore.Qt.Vertical if orientation == "vertical" else QtCore.Qt.Horizontal)
    bar.setRange(0, 1000)
    if orientation == "vertical":
        bar.setMinimumSize(28, 120)
    else:
        bar.setMinimumSize(160, 24)

    def _render(value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            bar.setFormat(_format_value(value))
            return
        span = vmax - vmin
        frac = 0.0 if span == 0 else (v - vmin) / span
        bar.setValue(int(round(max(0.0, min(1.0, frac)) * 1000)))
        bar.setFormat(_format_value(v))

    box = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(box) if orientation == "vertical" else QtWidgets.QHBoxLayout(box)
    lay.setContentsMargins(4, 4, 4, 4)
    label = QtWidgets.QLabel(str(name))
    label.setAlignment(QtCore.Qt.AlignCenter)
    lay.addWidget(label)
    lay.addWidget(bar)

    stop_event = threading.Event()
    _start_poll(box, item.get_current_value, _render, poll_interval, stop_event)
    box.stop = stop_event.set
    return box


def analog_gauge(item, title=None, vmin=None, vmax=None, poll_interval=0.5):
    """Stock-Qt stand-in for indicator_widgets.AnalogGauge's semicircular
    needle look: the same live horizontal bar_gauge, not a needle. See
    eco.widgets.indicator_widgets for the richer, hand-painted version."""
    return bar_gauge(item, title=title, vmin=vmin, vmax=vmax, orientation="horizontal", poll_interval=poll_interval)


def strip_chart(item, title=None, poll_interval=0.3, window_points=100):
    """Stock-Qt stand-in for indicator_widgets.StripChart's rolling line
    plot: no line, just the current value plus a bar showing where it
    falls within the min/max seen so far. `window_points` is accepted
    for interface parity but unused (there is no history buffer to cap).
    Mirrors eco.widgets.indicator_widgets_ipy.strip_chart exactly."""
    name = _item_name(item, title)
    seen = {"lo": None, "hi": None}

    bar = QtWidgets.QProgressBar()
    bar.setRange(0, 1000)
    bar.setMinimumSize(160, 24)
    bar.setTextVisible(False)
    value_label = QtWidgets.QLabel("--")

    def _render(value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            value_label.setText(_format_value(value))
            return
        lo = v if seen["lo"] is None else min(seen["lo"], v)
        hi = v if seen["hi"] is None else max(seen["hi"], v)
        seen["lo"], seen["hi"] = lo, hi
        span = hi - lo
        frac = 0.5 if span == 0 else (v - lo) / span
        bar.setValue(int(round(max(0.0, min(1.0, frac)) * 1000)))
        value_label.setText(f"{_format_value(v)}  [{_format_value(lo)} .. {_format_value(hi)}]")

    box = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(box)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.addWidget(QtWidgets.QLabel(str(name)))
    lay.addWidget(bar)
    lay.addWidget(value_label)

    stop_event = threading.Event()
    _start_poll(box, item.get_current_value, _render, poll_interval, stop_event)
    box.stop = stop_event.set
    return box


def numeric_tile(item, title=None, poll_interval=0.5, fmt="{:.4g}"):
    """A compact "KPI tile": name plus a large numeric readout -- stock
    QLabel with an enlarged font, no sparkline (see strip_chart's
    docstring for why; same reasoning applies here)."""
    name = _item_name(item, title)
    label = QtWidgets.QLabel(str(name))
    number = QtWidgets.QLabel("--")
    number.setAlignment(QtCore.Qt.AlignCenter)
    font = number.font()
    font.setPointSize(font.pointSize() + 10)
    font.setBold(True)
    number.setFont(font)

    def _render(value):
        try:
            text = fmt.format(value)
        except Exception:
            text = _format_value(value)
        number.setText(text)

    box = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(box)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.addWidget(label)
    lay.addWidget(number)

    stop_event = threading.Event()
    _start_poll(box, item.get_current_value, _render, poll_interval, stop_event)
    box.stop = stop_event.set
    return box


def slider(item, title=None, vmin=None, vmax=None, step=None, poll_interval=0.5):
    """A horizontal slider over [vmin, vmax] with a numeric readout --
    stock QSlider. Interactive (writes via set_target_value on release)
    if the item is settable; otherwise disabled, just following the
    polled value."""
    name = _item_name(item, title)
    if vmin is None or vmax is None:
        vmin, vmax = _guess_range(item)
    settable = hasattr(item, "set_target_value")
    step = step or max((vmax - vmin) / 200.0, 1e-9)

    sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    n_steps = max(1, int(round((vmax - vmin) / step)))
    sl.setRange(0, n_steps)
    sl.setEnabled(settable)
    sl.setMinimumWidth(200)
    value_label = QtWidgets.QLabel("--")
    value_label.setFixedWidth(60)

    def _pos_to_value(pos):
        return vmin + pos * step

    def _value_to_pos(value):
        return int(round((value - vmin) / step))

    def _render(value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        value_label.setText(_format_value(v))
        if sl.isSliderDown():
            return
        sl.blockSignals(True)
        sl.setValue(max(sl.minimum(), min(sl.maximum(), _value_to_pos(v))))
        sl.blockSignals(False)

    def _on_release():
        new_value = _pos_to_value(sl.value())
        threading.Thread(target=lambda: item.set_target_value(new_value), daemon=True).start()

    if settable:
        sl.sliderReleased.connect(_on_release)

    box = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(box)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.addWidget(QtWidgets.QLabel(str(name)))
    lay.addWidget(sl, 1)
    lay.addWidget(value_label)

    stop_event = threading.Event()
    _start_poll(box, item.get_current_value, _render, poll_interval, stop_event)
    box.stop = stop_event.set
    return box


def dial(item, title=None, vmin=None, vmax=None, step=None, poll_interval=0.5):
    """Stock-Qt stand-in for indicator_widgets.Dial. For an enum-valued
    item (anything exposing `enum_strs`), a row of checkable
    QPushButtons showing every choice at once -- same idea as
    indicator_widgets_ipy.dial's ToggleButtons, not the radial
    mode-select ring the richer Qt Dial draws. For a plain numeric item,
    the same QSlider as slider()."""
    enum_strs = list(getattr(item, "enum_strs", None) or [])
    if not enum_strs:
        return slider(item, title=title, vmin=vmin, vmax=vmax, step=step, poll_interval=poll_interval)

    name = _item_name(item, title)
    settable = hasattr(item, "set_target_value")
    suppress = [False]
    buttons = []

    def _on_clicked(idx):
        if suppress[0] or not settable:
            return
        new_value = enum_strs[idx]
        threading.Thread(target=lambda: item.set_target_value(new_value), daemon=True).start()

    btn_row = QtWidgets.QHBoxLayout()
    for i, choice_label in enumerate(enum_strs):
        b = QtWidgets.QPushButton(choice_label)
        b.setCheckable(True)
        b.setEnabled(settable)
        b.clicked.connect(lambda checked=False, i=i: _on_clicked(i))
        btn_row.addWidget(b)
        buttons.append(b)

    def _render(value):
        idx = _enum_index(value, enum_strs)
        if idx is None:
            return
        suppress[0] = True
        try:
            for i, b in enumerate(buttons):
                b.setChecked(i == idx)
        finally:
            suppress[0] = False

    box = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(box)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.addWidget(QtWidgets.QLabel(str(name)))
    lay.addLayout(btn_row)

    stop_event = threading.Event()
    _start_poll(box, item.get_current_value, _render, poll_interval, stop_event)
    box.stop = stop_event.set
    return box


#: same kind strings as eco.widgets.indicator_widgets.INDICATOR_TYPES /
#: eco.widgets.indicator_widgets_ipy.INDICATOR_TYPES -> (builder, needs_range)
INDICATOR_TYPES = (
    ("LED", led_indicator, False),
    ("Bar gauge", bar_gauge, True),
    ("Analog gauge", analog_gauge, True),
    ("Strip chart", strip_chart, False),
    ("Numeric tile (KPI)", numeric_tile, False),
    ("Dial / knob", dial, True),
    ("Slider", slider, True),
)


def create_indicator(kind, item, title=None, vmin=None, vmax=None, **kwargs):
    """Build one indicator gadget by name (one of INDICATOR_TYPES' first
    elements, e.g. "LED"/"Bar gauge"/...) -- same kind strings as the
    other two indicator-widget modules' create_indicator."""
    for label, factory, needs_range in INDICATOR_TYPES:
        if label == kind:
            if needs_range:
                return factory(item, title=title, vmin=vmin, vmax=vmax, **kwargs)
            return factory(item, title=title, **kwargs)
    raise ValueError(f"unknown indicator kind {kind!r}; choose one of {[t[0] for t in INDICATOR_TYPES]}")
