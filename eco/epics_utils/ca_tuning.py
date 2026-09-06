"""Channel-access tuning constants and diagnostics for silent ``None`` reads.

Two things live here, both born of the same recurring failure: pyepics'
``PV.get()`` returns ``None`` when it cannot deliver a value - it never
raises (see ``epics/pv.py``'s ``get_with_metadata``: every failure path is a
bare ``return None``). eco has been bitten by this at least three times, each
time on a different value and each time fixed only for that value:

* ``pulse_id``, mid-scan - ``int(None)`` aborting a scan several steps in;
  fixed with a monitor cache plus ``Daq.get_pulse_id()``.
* the EVR event codes and pulser numbers, during a concurrent ``init_all()``
  - a ``None`` used as if it were an event code, silently producing pulsers
  without a delay chain and outputs wired to throwaway dummies.
* ``las.xlt._offset``, read on every ``lxt`` change during a scan.

The pattern is always the same: a read that has no tolerance for a moment of
unavailability, and a caller that treats the resulting ``None`` as data.

THE GENERAL FIX (supersedes the per-PV ones above)
--------------------------------------------------
Each of those was fixed by hand-rolling ``auto_monitor=True`` for one PV.
That is the whole answer, generalised - see ``AUTO_MONITOR_DEFAULT`` below
for why, straight out of pyepics' source: with a monitor, a read is a dict
lookup that has no failure path at all; without one, every read is a network
round trip with two. eco's old ``auto_monitor=False`` default was therefore
not merely unhelpful, it was *the cause*.

So there are now two general mechanisms here instead of a growing list of
per-PV caches:

* **monitor by default, demote what is fast** - ``make_pv`` applies the
  policy, and a background sweeper measures actual update rates and drops
  the monitor on anything above ``AUTO_MONITOR_MAX_RATE``, remembering it
  across sessions. Measured on bernina: 7 074 channels monitored, 68
  demoted, ~8 % of one core standing cost.
* **retry at the chokepoint** - ``eco.epics_utils.adjustable._read_pv``
  retries a read that has worked before (``CA_READ_RETRIES``), so a
  momentary failure is absorbed once, for every caller, instead of each one
  discovering it separately. A channel that has never produced a value is
  not retried, so absent PVs stay cheap.
* **declare sensitive stretches** - ``sensitive_period`` marks a window
  (a scan step's acquisition, say) where subscriptions must not be
  reconfigured and reads get a more patient budget.

TRIAL - CONNECTION TIMEOUT
--------------------------
``CA_CONNECTION_TIMEOUT`` was ``0.05`` everywhere (22 hardcoded literals,
now all routed through this constant). Measured against the real control
system from a Bernina console, a fresh channel takes ~27 ms to connect
(p50; p99 31 ms) on an idle network - so 50 ms is a **2x margin**, while the
data path has ~100x (5 ms observed under a sustained 20-worker fan-out,
against pyepics' ~500 ms get timeout). That asymmetry is why the failures
look like connection failures rather than slow reads: the moment a virtual
circuit drops - IOC restart, network blip, gateway hiccup, all of which
cluster around heavy CA activity - ``pv.connected`` goes False and the next
``get()`` has 50 ms to re-establish a connection that takes seconds. It
returns ``None`` instead.

This value only applies while a channel is *not* connected: for a connected
PV ``wait_for_connection()`` returns immediately, so raising it costs
nothing on the normal path. It is deliberately **not** used for
``_wait_for_initialisation()``, which keeps the old fast-fail budget
(``CA_INIT_CONNECTION_TIMEOUT``) so that initializing a namespace full of
absent devices does not become slower.

**This is an experiment and may be reverted** - set
``CA_CONNECTION_TIMEOUT`` back to ``0.05`` and everything returns to the
previous behaviour in one line. See the CLAUDE.md section "CA connection
timeout (trial)".
"""

import atexit
import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# TRIAL (see module docstring): was 0.05 everywhere. Revert to 0.05 to undo.
CA_CONNECTION_TIMEOUT = 1.0

# --------------------------------------------------------------------------
# Monitor policy: monitor by default, demote only what is actually fast
#
# `auto_monitor=False` was the eco-wide default, to keep subscription traffic
# down. It is also, from pyepics' own source, the direct cause of the silent
# `None` reads above. `PV.get_with_metadata` starts with
#
#     if not self.wait_for_connection(timeout=timeout):
#         return None
#     if ((not use_monitor) or (not self.auto_monitor) or ... ):
#         metad = ca.get_with_metadata(...)
#         if metad is None:
#             return
#
# so with `auto_monitor=False` **every** read is a network round trip with
# two independent ways to come back `None`, while with `auto_monitor=True`
# and a cached value that whole block is skipped: the read is a dict lookup
# that cannot time out, and a momentary circuit drop costs nothing. Every
# per-PV fix so far (`Daq.get_pulse_id`, the event-code frequency cache) has
# been hand-rolling `auto_monitor=True` for one PV at a time.
#
# The traffic argument for `False` turns out to apply to very few channels.
# Measured on the real bernina namespace (a 3-minute recording of all 7 719
# monitorable status channels, see eco/status_server/DESIGN.md section 15.2):
#
#     >= 50 Hz     48 channels   87 % of all updates
#     10-50 Hz      9 channels    3 %
#      1-10 Hz    182 channels    9 %
#      < 1 Hz    7 480 channels    1 %
#
# i.e. 0.6 % of channels produce seven eighths of the load, and monitoring
# the other 99.4 % is close to free. So: monitor everything by default, and
# demote the handful that prove to be fast. `_MonitorRateTracker` below does
# that automatically, and remembers them across sessions so the next one
# never subscribes to them at all.
AUTO_MONITOR_DEFAULT = True

# A channel updating faster than this gets demoted to auto_monitor=False.
# 10 Hz sits in the empty gap in the distribution above (the 10-50 Hz band
# holds 9 channels of 7 719), so the threshold is not delicately placed.
AUTO_MONITOR_MAX_RATE = 10.0

# How often the sweeper looks at accumulated counts.
AUTO_MONITOR_SWEEP_INTERVAL = 5.0

# Where the learned fast-channel list is remembered. Per user rather than
# shared: it is a local performance hint, not beamline configuration, and a
# per-user file has none of the group-permission problems a shared one in
# /sf/... would bring (see eco.utilities.datafiles).
AUTO_MONITOR_STATE_FILE = Path(
    os.environ.get("ECO_CA_FAST_CHANNELS")
    or (Path.home() / ".eco" / "ca_fast_channels.json")
)

# How many times a read that has worked before may be retried before it is
# reported as a silent None. See `_read_pv` in eco.epics_utils.adjustable:
# a channel that demonstrably works and momentarily does not is the exact
# case worth one more try, and the retry is skipped entirely for a channel
# that has never produced anything.
CA_READ_RETRIES = 2
CA_READ_RETRY_DELAY = 0.05

# Extra patience during a period the caller has declared sensitive (a scan
# step's acquisition window, say), where a failed read is far more expensive
# than a few extra milliseconds. See `sensitive_period`.
CA_READ_RETRIES_SENSITIVE = 4

# Budget used by `_wait_for_initialisation()` only. Deliberately still the
# old value: that call is a best-effort "is it there yet" during namespace
# init, and lengthening it would multiply init time by the number of absent
# devices (of which bernina has plenty) for no benefit.
CA_INIT_CONNECTION_TIMEOUT = 0.05


# How often, per PV, a silent None read may be reported. A dead PV read in a
# get_status() fan-out would otherwise log thousands of times per scan.
_REPORT_INTERVAL = 60.0

_lock = threading.Lock()
_last_ok = {}       # pvname -> time of the last read that returned a value
_last_report = {}   # pvname -> time we last logged about it
_none_counts = {}   # pvname -> how many Nones since the last report


def note_successful_read(pvname):
    """Record that `pvname` just returned a real value.

    Cheap (one dict store) and called on every successful read, because the
    single most useful thing to know about a later ``None`` is whether that
    channel was working a moment ago - a transient - or has never produced
    anything at all - simply absent.
    """
    if pvname:
        _last_ok[pvname] = time.time()


def has_ever_succeeded(pvname):
    """Whether `pvname` has ever returned a value in this session.

    Used to keep the raised CA_CONNECTION_TIMEOUT from making absent PVs
    expensive: a channel that has never produced anything is almost
    certainly simply not there (bernina's namespace has plenty), and there
    is nothing to be gained by waiting a full second for it on every read of
    a status fan-out. A channel that *has* worked and is now unreadable is
    the case the longer budget exists for.
    """
    return pvname in _last_ok


def _ca_channel_state(pvname=None, limit=20000):
    """(disconnected, total, detail) from pyepics' channel-access cache.

    `epics.ca._cache` maps context -> pvname -> _CacheItem, and each item
    carries `.conn` (connected), `.ts` (timestamp of the last connection
    change or failed attempt) and `.failures`. That is a far better picture
    than `epics.pv._PVcache_`, which is only populated by `get_pv()` and so
    is empty for everything eco builds with `PV(...)` directly.

    Best-effort and bounded: this runs while something is already going
    wrong and must not itself become a problem.
    """
    try:
        from epics import ca as _ca

        cache = getattr(_ca, "_cache", None)
        if not cache:
            return (None, None, "")
        disconnected = total = 0
        detail = ""
        for _ctx, entries in cache.items():
            for nm, item in entries.items():
                total += 1
                if total > limit:
                    break
                if not getattr(item, "conn", True):
                    disconnected += 1
                if pvname and nm == pvname:
                    age = time.time() - getattr(item, "ts", time.time())
                    detail = (
                        f", channel state changed {age:.1f}s ago after "
                        f"{getattr(item, 'failures', '?')} failed attempt(s)"
                    )
        return (disconnected, total, detail)
    except Exception:
        return (None, None, "")


def describe_channel(pv=None, pvname=None):
    """One-line diagnostic: connection + history + CA-cache state.

    Factored out of `report_none_read` so any other hardened chokepoint that
    raises instead of returning `None` (e.g. `Daq.get_pulse_id`'s
    `TimeoutError`) can attach the same evidence a `None` read already gets,
    instead of a bare "timeout hit" that requires log archaeology after the
    fact to tell a dropped virtual circuit from a channel that was simply
    never demoted/recreated correctly.

    Best-effort like `_ca_channel_state`: called while something is already
    wrong, so it must not itself raise.
    """
    try:
        pvname = pvname or getattr(pv, "pvname", None)
        connected = getattr(pv, "connected", "?") if pv is not None else "?"
        last_ok = _last_ok.get(pvname) if pvname else None
        if last_ok is None:
            history = "never returned a value in this session"
        else:
            history = f"last returned a value {time.time() - last_ok:.1f}s ago"
        disconnected, total, detail = _ca_channel_state(pvname)
        if disconnected is None:
            ca_state = "CA cache unavailable"
        else:
            ca_state = (
                f"{disconnected}/{total} cached channels disconnected{detail}"
            )
        return f"connected={connected}, {history}, {ca_state}"
    except Exception:
        return "diagnostics unavailable"


def report_none_read(pv, name=None, kind="read"):
    """Log a `PV.get()` that returned None, with the context needed to tell
    a transient apart from an absent PV.

    This exists because the ``None`` is otherwise completely silent: it flows
    into the caller as if it were a value, and the failure surfaces somewhere
    else entirely (``int(None)``, ``event_codes[None]``,
    ``'NoneType' has no attribute ...``) or not at all. Rate-limited per PV
    so a dead channel in a status fan-out cannot flood the log, and it
    reports how many reads it swallowed since the last message.

    Deliberately diagnostic only - it changes no behaviour and returns
    nothing. The point is that the next occurrence identifies its own cause
    instead of us guessing.
    """
    try:
        pvname = getattr(pv, "pvname", None) or str(name)
        now = time.time()
        with _lock:
            _none_counts[pvname] = _none_counts.get(pvname, 0) + 1
            if now - _last_report.get(pvname, 0.0) < _REPORT_INTERVAL:
                return
            _last_report[pvname] = now
            swallowed = _none_counts.pop(pvname, 1)

        last_ok = _last_ok.get(pvname)
        if last_ok is None:
            history = "never returned a value in this session"
        else:
            history = f"last returned a value {now - last_ok:.1f}s ago"
        disconnected, total, detail = _ca_channel_state(pvname)
        if disconnected is None:
            ca_state = "CA cache unavailable"
        else:
            ca_state = (
                f"{disconnected}/{total} cached channels disconnected{detail}"
            )

        logger.warning(
            "silent None from %s (%s): pyepics returned no value; "
            "connected=%s, %s, %s, connection_timeout=%ss%s. "
            "The caller will see None as if it were data.",
            name or pvname,
            pvname,
            getattr(pv, "connected", "?"),
            history,
            ca_state,
            getattr(pv, "connection_timeout", "?"),
            f" [{swallowed} such reads since the last message]"
            if swallowed > 1
            else "",
        )
    except Exception:
        # diagnostics must never be able to break a read
        pass


# --------------------------------------------------------------------------
# adaptive monitor policy


_fast_lock = threading.RLock()
_fast_channels = set()      # pvnames known to update faster than the threshold
_fast_dirty = False         # something changed since the last save
_update_counts = {}         # pvname -> updates since the last sweep
_tracked = {}               # pvname -> (pv, callback_index)
_sweeper = None
_sensitive_depth = 0        # >0 while a caller has declared a sensitive period


def _load_fast_channels():
    try:
        with open(AUTO_MONITOR_STATE_FILE) as f:
            names = json.load(f)
        if isinstance(names, list):
            with _fast_lock:
                _fast_channels.update(str(n) for n in names)
            logger.debug(
                "ca_tuning: %d known fast channel(s) loaded from %s",
                len(_fast_channels), AUTO_MONITOR_STATE_FILE,
            )
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("ca_tuning: could not read %s", AUTO_MONITOR_STATE_FILE,
                     exc_info=True)


def save_fast_channels():
    """Persist the learned fast-channel list.

    Worth persisting because the learning itself costs something: a session
    that has to rediscover the ~50 fast channels subscribes to them for a few
    seconds first. Remembering them means the next session never opens those
    subscriptions at all.
    """
    global _fast_dirty
    with _fast_lock:
        if not _fast_dirty:
            return
        names = sorted(_fast_channels)
        _fast_dirty = False
    try:
        AUTO_MONITOR_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = AUTO_MONITOR_STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(names, f, indent=1)
        os.replace(tmp, AUTO_MONITOR_STATE_FILE)
    except Exception:
        logger.debug("ca_tuning: could not write %s", AUTO_MONITOR_STATE_FILE,
                     exc_info=True)


def is_known_fast(pvname):
    with _fast_lock:
        return pvname in _fast_channels


def clear_fast_channels():
    """Forget everything learned, so the next reads re-measure from scratch.

    For when a channel's rate has genuinely changed (a detector reconfigured,
    an event code retimed) and it is stuck demoted from a previous session.
    """
    global _fast_dirty
    with _fast_lock:
        _fast_channels.clear()
        _fast_dirty = True
    save_fast_channels()


def _count_update(pvname):
    # The hot path: one dict increment per CA update, on libca's callback
    # thread. Deliberately not locked - a lost increment costs nothing to a
    # rate heuristic, and taking a lock here would put every monitored
    # channel in the namespace through one contended lock.
    _update_counts[pvname] = _update_counts.get(pvname, 0) + 1


def _track(pv):
    """Attach the rate counter to `pv` and remember it for the sweeper."""
    pvname = getattr(pv, "pvname", None)
    if not pvname or pvname in _tracked:
        return
    try:
        # with_ctrlvars=False: pyepics defaults it to True, which issues a
        # blocking get_ctrlvars() per already-connected PV - for a whole
        # namespace that is exactly the get-storm this policy exists to
        # avoid (same reason eco.status_server.monitor_store passes it).
        index = pv.add_callback(
            lambda pvname=pvname, **kw: _count_update(pvname),
            with_ctrlvars=False,
        )
    except Exception:
        return
    _tracked[pvname] = (pv, index)


def _demote(pvname, pv, index, rate):
    """Stop monitoring a channel that updates too fast to be worth it."""
    try:
        # Never demote a channel somebody is deliberately monitoring: a
        # recording, a Monitor(), a CallbackEpics all register their own
        # callback, and clearing the subscription under them would silently
        # stop their data. Ours is the only one we may account for.
        if len(getattr(pv, "callbacks", {})) > 1:
            return False
        pv.remove_callback(index)
        pv.auto_monitor = False
    except Exception:
        logger.debug("ca_tuning: could not demote %s", pvname, exc_info=True)
        return False
    logger.info(
        "ca_tuning: %s updates at ~%.0f Hz (> %.0f Hz), dropping its monitor "
        "- reads of it go back to a direct CA get.",
        pvname, rate, AUTO_MONITOR_MAX_RATE,
    )
    return True


def _sweep_once(interval):
    global _fast_dirty
    if _sensitive_depth > 0:
        # Do not reconfigure subscriptions in the middle of an acquisition:
        # the point of a sensitive period is that nothing about channel
        # access changes under it. Counts keep accumulating; the next sweep
        # after it ends sees them.
        return
    # snapshot then zero, rather than clearing in place, so an update
    # landing mid-sweep is counted against the next window instead of lost
    counts = dict(_update_counts)
    for name in counts:
        _update_counts[name] = 0
    demoted = []
    for pvname, count in counts.items():
        if count / interval <= AUTO_MONITOR_MAX_RATE:
            continue
        entry = _tracked.get(pvname)
        if entry is None:
            continue
        pv, index = entry
        if _demote(pvname, pv, index, count / interval):
            demoted.append(pvname)
            _tracked.pop(pvname, None)
            _update_counts.pop(pvname, None)
    if demoted:
        with _fast_lock:
            _fast_channels.update(demoted)
            _fast_dirty = True
        save_fast_channels()


def _sweep_loop():
    # CRITICAL: this thread calls pv.auto_monitor = False / remove_callback
    # on channels it did not create, which ends up in libca's
    # ca_clear_subscription(). Every thread that touches Channel Access must
    # first attach to the *same* CA context the channel was created on
    # (pyepics's default is to implicitly create a new context on first use
    # per thread) - this codebase has already been burned by skipping that,
    # up to and including a segfault inside libca's CA-TCP-recv thread from
    # concurrent init_all() workers each running their own context (see
    # eco.utilities.config._run_init_pass, eco.status_server.namespace_store
    # ._ca_thread, eco.status_server.parallel_init - all attach explicitly
    # for exactly this reason). This function used not to, which is a live
    # bug: calling ca_clear_subscription() on a channel from an unattached
    # thread is undefined behaviour, not merely "affects only that one
    # subscription" - a plausible way for one demotion to transiently
    # disrupt delivery to a *different*, unrelated PV object monitoring the
    # same channel (e.g. Daq's dedicated pulse_id monitor, which shares a
    # chid with any other PV(...) built for the same pvname - pyepics
    # dedupes channels by (context, pvname), see epics.ca.create_channel).
    try:
        import epics.ca as ca

        ca.use_initial_context()
    except Exception:
        logger.warning("ca_tuning: sweeper could not attach to the shared "
                       "CA context", exc_info=True)
    last = time.time()
    while True:
        time.sleep(AUTO_MONITOR_SWEEP_INTERVAL)
        now = time.time()
        interval, last = max(now - last, 1e-6), now
        try:
            _sweep_once(interval)
        except Exception:
            logger.debug("ca_tuning: sweep failed", exc_info=True)


def _ensure_sweeper():
    global _sweeper
    if _sweeper is not None:
        return
    with _fast_lock:
        if _sweeper is not None:
            return
        _sweeper = threading.Thread(
            target=_sweep_loop, name="ca_tuning_monitor_sweeper", daemon=True
        )
        _sweeper.start()


def make_pv(pvname, auto_monitor=None, **kwargs):
    """Build a `PV` under the adaptive monitor policy.

    Use this instead of `PV(...)` for anything eco reads repeatedly. It
    monitors by default (see AUTO_MONITOR_DEFAULT for why that is both
    faster and the fix for the silent-None class of bug), except for
    channels already known to be too fast, and it registers the PV with the
    sweeper that finds the rest.

    `auto_monitor` still wins if given explicitly, for the cases that
    genuinely know better than the policy.
    """
    from epics import PV

    if auto_monitor is None:
        auto_monitor = AUTO_MONITOR_DEFAULT and not is_known_fast(pvname)
    pv = PV(pvname, auto_monitor=auto_monitor, **kwargs)
    if auto_monitor:
        _track(pv)
        _ensure_sweeper()
    return pv


class sensitive_period:
    """Declare a stretch of time where channel access must not be disturbed.

    Two things change while one is active: the sweeper leaves subscriptions
    alone (reconfiguring a monitor mid-acquisition is exactly the wrong
    moment), and reads get the more patient retry budget
    (`CA_READ_RETRIES_SENSITIVE`), because inside a scan step a failed read
    costs a run and a few extra milliseconds cost nothing.

    Reentrant and thread-safe by depth counting, so nesting - a step inside
    a scan inside a queue - behaves.

        with ca_tuning.sensitive_period("scan step"):
            ...
    """

    def __init__(self, what=""):
        self.what = what

    def __enter__(self):
        global _sensitive_depth
        with _fast_lock:
            _sensitive_depth += 1
        return self

    def __exit__(self, *exc):
        global _sensitive_depth
        with _fast_lock:
            _sensitive_depth = max(0, _sensitive_depth - 1)
        return False


def in_sensitive_period():
    return _sensitive_depth > 0


def read_retries():
    """How many extra attempts a read that has worked before may make."""
    return CA_READ_RETRIES_SENSITIVE if _sensitive_depth > 0 else CA_READ_RETRIES


def monitor_report():
    """What the policy currently believes, for looking at from a session."""
    with _fast_lock:
        fast = sorted(_fast_channels)
    return {
        "auto_monitor_default": AUTO_MONITOR_DEFAULT,
        "max_rate_hz": AUTO_MONITOR_MAX_RATE,
        "monitored": len(_tracked),
        "known_fast": fast,
        "n_known_fast": len(fast),
        "state_file": str(AUTO_MONITOR_STATE_FILE),
        "in_sensitive_period": in_sensitive_period(),
    }


_load_fast_channels()
atexit.register(save_fast_channels)
