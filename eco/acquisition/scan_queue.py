"""A queue of pending scan calls, run one at a time on a dedicated worker
thread, so submitting a scan doesn't block the calling session.

Motivation
----------
Today ``scans.dscan(...)`` *is* the scan -- it blocks the calling thread
until every step is done. There's no way to queue up several scans and
walk away, no way to ask "what's running" from elsewhere, and no pause
that takes effect *during* a scan rather than only after it. ``ScanQueue``
wraps the existing ``Scans``/``StepScan`` pair to add exactly that, without
touching either of them: it just calls ``Scans.xxx(..., start_immediately=
False)`` (already a supported code path -- it's what this session used
throughout to inspect a scan's plan before running it) to build the real
``StepScan`` -- positions, gridspecs, counters, all of it -- without moving
anything, checks every planned position against each adjustable's own
``get_limits()`` if it has one, and only then calls ``.scan_all()`` for
real.

Minimal example
----------------
    q = scans.queues.default                  # this Scans instance's "default" lane
    item = q.submit(scans.dscan, dummy_adjustable, -1, 1, 20, 0.3,
                     description="alignment check")
    item.wait()                               # block for the result, or just
                                               # walk away and check q.status()
    later = q.submit(scans.ascan, mono, 0, 10, 50, 0.1)
    q.pause()                                 # don't start the next queued item
    q.remove(later.id)                        # drop a not-yet-started item
    q.resume()

The same, without touching ``.queues`` directly -- every ``Scans`` method
accepts ``scan_queue=``, which routes the call through the named queue
(auto-created) instead of running it directly and returns the ``QueueItem``
instead of the ``StepScan``. ``scan_queue=None`` (the default everywhere)
keeps today's behaviour -- runs synchronously, blocks, returns the
``StepScan``, exactly as if ``ScanQueue`` didn't exist:

    item = scans.dscan(dummy_adjustable, -1, 1, 20, 0.3, scan_queue=True)
    # scan_queue=True / scan_queue=1 both mean "the 'default' queue";
    # scan_queue="alignment" routes through that named queue instead.

Not just scans -- submit() takes any callable
------------------------------------------------
``submit()`` auto-detects whether `method` looks like a ``Scans`` method
(accepts ``start_immediately``) or is just a plain function -- a motor
move, your own procedure, anything. A plain callable gets no StepScan
"simulation"/limit check (there's no scan to validate), it just runs when
its turn comes; pass ``validate=`` (a zero-arg callable that raises to
reject) for your own pre-flight check on one of these:

    scans.queues.default.submit(some_motor.set_target_value, 5.0)
    scans.queues.default.submit(lambda: some_motor.set_target_value(5.0).wait())
    scans.queues.default.submit(my_alignment_procedure, arg1, arg2,
                                 validate=lambda: assert_beam_is_on())

Composing scans and queues
---------------------------
    scans.queues.default                      # this Scans instance's
                                               # "default" lane (auto-
                                               # created on first use)
    scans.queues.alignment                    # a different lane, same
                                               # Scans instance -- its own
                                               # worker thread, runs
                                               # concurrently with .default
    scans.queues.pause_all()                  # queue-level pause (see
    scans.queues.stop_all()                   # below) across every lane
                                               # in this container
    get_queue("shared_alignment")             # an explicitly named,
                                               # *globally* shared queue --
                                               # multiple Scans instances
                                               # (or scripts) can route
                                               # through the same one on
                                               # purpose, so it serializes
                                               # across them. Independent
                                               # from any Scans instance's
                                               # own .queues container.

Pausing a scan already running
-------------------------------
``q.pause()`` only stops the *next* queued item from starting -- the
current one keeps going. To pause the scan that's actually running right
now, mid-flight, at its next step boundary:

    q.pause_current()     # == q._current.scan.pause()
    q.resume_current()
    q.stop_current()      # abandon it instead of resuming

Store / resume
---------------
A paused item can be snapshotted to disk and picked back up later --
possibly in a different process, since only the remaining values (not live
counter/adjustable state) are saved:

    q.pause_current()
    q.save_paused(q._current, "/path/to/paused_scan.json")
    ...
    item2 = q.resume_from_file("/path/to/paused_scan.json", root=bernina,
                                counters=[my_daq])

This is a first cut, not a hardened mechanism: it re-resolves adjustables
by dotted name from ``root`` and expects you to hand back live counters
explicitly (ad hoc counters like ``CounterValue`` aren't addressable by
name from a namespace the way adjustables are, so this doesn't try to
guess one). Fine for "the process died, let me pick this back up"; not a
substitute for a real job-persistence system.
"""
import inspect
import itertools
import json
import threading
import traceback
import weakref
from pathlib import Path


class LimitViolation(Exception):
    """Raised by the pre-flight check when a queued scan's plan sends some
    adjustable to a position outside its own ``get_limits()``."""


class QueueItem:
    """Handle for one submitted (or resumed) scan call. Returned by
    ``ScanQueue.submit()``/``resume_from_file()`` -- not constructed
    directly."""

    _ids = itertools.count()

    def __init__(self, call, description="", queue=None):
        self.id = next(QueueItem._ids)
        self.description = description
        self._call = call  # zero-arg callable: build (start_immediately=False) + hand back the StepScan
        self.state = "pending"  # pending -> validating -> running -> done | error | cancelled
        self.scan = None  # the StepScan, once built (from "validating" onward)
        self.result = None
        self.exception = None
        self._queue = queue  # owning ScanQueue, for wait()'s Ctrl-C handling
        self._done_event = threading.Event()

    def wait(self, timeout=None):
        """Block until this item finishes (or `timeout` elapses). Re-raises
        the scan's exception, if any, in the calling thread.

        Ctrl-C while blocked here is caught and turned into a prompt --
        abort just this item's queue, abort every queue that currently
        exists in this process, or keep waiting -- rather than a bare
        traceback. See handle_keyboard_interrupt(). This only catches a
        Ctrl-C landing *while blocked in this call*; a scan running
        unattended in the background isn't covered by this yet (see the
        module docstring / the survey artifact's §4.3 discussion)."""
        try:
            self._done_event.wait(timeout=timeout)
        except KeyboardInterrupt:
            handle_keyboard_interrupt(self._queue)
            raise
        if self.exception is not None:
            raise self.exception
        return self.result

    def cancel(self):
        """Remove this item before it starts. No effect (returns False) once
        it's already validating/running/finished."""
        if self.state == "pending":
            self.state = "cancelled"
            self._done_event.set()
            return True
        return False

    def __repr__(self):
        return f"<QueueItem #{self.id} {self.state!r} {self.description!r}>"


class ScanQueue:
    """One named queue: a list of pending items plus a dedicated worker
    thread that runs them one at a time. Prefer ``Scans.queue`` or
    ``get_queue(name)`` over constructing this directly."""

    def __init__(self, name="default", history_max=200):
        self.name = name
        self.history_max = history_max
        self.history = []
        self._items = []
        self._current = None
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._paused = False
        self._stop_worker = False
        self._worker = threading.Thread(
            target=self._run, daemon=True, name=f"ScanQueue[{name}]"
        )
        self._worker.start()
        # every ScanQueue registers itself here, whether created via
        # get_queue(name) (explicit sharing) or Scans.queue (private, one
        # per Scans instance) -- so "abort everything" (see
        # handle_keyboard_interrupt) can reach private queues too, without
        # Scans.queue needing to go through the name registry at all.
        with _all_queues_lock:
            _all_queues.append(weakref.ref(self))

    # -- submission --------------------------------------------------------

    def submit(
        self,
        method,
        *args,
        description="",
        skip_limit_check=False,
        validate=None,
        **kwargs,
    ):
        """Queue a call for this queue's worker thread to run in turn.
        Returns a ``QueueItem`` immediately.

        Two shapes, detected automatically (by whether `method` accepts a
        `start_immediately` argument):

        - a ``Scans`` method, e.g. ``scans.dscan`` -- built first with
          ``start_immediately=False`` (the "simulation": a real StepScan,
          positions and all, without moving anything), then every planned
          position is checked against each adjustable's own
          ``get_limits()`` (pass ``skip_limit_check=True`` to skip this),
          and only then is ``.scan_all()`` actually called.
        - anything else -- a plain function, e.g. ``motor.set_target_value``
          or your own procedure -- called exactly as given when its turn
          comes. No StepScan, no limit check (there's nothing scan-shaped
          to validate). Pass ``validate=`` (a zero-arg callable that raises
          to reject) for your own pre-flight check on one of these.

        `description` labels the ``QueueItem`` for status()/history, and --
        for the scan-shaped case, unless you already put your own
        ``description`` in `kwargs` -- also becomes the scan's own
        description.
        """
        is_scan_shaped = _accepts_start_immediately(method)
        kwargs = dict(kwargs)
        if is_scan_shaped:
            kwargs.setdefault("description", description)
            kwargs["start_immediately"] = False
        call = lambda: method(*args, **kwargs)
        item = QueueItem(
            call,
            description=description or getattr(method, "__name__", ""),
            queue=self,
        )
        item._is_scan_shaped = is_scan_shaped
        item._skip_limit_check = skip_limit_check or not is_scan_shaped
        item._validate = validate
        with self._wake:
            self._items.append(item)
            self._wake.notify_all()
        return item

    def remove(self, item_id):
        """Drop a still-pending item. Returns True if it was pending and got
        removed, False if it already started, already finished, or doesn't
        exist in this queue."""
        with self._lock:
            for item in self._items:
                if item.id == item_id:
                    if item.cancel():
                        self._items.remove(item)
                        self.history.append(item)
                        return True
                    return False
        return False

    # -- queue-level pause: don't start the NEXT item -----------------------

    def pause(self):
        with self._lock:
            self._paused = True

    def resume(self):
        with self._wake:
            self._paused = False
            self._wake.notify_all()

    def is_paused(self):
        with self._lock:
            return self._paused

    # -- currently-running item: pause/resume/stop IT, mid-flight ----------

    def pause_current(self):
        """Pause the scan running right now, at its next step boundary --
        unlike pause(), which only keeps the *next* item from starting."""
        item = self._current
        if item is not None and item.scan is not None:
            item.scan.pause()

    def resume_current(self):
        item = self._current
        if item is not None and item.scan is not None:
            item.scan.resume()

    def stop_current(self):
        """Abandon the currently-running item instead of resuming it."""
        item = self._current
        if item is not None and item.scan is not None:
            item.scan.request_stop()

    # -- status --------------------------------------------------------

    def status(self):
        with self._lock:
            return {
                "name": self.name,
                "paused": self._paused,
                "current": repr(self._current) if self._current else None,
                "pending": [repr(i) for i in self._items],
            }

    # -- store / resume --------------------------------------------------

    def save_paused(self, item, path):
        """Snapshot a paused item's remaining work to `path` as json. Only
        meaningful once `item.scan` exists and has actually stopped
        advancing (paused, e.g. via pause_current())."""
        if item.scan is None:
            raise ValueError("item has no scan yet (still pending) -- nothing to save")
        snap = item.scan.snapshot_remaining()
        snap["submitted_description"] = item.description
        Path(path).write_text(json.dumps(snap, indent=2, default=str))
        return path

    def resume_from_file(self, path, root, counters, scans_kwargs=None):
        """Rebuild and queue a StepScan for the *remaining* values saved by
        save_paused(). Adjustables are re-resolved by dotted name from
        `root` (e.g. the beamline namespace); pass the live `counters` you
        want to use -- ad hoc counters aren't addressable by name from a
        namespace the way adjustables are, so this doesn't try to guess."""
        snap = json.loads(Path(path).read_text())
        adjustables = [_resolve_dotted(root, n) for n in snap["adjustable_names"]]
        scans_kwargs = dict(scans_kwargs or {})
        scans_kwargs.setdefault("description", snap["description"])

        def call():
            from eco.acquisition.scan import StepScan

            return StepScan(
                adjustables,
                snap["values_remaining"],
                counters=counters,
                Npulses=snap["pulses_remaining"],
                gridspecs=snap["grid_specs"],
                **scans_kwargs,
            )

        item = QueueItem(
            call,
            description=f"resumed: {snap.get('submitted_description', '')}",
            queue=self,
        )
        item._is_scan_shaped = True
        item._skip_limit_check = False
        item._validate = None
        with self._wake:
            self._items.append(item)
            self._wake.notify_all()
        return item

    # -- worker loop -----------------------------------------------------

    def _run(self):
        while True:
            with self._wake:
                while (self._paused or not self._items) and not self._stop_worker:
                    self._wake.wait(timeout=0.5)
                if self._stop_worker:
                    return
                item = self._items.pop(0)
                self._current = item

            item.state = "validating"
            is_scan_shaped = getattr(item, "_is_scan_shaped", True)
            try:
                if item._validate is not None:
                    item._validate()
                if is_scan_shaped:
                    # start_immediately=False was baked in by submit(): this
                    # builds the StepScan (positions, gridspecs, counters --
                    # reads each adjustable's current value, but moves
                    # nothing) without running it yet.
                    scan = item._call()
                    if scan is None:
                        raise RuntimeError(
                            "the submitted method returned None instead of "
                            "the StepScan it built -- ScanQueue needs "
                            "Scans.xxx() to return the scan (as all of them "
                            "do) to validate and then run it"
                        )
                    item.scan = scan
                    if not item._skip_limit_check:
                        _check_limits(scan)
            except Exception as e:
                self._finish(item, exception=e)
                continue

            item.state = "running"
            try:
                if is_scan_shaped:
                    item.result = item.scan.scan_all()
                else:
                    # plain callable: nothing ran during "validating" above
                    # (there's no build-without-running for an arbitrary
                    # function), so this is where it actually happens.
                    item.result = item._call()
                self._finish(item)
            except Exception as e:
                traceback.print_exc()
                self._finish(item, exception=e)

    def _finish(self, item, exception=None):
        item.exception = exception
        item.state = "error" if exception is not None else "done"
        item._done_event.set()
        with self._lock:
            self._current = None
            self.history.append(item)
            del self.history[: -self.history_max]

    def shutdown(self):
        with self._wake:
            self._stop_worker = True
            self._wake.notify_all()
        self._worker.join(timeout=5)


def _check_limits(scan):
    """Pre-flight check: every planned position for every adjustable that
    exposes get_limits() -> (low, high) must be inside it. Skips adjustables
    without get_limits() (not every eco Adjustable has one) and, on a
    broken limits query, lets the scan through rather than blocking a scan
    that would otherwise have been fine -- a limits *query* failing isn't
    evidence the position is actually bad."""
    for adj, values in zip(scan.adjustables, zip(*scan._values_todo)):
        get_limits = getattr(adj, "get_limits", None)
        if not callable(get_limits):
            continue
        try:
            low, high = get_limits()
        except Exception:
            continue
        for v in values:
            if not (low <= v <= high):
                name = getattr(adj, "name", repr(adj))
                raise LimitViolation(
                    f"{name}: planned position {v} is outside its soft limits ({low}, {high})"
                )


def _resolve_dotted(root, dotted_path):
    obj = root
    for part in dotted_path.split("."):
        obj = getattr(obj, part)
    return obj


def _accepts_start_immediately(method):
    """Duck-types "is this a Scans method" for submit()'s auto-detection:
    does it accept a `start_immediately` argument? Works through
    functools.partial (inspect.signature unwraps it), which is how
    Scans._augment_docstrings shadows a method on an instance."""
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return "start_immediately" in sig.parameters


class ScanQueueContainer:
    """Named queues, auto-created on first access: ``scans.queues.default``,
    ``scans.queues.alignment``, ``scans.queues["alignment"]`` -- all
    equivalent ways to reach a lane. Queues here are private to this
    container (a fresh ``ScanQueueContainer`` per ``Scans`` instance, via
    ``Scans.queues``) -- independent from the module-level ``get_queue()``
    registry, and from another ``Scans`` instance's container, even if both
    happen to use the same lane name.
    """

    def __init__(self):
        self._queues = {}
        self._lock = threading.Lock()

    def __getitem__(self, name):
        with self._lock:
            q = self._queues.get(name)
            if q is None:
                q = ScanQueue(name=name)
                self._queues[name] = q
            return q

    def __getattr__(self, name):
        # only reached for names that aren't real attributes/methods below
        # (__getattribute__ already tried those first) -- so this can't
        # shadow names(), status(), pause_all(), etc.
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __iter__(self):
        with self._lock:
            return iter(list(self._queues.values()))

    def names(self):
        with self._lock:
            return sorted(self._queues.keys())

    def status(self):
        return {q.name: q.status() for q in self}

    def pause_all(self):
        for q in self:
            q.pause()

    def resume_all(self):
        for q in self:
            q.resume()

    def stop_all(self):
        """Abort every queue in this container: stop whatever's currently
        running (mid-flight, at its next step boundary) and drop every
        pending item. Each queue stays usable afterwards, just emptied."""
        for q in self:
            abort_queue(q)


_queues = {}
_queues_lock = threading.Lock()

# every ScanQueue ever constructed, private or named -- see the note in
# ScanQueue.__init__. Weakrefs so an abandoned private queue can still be
# garbage-collected.
_all_queues = []
_all_queues_lock = threading.Lock()


def get_queue(name="default"):
    """The named queue, creating it (and its worker thread) on first use.
    Two different names are two different queues, running concurrently --
    this is the explicit-sharing counterpart to ``Scans.queue`` (which
    gives each ``Scans`` instance its own *private* queue automatically,
    not going through this registry at all)."""
    with _queues_lock:
        q = _queues.get(name)
        if q is None:
            q = ScanQueue(name=name)
            _queues[name] = q
        return q


def all_queues():
    """Every ScanQueue currently alive in this process (private and named
    alike), pruning any that have since been garbage-collected."""
    with _all_queues_lock:
        alive = [r() for r in _all_queues]
        alive = [q for q in alive if q is not None]
        _all_queues[:] = [weakref.ref(q) for q in alive]
        return alive


def abort_queue(q):
    """Stop the currently-running item (if any) and drop every pending one,
    on this queue only. Does not stop the worker thread -- the queue is
    still usable afterwards, just emptied."""
    q.stop_current()
    with q._lock:
        for item in list(q._items):
            item.cancel()
        q._items.clear()


def abort_all_queues():
    for q in all_queues():
        abort_queue(q)


def handle_keyboard_interrupt(queue=None):
    """Ctrl-C landed while blocked in QueueItem.wait(). Ask whether to
    abort just `queue`, every queue in the process, or keep waiting --
    matching StepScan.scan_all()'s existing "question" pattern for the
    revert-to-initial prompt, rather than a silent, unrecoverable crash."""
    print("\nInterrupted.")
    label = queue.name if queue is not None else "?"
    try:
        choice = (
            input(
                f"Abort (a)ll queues, just (t)his one ({label!r}), "
                "or (c)ontinue waiting? [a/t/c] "
            )
            .strip()
            .lower()
        )
    except Exception:
        choice = "c"
    if choice == "a":
        abort_all_queues()
        print("Aborted all queues.")
    elif choice == "t" and queue is not None:
        abort_queue(queue)
        print(f"Aborted queue {queue.name!r}.")
    else:
        print("Continuing to wait.")
