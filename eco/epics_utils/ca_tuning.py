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

import logging
import threading
import time

logger = logging.getLogger(__name__)


# TRIAL (see module docstring): was 0.05 everywhere. Revert to 0.05 to undo.
CA_CONNECTION_TIMEOUT = 1.0

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
