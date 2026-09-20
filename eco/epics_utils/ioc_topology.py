"""Which physical IOC serves which namespace component - a small,
independent record, not a replacement for anything else that happens to
know something adjacent:

* `eco.devices_general.controllers.bernina_controllers.MOTOR_CONTROLLER_BOXES`
  hand-maps a few controller *prefixes* to their IOC, for an opt-in
  diagnostic "physical box" branch of the namespace. That code is expected
  to keep changing on its own timeline, and it only covers the boxes
  someone bothered to add there - coupling this module to it would mean
  this safety mechanism silently loses coverage every time that one is
  refactored, and never covers anything else. This module reads and writes
  neither that table nor its file.
* `eco.epics_utils.iocinfo` is the live lookup this module uses underneath
  (the `iocinfo.psi.ch` REST API) - a real network call, too slow to make
  on the fast path of every `init_all()`.

Why this exists
----------------
`Namespace.init_all()`'s concurrent pass (`_run_init_pass` in
eco.utilities.config) builds up to `max_workers` components at once, with no
notion of which physical hardware any of them talk to. Two components that
happen to be scheduled together and both live on the same small/embedded IOC
(e.g. a Moxa-hosted MForce box) hit it with simultaneous connection/get
traffic purely by thread-pool scheduling luck - suspected (not confirmed) as
a contributor to sporadic crashes seen on Bernina's moxa-based motion
controllers (tt_kb/clic/prof_kb on SARES20-MF2; other moxaboxes for
las/xrd). See the project's status-server/init_all investigation notes.

This module is the persisted answer to "which IOC(s) does component X's PVs
live on" - resolved once per component (the first time
`Namespace.init_all(check_object_crosstalk=True)` sees it, or on an explicit
re-check) via `iocinfo`, then cached to disk so every later `init_all()`
pass - including the fast, default, no-network-calls one - can consult it
for free to decide which components must never be built at the same time.
A name with no cached entry (the common case until it has been explicitly
checked) is simply unconstrained, identical to the behaviour before this
module existed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Per-user, like ca_tuning.py's learned "fast channels" list and for the same
# reason: a per-user file has none of the group-permission problems a shared
# one under /sf/... would bring, and a personal-account write to a *shared*
# file has broken a live server before (see the eco/status_server memory
# notes on shared adjustables-fs ownership). This is a local performance/
# safety hint, not beamline configuration.
IOC_TOPOLOGY_STATE_FILE = Path(
    os.environ.get("ECO_IOC_TOPOLOGY_FILE")
    or (Path.home() / ".eco" / "ioc_topology.json")
)

_lock = threading.RLock()
_cache: dict = {}  # component_name -> {"iocs": [...], "checked_at": t, "n_pvs": n}
_loaded = False
_dirty = False


def _load():
    global _loaded
    with _lock:
        if _loaded:
            return
        try:
            with open(IOC_TOPOLOGY_STATE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                _cache.update(data)
        except FileNotFoundError:
            pass
        except Exception:
            logger.debug("ioc_topology: could not read %s", IOC_TOPOLOGY_STATE_FILE,
                         exc_info=True)
        _loaded = True


def save():
    """Persist the cache. Called automatically by `record_iocs`; exposed so
    a caller doing many updates in a row can also call it once at the end -
    though there is little reason to, since `record_iocs` already does."""
    global _dirty
    with _lock:
        if not _dirty:
            return
        data = dict(_cache)
        _dirty = False
    try:
        IOC_TOPOLOGY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = IOC_TOPOLOGY_STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, IOC_TOPOLOGY_STATE_FILE)
    except Exception:
        logger.debug("ioc_topology: could not write %s", IOC_TOPOLOGY_STATE_FILE,
                     exc_info=True)


def known_iocs(component_name):
    """The cached IOC set for `component_name`, or None if never checked."""
    _load()
    with _lock:
        entry = _cache.get(component_name)
    return frozenset(entry["iocs"]) if entry else None


def known_iocs_for(names):
    """{name: frozenset(iocs)} for every name in `names` with a cached,
    non-empty entry. Names never checked (the common case until
    `init_all(check_object_crosstalk=True)` has run) are simply left out -
    not an error, and not a reason to constrain anything: see
    `eco.utilities.config._run_init_pass`'s per-IOC locks, which treat an
    absent name as "no known constraint", exactly today's behaviour."""
    _load()
    with _lock:
        return {
            name: frozenset(_cache[name]["iocs"])
            for name in names
            if name in _cache and _cache[name].get("iocs")
        }


def has_been_checked(component_name):
    _load()
    with _lock:
        return component_name in _cache


def record_iocs(component_name, iocs, n_pvs=None):
    """Remember (persist) that `component_name`'s PVs live on `iocs`. `iocs`
    may be empty - that is itself a real, useful answer ("checked, shares
    nothing with anything") - distinct from `component_name` never having
    been checked at all (`has_been_checked` returns False)."""
    global _dirty
    _load()
    with _lock:
        _cache[component_name] = {
            "iocs": sorted(set(iocs)),
            "checked_at": time.time(),
            "n_pvs": n_pvs,
        }
        _dirty = True
    save()


def forget(component_name):
    """Un-check a single component, so the next check_object_crosstalk pass
    resolves it again from scratch - e.g. after its hardware was moved to a
    different IOC. Safe to call for a name that was never checked."""
    global _dirty
    with _lock:
        _load()
        if component_name not in _cache:
            return False
        del _cache[component_name]
        _dirty = True
    save()
    return True


def clear():
    """Forget everything learned. For when the whole cache is suspect (e.g.
    a large-scale IOC re-hosting) rather than one component."""
    global _dirty
    with _lock:
        _load()
        _cache.clear()
        _dirty = True
    save()


def _prefix(pvname):
    """The part of a PV name before its first ':' - the unit most eco PV
    names share with everything else on the same physical controller (e.g.
    "SARES20-MF2:MOT_3.RBV" -> "SARES20-MF2"). Good enough to de-duplicate
    `iocinfo` lookups: a component's PVs overwhelmingly share one, or a
    couple of, such prefixes, not one per PV."""
    return pvname.split(":", 1)[0]


def component_pv_names(obj):
    """Every PV name under `obj`'s alias subtree, or () if it has none.

    Same `Alias.get_all()` walk `NamespaceMonitorStore.compare_channels` /
    `Namespace.status_collection` already use, just without filtering by
    channeltype - here the point is "every distinct piece of hardware this
    component touches", not "every status-worthy value".
    """
    alias = getattr(obj, "alias", None)
    if alias is None:
        return ()
    try:
        return tuple(sorted({a["channel"] for a in alias.get_all() if a.get("channel")}))
    except Exception:
        logger.debug("ioc_topology: could not enumerate PVs for an object",
                      exc_info=True)
        return ()


def discover_iocs(component_name, pv_names, timeout=10.0):
    """Resolve `pv_names` to their owning IOC(s) via the live `iocinfo`
    lookup, record the result, and return it as a frozenset.

    One `iocinfo.find_ioc` call per distinct PV *prefix* (see `_prefix`)
    rather than per PV, since a component's PVs overwhelmingly share a
    controller - normally one or two network calls, not hundreds.
    Best-effort per prefix: one prefix failing to resolve does not stop the
    others. If *none* resolve at all (e.g. the API is unreachable), nothing
    is recorded and this returns None, leaving `component_name` uncached -
    to be retried on the next explicit check, rather than permanently
    remembered as "shares nothing with anything", which an empty recorded
    result would otherwise indistinguishably mean.
    """
    from eco.epics_utils import iocinfo

    prefixes = sorted({_prefix(pv) for pv in pv_names if pv})
    if not prefixes:
        return None
    iocs = set()
    any_ok = False
    for prefix in prefixes:
        try:
            matches = iocinfo.find_ioc(prefix, timeout=timeout)
        except Exception:
            logger.debug("ioc_topology: could not resolve IOC for prefix %s",
                         prefix, exc_info=True)
            continue
        any_ok = True
        iocs.update(m.ioc for m in matches if m.ioc)
    if not any_ok:
        return None
    record_iocs(component_name, iocs, n_pvs=len(pv_names))
    return frozenset(iocs)


def report():
    """What this module currently knows, for looking at from a session."""
    _load()
    with _lock:
        by_ioc: dict = {}
        for name, entry in _cache.items():
            for ioc in entry.get("iocs", ()):
                by_ioc.setdefault(ioc, []).append(name)
        return {
            "state_file": str(IOC_TOPOLOGY_STATE_FILE),
            "n_checked": len(_cache),
            "shared_iocs": {
                ioc: sorted(names) for ioc, names in by_ioc.items() if len(names) > 1
            },
        }
