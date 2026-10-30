"""
The per-item "⋯" menu of the property grids -- the toolkit-independent part.

Every row of the Qt grid (eco.widgets.display_qt) and of the ipywidgets grid
(eco.widgets.display_widget) ends in a small menu button for its item --
Adjustable, Detector or Assembly alike. What it offers is decided here, from
what the item can do, and built by eco.widgets.item_menu_qt /
eco.widgets.item_menu_ipy:

- **Open** -- every ``_widget_*`` view the item has (property grid, tweak
  panel, viewer, SVG panel, ...; see the ``_widget_<something>`` convention
  in eco.elements.assembly), docked into eco desktop when the grid is docked
  there, shown below the row in a notebook.
- **Live strip chart** -- a rolling time plot of ``get_current_value()``,
  inside this window/page (monitored where the item can push changes,
  polled otherwise). Works for anything readable, EPICS or not.
- **Strip plot window** -- the archiver's ``strip_plot()`` (items with
  archived channels): its own process and window, full dispatcher/EPICS
  rate, recording and saving. Only where a window can appear (Qt, or a
  notebook kernel with a display).
- **History** -- ``get_archiver_time_range()`` (eco.dbase.archiver) for a
  chosen time range, plotted in place.
- **Add indicator** -- an LED/gauge/tile/... gadget (see
  eco.widgets.indicator_widgets and indicator_widgets_ipy).
- **Memories** -- the item's own memory browser, if it has one.
- **Details** -- full name, class, channels (PV names), unit, limits, doc.
- **Copy name / Copy PV names / Find IOC** -- Qt only (clipboard, IOC finder).

Nothing here talks to hardware while building a menu -- only class-level
lookups; whatever needs a read (details, history) runs in the background.
"""
import datetime
import enum
import inspect
import os
import threading
import time
from collections import deque

# label shown for a `_widget_*` method; anything else is named after the method
WIDGET_LABELS = {
    "_widget_assembly": "Property grid",
    "_widget_tweak": "Tweak panel",
    "_widget_viewer": "Viewer",
    "_widget_svg_panel": "SVG panel",
}

# (label, seconds) -- the history ranges offered, the second one is the default
HISTORY_RANGES = (
    ("10 min", 600),
    ("1 h", 3600),
    ("12 h", 12 * 3600),
    ("2 days", 2 * 86400),
    ("7 days", 7 * 86400),
)
DEFAULT_HISTORY_RANGE = "1 h"

# (label, seconds) -- the strip chart time windows, the second one is the default
STRIP_WINDOWS = (
    ("30 s", 30),
    ("1 min", 60),
    ("5 min", 300),
    ("15 min", 900),
    ("1 h", 3600),
)
DEFAULT_STRIP_WINDOW = "1 min"
STRIP_POLL_INTERVAL = 0.5  # s; monitored items report every change anyway
STRIP_MAX_POINTS = 20000


def full_name(item):
    try:
        return item.alias.get_full_name()
    except Exception:
        pass
    name = getattr(item, "name", None)
    return str(name) if name else repr(item)


def widget_methods(item):
    """[(label, method name)] of the views `item` offers: its ``_widget_*``
    methods, the one `widget()` opens by default marked as such and listed
    first. Class-level lookup only (no instance attribute is evaluated)."""
    cls = type(item)
    names = sorted(
        n for n in dir(cls) if n.startswith("_widget_") and callable(getattr(cls, n, None))
    )
    default = getattr(cls, "_default_widget", None)
    if not default and "_widget_assembly" in names:
        default = "_widget_assembly"
    result = []
    for name in names:
        label = WIDGET_LABELS.get(name) or name[len("_widget_"):].replace("_", " ").capitalize()
        if name == default:
            result.insert(0, (label + " (default)", name))
        else:
            result.append((label, name))
    return result


def call_widget_method(item, method_name, backend):
    """Call ``item.<method_name>()`` for `backend` ("qt"/"ipy"), passing
    `backend` on to methods that take one (e.g. ``_widget_tweak``), so a
    Qt grid gets the Qt view even where eco would otherwise guess."""
    method = getattr(item, method_name)
    try:
        params = inspect.signature(method).parameters
    except (TypeError, ValueError):
        params = {}
    if "backend" in params:
        return method(backend=backend)
    return method()


def is_readable(item):
    return callable(getattr(item, "get_current_value", None))


def has_history(item):
    return callable(getattr(type(item), "get_archiver_time_range", None))


def has_strip_plot(item):
    return callable(getattr(type(item), "strip_plot", None))


def can_open_windows():
    """Whether a separate window (the archiver strip plot) can appear here."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def has_memory(item):
    try:
        return getattr(item, "memory", None) is not None
    except Exception:
        return False


def channels_of(item):
    """The channel (PV) names of `item` and its children, from its alias --
    no channel-access traffic."""
    try:
        entries = item.alias.get_all()
    except Exception:
        return []
    seen = []
    for entry in entries:
        channel = entry.get("channel")
        if channel and channel not in seen:
            seen.append(channel)
    return seen


def kind_of(item):
    try:
        from eco import Adjustable, Detector
        from eco.elements.assembly import Assembly
    except Exception:
        return type(item).__name__
    kinds = []
    if isinstance(item, Adjustable):
        kinds.append("Adjustable")
    elif isinstance(item, Detector):
        kinds.append("Detector")
    if isinstance(item, Assembly):
        kinds.append("Assembly")
    return ", ".join(kinds) or "other"


def describe_item(item):
    """[(field, text)] describing `item`, for the Details view. May read
    hardware (unit, limits) -- call it off the GUI/kernel thread."""
    cls = type(item)
    rows = [
        ("name", full_name(item)),
        ("kind", kind_of(item)),
        ("class", f"{cls.__module__}.{cls.__qualname__}"),
    ]
    if is_readable(item):
        try:
            value = item.get_current_value()
            rows.append(("value", value.name if isinstance(value, enum.Enum) else repr(value)))
        except Exception as exc:
            rows.append(("value", f"<{type(exc).__name__}: {exc}>"))
    unit = getattr(item, "unit", None)
    if unit is not None:
        try:
            unit = unit.get_current_value() if hasattr(unit, "get_current_value") else unit
            if unit:
                rows.append(("unit", str(unit)))
        except Exception:
            pass
    get_limits = getattr(item, "get_limits", None)
    if callable(get_limits):
        try:
            rows.append(("limits", str(tuple(get_limits()))))
        except Exception:
            pass
    enum_strs = getattr(item, "enum_strs", None)
    if enum_strs:
        rows.append(("states", ", ".join(str(s) for s in enum_strs)))
    channels = channels_of(item)
    if channels:
        rows.append(("channels", "\n".join(channels)))
    views = widget_methods(item)
    if views:
        rows.append(("views", ", ".join(f"{name}()" for _, name in views)))
    doc = inspect.getdoc(cls)
    if doc and doc != inspect.getdoc(object):
        rows.append(("doc", doc.split("\n\n")[0]))
    return rows


def details_text(rows):
    width = max((len(field) for field, _ in rows), default=0)
    lines = []
    for field, text in rows:
        parts = str(text).split("\n")
        lines.append(f"{field:<{width}}  {parts[0]}")
        lines += [" " * (width + 2) + p for p in parts[1:]]
    return "\n".join(lines)


def fetch_history(item, seconds):
    """Archived data of `item` over the last `seconds` (a DataFrame with one
    column per channel, named by label), or None. Blocks on the archiver --
    call it off the GUI/kernel thread."""
    return item.get_archiver_time_range(
        start=-seconds, plot=False, verbose=False, column_names="label"
    )


def plot_history(ax, data):
    """Draw a `fetch_history` result onto `ax`; returns whether anything
    numeric was drawn. Same look as eco.dbase.archiver's own plots."""
    from eco.dbase.archiver import _plot_dataframe_on

    columns = [c for c in data.columns if not str(c).endswith(" count")]
    lines = _plot_dataframe_on(ax, data, columns)
    if lines:
        ax.legend(fontsize="small")
    return bool(lines)


# -- live strip chart: samples and drawing, shared by both toolkits ------------------


def numeric(value, enum_strs=None):
    """A plottable number for `value`, or None: numbers and bools as they
    are, enum members by their value, an enum label by its index."""
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    if enum_strs and isinstance(value, str):
        try:
            return float([str(s) for s in enum_strs].index(value))
        except ValueError:
            pass
    return None


class SeriesBuffer:
    """Time-stamped samples of one item, thread-safe: readers append from
    background threads, the chart draws a snapshot."""

    def __init__(self, item, max_points=STRIP_MAX_POINTS):
        self.enum_strs = getattr(item, "enum_strs", None)
        self._samples = deque(maxlen=max_points)
        self._lock = threading.Lock()
        self.last_text = "--"
        self.changed = threading.Event()

    def add(self, value, t=None):
        number = numeric(value, self.enum_strs)
        self.last_text = value.name if isinstance(value, enum.Enum) else str(value)
        if number is None:
            return False
        with self._lock:
            self._samples.append((time.time() if t is None else t, number))
        self.changed.set()
        return True

    def clear(self):
        with self._lock:
            self._samples.clear()

    def snapshot(self, window):
        """(times, values) of the last `window` seconds, the sample from
        before the window included so the step line starts at its left edge."""
        start = time.time() - window
        with self._lock:
            samples = list(self._samples)
        first = 0
        for i in range(len(samples) - 1, -1, -1):
            if samples[i][0] < start:
                first = i
                break
        samples = samples[first:]
        return [t for t, _ in samples], [v for _, v in samples]


def draw_strip(ax, buffer, window, title=None):
    """Draw the last `window` seconds of `buffer` onto `ax`: a step line held
    up to now (a monitored value stays where it is until it changes)."""
    from matplotlib import dates as mdates

    ax.clear()
    times, values = buffer.snapshot(window)
    now = time.time()
    if times:
        times = times + [now]
        values = values + [values[-1]]
        x = [datetime.datetime.fromtimestamp(t) for t in times]
        ax.step(x, values, where="post", color="#1f77b4", linewidth=1.4)
        ax.plot(x[-1:], values[-1:], "o", color="#1f77b4", markersize=3)
    ax.set_xlim(
        datetime.datetime.fromtimestamp(now - window), datetime.datetime.fromtimestamp(now)
    )
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    if buffer.enum_strs and values:
        ax.set_yticks(range(len(buffer.enum_strs)))
        ax.set_yticklabels([str(s) for s in buffer.enum_strs], fontsize="small")
    ax.grid(True, alpha=0.3)
    if title:
        ax.set_title(f"{title}  {buffer.last_text}", fontsize="small", loc="left")
