"""Backend-neutral alarm view data, shared by the Qt window (and later the
web app). Everything here may read files and detectors: call it from a
worker thread, never from a GUI slot.
"""

from __future__ import annotations

import time
from datetime import datetime

from eco.alarms import tracker as tr

#: background tint per state (light theme; text stays dark)
STATE_TINT = {
    tr.ACTIVE: "#f8c4c8",
    tr.CLEARING: "#ead3f3",
    tr.ACKED: "#fbe1e3",
    tr.PENDING: "#ffefc2",
    tr.STATE_OK: None,
    tr.STATE_UNKNOWN: "#eceef1",
    tr.INHIBITED: "#e3edf9",
    tr.SHELVED: "#e2f5f5",
    tr.DISABLED: "#f3f4f6",
}
#: dot colour per state
STATE_DOT = {
    tr.ACTIVE: "#d7263d",
    tr.CLEARING: "#a53fc4",
    tr.ACKED: "#b0475a",
    tr.PENDING: "#e0a100",
    tr.STATE_OK: "#2e9e44",
    tr.STATE_UNKNOWN: "#9aa1ab",
    tr.INHIBITED: "#4a78b5",
    tr.SHELVED: "#2b9a9a",
    tr.DISABLED: "#c3c7cd",
}


def _entries(obj):
    """[(path below obj, section label, Alarm)] for an AlarmSystem,
    AlarmPanel, AlarmGroup or single Alarm."""
    from eco.elements.alarm import Alarm, AlarmGroup, AlarmPanel

    if isinstance(obj, Alarm):
        return [("", "", obj)]
    if isinstance(obj, AlarmGroup):
        return [(c, obj.label, getattr(obj, c)) for c in obj._channel_names]
    if isinstance(obj, AlarmPanel):
        out = []
        for g in obj._group_names:
            grp = getattr(obj, g)
            out += [(f"{g}.{c}", grp.label, getattr(grp, c)) for c in grp._channel_names]
        return out
    # AlarmSystem
    out = []
    for p in obj._panel_names:
        panel = getattr(obj, p)
        out += [(f"{p}.{path}", f"{p}: {sec}" if sec else p, a) for path, sec, a in _entries(panel)]
    for a in obj.custom.get_alarms():
        out.append((f"custom.{a.name}", "custom", a))
    return out


def alarm_rows(obj, now=None):
    """Row dicts for every alarm below `obj`, plus whether the states came
    from a running monitor."""
    from eco.elements.alarm import (
        _fmt_num,
        _fmt_range,
        evaluate_alarms,
        fmt_duration,
        monitor_snapshot,
    )

    now = time.time() if now is None else now
    entries = _entries(obj)
    snap = monitor_snapshot()
    if not snap:
        evaluate_alarms([a for _, _, a in entries], now=now)
    rows = []
    for path, section, a in entries:
        info = a.get_state_info(snapshot=snap or {}, evaluate=False)
        s = a.get_settings()
        state = info.get("state") or tr.STATE_UNKNOWN
        if state in tr.RAISED_STATES:
            since = info.get("active_since")
        elif state == tr.PENDING:
            since = info.get("violating_since")
        else:
            since = info.get("state_since")
        dur = fmt_duration(now - since) if since else ""
        if state == tr.SHELVED:
            until = a.shelved_until()
            dur = f"{fmt_duration(until - now)} left" if until else dur
        unit = f" {a.unit}" if a.unit else ""
        rows.append({
            "path": path,
            "id": a.alarm_id,
            "section": section,
            "label": a.label or a.name,
            "value": _fmt_num(info.get("last_value")) + unit,
            "range": _fmt_range(s["min"], s["max"]),
            "level": s["level"],
            "state": state,
            "acked_by": info.get("acked_by") if state in tr.RAISED_STATES else None,
            "duration": dur,
            "count": info.get("count") or 0,
            "enabled": s["enabled"],
        })
    return rows, bool(snap)


def header_info(system):
    """Armed flag, monitor status, on call -- for a system header."""
    from eco.alarms.shifts import TZ
    from eco.elements.alarm import monitor_snapshot

    snap = monitor_snapshot()
    info = {"armed": False, "monitor": None, "oncall": None, "problems": []}
    try:
        info["armed"] = system.is_armed()
    except Exception:
        pass
    if snap:
        m = snap["monitor"]
        info["monitor"] = {"host": (m.get("host") or "?").split(".")[0], "age": time.time() - m["heartbeat"]}
        info["problems"] = list(m.get("problems", []))[:5]
        oc = snap.get("oncall")
    else:
        oc = None
        try:
            o = system._plan(fetch=False).on_call()
            oc = {"primary": o.primary, "backup": o.backup,
                  "next_change": o.next_change.timestamp() if o.next_change else None}
        except Exception:
            pass
    if oc:
        nxt = oc.get("next_change")
        info["oncall"] = {
            "primary": oc.get("primary", []),
            "backup": oc.get("backup", []),
            "until": datetime.fromtimestamp(nxt, TZ).strftime("%a %H:%M") if nxt else None,
        }
    return info
