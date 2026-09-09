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
    stream data."""

    name = "step_index"

    def __init__(self):
        self.value = 0.0

    def _getEventData(self):
        return self.value


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

    @property
    def scans(self):
        from eco.acquisition import scan  # avoid circular import

        if not hasattr(self, "_bs_counter"):
            self._bs_counter = BsStreamCounter(self, name=self.alias.get_full_name())
        if not hasattr(self, "_scans"):
            self._scans = scan.Scans(default_counters=[self._bs_counter])
        else:
            self._scans._default_counters = [self._bs_counter]
            self._scans._augment_docstrings()
        return self._scans

    Obj.scans = scans
    return Obj


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

    def __init__(self, sources, name=None, reduction=np.mean, timeout=10, eventworker=None):
        sources = sources if isinstance(sources, (list, tuple)) else [sources]
        self._raw = {}
        for src in sources:
            s = _resolve_stream(src, eventworker)
            self._raw[s.name] = s

        self.name = name or "+".join(self._raw)
        self.reduction = reduction
        self.timeout = timeout
        self.alias = Alias(self.name, channel=list(self._raw), channeltype="BS")

        self._step_source = _StepIndexSource()
        self._scan = None
        self._channels = {}
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

        self._scan = escape_stream.Scan(parameters=[self._step_source])
        self._step_source.value = 0.0
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
        self._teardown_bins()
        self._build_bins()
        self.step_pulse_ids = {}

    def _on_scan_end(self, scan=None, **kwargs):
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
            acquire=_wait_and_reduce, hold=False, get_result=lambda: self.last_value
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
        """
        step_index = scan.next_step if scan is not None else 0
        self._step_index = step_index
        self._step_source.value = float(step_index)
        ew = next(iter(self._channels.values()))._source.eventWorker
        start_pid = ew.event.getEventId() if ew.event is not None else None
        self.step_pulse_ids[step_index] = start_pid
        n0 = {name: len(self._bin(name, step_index)) for name in self._channels}
        return step_index, n0


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
