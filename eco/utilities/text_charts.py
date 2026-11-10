"""Time charts as text in the terminal, with keys to zoom and pan.

The terminal counterparts of the archiver's history plot
(``get_archiver_time_range``) and the live strip plot (``strip_plot``), for
where no window can appear (plain ssh, ``ECO_NO_X=1``) or when asked for
(``plot="text"``, or ``ECO_PLOT=text`` as the default). One panel per
channel over a shared time axis, drawn with eco.utilities.termplot
(uniplot), values as steps (a value holds until the next sample).

Keys while a chart is shown::

    ← →      pan in time (a quarter of the shown span)
    + -      zoom in / out in time
    ↑ ↓      zoom in / out in y
    a        autoscale y again
    f        live: follow now again / history: show everything
    space    live: pause / resume
    q        quit (the last picture stays)

Without a terminal to read keys from (output to a file, a notebook), one
picture is printed instead.
"""

import os
import select
import sys
import time

import numpy as np

KEY_HELP = (
    "← → pan   + - zoom time   ↑ ↓ zoom y   a autoscale y   "
    "f {follow}   {pause}q quit"
)

PLOT_MODES = ("auto", "text", "window", "none")


# -- where a chart goes -------------------------------------------------------------
def display_available():
    """A window can appear: an X/Wayland display and no ECO_NO_X."""
    from eco.widgets.tweak_recorder import display_available as _available

    return _available()


def plot_mode(plot="auto"):
    """'text', 'window' or 'none' for a chart's `plot` argument: 'text' and
    'window' as given; 'auto' (and True) from ECO_PLOT if set, else a window
    where a display is available and text otherwise; False/None: 'none'."""
    if plot is None or plot is False:
        return "none"
    if plot is True:
        plot = "auto"
    if not isinstance(plot, str):
        return "window"  # an Axes / Figure to draw into
    mode = plot.strip().lower()
    if mode not in PLOT_MODES:
        raise ValueError(f"plot={plot!r}: one of {PLOT_MODES}, True/False, or an Axes")
    if mode == "auto":
        mode = os.environ.get("ECO_PLOT", "").strip().lower() or "auto"
        if mode not in PLOT_MODES:
            mode = "auto"
    if mode == "auto":
        mode = "window" if display_available() else "text"
    if mode == "window" and not display_available():
        print("no display for a window, drawing as text")
        mode = "text"
    return mode


# -- drawing ------------------------------------------------------------------------
def _local_datetime64(times):
    """Epoch seconds -> naive local datetime64 (what uniplot labels)."""
    times = np.asarray(times, dtype=float)
    offset = time.localtime().tm_gmtoff
    return ((times + offset) * 1e6).astype("int64").astype("datetime64[us]")


def _steps(times, values, t_hold=None):
    """A sample holds until the next one: (t0,v0) (t1,v0) (t1,v1) ...,
    held on to `t_hold` (now, for live data)."""
    times = np.asarray(times, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(times) == 0:
        return times, values
    t = np.repeat(times, 2)[1:]
    v = np.repeat(values, 2)[:-1]
    if t_hold is not None and t_hold > times[-1]:
        t = np.append(t, t_hold)
        v = np.append(v, values[-1])
    return t, v


def _visible(times, values, t_start, t_end):
    """The samples within [t_start, t_end], plus the one before t_start (a
    step starts at its left edge)."""
    times = np.asarray(times, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(times) == 0:
        return times, values
    order = np.argsort(times, kind="stable")
    times, values = times[order], values[order]
    first = max(0, int(np.searchsorted(times, t_start, side="right")) - 1)
    last = int(np.searchsorted(times, t_end, side="right"))
    t, v = times[first:last], values[first:last]
    ok = np.isfinite(v)
    return t[ok], v[ok]


def render(series, t_start, t_end, y_zoom=1.0, width=None, rows=None, hold=None, footer=""):
    """The panels of `series` ([(label, times, values)], epoch seconds) for
    [t_start, t_end] as text, sized to the terminal (`rows` lines in all,
    `footer` included). `y_zoom` < 1 zooms into y around each panel's
    middle. `hold`: draw the last value on to this time (now, live)."""
    from uniplot import plot_to_string

    from eco.utilities import termplot

    size = termplot.terminal_size()
    width = width or max(20, size.columns - 14)
    rows = rows or size.lines - 1
    footer_rows = footer.count("\n") + 1 if footer else 0
    n = max(1, len(series))
    # per panel: title + 2 frame lines + its rows; one line of time labels
    height = max(2, (rows - footer_rows - 1 - 3 * n) // n)
    x_min, x_max = _local_datetime64([t_start, t_end])
    blocks = []
    for k, (label, times, values) in enumerate(series):
        t, v = _visible(times, values, t_start, t_end)
        t, v = _steps(t, v, t_hold=min(hold, t_end) if hold else None)
        kwargs = dict(width=width, height=height, x_min=x_min, x_max=x_max, lines=True,
                      x_labels=(k == n - 1))
        title = str(label)
        if len(v):
            low, high = float(np.min(v)), float(np.max(v))
            if y_zoom != 1.0:
                middle, half = (low + high) / 2, max(high - low, 1e-12) / 2 * y_zoom
                low, high = middle - half, middle + half
                kwargs.update(y_min=low, y_max=high)
            title = f"{label}   {v[-1]:.6g}"
            xs, ys = _local_datetime64(t), v
        else:
            title = f"{label}   (no data in this span)"
            xs, ys = x_min[None], np.array([np.nan])
        try:
            text = plot_to_string(ys, xs, title=title, **kwargs)
        except Exception as exc:  # never let one panel cost the picture
            text = f"{title}\n  (cannot draw: {exc})"
        blocks.append(text.rstrip("\n"))
    if footer:
        blocks.append(footer)
    return "\n".join(blocks)


# -- the interactive loop -------------------------------------------------------------
_KEYS = {
    "\x1b[D": "left", "\x1b[C": "right", "\x1b[A": "up", "\x1b[B": "down",
    "+": "zoom_in", "=": "zoom_in", "-": "zoom_out", "_": "zoom_out",
    "a": "autoscale", "f": "follow", " ": "pause", "q": "quit", "\x1b": "quit",
}


def _read_key(fd, timeout):
    """A key within `timeout` seconds (arrows as their escape sequence), or None."""
    if not select.select([fd], [], [], timeout)[0]:
        return None
    data = os.read(fd, 1)
    if data == b"\x1b":
        # an arrow key sends three bytes at once; Esc alone sends one
        if select.select([fd], [], [], 0.02)[0]:
            data += os.read(fd, 2)
    return data.decode("utf8", "replace")


class TimeView:
    """What is shown: the time span and the y zoom, and whether a live
    chart follows now."""

    def __init__(self, window, t_end=None, live=False, t_first=None):
        self.window = float(window)
        self.t_end = t_end
        self.live = live
        self.following = live
        self.paused = False
        self.y_zoom = 1.0
        self._t_first = t_first  # history: where the data start, for "f"
        self._full = (window, t_end)

    def span(self, now):
        end = now if self.following else self.t_end
        return end - self.window, end

    def handle(self, action, now):
        start, end = self.span(now)
        if action in ("left", "right"):
            self.t_end = end + (-1 if action == "left" else 1) * self.window / 4
            if self.live and self.t_end >= now:
                self.t_end, self.following = now, not self.paused
            else:
                self.following = False
        elif action in ("zoom_in", "zoom_out"):
            middle = (start + end) / 2
            self.window *= 0.5 if action == "zoom_in" else 2.0
            if not self.following:
                self.t_end = middle + self.window / 2
        elif action == "up":
            self.y_zoom *= 0.5
        elif action == "down":
            self.y_zoom *= 2.0
        elif action == "autoscale":
            self.y_zoom = 1.0
        elif action == "follow":
            if self.live:
                self.following, self.paused = True, False
            else:
                self.window, self.t_end = self._full
                self.y_zoom = 1.0
        elif action == "pause" and self.live:
            self.paused = not self.paused
            if self.paused:
                self.t_end, self.following = end, False
            else:
                self.following = True


def show(get_series, window, t_end=None, live=False, max_rate=5.0, title=None):
    """Show the chart of `get_series()` ([(label, times, values)]) and run
    the keys until q. `live`: refresh it, following now. Without a
    terminal, print one picture."""
    from eco.utilities import termplot

    view = TimeView(window, t_end=t_end, live=live)

    def picture():
        now = time.time()
        start, end = view.span(now)
        state = ""
        if live:
            state = ("following now" if view.following else "paused" if view.paused
                     else "scrolled back (f: follow now)")
        head = f"{title}   " if title else ""
        span = _format_span(end - start)
        footer = f"{head}{span}{'   ' + state if state else ''}\n" + KEY_HELP.format(
            follow="follow now" if live else "all", pause="space pause   " if live else ""
        )
        return render(get_series(), start, end, view.y_zoom, hold=now if live else None,
                      footer=footer)

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        if live:  # something to show: collect for a moment first
            time.sleep(min(float(window), 2.0))
        print(picture())
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    region = termplot.LiveRegion()
    region.install()
    try:
        tty.setcbreak(fd)
        region.show(picture())
        interval = 1.0 / max(0.5, float(max_rate))
        while True:
            key = _read_key(fd, interval if live else None)
            if key is not None:
                action = _KEYS.get(key)
                if action == "quit":
                    break
                if action:
                    view.handle(action, time.time())
            region.show(picture())
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        region.uninstall()
        print()


def _format_span(seconds):
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{seconds / size:.3g} {unit} shown"
    return f"{seconds:.3g} s shown"


# -- history: archived data -------------------------------------------------------------
def history_series(data, channels, labels=None):
    """[(label, times, values)] from an archiver DataFrame (UTC index)."""
    import enum

    import pandas as pd

    def number(v):
        # archiver enums (datahub.Enum) by their id, Python enums by value
        if hasattr(v, "id") and not isinstance(v, (int, float)):
            return v.id
        return v.value if isinstance(v, enum.Enum) else v

    labels = labels or channels
    index = pd.DatetimeIndex(data.index)
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    # epoch seconds, whatever resolution the index has (asi8 is not always ns)
    times = ((index - pd.Timestamp("1970-01-01")) / pd.Timedelta(seconds=1)).to_numpy(dtype=float)
    series = []
    for channel, label in zip(channels, labels):
        if channel not in data:
            continue
        values = data[channel].map(number)
        values = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(values)
        if ok.any():
            series.append((label, times[ok], values[ok]))
    return series


def show_history(data, channels, labels=None):
    """The archiver history chart as text (see `show`)."""
    series = history_series(data, channels, labels)
    if not series:
        print("nothing numeric to plot")
        return
    t_first = min(s[1][0] for s in series)
    t_last = max(s[1][-1] for s in series)
    span = max(t_last - t_first, 1.0)
    show(lambda: series, window=span * 1.02, t_end=t_last + span * 0.01, title="history")


# -- live: the strip chart ------------------------------------------------------------------
class LiveFeed:
    """Live samples in this process: channels from the dispatcher/EPICS
    (the strip plot window's own sources) and `get_current_value()` of
    other objects, polled. Keeps `keep` seconds, so a zoom out or a pan
    back has data. `labels` align with `monitorable_names + channels`."""

    def __init__(self, channels=(), force_type=None, channel_types=None, labels=None,
                 monitorables=(), monitorable_names=(), keep=3600, duration=24 * 3600,
                 poll_interval=0.2):
        import threading

        from eco.dbase.archiver import LIVE_SOURCES, _StripBuffer, _group_by_type

        self.buffer = _StripBuffer(keep)
        self.names = list(monitorable_names) + list(channels)
        self.labels = list(labels) if labels else list(self.names)
        self._sources = []
        for channel_type, group in _group_by_type(list(channels), force_type, channel_types).items():
            source = LIVE_SOURCES[channel_type](time_type="sec")
            source.add_listener(self.buffer)
            source.request(dict(channels=group, start=0.0, end=float(duration)), background=True)
            self._sources.append(source)
        self._stop = threading.Event()
        self._poller = None
        if monitorables:
            pairs = list(zip(monitorable_names, monitorables))

            def poll():
                while not self._stop.is_set():
                    for name, obj in pairs:
                        try:
                            self.buffer.on_channel_record(None, name, time.time(), None,
                                                          obj.get_current_value())
                        except Exception:
                            pass  # one failed read must not end the chart
                    self._stop.wait(poll_interval)

            self._poller = threading.Thread(target=poll, daemon=True)
            self._poller.start()

    def series(self):
        snapshot = self.buffer.snapshot()
        return [(label, *snapshot.get(name, ([], []))) for name, label in zip(self.names, self.labels)]

    def stop(self):
        self._stop.set()
        # as the strip plot window does: abort, join, close once the thread ended
        for source in self._sources:
            try:
                source.abort()
            except Exception:
                pass
        for source in self._sources:
            try:
                source.join(timeout=5)
                if not source.is_thread_running():
                    source.close()
            except Exception:
                pass


def show_live(feed, window=60, max_rate=5, title="strip chart"):
    """The live strip chart of `feed` as text, until q (see `show`).
    Returns {label: (times, values)} of what was collected."""
    try:
        show(feed.series, window=window, live=True, max_rate=max_rate, title=title)
        return {label: (list(t), list(v)) for label, t, v in feed.series()}
    finally:
        feed.stop()
