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
   for post-hoc data? Yes to the first part -- verified directly against a
   live stream (fixed value-range bins, ``t.digitize(bins).categorize(i)``,
   produced a correct multi-bin histogram). For step-scan binning, ``Scan``
   with a synthetic "current step index" parameter (rather than a real bs
   channel) is the right primitive -- but see the bug below, found and
   worked around here rather than in ``escape.stream`` itself.

The ``Scan`` open-bin bug (found, not yet fixed upstream)
------------------------------------------------------------
``escape.stream.Scan(parameters=[...])`` with no ``values=`` starts empty
and grows one bin at a time, the first time a genuinely new parameter value
is seen (``Scan._append()``). Each ``Stream`` wrapping it gets its own
``DataManager``, whose per-bin deque list is sized to
``len(scan._values)`` **at the moment that Stream/DataManager is
constructed** -- not kept in sync afterwards.

That is fine for a single channel (it always discovers a bin via its own
call to ``scan._append()`` before ever indexing into it). It is **not**
fine the moment a *second* channel shares the same growing ``Scan``: if
channel A's own event stream reaches a new step index before channel B's
does, A's call to ``scan._append()`` grows the *shared* ``_values`` list,
but B's own (unrelated) ``_data`` list is not grown to match. B's next
event then indexes past the end of its own list. Confirmed directly: this
raises ``IndexError: list index out of range`` inside
``Stream._appendEventData``, which runs as an ``EventWorker`` callback
inside a blanket ``try/except`` (``EventWorker.eventLoop``) -- so it never
raises up to caller code, it just prints
``"EventWorker callback error: list index out of range"`` once per event,
forever, and **that channel silently stops recording data for the rest of
the scan.**

Fix used here: never leave the shared ``Scan`` open-ended when more than
one channel is attached to it. Every bin is pre-declared up front
(``values=[(0.0,), (1.0,), ...]``) so every channel's ``DataManager`` is
born the correct final size regardless of which channel's data happens to
arrive first for any given step -- confirmed directly to eliminate the
crash and produce identical, correct per-step sample counts across three
simultaneously-binned channels. The number of steps is read from the
``scan`` object StepScan already passes into ``Counter.acquire``/``start``
(``len(scan._values_todo) + len(scan._values_done)``), so nothing new has
to be threaded through ``scan.py`` for this either.

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
    the underlying ``Stream``."""
    from escape import stream as escape_stream
    from eco.detector.detectors_psi import DetectorBsStream

    if isinstance(source, DetectorBsStream):
        return source.stream
    if isinstance(source, escape_stream.Stream):
        return source
    if isinstance(source, str):
        if eventworker is None:
            from eco.detector.detectors_psi import _ensure_bs_event_worker

            eventworker = _ensure_bs_event_worker()
        return escape_stream.Stream(source, eventworker)
    raise TypeError(
        "Expected a bs channel name, escape.stream.Stream, or DetectorBsStream, "
        f"got {type(source)}"
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

        self._build_bins(n_steps=1)  # standalone (no-scan) mode: one bin

        self.callbacks_start_scan = [self._on_scan_start]
        self.callbacks_start_step = []
        self.callbacks_step_counting = []
        self.callbacks_end_step = []
        self.callbacks_end_scan = [self._on_scan_end]

    # -- (re)building the shared per-step bin structure ---------------------
    def _build_bins(self, n_steps):
        from escape import stream as escape_stream

        n_steps = max(1, n_steps or 1)
        self._scan = escape_stream.Scan(
            parameters=[self._step_source],
            values=[(float(k),) for k in range(n_steps)],
        )
        self._step_source.value = 0.0
        self._step_index = 0
        self.step_pulse_ids = {}

        self._channels = {}
        for name, base in self._raw.items():
            binned = escape_stream.Stream(source=base._source, scan=self._scan)
            binned.accumulate(True)
            binned._source.eventWorker.eventCallbacks.append(self._new_data.set)
            self._channels[name] = binned

    def _teardown_bins(self):
        for s in self._channels.values():
            s.accumulate(False)
            try:
                s._source.eventWorker.eventCallbacks.remove(self._new_data.set)
            except ValueError:
                pass
        self._channels = {}

    def _on_scan_start(self, scan=None, **kwargs):
        n_steps = None
        if scan is not None:
            n_steps = len(getattr(scan, "_values_todo", [])) + len(
                getattr(scan, "_values_done", [])
            )
        self._teardown_bins()
        self._build_bins(n_steps)

    def _on_scan_end(self, scan=None, **kwargs):
        self._teardown_bins()
        self._build_bins(n_steps=1)

    def close(self):
        """Unsubscribe every channel."""
        self._teardown_bins()

    # -- reading the current step's bin -------------------------------------
    def _bin(self, name, step_index=None):
        idx = self._step_index if step_index is None else step_index
        return self._channels[name]._dataManager._data[idx]

    def _eventids(self, name, step_index=None):
        idx = self._step_index if step_index is None else step_index
        return self._channels[name]._dataManager._eventIds[idx]

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
        """Point the shared bin key at *scan*'s current step (or the single
        standalone bin if no scan) and return ``(step_index, n0)``, where
        ``n0`` is how many samples were already in that step's bin per
        channel -- data from *before* this call, to be excluded when
        reducing (see ``_reduce_step``). For a fresh per-step bin this is
        always 0; for the standalone single-ever-bin case it is whatever a
        previous acquire()/get_current_value() call already left there.
        """
        # No scan -> standalone use, always the single bin built by
        # _build_bins(n_steps=1); a real scan's step index is used verbatim.
        step_index = scan.next_step if scan is not None else 0
        if step_index >= len(self._scan._values):
            raise IndexError(
                f"{self.name}: step {step_index} exceeds the {len(self._scan._values)} "
                "bins allocated at scan start -- was callbacks_start_scan run for this scan?"
            )
        self._step_index = step_index
        self._step_source.value = float(step_index)
        ew = next(iter(self._channels.values()))._source.eventWorker
        start_pid = ew.event.getEventId() if ew.event is not None else None
        self.step_pulse_ids[step_index] = start_pid
        n0 = {name: len(self._bin(name, step_index)) for name in self._channels}
        return step_index, n0
