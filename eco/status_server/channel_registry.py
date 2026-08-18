"""Alias <-> EPICS channel mapping for a beamline namespace.

Deliberately reads a plain JSON file with `json.load` instead of importing
`eco` and walking the live Assembly/Namespace tree. That keeps the status
server a self-contained process with no dependency on hardware objects,
IOC connections made as a side effect of importing eco, or any of the
(fairly heavy) eco package import chain - it only ever needs to know
"alias X is EPICS channel Y of type Z".

Two ways to obtain the registry file for a namespace:

1. Point straight at the namespace's existing alias file, e.g.
   ``eco/aliases/namespaces/bernina.json``. This is already in exactly the
   schema read here (``[{"alias", "channel", "channeltype"}, ...]``), since
   it is what ``eco.utilities.config.Namespace`` writes to via
   ``alias_namespace.update(...)`` as components are appended. Whether it
   is kept fresh on disk (via ``.store()``) depends on the beamline's
   startup script - check before relying on it in production.

2. Regenerate it explicitly with ``export_namespace_channels`` below, run
   once from a live, already-initialised eco session. This walks
   ``namespace.alias.get_all()``, a pure attribute walk of the alias tree
   built by ``Assembly._append()`` - it does not touch any PV or hardware,
   so it's safe to call at any time, e.g. after adding a new device.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Channel:
    alias: str
    pvname: str
    channeltype: str = "CA"


def load_channel_registry(
    path: str | Path, channeltypes: Sequence[str] = ("CA",)
) -> list[Channel]:
    """Load the alias->channel mapping for a namespace from a JSON file.

    `channeltypes` filters to the kinds of channels this service knows how
    to monitor. Only "CA" (plain EPICS Channel Access) is supported by the
    prototype PV-based monitor store in monitor_store.py - "BS" (bsread /
    DataBuffer) channels use a different live-data transport entirely (see
    eco/dbase/archiver.py LIVE_SOURCES) and are out of scope for v1.
    """
    data = json.loads(Path(path).read_text())
    channels = [
        Channel(
            alias=entry["alias"],
            pvname=entry["channel"],
            channeltype=entry.get("channeltype", "CA"),
        )
        for entry in data
        if entry.get("channeltype", "CA") in channeltypes
    ]
    aliases_seen = set()
    deduped = []
    for ch in channels:
        if ch.alias in aliases_seen:
            continue
        aliases_seen.add(ch.alias)
        deduped.append(ch)
    return deduped


def export_namespace_channels(
    namespace, path: str | Path, channeltypes: Sequence[str] = ("CA",)
) -> list[dict]:
    """Run from a live eco session to (re)generate a registry file, e.g.:

        from bernina import namespace
        from eco.status_server.channel_registry import export_namespace_channels
        export_namespace_channels(
            namespace, "/etc/eco-status-server/bernina_channels.json"
        )

    Safe to call at any time: only reads `namespace.alias`, never touches a
    PV. Re-run whenever the namespace's set of devices changes.
    """
    entries = namespace.alias.get_all(channeltypes=list(channeltypes))
    Path(path).write_text(json.dumps(entries, indent=2, sort_keys=True))
    return entries


def load_flat_pv_list(path: str | Path) -> list[Channel]:
    """Load a plain inventory of PV names with no alias information - e.g.
    a hand-maintained ``pv_list.pkl`` (a pickled list of PV name strings) or
    a plain text file with one PV name per line.

    Checked against a real example (a home-directory ``pv_list.pkl`` dumped
    from the bernina namespace, 7062 unique PV names): it goes well beyond
    ``eco/aliases/namespaces/bernina.json`` (1121 entries, 549 of them
    overlapping) - mostly individual motor-record fields (``.VELO``,
    ``.RBV``, ``.DIR``, ``.ACCL``, ``.LLM``, ``.HLM``, ``.SPMG``, ...) and
    timing-system channels (EVR pulse config, ``SIN-TIMAST-TMA`` event
    fields) that aren't exposed as a top-level alias anywhere, but are
    ordinary CA channels like any other - mechanically just as monitorable.

    Since a flat list has no alias, the PV name itself is used as the
    alias (see `merge_channels` for how this combines with a "real" alias
    when the same PV is present in both sources).
    """
    p = Path(path)
    if p.suffix == ".pkl":
        import pickle

        with p.open("rb") as f:
            names = pickle.load(f)
    else:
        names = [
            line.strip()
            for line in p.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    seen = set()
    channels = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        channels.append(Channel(alias=name, pvname=name, channeltype="CA"))
    return channels


def merge_channels(*channel_lists: Iterable[Channel]) -> list[Channel]:
    """Combine channel lists from multiple sources (e.g. the namespace
    alias file plus a supplementary flat PV inventory), deduplicating by
    PV name. Where the same PV appears in more than one source, the entry
    from whichever source is passed *first* wins - pass the namespace's
    real aliases before a flat/synthetic-alias list so the friendlier name
    is kept.
    """
    by_pvname: dict[str, Channel] = {}
    for channels in channel_lists:
        for ch in channels:
            by_pvname.setdefault(ch.pvname, ch)
    return list(by_pvname.values())
