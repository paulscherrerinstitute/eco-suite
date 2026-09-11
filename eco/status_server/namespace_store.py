"""Namespace-hosted status/monitor store - hosts the live eco namespace
(e.g. bernina) directly inside the server process, instead of a bare
alias/PV registry (see channel_registry.py / monitor_store.py).

Why this exists: a plain PV registry can only ever answer for channels it
knows the names of. It has no way to represent values backed by
AdjustableMemory/DetectorMemory (pure in-process Python values) or
AdjustableFS (JSON-file-backed settings) - both of which namespace.get_status()
includes. Matching namespace.get_status() 1:1 means walking the *live*
status_collection tree, which means the server must hold the real namespace
object, not just a list of channel names.

Lifecycle
---------
Construction never blocks: the namespace import + ``init_all()`` run on a
background thread, so an HTTP layer can be serving ``/health`` (reporting
``state="initializing"`` and live progress) from the first second. Callers
poll until ``state == "ready"``; ``wait_ready()`` does that in-process.
States are ``importing`` -> ``initializing`` -> ``ready``, plus
``reinitializing`` (a ready store rebuilding) and ``failed`` (the initial
import/init raised - the process stays up so the failure is reportable
over HTTP, and ``/admin/reinit`` can retry it).

``generation`` increments on every successful (re)build, so a client can
tell "the reinit I asked for has completed" apart from "it hasn't started
yet" without racing the state flag.

Trade-offs versus the channel_registry.py/monitor_store.py approach, found
by testing against the real bernina namespace, not assumed:

- Startup is slow and depends on external systems. Importing the module is
  cheap (components are lazy proxies), but namespace.init_all() takes
  minutes for the ~90 "required" bernina components, in part because some
  components authenticate to auxiliary machines over SSH during __init__.
  That is exactly why this is a long-lived server: the cost is paid once,
  centrally, instead of in every interactive session and at every
  ``Daq.init_namespace`` call.

- init_all() with max_workers>1 used to segfault the interpreter inside
  libca's CA-TCP-recv thread (confirmed via dmesg, not guessed) because
  each worker thread implicitly created its own CA context. That is fixed
  in eco itself now - ``Namespace._run_init_pass`` attaches every worker to
  one shared context via ``epics.ca.use_initial_context()`` - so this
  module can and does use several workers. Any *new* thread added here
  that touches Channel Access must do the same; ``_ca_thread`` wraps that.

- AdjustableMemory/DetectorMemory values are process-local. If this
  server runs as its own process (as opposed to being embedded in the
  interactive session), it holds its OWN instances of these objects -
  changes made through a *different* eco session (e.g. an interactive
  ipython session a scientist is using) are invisible to the server and
  vice versa. This is a fundamental limitation of hosting a second,
  separate copy of the namespace, not a bug to fix - see DESIGN.md.
"""

from __future__ import annotations

import importlib
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from eco.aliases.channel_lists import CHANNEL_LIST_CHANNELTYPES, compare_channel_lists
from eco.elements.adjustable import CallbackComposedValue
from eco.elements.protocols import Detector, MonitorableValueUpdate

logger = logging.getLogger(__name__)


IMPORTING = "importing"
INITIALIZING = "initializing"
READY = "ready"
REINITIALIZING = "reinitializing"
FAILED = "failed"

BUSY_STATES = (IMPORTING, INITIALIZING, REINITIALIZING)


class NotReady(Exception):
    """Raised when a snapshot is requested before the namespace is usable."""

    def __init__(self, state, detail=None):
        self.state = state
        self.detail = detail
        super().__init__(detail or f"namespace store is '{state}', not ready")


# Kept as an alias: the first prototype of this module raised
# ReinitInProgress and namespace_server/DESIGN.md referred to it by name.
class ReinitInProgress(NotReady):
    pass


def _ca_thread(target, *args, name=None, **kwargs):
    """Start a daemon thread that is attached to the shared CA context.

    Any thread in this process that touches Channel Access must attach to
    the one "initial context" rather than implicitly creating its own - see
    the module docstring.
    """

    def _wrapped():
        try:
            import epics.ca as ca

            ca.use_initial_context()
        except Exception:
            logger.warning("Could not attach thread to the shared CA context",
                           exc_info=True)
        target(*args, **kwargs)

    t = threading.Thread(target=_wrapped, name=name, daemon=True)
    t.start()
    return t


class NamespaceMonitorStore:
    def __init__(
        self,
        module_name: str,
        attr_name: str = "namespace",
        init_required_only: bool = True,
        names: list[str] | None = None,
        exclude_names: list[str] | None = None,
        use_monitors: bool = False,
        init_workers: int = 8,
        init_cycles: int = 4,
        init_retry_passes: int = 2,
        retry_workers: int = 1,
        monitor_workers: int = 16,
        read_workers: int = 20,
        start: bool = True,
    ):
        self.module_name = module_name
        self.attr_name = attr_name
        self.init_required_only = init_required_only
        # Explicit name list: the way to pin this server to a *known-good*
        # subset without touching namespace.required_names(), which for
        # bernina is an AdjustableFS backed by a file shared with every
        # interactive session (writing it here would change everyone's
        # startup set - see config.NamespaceServerConfig.names).
        self.configured_names = list(names) if names else None
        self.exclude_names = set(exclude_names or ())
        self.use_monitors = use_monitors
        self.init_workers = init_workers
        self.init_cycles = init_cycles
        self.init_retry_passes = init_retry_passes
        self.retry_workers = retry_workers
        self.monitor_workers = monitor_workers
        self.read_workers = read_workers

        self._lock = threading.RLock()
        self._ready = threading.Event()
        self.state = IMPORTING
        self.state_detail = "not started"
        self.state_since = time.time()
        self.generation = 0
        self.last_error = None
        self.last_init_seconds = None
        self.last_init_finished = None

        self.namespace = None
        self._target_names = set()
        self._monitors = {}  # full_name -> callback handle (monitored case)
        self._direct = {}    # full_name -> live Detector (read at snapshot time)
        self._monitor_objects = {}  # full_name -> object behind a monitor
        self._monitorable = {}  # full_name -> detector that can be CA-monitored
        self._channels = {}  # full_name -> pv/channel name, where known
        self._worker = None
        self._recordings = {}
        self._recordings_lock = threading.RLock()
        # (pgroup, run_number, key) -> {"values": {...}, "pushed_at": t} --
        # see push_status()'s docstring for what this is and why snapshot()
        # itself can never produce these values.
        self._pushed = {}

        if start:
            self.start()

    # -- state -------------------------------------------------------------

    def _set_state(self, state, detail=None):
        with self._lock:
            self.state = state
            self.state_detail = detail
            self.state_since = time.time()
            if state == READY:
                self._ready.set()
            else:
                self._ready.clear()
        logger.info("store state -> %s (%s)", state, detail)

    @property
    def busy(self):
        """Backwards-compatible flag: True whenever a snapshot would fail."""
        return self.state != READY

    @property
    def busy_reason(self):
        return None if self.state == READY else (self.state_detail or self.state)

    def wait_ready(self, timeout=None):
        return self._ready.wait(timeout)

    @property
    def target_names(self):
        """The namespace names this server initializes and serves."""
        return set(self._target_names)

    # -- setup -------------------------------------------------------------

    def start(self):
        """Kick off the (slow) import+init on a background thread."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                raise NotReady(self.state, "an initialization is already running")
            self._set_state(IMPORTING, f"importing {self.module_name}")
            self._worker = _ca_thread(
                self._build_worker, name="namespace-store-init"
            )
        return self._worker

    def _import_namespace(self):
        module = importlib.import_module(self.module_name)
        return getattr(module, self.attr_name)

    def _resolve_target_names(self, namespace):
        """Which namespace names this server initializes and serves.

        Deliberately does NOT write namespace.required_names() - see
        `configured_names` in __init__.
        """
        all_names = set(namespace.all_names)
        if self.configured_names is not None:
            wanted = set(self.configured_names)
            unknown = wanted - all_names
            if unknown:
                logger.warning(
                    "configured names not present in namespace %s (ignored): %s",
                    self.module_name, sorted(unknown),
                )
            target = wanted & all_names
        elif self.init_required_only:
            try:
                required = set(namespace.required_names())
            except Exception:
                logger.warning("could not read required_names()", exc_info=True)
                required = set()
            target = (required & all_names) or all_names
        else:
            target = all_names
        return target - self.exclude_names

    def _build_worker(self):
        t_start = time.time()
        try:
            namespace = self._import_namespace()
            with self._lock:
                self.namespace = namespace
                self._target_names = self._resolve_target_names(namespace)
            self._set_state(
                INITIALIZING,
                f"init_all over {len(self._target_names)} component(s)",
            )
            self._init_namespace(namespace)
            self._build_monitors()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.error("namespace store build failed", exc_info=True)
            self._set_state(FAILED, self.last_error)
            return
        self._finish_build(t_start, verb="built")

    def _init_namespace(self, namespace):
        # required_only=False + exclude_names is how a *specific* target set
        # is expressed without mutating namespace.required_names().
        exclude = sorted(set(namespace.all_names) - self._target_names)
        for attempt in range(1 + max(0, self.init_retry_passes)):
            # First pass parallel for throughput; retry passes serial. By
            # then only a handful of stragglers are left, and workers
            # colliding on a shared dependency is the whole reason they are
            # stragglers - one worker cannot collide with itself.
            workers = self.init_workers if attempt == 0 else self.retry_workers
            namespace.init_all(
                required_only=False,
                exclude_names=exclude,
                max_workers=workers,
                background=False,
                silent=True,
                N_cycles=self.init_cycles,
            )
            stuck = self._names_never_really_attempted(namespace)
            if not stuck:
                break
            # These did not *fail*: they timed out waiting for a concurrent
            # worker to finish building the same component (init_timeout is
            # 30 s per component, and several bernina devices legitimately
            # take longer than that), and init_all gave up on them without
            # ever recording a real error. Retrying them - now that whatever
            # they collided with is done - recovers components that would
            # otherwise be missing from every snapshot for the life of the
            # process. Measured on bernina: att, att_usd, kb and xrd land
            # here on some runs and not others, purely by scheduling luck.
            logger.info(
                "init retry pass %d for %d name(s) that only timed out "
                "waiting on a concurrent build: %s",
                attempt + 1, len(stuck), sorted(stuck),
            )
            namespace.move_failed_to_lazy(*stuck)

    @staticmethod
    def _names_never_really_attempted(namespace):
        """Failed names that carry no genuine error.

        Three shapes, all meaning "this was never actually built", as opposed
        to "this device is broken":

        * a GivenUpInitialisationError - init_all's `giveup_failed` moves
          whatever is still lazy at the end of the pass into failed_items and
          records this, which is where a name lands when the retry loop hit
          its cycle cap;
        * no recorded exception at all - the same case before init_all
          started recording a reason for it; kept so a namespace populated by
          older code (or by anything else writing failed_items directly) is
          still retried rather than silently written off;
        * an IsInitialisingError - it timed out waiting for another worker
          that was building the same component.

        Anything with a real exception (including
        IncompleteInitialisationError, which still leaves a usable partial
        object) is left alone: retrying it just costs time.
        """
        from eco.utilities.config import (
            GivenUpInitialisationError,
            IsInitialisingError,
        )

        out = set()
        for name in set(namespace.failed_names):
            exc = namespace.failed_items_excpetion.get(name)
            if exc is None or isinstance(
                exc, (IsInitialisingError, GivenUpInitialisationError)
            ):
                out.add(name)
        return out

    # -- monitors ----------------------------------------------------------

    def _status_detectors(self):
        out = []
        for ts in self.namespace.status_collection.get_list():
            if not isinstance(ts, Detector):
                continue
            # base=None matches Daq.append_start_status_to_scan's
            # namespace.get_status(base=None, ...) key naming exactly, so a
            # snapshot from here is a drop-in replacement for that dict.
            try:
                full_name = ts.alias.get_full_name(base=None)
            except Exception:
                full_name = getattr(ts, "name", repr(ts))
            out.append((full_name, ts))
        return out

    def _build_monitors(self):
        """Set up the CA-monitor cache, if this store was configured for it.

        Off by default: snapshots go through namespace.get_status(), which
        already fans out over its own thread pool and is the exact call the
        daq client makes today, so the server answers with values obtained
        the same way rather than from a second, parallel caching mechanism
        that could drift from it. The monitor path stays available
        (use_monitors=True) for the case where snapshot latency, rather than
        namespace initialization, turns out to be the bottleneck.
        """
        self._stop_all_monitors()
        monitors, direct, channels = {}, {}, {}
        candidates = []
        if not self.use_monitors:
            monitorable = {}
            for full_name, ts in self._status_detectors():
                try:
                    channels[full_name] = ts.alias.channel
                except Exception:
                    pass
                direct[full_name] = ts
                if isinstance(ts, MonitorableValueUpdate):
                    monitorable[full_name] = ts
            with self._lock:
                self._monitors = {}
                self._direct = direct
                self._channels = channels
                self._monitorable = monitorable
            logger.info(
                "live get_status mode: %d status detector(s), %d monitorable",
                len(direct), len(monitorable),
            )
            return
        for full_name, ts in self._status_detectors():
            try:
                channels[full_name] = ts.alias.channel
            except Exception:
                pass
            if self.use_monitors and isinstance(ts, MonitorableValueUpdate):
                candidates.append((full_name, ts))
            else:
                # Not monitorable - AdjustableMemory, DetectorMemory,
                # AdjustableFS and similar land here. These are
                # process-local/file reads, not CA gets, so reading them
                # fresh on every snapshot is cheap.
                direct[full_name] = ts

        # Monitor setup does a blocking pv.get() per channel (CallbackEpics.
        # start(add_current_value=True)), so a disconnected channel costs a
        # full CA timeout. Fan that out - Namespace.get_status() already
        # reads concurrently, so this is not a new threading assumption.
        def _setup(item):
            full_name, ts = item
            try:
                mon = ts.set_current_value_callback(func="latest")
                if mon is None:
                    return full_name, ts, None
                mon.start()
                return full_name, ts, mon
            except Exception:
                logger.warning(
                    "Could not start a monitor for %s, falling back to a "
                    "direct read at snapshot time", full_name, exc_info=True,
                )
                return full_name, ts, None

        if candidates:
            with ThreadPoolExecutor(
                max_workers=self.monitor_workers, initializer=_ca_initializer
            ) as exc:
                for full_name, ts, mon in exc.map(_setup, candidates):
                    if mon is None:
                        direct[full_name] = ts
                    else:
                        monitors[full_name] = mon
                        # keep the object too, for the "monitor never fired"
                        # fallback in snapshot()
                        self._monitor_objects[full_name] = ts

        with self._lock:
            self._monitors = monitors
            self._direct = direct
            self._channels = channels
            self._monitorable = {
                n: o for n, o in list(self._monitor_objects.items())
            }
        logger.info(
            "monitors built: %d monitored, %d direct-read", len(monitors), len(direct)
        )

    def _stop_all_monitors(self):
        for name, mon in list(self._monitors.items()):
            try:
                mon.stop()
            except Exception:
                logger.warning("Error stopping monitor %s", name, exc_info=True)
        self._monitors = {}
        self._monitor_objects = {}

    # -- serving -----------------------------------------------------------

    def snapshot(self, allow_stale=False, max_workers=None):
        """Return the same dict shape as namespace.get_status(base=None).

        allow_stale: answer even while a reinit is running. Off by default,
        and not just because the values would be a mix of old and new: a
        reinit rebuilds the status_collection this walks, so reading it
        concurrently can also fail outright. Only reach for it when a stale
        answer is genuinely better than none.
        """
        if self.state != READY and not (allow_stale and self.namespace is not None):
            raise NotReady(self.state, self.state_detail)

        t0 = time.time()

        if not self.use_monitors:
            # Default path: exactly what Daq.append_start_status_to_scan
            # calls today, run here instead of in the client session -
            # including get_status()'s own ThreadPoolExecutor fan-out. The
            # win is not a faster fan-out, it is that this process already
            # holds an initialized namespace, so the client pays neither
            # init_all() nor the import.
            snap = self.namespace.get_status(
                base=None,
                raise_on_incomplete=False,
                threads=True,
                max_workers=int(max_workers or self.read_workers),
            )
            snap["snapshot_seconds"] = time.time() - t0
            snap["read_workers"] = int(max_workers or self.read_workers)
            snap["generation"] = self.generation
            snap["mode"] = "get_status"
            return snap

        status, status_times = {}, {}
        stale = []

        for name, mon in list(self._monitors.items()):
            data = getattr(mon, "data", None) or {}
            if data.get("timestamp") is None and data.get("value") is None:
                # Monitor is set up but never delivered a value (channel
                # disconnected at setup time and since). get_status() would
                # still attempt a live read here, so do the same rather than
                # silently reporting None.
                stale.append(name)
                continue
            status[name] = data.get("value")
            status_times[name] = 0.0

        def _read(item):
            name, obj = item
            t = time.time()
            try:
                return name, obj.get_current_value(), time.time() - t
            except Exception:
                logger.debug("Could not read %s directly", name, exc_info=True)
                return name, None, time.time() - t

        to_read = list(self._direct.items()) + [
            (n, self._monitor_objects[n]) for n in stale if n in self._monitor_objects
        ]
        if to_read:
            with ThreadPoolExecutor(
                max_workers=self.read_workers, initializer=_ca_initializer
            ) as exc:
                for name, value, dt in exc.map(_read, to_read):
                    status[name] = value
                    status_times[name] = dt

        return {
            "status": status,
            "status_channels": dict(self._channels),
            "status_times": status_times,
            "selections": {},
            "snapshot_seconds": time.time() - t0,
            "n_stale_monitors": len(stale),
            "generation": self.generation,
            "mode": "monitors",
        }

    # -- client-pushed values -----------------------------------------------
    #
    # Some values have no CA channel at all -- e.g. scans.acquiring_scan.*,
    # built from DetectorMemory (eco.elements.detector), whose Alias is
    # constructed with channel=None. snapshot()'s monitored path only ever
    # tracks what the channel registry knows about, and the channel registry
    # itself is built from Alias.get_all(), which only returns an alias
    # `if self.channel:` (eco/aliases/aliases.py) -- so this store can never
    # see such values by polling, no matter how it is configured. The client
    # session that actually resolved and used the object already has the
    # real value; push_status() lets it hand that over instead.

    def push_status(self, pgroup, run_number, key, values):
        """Record `values` to be merged into the next matching
        /status/job read (see namespace_server.status_job), replacing any
        not-yet-collected push for the same (pgroup, run_number, key) --
        the caller always sends its full current view, not a delta."""
        if not isinstance(values, dict):
            raise TypeError(f"values must be a dict, got {type(values).__name__}")
        with self._lock:
            self._pushed[(str(pgroup), int(run_number), str(key))] = {
                "values": dict(values),
                "pushed_at": time.time(),
            }

    def pop_pushed_status(self, pgroup, run_number, key, max_age=300.0):
        """Consume and return the values pushed for this (pgroup,
        run_number, key), or {} if nothing was pushed or the push is older
        than max_age. The age cap matters because a push is not guaranteed
        to ever be collected (an aborted scan, a client that crashed right
        after pushing) -- without it, a stale push would sit here and
        silently attach itself to a later, unrelated job read at the same
        key once someone eventually asks."""
        try:
            k = (str(pgroup), int(run_number), str(key))
        except (TypeError, ValueError):
            return {}
        with self._lock:
            entry = self._pushed.pop(k, None)
        if entry is None:
            return {}
        if time.time() - entry["pushed_at"] > max_age:
            return {}
        return entry["values"]

    def connection_report(self):
        ns = self.namespace
        n_init = n_failed = None
        failed = []
        failed_required = []
        if ns is not None:
            try:
                n_init = len(self._target_names & set(ns.initialized_names))
                failed = sorted(self._target_names & set(ns.failed_names))
                n_failed = len(failed)
                # A failed component that is in required_names() is the one
                # a client has to be told about loudly: the setup is not
                # supposed to fail those, so one of them missing means the
                # status this server serves is incomplete in a way that
                # matters, not merely in a way that is expected.
                required = set(ns.required_names())
                failed_required = sorted(set(failed) & required)
            except Exception:
                logger.debug("could not compute init progress", exc_info=True)
        return {
            "state": self.state,
            "state_detail": self.state_detail,
            "state_seconds": time.time() - self.state_since,
            "ready": self.state == READY,
            "generation": self.generation,
            "n_monitored": len(self._monitors),
            "n_direct_read": len(self._direct),
            "n_monitorable": len(self._monitorable),
            "recordings": self.list_recordings(),
            "n_target_names": len(self._target_names),
            "n_initialized": n_init,
            "n_failed": n_failed,
            "failed_names": failed,
            "failed_required": failed_required,
            "n_failed_required": len(failed_required),
            "last_error": self.last_error,
            "last_init_seconds": self.last_init_seconds,
            "last_init_finished": self.last_init_finished,
        }

    def failure_details(self):
        ns = self.namespace
        if ns is None:
            return {}
        out = {}
        for name in sorted(self._target_names & set(ns.failed_names)):
            try:
                out[name] = repr(ns.failed_items_excpetion.get(name))[:1000]
            except Exception:
                out[name] = "<no exception recorded>"
        return out

    def compare_channels(self, list_names=None):
        """Compare this store's initialized namespace against the DAQ's
        recorded-channel lists (channels_JF/channels_BS/channels_BSCAM) --
        see eco.aliases.channel_lists. No CA traffic: the alias tree and
        the recorded lists (AdjustableFS, JSON-file backed) are both
        already in-process, so this is a fast, synchronous in-memory
        computation, same character as /aliases.

        A recorded-list name not yet in ``ns.all_names`` (e.g. excluded
        from this server's target set) is simply left out of the result
        rather than raising -- same "skip, don't fabricate" rule
        compare_channel_lists documents for a list it could not resolve.
        """
        if self.state != READY:
            raise NotReady(self.state, self.state_detail)
        ns = self.namespace
        if ns is None:
            raise NotReady(self.state, "no namespace")
        list_names = list(list_names) if list_names else list(CHANNEL_LIST_CHANNELTYPES)
        channeltypes = [
            CHANNEL_LIST_CHANNELTYPES[n] for n in list_names
            if n in CHANNEL_LIST_CHANNELTYPES
        ]
        alias_list = ns.alias.get_all(channeltypes=channeltypes)
        recorded_by_list = {}
        for name in list_names:
            if name not in CHANNEL_LIST_CHANNELTYPES or name not in ns.all_names:
                continue
            try:
                recorded_by_list[name] = list(ns.get_obj(name).get_current_value())
            except Exception:
                logger.warning("compare_channels: could not read '%s'", name,
                               exc_info=True)
        return compare_channel_lists(alias_list, recorded_by_list, list_names=list_names)

    # -- recording ---------------------------------------------------------

    def monitorable_names(self):
        return sorted(self._monitorable)

    def start_recording(self, recording_id, names=None, mode="all",
                        min_interval=0.0, sample_interval=0.1,
                        max_points_per_channel=100_000,
                        max_value_elements=None,
                        subscription_mask=True, pgroup=None, run_number=None):
        """Start monitoring every monitorable status detector (or `names`).

        See RecordingSession for what the modes do. Refused while the store
        is not ready: attaching monitors to objects a reinit is about to
        tear down would leave dangling callbacks.

        pgroup/run_number are optional and purely for later cross-
        referencing: a status capture for the same (pgroup, run_number) can
        then opportunistically backfill this recording's still-empty
        channels from its own snapshot - see backfill_running_recordings.
        """
        if self.state != READY:
            raise NotReady(self.state, self.state_detail)
        with self._recordings_lock:
            existing = self._recordings.get(recording_id)
            if existing is not None and existing.is_running:
                raise ValueError(f"recording '{recording_id}' is already running")
            if names is None:
                selected = list(self._monitorable.items())
            else:
                unknown = [n for n in names if n not in self._monitorable]
                if unknown:
                    raise KeyError(
                        f"not monitorable (or unknown): {unknown[:10]}"
                        + (" ..." if len(unknown) > 10 else "")
                    )
                selected = [(n, self._monitorable[n]) for n in names]
            detectors = [
                (name, obj, self._channels.get(name)) for name, obj in selected
            ]
            try:
                required_component_names = set(self.namespace.required_names())
            except Exception:
                logger.debug("could not read required_names() for recording %s",
                             recording_id, exc_info=True)
                required_component_names = set()
            session = RecordingSession(
                recording_id,
                detectors,
                mode=mode,
                min_interval=min_interval,
                sample_interval=sample_interval,
                max_points_per_channel=max_points_per_channel,
                max_value_elements=max_value_elements,
                subscription_mask=subscription_mask,
                pgroup=pgroup,
                run_number=run_number,
                required_component_names=required_component_names,
            )
            session.start()
            self._recordings[recording_id] = session
            return session

    def backfill_running_recordings(self, pgroup, run_number, status,
                                    status_times=None):
        """Opportunistically fill in a single value for any still-empty
        channel of every *running* recording started for this (pgroup,
        run_number), from a status snapshot that was already being taken
        for other reasons - see RecordingSession.backfill_from_status.

        Best-effort and silent by design: called from a status capture's
        own code path, where a bug here must not turn a successful status
        capture into a failed one. Returns the number of recordings
        touched (not the number of channels filled), mainly for tests.
        """
        if pgroup is None or run_number is None or not status:
            return 0
        try:
            run_number = int(run_number)
        except (TypeError, ValueError):
            return 0
        touched = 0
        with self._recordings_lock:
            sessions = list(self._recordings.values())
        for session in sessions:
            if not session.is_running:
                continue
            if session.pgroup != pgroup or session.run_number != run_number:
                continue
            try:
                session.backfill_from_status(status, status_times)
                touched += 1
            except Exception:
                logger.debug("could not backfill recording %s from status",
                             session.recording_id, exc_info=True)
        return touched

    def stop_recording(self, recording_id):
        with self._recordings_lock:
            session = self._recordings.get(recording_id)
            if session is None:
                raise KeyError(f"no such recording '{recording_id}'")
            if not session.is_running:
                raise ValueError(f"recording '{recording_id}' is already stopped")
            data = session.stop()
        return {
            **session.report(),
            "channels": {n: session.channels.get(n) for n in data},
            "data": data,
        }

    def recording_report(self, recording_id, with_channels=False, with_size=False,
                         size_top_n=None):
        session = self._recordings.get(recording_id)
        if session is None:
            raise KeyError(f"no such recording '{recording_id}'")
        rep = session.report(with_channels=with_channels)
        if with_size:
            rep["size_per_channel"] = session.size_report(top_n=size_top_n)
        return rep

    def list_recordings(self):
        with self._recordings_lock:
            return [s.report() for s in self._recordings.values()]

    def drop_recording(self, recording_id):
        """Forget a finished recording, freeing its buffers."""
        with self._recordings_lock:
            session = self._recordings.get(recording_id)
            if session is None:
                raise KeyError(f"no such recording '{recording_id}'")
            if session.is_running:
                raise ValueError(f"recording '{recording_id}' is still running")
            del self._recordings[recording_id]

    def stop_all_recordings(self):
        with self._recordings_lock:
            running = [s for s in self._recordings.values() if s.is_running]
        for session in running:
            try:
                session.stop()
            except Exception:
                logger.warning("could not stop recording %s", session.recording_id,
                               exc_info=True)
        return [s.recording_id for s in running]

    # -- reinit ------------------------------------------------------------

    def start_reinit(self, mode="failed", names=None, reload_modules=False,
                     new_names=None):
        """Rebuild namespace content in the background.

        mode:
          "failed"  - rebuild only the components that failed (default; the
                      common "the IOC is back up now" case).
          "names"   - rebuild exactly `names`.
          "full"    - rebuild every component in the target set.
          "init"    - do not rebuild anything already built; just run
                      init_all() again over the target set, which picks up
                      names that were never initialized (e.g. after
                      `new_names` widened the set).
          "reimport"- throw the whole namespace away: drop every `eco.*`
                      module from sys.modules, re-import, and initialize a
                      brand-new Namespace object from current source. This
                      is the in-process approximation of "kill it and start
                      over". It genuinely re-runs the assembly module
                      (bernina.py), which `reinitialize(reload_modules=True)`
                      deliberately refuses to do - but it cannot unload the
                      *old* code that live objects still reference, and the
                      previous namespace's CA channels and device threads
                      are not reclaimed. When that matters (edits to eco's
                      core, a namespace left in a bad state, or simply
                      wanting a guaranteed-clean process), restart the
                      process instead - see namespace_server's
                      /admin/restart, which is what the client's
                      reinit(mode="restart") uses.

        reload_modules: importlib.reload() each rebuilt component's defining
        module first, so source edits are picked up. Namespace.reinitialize()
        deliberately refuses this for classes defined in the namespace's own
        root module (reloading bernina.py would re-run the whole beamline
        assembly script) - for those, and for changes to eco's core, use the
        process-level restart endpoint instead.

        new_names: replace the served/initialized target set (see
        `configured_names`) before rebuilding.

        Raises NotReady synchronously if a build is already running, so the
        REST layer can answer 409 immediately.
        """
        with self._lock:
            if self.state in BUSY_STATES:
                raise NotReady(self.state, self.state_detail)
            if self.namespace is None:
                # Initial build failed before it produced a namespace: the
                # only meaningful retry is a full start() from scratch.
                return self.start()
            self._set_state(REINITIALIZING, f"reinit mode={mode}")
            self._worker = _ca_thread(
                self._reinit_worker,
                mode, names, reload_modules, new_names,
                name="namespace-store-reinit",
            )
        return self._worker

    def _reinit_worker(self, mode, names, reload_modules, new_names):
        t_start = time.time()
        try:
            if mode == "reimport":
                # Nothing of the old namespace survives, so tearing its
                # monitors down first is all the cleanup that is possible.
                self.stop_all_recordings()
                self._stop_all_monitors()
                self._reimport_namespace()
                if new_names is not None:
                    self.configured_names = list(new_names)
                ns = self.namespace
                self._target_names = self._resolve_target_names(ns)
                self._init_namespace(ns)
                self._build_monitors()
                self._finish_build(t_start)
                return

            ns = self.namespace
            if new_names is not None:
                self.configured_names = list(new_names)
                self._target_names = self._resolve_target_names(ns)
            # Monitors and recordings hold callbacks on PVs owned by
            # objects that reinitialize() is about to tear down - drop them
            # first. A running recording is stopped, not silently carried
            # over: its remaining channels would be attached to dead
            # objects, so the honest thing is to end it.
            stopped = self.stop_all_recordings()
            if stopped:
                logger.warning("reinit stopped running recording(s): %s", stopped)
            self._stop_all_monitors()

            if mode == "failed":
                targets = sorted(self._target_names & set(ns.failed_names))
            elif mode == "names":
                targets = sorted(set(names or ()) & set(ns.all_names))
            elif mode == "full":
                targets = sorted(self._target_names & set(ns.all_names))
            elif mode == "init":
                targets = []
            else:
                raise ValueError(f"unknown reinit mode {mode!r}")

            if targets:
                logger.info("reinitializing %d component(s)", len(targets))
                ns.reinitialize(
                    *targets, verbose=False, raise_errors=False,
                    reload_modules=reload_modules,
                )
            # Always follow with an init_all() pass: it is a no-op for
            # already-initialized names and picks up anything still lazy.
            self._init_namespace(ns)
            self._build_monitors()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.error("Background reinit failed", exc_info=True)
            # A failed reinit still leaves a usable (if stale) namespace, so
            # go back to READY rather than FAILED - but try to rebuild the
            # monitors we tore down, otherwise every snapshot would be empty.
            try:
                self._build_monitors()
            except Exception:
                logger.error("could not rebuild monitors after a failed reinit",
                             exc_info=True)
                self._set_state(FAILED, self.last_error)
                return
            self._set_state(READY, f"reinit failed: {self.last_error}")
            return
        self._finish_build(t_start, verb="rebuilt")

    def _finish_build(self, t_start, verb="rebuilt"):
        self.last_init_seconds = time.time() - t_start
        self.last_init_finished = time.time()
        with self._lock:
            self.generation += 1
        self._set_state(
            READY,
            f"generation {self.generation}, {verb} in {self.last_init_seconds:.1f} s",
        )

    def _reimport_namespace(self):
        """Drop every eco module and re-import, yielding a fresh Namespace."""
        with self._lock:
            self.namespace = None
            self._monitors, self._direct, self._channels = {}, {}, {}
            self._monitor_objects = {}
        purged = purge_eco_modules()
        importlib.invalidate_caches()
        logger.info("reimport: purged %d eco module(s) from sys.modules", purged)
        namespace = self._import_namespace()
        with self._lock:
            self.namespace = namespace
            self._target_names = self._resolve_target_names(namespace)


# ---------------------------------------------------------------------------
# recording


class RecordingSession:
    """One "monitor everything monitorable while this run happens" job.

    Attaches a CA monitor to every :class:`MonitorableValueUpdate` detector
    in the namespace and buffers updates in memory until stopped, so the
    result can be written as one ``escape`` ``ArrayTimestamps`` per channel
    (see storage.write_monitor_recording).

    Three modes, which differ in what the per-update callback does. That
    matters more than it looks: every CA update crosses the C-to-Python
    boundary on libca's receive thread and takes the GIL, so with thousands
    of channels the *callback body* is the only part of the cost this
    process controls.

    ``all``
        Append every update. Truest record, unbounded memory - a previous
        prototype left monitoring a single ~100 Hz PV overnight and the OOM
        killer took out unrelated system processes (see DESIGN.md's incident
        log), hence ``max_points_per_channel``.
    ``throttle`` (``min_interval``)
        Append only if at least ``min_interval`` has passed since this
        channel's last stored point. Cuts stored data and everything
        downstream of it, but the callback still runs for every update - it
        has to, in order to decide to drop it.
    ``sample`` (``sample_interval``)
        The callback only overwrites a per-channel "latest" slot; a sampler
        thread copies the slots into the buffers on a fixed grid, but only
        where the slot actually changed since the last tick. Per-update work
        is constant and minimal, and a fast channel is decimated to the grid
        rate. What is lost is the exact timing of the updates that were
        skipped.

        The change check is not an optimization, it is what makes the mode
        usable: without it, sampling *every* channel on the grid upsamples
        the slow majority. Measured on bernina - 7 719 channels of which
        7 440 update slower than 0.1 Hz - a plain 10 Hz grid produced 14.8
        million points against 1.0 million for ``all``, i.e. 15x *more* data
        from the mode meant to produce less.

    None of the three reduces how often the IOC sends. ``subscription_mask``
    is the only knob here that does - see start().
    """

    def __init__(
        self,
        recording_id,
        detectors,
        mode="all",
        min_interval=0.0,
        sample_interval=0.1,
        max_points_per_channel=100_000,
        max_value_elements=None,
        subscription_mask=True,
        pgroup=None,
        run_number=None,
        required_component_names=None,
    ):
        if mode not in ("all", "throttle", "sample"):
            raise ValueError(f"unknown recording mode {mode!r}")
        self.recording_id = recording_id
        self.detectors = detectors  # [(full_name, obj, channel)]
        # Only used to find this session again from a status capture for the
        # same run (see backfill_from_status / NamespaceMonitorStore
        # .backfill_running_recordings) - optional, purely informational
        # otherwise.
        self.pgroup = pgroup
        self.run_number = run_number
        # Top-level namespace component names (namespace.required_names())
        # - a channel whose full_name starts with one of these and failed to
        # attach is reported separately in report()'s failed_required, the
        # same distinction /health's failed_required already makes for init
        # failures. A full_name's top-level component is everything before
        # its first ".", matching Alias.get_full_name(base=None)'s own
        # dotted convention.
        self._required_names = set(required_component_names or ())
        self.mode = mode
        self.min_interval = float(min_interval)
        self.sample_interval = float(sample_interval)
        self.max_points_per_channel = int(max_points_per_channel)
        # Drop updates whose value has more elements than this. Aimed at
        # waveform channels, which dominate the output file by an order of
        # magnitude: in a 3-minute bernina recording, two 8000-sample
        # digitizer waveforms were 204 MB of a 239 MB file, while all 7 677
        # scalar channels together came to 8 MB. None keeps everything.
        self.max_value_elements = (
            int(max_value_elements) if max_value_elements else None
        )
        self.subscription_mask = subscription_mask

        self.started_at = None
        self.stopped_at = None
        self.channels = {name: channel for name, _, channel in detectors}

        self._lock = threading.Lock()
        self._buffers = {}
        self._latest = {}
        self._last_stored_at = {}
        self._monitors = {}
        self._sampler = None
        self._sampler_stop = threading.Event()
        # stop() hands the buffers away, so the final counts have to be kept
        # for report() rather than recomputed from what is left behind.
        self._final_counts = None

        # plain ints touched from the CA callback thread(s); exact counts
        # are not worth a lock in the hot path, and they are only ever
        # reported, never used for control flow.
        self.n_updates = 0
        self.n_stored = 0
        self.n_dropped_throttle = 0
        self.n_dropped_full = 0
        self.n_dropped_large = 0
        self.n_attach_failed = 0
        self.n_attached = 0
        self.n_seeded = 0
        self.n_backfilled = 0
        # Names (not just a count) of channels that failed to attach - kept
        # so NamespaceMonitorStore.start_recording can classify them against
        # namespace.required_names(), same as /health's failed_required.
        self._attach_failed_names = []

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        self.started_at = time.time()
        for name, obj, _ in self.detectors:
            self._buffers[name] = {"values": [], "timestamps": []}
            try:
                mon = obj.set_current_value_callback(func=self._make_callback(name))
                if mon is None:
                    self.n_attach_failed += 1
                    self._attach_failed_names.append(name)
                    continue
                # with_ctrlvars=False always: get_ctrlvars() (units, limits,
                # precision) is a blocking CA round trip per channel
                # regardless of monitor state - a get-storm at namespace
                # scale either way.
                #
                # add_current_value, though, only costs that same round trip
                # for a channel that would need one anyway: pyepics'
                # PV.get_with_metadata() returns the cached last value with
                # zero network traffic when auto_monitor is already True and
                # the channel is connected (see ca_tuning's "Resolved
                # 2026-09-06" note - measured at ~0.017 ms there). That is
                # true for the large majority of channels under
                # ca_tuning.make_pv()'s default policy, so seed those for
                # free rather than leaving every channel's first point to
                # chance; a demoted "fast" channel or one not yet connected
                # would make this a real blocking get, so those still get no
                # seed here - see backfill_from_status for how they can get
                # one anyway, opportunistically, without new CA traffic.
                pv = getattr(mon, "pv", None)
                seed = bool(
                    pv is not None
                    and getattr(pv, "auto_monitor", False)
                    and getattr(pv, "connected", False)
                )
                if not seed and isinstance(mon, CallbackComposedValue):
                    # AdjustableVirtual/DetectorVirtual: its seed is a
                    # get_current_value() recompute from its own already-
                    # monitored parents (mon.start() below only reaches
                    # here because set_current_value_callback() already
                    # required every parent to be a MonitorableValueUpdate),
                    # not a CA get of its own - free in the common case. Not
                    # strictly free if a parent happens to be individually
                    # demoted/disconnected right now, but that costs at
                    # most that one parent's own get, bounded by how many
                    # direct parents one computed value has (a handful),
                    # nowhere near the per-channel storm this policy exists
                    # to avoid - simpler to always seed these than to
                    # recurse the same connected/auto_monitor check through
                    # an arbitrary composition tree.
                    seed = True
                mon.start(
                    add_current_value=seed,
                    with_ctrlvars=False,
                    auto_monitor=self.subscription_mask,
                )
                self._monitors[name] = mon
                if seed:
                    self.n_seeded += 1
            except Exception:
                self.n_attach_failed += 1
                self._attach_failed_names.append(name)
                logger.debug("could not attach a recording monitor to %s", name,
                             exc_info=True)
        if self.mode == "sample":
            self._sampler_stop.clear()
            self._sampler = threading.Thread(
                target=self._sampler_loop, name=f"rec-sampler-{self.recording_id}",
                daemon=True,
            )
            self._sampler.start()
        self.n_attached = len(self._monitors)
        logger.info(
            "recording '%s' started: %d/%d channels monitored, mode=%s",
            self.recording_id, self.n_attached, len(self.detectors), self.mode,
        )
        return self

    def _make_callback(self, name):
        buffers = self._buffers
        mode = self.mode

        if mode == "sample":
            latest = self._latest

            def _on_update(pvname=None, value=None, timestamp=None, **kwargs):
                # deliberately the cheapest body that can be written: one
                # tuple, one dict store, one counter bump. Everything else
                # happens on the sampler thread.
                latest[name] = (value, timestamp)
                self.n_updates += 1

            return _on_update

        if mode == "throttle":
            min_interval = self.min_interval
            last = self._last_stored_at

            def _on_update(pvname=None, value=None, timestamp=None, **kwargs):
                self.n_updates += 1
                now = time.time()
                if now - last.get(name, 0.0) < min_interval:
                    self.n_dropped_throttle += 1
                    return
                last[name] = now
                self._append(buffers[name], value, timestamp)

            return _on_update

        def _on_update(pvname=None, value=None, timestamp=None, **kwargs):
            self.n_updates += 1
            self._append(buffers[name], value, timestamp)

        return _on_update

    def _append(self, buf, value, timestamp):
        if len(buf["values"]) >= self.max_points_per_channel:
            self.n_dropped_full += 1
            return
        if self.max_value_elements is not None:
            n = getattr(value, "size", None)
            if n is None:
                try:
                    n = len(value)
                except TypeError:
                    n = 1
            if n > self.max_value_elements:
                self.n_dropped_large += 1
                return
        buf["values"].append(value)
        buf["timestamps"].append(
            timestamp if timestamp is not None else time.time()
        )
        self.n_stored += 1

    def _sampler_loop(self):
        latest = self._latest
        buffers = self._buffers
        interval = self.sample_interval
        last_ts = {}
        next_tick = time.time()
        while not self._sampler_stop.is_set():
            next_tick += interval
            for name, slot in list(latest.items()):
                value, timestamp = slot
                # Only store a channel that actually got an update since the
                # last tick - see the class docstring for why sampling
                # everything unconditionally is worse than not sampling at
                # all. Compared on the CA timestamp: it is what identifies an
                # update, and unlike the value it is always scalar (a
                # waveform value would make `==` return an array).
                if timestamp is not None and last_ts.get(name) == timestamp:
                    continue
                last_ts[name] = timestamp
                self._append(buffers[name], value, timestamp)
            delay = next_tick - time.time()
            if delay > 0:
                self._sampler_stop.wait(delay)
            else:
                # fell behind; resync rather than spin trying to catch up
                next_tick = time.time()

    def stop(self):
        if self._sampler is not None:
            self._sampler_stop.set()
            self._sampler.join(timeout=5)
            self._sampler = None
        for name, mon in list(self._monitors.items()):
            try:
                mon.stop()
            except Exception:
                logger.debug("error stopping recording monitor %s", name, exc_info=True)
        self._monitors = {}
        self.stopped_at = time.time()
        with self._lock:
            self._final_counts = {
                name: len(b["values"]) for name, b in self._buffers.items()
            }
            buffers, self._buffers = self._buffers, {
                name: {"values": [], "timestamps": []} for name in self._buffers
            }
        # Hand the buffers over rather than copying them: at namespace scale
        # a copy transiently doubles the memory this recording is holding,
        # which is the one resource a long recording is short of.
        #
        # The truncation is not paranoia: a callback that fired between the
        # value append and the timestamp append (or the reverse) leaves the
        # two lists one element apart, and ArrayTimestamps would then pair
        # values with the wrong timestamps. Monitors are stopped just above,
        # so the window is small, but "small" is not "closed".
        out = {}
        for name, b in buffers.items():
            n = min(len(b["values"]), len(b["timestamps"]))
            if n:
                out[name] = {"values": b["values"][:n], "timestamps": b["timestamps"][:n]}
        return out

    @property
    def is_running(self):
        return self.started_at is not None and self.stopped_at is None

    def backfill_from_status(self, status, status_times=None):
        """Give any channel that still has zero recorded points one value
        from `status` (a get_status()-shaped {alias: value} dict) - the
        channel it corresponds to has an entry in status.json regardless of
        whether it ever updated, since get_status() reads it directly
        rather than relying on a monitor.

        Deliberately opportunistic, not a trigger of its own: this is meant
        to be called with a status snapshot that was already being taken
        for other reasons (a scan's own status_run_start/status_run_end
        capture), never to justify a fresh CA read purely to backfill one
        channel - see NamespaceMonitorStore.backfill_running_recordings,
        the only caller. A channel not in `status` either (never attempted,
        or the status read itself failed) still ends up with nothing, same
        as before this existed.

        Same race tolerance as the rest of this class (see stop()'s
        docstring): a genuine update landing in the same instant as a
        backfill for the same channel is not locked against, since the live
        per-channel callback path is not locked either - worst case here is
        one extra, slightly-out-of-order point on a channel that was about
        to update anyway, not a correctness problem worth a lock that
        would not be honoured on the other side regardless.
        """
        if not status:
            return 0
        now = time.time()
        filled = 0
        for name, buf in self._buffers.items():
            if buf["values"]:
                continue
            if name not in status:
                continue
            value = status[name]
            if value is None:
                continue
            self._append(buf, value, (status_times or {}).get(name, now))
            filled += 1
        self.n_backfilled += filled
        return filled

    def size_report(self, top_n=None):
        """Per-channel projected size: rate (this channel's own point
        count over the recording's elapsed time so far) times a sample
        value's size (element count * dtype itemsize, i.e. "frequency *
        data shape * bitdepth") - to judge storage/bandwidth cost from a
        short trial recording before committing to a long one, without
        needing to guess or wait for the real thing to finish.

        Works on a still-running recording (reads the live buffers - the
        same lax race tolerance as the rest of this class: a value read
        here while a CA callback is mid-append is not locked against
        beyond the dict-level snapshot, see stop()'s own docstring) as
        well as a stopped one, though a stopped session's buffers are
        empty (stop() hands them to the caller) - call this before
        stop(), not after, if you want it there too.

        Returns ``{full_name: {"n_points", "rate_hz", "n_elements",
        "bytes_per_point", "projected_bytes_per_s"}}``, sorted by
        projected_bytes_per_s descending and truncated to `top_n` if
        given - the point of this is usually "what are the biggest
        contributors", not a complete listing.
        """
        elapsed = (self.stopped_at or time.time()) - (self.started_at or time.time())
        if elapsed <= 0:
            return {}
        with self._lock:
            snapshot = {name: list(buf["values"]) for name, buf in self._buffers.items()}
        out = {}
        for name, values in snapshot.items():
            n_points = len(values)
            if not n_points:
                continue
            try:
                arr = np.asarray(values[-1])
                n_elements = int(arr.size) or 1
                bytes_per_point = int(arr.nbytes) or arr.itemsize
            except Exception:
                # a value numpy cannot characterise at all (rare) - still
                # worth a rough entry rather than silently dropping the
                # channel from the report.
                n_elements, bytes_per_point = 1, 8
            rate_hz = n_points / elapsed
            out[name] = {
                "n_points": n_points,
                "rate_hz": rate_hz,
                "n_elements": n_elements,
                "bytes_per_point": bytes_per_point,
                "projected_bytes_per_s": rate_hz * bytes_per_point,
            }
        out = dict(
            sorted(out.items(), key=lambda kv: -kv[1]["projected_bytes_per_s"])
        )
        if top_n:
            out = dict(list(out.items())[:top_n])
        return out

    def report(self, with_channels=False):
        elapsed = (self.stopped_at or time.time()) - (self.started_at or time.time())
        counts = self._final_counts if self._final_counts is not None else {
            name: len(b["values"]) for name, b in self._buffers.items()
        }
        n_points = sum(counts.values())
        # Which of the failed-to-attach channels belong to a *required*
        # namespace component - the recording equivalent of /health's
        # failed_required, same "everyone else failing is expected, this
        # failing is not" distinction.
        failed_required = sorted(
            {n for n in self._attach_failed_names
             if n.split(".", 1)[0] in self._required_names}
        )
        rep = {
            "recording_id": self.recording_id,
            "pgroup": self.pgroup,
            "run_number": self.run_number,
            "running": self.is_running,
            "mode": self.mode,
            "min_interval": self.min_interval,
            "sample_interval": self.sample_interval,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "elapsed_s": elapsed,
            "n_channels_requested": len(self.detectors),
            # attached < requested: a Monitorable whose
            # set_current_value_callback returns None (no underlying PV) or
            # raises is counted in n_attach_failed, not silently folded in.
            "n_channels_attached": self.n_attached,
            "n_attach_failed": self.n_attach_failed,
            "failed_required": failed_required,
            "n_failed_required": len(failed_required),
            "n_seeded": self.n_seeded,
            "n_backfilled": self.n_backfilled,
            "n_updates": self.n_updates,
            "n_stored": n_points,
            "n_dropped_throttle": self.n_dropped_throttle,
            "n_dropped_at_cap": self.n_dropped_full,
            "n_dropped_too_large": self.n_dropped_large,
            "max_value_elements": self.max_value_elements,
            "updates_per_s": self.n_updates / elapsed if elapsed > 0 else None,
            "stored_per_s": n_points / elapsed if elapsed > 0 else None,
            "n_channels_with_data": sum(1 for n in counts.values() if n),
        }
        if with_channels:
            rep["points_per_channel"] = dict(counts)
        return rep


def purge_eco_modules(modules=None):
    """Remove every ``eco.*`` module from a module table, returning how many.

    Used by reinit mode "reimport". Note what this does NOT do: it cannot
    unload the modules, only forget them, so any object still referencing an
    old class keeps that class - and the *next* import creates a second,
    distinct copy of it. Code elsewhere in the process that compares classes
    or catches specific exception types will then see mismatches (this
    caught out eco's own test suite once: a test that ran after a reimport
    stopped recognising `IsInitialisingError`). That is the honest reason
    reinit defaults to a process restart instead.

    ``eco.status_server`` itself is deliberately kept: this code is running
    out of it, and re-importing it would only create a second copy of the
    very classes holding the current call stack.
    """
    modules = sys.modules if modules is None else modules
    purged = 0
    for modname in list(modules):
        if (modname == "eco" or modname.startswith("eco.")) and not (
            modname.startswith("eco.status_server")
        ):
            modules.pop(modname, None)
            purged += 1
    return purged


def _ca_initializer():
    try:
        import epics.ca as ca

        ca.use_initial_context()
    except Exception:
        logger.debug("could not attach pool thread to shared CA context",
                     exc_info=True)
