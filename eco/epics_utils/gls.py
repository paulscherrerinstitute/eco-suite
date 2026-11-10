"""Search and name-conversion helpers for PSI's building-automation data
("GLS"/"OSFA" -- room climate, HVAC -- as distinct from the accelerator
control system) as bridged into datahub's retrieval service.

Confirmed 2026-11-08, from a real console (saresb-cons-05) against the real
service -- re-check rather than assume still true:

- These channels are **archive-only**: `caget` of the equivalent-looking PV
  name times out ("not found"). The accelerator control network and PSI's
  building-management network are separate; only the archiver side of GLS
  crosses over into datahub, under the ``sf-archiver`` backend.
- The channel-id naming convention there is *not* an EPICS PV name and *not*
  a simple prefix swap of one (e.g. not "D_OSFA_..." -> "A_OSFA_..."). It is::

      <building>-<room>-<device>:<point>_<value_type>_D-OSFA[-DIFF]

  e.g. ``IKLUM-8701-EB01904:M01_A_D-OSFA`` -- building "IKLUM", room "8701",
  device "EB01904", point "M01", value_type "A" (an analogue/measured
  value; "H" seen for an enum/binary one). The trailing "-DIFF" variant seen
  on some channels is a second, separate series (presumably a setpoint
  deviation), not a modifier on the same series.
- Coverage is **partial and not guessable**: only ~31 distinct
  building/room/device combinations are bridged into ``sf-archiver`` at all
  (see `list_gls_channels()` for the current, authoritative list) -- most
  guessed building/room/device combinations (including several tried while
  building this module) resolve to nothing, live or archived. Always
  confirm with a search; never assume a channel exists because its name
  looks plausible.
- Where a GLS channel *is* bridged, its historical depth matches whatever
  the ``sf-archiver`` retention currently goes back to (the same for every
  channel there, GLS or EPICS-sourced) -- it is not itself limited. Its
  *sampling*, however, is markedly coarser than a typical EPICS-archived
  value in the same facility (observed ~110s median interval for a GLS
  room-temperature channel against ~30s for a comparable EPICS one).

See `eco.epics_utils.detector.DetectorArchiverOnly` (generic archive-only
Detector) and `eco.epics_utils.detector.DetectorGls` (EPICS-primary,
GLS-fallback combination) for the Detector classes built on top of this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from datahub import Daqbuf

GLS_BACKEND = "sf-archiver"

_CHANNEL_RE = re.compile(
    r"^(?P<building>[A-Za-z]+)-(?P<room>\d+)-(?P<device>[A-Za-z0-9]+):"
    r"(?P<point>[A-Za-z]+\d+)_(?P<value_type>[A-Za-z]+)_D-OSFA(?P<diff>-DIFF)?$"
)

# Tokens that carry no identifying information of their own when pulled out
# of a guessed, underscore-style name such as "D_OSFA_IKLUM_8701_EB01904_M01_A"
# (see `guess_to_gls_channels`) -- the live/archived marker and the "OSFA"
# literal itself.
_IGNORED_TOKENS = {"D", "A", "H", "OSFA"}
_DEVICE_TOKEN_RE = re.compile(r"^[A-Za-z]{1,2}\d{4,6}$")


@dataclass
class GlsChannel:
    """A parsed GLS/OSFA archived channel id (see module docstring for the
    naming convention)."""

    channel: str
    building: str
    room: str
    device: str
    point: str
    value_type: str
    diff: bool


def parse_gls_channel(channel: str) -> "GlsChannel | None":
    """`channel` split into its parts, or None if it doesn't match the known
    GLS/OSFA naming convention at all (e.g. it's an unrelated channel that
    happened to match a search)."""
    m = _CHANNEL_RE.match(channel)
    if not m:
        return None
    g = m.groupdict()
    return GlsChannel(
        channel=channel,
        building=g["building"],
        room=g["room"],
        device=g["device"],
        point=g["point"],
        value_type=g["value_type"],
        diff=bool(g["diff"]),
    )


def gls_search(pattern: str, case_sensitive: bool = False, backend: str = GLS_BACKEND):
    """Channel ids in `backend` whose name matches `pattern` (substring or
    regex -- whatever datahub's own search endpoint accepts), as a plain
    sorted list of strings -- e.g. ``gls_search("IKLUM-8701")``.

    Unlike calling `datahub.Daqbuf.search` directly, this never returns a
    pretty-printed table or a bare `None` for "nothing found": always a list,
    empty if there were no matches."""
    source = Daqbuf(backend=backend)
    source.verbose = True  # ask for the raw {"channels": [...]} dict back
    result = source.search(pattern, case_sensitive=case_sensitive) or {}
    return sorted({c["name"] for c in result.get("channels", [])})


def find_gls_channels(building=None, room=None, device=None, backend: str = GLS_BACKEND):
    """`GlsChannel`s currently archived under `backend` whose name contains
    all of the given, substring-matched tokens (building e.g. "IKLUM"/
    "IKLTK"/"IKUPK", room e.g. "8701", device e.g. "EB01904") -- whichever
    are given. With none given, returns the *complete* current inventory
    (see module docstring for a snapshot count; call this for the real,
    current one). Only entries matching the known naming convention are
    returned (see `parse_gls_channel`) -- anything a loose search pulled in
    that doesn't match it is silently dropped, on the assumption it's an
    unrelated channel rather than a new GLS naming variant."""
    pattern = "-".join(p for p in (building, room, device) if p) or "OSFA"
    channels = gls_search(pattern, backend=backend)
    parsed = [parse_gls_channel(c) for c in channels]
    return [p for p in parsed if p]


def list_gls_channels(backend: str = GLS_BACKEND):
    """Every GLS/OSFA channel currently bridged into `backend`, parsed -- the
    full inventory, useful to find the right building/room for a new sensor
    by browsing instead of guessing its id (most guesses don't exist; see
    module docstring)."""
    return find_gls_channels(backend=backend)


def guess_to_gls_channels(name_or_tokens, backend: str = GLS_BACKEND):
    """Best-effort bridge from an old-style guessed name -- e.g. the dead
    ``D_OSFA_IKLTK_2401_EB06501_M01_A``-style PV names that used to sit
    (commented out, never resolving) in `eco.fel.atmosphere` -- to whatever
    is *actually* archived for the same building/room/device, if anything.

    `name_or_tokens` is either such a string (split on non-alphanumerics) or
    an already-split iterable of tokens. Recognises a building as the first
    all-letters token of length >= 4, a room as the first all-digits token,
    and a device as the first token shaped like "EB01904"/"EM01101"/etc; the
    live/archived marker and the literal "OSFA" are ignored if present, in
    whichever order. Returns `find_gls_channels(...)` for whatever was
    recognised -- empty if nothing in the name was recognisable, or if it
    was but nothing is actually bridged for it (the common case: check
    `list_gls_channels()` before assuming a miss means "try a different
    guess", since whole rooms are simply often not bridged at all).

    This is a convenience for the "I have an old guessed name" case only --
    for anything else, call `find_gls_channels`/`gls_search` directly rather
    than fighting this heuristic."""
    if isinstance(name_or_tokens, str):
        tokens = re.findall(r"[A-Za-z0-9]+", name_or_tokens)
    else:
        tokens = list(name_or_tokens)
    tokens = [t for t in tokens if t.upper() not in _IGNORED_TOKENS]
    building = next((t for t in tokens if t.isalpha() and len(t) >= 4), None)
    room = next((t for t in tokens if t.isdigit()), None)
    device = next((t for t in tokens if _DEVICE_TOKEN_RE.match(t)), None)
    if not any((building, room, device)):
        return []
    return find_gls_channels(building=building, room=room, device=device, backend=backend)
