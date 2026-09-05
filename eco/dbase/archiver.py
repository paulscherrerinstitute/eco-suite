import datetime
import dateutil.parser
import logging
import multiprocessing
import re
import subprocess
import sys
import threading
import time
from collections import deque
from numbers import Number

import pandas as pd
from matplotlib import pyplot as plt
from matplotlib import dates as mdates
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button

from datahub import Consumer, Daqbuf, Dispatcher, Enum, Epics, Table, pulse_id_to_time

from .. import ecocnf
from ..elements.assembly import Assembly
from ..epics_utils.detector import DetectorPvDataStream

_logger = logging.getLogger(__name__)

# Historical data backends, keyed by the channel type convention used
# throughout eco: "CA" channels are archived by the EPICS Archiver Appliance
# (served, despite the name, under the "sf-archiver" backend), "BS" channels
# are the beam-synchronous data recorded in the DataBuffer ("sf-databuffer").
# Both are retrieved through the same "Daqbuf" retrieval service - the old
# DataBuffer/"sf-archiverappliance" endpoint used previously no longer serves
# archiver data at all.
HISTORY_BACKENDS = {"CA": "sf-archiver", "BS": "sf-databuffer"}
DEFAULT_HISTORY_BACKEND = HISTORY_BACKENDS["BS"]

# Live data sources for the same two channel types: "BS" channels are
# streamed through the bsread Dispatcher, "CA" channels are read directly
# via EPICS channel access.
LIVE_SOURCES = {"CA": Epics, "BS": Dispatcher}

# A historical query whose end time is within this many seconds of "now" can
# spuriously come back empty - mostly a cold-start effect on a freshly opened
# connection, occasionally genuine retrieval-service ingestion lag - and is
# worth a few retries. A query further in the past that comes back empty
# means there really is no data, and is not retried.
RETRY_WINDOW = datetime.timedelta(seconds=60)
RETRY_COUNT = 6
RETRY_DELAY = 1.5

# A single, facility-wide pulse-id counter (the injector gun EVR's raw
# pulse-id readback) that is always live at high rate, regardless of which
# beamline or experiment is running. Pulse ids are one shared global counter
# for the whole facility, so this one small scalar channel's ingestion lag
# stands in for "how far behind is sf-databuffer's ingestion right now" for
# any BS channel -- without having to repeatedly re-query the (often much
# larger) channels actually being waited for.
DATABUFFER_NOW_CHANNEL = "SIN-CVME-TIFGUN-EVR0:RX-PULSEID"


def databuffer_lag(channel=DATABUFFER_NOW_CHANNEL, window_seconds=30):
    """Measure how far behind wall-clock "now" sf-databuffer's ingestion
    currently is, using the last `window_seconds` of `channel` (a fast,
    always-live scalar by default).

    Returns `(latest_pulse_id, latest_time, lag_seconds)`, where
    `latest_time` is a Unix timestamp (seconds). Raises `RuntimeError` if
    `channel` returned no data at all in the window -- it is then not
    actually a live channel, or the retrieval service is unreachable/stalled.
    """
    source = Daqbuf(backend=DEFAULT_HISTORY_BACKEND, time_type="sec")
    table = Table()
    source.add_listener(table)
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(seconds=window_seconds)
    source.request(
        dict(channels=[channel], start=datetime2str(start), end=datetime2str(end))
    )
    df = table.as_dataframe()
    if df is None or df.empty:
        raise RuntimeError(
            f"no data for {channel!r} in the last {window_seconds}s -- is it "
            "still a live channel?"
        )
    latest_pulse_id = int(df[channel].iloc[-1])
    latest_time = df.index[-1] / 1e9  # index is a ns timestamp
    return latest_pulse_id, latest_time, time.time() - latest_time


def wait_for_databuffer(
    target_time, timeout=60, poll_interval=2.0, channel=DATABUFFER_NOW_CHANNEL
):
    """Block until sf-databuffer's ingestion has caught up to `target_time`
    (a `time.time()`-style Unix timestamp), instead of blindly polling the
    actual data being waited for.

    Replaces a "sleep 1s, retry the real (possibly large) query, repeat up to
    N times" loop: a first, informed sleep covers the bulk of the lag
    estimated from a single cheap probe-channel query; after that it
    re-checks the same cheap probe every `poll_interval` seconds. Raises
    `TimeoutError` if ingestion has not caught up within `timeout` seconds.
    Returns the final measured lag (seconds) once caught up.
    """
    deadline = time.time() + timeout
    _, latest_time, lag = databuffer_lag(channel=channel)
    remaining = target_time - latest_time
    if remaining > 0:
        time.sleep(min(remaining + 1.0, timeout))
    while True:
        _, latest_time, lag = databuffer_lag(channel=channel)
        if latest_time >= target_time:
            return lag
        if time.time() >= deadline:
            raise TimeoutError(
                f"sf-databuffer has not ingested up to the requested time "
                f"after {timeout}s (still {target_time - latest_time:.1f}s behind)"
            )
        time.sleep(poll_interval)


def _is_recent(iso_end):
    end = dateutil.parser.parse(iso_end)
    if end.tzinfo is None:
        end = end.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) - end < RETRY_WINDOW


def datetime2str(datetime_date):
    return datetime_date.isoformat()


def local2utc(datetime_date):
    return datetime_date.replace(
        tzinfo=None,
    ).astimezone(
        tz=datetime.timezone.utc,
    )


def _resolve_range(start, end, kwargs):
    """Turn (start, end) into a pair of absolute datetimes. `end` defaults to
    now; `start` can be a datetime, an ISO string, a timedelta, a dict of
    timedelta kwargs, a number of seconds (all relative to `end`), or -
    if none of the above is given - is built from **kwargs (e.g. hours=1)."""
    if not end:
        end = datetime.datetime.now()
        if isinstance(start, datetime.timedelta):
            start = end + start
        elif isinstance(start, dict):
            start = end + datetime.timedelta(**start)
        elif isinstance(start, Number):
            start = end + datetime.timedelta(seconds=start)
        elif start is None:
            start = end + datetime.timedelta(**kwargs)
    if isinstance(start, str):
        start = dateutil.parser.parse(start)
    if isinstance(end, str):
        end = dateutil.parser.parse(end)
    return start, end


def _group_by_type(channels, force_type=None, channel_types=None):
    """Group `channels` by their channel type ("CA" or "BS"): either the same
    type for all of them (`force_type`) or individually (`channel_types`, a
    list of "CA"/"BS"/None running parallel to `channels`, as found e.g. in
    Alias entries - None falls back to "BS"). Returns {type: [channel, ...]}."""
    if force_type:
        if force_type not in HISTORY_BACKENDS:
            raise Exception(f"force_type must be one of {list(HISTORY_BACKENDS)}")
        return {force_type: list(channels)}
    groups = {}
    types = channel_types or []
    for i, channel in enumerate(channels):
        channel_type = types[i] if i < len(types) else None
        if channel_type not in HISTORY_BACKENDS:
            channel_type = "BS"
        groups.setdefault(channel_type, []).append(channel)
    return groups


def _glob_to_regex(pattern):
    """Convert a simple unix glob pattern ('*', '?') into a regex understood
    by the channel search endpoint. `fnmatch.translate` is not usable here:
    its output uses Python-specific syntax (e.g. `(?s:...)`, trailing `\\Z`)
    that the server's regex engine silently fails to match against."""
    escaped = re.escape(pattern)
    return escaped.replace(r"\*", ".*").replace(r"\?", ".")


def _show_figure(fig):
    """Display `fig` in whatever environment we are in. Desktop backends
    (Qt/Tk) get `plt.show`; the Jupyter ipympl/widget backend needs the canvas
    displayed explicitly (figures made inside a function are not auto-shown);
    the inline backend auto-displays figures created during the cell."""
    backend = plt.get_backend().lower()
    if any(k in backend for k in ("ipympl", "widget", "nbagg")):
        try:
            from IPython.display import display

            display(fig.canvas)
            return
        except Exception:
            pass
    elif "inline" in backend:
        return
    plt.show(block=False)


def _ensure_esc_h5(path):
    """Normalise `path` to an escape results filename (needs both `.esc` and
    `.h5` in its suffixes)."""
    from pathlib import Path

    p = Path(path)
    if ".esc" in p.suffixes and (".h5" in p.suffixes or ".zarr" in p.suffixes):
        return str(p)
    name = p.name
    for suffix in p.suffixes:
        name = name[: -len(suffix)]
    return str(p.with_name(name + ".esc.h5"))


def _ask_save_filename(default_name):
    """Ask the user for a save path, using a native file dialog when a GUI
    toolkit is available (Qt, then Tk) and falling back to a text prompt (which
    works in a terminal or a Jupyter notebook). Returns None if cancelled."""
    try:
        from matplotlib.backends.qt_compat import QtWidgets

        if QtWidgets.QApplication.instance() is not None:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                None, "Save strip recording", default_name, "escape (*.esc.h5)"
            )
            return path or None
    except Exception:
        pass
    try:
        import os

        if os.environ.get("DISPLAY"):
            import tkinter
            from tkinter import filedialog

            root = tkinter.Tk()
            root.withdraw()
            path = filedialog.asksaveasfilename(
                title="Save strip recording",
                initialfile=default_name,
                defaultextension=".esc.h5",
                filetypes=[("escape", "*.esc.h5"), ("all", "*")],
            )
            root.destroy()
            return path or None
    except Exception:
        pass
    try:
        path = input(f"Save strip recording to [{default_name}]: ").strip()
        return path or default_name
    except Exception:
        return None


def _plot_dataframe(data, channels, labels=None):
    # A fresh figure, rather than `plt.gca()`: reusing an axes that already
    # holds an unrelated, plain-numeric plot corrupts the shared data limits
    # once date data is added (matplotlib unions old and new ranges), which
    # silently blows up the visible x-range to spans of decades and leaves
    # the tick labels showing that bogus range's raw internal day-numbers
    # instead of the actual dates.
    _, ah = plt.subplots()
    if not labels:
        labels = channels
    plotted = False
    for chan, label in zip(channels, labels):
        if chan not in data:
            continue
        # Archiver "enum" channels come back as `Enum` objects (id +
        # description), not plain numbers; plot the numeric id. `data` itself
        # is left untouched (this column is a temporary copy).
        column = data[chan].map(lambda v: v.id if isinstance(v, Enum) else v)
        sel = ~column.isnull()
        if not any(sel):
            continue
        x = data.index[sel].to_pydatetime()
        y = column[sel]
        try:
            ah.step(x, y, ".-", label=label, where="post")
            plotted = True
        except (TypeError, ValueError) as e:
            _logger.warning("Skipping %r in the plot: not numeric (%s)", chan, e)
    if plotted:
        locator = mdates.AutoDateLocator()
        ah.xaxis.set_major_locator(locator)
        ah.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    plt.xticks(rotation=30)
    plt.legend()
    plt.tight_layout()
    plt.xlabel(data.index.name)
    ah.figure.tight_layout()
    _show_figure(ah.figure)


def _read_flushed_channel(flushed_file, channel):
    """Read back everything `_StripBuffer._flush_locked` has written so far
    for `channel` in `flushed_file`, as `(timestamps_ns, values)` numpy
    arrays - or `None` if that channel was never flushed (e.g. it never
    exceeded the in-memory buffer, or wasn't part of this recording)."""
    import numpy as np
    import h5py
    from escape import ArrayTimestamps

    with h5py.File(flushed_file, "r") as f:
        if channel not in f:
            return None
    placeholder = ArrayTimestamps(
        data=np.array([]),
        timestamps=np.array([], dtype=np.int64),
        timestamp_intervals=np.zeros((0, 2), dtype=np.int64),
        name=channel,
    )
    placeholder.set_h5_storage_file(flushed_file, "/", channel)
    return (
        np.asarray(placeholder.h5.timestamps),
        np.asarray(placeholder.h5.get_data_da().compute()),
    )


def _build_dataset(recorded, results_file=None, name="strip_recording", flushed_file=None):
    """Assemble raw `{channel: [(timestamp_sec, value), ...]}` samples (as
    returned by `_StripBuffer.recorded()` - the in-memory tail, i.e.
    whatever has arrived since the last auto-flush, or everything if there
    never was one) into an escape `DataSet`, one `ArrayTimestamps` per
    channel (timestamps in nanoseconds), merging in anything already
    auto-flushed to disk at `flushed_file` (see `_StripBuffer._flush_locked`)
    first. Pass `results_file` (an escape `.esc.h5` path) to also persist
    the *merged* result there - a fresh file/`ArrayTimestamps`, deliberately
    not the same object bound to `flushed_file`, so this works regardless of
    whether `results_file` is that same path, a different one, or auto-flush
    never happened at all.

    Split out of `_StripPlot.to_dataset` so `_StripPlotHandle` can build the
    same thing from raw samples fetched over the strip-plot subprocess's
    pipe, without needing an escape `DataSet` (which isn't reliably
    picklable) to cross the process boundary itself."""
    import numpy as np
    from escape import ArrayTimestamps, DataSet

    if results_file is not None:
        dataset = DataSet(results_file=results_file, mode="w", name=name)
    else:
        dataset = DataSet(name=name)
    channels = set(recorded)
    if flushed_file is not None:
        import h5py

        with h5py.File(flushed_file, "r") as f:
            channels |= set(f.keys())
    for channel in channels:
        ts_chunks, val_chunks = [], []
        flushed = _read_flushed_channel(flushed_file, channel) if flushed_file else None
        if flushed is not None:
            ts_chunks.append(flushed[0])
            val_chunks.append(flushed[1])
        samples = recorded.get(channel) or []
        if samples:
            ts_chunks.append(
                np.array([int(round(s[0] * 1e9)) for s in samples], dtype=np.int64)
            )
            val_chunks.append(np.asarray([s[1] for s in samples]))
        if not ts_chunks:
            continue
        timestamps = np.concatenate(ts_chunks)
        values = np.concatenate(val_chunks)
        order = np.argsort(timestamps)
        timestamps, values = timestamps[order], values[order]
        array = ArrayTimestamps(
            data=values,
            timestamps=timestamps,
            timestamp_intervals=np.array([[timestamps[0], timestamps[-1]]]),
            name=channel,
        )
        dataset.append(array, name=channel)
        if results_file is not None:
            array.store()
    return dataset


class _StripBuffer(Consumer):
    """Thread-safe buffer fed by the live sources' background threads. Always
    keeps the most recent `window` seconds for display; while `recording` is
    on it also accumulates the full, untrimmed history for `to_dataset`.

    A long and/or high-rate recording would otherwise grow `_record`
    unboundedly in memory. Instead, once available system RAM has dropped
    to `FLUSH_INITIAL_FRACTION` of what it was when recording started, the
    accumulated samples are written to an escape `.esc.h5` file (auto-named
    if `start_recording` wasn't given one) and cleared from memory; the same
    happens again every further `FLUSH_STEP_FRACTION` drop, so memory use
    stays bounded regardless of how long the recording runs. `to_dataset`
    (via `_build_dataset`) transparently merges whatever's on disk with
    whatever's still in memory, so nothing about the recording API changes -
    this is purely a memory-safety net, invisible unless it's needed."""

    FLUSH_INITIAL_FRACTION = 0.5
    FLUSH_STEP_FRACTION = 0.1
    FLUSH_CHECK_INTERVAL = 1.0  # seconds between available-RAM checks

    def __init__(self, window):
        super().__init__(timetype="sec")
        self.window = window
        self.recording = False
        self._data = {}
        self._record = {}
        self._lock = threading.Lock()
        self._results_file = None
        self._flushed = False
        self._mem_avail_at_start = None
        self._next_flush_avail = None
        self._last_mem_check = 0.0

    def on_channel_record(self, source, name, timestamp, pulse_id, value, **kwargs):
        if isinstance(value, Enum):
            value = value.id
        elif not isinstance(value, Number):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return  # skip non-scalar channels (arrays, strings, ...)
        with self._lock:
            self._data.setdefault(name, deque()).append((timestamp, value))
            if self.recording:
                self._record.setdefault(name, []).append((timestamp, value))
                self._maybe_flush_locked()

    def _maybe_flush_locked(self):
        """Call with `self._lock` held. Throttled (`FLUSH_CHECK_INTERVAL`)
        available-RAM check; flushes (see class docstring) once it's due."""
        if self._next_flush_avail is None:
            return  # psutil wasn't available at start_recording() time
        now = time.time()
        if now - self._last_mem_check < self.FLUSH_CHECK_INTERVAL:
            return
        self._last_mem_check = now
        try:
            import psutil

            available = psutil.virtual_memory().available
        except Exception:
            return
        # `_next_flush_avail <= 0` means the 10%-of-starting-availability
        # steps have already run out (an extreme/pathological recording) -
        # from there on, real available RAM (always > 0) could never be
        # "<=" a threshold that had simply kept counting down past zero, so
        # that comparison is skipped and every throttled check flushes
        # instead: the one regime where stopping would be exactly wrong.
        if 0 < self._next_flush_avail and available > self._next_flush_avail:
            return
        self._flush_locked()
        self._next_flush_avail = max(
            0.0, self._next_flush_avail - self._mem_avail_at_start * self.FLUSH_STEP_FRACTION
        )

    def _flush_locked(self):
        """Call with `self._lock` held. Writes every channel's currently
        accumulated samples to `self._results_file` (auto-naming it first if
        needed) via escape's incremental `ArrayH5File.append` - each flush
        becomes one more chunk in the file, not a rewrite of the whole thing
        - then clears them from memory."""
        import numpy as np
        from escape import ArrayTimestamps

        if self._results_file is None:
            self._results_file = datetime.datetime.now().strftime(
                "strip_%Y%m%d_%H%M%S_autoflush.esc.h5"
            )
            _logger.warning(
                "Strip plot recording is large enough that available RAM has "
                "dropped to about %.0f%% of its level when recording started; "
                "flushing accumulated samples to disk at %s (and will keep "
                "doing so) instead of holding them all in memory. "
                "`.to_dataset()`/`.dataset` still return the complete "
                "recording, merged from this file and memory.",
                100 * self._next_flush_avail / self._mem_avail_at_start,
                self._results_file,
            )
        for channel, samples in self._record.items():
            if not samples:
                continue
            timestamps = np.array(
                [int(round(s[0] * 1e9)) for s in samples], dtype=np.int64
            )
            values = np.asarray([s[1] for s in samples])
            array = ArrayTimestamps(
                data=values,
                timestamps=timestamps,
                timestamp_intervals=np.array([[timestamps[0], timestamps[-1]]]),
                name=channel,
            )
            array.set_h5_storage_file(self._results_file, "/", channel)
            # Not array.store(): it unconditionally passes a `lock` kwarg
            # that ArrayH5File.append (unlike ArrayH5Dataset.append) doesn't
            # accept.
            array.h5.append(array.data, array.timestamps, array.scan)
            samples.clear()
        self._flushed = True

    def snapshot(self):
        cutoff = time.time() - self.window
        out = {}
        with self._lock:
            for name, samples in self._data.items():
                while samples and samples[0][0] < cutoff:
                    samples.popleft()
                out[name] = ([s[0] for s in samples], [s[1] for s in samples])
        return out

    def start_recording(self, results_file=None):
        """`results_file`: where to auto-flush if/when RAM pressure requires
        it (see class docstring) - auto-named on first actual flush if not
        given. Also `.to_dataset(results_file=...)`'s final destination if
        that's later given a *different* path explicitly."""
        with self._lock:
            self._record = {}
            self.recording = True
            self._results_file = results_file
            self._flushed = False
            try:
                import psutil

                self._mem_avail_at_start = psutil.virtual_memory().available
            except Exception:
                self._mem_avail_at_start = None
            self._next_flush_avail = (
                self._mem_avail_at_start * self.FLUSH_INITIAL_FRACTION
                if self._mem_avail_at_start is not None
                else None
            )
            self._last_mem_check = 0.0

    def stop_recording(self):
        with self._lock:
            self.recording = False

    def recorded(self):
        with self._lock:
            return {name: list(samples) for name, samples in self._record.items()}

    @property
    def flushed_file(self):
        """Path anything of the current/last recording was auto-flushed to,
        or `None` if it all still fit comfortably in memory."""
        return self._results_file if self._flushed else None


class _StripPlot:
    """Handle for a running `DataHub.strip_plot`: a live, rolling matplotlib
    plot fed from live Dispatcher/EPICS sources. The plot redraws at a fixed
    rate (independent of the data rate), reading from a shared buffer.

    Closing the window (or calling `stop()`) stops the underlying monitors. A
    "Record" button captures the full history while enabled; on stop it is
    assembled into an escape `DataSet` (also via `to_dataset()`), exposed as
    `.dataset`. A "Save..." button writes it to a chosen escape `.esc.h5` file
    via a native file dialog (falling back to a text prompt in a notebook).
    Works with desktop (Qt/Tk) and Jupyter (ipympl) backends."""

    BUTTON_ROW_Y = 0.02
    BUTTON_ROW_HEIGHT = 0.042  # 0.035 * 1.2
    # Confirmed via actual get_window_extent() measurement (not just
    # tight_layout's estimate): the xlabel ("time") and the offset text
    # (e.g. "2026-Sep-03") share one row *below* the tick-label row, and that
    # combined stack needs ~0.09 of figure height below the axes' bottom
    # spine, essentially regardless of padding. A smaller value (0.06, tried
    # once) isn't just "a bit tight" - it pushes the xlabel down far enough
    # to render *behind* the opaque button row instead of merely close to
    # it, making it disappear rather than just look cramped. No
    # title/suptitle is used, so top can sit right at the figure edge.
    _CONTENT_MARGIN = 0.105
    _TOP_MARGIN = 0.98

    def __init__(
        self, sources, channels, labels, buffer, max_rate, duration, step=True, grid=True
    ):
        self._sources = [source for source, _ in sources]
        self._buffer = buffer
        self._stopped = False
        self.dataset = None
        self.fig, self.ax = plt.subplots()
        self.fig.subplots_adjust(
            top=self._TOP_MARGIN,
            bottom=self.BUTTON_ROW_Y + self.BUTTON_ROW_HEIGHT + self._CONTENT_MARGIN,
        )
        self._lines = {}
        # A monitored EPICS/bsread value genuinely holds constant between
        # samples - it doesn't linearly interpolate towards the next one -
        # so a step plot (each point the *start*/left edge of its horizontal
        # segment, i.e. drawstyle "steps-post") represents the real signal
        # more faithfully than the default straight-line connection.
        drawstyle = "steps-post" if step else "default"
        for channel, label in zip(channels, labels):
            (line,) = self.ax.plot([], [], ".-", drawstyle=drawstyle, label=label)
            self._lines[channel] = line
        self.ax.grid(grid)
        locator = mdates.AutoDateLocator()
        self.ax.xaxis.set_major_locator(locator)
        self.ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        self.ax.set_xlabel("time")
        self.ax.legend(loc="upper left")
        # One row of low buttons rather than the previous two tall ones -
        # Record (captures the full history into an escape DataSet), Save...
        # (writes it to a chosen .esc.h5 file), Autoscale X/Y (re-enables
        # autoscaling on just that axis after a toolbar zoom disabled it -
        # zooming a rectangle sets explicit x/ylim on both at once), and
        # Window x2/:2 (doubles/halves how much history the buffer keeps and
        # the plot shows, `_StripBuffer.window`).
        buttons = [
            ("Record", self._toggle_record),
            ("Save...", self._save),
            ("Autoscale X", self._autoscale_x),
            ("Autoscale Y", self._autoscale_y),
            ("Window ×2", self._double_window),
            ("Window /2", self._half_window),
        ]
        left, right, gap = 0.02, 0.98, 0.01
        width = (right - left - gap * (len(buttons) - 1)) / len(buttons)
        self._buttons = []
        for i, (label, callback) in enumerate(buttons):
            btn_ax = self.fig.add_axes(
                [left + i * (width + gap), self.BUTTON_ROW_Y, width, self.BUTTON_ROW_HEIGHT]
            )
            button = Button(btn_ax, label)
            button.on_clicked(callback)
            self._buttons.append(button)
        self._record_button = self._buttons[0]  # _toggle_record relabels it
        # Redraw on a timer at max_rate Hz - decoupled from the data rate, so a
        # 100 Hz beam-synchronous channel still only repaints max_rate times/s.
        interval = max(1, int(round(1000.0 / max_rate)))
        self._anim = FuncAnimation(
            self.fig,
            self._update,
            interval=interval,
            blit=False,
            cache_frame_data=False,
        )
        # Closing the window tears down the monitors, off the GUI thread so the
        # window closes without waiting on the sources.
        self.fig.canvas.mpl_connect(
            "close_event",
            lambda event: threading.Thread(target=self.stop, daemon=True).start(),
        )
        for source, group_channels in sources:
            source.request(
                dict(channels=group_channels, start=0.0, end=float(duration)),
                background=True,
            )
        _show_figure(self.fig)

    def _update(self, frame):
        snapshot = self._buffer.snapshot()
        for name, line in self._lines.items():
            timestamps, values = snapshot.get(name, ([], []))
            if timestamps:
                xs = mdates.date2num(
                    [datetime.datetime.fromtimestamp(t) for t in timestamps]
                )
                line.set_data(xs, values)
        self.ax.relim()
        self.ax.autoscale_view()
        return list(self._lines.values())

    def _toggle_record(self, event):
        if self._buffer.recording:
            self._buffer.stop_recording()
            self._record_button.label.set_text("Record")
            # Guard the escape/DataSet build: a failure here must not break the
            # button or lose the recording (still on the handle's buffer).
            try:
                self.dataset = self.to_dataset()
                _logger.info(
                    "Strip plot recording stopped: %d channel(s) captured "
                    "(available as the handle's `.dataset`)",
                    len(self.dataset.datasets),
                )
            except Exception:
                _logger.exception(
                    "Could not build the escape DataSet from the recording; "
                    "the raw samples are still available via the handle."
                )
        else:
            self._buffer.start_recording()
            self._record_button.label.set_text("Stop")
            _logger.info("Strip plot recording started")
        self.fig.canvas.draw_idle()

    def _save(self, event):
        if not self._buffer.recorded() and not self._buffer.flushed_file:
            _logger.warning("Nothing recorded yet - press Record first.")
            return
        default = datetime.datetime.now().strftime("strip_%Y%m%d_%H%M%S.esc.h5")
        path = _ask_save_filename(default)
        if not path:
            return
        path = _ensure_esc_h5(path)
        try:
            self.dataset = self.to_dataset(results_file=path)
            _logger.info("Strip recording saved to %s", path)
        except Exception:
            _logger.exception("Failed to save strip recording to %s", path)

    def _autoscale_x(self, event):
        self.ax.autoscale(enable=True, axis="x")
        self._redraw_now()

    def _autoscale_y(self, event):
        self.ax.autoscale(enable=True, axis="y")
        self._redraw_now()

    def _redraw_now(self):
        # _update() (the animation timer) applies this next tick regardless -
        # this just makes the button feel immediate rather than waiting up to
        # 1/max_rate seconds for it.
        self.ax.relim()
        self.ax.autoscale_view()
        self.fig.canvas.draw_idle()

    def _double_window(self, event):
        self._buffer.window *= 2

    def _half_window(self, event):
        self._buffer.window = max(1.0, self._buffer.window / 2)

    def record(self, results_file=None):
        """Start accumulating the full history for `to_dataset`/`.dataset`.
        `results_file`: see `_StripBuffer.start_recording` - only matters if
        the recording is long/fast enough to need auto-flushing to disk;
        left as `None` it's auto-named only if/when that actually happens."""
        self._buffer.start_recording(results_file=results_file)

    def is_recording(self):
        return self._buffer.recording

    def to_dataset(self, results_file=None, name="strip_recording"):
        """Assemble everything recorded so far into an escape `DataSet`, one
        `ArrayTimestamps` per channel (timestamps in nanoseconds) - merging
        in anything already auto-flushed to disk, transparently. Pass
        `results_file` (an escape `.esc.h5` path) to also persist it."""
        return _build_dataset(
            self._buffer.recorded(),
            results_file,
            name,
            flushed_file=self._buffer.flushed_file,
        )

    def is_running(self):
        return any(source.is_running() for source in self._sources)

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        try:
            self._anim.event_source.stop()
        except Exception:
            pass
        # Signal each source to stop and let its own thread tear itself down.
        # Calling `source.close()` from here would destroy the bsread zmq
        # context from the wrong thread and deadlock; instead we abort and
        # join, then close only once the thread has actually exited (by which
        # point it has already released its context safely).
        for source in self._sources:
            source.abort()
        for source in self._sources:
            source.join(timeout=5)
            if not source.is_thread_running():
                source.close()


def _strip_plot_ipc_loop(conn, buffer):
    """Service `_StripPlotHandle` requests (record/is_recording/get_recorded)
    for as long as `conn` stays open. Runs on its own thread in the strip-plot
    subprocess so it neither depends on nor blocks the Qt main loop; `buffer`
    (a `_StripBuffer`) is already internally lock-protected, so calling its
    methods from here concurrently with the GUI thread (which also reads/
    drives it, e.g. via the window's own Record button) is safe."""
    while True:
        try:
            if not conn.poll(1):
                continue
            cmd = conn.recv()
        except (EOFError, OSError):
            return
        try:
            name = cmd[0] if isinstance(cmd, tuple) else cmd
            if name == "record":
                results_file = cmd[1] if isinstance(cmd, tuple) else None
                buffer.start_recording(results_file=results_file)
                conn.send(("ok", None))
            elif name == "is_recording":
                conn.send(("ok", buffer.recording))
            elif name == "get_recorded":
                conn.send(("ok", (buffer.recorded(), buffer.flushed_file)))
            elif name == "stop_recording":
                buffer.stop_recording()
                conn.send(("ok", (buffer.recorded(), buffer.flushed_file)))
            else:
                conn.send(("error", f"unknown command {cmd!r}"))
        except Exception as exc:
            try:
                conn.send(("error", str(exc)))
            except Exception:
                return


def _strip_plot_subprocess_main(
    channels,
    force_type,
    channel_types,
    window,
    max_rate,
    duration,
    labels,
    step,
    grid,
    conn,
):
    """Entry point for the strip-plot subprocess (see `DataHub.strip_plot`).
    Builds the same sources/buffer/window `DataHub.strip_plot` used to build
    directly, then blocks until the window is closed.

    Runs in its own process, deliberately: matplotlib's Qt/Tk event loop is
    only pumped by IPython's GUI-integration hook between prompts, so a plot
    living in the caller's own process would freeze for the duration of any
    single blocking statement (e.g. a synchronous motor move) and only catch
    up once control returned to the prompt. A dedicated process has nothing
    else to do but run that event loop, so it keeps redrawing regardless of
    what the launching session is doing.

    `conn` is this end of the pipe back to the `_StripPlotHandle` in the
    launching process - serviced on its own thread (`_strip_plot_ipc_loop`)
    so `.record()`/`.to_dataset()` etc. work from there too, alongside the
    window's own Record/Save buttons.
    """
    groups = _group_by_type(channels, force_type, channel_types)
    buffer = _StripBuffer(window)
    sources = []
    for channel_type, group_channels in groups.items():
        source = LIVE_SOURCES[channel_type](time_type="sec")
        source.add_listener(buffer)
        sources.append((source, group_channels))
    threading.Thread(
        target=_strip_plot_ipc_loop, args=(conn, buffer), daemon=True
    ).start()
    # Held in `plot` (not discarded) so its FuncAnimation isn't
    # garbage-collected out from under it before plt.show() blocks.
    plot = _StripPlot(
        sources, channels, labels, buffer, max_rate, duration, step=step, grid=grid
    )
    plt.show()  # blocks (across all backends) until the window is closed


def _strip_plot_subprocess_bootstrap(fd):
    """Real entry point of the strip-plot subprocess, invoked as
    ``python -c "import eco.dbase.archiver as m; m._strip_plot_subprocess_bootstrap(<fd>)"``
    by `DataHub.strip_plot` - deliberately plain `subprocess.Popen`, not
    `multiprocessing.Process`.

    `multiprocessing`'s spawn start method unconditionally re-executes
    `sys.modules['__main__'].__file__` from the *launching* process inside
    the child (via `runpy.run_path`, so that `if __name__ == "__main__":`-
    guarded scripts behave correctly across the fork/spawn boundary). An eco
    session's `__main__` *is* `eco/startup_inline.py` (it becomes `__main__`
    via IPython's `%run -m eco.startup_inline ...` at session start - see
    `eco_cli.py`), which does an unguarded `argparse.parse_args()` at module
    level. `multiprocessing.Process` therefore silently re-ran that parser
    in every strip-plot child, against the *session's* raw launch argv
    (`--profile=eco --no-banner -i -c run -m eco.startup_inline -l -s
    bernina`) - which it naturally doesn't recognize - and the child died on
    that `SystemExit` before ever reaching the actual plot. Plain
    `subprocess.Popen` with this explicit command never touches `__main__`
    reconstruction at all, so it doesn't trip over this.

    `fd` is the inherited end of the duplex pipe `DataHub.strip_plot` made
    with `multiprocessing.Pipe()` (still fine to use for the `Connection`
    object itself - only automatic *process spawning* was the problem).
    The plot's arguments arrive as the first message over it.
    """
    from multiprocessing.connection import Connection

    conn = Connection(fd)
    (
        channels,
        force_type,
        channel_types,
        window,
        max_rate,
        duration,
        labels,
        step,
        grid,
    ) = conn.recv()
    _strip_plot_subprocess_main(
        channels,
        force_type,
        channel_types,
        window,
        max_rate,
        duration,
        labels,
        step,
        grid,
        conn,
    )


class _StripPlotHandle:
    """Parent-side handle for a strip plot running in its own subprocess (see
    `DataHub.strip_plot`). `.record()`/`.stop_recording()`/`.is_recording()`/
    `.to_dataset()` talk to the `_StripBuffer` living in that subprocess over
    a pipe (serviced by `_strip_plot_ipc_loop`); the window's own Record/Save
    buttons work the same as before, independently. `.stop()` ends the plot
    and closes the window."""

    def __init__(self, process, conn):
        self._process = process  # a subprocess.Popen, not multiprocessing.Process
        self._conn = conn
        self.dataset = None

    def is_running(self):
        return self._process.poll() is None

    def stop(self):
        """End the plot and close its window."""
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)

    def _call(self, cmd, timeout=10):
        if self._process.poll() is not None:
            raise RuntimeError("strip plot subprocess is no longer running")
        self._conn.send(cmd)
        if not self._conn.poll(timeout):
            raise TimeoutError(
                f"strip plot subprocess did not respond to {cmd!r} within {timeout}s"
            )
        status, payload = self._conn.recv()
        if status == "error":
            raise RuntimeError(f"strip plot subprocess: {payload}")
        return payload

    def record(self, results_file=None):
        """Start accumulating the full history for `to_dataset`/`.dataset`.
        `results_file`: see `_StripBuffer.start_recording` - only matters if
        the recording is long/fast enough to need auto-flushing to disk;
        left as `None` it's auto-named only if/when that actually happens."""
        self._call(("record", results_file))

    def is_recording(self):
        return self._call("is_recording")

    def stop_recording(self, name="strip_recording"):
        """Stop recording and assemble everything captured into `.dataset`."""
        recorded, flushed_file = self._call("stop_recording")
        self.dataset = _build_dataset(recorded, name=name, flushed_file=flushed_file)
        return self.dataset

    def to_dataset(self, results_file=None, name="strip_recording"):
        """Assemble everything recorded so far (recording may still be
        running) into an escape `DataSet`, one `ArrayTimestamps` per channel -
        merging in anything already auto-flushed to disk, transparently.
        Pass `results_file` (an escape `.esc.h5` path) to also persist it."""
        recorded, flushed_file = self._call("get_recorded")
        self.dataset = _build_dataset(
            recorded, results_file, name, flushed_file=flushed_file
        )
        return self.dataset


class DataHub(Assembly):
    """
    Access to historical and live channel data via the PSI `datahub` package.

    - Historical data: `get_data_time_range` / `get_data_pulse_id_range`,
      covering both "BS" (beam-synchronous, fast, DataBuffer) and "CA"
      (EPICS Archiver Appliance, slow) channels.
    - Live data: `get_live_data` captures a fixed duration straight from the
      bsread Dispatcher ("BS") or EPICS ("CA"); `strip_plot` opens a live,
      rolling plot fed from the same sources.

    Retrieving "CA" historical data requires the `cbor2` package to be
    installed - without it, the underlying Daqbuf JSON fallback fails on
    channels that have no pulse id associated (e.g. `pip install cbor2`).
    """

    def __init__(self, pv_pulse_id=None, name=None, add_to_cnf=False):
        super().__init__(name=name)
        if pv_pulse_id:
            self._append(DetectorPvDataStream, pv_pulse_id, name="pulse_id")
        if add_to_cnf:
            ecocnf.archiver = self
        self._history_sources = {}

    def _history_source(self, backend):
        if backend not in self._history_sources:
            source = Daqbuf(backend=backend, time_type="sec")
            if not source.cbor:
                _logger.warning(
                    "cbor2 is not installed: historical queries on backend "
                    "'%s' may fail for channels without pulse ids "
                    "(pip install cbor2 to fix)",
                    backend,
                )
            self._history_sources[backend] = source
        return self._history_sources[backend]

    def _live_source(self, force_type):
        force_type = force_type or "BS"
        try:
            cls = LIVE_SOURCES[force_type]
        except KeyError:
            raise Exception(f"force_type must be one of {list(LIVE_SOURCES)}")
        return cls(time_type="sec")

    def get_data(self, channels, start, end, force_type=None, channel_types=None):
        groups = _group_by_type(channels, force_type, channel_types)
        table = Table()
        for channel_type, group_channels in groups.items():
            backend = HISTORY_BACKENDS[channel_type]
            source = self._history_source(backend)
            # DataBuffer ("BS") data is effectively continuous, so an empty
            # result for a query ending near "now" is almost always a
            # transient retrieval hiccup and worth retrying. Archiver ("CA")
            # data is event-driven - "no data in this window" is a normal,
            # frequent outcome there (the channel just hasn't changed) and is
            # not retried.
            retry = backend == DEFAULT_HISTORY_BACKEND and _is_recent(end)
            self._request_into(source, group_channels, start, end, table, retry)
        return table.as_dataframe()

    def _request_into(self, source, channels, start, end, table, retry):
        """Request `channels` on `source`, feeding `table`. Channels that are
        just empty (no error) are retried up to `retry`'s policy. A batch
        request that errors out - e.g. a component's ".EGU"/status/enum
        sub-channel that was never archived, alongside others that were - is
        retried one channel at a time instead, so that one bad channel is
        logged and skipped rather than losing the whole group's data."""
        attempts = RETRY_COUNT if retry else 1
        for attempt in range(attempts):
            source.add_listener(table)
            try:
                source.request(dict(channels=channels, start=start, end=end))
            except Exception as e:
                source.remove_listeners()
                if len(channels) == 1:
                    _logger.warning(
                        "Skipping %r: could not retrieve historical data (%s)",
                        channels[0],
                        e,
                    )
                else:
                    for channel in channels:
                        self._request_into(source, [channel], start, end, table, retry)
                return
            source.remove_listeners()
            if any(ch in table.data for ch in channels):
                return
            if attempt < attempts - 1:
                time.sleep(RETRY_DELAY)

    def get_data_time_range(
        self,
        channels=[],
        start=None,
        end=None,
        plot=False,
        force_type=None,
        channel_types=None,
        labels=None,
        convert_timezone=False,
        **kwargs,
    ):
        """Retrieve historical data for `channels` between `start` and `end`
        (datetimes, ISO date strings, or - for `start` - a timedelta/dict of
        timedelta kwargs/number of seconds relative to `end`). `end` defaults
        to now; if `start` is not given either, it is derived from **kwargs
        (e.g. `hours=1`).

        `force_type` ("CA" or "BS") selects the backend for all channels;
        `channel_types` instead gives the type of each channel individually
        (a list running parallel to `channels`, as found e.g. in Alias
        entries).
        """
        start, end = _resolve_range(start, end, kwargs)

        data = self.get_data(
            channels,
            start=datetime2str(local2utc(start)),
            end=datetime2str(local2utc(end)),
            force_type=force_type,
            channel_types=channel_types,
        )
        if data is not None:
            data.index = pd.to_datetime(data.index, unit="s", utc=True)
            data.index.name = "timestamp"
            if convert_timezone:
                data.index = data.index.tz_convert("Europe/Zurich")
        if plot and data is not None:
            _plot_dataframe(data, channels, labels)
        return data

    def get_data_pulse_id_range(
        self,
        channels=[],
        start=None,
        end=None,
        plot=False,
        force_type=None,
        channel_types=None,
        convert_timezone=False,
        labels=None,
    ):
        """Retrieve historical data for `channels` between pulse ids `start`
        and `end`. If `end` is not given, it defaults to the current pulse id
        (from this DataHub's `pulse_id` channel) and `start` is taken as an
        offset relative to it (e.g. `start=-1000` for the last 1000 pulses).

        Pulse id ranges are converted to a time range before querying, since
        the retrieval service does not support ranged pulse id queries.
        Meaningful only for "BS" (beam-synchronous) channels.
        """
        if not end:
            if hasattr(self, "pulse_id"):
                # pyepics' PV.get() returns None on a timed-out get instead of
                # raising, so a plain int() here died with an opaque
                # "int() argument must be ... not 'NoneType'" whenever channel
                # access was congested -- same failure that used to end scans
                # in Daq.stop(); see Daq.get_pulse_id.
                current = self.pulse_id.get_current_value()
                if current is None:
                    raise Exception(
                        "could not read the current pulse id (channel access "
                        "timed out); pass an explicit `end` pulse id"
                    )
                end = int(current)
            else:
                raise Exception("no end pulse id provided")
            start = start + end

        start_sec = pulse_id_to_time(start)
        end_sec = pulse_id_to_time(end)
        data = self.get_data(
            channels,
            start=datetime2str(datetime.datetime.utcfromtimestamp(start_sec)) + "Z",
            end=datetime2str(datetime.datetime.utcfromtimestamp(end_sec)) + "Z",
            force_type=force_type,
            channel_types=channel_types,
        )
        if data is not None:
            data.index = pd.to_datetime(data.index, unit="s", utc=True)
            data.index.name = "timestamp"
            if convert_timezone:
                data.index = data.index.tz_convert("Europe/Zurich")
        if plot and data is not None:
            _plot_dataframe(data, channels, labels)
        return data

    def get_live_data(
        self,
        channels=[],
        duration=10,
        force_type=None,
        plot=False,
        labels=None,
        convert_timezone=False,
    ):
        """Capture `duration` seconds of live data straight from the source:
        the bsread Dispatcher for beam-synchronous channels (force_type="BS",
        default) or directly from EPICS for slow channels (force_type="CA").
        Unlike historical data, all channels in one call must be of the same
        type.
        """
        source = self._live_source(force_type)
        table = Table()
        source.add_listener(table)
        try:
            source.request(dict(channels=channels, start=0.0, end=float(duration)))
        finally:
            source.close()
        data = table.as_dataframe()
        if data is not None:
            data.index = pd.to_datetime(data.index, unit="s", utc=True)
            data.index.name = "timestamp"
            if convert_timezone:
                data.index = data.index.tz_convert("Europe/Zurich")
        if plot and data is not None:
            _plot_dataframe(data, channels, labels)
        return data

    def strip_plot(
        self,
        channels=[],
        force_type=None,
        channel_types=None,
        window=60,
        max_rate=5,
        duration=24 * 3600,
        labels=None,
        step=True,
        grid=True,
    ):
        """Open a live, rolling strip plot for `channels`, streamed from the
        bsread Dispatcher ("BS" channels) and/or directly from EPICS ("CA"
        channels) - mixed types in one plot are fine, unlike `get_live_data`.
        `force_type`/`channel_types` select the source per channel exactly as
        for the historical methods.

        `window` is the number of seconds of history shown; `max_rate` caps the
        redraw rate (Hz, independent of the data rate); `duration` is how long
        the underlying stream stays open. `step` (default `True`) draws each
        channel as a step plot - a sample holds constant (a horizontal line)
        from its own point until the next one, rather than linearly
        interpolating towards it - which is how a monitored value actually
        behaves; pass `False` for the plain connect-the-dots line. `grid`
        (default `True`) shows axis gridlines.

        Runs in its own subprocess, so the plot keeps redrawing live even
        while this session is blocked on something else (e.g. a synchronous
        motor move) - matplotlib's Qt/Tk event loop would otherwise only get
        pumped between prompts. Returns a handle: `.stop()` ends the plot and
        closes the window (as does closing it directly); `.record()` /
        `.stop_recording()` / `.is_recording()` / `.to_dataset()` mirror the
        window's own "Record"/"Save..." buttons, over a pipe to the
        subprocess, and populate the handle's `.dataset`.
        """
        if labels is None:
            labels = channels
        # subprocess.Popen with an explicit command, deliberately not
        # multiprocessing.Process - see _strip_plot_subprocess_bootstrap's
        # docstring for why the latter breaks under this session's IPython
        # %run-based startup.
        parent_conn, child_conn = multiprocessing.Pipe()
        child_fd = child_conn.fileno()
        bootstrap = (
            "import ctypes, signal;"
            " ctypes.CDLL('libc.so.6').prctl(1, signal.SIGTERM);"
            " import eco.dbase.archiver as m;"
            f" m._strip_plot_subprocess_bootstrap({child_fd})"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", bootstrap], pass_fds=(child_fd,)
        )
        child_conn.close()  # this end now belongs to the subprocess only
        parent_conn.send(
            (
                channels,
                force_type,
                channel_types,
                window,
                max_rate,
                duration,
                labels,
                step,
                grid,
            )
        )
        return _StripPlotHandle(process, parent_conn)

    def search(self, searchstring, backend=None):
        """Search channel names using a simple unix glob expression (e.g.
        '*ARES*'). `backend` restricts the search to "sf-databuffer" (BS) or
        "sf-archiver" (CA); by default all backends are searched."""
        return Daqbuf(backend=backend).search(_glob_to_regex(searchstring))
