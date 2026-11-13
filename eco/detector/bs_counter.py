"""A StepScan "counter" backed directly by one or more live bs
(beam-synchronous / escape) streams, instead of an EPICS PV monitor.

Started as a proof of concept, exercised in ``tests/test_bs_counter_mockup.py``
against ``escape.stream``'s synthetic local test stream (no real beamline
needed); used by ``Stream.scans``, ``DetectorBsStream(bs_scan_mode=True)``
and adaptive scans.

Where the state lives
---------------------
Like every other counter, a ``BsStreamCounter`` keeps per-scan data on the
scan (a ``_ScanBins`` in ``scan.counter_scratch(name)["bins"]``: bins, step
keys, pulse ids, live plot, arrays, file), and on itself only the channel
subscriptions and its settings. So one counter -- e.g. the one a detector
keeps -- can be passed to any number of scans, also concurrent ones, and
nothing is binned between scans. ``last_arrays``, ``stored_filename``,
``step_pulse_ids`` and ``grid()`` read the most recent scan's bins
(``scan_bins(scan)`` any other's).

Two questions this answers
---------------------------
1. Does ``StepScan``/``eco/acquisition/scan.py`` need new machinery to
   support a bs-stream-backed counter? No -- ``Counter``
   (``eco/acquisition/counter_protocol.py``) is duck-typed on purpose, and
   its "simple mode" (``acquire(scan, Npulses)`` -> something with
   ``.wait()``) and "start/stop mode" already fit this exactly, unchanged.

2. Does ``escape.stream``'s live histogram/binning machinery
   (``Stream.digitize().categorize()``, and the more general
   ``Scan(parameters=[...])`` it's built on) still work, and can it drive
   per-scan-step binning the way ``escape.Array``'s scan-index binning does
   for post-hoc data? Yes -- verified directly against a live stream (fixed
   value-range bins, ``t.digitize(bins).categorize(i)``, produced a correct
   multi-bin histogram). For step-scan binning, ``Scan`` with a synthetic
   "current step index" parameter (rather than a real bs channel) is the
   right primitive -- see below.

The ``Scan`` open-bin bug (found here, fixed upstream in escape-fel 0.2.7)
----------------------------------------------------------------------------
``escape.stream.Scan(parameters=[...])`` with no ``values=`` starts empty
and grows one bin at a time, the first time a genuinely new parameter value
is seen (``Scan._append()``). Each ``Stream`` wrapping it gets its own
``DataManager``. Before escape-fel 0.2.7, a ``DataManager``'s per-bin deque
list was only grown when *that* ``DataManager``'s own call to
``scan._append()`` reported a new bin -- so if a *second* channel sharing
the same growing ``Scan`` reached a given step only after some other
channel had already registered it, the second channel's own list was never
grown to match, and its next append indexed past the end of its own list.
That raised ``IndexError`` inside an ``EventWorker`` callback, silently
swallowed by ``EventWorker.eventLoop``'s blanket ``try/except`` -- so it
never surfaced as an exception, just endless
``"EventWorker callback error: list index out of range"`` console spam,
while the affected channel silently stopped recording data for the rest of
the scan. Confirmed directly (multiple independently-built ``Stream``s
sharing one dynamically-growing ``Scan``, reaching each new bin at
different times), reported upstream, and fixed in escape-fel 0.2.7
(``DataManager.append`` now grows its own buffers to cover whatever index
it needs, rather than trusting the shared ``doappend`` flag). Verified the
fix directly against the installed 0.2.7: the same reproduction that used
to lose a channel's data now records correctly, with the shared ``Scan``
left genuinely open-ended -- no pre-declared bin count needed, which is why
``BsStreamCounter`` below no longer computes ``n_steps`` from the scan
object the way an earlier version of this file did as a workaround.

Bin assignment: step index, not pulse-id math
-----------------------------------------------
The bin key fed into ``Scan`` is a small mutable ``_StepIndexSource``
object (satisfies the same ``_getEventData()``/``.name`` shape
``escape.stream`` expects of a real channel, without being one) whose
``.value`` this class sets to the current step number on
``start()``/``acquire()``. A *fixed-width pulse-id window* per step
(``[start_id, start_id + Npulses)``, computed once for the whole scan up
front, mirroring the ``digitize(edges)`` pattern) was considered and
rejected: unlike a real facility repetition-rate signal, StepScan's step
boundaries do not fall at fixed pulse-id intervals -- move/settle time
varies per step -- so a pre-baked pulse-id schedule would silently
misattribute shots during a slow step. Recording the *live* pulse_id at
the moment each step actually starts (``self.step_pulse_ids``, mirroring
``Daq.start()``'s ``get_pulse_id(newer_than=...)``) is kept anyway, purely
as a diagnostic/traceability record -- it does not drive the binning.

What changed testing against the real dispatcher (not the synthetic test
stream): console noise, and a real teardown/rebuild bug
--------------------------------------------------------------------------
escape-fel has since moved to a ``datahub``-backed default event handler
(``DataHubEventHandler(backend="bsread")``, wrapping PSI's ``psi-datahub``
package) instead of the plain-bsread ``EventHandler_SFEL`` this was first
tested against -- ``EventWorker()`` with no explicit ``eventHandler`` now
picks the datahub one whenever ``datahub`` is importable. That's a real
behavior change, not just internal refactor: this class's own
teardown/rebuild pattern used to call ``Stream.accumulate(True)``/``(False)``
(which round-trips through ``EventWorker.registerSource``/``removeSource``)
on **every scan start and end** -- fine against the synthetic
``LocalEventHandler``, but against the real dispatcher/datahub handler this:

1. produced a lot of console output, because ``registerSource`` on that
   handler unconditionally schedules a debounced restart of the *entire
   shared* connection (``EventWorker._schedule_restart``) on every call,
   regardless of whether the channel was already subscribed -- so every
   scan start/end paid for a full connection teardown+rebuild rather than a
   no-op;
2. was confirmed, on a real run against ``SAROP21-PBPS133:INTENSITY``, to
   occasionally race with the datahub-backed handler's own background
   receive thread mid-teardown (``zmq.error.ZMQError: Socket operation on
   non-socket`` from a ``do_run`` thread reading a socket the restart had
   just closed underneath it) -- a real bug, not just noise, though not
   this module's own bug to fix (see below).

Fixed here by decoupling the two lifecycles that were conflated: **channel
subscription** (``registerSource``, done once per channel in ``__init__``,
never repeated) from **per-scan bin structure** (a fresh ``Scan`` built per scan in
``_ScanBins``, which only manipulates local ``eventCallbacks``
bookkeeping -- no ``accumulate()``, no ``registerSource``/``removeSource``
at all after ``__init__``). Confirmed directly: two consecutive real
``ascan()`` runs against the real dispatcher, with a real ``DummyAdjustable``,
now produce no extra console output and no teardown-race exception, with
correct per-step values and pulse_id partitioning both times.

Recommended upstream (not done here -- this file only avoids triggering
it): ``EventWorker.registerSource``/``removeSource`` should skip
``_schedule_restart()`` when the channel is already in (or already absent
from) ``source_ids`` -- i.e. only restart when the *subscribed channel set
actually changes* -- which would make calling ``accumulate(True)`` on an
already-accumulating Stream, or ``(False)`` on an already-stopped one, a
safe no-op instead of a full connection cycle. That would also close the
observed race at its root (no restart, nothing to race with the datahub
handler's own stream teardown), independent of any caller working around
it the way this module now does.
"""

from datetime import datetime
from pathlib import Path
import threading
from threading import Event
from time import time as _time

import numpy as np

from eco.acquisition import peak_analysis as _peak
from eco.acquisition.utilities import Acquisition, mean_sem
from eco.utilities import mainthread
from eco.aliases import Alias


class _StepIndexSource:
    """Mutable stand-in for a real bs channel, used only as the ``Scan``
    binning key: its "current value" is whatever step is presently open,
    set directly by ``BsStreamCounter`` rather than derived from any
    stream data.

    ``BsStreamCounter`` uses one of these per grid dimension (just one,
    named "step_index", for an ordinary linear scan) -- ``Scan`` already
    keys bins on the *tuple* of however many parameters it's given, so N
    of these give N-D (grid) binning for free; see
    ``BsStreamCounter._advance_step`` and ``escape.stream.Grid``.

    Also duck-types ``.unit`` (unused by ``Scan`` itself, but real
    ``escape.stream`` code assumes any scan parameter has one --
    e.g. ``escape.stream.plots.Plot.plot()``'s axis auto-labeling, used by
    ``BsStreamCounter``'s ``live_plot``).
    """

    def __init__(self, name="step_index"):
        self.name = name
        self.unit = "step"
        self.value = 0.0

    def _getEventData(self):
        return self.value


_scan_in_scan_units_class = None


def _scan_in_scan_units(escape_stream):
    """``escape_stream.Scan`` subclass whose single parameter reads as the
    scan's own values (motor positions) instead of the bin key.

    The bins stay keyed on the step index (see ``_StepIndexSource`` and the
    module docstring for why), but everything escape draws or derives from
    ``scan[parameter]`` -- ``Stream.plot_med`` and its peak overlay, and
    ``Stream.to_array``'s default parameter -- then speaks in the units of
    what is being scanned. ``step_values`` maps step index -> value and is
    filled by ``BsStreamCounter._advance_step``, *before* the bin key moves to
    that step, so the mapping is there by the time the bin exists; a step
    without an entry reads as its index, as before.

    Built on first use rather than at import, like every other
    ``escape.stream`` import in this module.
    """
    global _scan_in_scan_units_class
    if _scan_in_scan_units_class is None:

        class ScanInScanUnits(escape_stream.Scan):
            def __init__(self, *args, step_values=None, **kwargs):
                super().__init__(*args, **kwargs)
                self._step_values = {} if step_values is None else step_values

            def __getitem__(self, item):
                values = super().__getitem__(item)
                names = self._parameterNames or []
                if len(names) == 1 and len(values) and (item == 0 or item == names[0]):
                    return np.asarray(
                        [self._step_values.get(int(round(s)), s) for s in values],
                        dtype=float,
                    )
                return values

            def copy(self):
                return ScanInScanUnits(
                    parameters=self._parameters,
                    values=list(self._values),
                    precision=self._precision,
                    step_values=self._step_values,
                )

        _scan_in_scan_units_class = ScanInScanUnits
    return _scan_in_scan_units_class


def _get_grid_specs(scan):
    """Return *scan*'s ``grid_specs`` dict (``{"shape", "positions",
    "index_plan", "grid_dimension_names"}``, see
    ``eco.acquisition.scan.Scans.meshscan``) if it has one and it's set,
    else ``None`` -- covers both a plain scan (no ``grid_specs`` attribute
    at all) and an ordinary non-grid scan (``grid_specs`` present but
    ``None``) the same way."""
    if scan is None or not hasattr(scan, "grid_specs"):
        return None
    try:
        return scan.grid_specs.get_current_value()
    except Exception:
        return None


def _resolve_stream(source, eventworker=None):
    """Accept a bs channel name, a live ``escape.stream.Stream``, or an
    ``eco.detector.detectors_psi.DetectorBsStream`` (whose already-built
    ``.stream`` is reused directly -- no second subscription), and return
    the underlying ``Stream``. Duck-typed rather than an ``isinstance``
    check against the concrete ``DetectorBsStream`` class: any object
    exposing a ``.stream`` that is itself an ``escape.stream.Stream`` is
    accepted, so a from-scratch device class (e.g. a throwaway dev/test
    detector) works exactly like ``DetectorBsStream`` here without having
    to subclass it or be registered anywhere."""
    from escape import stream as escape_stream

    if isinstance(source, escape_stream.Stream):
        return source
    if isinstance(source, str):
        if eventworker is None:
            from eco.detector.detectors_psi import _ensure_bs_event_worker

            eventworker = _ensure_bs_event_worker()
        return escape_stream.Stream(source, eventworker)
    inner = getattr(source, "stream", None)
    if isinstance(inner, escape_stream.Stream):
        return inner
    raise TypeError(
        "Expected a bs channel name, an escape.stream.Stream, or an object "
        f"with a .stream attribute that is one, got {type(source)}"
    )


def bs_scannable(Obj):
    """Class decorator giving a bs-stream-backed detector a lazy ``.scans``
    container powered by ``BsStreamCounter`` -- the bs-stream analogue of
    ``eco.acquisition.decorators.scannable`` (which wires up ``CounterValue``,
    i.e. EPICS-monitor/polling-based scanning, instead). Useful for a
    from-scratch throwaway bs-stream detector class; ``DetectorBsStream``
    itself (``eco.detector.detectors_psi``) doesn't use this decorator --
    it needs the choice between the two per *instance*, not per class (see
    its ``bs_scan_mode`` constructor flag), which a class decorator can't
    express.

    Built once and cached (unlike ``scannable``, which deliberately rebuilds
    its ``CounterValue`` on every access to reset accumulated data): a
    ``BsStreamCounter`` holds a live bs subscription, so discarding and
    rebuilding it on every ``.scans`` access would leak one subscription
    (and its dispatcher-restart cost) per access instead of reusing one.
    """
    Obj.scans = _make_scans_property(lambda self: self.alias.get_full_name())
    return Obj


def _make_scans_property(get_name):
    """Shared body behind ``bs_scannable``'s ``.scans`` and
    ``install_stream_scans()``'s patched ``Stream.scans`` -- both just wrap
    ``self`` in a cached ``BsStreamCounter``/``Scans`` pair, differing only
    in how they name the counter (``get_name(self)``)."""

    @property
    def scans(self):
        from eco.acquisition import scan  # avoid circular import

        if not hasattr(self, "_bs_counter"):
            self._bs_counter = BsStreamCounter(self, name=get_name(self))
        if not hasattr(self, "_scans"):
            self._scans = scan.Scans(default_counters=[self._bs_counter])
            self._scans._set_owner(self)  # its scans' resume command
        else:
            self._scans._default_counters = [self._bs_counter]
            self._scans._augment_docstrings()
        return self._scans

    return scans


_stream_scans_installed = False


def install_stream_scans():
    """Monkey-patch ``escape.stream.Stream`` with a lazy ``.scans``
    property (same mechanism as ``bs_scannable``), so *any* ``Stream``
    instance anywhere in eco -- built through ``DetectorBsStream``,
    directly via ``escape.stream.Stream(...)``, or produced by Stream
    arithmetic (``a / b``, ``.element(0)``, ...) -- can be scanned with
    ``some_stream.scans.ascan(...)``/``.meshscan(...)`` immediately, with
    no wrapping needed.

    Idempotent (checked both here and by the ``hasattr`` guards inside the
    property itself) -- safe to call from multiple entry points; see
    ``eco.detector.detectors_psi._ensure_bs_event_worker()``, which calls
    this on the very first ``DetectorBsStream`` construction in a session
    (the earliest reliable "this session touches bs streams" signal), and
    this module's own import, below.
    """
    global _stream_scans_installed
    if _stream_scans_installed:
        return
    from escape.stream import Stream as EscapeStream

    EscapeStream.scans = _make_scans_property(lambda self: self.name)
    _stream_scans_installed = True


install_stream_scans()


# escape keeps 1000 samples per bin by default; a step may have more shots
MAX_SHOTS_PER_STEP = 1_000_000


class _ScanBins:
    """Everything of one scan (or of one standalone read) of a
    ``BsStreamCounter``: the per-step bins, the step keys, the pulse ids, the
    live plot, the arrays and the file.

    Kept on the scan (``scan.counter_scratch(counter.name)["bins"]``), like
    every other counter's per-scan data, not on the counter: the counter
    keeps only the channel subscriptions and its settings, so one counter
    (e.g. the one a ``DetectorBsStream`` keeps) serves any number of scans,
    also at the same time, and nothing is binned between scans.

    Binning runs only while a step collects: ``resume()`` (when a step
    starts, after its key is set) adds the callbacks on the shared event
    worker -- never ``registerSource``, see ``BsStreamCounter`` and the
    module docstring -- and ``pause()`` (once the step's shots are in)
    removes them, so nothing lands in a bin between steps, e.g. during a
    move. ``close()`` ends it for good; the data stay readable afterwards.
    """

    def __init__(self, raw, grid_specs=None, step_name=None, step_unit=None):
        from escape import stream as escape_stream

        # One source per grid dimension -- just one ("step_index") for an
        # ordinary linear scan. Scan() keys bins on the tuple of however many
        # of these there are, so this is the entire mechanism N-D (grid)
        # binning needs -- see escape.stream.Grid, which reads this same
        # tuple structure back out of self.scan._values.
        ndim = len(grid_specs["shape"]) if grid_specs else 1
        if ndim <= 1:
            self.step_sources = [_StepIndexSource("step_index")]
            # before the Scan below is built: it takes the parameter names
            # from its sources at construction
            if step_name is not None:
                self.step_sources[0].name = step_name
                self.step_sources[0].unit = step_unit
        else:
            self.step_sources = [_StepIndexSource(f"step_index_{d}") for d in range(ndim)]
        self.grid_specs = grid_specs
        # step index -> the scan's own value at that step (_scan_in_scan_units)
        self.step_values = {}
        # Open-ended (values=None): bins are created on demand as new step
        # indices appear. Safe to share across every channel regardless of
        # which one's data reaches a given step first -- see the module
        # docstring (escape-fel 0.2.7, DataManager.append).
        if ndim <= 1:
            self.scan = _scan_in_scan_units(escape_stream)(
                parameters=list(self.step_sources), step_values=self.step_values
            )
        else:
            self.scan = escape_stream.Scan(parameters=list(self.step_sources))
        self.new_data = Event()
        self.step_pulse_ids = {}
        self.pending = None  # (step_index, n0) between start() and stop()
        self.last_samples = None
        self.plot = None
        self.arrays = {}
        self.stored_filename = None
        self.running = True
        self.binning = False
        self.channels = {}
        for name, base in raw.items():
            self.channels[name] = escape_stream.Stream(
                source=base._source, scan=self.scan, maxlen=MAX_SHOTS_PER_STEP
            )

    @property
    def ndim(self):
        return len(self.step_sources)

    def resume(self):
        """Bin from now on (into whatever step the keys point at)."""
        if self.binning or not self.running:
            return
        self.binning = True
        for s in self.channels.values():
            ew = s._source.eventWorker
            ew.eventCallbacks.append(s._appendEventData)
            ew.eventCallbacks.append(self.new_data.set)

    def pause(self):
        """Stop binning (remove the event callbacks, never the channel
        subscription). The bins keep what they collected."""
        if not self.binning:
            return
        self.binning = False
        for s in self.channels.values():
            ew = s._source.eventWorker
            for callback in (s._appendEventData, self.new_data.set):
                try:
                    ew.eventCallbacks.remove(callback)
                except ValueError:
                    pass

    def close(self):
        self.pause()
        self.running = False

    def trim(self, idx, n0=None, pulse_range=None, n=None):
        """Keep in bin `idx` of every channel only the shots of the step:
        those after the first ``n0[name]``, within `pulse_range` (first, last
        pulse id, inclusive), at most the first `n` -- what was reduced, and
        so what is stored, plotted and analysed."""
        for name, s in self.channels.items():
            dm = s._dataManager
            with dm._lock:
                if idx >= len(dm._data):
                    continue
                start = (n0 or {}).get(name, 0)
                pairs = list(zip(dm._data[idx], dm._eventIds[idx]))[start:]
                if pulse_range is not None:
                    first, last = pulse_range
                    pairs = [(v, i) for v, i in pairs if i is not None and first <= i <= last]
                if n is not None:
                    pairs = pairs[:n]
                dm._data[idx].clear()
                dm._eventIds[idx].clear()
                for v, i in pairs:
                    dm._data[idx].append(v)
                    dm._eventIds[idx].append(i)
                if hasattr(dm, "_counts") and idx < len(dm._counts):
                    dm._counts[idx] = len(pairs)

    # Bins are created on demand (open-ended Scan) the first time a matching
    # event actually arrives for a given channel, so `idx` may not exist yet
    # in a freshly-advanced-to step -- treat that as "no samples yet", not
    # an error.
    def bin(self, name, idx):
        data = self.channels[name]._dataManager._data
        return data[idx] if idx < len(data) else []

    def eventids(self, name, idx):
        eventids = self.channels[name]._dataManager._eventIds
        return eventids[idx] if idx < len(eventids) else []


class BsStreamCounter:
    """Scalar Detector/Counter reading one or more bs channels from live
    ``escape.stream`` subscriptions, with per-scan-step histogram binning.

    Parameters
    ----------
    sources : str | escape.stream.Stream | DetectorBsStream | list of these
        One or more channels to read together, sharing the same per-step
        bin boundaries.
    name : str, optional
    reduction : callable, default ``numpy.mean``
        Applied to the list of raw samples collected for a step (or, in
        standalone/no-scan use, for ``get_current_value()``).
    timeout : float, default 10
        Seconds ``acquire()`` will wait for enough new samples to arrive
        in the current step's bin before raising ``TimeoutError``.
    eventworker : escape.stream.EventWorker, optional
        Defaults to the module-global worker shared by ``DetectorBsStream``
        for any *name*-string sources; ignored for sources that already
        carry their own (a ``Stream`` or ``DetectorBsStream``).
    live_plot : bool, default True
        Automatically open (and keep live-updating) a plot for a 1-D scan
        -- ``plot_med()`` of the scan's binned stream (median + percentile bands
        + peak overlay, see ``escape.stream.Stream.plot_med``), reusing
        the exact same already-accumulating per-step Stream this counter
        uses internally, so this costs nothing beyond drawing. Only for a
        single-channel, single-dimension (non-grid) counter -- silently
        skipped otherwise (a grid counter has no 1-D "vs. scan variable"
        plot to draw; use ``.grid().plot()`` instead once the scan is a
        real mesh/grid scan). This is what a plain
        ``BsStreamCounter`` (including one obtained through
        ``bs_scannable``/the patched ``Stream.scans``, see
        ``install_stream_scans()``) was missing compared to the older
        EPICS-monitor-based ``CounterValue``, which always auto-plots.
    store : bool, default True
        Automatically convert each channel's just-finished bins to a real
        ``escape.Array`` and write them to a fresh ``.esc.h5`` file when a
        scan ends (see ``_build_arrays``/``_store_arrays``) -- the
        bs-stream analogue of ``CounterValue.create_arrays``/
        ``store_arrays``, which do this unconditionally for the older
        EPICS-monitor path. This was the other thing a plain
        ``BsStreamCounter`` was missing compared to ``CounterValue``:
        without it, a bs-stream scan's data lived only in this counter's
        in-memory bins (``last_value(s)``, ``.grid()``), with nothing on
        disk. Failure to store (any exception -- a bad
        directory, a concurrent writer, ...) is printed, never raised, so
        it can't fail the scan itself; see ``_store_arrays``.
    storage_dir : str | Path | callable, default "auto"
        Where to write the ``.esc.h5`` file -- forwarded to
        ``_store_arrays`` the same way ``CounterValue.store_arrays``'s own
        ``directory`` argument works (``"auto"`` -> current directory,
        matching ``eco.acquisition.counters.DEFAULT_STORAGE_DIR``; a
        callable is called with no arguments to get the path). Ignored if
        ``store`` is ``False``.
    peak_analysis : True, False or dict, default True
        After every step of a 1-D scan (and at its end), run escape's
        ``find_peak`` on the per-step medians against the scan variable's own
        values, and keep the result in ``scan.peak_analysis`` -- see
        ``eco.acquisition.peak_analysis`` for the fields and for what is not
        analysed (n-d/grid scans, several channels). A dict passes
        ``n_bg``, ``bg_model``, ``fixed_offset`` and/or ``mode`` on to
        ``find_peak``. The overlay of the live plot (``live_plot``) is
        escape's own and keeps its own settings (the Peak-params panel), so
        with non-default settings here the two can differ.

    Usage
    -----
    Outside a scan, each channel's ``get_current_value()``-equivalent is
    ``.last_values['<channel>']`` after calling ``.acquire().wait()``, or
    read live via ``.get_current_value()`` (single channel only). Bins
    exist only during such a read.

    As a StepScan counter (``eco.acquisition.counter_protocol.Counter``),
    pass it in ``scan.counters``: ``callbacks_start_scan`` builds the scan's
    bins (on the scan), ``callbacks_end_scan`` stops them binning, and
    ``acquire(scan, Npulses)`` / ``start(scan)``+``stop(scan)`` wait for
    and reduce that step's bin, across all channels at once.
    """

    def __init__(
        self, sources, name=None, reduction=np.mean, timeout=10, eventworker=None,
        live_plot=True, store=True, storage_dir="auto", peak_analysis=True,
    ):
        sources = sources if isinstance(sources, (list, tuple)) else [sources]
        self._raw = {}
        for src in sources:
            s = _resolve_stream(src, eventworker)
            self._raw[s.name] = s

        self.name = name or "+".join(self._raw)
        self.reduction = reduction
        self.timeout = timeout
        self.live_plot = live_plot
        self.store = store
        self.storage_dir = storage_dir
        self._peak_settings = _peak.parse_settings(peak_analysis)
        self.alias = Alias(self.name, channel=list(self._raw), channeltype="BS")

        # Per-scan state lives on the scans (_ScanBins); the counter only
        # points at the most recent scan's, for grid()/last_arrays/... after
        # the fact, and remembers which bins still bin, for close().
        self._last_scan_bins = None
        self._open_bins = set()
        self._standalone_bins = None  # between a scan-less start() and stop()
        # Convenience copies of the most recent step, whatever scan it was in.
        self.last_value = None
        self.last_values = {}
        self.last_pulse_ids = {}
        self.last_samples = None

        # Subscribe each channel with the transport exactly once, here, for
        # the life of this counter -- see the module docstring for why this
        # must not repeat on every scan.
        for base in self._raw.values():
            base._source.eventWorker.registerSource(base._source.name)

        # following the scan's leading counter: start()/stop() around its
        # acquisition (see stop()), no exposure of its own
        self.follows = True
        self.exposure_doc = "number of bs shots per step"
        self.exposure_keyword = "Npulses"

        self.callbacks_start_scan = [self._on_scan_start]
        self.callbacks_start_step = []
        self.callbacks_step_counting = []
        self.callbacks_end_step = [self._update_peak_analysis]
        self.callbacks_end_scan = [self._on_scan_end]
        # a scan resumed after it ended unfinished (StepScan.scan_all again)
        self.callbacks_resume_scan = [self._on_scan_resume]

    # -- the most recent scan's results ---------------------------------------
    @property
    def last_arrays(self):
        """``{channel_name: escape.Array}`` of the most recently finished scan
        (see ``_build_arrays``), {} before the first one."""
        return self._last_scan_bins.arrays if self._last_scan_bins else {}

    @property
    def stored_filename(self):
        """The file the most recent scan's arrays went to, or None."""
        return self._last_scan_bins.stored_filename if self._last_scan_bins else None

    @property
    def step_pulse_ids(self):
        """{step index: pulse id at the step's start} of the most recent scan
        -- a diagnostic record only, it does not drive the binning."""
        return self._last_scan_bins.step_pulse_ids if self._last_scan_bins else {}

    # -- where a scan's bins are -----------------------------------------------
    def _scratch(self, scan):
        counter_scratch = getattr(scan, "counter_scratch", None)
        if counter_scratch is not None:
            return counter_scratch(self.name)
        # a scan object without counter_scratch: the same dict, by hand
        return vars(scan).setdefault("counter_state", {}).setdefault(self.name, {})

    def scan_bins(self, scan=None):
        """The ``_ScanBins`` of *scan* (None if this counter has none there),
        or of the most recent scan."""
        if scan is None:
            return self._last_scan_bins
        return self._scratch(scan).get("bins")

    def _open(self, scan=None):
        """New bins: for *scan* (stored on it), or standalone (scan=None)."""
        grid_specs = _get_grid_specs(scan)
        step_name, step_unit = (None, None) if grid_specs else self._step_label(scan)
        bins = _ScanBins(self._raw, grid_specs, step_name, step_unit)
        self._open_bins.add(bins)
        if scan is not None:
            self._scratch(scan)["bins"] = bins
            self._last_scan_bins = bins
        return bins

    def _close(self, bins):
        bins.close()
        self._open_bins.discard(bins)

    def _bins_for(self, scan):
        # a scan whose start callbacks this counter missed still gets bins
        return self.scan_bins(scan) or self._open(scan)

    def _on_scan_start(self, scan=None, **kwargs):
        bins = self._open(scan)
        self._start_live_plot(bins)
        # n-d scans, several channels, or another analysing counter: no-op
        _peak.attach(scan, self.name, self._peak_settings, n_channels=len(self._raw))

    def _on_scan_resume(self, scan=None, **kwargs):
        """Bin again into the same bins, and restart their plot's redraw,
        both stopped by `_on_scan_end` when the scan ended unfinished."""
        bins = self.scan_bins(scan) if scan is not None else None
        if bins is None:
            return
        bins.running = True
        self._open_bins.add(bins)
        if bins.plot is not None:
            self._restart_live_plot(bins.plot)

    @mainthread.on_main  # the redraw timer lives on the main thread
    def _restart_live_plot(self, plot):
        try:
            plot.start()
        except Exception as exc:
            print(f"{self.name}: couldn't restart the live plot: {exc}")

    def _step_label(self, scan):
        """(name, unit) of the scanned adjustable for the (single) step
        source, so escape's axis label (``"<name> / <unit>"``, see
        ``Plot.plot``) reads e.g. "motor / mm" rather than "step_index / step".
        (None, None) for a scan object that does not say what it scans."""
        scan_parameters = (getattr(scan, "scan_info", None) or {}).get("scan_parameters")
        names = (scan_parameters or {}).get("name") or []
        if len(names) == 1:
            return str(names[0]), _peak.adjustable_unit(scan)
        return None, None

    def _scan_value_at(self, scan):
        """The scanned adjustable's target value for the step now being
        acquired, or None if *scan* does not say (``values_current_step`` is
        set by ``StepScan.do_next_step`` before the move)."""
        try:
            return float(scan.values_current_step[0])
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def _update_peak_analysis(self, scan=None, **kwargs):
        """Re-run the peak/step analysis on the steps finished so far: the
        per-step medians of the (single) channel against the scan variable's
        own values -- in scan units, unlike the step-index axis the bins are
        keyed on. Result in ``scan.peak_analysis``, see
        ``eco.acquisition.peak_analysis``. Never raises."""
        analysis = _peak.get_attached(scan, self.name)
        bins = self.scan_bins(scan) if scan is not None else None
        if analysis is None or bins is None or len(bins.channels) != 1:
            return analysis
        try:
            (channel,) = bins.channels.values()
            data = list(channel._dataManager._data)
            # one key per bin, in creation order; a step that produced no
            # sample never got a bin, so the key -- not the position -- says
            # which step a bin is
            keys = list(bins.scan._values)
            positions = _peak.scan_positions(scan)
            n = min(len(data), len(keys))
            x = np.full(n, np.nan)
            y = np.full(n, np.nan)
            for j in range(n):
                step = int(round(keys[j][0]))
                if step < len(positions):
                    x[j] = positions[step]
                samples = np.asarray(list(data[j]), dtype=float)
                samples = samples[np.isfinite(samples)] if samples.ndim == 1 else []
                if len(samples):
                    y[j] = np.median(samples)
            analysis.update(x, y)
        except Exception as exc:
            _peak.note_once(
                f"bs_counter_failed_{type(exc).__name__}",
                f"{self.name}: peak analysis failed: {type(exc).__name__}: {exc}",
            )
        return analysis

    def _finish_peak_analysis(self, scan=None):
        """Last pass at the end of the scan, and the one-line result."""
        analysis = self._update_peak_analysis(scan)
        if analysis is not None and analysis.summary():
            print(analysis.summary())

    # the live plot (and its redraw QTimer) lives on the main thread, also
    # when the scan runs on a queue's worker thread
    @mainthread.on_main
    def _start_live_plot(self, bins):
        bins.plot = None
        if not self.live_plot or bins.ndim != 1 or len(bins.channels) != 1:
            return
        (name,) = bins.channels
        try:
            # Short timeout: this runs synchronously at scan start, so it
            # must not meaningfully delay the scan if data isn't flowing
            # yet -- plot_med() itself keeps live-updating afterward
            # regardless (see escape.stream.plots.Plot), it just won't
            # have its very first point yet.
            # label=self.name: this counter's own (possibly custom) name,
            # not the raw bs channel plot_med() would otherwise title/label
            # the plot with -- e.g. "mon_opt.intensity", not
            # "SAROP21-PBPS133:INTENSITY".
            bins.plot = bins.channels[name].plot_med(timeout=2, label=self.name)
        except Exception as exc:
            print(f"{self.name}: couldn't start a live plot: {exc}")

    def _on_scan_end(self, scan=None, **kwargs):
        bins = self.scan_bins(scan) if scan is not None else None
        if bins is None:
            return
        self._finish_peak_analysis(scan)
        # Stop binning; the bins keep this scan's data for grid(), the arrays
        # and the plot's last redraw.
        self._close(bins)
        if bins.plot is not None:
            # Stop the redraw timer (not accumulate(False)/close the figure:
            # the channel subscription stays) so the final result stays.
            self._stop_live_plot(bins.plot)

        if self.store:
            # Never let a storage problem fail the scan itself -- same
            # contract as CounterValue.store_arrays, which wraps its own
            # DataSet creation in a bare try/except and prints instead of
            # raising.
            try:
                self._build_arrays(bins, scan)
                self._store_arrays(bins)
            except Exception as exc:
                print(f"{self.name}: couldn't build/store arrays: {exc}")

    def _build_arrays(self, bins, scan):
        """Convert this scan's per-channel bins into real ``escape.Array``
        objects, one per channel -- with the parameter axis relabeled to the
        scan's actual adjustable values (mirroring
        ``eco.acquisition.counters.parameter_from_scan``) instead of
        ``Stream.to_array()``'s own default, the synthetic step index the
        bins are keyed on (see ``_StepIndexSource`` and the module
        docstring) -- which carries no physical meaning of its own, only bin
        identity.

        Sets ``bins.arrays`` (``{channel_name: escape.Array}``); left empty
        if ``scan`` isn't a real ``eco.acquisition.scan.StepScan`` (no
        ``scan_info``) or no steps were recorded (a scan that errored before
        its first step).
        """
        scan_info = getattr(scan, "scan_info", None)
        scan_values = scan_info.get("scan_values") if scan_info else None
        if not scan_values:
            return
        n_steps = len(scan_values)
        par_names = scan_info["scan_parameters"]["name"]
        parameter = {
            parname: {"values": [tvs[n] for tvs in scan_values]}
            for n, parname in enumerate(par_names)
        }

        arrays = {}
        for name, channel in bins.channels.items():
            n_bins = len(channel.lens())
            if n_bins == n_steps:
                arrays[name] = channel.to_array(parameter=parameter, grid_specs=bins.grid_specs)
            else:
                # A step that timed out with zero samples never created a
                # bin (open-ended Scan, see _ScanBins) -- the real scan
                # values wouldn't line up against this channel's shorter bin
                # list, so fall back to to_array()'s own parameter: that of
                # the channel's scan, which reads each bin that exists by its
                # own step's value (see _scan_in_scan_units) rather than by
                # its position.
                print(
                    f"{self.name}: {name} has {n_bins} bins but the scan "
                    f"took {n_steps} steps -- using the scan values of the "
                    "bins that exist instead of one per step."
                )
                arrays[name] = channel.to_array()
        bins.arrays = arrays

    def _store_arrays(self, bins):
        """Write ``bins.arrays`` (see ``_build_arrays``) to a fresh
        ``escape.DataSet`` HDF5 file, one dataset per channel -- the
        bs-stream-counter analogue of
        ``eco.acquisition.counters.CounterValue.store_arrays`` (same
        ``DataSet.create_with_new_result_file``/``.append``/``.store()``
        round-trip, reusing the same ``DEFAULT_STORAGE_DIR``/directory
        handling), just scoped to this one counter's channels rather than
        a whole scan's monitors.

        Sets ``bins.stored_filename`` to the resulting path; leaves
        ``bins.arrays`` itself as the plain in-memory arrays already built
        by ``_build_arrays`` (unlike ``CounterValue.store_arrays``, this does
        not reload/replace them with file-backed handles after writing --
        see the comment below for why not). No-op if there are no arrays.
        """
        if not bins.arrays:
            return
        from escape import DataSet

        from eco.acquisition.counters import DEFAULT_STORAGE_DIR
        from eco.utilities.datafiles import ensure_dir, ensure_group_writable

        directory = self.storage_dir
        if directory == "auto":
            directory = DEFAULT_STORAGE_DIR
        if callable(directory):
            directory = directory()
        directory = Path(directory)
        if not directory.exists():
            try:
                ensure_dir(directory)
            except Exception:
                print(f"{self.name}: could not create directory {directory.resolve()} !")

        filename = datetime.now().strftime("%Y-%m-%d_%H:%M:%S") + f".{self.name}.esc.h5"
        path = directory / filename

        d = DataSet.create_with_new_result_file(path, force_overwrite=False)
        for k, arr in bins.arrays.items():
            d.append(arr, name=k)
            arr.store()
        d.results_file.close()
        ensure_group_writable(path)
        bins.stored_filename = path.resolve().as_posix()
        print(f"{self.name}: stored filename {bins.stored_filename}")

        # Deliberately does NOT re-open/replace bins.arrays[k] with a
        # freshly reloaded, file-backed handle the way
        # CounterValue.store_arrays does for its own ArrayTimestamps
        # objects: escape.Array's own dataset wrapper (ArrayH5Dataset) is
        # lazy for at least some attributes even past load_from_result_file
        # (default lazy_loading=False notwithstanding), so a *second*
        # load-then-close round-trip here left the arrays holding
        # objects tied to an already-closed h5py.File - any later access
        # (e.g. arr._scan_parameter) raised deep inside h5py rather than
        # working, found by actually reading last_arrays back after a real
        # scan. The arrays from _build_arrays are plain in-memory
        # numpy-backed Array objects with no file dependency at all, so
        # there is nothing to refresh - keep them.

    def close(self):
        """Unsubscribe every channel for good (the one deliberate use of
        removeSource/its restart -- everything else keeps the subscription
        alive for this counter's whole lifetime, see the module docstring)."""
        for bins in list(self._open_bins):
            self._close(bins)
        for base in self._raw.values():
            base._source.eventWorker.removeSource(base._source.name)

    # -- reading a step's bin ---------------------------------------------------
    def _reduce_step(self, bins, step_index, n0=None, pulse_range=None):
        # n0: per-channel index into that bin to start reducing from -- data
        # already sitting in the bin *before* this acquisition began must not
        # be included, only what arrived since. Bins created fresh per scan
        # step start empty, so n0=0 there is a no-op.
        # pulse_range: (first, last) pulse id, inclusive -- only those shots
        # (following a counter that recorded its pulses, see stop()).
        n0 = n0 or {name: 0 for name in bins.channels}
        result = {}
        pulse_ids = {}
        samples_all = {}
        for name in bins.channels:
            samples = list(bins.bin(name, step_index))[n0[name] :]
            ids = list(bins.eventids(name, step_index))[n0[name] :]
            if pulse_range is not None:
                first, last = pulse_range
                kept = [
                    (v, i) for v, i in zip(samples, ids)
                    if i is not None and first <= i <= last
                ]
                samples = [v for v, _ in kept]
                ids = [i for _, i in kept]
            samples_all[name] = samples
            result[name] = (
                self.reduction(samples) if len(samples) > 1 else (samples[0] if samples else None)
            )
            pulse_ids[name] = ids
        bins.last_samples = samples_all
        value = result if len(result) > 1 else next(iter(result.values()), None)
        self.last_samples = samples_all
        self.last_values = result
        self.last_pulse_ids = pulse_ids
        self.last_value = value
        return value

    def step_feedback(self, scan=None, channel=None):
        """(mean, standard error, n) of the shots of *scan*'s last reduced
        step (without a scan: of the most recent step anywhere), one channel
        (default: the first) -- the live feedback an adaptive scan steers by
        (eco.acquisition.adaptive). Always the mean, whatever `reduction` is.
        None before the first step."""
        bins = self.scan_bins(scan) if scan is not None else None
        samples = bins.last_samples if bins is not None else self.last_samples
        if not samples:
            return None
        name = channel if channel is not None else next(iter(samples))
        return mean_sem(samples[name])

    def _wait_for_n_new(self, bins, step_index, n0, n_new):
        deadline = _time() + self.timeout
        while any(len(bins.bin(name, step_index)) < n0[name] + n_new for name in bins.channels):
            if _time() > deadline:
                have = {
                    name: len(bins.bin(name, step_index)) - n0[name] for name in bins.channels
                }
                raise TimeoutError(
                    f"{self.name}: step {step_index} got {have}/{n_new} new bs shots "
                    f"within {self.timeout}s -- is the stream still live?"
                )
            bins.new_data.wait(timeout=0.05)
            bins.new_data.clear()

    @mainthread.on_main
    def _stop_live_plot(self, plot):
        try:
            plot.replot()  # one last redraw with the true final data
            plot.stop()
        except Exception:
            pass

    def _flush_live_plot(self, bins):
        """Process the live-plot figure's pending GUI events (paints, and its
        own redraw ``QTimer``'s callback).

        Must be called from the *calling* thread of a scan (e.g. the
        interactive Qt/ipympl session's own thread) -- not from
        ``_wait_for_n_new()`` itself, which normally runs on a separate
        acquisition thread (``acquire()`` builds its ``Acquisition`` with
        ``hold=False``, so the wait loop is already off-thread by the time
        it starts). A GUI toolkit's event loop belongs to a single thread,
        and calling into it from any other thread is unsafe -- see
        ``acquire()``, which instead passes this as ``on_tick=`` to
        :class:`eco.acquisition.utilities.Acquisition`, so it runs
        repeatedly on the *calling* thread's own blocking ``wait()`` call.

        The GUI-thread pumping is needed at all because a scan runs as one
        long synchronous Python call on that thread -- the same thread an
        interactive session's GUI event loop normally runs on. Nothing
        services that event loop while ``ascan()``/``meshscan()`` is
        running, so without this, the live-plot window sits there
        unpainted (often literally black -- the canvas never got its first
        real paint event) for the whole scan, and the redraw ``QTimer``
        started by ``Stream.plot_med()`` never fires either -- both only
        catch up once the scan call returns and control is handed back to
        the normal event loop.
        """
        if bins.plot is None or threading.current_thread() is not threading.main_thread():
            return  # off the main thread (a queue): the heartbeat services Qt
        try:
            bins.plot.fig.canvas.flush_events()
        except Exception:
            pass

    # -- plain Detector protocol (single channel, standalone use only) ------
    def get_current_value(self):
        """The next shot of the (single) channel; None if none arrives within
        ``timeout``. Bins exist only for this read, so nothing keeps
        collecting afterwards."""
        if len(self._raw) != 1:
            return dict(self.last_values) if self.last_values else None
        bins = self._open()
        bins.resume()
        try:
            (name,) = bins.channels
            try:
                self._wait_for_n_new(bins, 0, {name: 0}, 1)
            except TimeoutError:
                return None
            return list(bins.bin(name, 0))[-1]
        finally:
            self._close(bins)

    # -- scan Counter protocol, simple mode ----------------------------------
    def acquire(self, scan=None, Npulses=1, **kwargs):
        Npulses = Npulses or 1
        standalone = scan is None
        bins = self._open() if standalone else self._bins_for(scan)
        step_index, n0 = self._advance_step(bins, scan)
        result = {}

        def _wait_and_reduce():
            t_start = _time()
            try:
                self._wait_for_n_new(bins, step_index, n0, Npulses)
                skip = n0
                if not standalone:
                    # this step's shots are in: no more into its bin, and
                    # keep exactly the N it is reduced from
                    bins.pause()
                    bins.trim(step_index, n0, n=Npulses)
                    skip = None
                result["value"] = self._reduce_step(bins, step_index, skip)
            finally:
                if standalone:
                    self._close(bins)
            set_window = getattr(scan, "set_step_window", None)
            ids = [i for ids in self.last_pulse_ids.values() for i in ids if i is not None]
            if set_window is not None and ids:  # only taken if this counter leads
                set_window(
                    self, pulse_id_start=int(min(ids)), pulse_id_stop=int(max(ids)),
                    time_start=t_start, time_stop=_time(),
                )
            return result["value"]

        return Acquisition(
            acquire=_wait_and_reduce, hold=False, get_result=lambda: result.get("value"),
            on_tick=lambda: self._flush_live_plot(bins),
        )

    # -- scan Counter protocol, start/stop mode ------------------------------
    def start(self, scan=None, **kwargs):
        if scan is None:
            bins = self._standalone_bins = self._open()
        else:
            bins = self._bins_for(scan)
        bins.pending = self._advance_step(bins, scan)

    def stop(self, scan=None, **kwargs):
        """End the step. Following a counter that recorded its pulses
        (``scan.step_window``, e.g. the DAQ): wait until every channel has a
        shot at or past the last of them -- bs data arrive some time after
        the pulse -- and keep exactly the shots of that range. Otherwise:
        what arrived since ``start()``."""
        bins = self._standalone_bins if scan is None else self.scan_bins(scan)
        step_index, n0 = bins.pending
        window = getattr(scan, "step_window", None) or {}
        first, last = window.get("pulse_id_start"), window.get("pulse_id_stop")
        if first is not None and last is not None:
            self._wait_for_pulse_id(bins, step_index, last)
            pulse_range = (first, last)
        else:
            pulse_range = None
        if scan is not None:
            # no more into this step's bin; keep exactly its shots
            bins.pause()
            bins.trim(step_index, n0, pulse_range=pulse_range)
            n0, pulse_range = None, None
        self._reduce_step(bins, step_index, n0, pulse_range=pulse_range)
        if scan is None:
            self._close(bins)
            self._standalone_bins = None
        return {"files": []}

    def _wait_for_pulse_id(self, bins, step_index, pulse_id):
        """Until every channel's bin of `step_index` has a shot with a pulse
        id >= `pulse_id`; TimeoutError after ``timeout`` seconds."""

        def reached(name):
            ids = bins.eventids(name, step_index)
            return bool(ids) and ids[-1] is not None and ids[-1] >= pulse_id

        deadline = _time() + self.timeout
        while not all(reached(name) for name in bins.channels):
            if _time() > deadline:
                missing = [name for name in bins.channels if not reached(name)]
                raise TimeoutError(
                    f"{self.name}: step {step_index}: no shot up to pulse id {pulse_id} "
                    f"from {missing} within {self.timeout}s -- is the stream still live?"
                )
            bins.new_data.wait(timeout=0.05)
            bins.new_data.clear()

    def _advance_step(self, bins, scan):
        """Point the bin key of *bins* at *scan*'s current step (or a single
        bin, index 0, if no scan) and return ``(step_index, n0)``, where
        ``n0`` is how many samples were already in that step's bin per
        channel -- data from *before* this call, to be excluded when
        reducing (see ``_reduce_step``). For a fresh per-step bin this is
        always 0.

        Bins are created on demand (open-ended ``Scan``, see ``_ScanBins``),
        so no "step exceeds allocated bins" check is needed here -- any
        non-negative step index is valid, it simply hasn't collected any
        samples yet until the first matching event arrives.

        Grid (N-D) scans: ``bins.step_sources`` has one entry per grid
        dimension (sized from *scan*'s own ``grid_specs``) -- each gets set
        from ``grid_specs["index_plan"][step_index]`` instead of the plain
        linear ``step_index``, so the ``Scan`` keys bins on the
        (x_index, y_index, ...) tuple rather than a flat count. The linear
        ``step_index`` itself is still what indexes into
        ``DataManager._data``: a real StepScan/meshscan visits each grid
        cell exactly once, in ``index_plan`` order, so the Nth step is
        always the Nth genuinely-new tuple the ``Scan`` discovers --
        ``scan._values[step_index] == index_plan[step_index]`` holds the
        same way the plain 1-D case already relied on
        ``scan._values[step_index]`` matching step order.
        """
        step_index = scan.next_step if scan is not None else 0
        grid_specs = bins.grid_specs
        if grid_specs:
            grid_index = grid_specs["index_plan"][step_index]
            for src, idx in zip(bins.step_sources, grid_index):
                src.value = float(idx)
        else:
            # the mapping first: the bin for this step comes into being on the
            # first event after the key moves (see _scan_in_scan_units)
            value = self._scan_value_at(scan)
            if value is not None:
                bins.step_values[step_index] = value
            bins.step_sources[0].value = float(step_index)
        ew = next(iter(bins.channels.values()))._source.eventWorker
        bins.step_pulse_ids[step_index] = ew.event.getEventId() if ew.event is not None else None
        n0 = {name: len(bins.bin(name, step_index)) for name in bins.channels}
        bins.resume()  # after the keys moved: from here on into this step
        return step_index, n0

    def grid(self, channel=None, shape=None, positions=None, dimension_names=None, scan=None):
        """Wrap one of this counter's channels as a live
        ``escape.stream.Grid`` (N-D reshaping + optional ``plot=True``
        live plotting for every reduction method -- see that class).

        Only meaningful for a real grid/mesh scan
        (``eco.acquisition.scan.Scans.meshscan``) -- that's what gives the
        bins more than one dimension and the ``grid_specs`` used to default
        ``shape``/``positions``/``dimension_names`` below.

        Reads the bins of *scan*, by default of the most recent scan: live
        and filling in while it runs, complete once it has ended.

        Parameters
        ----------
        channel : str, optional
            Which of this counter's channels to view as a grid; required
            only if this counter has more than one (with exactly one,
            that one is used automatically).
        shape, positions, dimension_names : optional
            Forwarded to ``Grid()``; default to the scan's own
            ``grid_specs`` when omitted.
        scan : optional
            The scan whose bins to show (default: the most recent one).

        Returns
        -------
        escape.stream.Grid
        """
        from escape.stream import Grid

        bins = self.scan_bins(scan)
        if bins is None:
            raise ValueError(
                f"{self.name}: no scan data to grid yet -- run a scan "
                "(callbacks_start_scan) first."
            )
        channels = bins.channels
        if channel is None:
            if len(channels) != 1:
                raise ValueError(
                    f"{self.name}: multiple channels ({list(channels)}) -- "
                    "pass channel=<name> to pick one for the grid."
                )
            (channel,) = channels
        gs = bins.grid_specs or {}
        if shape is None:
            shape = gs.get("shape")
        if positions is None:
            positions = gs.get("positions")
        if dimension_names is None:
            dimension_names = gs.get("grid_dimension_names")
        if shape is None:
            raise ValueError(
                f"{self.name}: no grid shape known -- run a real mesh/grid scan "
                "first (callbacks_start_scan sets this from scan.grid_specs), "
                "or pass shape= explicitly."
            )
        return Grid(
            channels[channel], shape=shape, positions=positions,
            dimension_names=dimension_names,
        )

    # The throwaway bs-stream test detector that used to live here
    # (`DetectorBsTest`, for exercising `BsStreamCounter`/`bs_scannable`
    # end to end without touching production `DetectorBsStream` instances)
    # has been absorbed into `eco.detector.detectors_psi.DetectorBsStream`
    # itself as its `bs_scan_mode=True` constructor flag, now that this has
    # been confirmed working against the real dispatcher -- see that
    # class's docstring. Construct a plain `DetectorBsStream(bs_channel,
    # cachannel="none", name=..., bs_scan_mode=True)` instead.
