"""
ipywidgets-native counterparts of eco.widgets.indicator_widgets (the
Qt-only LabVIEW-style LED/gauge/dial "gadgets"), for the lab/voila front
ends. First pass, deliberately built entirely from stock ipywidgets
controls (FloatProgress, ToggleButtons, FloatSlider, Button, HTML) --
"something that works", not a redraw of the Qt gadgets' exact look. Two
of the seven don't have a good stock equivalent (a real needle gauge, a
rolling line plot) and are approximated with a plain bar/value display
rather than pulling in a new charting/gauge library; see
docs/widget_views.md for the full mapping and the (not yet added)
candidate for those two -- Plotly's go.Indicator, itself a real
ipywidgets.DOMWidget (FigureWidget), for both at once.

Each function below builds one live, self-polling ipywidgets.Widget (own
background thread, own .stop()) for one Adjustable/Detector item -- same
background-thread-sets-.value-directly polling convention already used
throughout eco.widgets.display_widget (see
build_adjustable_control_widget there), not a new pattern. Writes (where
the gadget is interactive) go through item.set_target_value() on a
short-lived thread, same convention the Qt gadgets and the rest of eco's
widgets already use.

Usage:
    from eco.widgets.indicator_widgets_ipy import bar_gauge
    from IPython.display import display
    display(bar_gauge(my_assembly.pressure, vmin=0, vmax=100))
"""
import enum
import threading

import ipywidgets as widgets


def _format_value(value):
    if isinstance(value, enum.Enum):
        return value.name
    try:
        return f"{float(value):.4g}"
    except (TypeError, ValueError):
        return str(value)


def _guess_range(item):
    """Best-effort (vmin, vmax) from a handful of common limit-attribute
    names; (0.0, 100.0) if none are found or usable -- same heuristic as
    eco.widgets.indicator_widgets._guess_range (duplicated rather than
    imported, since that module pulls in qtpy at import time and this one
    is meant to work in a Qt-less notebook/Voila deployment)."""
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
    """Best-effort index of `value` within `enum_strs` -- a polled value
    may come back as an int index, an enum.Enum member (compared by
    .name), or a plain string already matching one of enum_strs. None if
    it doesn't resolve to any of them."""
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


def _start_poll(get_value, render, poll_interval, stop_event):
    """Background thread: read get_value() every poll_interval seconds and
    hand each reading to render(value) -- render is expected to touch
    widget .value traits directly, same convention as
    eco.widgets.display_widget._apply_item_update's poll loop."""

    def _loop():
        while not stop_event.is_set():
            try:
                value = get_value()
            except Exception:
                pass
            else:
                try:
                    render(value)
                except Exception:
                    pass
            if stop_event.wait(poll_interval):
                break

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def led_indicator(item, title=None, on_value=True, off_value=False, threshold=None, poll_interval=0.3):
    """A round on/off indicator: a Button styled green/red/plain via its
    button_style, since a plain HTML/Label can't take a click back from
    the browser. "On" means value == on_value, or, if `threshold` is
    given, value >= threshold. Click to toggle if the item is settable."""
    name = _item_name(item, title)
    settable = hasattr(item, "set_target_value")
    state = {"is_on": None}

    btn = widgets.Button(
        icon="circle", tooltip=name, disabled=not settable,
        layout=widgets.Layout(width="42px", height="32px"),
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
        btn.button_style = "" if is_on is None else ("success" if is_on else "danger")

    def _on_click(b=None):
        if not settable:
            return
        new_value = off_value if state["is_on"] else on_value
        threading.Thread(target=lambda: item.set_target_value(new_value), daemon=True).start()

    btn.on_click(_on_click)
    stop_event = threading.Event()
    _start_poll(item.get_current_value, _render, poll_interval, stop_event)

    box = widgets.HBox([widgets.Label(str(name)), btn])
    box.stop = stop_event.set
    return box


def bar_gauge(item, title=None, vmin=None, vmax=None, orientation="vertical", poll_interval=0.5):
    """A vertical (default) or horizontal fill bar against [vmin, vmax] --
    stock ipywidgets.FloatProgress, read-only (use slider() for a
    settable bar-like control)."""
    name = _item_name(item, title)
    if vmin is None or vmax is None:
        vmin, vmax = _guess_range(item)
    size = (
        widgets.Layout(width="50px", height="160px")
        if orientation == "vertical"
        else widgets.Layout(width="220px")
    )
    bar = widgets.FloatProgress(min=vmin, max=vmax, orientation=orientation, layout=size)
    value_label = widgets.Label("--")

    def _render(value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            value_label.value = _format_value(value)
            return
        bar.value = max(vmin, min(vmax, v))
        value_label.value = _format_value(v)

    stop_event = threading.Event()
    _start_poll(item.get_current_value, _render, poll_interval, stop_event)

    header = widgets.Label(str(name))
    body = widgets.VBox([bar, value_label]) if orientation == "vertical" else widgets.HBox([bar, value_label])
    box = widgets.VBox([header, body])
    box.stop = stop_event.set
    return box


def analog_gauge(item, title=None, vmin=None, vmax=None, poll_interval=0.5):
    """Stock-ipywidgets stand-in for the Qt AnalogGauge's semicircular
    needle look: the same live horizontal bar_gauge, not a needle. A real
    needle gauge needs a plotting/gauge library -- Plotly's go.Indicator
    is the recommended one (see docs/widget_views.md) -- deliberately not
    added in this first, dependency-free pass."""
    return bar_gauge(item, title=title, vmin=vmin, vmax=vmax, orientation="horizontal", poll_interval=poll_interval)


def strip_chart(item, title=None, poll_interval=0.3, window_points=100):
    """Stock-ipywidgets stand-in for the Qt StripChart's rolling line
    plot: no line, just the current value plus a bar showing where it
    falls within the min/max seen so far (the window grows as new
    extremes arrive). A real rolling plot needs a charting widget library
    -- bqplot is the natural pick, already a real Jupyter widget (see
    docs/widget_views.md) -- deliberately not added in this first pass.
    `window_points` is accepted for interface parity with the Qt
    StripChart but unused here (there is no history buffer to cap)."""
    name = _item_name(item, title)
    seen = {"lo": None, "hi": None}
    bar = widgets.FloatProgress(min=0.0, max=1.0, layout=widgets.Layout(width="220px"))
    value_label = widgets.Label("--")

    def _render(value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            value_label.value = _format_value(value)
            return
        lo = v if seen["lo"] is None else min(seen["lo"], v)
        hi = v if seen["hi"] is None else max(seen["hi"], v)
        seen["lo"], seen["hi"] = lo, hi
        span = hi - lo
        bar.value = 0.5 if span == 0 else (v - lo) / span
        value_label.value = f"{_format_value(v)}  [{_format_value(lo)} .. {_format_value(hi)}]"

    stop_event = threading.Event()
    _start_poll(item.get_current_value, _render, poll_interval, stop_event)

    box = widgets.VBox([widgets.Label(str(name)), bar, value_label])
    box.stop = stop_event.set
    return box


def numeric_tile(item, title=None, poll_interval=0.5, fmt="{:.4g}"):
    """A compact "KPI tile": name plus a large numeric readout -- stock
    ipywidgets.HTML, no sparkline (see strip_chart's docstring for why;
    same reasoning applies here)."""
    name = _item_name(item, title)
    label = widgets.HTML(f"<b>{name}</b>")
    number = widgets.HTML("<div style='font-size:28px; font-weight:600;'>--</div>")

    def _render(value):
        try:
            text = fmt.format(value)
        except Exception:
            text = _format_value(value)
        number.value = f"<div style='font-size:28px; font-weight:600;'>{text}</div>"

    stop_event = threading.Event()
    _start_poll(item.get_current_value, _render, poll_interval, stop_event)

    box = widgets.VBox([label, number])
    box.stop = stop_event.set
    return box


def slider(item, title=None, vmin=None, vmax=None, step=None, poll_interval=0.5):
    """A horizontal slider over [vmin, vmax] with a numeric readout --
    stock ipywidgets.FloatSlider. Interactive (writes via
    set_target_value on release, i.e. continuous_update=False) if the
    item is settable; otherwise disabled, just following the polled
    value."""
    name = _item_name(item, title)
    if vmin is None or vmax is None:
        vmin, vmax = _guess_range(item)
    settable = hasattr(item, "set_target_value")
    suppress = [False]

    w = widgets.FloatSlider(
        min=vmin, max=vmax, step=step or max((vmax - vmin) / 200.0, 1e-9),
        description=str(name), continuous_update=False, disabled=not settable,
        layout=widgets.Layout(width="320px"),
    )

    def _render(value):
        try:
            v = max(vmin, min(vmax, float(value)))
        except (TypeError, ValueError):
            return
        suppress[0] = True
        try:
            w.value = v
        finally:
            suppress[0] = False

    def _on_change(change):
        if suppress[0] or not settable or change.get("name") != "value":
            return
        new_value = change["new"]
        threading.Thread(target=lambda: item.set_target_value(new_value), daemon=True).start()

    w.observe(_on_change, names="value")
    stop_event = threading.Event()
    _start_poll(item.get_current_value, _render, poll_interval, stop_event)
    w.stop = stop_event.set
    return w


def dial(item, title=None, vmin=None, vmax=None, step=None, poll_interval=0.5):
    """Stock-ipywidgets stand-in for the Qt Dial. For an enum-valued item
    (anything exposing `enum_strs`), a row of ToggleButtons showing every
    choice at once -- same idea as the Qt version's mode-select ring, laid
    out as buttons instead of a circle. For a plain numeric item, the same
    FloatSlider as slider() -- dragging a value around a circle isn't
    idiomatic web UX, so this reuses the slider rather than faking a round
    knob."""
    enum_strs = list(getattr(item, "enum_strs", None) or [])
    if not enum_strs:
        return slider(item, title=title, vmin=vmin, vmax=vmax, step=step, poll_interval=poll_interval)

    name = _item_name(item, title)
    settable = hasattr(item, "set_target_value")
    suppress = [False]

    toggles = widgets.ToggleButtons(options=enum_strs, disabled=not settable)

    def _render(value):
        idx = _enum_index(value, enum_strs)
        if idx is None:
            return
        suppress[0] = True
        try:
            toggles.value = enum_strs[idx]
        finally:
            suppress[0] = False

    def _on_change(change):
        if suppress[0] or not settable or change.get("name") != "value":
            return
        new_value = change["new"]
        threading.Thread(target=lambda: item.set_target_value(new_value), daemon=True).start()

    toggles.observe(_on_change, names="value")
    stop_event = threading.Event()
    _start_poll(item.get_current_value, _render, poll_interval, stop_event)

    box = widgets.VBox([widgets.Label(str(name)), toggles])
    box.stop = stop_event.set
    return box


#: name shown alongside eco.widgets.indicator_widgets.INDICATOR_TYPES'
#: kind labels (same strings, so a future shared "add indicator" picker
#: can drive either backend off one list) -> (builder, needs_range)
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
    elements, e.g. "LED"/"Bar gauge"/...) -- same kind strings as
    eco.widgets.indicator_widgets.create_indicator's Qt version."""
    for label, factory, needs_range in INDICATOR_TYPES:
        if label == kind:
            if needs_range:
                return factory(item, title=title, vmin=vmin, vmax=vmax, **kwargs)
            return factory(item, title=title, **kwargs)
    raise ValueError(f"unknown indicator kind {kind!r}; choose one of {[t[0] for t in INDICATOR_TYPES]}")
