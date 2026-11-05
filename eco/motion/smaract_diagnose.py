"""Diagnosis of SmarAct MCS channels behind the motorSmarAct IOCs.

Two levels:

* EPICS only (default, nothing is written): per channel, has the IOC's
  poller ever updated the motor record since the IOC started, what the
  MSTA bits say, alarm states, sensor type readback, and whether failing
  channels cluster on one 3-channel module of the controller.
* query_controller=True: additionally asks the controller itself, through
  the IOC's asyn record ``<P>:asyn`` (same TCP connection the IOC uses),
  with read-only ASCII *get* commands of the first-generation MCS
  (``:GNC``, ``:GSE``, ``:GS<ch>``, ``:GST<ch>``, ``:GP<ch>``,
  ``:GPPK<ch>``). Nothing is set and nothing moves, but it does write the
  query string into ``<P>:asyn.AOUT``. Not available for MCS2-generation
  controllers (SCPI-like protocol), which are recognised and skipped.

Usage::

    smaract_usd.diagnose()                        # all 18 channels
    smaract_usd.diagnose(query_controller=True)
    smaract_usd.stage1.diagnose(query_controller=True)

    from eco.motion.smaract_diagnose import diagnose_controller
    diagnose_controller("SARES20-MCS1:MOT_", channels=[1, 2, 3, 4])

Returns a list of dicts (one per channel) and prints a report.

Status: a first note, not a finished tool. What it can see is limited --
the controller's per-channel answers say little more than "sensor present
or not"; why the IOC stops polling an axis needs the IOC console / driver
messages, which this does not read.

Observed on SARES20-MCS1 (2026-11-05, IOC restarted 13:47 with nothing on
channels 1-3 and 12): the controller reported 18 channels, sensors enabled
(``:SE1``), status "stopped" and sensor type 1 on every queried channel,
but ``:GP<ch>`` answered error 129 (no sensor present) on exactly the
channels the IOC never polls, and a position on the working ones. So the
module was fine; the driver drops axes that have no sensor when the IOC
starts. Every channel answered ``:PPK<ch>,0`` (position not known), the
working ones with dial positions of a few nm, which looks like a
controller power cycle (positions reset, references lost).

Replies are read with a fresh CA get and checked against the channel they
belong to: reading the monitor-cached TINP right after the put returned
the previous command's reply now and then.
"""

import threading
import time

import epics

# motor record MSTA bits
MSTA_BITS = {
    0: "direction+",
    1: "done",
    2: "plus_limit",
    3: "home_switch",
    5: "closed_loop",
    6: "slip_stall",
    7: "at_home",
    8: "encoder_present",
    9: "problem",
    10: "moving",
    11: "gain_support",
    12: "comm_error",
    13: "minus_limit",
    14: "homed",
}

# MCS (first generation) ASCII interface, ":S<ch>,<code>" channel status
MCS_STATUS = {
    0: "stopped",
    1: "stepping",
    2: "scanning",
    3: "holding",
    4: "targeting",
    5: "move delay",
    6: "calibrating",
    7: "finding reference mark",
    8: "opening",
}

# ":E<ch>,<code>" error replies; 0 is the plain acknowledge
MCS_ERRORS = {
    0: "ok",
    1: "syntax error",
    2: "invalid command",
    3: "overflow",
    4: "parse error",
    5: "too few parameters",
    6: "too many parameters",
    7: "invalid parameter",
    8: "wrong mode",
    129: "no sensor present",
    140: "sensor disabled",
    141: "command overridden",
    142: "end stop reached",
    143: "wrong sensor type",
    144: "could not find reference mark",
    145: "wrong end effector type",
    146: "movement locked",
    147: "range limit reached",
    148: "physical position unknown",
    150: "command not processable",
    151: "waiting for trigger",
    152: "command not triggerable",
    153: "command queue full",
    154: "invalid component",
    155: "invalid sub component",
    156: "invalid property",
    157: "permission denied",
    159: "power amplifier disabled",
}

CHANNELS_PER_MODULE = 3
# EPICS epoch (1990) in POSIX seconds: a record that was never processed
# carries this (or an earlier) timestamp
_NEVER = 631152000.0 + 1

_query_lock = threading.Lock()


def _get(name, timeout=2.0, as_string=False):
    """(value, timestamp, severity) or (None, None, None) if unreachable."""
    pv = epics.get_pv(name, connect=True, timeout=timeout)
    if not pv.connected:
        return None, None, None
    # use_monitor=False: a fresh read, not the monitor cache -- right after
    # a put to the asyn record the cached TINP can still be the previous reply
    meta = pv.get_with_metadata(
        form="time", timeout=timeout, as_string=as_string, use_monitor=False
    )
    if meta is None:
        return None, None, None
    return meta.get("value"), meta.get("timestamp"), meta.get("severity")


def decode_msta(msta):
    if msta is None:
        return []
    msta = int(msta)
    return [name for bit, name in MSTA_BITS.items() if msta & (1 << bit)]


def split_pvname(pvname):
    """'SARES20-MCS1:MOT_4' -> ('SARES20-MCS1', 4)"""
    prefix, motor = pvname.split(":", 1)
    return prefix, int(motor.rsplit("_", 1)[1])


def ioc_name(prefix):
    """IOC naming used at Bernina: SARES20-MCS<n> -> SARES20-CPCL-MCS00<n>."""
    area, mcs = prefix.split("-", 1)
    return f"{area}-CPCL-{mcs[:3]}{int(mcs[3:]):03d}"


def ioc_info(prefix):
    ioc = ioc_name(prefix)
    start, _, _ = _get(f"{ioc}:STARTTOD", as_string=True)
    uptime, _, _ = _get(f"{ioc}:UPTIME", as_string=True)
    return {"ioc": ioc, "started": start, "uptime": uptime}


def _ioc_start_posix(started):
    if not started:
        return None
    try:
        return time.mktime(time.strptime(started[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def epics_state(pvname):
    """Everything the IOC tells about one channel, without writing."""
    msta, msta_ts, _ = _get(pvname + ".MSTA")
    rbv, rbv_ts, _ = _get(pvname + ".RBV")
    stat, _, sevr = _get(pvname + ".STAT", as_string=True)
    desc, _, _ = _get(pvname + ".DESC", as_string=True)
    ptyp, _, _ = _get(pvname + "_PTYP_RB")
    mclf, _, _ = _get(pvname + "_MCLF")
    cal, cal_ts, _ = _get(pvname + "_CAL")
    last_update = max([t for t in (msta_ts, rbv_ts) if t] or [0]) or None
    return {
        "pv": pvname,
        "connected": msta is not None,
        "description": desc,
        "msta": None if msta is None else int(msta),
        "msta_flags": decode_msta(msta),
        "readback": rbv,
        "last_update": last_update,
        "record_alarm": stat,
        "record_severity": sevr,
        "sensor_type_rb": ptyp,
        "max_frequency": mclf,
        "calibrated_since_ioc_start": bool(cal_ts and cal_ts > _NEVER),
    }


# ---------------------------------------------------------------- controller


def controller_generation(prefix):
    """'MCS' (first generation, ASCII) or 'MCS2' -- read from the IOC's
    MCS2-only PVs is not possible, so we look at the asyn port's
    connection: MCS2 controllers listen on 55551."""
    host, _, _ = _get(f"{prefix}:asyn.HOSTINFO", as_string=True)
    if host and host.strip().endswith(":55551"):
        return "MCS2"
    return "MCS"


def query(prefix, command, timeout=3.0):
    """Send one read-only ASCII command through <prefix>:asyn and return the
    reply string (or raise RuntimeError). Only get commands are accepted."""
    if not command.startswith(":G"):
        raise ValueError(f"only ':G...' (get) commands are allowed, not {command!r}")
    rec = f"{prefix}:asyn"
    tmod, _, _ = _get(rec + ".TMOD", as_string=True)
    ofmt, _, _ = _get(rec + ".OFMT", as_string=True)
    if tmod != "Write/Read" or ofmt != "ASCII":
        raise RuntimeError(
            f"{rec} is configured TMOD={tmod!r} OFMT={ofmt!r}; expected "
            "Write/Read and ASCII -- not changing it, query skipped"
        )
    expected = _expected_reply_prefixes(command)
    for _attempt in range(2):
        with _query_lock:
            ok = epics.caput(rec + ".AOUT", command, wait=True, timeout=timeout)
            if ok is None:
                raise RuntimeError(f"{rec}: put of {command!r} timed out")
            reply, _, _ = _get(rec + ".TINP", as_string=True)
            sevr, _, _ = _get(rec + ".SEVR")
            errs, _, _ = _get(rec + ".ERRS", as_string=True)
        if sevr and sevr >= 2:
            raise RuntimeError(f"{rec}: no reply to {command!r} ({errs or 'alarm'})")
        reply = (reply or "").strip()
        if expected is None or reply.startswith(expected):
            return reply
    raise RuntimeError(
        f"{rec}: reply {reply!r} does not belong to {command!r} "
        "(stale or interleaved) -- not trusted"
    )


def _expected_reply_prefixes(command):
    """':GS3' -> (':S3,', ':E3,'); None for controller-wide commands, whose
    reply cannot be told apart this way."""
    body = command[2:]
    i = len(body)
    while i > 0 and body[i - 1].isdigit():
        i -= 1
    key, channel = body[:i], body[i:]
    if not channel:
        return None
    return (f":{key}{channel},", f":E{channel},")


def _parse(reply):
    """':S0,3' -> ('S', [0, 3]); ':E0,129' -> ('E', [0, 129]);
    ':N18' -> ('N', [18])."""
    if not reply.startswith(":"):
        return None, reply
    body = reply[1:]
    i = 0
    while i < len(body) and body[i].isalpha():
        i += 1
    key, rest = body[:i], body[i:]
    try:
        values = [int(v) for v in rest.split(",") if v != ""]
    except ValueError:
        values = rest
    return key, values


def _describe(reply):
    key, values = _parse(reply)
    if key == "E" and isinstance(values, list) and len(values) == 2:
        code = values[1]
        return f"error {code}: {MCS_ERRORS.get(code, 'unknown code')}"
    if key == "S" and isinstance(values, list) and len(values) == 2:
        return MCS_STATUS.get(values[1], f"status {values[1]}")
    if key in ("ST", "PPK", "P") and isinstance(values, list) and len(values) == 2:
        return str(values[1])
    return reply


def controller_state(prefix, address):
    """Ask the controller about one channel (address = channel number - 1)."""
    out = {}
    for label, cmd in [
        ("status", f":GS{address}"),
        ("sensor_type", f":GST{address}"),
        ("position_nm", f":GP{address}"),
        ("position_known", f":GPPK{address}"),
    ]:
        try:
            reply = query(prefix, cmd)
            out[label] = _describe(reply)
            out[label + "_raw"] = reply
        except Exception as e:
            out[label] = f"query failed: {e}"
            out[label + "_raw"] = None
    return out


def controller_global(prefix):
    out = {}
    for label, cmd in [
        ("channels", ":GNC"),
        ("sensor_mode", ":GSE"),
        ("interface_version", ":GIV"),
    ]:
        try:
            out[label] = query(prefix, cmd)
        except Exception as e:
            out[label] = f"query failed: {e}"
    return out


# ------------------------------------------------------------------ verdicts


def _n_channels(ctrl_global):
    key, values = _parse(str((ctrl_global or {}).get("channels", "")))
    if key == "N" and isinstance(values, list) and values:
        return values[0]
    return None


def assess(state, ioc_start=None, ctrl=None, n_channels=None):
    """List of findings (strings) for one channel; empty means healthy.

    n_channels: number of channels the controller itself reports (:GNC)."""
    findings = []
    if not state["connected"]:
        return ["motor record not reachable over CA (IOC down / wrong PV)"]
    never = state["last_update"] is None or state["last_update"] < _NEVER
    stale = (
        ioc_start is not None
        and state["last_update"] is not None
        and state["last_update"] < ioc_start - 1
    )
    flags = state["msta_flags"]
    if never or stale:
        findings.append(
            "motor record never updated by the driver since the IOC started: "
            "the driver does not poll this axis. Typical when nothing was "
            "connected to the channel when the IOC started (a stage plugged "
            "in later stays dead until the IOC is restarted); otherwise the "
            "controller does not answer for this channel"
        )
    elif "encoder_present" not in flags:
        findings.append("no encoder/sensor reported (MSTA bit 8 clear)")
    if "comm_error" in flags:
        findings.append("driver reports a communication error (MSTA bit 12)")
    if "problem" in flags:
        findings.append("driver reports a problem (MSTA bit 9)")
    if state["record_severity"] and state["record_severity"] >= 2:
        findings.append(f"motor record in alarm: {state['record_alarm']}")
    address = split_pvname(state["pv"])[1] - 1
    if n_channels is not None and address >= n_channels:
        findings.append(
            f"controller reports only {n_channels} channels: address "
            f"{address} does not exist for it -- its module is not detected "
            "(module failure, or not seated/powered)"
        )
    if ctrl:
        for key in ("status", "sensor_type", "position_nm"):
            value = str(ctrl.get(key, ""))
            if value.startswith("error") or value.startswith("query failed"):
                findings.append(f"controller {key}: {value}")
        if ctrl.get("position_known") == "0":
            findings.append("controller: physical position unknown (not referenced)")
        if never and not any(f.startswith("controller") for f in findings):
            findings.append(
                "controller answers normally for this channel but the IOC "
                "does not poll it: the channel was probably unavailable when "
                "the IOC started -- restart the IOC once it answers"
            )
    return findings


def module_summary(results):
    """Group failing channels by 3-channel module of the controller."""
    modules = {}
    for r in results:
        _, n = split_pvname(r["pv"])
        modules.setdefault((n - 1) // CHANNELS_PER_MODULE, []).append(r)
    lines = []
    for m, rs in sorted(modules.items()):
        failing = [r for r in rs if r["findings"]]
        if failing and len(failing) == len(rs) == CHANNELS_PER_MODULE:
            chans = [split_pvname(r["pv"])[1] for r in rs]
            lines.append(
                f"all channels of module {m + 1} (channels {chans[0]}-{chans[-1]}, "
                f"addresses {chans[0] - 1}-{chans[-1] - 1}) fail together: "
                "either nothing is connected to any of them, or the module "
                "itself is not working -- query_controller=True (channel "
                "count, sensor per channel) tells which"
            )
    return lines


# -------------------------------------------------------------------- report


def _age(ts):
    if not ts or ts < _NEVER:
        return "never"
    s = time.time() - ts
    for unit, div in (("d", 86400), ("h", 3600), ("min", 60)):
        if s >= div:
            return f"{s / div:.1f} {unit} ago"
    return f"{s:.0f} s ago"


def print_report(prefix, ioc, results, ctrl_global=None, summary=()):
    print(
        f"{prefix}  IOC {ioc['ioc']}  started {ioc['started']}  "
        f"(uptime {ioc['uptime']})"
    )
    if ctrl_global:
        print("controller: " + "  ".join(f"{k}={v}" for k, v in ctrl_global.items()))
    print()
    for r in results:
        _, n = split_pvname(r["pv"])
        verdict = "OK " if not r["findings"] else "BAD"
        line = (
            f"{verdict} ch{n:<2} addr {n - 1:<2} {str(r['description'])[:22]:<22} "
            f"MSTA={r['msta']!s:<6} rbv={r['readback']!s:<11} "
            f"updated {_age(r['last_update'])}"
        )
        if r.get("controller"):
            c = r["controller"]
            line += (
                f"  | ctrl: {c.get('status')}, sensor {c.get('sensor_type')}, "
                f"pos_known {c.get('position_known')}"
            )
        print(line)
        for f in r["findings"]:
            print(f"      - {f}")
    for line in summary:
        print("\n* " + line)


def diagnose_channels(pvnames, query_controller=False, verbose=True):
    """Diagnose motor records (full PV names) that belong to one controller."""
    prefix = split_pvname(pvnames[0])[0]
    ioc = ioc_info(prefix)
    ioc_start = _ioc_start_posix(ioc["started"])
    ctrl_global = None
    can_query = query_controller and controller_generation(prefix) == "MCS"
    if query_controller and not can_query:
        print(
            f"{prefix}: MCS2-generation controller, controller queries "
            "not implemented -- EPICS-level diagnosis only"
        )
    if can_query:
        ctrl_global = controller_global(prefix)
    results = []
    for pv in pvnames:
        state = epics_state(pv)
        ctrl = None
        if can_query and state["connected"]:
            ctrl = controller_state(prefix, split_pvname(pv)[1] - 1)
        state["controller"] = ctrl
        state["findings"] = assess(
            state, ioc_start, ctrl, n_channels=_n_channels(ctrl_global)
        )
        results.append(state)
    summary = module_summary(results)
    if verbose:
        print_report(prefix, ioc, results, ctrl_global, summary)
    return results


def diagnose_controller(pvbase, channels=range(1, 19), **kwargs):
    """pvbase like 'SARES20-MCS1:MOT_'."""
    return diagnose_channels([f"{pvbase}{n}" for n in channels], **kwargs)
