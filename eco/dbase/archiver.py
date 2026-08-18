import datetime
import dateutil.parser
import logging
import re
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
from ..epics.detector import DetectorPvDataStream

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


class _StripBuffer(Consumer):
    """Thread-safe buffer fed by the live sources' background threads. Always
    keeps the most recent `window` seconds for display; while `recording` is
    on it also accumulates the full, untrimmed history for `to_dataset`."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.recording = False
        self._data = {}
        self._record = {}
        self._lock = threading.Lock()

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

    def snapshot(self):
        cutoff = time.time() - self.window
        out = {}
        with self._lock:
            for name, samples in self._data.items():
                while samples and samples[0][0] < cutoff:
                    samples.popleft()
                out[name] = ([s[0] for s in samples], [s[1] for s in samples])
        return out

    def start_recording(self):
        with self._lock:
            self._record = {}
            self.recording = True

    def stop_recording(self):
        with self._lock:
            self.recording = False

    def recorded(self):
        with self._lock:
            return {name: list(samples) for name, samples in self._record.items()}


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

    def __init__(self, sources, channels, labels, buffer, max_rate, duration):
        self._sources = [source for source, _ in sources]
        self._buffer = buffer
        self._stopped = False
        self.dataset = None
        self.fig, self.ax = plt.subplots()
        self.fig.subplots_adjust(bottom=0.18)
        self._lines = {}
        for channel, label in zip(channels, labels):
            (line,) = self.ax.plot([], [], ".-", label=label)
            self._lines[channel] = line
        locator = mdates.AutoDateLocator()
        self.ax.xaxis.set_major_locator(locator)
        self.ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        self.ax.set_xlabel("time")
        self.ax.legend(loc="upper left")
        # Record button (captures the full history into an escape DataSet) and
        # Save button (writes the recording to a chosen escape .esc.h5 file).
        self._record_button_ax = self.fig.add_axes([0.02, 0.02, 0.18, 0.075])
        self._record_button = Button(self._record_button_ax, "Record")
        self._record_button.on_clicked(self._toggle_record)
        self._save_button_ax = self.fig.add_axes([0.21, 0.02, 0.18, 0.075])
        self._save_button = Button(self._save_button_ax, "Save...")
        self._save_button.on_clicked(self._save)
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
        if not self._buffer.recorded():
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

    def record(self):
        """Start accumulating the full history for `to_dataset`/`.dataset`."""
        self._buffer.start_recording()

    def is_recording(self):
        return self._buffer.recording

    def to_dataset(self, results_file=None, name="strip_recording"):
        """Assemble everything recorded so far into an escape `DataSet`, one
        `ArrayTimestamps` per channel (timestamps in nanoseconds). Pass
        `results_file` (an escape `.esc.h5` path) to also persist it."""
        import numpy as np
        from escape import ArrayTimestamps, DataSet

        recorded = self._buffer.recorded()
        if results_file is not None:
            dataset = DataSet(results_file=results_file, mode="w", name=name)
        else:
            dataset = DataSet(name=name)
        for channel, samples in recorded.items():
            if not samples:
                continue
            timestamps = np.array(
                [int(round(s[0] * 1e9)) for s in samples], dtype=np.int64
            )
            values = np.asarray([s[1] for s in samples])
            intervals = np.array([[timestamps[0], timestamps[-1]]])
            array = ArrayTimestamps(
                data=values,
                timestamps=timestamps,
                timestamp_intervals=intervals,
                name=channel,
            )
            dataset.append(array, name=channel)
            if results_file is not None:
                array.store()
        return dataset

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
                end = int(self.pulse_id.get_current_value())
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
    ):
        """Open a live, rolling strip plot for `channels`, streamed from the
        bsread Dispatcher ("BS" channels) and/or directly from EPICS ("CA"
        channels) - mixed types in one plot are fine, unlike `get_live_data`.
        `force_type`/`channel_types` select the source per channel exactly as
        for the historical methods.

        `window` is the number of seconds of history shown; `max_rate` caps the
        redraw rate (Hz, independent of the data rate); `duration` is how long
        the underlying stream stays open.

        Returns a handle: `.stop()` ends the plot and its monitors (as does
        closing the window). The window's "Record" button (or `.record()`)
        captures the full, untrimmed history; stopping the recording assembles
        it into an escape `DataSet` on `.dataset`, also reachable via
        `.to_dataset(results_file=...)`. Keep the handle referenced so the
        animation is not garbage-collected.
        """
        if labels is None:
            labels = channels
        groups = _group_by_type(channels, force_type, channel_types)
        buffer = _StripBuffer(window)
        sources = []
        for channel_type, group_channels in groups.items():
            source = self._live_source(channel_type)
            source.add_listener(buffer)
            sources.append((source, group_channels))
        return _StripPlot(sources, channels, labels, buffer, max_rate, duration)

    def search(self, searchstring, backend=None):
        """Search channel names using a simple unix glob expression (e.g.
        '*ARES*'). `backend` restricts the search to "sf-databuffer" (BS) or
        "sf-archiver" (CA); by default all backends are searched."""
        return Daqbuf(backend=backend).search(_glob_to_regex(searchstring))
