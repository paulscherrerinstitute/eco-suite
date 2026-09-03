"""Generic, polling-based live strip plot for arbitrary monitorables - any
object satisfying `eco.elements.protocols.Detector` (i.e. exposing
`get_current_value()`), not just raw EPICS/bsread channels. Can also mix in
plain channel-id strings, streamed the same way as
`eco.dbase.archiver.DataHub.strip_plot`.

Reuses that module's plotting machinery (`_StripBuffer`/`_StripPlot`/the
subprocess-and-pipe architecture/the Record-Save-Autoscale-Window button
row) wholesale - only the *data acquisition* differs: an arbitrary
monitorable can't be handed to the strip-plot subprocess the way a channel
name can (`DataHub.strip_plot`'s subprocess independently reconnects to
EPICS/bsread using just the channel string; a live device object wraps
things - open sockets, pyepics PVs, threads - that only make sense bound to
*this* process and generally aren't even picklable). So here the *launching*
process keeps the real objects and polls `get_current_value()` on its own
background thread (deliberately not the main/IPython thread, so polling
keeps going even while that thread is blocked in something like a
synchronous motor move), pushing `(name, timestamp, value)` samples to the
subprocess over a second pipe; the subprocess just feeds them into the same
`_StripBuffer` a datahub `Consumer` callback would.
"""

import multiprocessing
import subprocess
import sys
import threading
import time

from matplotlib import pyplot as plt

from ..elements.protocols import resolve_lazy
from ..dbase.archiver import (
    LIVE_SOURCES,
    _group_by_type,
    _StripBuffer,
    _StripPlot,
    _StripPlotHandle,
    _strip_plot_ipc_loop,
)


def _monitorable_name(obj):
    """This object's own name, preferring its alias (if it has one - see
    e.g. `eco.epics_utils.get_from_archive`'s `_archiver_channels`) over its
    plain `.name`, over a bare `repr()` as a last resort."""
    alias = getattr(obj, "alias", None)
    if alias is not None:
        try:
            return alias.get_full_name()
        except Exception:
            pass
    return getattr(obj, "name", None) or repr(obj)


def _poll_and_push(names, monitorables, poll_interval, data_conn, stop_event):
    """Runs in the *launching* process, on its own thread: reads
    `get_current_value()` off each of `monitorables` every `poll_interval`
    seconds and pushes `(name, timestamp, value)` to the subprocess over
    `data_conn`. Silently skips a monitorable for one tick on a read error
    (transient EPICS hiccups shouldn't kill the whole plot) and exits
    cleanly once the subprocess side of the pipe is gone."""
    resolved = [resolve_lazy(m) for m in monitorables]
    while not stop_event.is_set():
        tick_start = time.time()
        for name, obj in zip(names, resolved):
            try:
                value = obj.get_current_value()
            except Exception:
                continue
            if not isinstance(value, (int, float)):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            try:
                data_conn.send((name, time.time(), value))
            except (BrokenPipeError, OSError):
                return
        stop_event.wait(max(0.0, poll_interval - (time.time() - tick_start)))


def _strip_plot_data_feed_loop(data_conn, buffer):
    """Runs in the strip-plot subprocess, on its own thread: reads samples
    pushed by `_poll_and_push` (in the launching process) and feeds them
    into `buffer` exactly as a datahub `Consumer` callback would."""
    while True:
        try:
            name, timestamp, value = data_conn.recv()
        except (EOFError, OSError):
            return
        buffer.on_channel_record(None, name, timestamp, None, value)


def _strip_plot_subprocess_main(
    channels,
    polled_names,
    labels,
    force_type,
    channel_types,
    window,
    max_rate,
    duration,
    step,
    grid,
    cmd_conn,
    data_conn,
):
    """Entry point for the strip-plot subprocess - see `strip_plot` and this
    module's docstring for why it's a subprocess and why polled monitorables
    arrive over `data_conn` rather than being reconstructed here."""
    buffer = _StripBuffer(window)
    sources = []
    if channels:
        groups = _group_by_type(channels, force_type, channel_types)
        for channel_type, group_channels in groups.items():
            source = LIVE_SOURCES[channel_type](time_type="sec")
            source.add_listener(buffer)
            sources.append((source, group_channels))
    threading.Thread(
        target=_strip_plot_ipc_loop, args=(cmd_conn, buffer), daemon=True
    ).start()
    threading.Thread(
        target=_strip_plot_data_feed_loop, args=(data_conn, buffer), daemon=True
    ).start()
    all_names = list(channels) + list(polled_names)
    # Held in `plot` (not discarded) so its FuncAnimation isn't
    # garbage-collected out from under it before plt.show() blocks.
    plot = _StripPlot(
        sources, all_names, labels, buffer, max_rate, duration, step=step, grid=grid
    )
    plt.show()  # blocks (across all backends) until the window is closed


def _strip_plot_subprocess_bootstrap(cmd_fd, data_fd):
    """Invoked as ``python -c "import eco.utilities.strip_plot as m;
    m._strip_plot_subprocess_bootstrap(<cmd_fd>, <data_fd>)"`` by
    `strip_plot` - deliberately plain `subprocess.Popen`, not
    `multiprocessing.Process`; see
    `eco.dbase.archiver._strip_plot_subprocess_bootstrap`'s docstring for
    why (it re-executes this session's `eco/startup_inline.py` and dies on
    its `argparse.parse_args()` otherwise)."""
    from multiprocessing.connection import Connection

    cmd_conn = Connection(cmd_fd)
    data_conn = Connection(data_fd)
    (
        channels,
        polled_names,
        labels,
        force_type,
        channel_types,
        window,
        max_rate,
        duration,
        step,
        grid,
    ) = cmd_conn.recv()
    _strip_plot_subprocess_main(
        channels,
        polled_names,
        labels,
        force_type,
        channel_types,
        window,
        max_rate,
        duration,
        step,
        grid,
        cmd_conn,
        data_conn,
    )


class _PollingStripPlotHandle(_StripPlotHandle):
    """`_StripPlotHandle` plus tearing down the launching process's own
    polling thread on `.stop()` (closing the window without going through
    the handle - clicking its X - still needs no extra teardown here: the
    poll thread's next `data_conn.send()` just hits a closed pipe and
    returns on its own)."""

    def __init__(self, process, cmd_conn, poll_thread, stop_event):
        super().__init__(process, cmd_conn)
        self._poll_thread = poll_thread
        self._poll_stop_event = stop_event

    def stop(self):
        self._poll_stop_event.set()
        super().stop()
        self._poll_thread.join(timeout=5)


def strip_plot(
    *monitorables_args,
    monitorables=(),
    channels=(),
    force_type=None,
    channel_types=None,
    labels=None,
    window=60,
    max_rate=5,
    duration=24 * 3600,
    poll_interval=0.2,
    step=True,
    grid=True,
):
    """Open a live, rolling strip plot mixing arbitrary monitorables (any
    objects satisfying `eco.elements.protocols.Detector`, e.g. Adjustables/
    Detectors - polled on a background thread in this process, so this
    works for anything with `get_current_value()`, EPICS-backed or not) -
    passed either positionally (`strip_plot(det1, det2, ...)`, named from
    each one's own alias if it has one, else `.name`, else `repr()` - see
    `_monitorable_name`) or via `monitorables=[...]` (same thing, just
    explicit) - with `channels` (raw EPICS/bsread channel-id strings,
    streamed live the same way as `eco.dbase.archiver.DataHub.strip_plot` -
    `force_type`/`channel_types` select their source exactly as there).

    `labels` (default: each monitorable's derived name, then each channel
    string as-is), if given, must align with
    `list(monitorables_args) + list(monitorables) + list(channels)` in that
    order. `window`/`max_rate`/`duration`/`step`/`grid` are as in
    `DataHub.strip_plot`; `poll_interval` is the monitorables' polling
    period in seconds.

    Runs in its own subprocess for the same reason `DataHub.strip_plot`
    does: matplotlib's Qt/Tk event loop is only pumped by IPython's GUI-
    integration hook between prompts, so a plot living in this process would
    freeze for the duration of any single blocking statement (e.g. a
    synchronous motor move). Returns a handle: `.stop()` ends the plot,
    closes the window, and stops the polling thread (as does closing the
    window directly - polling then stops on its own once the pipe breaks);
    `.record()` / `.stop_recording()` / `.is_recording()` / `.to_dataset()`
    mirror the window's own "Record"/"Save..." buttons and populate the
    handle's `.dataset`.
    """
    monitorables = list(monitorables_args) + list(monitorables)
    channels = list(channels)
    polled_names = [_monitorable_name(m) for m in monitorables]
    if labels is None:
        labels = polled_names + channels

    cmd_parent, cmd_child = multiprocessing.Pipe()
    data_parent, data_child = multiprocessing.Pipe()
    cmd_fd, data_fd = cmd_child.fileno(), data_child.fileno()
    bootstrap = (
        "import ctypes, signal;"
        " ctypes.CDLL('libc.so.6').prctl(1, signal.SIGTERM);"
        " import eco.utilities.strip_plot as m;"
        f" m._strip_plot_subprocess_bootstrap({cmd_fd}, {data_fd})"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", bootstrap], pass_fds=(cmd_fd, data_fd)
    )
    cmd_child.close()  # these two ends now belong to the subprocess only
    data_child.close()
    cmd_parent.send(
        (
            channels,
            polled_names,
            labels,
            force_type,
            channel_types,
            window,
            max_rate,
            duration,
            step,
            grid,
        )
    )

    stop_event = threading.Event()
    poll_thread = threading.Thread(
        target=_poll_and_push,
        args=(polled_names, monitorables, poll_interval, data_parent, stop_event),
        daemon=True,
    )
    poll_thread.start()
    return _PollingStripPlotHandle(process, cmd_parent, poll_thread, stop_event)
