"""What kind of motor is behind a motor record, and how can it be homed?

``identify_motor("SARES20-DSD:MOTOR_DSDX")`` (or ``MotorRecord.identify()``)
looks at one EPICS motor record from the outside and reports what it found
and where it found it:

* **controller** -- PowerBrick, Schneider (MDrive Plus / MForce / Lexium
  Motion Module), SmarAct MCS, Newport XPS, or the asyn driver name for the
  rest. From the asyn port in the record's ``OUT`` link, resolved through the
  IOC's startup script under ``/ioc`` where that is mounted (consoles), else
  from the port name alone.
* **the drive itself** -- a Schneider drive is asked through the IOC's own
  ``_get`` passthrough (``PN`` part number, ``VR`` firmware, ``EE`` encoder,
  ``S1``-``S4`` input setup, ``R1`` homed flag of the PSI homing scripts); a
  PowerBrick axis through gpascii (capture source, home velocity, offset).
  Only reads -- no motion command, no configuration write.
* **homing** -- what the record's HOMF/HOMR really does on this controller,
  which other reference (index mark, limit switch) the hardware offers, and
  whether a PSI homing script is attached (``<prefix>:HOMING``).

How the pieces were established (2026-11-02, all 362 Bernina/Aramis motor
records, see the docstrings below): HOMF on a Schneider drive sends ``HM``
(home *switch*, needs an input set up as type 1); index-mark homing (``HI``)
is not reachable through the record at all, which is why ``/ioc/qt/*_home.py``
exist. HOMF on a PowerBrick sends ``#n hm``, which captures on whatever
``CaptCtrl``/``CaptFlagSel`` select -- so the homing source is readable from
the controller.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field

_OUT_PORT = re.compile(r"@asyn\(\s*([^,\s)]+)\s*(?:,\s*(\d+))?")

# motor-record MSTA bits (0-based)
MSTA_PLUS_LS = 1 << 2
MSTA_HOME_LS = 1 << 3
MSTA_ENCODER = 1 << 8
MSTA_PROBLEM = 1 << 9
MSTA_MINUS_LS = 1 << 13
MSTA_HOMED = 1 << 14

# asyn controller-creating functions in IOC startup scripts -> family
_CREATORS = {
    "ImsMDrivePlusCreateController": "schneider",
    "powerPmacCreateController": "powerbrick",
    "MCSCreateController": "smaract_mcs",
    "XPSCreateController": "newport_xps",
    "ddriveCreateController": "piezojena_ddrive",
}
# without /ioc: guess from the asyn port name in OUT
_PORT_GUESS = (
    (re.compile(r"PPMAC\d*$"), "powerbrick"),
    (re.compile(r"MCS\d*$"), "smaract_mcs"),
    (re.compile(r"XPS\d*$"), "newport_xps"),
)

# MDrive Plus / MForce input/output setup (Sn=type,active,sink), MCode manual
SCHNEIDER_IO_TYPES = {
    0: "general input",
    1: "home switch",
    2: "+limit",
    3: "-limit",
    4: "G0 (starts program)",
    5: "soft stop",
    6: "pause",
    7: "jog+",
    8: "jog-",
    11: "reset",
    16: "general output",
    17: "moving output",
    18: "fault output",
    19: "stall output",
}

POWERBRICK_FLAGS = {0: "home switch", 1: "+limit", 2: "-limit", 3: "user flag"}

# ``/ioc/<IOC>/startup.script_<IOC>``; only mounted on the consoles
IOC_BOOT_ROOT = "/ioc"

_get_locks: dict[str, threading.Lock] = {}
_get_locks_lock = threading.Lock()


@dataclass
class Homing:
    """How this axis can be referenced. ``record`` is what HOMF/HOMR does,
    ``alternatives`` what else the hardware offers, ``script`` a PSI homing
    procedure attached to the device (its ``HOMING`` state PV)."""

    record: str = "unknown"
    alternatives: list[str] = field(default_factory=list)
    script: str | None = None
    homed: bool | None = None

    def __str__(self):
        rows = [("HOMF/HOMR", self.record)] + [("could also", a) for a in self.alternatives]
        rows += [("homing script", self.script), ("homed", None if self.homed is None else ("yes" if self.homed else "no"))]
        return "\n".join(f"{k:<13}  {v}" for k, v in rows if v is not None)

    def _repr_pretty_(self, p, cycle):
        p.text(str(self))


@dataclass
class MotorIdentity:
    pvname: str
    description: str | None = None
    online: bool = False
    ioc: str | None = None
    ca_server: str | None = None
    asyn_port: str | None = None
    axis: int | None = None
    family: str = "unknown"
    driver: str | None = None
    link: str | None = None
    model: str | None = None
    firmware: str | None = None
    encoder: bool | None = None
    limits: dict = field(default_factory=dict)
    homing: Homing = field(default_factory=Homing)
    details: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def __str__(self):
        name = f"{self.pvname}" + (f"  ({self.description})" if self.description else "")
        if not self.online:
            return f"{name}\n  offline"
        rows = [
            ("controller", " ".join(x for x in (self.family, self.model and f"- {self.model}") if x)),
            ("firmware", self.firmware),
            ("IOC", " ".join(x for x in (self.ioc, self.ca_server and f"(CA {self.ca_server})") if x)),
            ("asyn", " ".join(x for x in (self.asyn_port, self.axis is not None and f"axis {self.axis}", self.driver and f"[{self.driver}]") if x)),
            ("link", self.link),
            ("encoder", None if self.encoder is None else ("yes" if self.encoder else "no")),
            ("limits", ", ".join(f"{k}: {v}" for k, v in self.limits.items()) or None),
            ("homed", None if self.homing.homed is None else ("yes" if self.homing.homed else "no")),
            ("HOMF/HOMR", self.homing.record),
        ]
        rows += [("could also", a) for a in self.homing.alternatives]
        rows += [("homing script", self.homing.script)]
        rows += [(k, v) for k, v in self.details.items()]
        rows += [("problem", p) for p in self.problems]
        width = max(len(k) for k, v in rows if v not in (None, ""))
        lines = [name] + [f"  {k:<{width}}  {v}" for k, v in rows if v not in (None, "")]
        return "\n".join(lines)

    def _repr_pretty_(self, p, cycle):
        p.text(str(self))


def identify_motor(pvname: str, deep: bool = True, timeout: float = 2.0) -> MotorIdentity:
    """Identify the motor behind motor record ``pvname``.

    ``deep=False`` stays with channel access and the IOC boot files; ``True``
    (default) also asks the drive (Schneider via ``_get``, ~0.3 s per value;
    PowerBrick via gpascii, locked behind sf-gw where that applies)."""
    from epics import PV

    pvname = pvname.split(".")[0]
    ident = MotorIdentity(pvname)
    fields = {f: PV(f"{pvname}.{f}", auto_monitor=False) for f in ("OUT", "DESC", "MSTA", "UEIP", "DTYP")}
    if not fields["OUT"].wait_for_connection(timeout=timeout):
        return ident
    ident.online = True
    ident.ca_server = fields["OUT"].host
    val = {f: pv.get(as_string=f != "MSTA", timeout=timeout) for f, pv in fields.items()}
    ident.description = val["DESC"] or None
    out = val["OUT"] or ""
    m = _OUT_PORT.match(out)
    if m:
        ident.asyn_port = m.group(1)
        ident.axis = int(m.group(2)) if m.group(2) else None
    ident.evidence.append(f"OUT {out!r}, DTYP {val['DTYP']!r}")

    msta = val["MSTA"]
    if msta is not None:
        msta = int(msta)
        ident.encoder = bool(msta & MSTA_ENCODER)
        ident.homing.homed = bool(msta & MSTA_HOMED)
        active = [n for n, b in (("+limit", MSTA_PLUS_LS), ("-limit", MSTA_MINUS_LS), ("home switch", MSTA_HOME_LS)) if msta & b]
        if active:
            ident.limits["active now"] = ", ".join(active)
        if msta & MSTA_PROBLEM:
            ident.problems.append("MSTA problem bit set")

    ident.ioc = _ioc_name(pvname, ident.ca_server)
    _from_boot_files(ident)
    if ident.family == "unknown" and ident.asyn_port:
        for pattern, family in _PORT_GUESS:
            if pattern.match(ident.asyn_port):
                ident.family = family
                ident.evidence.append(f"family from asyn port name {ident.asyn_port}")
    if ident.family == "unknown" and _schneider_get_pv(pvname, timeout=0.5):
        ident.family = "schneider"
        ident.evidence.append("family from the MCode passthrough PV")

    ident.homing.script = _homing_script(pvname, timeout)
    handler = {
        "schneider": _schneider,
        "powerbrick": _powerbrick,
        "smaract_mcs": _smaract,
        "newport_xps": _xps,
    }.get(ident.family)
    if handler:
        handler(ident, deep, timeout)
    return ident


# pvname -> (deep, MotorIdentity); a deep result also answers shallow questions
_identities: dict[str, tuple[bool, MotorIdentity]] = {}


def cached_identity(pvname: str, deep: bool = False, refresh: bool = False) -> MotorIdentity:
    """``identify_motor`` once per session and record; ``refresh`` asks again."""
    pvname = pvname.split(".")[0]
    hit = _identities.get(pvname)
    # an offline record is asked again next time
    if hit and not refresh and hit[1].online and (hit[0] or not deep):
        return hit[1]
    ident = identify_motor(pvname, deep=deep)
    _identities[pvname] = (deep, ident)
    return ident


class MotorController:
    """``motor.controller``: what drives a motor record, looked up on first
    use and kept for the session -- creating it costs nothing, and it is not
    part of the motor's status, so scans never read it.

    ``type``, ``ioc`` and ``link`` come from channel access and the IOC boot
    files (about a second the first time); ``homing`` also asks the drive or
    controller (a few seconds for a Schneider drive). ``refresh()`` forgets
    what was found; ``motor.identify()`` gives the full report."""

    def __init__(self, pvname):
        self._pvname = pvname

    def identity(self, deep=False, refresh=False) -> MotorIdentity:
        return cached_identity(self._pvname, deep=deep, refresh=refresh)

    @property
    def type(self) -> str:
        """Controller family, plus the drive model once it was asked
        (``homing`` or ``identify()``), e.g. ``schneider MDrive Plus``."""
        ident = self.identity()
        if not ident.online:
            return "offline"
        model = ident.model and ident.model.split(":", 1)[-1].split("(")[0].strip()
        return " ".join(x for x in (ident.family, model) if x)

    @property
    def ioc(self) -> str | None:
        return self.identity().ioc

    @property
    def link(self) -> str | None:
        """Physical connection: ``TCP host:port``, ``serial /dev/ttyMn``, ..."""
        return self.identity().link

    @property
    def homing(self) -> Homing:
        """What HOMF/HOMR does on this controller, other references the
        hardware offers, an attached PSI homing script, and the homed flag."""
        return self.identity(deep=True).homing

    def refresh(self):
        _identities.pop(self._pvname.split(".")[0], None)

    def __str__(self):
        return str(self.identity())

    def _repr_pretty_(self, p, cycle):
        p.text(str(self))

    def __repr__(self):
        hit = _identities.get(self._pvname.split(".")[0])
        state = f"{hit[1].family}" if hit else "not looked up yet"
        return f"<MotorController {self._pvname}: {state}>"


def identify_motors(pvnames, deep: bool = True, max_workers: int = 8):
    """``identify_motor`` for many records, in parallel; returns a dict."""
    from concurrent.futures import ThreadPoolExecutor

    from epics import ca

    ca.initialize_libca()
    pvnames = list(pvnames)
    # CA from threads without the initial context crashes the interpreter
    with ThreadPoolExecutor(max_workers=max_workers, initializer=ca.use_initial_context) as pool:
        return dict(zip(pvnames, pool.map(lambda pv: identify_motor(pv, deep=deep), pvnames)))


def survey_table(identities) -> str:
    """One line per motor: controller, model, encoder, homed, HOMF meaning."""
    lines = []
    for ident in identities.values() if isinstance(identities, dict) else identities:
        if not ident.online:
            lines.append(f"{ident.pvname:34s} offline")
            continue
        enc = {True: "enc", False: "   ", None: " ? "}[ident.encoder]
        homed = {True: "homed", False: "     ", None: "  ?  "}[ident.homing.homed]
        lines.append(
            f"{ident.pvname:34s} {ident.family:12s} {(ident.model or ''):26.26s} {enc} {homed} {ident.homing.record}"
        )
    return "\n".join(lines)


# -- where the record lives -----------------------------------------------------


def _ioc_name(pvname, ca_server, attempts=3):
    from eco.epics_utils import iocinfo

    for attempt in range(attempts):
        # iocinfo drops requests now and then when asked in parallel
        try:
            iocs = {r.ioc for r in iocinfo.search_records(pvname, fuzzy=False)}
        except Exception:
            time.sleep(0.5 * (attempt + 1))
            continue
        if len(iocs) == 1:
            return iocs.pop()
        break
    if ca_server and "cagw" not in ca_server:
        return ca_server.split(":")[0].split(".")[0].upper()
    return None


def _boot_script(ioc):
    if not ioc:
        return None
    for name in (f"startup.script_{ioc}", "startup.script"):
        path = os.path.join(IOC_BOOT_ROOT, ioc, name)
        try:
            with open(path) as f:
                return path, f.read()
        except OSError:
            continue
    return None


_ARGS = r"\(\s*\"?([^\",\s)]+)\"?\s*,\s*\"?([^\",)]+?)\"?\s*[,)]"


def _from_boot_files(ident):
    """Controller family and physical link from the IOC startup script:
    ``XxxCreateController(motor port, asyn port, ...)`` and
    ``drvAsyn{IP,Serial}PortConfigure(asyn port, address, ...)``."""
    boot = _boot_script(ident.ioc)
    if boot is None or not ident.asyn_port:
        return
    path, text = boot
    text = "\n".join(line.split("#")[0] for line in text.splitlines())
    for creator, port, lower in re.findall(r"(\w+CreateController)\s*" + _ARGS, text):
        if port != ident.asyn_port:
            continue
        ident.driver = creator
        ident.family = _CREATORS.get(creator, ident.family)
        ident.evidence.append(f"{os.path.basename(path)}: {creator}({port}, {lower})")
        for kind, aport, address in re.findall(r"drvAsyn(IP|Serial)PortConfigure\s*" + _ARGS, text):
            if aport == lower:
                ident.link = f"{'TCP' if kind == 'IP' else 'serial'} {address}"
        if ident.link is None and creator == "XPSCreateController":
            # XPSCreateController(port, IP address, ...) connects itself
            ident.link = f"TCP {lower}"
        break


def _homing_script(pvname, timeout):
    """State of a PSI homing procedure (``/ioc/qt/*_home.py``): these keep
    ``<prefix>:HOMING`` (or ``HOMING_<name>``) at 1 running, 2 done, 3 failed."""
    from epics import PV

    prefix = pvname.split(":")[0]
    for name in (f"{prefix}:HOMING", f"{prefix}:HOMING_{prefix.split('-')[-1]}"):
        pv = PV(name, auto_monitor=False)
        if pv.wait_for_connection(timeout=min(timeout, 0.5)):
            state = pv.get(as_string=True, timeout=timeout)
            return f"{name} = {state}"
    return None


# -- Schneider ------------------------------------------------------------------


def _schneider_get_pv(pvname, timeout=1.0):
    """The IOC's MCode passthrough for this axis: ``P:MOTOR_X``/``P:MOT_X``
    -> ``P:X_get`` (DSD, MF1/MF2, PBPS, PPRM, OATT, RIXS all follow it)."""
    from epics import PV

    prefix, _, tail = pvname.partition(":")
    name = f"{prefix}:{re.sub(r'^MOT(OR)?_', '', tail)}_get"
    pv = PV(name, auto_monitor=False)
    return pv if pv.wait_for_connection(timeout=timeout) else None


def mcode_query(get_pv, mnemonic, settle=0.3, retries=2):
    """Ask one MCode variable through a ``_get`` stringin (write the bare
    mnemonic, the drive's reply replaces it). ``None`` when the drive does
    not answer: the record then still holds the mnemonic, or an error.

    Read-only by construction: the IOC's StreamDevice protocol sends
    ``1pr <mnemonic>``, so even a command mnemonic is only printed."""
    with _get_locks_lock:
        lock = _get_locks.setdefault(get_pv.pvname, threading.Lock())
    with lock:
        for attempt in range(retries):
            get_pv.put(mnemonic, wait=True, timeout=5)
            time.sleep(settle * (1 + 2 * attempt))
            reply = (get_pv.get(as_string=True, timeout=2) or "").strip()
            if reply and reply.upper() != mnemonic.upper():
                # some firmware echoes "IS = 1, 2, 1"
                head, sep, tail = reply.partition("=")
                return tail.strip() if sep and head.strip().upper() == mnemonic.upper() else reply
    return None


def schneider_model(part_number):
    """Product line from the MCode part number (``PR PN``)."""
    pn = (part_number or "").upper()
    if pn.startswith("LMM"):
        return "Lexium Motion Module (drive board, external motor)"
    if pn.startswith("LMD"):
        return "Lexium MDrive (integrated motor)"
    if pn.startswith("MF"):
        return "MForce (drive board, external motor)"
    if pn.startswith("MD"):
        return "MDrive Plus (integrated motor)"
    return None


def _schneider(ident, deep, timeout):
    ident.homing.record = "HM: home switch (driver needs an input set up as home)"
    get_pv = _schneider_get_pv(ident.pvname, timeout=timeout)
    if get_pv is None:
        ident.problems.append("no MCode passthrough (_get) found")
        return
    ident.details["MCode"] = get_pv.pvname[: -len("_get")] + "_get/_set"
    if not deep:
        return
    q = {m: mcode_query(get_pv, m) for m in ("PN", "VR")}
    if q["PN"] is None:
        ident.problems.append("drive does not answer through _get")
        return
    ident.model = f"{q['PN']}: {schneider_model(q['PN']) or 'unknown line'}"
    ident.firmware = q["VR"]
    if q["PN"].upper().startswith("LMM"):
        _schneider_lmm(ident, get_pv)
    else:
        _schneider_mdrive(ident, get_pv)


def _schneider_mdrive(ident, get_pv):
    q = {m: mcode_query(get_pv, m) for m in ("EE", "S1", "S2", "S3", "S4", "R1")}
    if q["EE"] == "1":
        ident.details["encoder lines"] = mcode_query(get_pv, "EL")
    inputs = {}
    for n in range(1, 5):
        setup = q[f"S{n}"]
        try:
            kind, active, _ = (int(x) for x in setup.split(","))
        except (AttributeError, ValueError):
            continue
        inputs[n] = (kind, active)
    _schneider_homing(ident, inputs, q["EE"] == "1", q["R1"])


def _schneider_lmm(ident, get_pv):
    """Lexium Motion Modules (MF1/MF2 racks) configure inputs with ``IS``,
    whose multi-line reply the ``_get`` stringin cuts to its first line."""
    q = {m: mcode_query(get_pv, m) for m in ("EE", "EL", "IS", "R1")}
    ident.details["input setup"] = f"IS = {q['IS']} (first line only through _get)"
    if q["EE"] == "1":
        ident.details["encoder lines"] = q["EL"]
    _schneider_homing(ident, {}, q["EE"] == "1", q["R1"], inputs_known=False)


def _schneider_homing(ident, inputs, encoder_enabled, r1, inputs_known=True):
    names = {n: SCHNEIDER_IO_TYPES.get(k, f"type {k}") + ("" if a else " (active low)") for n, (k, a) in inputs.items()}
    if names:
        ident.details["inputs"] = ", ".join(f"S{n} {v}" for n, v in names.items())
    kinds = {k for k, _ in inputs.values()}
    ident.limits.update({"switches": "+" * (2 in kinds) + "-" * (3 in kinds) or "none configured"} if inputs_known else {})
    if inputs_known:
        ident.homing.record = (
            "HM: to the home switch" if 1 in kinds else "fails: no input is set up as home switch"
        )
    if encoder_enabled:
        ident.homing.alternatives.append("HI: encoder index mark (MCode only, not via the record)")
    if inputs_known and kinds & {2, 3}:
        ident.homing.alternatives.append("limit switch: drive into it, set the position there")
    if r1 is not None:
        ident.details["R1 homed flag"] = r1
    if ident.encoder is None:
        ident.encoder = encoder_enabled


# -- PowerBrick -----------------------------------------------------------------


def powerbrick_capture(capt_ctrl, flag_sel):
    """What a PowerBrick ``#n hm`` triggers on: ``CaptCtrl`` bit 0 index,
    bit 1 flag, bit 2/3 invert them; ``CaptFlagSel`` picks the flag."""
    capt_ctrl, flag_sel = int(capt_ctrl), int(flag_sel)
    parts = []
    if capt_ctrl & 1:
        parts.append("encoder index" + (" (low)" if capt_ctrl & 4 else ""))
    if capt_ctrl & 2:
        parts.append(POWERBRICK_FLAGS.get(flag_sel, f"flag {flag_sel}") + (" (active low)" if capt_ctrl & 8 else ""))
    return " and ".join(parts) or "immediate (no reference)"


def _powerbrick(ident, deep, timeout):
    ident.homing.record = "#n hm: capture set up on the controller"
    if not deep or ident.axis is None:
        return
    from .powerbrick import get_power_brick_comm, ioc_host

    host = ident.ca_server.split(":")[0] if ident.ca_server and "-CPPM-" in ident.ca_server.upper() else None
    try:
        comm = get_power_brick_comm(host or ioc_host(ident.pvname))
        get = lambda par: comm.get_parameter(par, autoconvert=False).split("=", 1)[1].strip()
        ax = ident.axis
        flag = get(f"Motor[{ax}].pCaptFlag")
        motor = {p: get(f"Motor[{ax}].{p}") for p in ("HomeVel", "HomeOffset", "HomeComplete", "EncType")}
    except PermissionError as e:
        ident.details["PowerBrick"] = str(e).splitlines()[0]
        return
    except Exception as e:
        ident.problems.append(f"PowerBrick not readable: {e}")
        return
    ident.link = f"gpascii {comm.hostname}" + (f" via {comm.jump}" if comm.jump else "")
    channel = flag.split(".Status")[0]
    direction = "-" if motor["HomeVel"].startswith("-") else "+"
    if ".Chan[" in channel:
        try:
            source = powerbrick_capture(get(f"{channel}.CaptCtrl"), get(f"{channel}.CaptFlagSel"))
        except Exception as e:
            source = f"unknown ({e})"
    else:
        source = f"capture on {channel} (MACRO station, not decoded)"
    ident.homing.record = f"#{ax} hm: {source}, direction {direction}, HomeOffset {motor['HomeOffset']}"
    ident.homing.homed = motor["HomeComplete"] == "1"
    ident.details["EncType"] = motor["EncType"]


# -- SmarAct / XPS ----------------------------------------------------------------


def _smaract(ident, deep, timeout):
    ident.homing.record = "find reference mark (MCS FRM); needs a positioner with sensor"
    if ident.encoder is False:
        ident.homing.alternatives.append("no sensor reported: open-loop stick-slip, no reference")


def _xps(ident, deep, timeout):
    ident.homing.record = "XPS group home (moves every positioner of the group)"
