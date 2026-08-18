"""Find IOCs by name or PV pattern, and locate their console (telnet/procServ).

This is a Python re-implementation of the lookup half of PSI's `cmdt`
("controls mighty debug tool", `/sf/controls/applications/cmdt/cmdt.py`) --
the part that answers "which IOC serves this PV, and what host:port do I
telnet to for its console" -- built against the same backing service cmdt
itself uses: the `iocinfo.psi.ch` REST API.

What this can and can't tell you
---------------------------------
* :func:`search_records` / :func:`find_ioc` resolve a PV name/pattern to the
  **IOC name** that owns it (via ``GET /records?pattern=...``).
* :func:`get_ioc` / :func:`resolve_console` resolve an IOC name to its boot
  host, IP, platform and (usually) its console ``host:port`` (via
  ``GET /ioc/<name>``, field ``shellbox``). A handful of IOCs don't report
  `shellbox` through the API; :data:`CONSOLE_PORT_OVERRIDES` is a small,
  manually maintained fallback table for those, seeded from the equivalent
  table in `cmdt.py` (`DebugTool.ioc2host_port_lut`), which that tool's
  comment says was built by SSHing into each box as root and reading
  ``/etc/shellbox.conf``.
* For the Bernina MForce/MDrive motor controllers specifically
  (`SARES20-MF1`/`SARES20-MF2`), the owning IOC (`SARES20-CSSU-MF1`/`-MF2`)
  is itself reported by the API as a **Moxa DA-662A-16-LX embedded device
  server** (`platform` field) -- i.e. the IOC runs directly on the same box
  that has the RS-485 serial ports wired to the drives. So for this
  hardware family, the IOC console host *is* effectively the serial
  gateway, which is different from a "soft IOC on a rack server talking out
  to a separate terminal server" topology. This is inferred from the API's
  `platform`/`epicsHostArchitecture` fields, not independently confirmed by
  reading that box's own asyn/startup config.

Sending input to a console -- :func:`send_console_keys` / :func:`restart_ioc`
--------------------------------------------------------------------------
:func:`check_console_reachable` and :func:`read_console_output` never send
anything (pure liveness probe / passive read). :func:`send_console_keys` and
:func:`restart_ioc` do send bytes and are real, disruptive, live actions --
:func:`restart_ioc` sends :data:`RESTART_KEY`, procServ's restart-child
hotkey (Ctrl-X, ``0x18``). This was confirmed operationally (2026-08-18) for
the Bernina MForce IOC consoles (`SARES20-CSSU-MF1`/`-MF2`) by the beamline
operator -- it may not hold for other IOCs/procServ configurations
elsewhere, since the key is configurable per-deployment
(`--restart-key`/`--quit-key`). Neither function gates or confirms anything
itself; callers (the GUI restart buttons) are responsible for confirming
with a human before calling :func:`restart_ioc`.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field

import requests

API_BASE = "http://iocinfo.psi.ch/api/v2"

# procServ's restart-child hotkey (Ctrl-X). Confirmed operationally
# (2026-08-18) for the Bernina MForce IOC consoles (SARES20-CSSU-MF1/-MF2)
# by the beamline operator -- see module docstring. Not independently
# verified for other IOCs; procServ's restart/quit keys are configurable
# per-deployment.
RESTART_KEY = b"\x18"  # Ctrl-X

# Manually maintained fallback for IOCs whose `GET /ioc/<name>` response has
# `shellbox: null`. Seeded from `cmdt.py`'s `DebugTool.ioc2host_port_lut`
# (source comment there: discovered via `ssh root@<host> 'cat
# /etc/shellbox.conf'`). Only the Bernina-relevant entries are carried over
# here; add more as needed following the same discovery method.
CONSOLE_PORT_OVERRIDES: dict[str, tuple[str, int]] = {
    "SARES20-CSSU-MF1": ("SARES20-CSSU-MF1", 50001),
    "SARES20-CSSU-MF2": ("SARES20-CSSU-MF2", 50001),
}


@dataclass
class RecordMatch:
    ioc: str
    name: str
    type: str | None = None
    description: str | None = None
    facility: str | None = None


@dataclass
class IocBoot:
    ioc: str
    hostname: str | None = None
    ip_address: str | None = None
    platform: str | None = None
    epics_version: str | None = None
    epics_host_architecture: str | None = None
    boot_date: str | None = None
    responsible: str | None = None
    facility: str | None = None
    shellbox: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: dict) -> "IocBoot":
        return cls(
            ioc=d.get("ioc"),
            hostname=d.get("hostname"),
            ip_address=d.get("ipAddress"),
            platform=d.get("platform"),
            epics_version=d.get("epicsVersion"),
            epics_host_architecture=d.get("epicsHostArchitecture"),
            boot_date=d.get("bootDate"),
            responsible=d.get("responsible"),
            facility=d.get("facility"),
            shellbox=d.get("shellbox"),
            raw=d,
        )


@dataclass
class IocMatch:
    """One IOC found by :func:`find_ioc`, with its matched devices and (if
    resolvable) console address."""

    ioc: str
    devices: list[str]
    boot: IocBoot | None
    console_host: str | None
    console_port: int | None
    facility: str | None = None

    def __str__(self) -> str:
        console = (
            f"{self.console_host}:{self.console_port}"
            if self.console_host
            else "console unknown"
        )
        fac = f" ({self.facility})" if self.facility else ""
        devs = ", ".join(sorted(self.devices)[:5])
        more = "" if len(self.devices) <= 5 else f", +{len(self.devices) - 5} more"
        return f"{self.ioc}{fac} [{console}] devices: {devs}{more}"


def _get(path: str, params: dict, timeout: float) -> object:
    res = requests.get(f"{API_BASE}{path}", params=params, timeout=timeout)
    res.raise_for_status()
    return res.json()


_REGEX_METACHARS = set(".^$*+?{}[]()|\\")


def _fuzzify(pattern: str) -> str:
    """Wrap a plain name/substring in ``.*...*`` for substring matching,
    unless it already looks like a deliberate regex (contains a regex
    metacharacter) -- e.g. ``"XRD"`` -> ``".*XRD.*"``, but
    ``".*SARES20-MF.*"`` or ``"S.*:PICTURE"`` pass through untouched.

    Needed because the `iocinfo.psi.ch` API does not substring-match a bare
    pattern the way `re.search` would: ``pattern=XRD`` matches nothing,
    ``pattern=.*XRD.*`` matches everything containing "XRD" -- confirmed
    live (0 vs. 939 records). Without this, a plain name search like the
    ones `cmdt` supports (e.g. ``cmdt -p XRD``, which searches a local
    substring-matched cache) would silently return nothing here.
    """
    if any(c in _REGEX_METACHARS for c in pattern):
        return pattern
    return f".*{pattern}.*"


def search_records(pattern: str, timeout: float = 10.0, fuzzy: bool = True) -> list[RecordMatch]:
    """Find PVs matching *pattern* and the IOC each lives on. Equivalent to
    ``GET /records?pattern=...``.

    By default (``fuzzy=True``) a plain name with no regex metacharacters is
    substring-matched via :func:`_fuzzify` (``"XRD"`` behaves like
    ``".*XRD.*"``). Pass ``fuzzy=False`` to send *pattern* to the API
    unchanged (exact regex control, anchored the way the API interprets it).
    """
    query = _fuzzify(pattern) if fuzzy else pattern
    data = _get("/records", {"pattern": query, "startDate": "2025-01-01T00:00:00"}, timeout)
    return [
        RecordMatch(
            ioc=r.get("ioc"),
            name=r.get("name"),
            type=r.get("type"),
            description=r.get("description"),
            facility=r.get("facility"),
        )
        for r in data
    ]


def get_ioc(ioc_name: str, timeout: float = 10.0) -> IocBoot:
    """Fetch full boot/host info for one IOC. Equivalent to ``GET /ioc/<name>``."""
    data = _get(f"/ioc/{ioc_name}", {}, timeout)
    return IocBoot.from_json(data)


def resolve_console(ioc_name: str, boot: IocBoot | None = None, timeout: float = 10.0) -> tuple[str, int] | None:
    """Resolve an IOC's console ``(host, port)``, or ``None`` if unknown.

    Tries the API's own `shellbox` field first, then
    :data:`CONSOLE_PORT_OVERRIDES`.
    """
    if boot is None:
        try:
            boot = get_ioc(ioc_name, timeout=timeout)
        except requests.RequestException:
            boot = None
    if boot is not None and boot.shellbox:
        host, _, port = boot.shellbox.rpartition(":")
        if host and port.isdigit():
            return host, int(port)
    if ioc_name in CONSOLE_PORT_OVERRIDES:
        return CONSOLE_PORT_OVERRIDES[ioc_name]
    return None


def find_ioc(pattern: str, timeout: float = 10.0, fuzzy: bool = True) -> list[IocMatch]:
    """Search PVs/IOC names matching *pattern* and resolve each owning IOC's
    console address. This is the main entry point -- equivalent to what
    `cmdt.py -p <pattern>` resolves before offering you a debug session.

    ``fuzzy=True`` (default) substring-matches a plain name via
    :func:`_fuzzify` (``find_ioc("XRD")`` finds every IOC with "XRD"
    anywhere in a PV/IOC name, matching `cmdt`'s behaviour); pass
    ``fuzzy=False`` to search with *pattern* as a literal regex instead.
    """
    records = search_records(pattern, timeout=timeout, fuzzy=fuzzy)
    ioc2devices: dict[str, set[str]] = {}
    for r in records:
        ioc2devices.setdefault(r.ioc, set()).add(r.name.split(":")[0])

    # A pattern can also directly be an *exact* IOC name with no matching
    # records (e.g. it's currently not serving any registered PVs) -- try it
    # directly too so `find_ioc("SARES20-CSSU-MF1")` still works. Only kept
    # if that IOC actually exists, so a genuine no-match search reports
    # "not found" instead of a phantom, info-less result.
    if not ioc2devices:
        try:
            get_ioc(pattern, timeout=timeout)
        except requests.RequestException:
            pass
        else:
            ioc2devices[pattern] = set()

    matches = []
    for ioc, devices in ioc2devices.items():
        try:
            boot = get_ioc(ioc, timeout=timeout)
        except requests.RequestException:
            boot = None
        console = resolve_console(ioc, boot=boot, timeout=timeout)
        matches.append(
            IocMatch(
                ioc=ioc,
                devices=sorted(devices),
                boot=boot,
                console_host=console[0] if console else None,
                console_port=console[1] if console else None,
                facility=boot.facility if boot else None,
            )
        )
    return matches


def check_console_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Pure liveness probe: open a TCP connection to the console port and
    immediately close it again. Sends and reads nothing. Returns False on
    any connection error (refused, timed out, unresolved host, ...)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def read_console_output(host: str, port: int, read_seconds: float = 2.0, timeout: float = 3.0) -> str:
    """Connect to an IOC console and passively capture whatever text streams
    out for *read_seconds*, without sending anything -- e.g. to see the
    procServ banner, or the IOC's boot log right after a restart. Raises on
    connection failure (use :func:`check_console_reachable` first if you
    want to distinguish "unreachable" from "reachable but silent")."""
    chunks: list[bytes] = []
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(0.5)
        deadline = time.monotonic() + read_seconds
        while time.monotonic() < deadline:
            try:
                data = s.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("ascii", errors="replace")


def send_console_keys(host: str, port: int, data: bytes, timeout: float = 3.0) -> None:
    """Send raw bytes to an IOC console and close the connection. Real,
    live, disruptive action -- no confirmation and no safety net here. Only
    call this after a human has confirmed the action (see :func:`restart_ioc`
    and the GUI restart buttons in eco.widgets.ioc_finder_widget /
    eco.widgets.ioc_finder_qt, which gate this behind a confirmation step)."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(data)


def restart_ioc(host: str, port: int, timeout: float = 3.0) -> None:
    """Send :data:`RESTART_KEY` (procServ's Ctrl-X restart-child hotkey) to
    an IOC console. Restarts the IOC process -- there is no undo. Not gated
    by any confirmation here; see the module docstring and
    :func:`send_console_keys`."""
    send_console_keys(host, port, RESTART_KEY, timeout=timeout)
