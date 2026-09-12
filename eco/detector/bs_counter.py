"""Investigation mockup: a StepScan "counter" backed directly by one or more
live bs (beam-synchronous / escape) streams, instead of an EPICS PV monitor.

Not wired into any namespace or ``bernina.py`` -- this is a proof of concept
for a design discussion, exercised in ``tests/test_bs_counter_mockup.py``
against ``escape.stream``'s synthetic local test stream (no real beamline
needed).

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
never repeated) from **per-scan bin structure** (a fresh ``Scan`` built in
``_build_bins()``, which now only manipulates local ``eventCallbacks``
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

from threading import Event
from time import time as _time

import numpy as np

from eco.acquisition.utilities import Acquisition
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
    """Class decorator giving a bs-stream-backed detector (e.g.
    ``DetectorBsStream``) a lazy ``.scans`` container powered by
    ``BsStreamCounter`` -- the bs-stream analogue of
    ``eco.acquisition.decorators.scannable`` (which wires up ``CounterValue``,
    i.e. EPICS-monitor/polling-based scanning, instead).

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
        -- ``self._channels[name].plot_med()`` (median + percentile bands
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

    Usage
    -----
    Outside a scan, each channel's ``get_current_value()``-equivalent is
    ``.last_values['<channel>']`` after calling ``.acquire().wait()``, or
    read live via ``.get_current_value()`` (single channel only).

    As a StepScan counter (``eco.acquisition.counter_protocol.Counter``),
    pass it in ``scan.counters``: ``callbacks_start_scan``/``_end_scan``
    (re)build the per-step bins sized to that scan's step count, and
    ``acquire(scan, Npulses)`` / ``start(scan)``+``stop(scan)`` wait for
    and reduce that step's bin, across all channels at once.
    """

    def __init__(
        self, sources, name=None, reduction=np.mean, timeout=10, eventworker=None,
        live_plot=True,
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
        self._plot = None
        self.alias = Alias(self.name, channel=list(self._raw), channeltype="BS")

        # One source per grid dimension -- just one ("step_index") for an
        # ordinary linear scan; _on_scan_start() sizes this to match
        # scan.grid_specs for a real mesh/grid scan, before _build_bins()
        # builds the Scan() from them. Scan() keys bins on the tuple of
        # however many of these there are, so this is the entire mechanism
        # N-D (grid) binning needs -- see escape.stream.Grid, which reads
        # this same tuple structure back out of self._scan._values (also
        # what self.grid() below hands it).
        self._step_sources = [_StepIndexSource("step_index")]
        self._grid_specs = None  # set by _on_scan_start() for a grid scan
        self._scan = None
        self._channels = {}
        self._scan_running = False
        # Snapshot of the most recently *completed* scan's bins -- see
        # _on_scan_end() and grid().
        self._last_channels = {}
        self._last_scan = None
        self._last_grid_specs = None
        self._new_data = Event()
        self._step_index = 0
        self.last_value = None
        self.last_values = {}
        self.last_pulse_ids = {}
        self.step_pulse_ids = {}

        # Subscribe each channel with the transport exactly once, here, for
        # the life of this counter -- see _build_bins()'s docstring for why
        # this must not repeat on every scan.
        for base in self._raw.values():
            base._source.eventWorker.registerSource(base._source.name)

        self._build_bins()

        self.callbacks_start_scan = [self._on_scan_start]
        self.callbacks_start_step = []
        self.callbacks_step_counting = []
        self.callbacks_end_step = []
        self.callbacks_end_scan = [self._on_scan_end]

    # -- (re)building the shared per-step bin structure ---------------------
    def _build_bins(self):
        """Fresh per-scan bins, reusing the already-subscribed channels.

        Open-ended (values=None): bins are created on demand as new step
        indices appear. Safe to share across every channel in self._raw
        regardless of which one's data reaches a given step first -- see
        the module docstring (escape-fel 0.2.7, DataManager.append).

        Deliberately does NOT call ``Stream.accumulate(True)`` (which is
        what an earlier version of this method did): that calls
        ``EventWorker.registerSource``, which -- for the real
        dispatcher/datahub-backed handler -- unconditionally schedules a
        debounced restart of the *entire shared* underlying connection
        (``EventWorker._schedule_restart``) even when the channel is
        already subscribed, on every single call. Doing that on every scan
        start/end (this method used to run from both) was confirmed
        against the real dispatcher to be the source of most of this
        class's console output, and to occasionally race with the
        datahub-backed handler's own stream teardown (an
        already-registered channel does not need registering again, only a
        fresh bin to write into).
        """
        from escape import stream as escape_stream

        self._scan = escape_stream.Scan(parameters=list(self._step_sources))
        for src in self._step_sources:
            src.value = 0.0
        self._step_index = 0
        # NOTE: step_pulse_ids is deliberately NOT reset here -- _build_bins()
        # runs at the end of a scan too (_on_scan_end), and clearing it there
        # would wipe the just-finished scan's record before calling code ever
        # gets to read it. Only _on_scan_start resets it (a fresh scan is
        # starting, the old record is no longer relevant); __init__ starts it
        # at {} directly.

        self._channels = {}
        for name, base in self._raw.items():
            binned = escape_stream.Stream(source=base._source, scan=self._scan)
            ew = binned._source.eventWorker
            if binned._appendEventData not in ew.eventCallbacks:
                ew.eventCallbacks.append(binned._appendEventData)
            ew.eventCallbacks.append(self._new_data.set)
            self._channels[name] = binned

    def _teardown_bins(self):
        # Mirrors _build_bins(): only removes the per-scan bin-wrapper's own
        # callbacks, never touches the channel subscription itself (no
        # accumulate(False)/removeSource -- see _build_bins()'s docstring).
        for s in self._channels.values():
            ew = s._source.eventWorker
            try:
                ew.eventCallbacks.remove(s._appendEventData)
            except ValueError:
                pass
            try:
                ew.eventCallbacks.remove(self._new_data.set)
            except ValueError:
                pass
        self._channels = {}

    def _on_scan_start(self, scan=None, **kwargs):
        # Size self._step_sources to match *this* scan's grid dimensionality
        # (1 for an ordinary linear scan) before _build_bins() builds the
        # Scan() from them -- growing this later, inside _advance_step(),
        # would be after the Scan/DataManagers for this run already exist
        # with the wrong number of parameters.
        ndim = 1
        grid_specs = _get_grid_specs(scan)
        if grid_specs:
            ndim = len(grid_specs["shape"])
        self._step_sources = (
            [_StepIndexSource("step_index")]
            if ndim <= 1
            else [_StepIndexSource(f"step_index_{d}") for d in range(ndim)]
        )
        self._grid_specs = grid_specs  # None for an ordinary scan -- see grid()
        self._scan_running = True
        self._teardown_bins()
        self._build_bins()
        self.step_pulse_ids = {}
        self._start_live_plot(ndim)

    def _start_live_plot(self, ndim):
        self._plot = None
        if not self.live_plot or ndim != 1 or len(self._channels) != 1:
            return
        (name,) = self._channels
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
            self._plot = self._channels[name].plot_med(timeout=2, label=self.name)
        except Exception as exc:
            print(f"{self.name}: couldn't start a live plot: {exc}")

    def _on_scan_end(self, scan=None, **kwargs):
        # Snapshot the just-finished scan's bins before _build_bins() below
        # replaces self._channels/self._scan with fresh, empty ones for
        # standalone use -- without this, grid()/any post-scan inspection
        # of the full per-step data (not just last_value(s), the one step
        # snapshot) would see nothing the moment the scan ends, since
        # tearing down and rebuilding is exactly what always happened here
        # even before grid() existed (fine for last_value(s), which are
        # already-extracted plain values, not fine for anything reading
        # the bins themselves after the fact).
        self._last_channels = dict(self._channels)
        self._last_scan = self._scan
        self._last_grid_specs = self._grid_specs
        self._scan_running = False
        if self._plot is not None:
            # Stop the redraw timer (not accumulate(False)/close the
            # figure -- see live_plot's docstring on why this counter
            # never tears down the channel subscription itself) so the
            # scan's final result stays visible instead of continuing to
            # "update" against the fresh, empty standalone bins
            # _build_bins() is about to create.
            try:
                self._plot.replot()  # one last redraw with the true final data
                self._plot.stop()
            except Exception:
                pass
        self._teardown_bins()
        self._build_bins()

    def close(self):
        """Unsubscribe every channel for good (the one deliberate use of
        removeSource/its restart -- everything else keeps the subscription
        alive for this counter's whole lifetime, see _build_bins())."""
        self._teardown_bins()
        for base in self._raw.values():
            base._source.eventWorker.removeSource(base._source.name)

    # -- reading the current step's bin -------------------------------------
    # Bins are created on demand (open-ended Scan) the first time a matching
    # event actually arrives for a given channel, so `idx` may not exist yet
    # in a freshly-advanced-to step -- treat that as "no samples yet", not
    # an error.
    def _bin(self, name, step_index=None):
        idx = self._step_index if step_index is None else step_index
        data = self._channels[name]._dataManager._data
        return data[idx] if idx < len(data) else []

    def _eventids(self, name, step_index=None):
        idx = self._step_index if step_index is None else step_index
        eventids = self._channels[name]._dataManager._eventIds
        return eventids[idx] if idx < len(eventids) else []

    def _reduce_step(self, step_index, n0=None):
        # n0: per-channel index into that bin to start reducing from -- data
        # already sitting in the bin *before* this acquisition began (e.g.
        # standalone/no-scan use, where every acquire() shares the same
        # single bin) must not be included, only what arrived since. Bins
        # created fresh per scan step start empty, so n0=0 there is a no-op.
        n0 = n0 or {name: 0 for name in self._channels}
        result = {}
        pulse_ids = {}
        for name in self._channels:
            samples = list(self._bin(name, step_index))[n0[name] :]
            result[name] = (
                self.reduction(samples) if len(samples) > 1 else (samples[0] if samples else None)
            )
            pulse_ids[name] = list(self._eventids(name, step_index))[n0[name] :]
        self.last_values = result
        self.last_pulse_ids = pulse_ids
        self.last_value = result if len(result) > 1 else next(iter(result.values()), None)
        return self.last_value

    def _wait_for_n_new(self, step_index, n0, n_new):
        deadline = _time() + self.timeout
        while any(len(self._bin(name, step_index)) < n0[name] + n_new for name in self._channels):
            if _time() > deadline:
                have = {
                    name: len(self._bin(name, step_index)) - n0[name] for name in self._channels
                }
                raise TimeoutError(
                    f"{self.name}: step {step_index} got {have}/{n_new} new bs shots "
                    f"within {self.timeout}s -- is the stream still live?"
                )
            self._new_data.wait(timeout=0.05)
            self._new_data.clear()

    def _flush_live_plot(self):
        """Process this counter's live-plot figure's pending GUI events
        (paints, and its own redraw ``QTimer``'s callback).

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
        if self._plot is None:
            return
        try:
            self._plot.fig.canvas.flush_events()
        except Exception:
            pass

    # -- plain Detector protocol (single channel, standalone use only) ------
    def get_current_value(self):
        if len(self._channels) != 1:
            return dict(self.last_values) if self.last_values else None
        (name,) = self._channels
        if not self._bin(name):
            # freshly subscribed (e.g. just built for this one call) -- wait
            # briefly for the first shot rather than reporting "no value" for
            # a channel that is, in fact, live.
            try:
                self._wait_for_n_new(self._step_index, {name: 0}, 1)
            except TimeoutError:
                return None
        return list(self._bin(name))[-1]

    # -- scan Counter protocol, simple mode ----------------------------------
    def acquire(self, scan=None, Npulses=1, **kwargs):
        Npulses = Npulses or 1
        step_index, n0 = self._advance_step(scan)

        def _wait_and_reduce():
            self._wait_for_n_new(step_index, n0, Npulses)
            return self._reduce_step(step_index, n0)

        return Acquisition(
            acquire=_wait_and_reduce, hold=False, get_result=lambda: self.last_value,
            on_tick=self._flush_live_plot,
        )

    # -- scan Counter protocol, start/stop mode ------------------------------
    def start(self, scan=None, **kwargs):
        self._step_index, self._n0 = self._advance_step(scan)

    def stop(self, scan=None, **kwargs):
        self._reduce_step(self._step_index, self._n0)
        return {"files": []}

    def _advance_step(self, scan):
        """Point the shared bin key at *scan*'s current step (or a single
        standalone bin, index 0, if no scan) and return ``(step_index, n0)``,
        where ``n0`` is how many samples were already in that step's bin per
        channel -- data from *before* this call, to be excluded when
        reducing (see ``_reduce_step``). For a fresh per-step bin this is
        always 0; for the standalone single-ever-bin case it is whatever a
        previous acquire()/get_current_value() call already left there.

        Bins are created on demand (open-ended ``Scan``, see
        ``_build_bins``), so no "step exceeds allocated bins" check is
        needed here -- any non-negative step index is valid, it simply
        hasn't collected any samples yet until the first matching event
        arrives.

        Grid (N-D) scans: ``self._step_sources`` has one entry per grid
        dimension (sized in ``_on_scan_start``, from *scan*'s own
        ``grid_specs``) -- each gets set from
        ``grid_specs["index_plan"][step_index]`` instead of the plain
        linear ``step_index``, so the shared ``Scan`` keys bins on the
        (x_index, y_index, ...) tuple rather than a flat count. The linear
        ``step_index`` itself is still what indexes into
        ``DataManager._data`` (unchanged): a real StepScan/meshscan visits
        each grid cell exactly once, in ``index_plan`` order, so the Nth
        step is always the Nth genuinely-new tuple the shared ``Scan``
        discovers -- ``scan._values[step_index] == index_plan[step_index]``
        holds the same way the plain 1-D case already relied on
        ``scan._values[step_index]`` matching step order.
        """
        step_index = scan.next_step if scan is not None else 0
        self._step_index = step_index
        grid_specs = _get_grid_specs(scan)
        if grid_specs:
            grid_index = grid_specs["index_plan"][step_index]
            for src, idx in zip(self._step_sources, grid_index):
                src.value = float(idx)
        else:
            self._step_sources[0].value = float(step_index)
        ew = next(iter(self._channels.values()))._source.eventWorker
        start_pid = ew.event.getEventId() if ew.event is not None else None
        self.step_pulse_ids[step_index] = start_pid
        n0 = {name: len(self._bin(name, step_index)) for name in self._channels}
        return step_index, n0

    def grid(self, channel=None, shape=None, positions=None, dimension_names=None):
        """Wrap one of this counter's channels as a live
        ``escape.stream.Grid`` (N-D reshaping + optional ``plot=True``
        live plotting for every reduction method -- see that class).

        Only meaningful once ``callbacks_start_scan`` has run for a real
        grid/mesh scan (``eco.acquisition.scan.Scans.meshscan``) -- that's
        what sizes ``self._step_sources`` to more than one dimension and
        records ``grid_specs`` (used to default ``shape``/``positions``/
        ``dimension_names`` below) in the first place.

        Works both while such a scan is still running (a live, filling-in
        grid -- reads ``self._channels``, the currently-active bins) and
        after it has finished (reads ``self._last_channels``, a frozen
        snapshot taken the moment the scan ended, *before*
        ``callbacks_end_scan`` rebuilds fresh, empty bins for standalone
        use -- without that snapshot, calling this after the scan would
        see nothing, even though ``last_value``/``last_values`` still
        report the final step's value just fine, since those are already-
        extracted plain values rather than a reference into the bins).

        Parameters
        ----------
        channel : str, optional
            Which of this counter's channels to view as a grid; required
            only if this counter has more than one (with exactly one,
            that one is used automatically).
        shape, positions, dimension_names : optional
            Forwarded to ``Grid()``; default to this counter's
            last-seen ``grid_specs`` (from the scan itself) when omitted.

        Returns
        -------
        escape.stream.Grid
        """
        from escape.stream import Grid

        channels = self._channels if self._scan_running else self._last_channels
        grid_specs = self._grid_specs if self._scan_running else self._last_grid_specs
        if not channels:
            raise ValueError(
                f"{self.name}: no scan data to grid yet -- run a scan "
                "(callbacks_start_scan) first, or check .close()d channels."
            )
        if channel is None:
            if len(channels) != 1:
                raise ValueError(
                    f"{self.name}: multiple channels ({list(channels)}) -- "
                    "pass channel=<name> to pick one for the grid."
                )
            (channel,) = channels
        gs = grid_specs or {}
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


@bs_scannable
class DetectorBsTest:
    """Throwaway bs-stream test detector for exercising ``BsStreamCounter``/
    ``bs_scannable`` end to end -- construction, ``.scans``, a real
    ``ascan()`` -- against the real facility dispatcher, without touching
    ``DetectorBsStream`` or any production device built on it (e.g.
    ``mon_opt.intensity``, which deliberately stays on the plain
    EPICS-monitor ``@scannable``/``CounterValue`` path; see this module's
    "What changed testing against the real dispatcher" section above for
    why bs-stream scanning isn't ready to be the default there yet).

    This is explicitly **not** wired into any namespace and has no PV
    mirror, settings, or status-display integration -- construct throwaway
    instances directly, e.g.::

        det = DetectorBsTest("SAROP21-PBPS133:INTENSITY", name="test_intensity")
        det.get_current_value()
        det.scans.ascan(some_dummy_adjustable, 0, 4, 4, 10)
        det._bs_counter.close()   # unsubscribe when done experimenting

    Confirmed against the real dispatcher (``SAROP21-PBPS133:INTENSITY``,
    an ``eco.elements.adjustable.DummyAdjustable``, two consecutive
    ``ascan()`` runs): correct per-step values, correct per-step pulse_id
    partitioning, and -- after the ``_build_bins()`` fix described above --
    no extra console output and no teardown-race exception.
    """

    def __init__(self, bs_channel, name=None):
        from escape import stream as escape_stream
        from eco.detector.detectors_psi import _ensure_bs_event_worker

        self.name = name or bs_channel
        self.bs_channel = bs_channel
        self.alias = Alias(self.name, channel=bs_channel, channeltype="BS")
        _ensure_bs_event_worker()
        self.stream = escape_stream.Stream(bs_channel, None)

    def get_current_value(self):
        return self._get_bs_current_value()

    def _get_bs_current_value(self):
        # Lazily built and cached (not per-call) -- see BsStreamCounter/
        # bs_scannable docstrings for why that must not be rebuilt on every
        # read (it holds a live bs subscription).
        if not hasattr(self, "_bs_counter"):
            self._bs_counter = BsStreamCounter(self, name=self.name)
        return self._bs_counter.get_current_value()
