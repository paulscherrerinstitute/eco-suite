"""Alarms on Detectors: thresholds, timing, and display.

An :class:`Alarm` watches any Detector (anything with ``get_current_value()``)
and turns its readings into a timed alarm state -- see
:mod:`eco.alarms.tracker` for the state machine (ok -> pending -> active ->
acked -> clearing -> ok, plus disabled/shelved/inhibited/unknown). Every
parameter is a settable adjustable on the alarm itself::

    a = alarms.overview.swissfel.Electron_Beam
    a.min(5); a.delay(30); a.level("critical")      # or a.min.set_target_value(5)
    a.shelve(60)          # silence for an hour
    a.ack()               # acknowledge
    a.settings()          # all parameters

Parameter values set this way are operator *overrides*, stored in the shared
alarm settings file (:mod:`eco.alarms.store`), so they reach every session
and the alarm monitor; the constructor arguments are the defaults
(``reset_settings()`` returns to them).

Level decides who hears about it once raised:
``display`` (only shown, the default), ``warning`` (a normal push message to
whoever is on shift), ``critical`` (a repeating emergency push until
acknowledged, with escalation) -- the sending is done by the alarm monitor
(:mod:`eco.alarms.monitor`), never by a display.

The timed state shown in displays comes from the monitor's ``state.json``
while a monitor is running. Without one, displays evaluate locally on each
look, so `delay` counting then only advances while somebody is looking.

:class:`AlarmGroup` and :class:`AlarmPanel` are trees of alarms for an
alarm-overview panel (see :mod:`eco.devices_general.alarms`);
:class:`eco.alarms.system.AlarmSystem` is the namespace-level object.
"""

from __future__ import annotations

import getpass
import re
import time
from enum import IntEnum

import colorama

from eco.alarms import tracker as tr
from eco.alarms.store import SETTINGS, STATE
from eco.elements.adjustable import AdjustableGetSet
from eco.elements.assembly import Assembly
from eco.elements.detector import DetectorGet

_RESET = colorama.Style.RESET_ALL
_DIM = colorama.Style.DIM

LEVELS = ("display", "warning", "critical")

#: seconds after which the monitor's state.json counts as stale
MONITOR_STALE_S = 20.0

# ---------------------------------------------------------------------------
# colours / formatting

_STATE_COLOR = {
    tr.ACTIVE: colorama.Fore.RED + colorama.Style.BRIGHT,
    tr.CLEARING: colorama.Fore.MAGENTA,
    tr.ACKED: colorama.Fore.RED,
    tr.PENDING: colorama.Fore.YELLOW,
    tr.STATE_OK: colorama.Fore.GREEN,
    tr.STATE_UNKNOWN: _DIM,
    tr.INHIBITED: colorama.Fore.BLUE,
    tr.SHELVED: colorama.Fore.CYAN,
    tr.DISABLED: _DIM,
    # value-only nuances
    "near": colorama.Fore.YELLOW,
}

_STATE_GLYPH = {
    tr.ACTIVE: "●",
    tr.CLEARING: "◐",
    tr.ACKED: "◉",
    tr.PENDING: "◔",
    tr.STATE_OK: "●",
    tr.STATE_UNKNOWN: "○",
    tr.INHIBITED: "⊘",
    tr.SHELVED: "◌",
    tr.DISABLED: "·",
}

_LEVEL_TEXT = {"display": "display", "warning": "warning", "critical": "CRITICAL"}


def _colored(text, state):
    return f"{_STATE_COLOR.get(state, '')}{text}{_RESET}"


def _fmt_num(v):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{v:.4g}"
    s = " ".join(str(v).split())
    return s if len(s) <= 24 else s[:23] + "…"


def fmt_duration(s):
    """12s, 4m12s, 3h05m, 2d04h."""
    if s is None:
        return ""
    s = int(max(0, s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600:02d}h"


def _fmt_range(lo, hi):
    if lo is None and hi is None:
        return "(EPICS)"
    if lo is None:
        return f"≤ {_fmt_num(hi)}"
    if hi is None:
        return f"≥ {_fmt_num(lo)}"
    return f"[{_fmt_num(lo)}, {_fmt_num(hi)}]"


# ---------------------------------------------------------------------------
# severity (instantaneous, kept for compatibility)


class AlarmSeverity(IntEnum):
    OK = 0
    WARNING = 1
    ALARM = 2
    UNKNOWN = 3

    def __str__(self):
        return self.name


#: EPICS alarm severity (0=NO_ALARM, 1=MINOR, 2=MAJOR, 3=INVALID) ->
#: AlarmSeverity, for alarms without limits of their own.
_EPICS_SEVERITY = {
    0: AlarmSeverity.OK,
    1: AlarmSeverity.WARNING,
    2: AlarmSeverity.ALARM,
    3: AlarmSeverity.UNKNOWN,
}

_SEVERITY_CONDITION = {
    AlarmSeverity.OK: tr.OK,
    AlarmSeverity.WARNING: tr.OK,
    AlarmSeverity.ALARM: tr.BAD,
    AlarmSeverity.UNKNOWN: tr.UNKNOWN,
}


# ---------------------------------------------------------------------------
# monitor snapshot


def monitor_snapshot(max_age=MONITOR_STALE_S):
    """The running monitor's ``state.json`` document, or `None` if there is
    no monitor or its last heartbeat is older than `max_age` seconds."""
    try:
        doc = STATE.read()
    except Exception:
        return None
    hb = (doc or {}).get("monitor", {}).get("heartbeat")
    if not hb or time.time() - hb > max_age:
        return None
    return doc


def _current_user():
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------


def _parse_level(v):
    v = str(v).strip().lower()
    if v not in LEVELS:
        raise ValueError(f"alarm level must be one of {LEVELS}, not {v!r}")
    return v


def _parse_limit(v):
    if v is None or (isinstance(v, str) and v.strip().lower() in ("", "none", "-")):
        return None
    return float(v)


def _parse_seconds(v):
    v = float(v or 0)
    if v < 0:
        raise ValueError("delay must be >= 0 s")
    return v


def _parse_bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _parse_id_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return [str(s) for s in v]


#: setting name -> parser. Order = order in .settings().
_SETTINGS = {
    "enabled": _parse_bool,
    "min": _parse_limit,
    "max": _parse_limit,
    "delay": _parse_seconds,
    "clear_delay": _parse_seconds,
    "level": _parse_level,
    "unknown_is_alarm": _parse_bool,
    "inhibit_by": _parse_id_list,
    "note": lambda v: "" if v is None else str(v),
}


def _qt_or_generic(obj, kind, **kwargs):
    """`_default_widget` target: the alarm Qt window, or the generic
    property grid in a notebook / without Qt."""
    from eco.utilities.utilities import is_notebook

    if not is_notebook():
        try:
            from eco.widgets import alarms_qt
        except ImportError:
            alarms_qt = None
        if alarms_qt is not None:
            cls = alarms_qt.AlarmDetailQt if kind == "detail" else alarms_qt.AlarmTableQt
            return cls(obj).start()
    return obj._widget_assembly(**kwargs)


class Alarm(Assembly):
    """An alarm on one Detector.

    Parameters
    ----------
    detector : Detector or str
        Anything with ``get_current_value()``, or a dotted namespace path
        (``"mon_kb.intensity"``) resolved on first use via `resolver`.
    min, max : float or None
        Alarm range; the alarm condition is ``value < min`` or
        ``value > max``. With neither, the detector's own EPICS severity is
        used (MAJOR = alarm) if it has ``get_severity()``.
    delay : float
        Seconds the condition must persist before the alarm is raised.
    clear_delay : float
        Seconds the value must be fine again before a raised alarm clears.
    level : "display" | "warning" | "critical"
    unknown_is_alarm : bool
        Treat an unreadable value (disconnected IOC) as an alarm condition.
    inhibit_by : list of alarm ids
        While any of these alarms is not ok (e.g. "no electron beam"), this
        alarm is ``inhibited`` instead of raised.
    label : str
        Short human-readable name for messages; default the alarm name.
    alarm_id : str
        Key for the shared settings; default the full alias name.
    """

    _default_widget = "_widget_alarm"

    def _widget_alarm(self, **kwargs):
        """Live state plus editable settings (Qt)."""
        return _qt_or_generic(self, "detail", **kwargs)

    def __init__(
        self,
        detector,
        min=None,
        max=None,
        name=None,
        unit=None,
        delay=10.0,
        clear_delay=10.0,
        level="display",
        enabled=True,
        unknown_is_alarm=False,
        inhibit_by=(),
        note="",
        label=None,
        alarm_id=None,
        resolver=None,
    ):
        super().__init__(name=name)
        self._detector = detector
        self._resolver = resolver
        self.unit = unit
        self.label = label or name
        self._alarm_id = alarm_id
        self._defaults = {
            "enabled": _parse_bool(enabled),
            "min": _parse_limit(min),
            "max": _parse_limit(max),
            "delay": _parse_seconds(delay),
            "clear_delay": _parse_seconds(clear_delay),
            "level": _parse_level(level),
            "unknown_is_alarm": _parse_bool(unknown_is_alarm),
            "inhibit_by": _parse_id_list(inhibit_by),
            "note": note or "",
        }
        self.tracker = tr.AlarmTracker()
        for key in _SETTINGS:
            self._append(
                AdjustableGetSet,
                (lambda k=key: self.get_setting(k)),
                (lambda v, k=key: self.set_setting(k, v)),
                name=key,
                is_setting=True,
                is_display=False,
                is_status=False,
            )
        self._append(
            DetectorGet,
            lambda: self.get_state(),
            name="state",
            is_setting=False,
            is_display=False,
            is_status=True,
        )

    # -- identity ---------------------------------------------------------
    @property
    def alarm_id(self):
        if self._alarm_id:
            return self._alarm_id
        try:
            return self.alias.get_full_name()
        except Exception:
            return self.name

    @property
    def detector(self):
        if isinstance(self._detector, str):
            if self._resolver is None:
                raise RuntimeError(
                    f"alarm {self.alarm_id}: no resolver for detector path "
                    f"{self._detector!r}"
                )
            self._detector = self._resolver(self._detector)
        return self._detector

    @property
    def detector_path(self):
        """Dotted name of the watched detector, for messages/tables."""
        if isinstance(self._detector, str):
            return self._detector
        alias = getattr(self._detector, "alias", None)
        try:
            return alias.get_full_name() if alias is not None else None
        except Exception:
            return None

    # -- settings ---------------------------------------------------------
    def _overrides(self):
        try:
            return SETTINGS.read().get(self.alarm_id, {})
        except Exception:
            return {}

    def get_setting(self, key):
        ov = self._overrides()
        if key in ov:
            return ov[key]
        return self._defaults.get(key)

    def get_settings(self):
        ov = self._overrides()
        return {k: ov.get(k, self._defaults.get(k)) for k in _SETTINGS}

    def set_setting(self, key, value):
        if key not in _SETTINGS:
            raise KeyError(f"unknown alarm setting {key!r}; known: {list(_SETTINGS)}")
        value = _SETTINGS[key](value)
        aid = self.alarm_id

        def upd(doc):
            entry = doc.setdefault(aid, {})
            if value == self._defaults.get(key):
                entry.pop(key, None)
            else:
                entry[key] = value
            entry["_changed"] = {"by": _current_user(), "at": time.time(), "key": key}

        SETTINGS.update(upd)

    def reset_settings(self):
        """Forget all operator overrides (back to the code defaults)."""
        aid = self.alarm_id

        def upd(doc):
            entry = doc.get(aid, {})
            keep = {k: entry[k] for k in ("shelved_until", "shelved_by") if k in entry}
            if keep:
                doc[aid] = keep
            else:
                doc.pop(aid, None)

        SETTINGS.update(upd)

    @property
    def min_value(self):
        return self.get_setting("min")

    @property
    def max_value(self):
        return self.get_setting("max")

    # -- shelve / ack -----------------------------------------------------
    def shelve(self, minutes=60, reason=""):
        """Silence this alarm for `minutes` (it shows as ``shelved``, a
        raised alarm is cleared, nobody is paged). ``unshelve()`` ends it."""
        aid = self.alarm_id
        until = time.time() + 60.0 * float(minutes)

        def upd(doc):
            e = doc.setdefault(aid, {})
            e["shelved_until"] = until
            e["shelved_by"] = {"by": _current_user(), "at": time.time(), "reason": reason}

        SETTINGS.update(upd)
        return until

    def unshelve(self):
        aid = self.alarm_id

        def upd(doc):
            e = doc.get(aid, {})
            e.pop("shelved_until", None)
            e.pop("shelved_by", None)

        SETTINGS.update(upd)

    def shelved_until(self):
        t = self._overrides().get("shelved_until")
        return t if t and t > time.time() else None

    def ack(self, by=None):
        """Acknowledge a raised alarm: stops repeating phone alarms and
        escalation. Goes to the monitor through the shared settings file."""
        by = by or _current_user()
        now = time.time()
        aid = self.alarm_id

        def upd(doc):
            doc.setdefault(aid, {})["ack_request"] = {"by": by, "at": now}

        SETTINGS.update(upd)
        self.tracker.ack(now, by=by)

    # -- reading ----------------------------------------------------------
    def get_current_value(self):
        return self.detector.get_current_value()

    def get_condition(self, value=None, read=True):
        """Instantaneous condition ``"ok"``/``"bad"``/``"unknown"``.
        Returns (condition, value)."""
        lo, hi = self.get_setting("min"), self.get_setting("max")
        if lo is not None or hi is not None:
            if read:
                try:
                    value = self.get_current_value()
                except Exception:
                    return tr.UNKNOWN, None
            if value is None:
                return tr.UNKNOWN, None
            try:
                if lo is not None and value < lo:
                    return tr.BAD, value
                if hi is not None and value > hi:
                    return tr.BAD, value
            except TypeError:
                return tr.UNKNOWN, value
            return tr.OK, value
        if read:
            try:
                value = self.get_current_value()
            except Exception:
                value = None
        return _SEVERITY_CONDITION[self._epics_severity()], value

    def _epics_severity(self):
        get_severity = getattr(self.detector, "get_severity", None)
        if get_severity is None:
            return AlarmSeverity.UNKNOWN
        try:
            sev = get_severity()
        except Exception:
            sev = None
        return _EPICS_SEVERITY.get(sev, AlarmSeverity.UNKNOWN)

    def get_severity(self):
        """Instantaneous `AlarmSeverity` (no delay, no latching)."""
        lo, hi = self.get_setting("min"), self.get_setting("max")
        if lo is None and hi is None:
            return self._epics_severity()
        cond, _ = self.get_condition()
        return {tr.OK: AlarmSeverity.OK, tr.BAD: AlarmSeverity.ALARM}.get(
            cond, AlarmSeverity.UNKNOWN
        )

    def is_ok(self):
        return self.get_severity() == AlarmSeverity.OK

    def check(self, now=None, inhibited=False):
        """Read the detector once and advance the local tracker. Returns the
        tracker events of this step (``["raised"]``, ``["cleared"]``, ...)."""
        now = time.time() if now is None else now
        s = self.get_settings()
        cond, value = self.get_condition()
        ack = self._overrides().get("ack_request")
        events = self.tracker.update(
            cond,
            now,
            value=value,
            delay=s["delay"],
            clear_delay=s["clear_delay"],
            enabled=s["enabled"],
            shelved_until=self.shelved_until(),
            inhibited=inhibited,
            unknown_is_alarm=s["unknown_is_alarm"],
        )
        if (
            ack
            and self.tracker.raised
            and not self.tracker.acked
            and ack.get("at", 0) >= (self.tracker.active_since or 0)
        ):
            events += self.tracker.ack(now, by=ack.get("by"))
        return events

    # -- state for display --------------------------------------------------
    def get_state_info(self, snapshot=None, evaluate=True):
        """Tracker fields (dict) plus ``source``: ``"monitor"`` from a running
        monitor's snapshot, else ``"local"`` (evaluated now if `evaluate`)."""
        snap = monitor_snapshot() if snapshot is None else snapshot
        if snap:
            d = snap.get("alarms", {}).get(self.alarm_id)
            if d is not None:
                return dict(d, source="monitor")
        if evaluate:
            try:
                self.check()
            except Exception:
                pass
        return dict(self.tracker.to_dict(), source="local")

    def get_state(self):
        return self.get_state_info()["state"]

    def _proximity(self, value, state):
        """Colour key for the value cell: the state, or ``"near"`` within
        20% of a bound while ok."""
        if state != tr.STATE_OK:
            return state
        lo, hi = self.get_setting("min"), self.get_setting("max")
        if lo is None or hi is None or value is None:
            return state
        try:
            margin = 0.2 * (hi - lo)
            if value < lo + margin or value > hi - margin:
                return "near"
        except TypeError:
            pass
        return state

    def display_row(self, label=None, snapshot=None, now=None):
        now = time.time() if now is None else now
        info = self.get_state_info(snapshot=snapshot)
        state = info.get("state") or tr.STATE_UNKNOWN
        value = info.get("last_value")
        s = self.get_settings()
        if state in tr.RAISED_STATES:
            since = info.get("active_since")
        elif state == tr.PENDING:
            since = info.get("violating_since")
        else:
            since = info.get("state_since")
        dur = fmt_duration(now - since) if since else ""
        state_txt = state.upper() if state in tr.RAISED_STATES else state
        if state in tr.RAISED_STATES and info.get("acked_by"):
            state_txt += f" · {info['acked_by']}"
        if state == tr.SHELVED:
            until = self.shelved_until()
            if until:
                dur = f"{fmt_duration(until - now)} left"
        level = s["level"]
        level_txt = _LEVEL_TEXT[level]
        if level == "critical":
            level_txt = colorama.Style.BRIGHT + level_txt + _RESET
        elif level == "display":
            level_txt = _DIM + level_txt + _RESET
        unit = f" {self.unit}" if self.unit else ""
        return [
            _colored(_STATE_GLYPH.get(state, "?"), state),
            label or self.label or self.name,
            _colored(_fmt_num(value) + unit, self._proximity(value, state)),
            _fmt_range(s["min"], s["max"]),
            level_txt,
            _colored(state_txt, state),
            dur,
            str(info.get("count") or 0),
        ]

    def get_display_str(self, tablefmt="simple", **kwargs):
        rows = [self.display_row()]
        table = _render_alarm_table(rows, tablefmt=tablefmt)
        s = self.get_settings()
        pv = getattr(self._detector, "pvname", None)
        watching = self.detector_path or repr(self._detector)
        extra = [f"watching: {pv} (PV)" if pv else f"watching: {watching}"]
        extra.append(
            f"delay {fmt_duration(s['delay'])}, clear after {fmt_duration(s['clear_delay'])}"
            + (", unknown = alarm" if s["unknown_is_alarm"] else "")
            + ("" if s["enabled"] else ", DISABLED")
        )
        if s["inhibit_by"]:
            extra.append("inhibited while not ok: " + ", ".join(s["inhibit_by"]))
        if s["note"]:
            extra.append(f"note: {s['note']}")
        info = self.get_state_info(evaluate=False)
        if info.get("count"):
            ta = info.get("total_active_s", 0) or 0
            if info.get("active_since"):
                ta += time.time() - info["active_since"]
            extra.append(f"raised {info['count']}x, {fmt_duration(ta)} in alarm in total")
        extra.append(f"state from: {info.get('source')}")
        return table + "\n" + "\n".join(_DIM + "  " + e + _RESET for e in extra)

    def __repr__(self):
        return self.get_display_str()


# ---------------------------------------------------------------------------
# tables

ALARM_TABLE_HEADERS = ["", "Alarm", "Value", "Range", "Level", "State", "For", "#"]
_ALARM_TABLE_COLALIGN = ["center", "left", "right", "left", "left", "left", "right", "right"]


def _render_alarm_table(rows, group_keys=None, tablefmt="simple"):
    if not rows:
        return "(no alarms)"
    from eco.utilities.tables import format_table, section_row_styles

    # no section shading: in an alarm table the colour is the state, and the
    # section is already part of the alarm name
    row_styles = [""]
    return format_table(
        rows,
        headers=ALARM_TABLE_HEADERS,
        tablefmt=tablefmt,
        maxcolwidths=[None, None, 26, None, None, None, None, None],
        colalign=_ALARM_TABLE_COLALIGN,
        row_styles=row_styles,
    )


def _safe_attr_name(label):
    """Free-text section label -> valid Python identifier."""
    s = re.sub(r"\W+", "_", label.strip()).strip("_").lower()
    if not s or s[0].isdigit():
        s = f"g_{s}"
    return s


def evaluate_alarms(alarms, now=None):
    """Advance the local trackers of `alarms` (iterable of `Alarm`) one
    step, resolving ``inhibit_by`` among them. Returns
    ``{alarm_id: events}``. Alarms inhibiting others are checked first."""
    now = time.time() if now is None else now
    alarms = list(alarms)
    by_id = {a.alarm_id: a for a in alarms}
    done, out = set(), {}

    def lookup(other):
        # ids carry the namespace prefix ("bernina.alarms...."); accept
        # "alarms.overview...." or any other dotted tail too
        if other in by_id:
            return by_id[other]
        tail = "." + other.lower()
        hits = [a for i, a in by_id.items() if i.lower().endswith(tail)]
        return hits[0] if len(hits) == 1 else None

    def run(a, stack=()):
        aid = a.alarm_id
        if aid in done:
            return
        inhibited = False
        for other in a.get_setting("inhibit_by") or []:
            o = lookup(other)
            if o is None or o.alarm_id in stack:
                continue
            run(o, stack + (aid,))
            if o.tracker.state not in (tr.STATE_OK,):
                inhibited = True
        try:
            out[aid] = a.check(now=now, inhibited=inhibited)
        except Exception as e:
            out[aid] = []
            a.tracker.last_condition = f"error: {e}"
        done.add(aid)

    for a in alarms:
        run(a)
    return out


def summary_counts(rows_states):
    """Count states for a summary line."""
    c = {}
    for st in rows_states:
        c[st] = c.get(st, 0) + 1
    return c


def summary_line(states):
    c = summary_counts(states)
    parts = []
    n_raised = c.get(tr.ACTIVE, 0) + c.get(tr.ACKED, 0) + c.get(tr.CLEARING, 0)
    if n_raised:
        s = f"{n_raised} raised"
        if c.get(tr.ACTIVE):
            s += f" ({c[tr.ACTIVE]} unacknowledged)"
        parts.append(_colored(s, tr.ACTIVE))
    for st, word in (
        (tr.PENDING, "pending"),
        (tr.STATE_UNKNOWN, "unknown"),
        (tr.INHIBITED, "inhibited"),
        (tr.SHELVED, "shelved"),
        (tr.DISABLED, "disabled"),
        (tr.STATE_OK, "ok"),
    ):
        if c.get(st):
            parts.append(_colored(f"{c[st]} {word}", st))
    return " · ".join(parts) if parts else "no alarms"


class AlarmGroup(Assembly):
    """One labelled section of an alarm panel -- a flat collection of
    named `Alarm`s."""

    _default_widget = "_widget_alarms"

    def _widget_alarms(self, **kwargs):
        """Alarm table with acknowledge/shelve (Qt)."""
        return _qt_or_generic(self, "table", **kwargs)

    def __init__(self, channels, name=None, label=None, alarm_defaults=None):
        """`channels`: iterable of dicts, each either
        ``{"name": ..., "detector": <Detector>, "min": ..., "max": ...}`` or
        ``{"name": ..., "pv": <PV name>, ...}`` (builds a
        `DetectorPvData`); any further keys are `Alarm` arguments.
        `alarm_defaults` are `Alarm` arguments applied to every channel
        unless the channel sets them itself."""
        super().__init__(name=name)
        self.label = label if label is not None else name
        self._channel_names = []
        for ch in channels:
            ch = dict(alarm_defaults or {}, **ch)
            cname = ch.pop("name")
            detector = ch.pop("detector", None)
            if detector is None:
                pv = ch.pop("pv")
                from eco.epics_utils.detector import DetectorPvData

                detector = DetectorPvData(pv, name=f"{cname}_pv")
            self._append(Alarm, detector=detector, name=cname, is_status=True, **ch)
            self._channel_names.append(cname)

    def get_alarms(self):
        return [getattr(self, n) for n in self._channel_names]

    def get_severities(self):
        return {n: getattr(self, n).get_severity() for n in self._channel_names}

    def worst_severity(self):
        sevs = list(self.get_severities().values())
        return max(sevs) if sevs else AlarmSeverity.UNKNOWN

    def _rows(self, snapshot, now, prefix=""):
        rows, states = [], []
        if not snapshot:
            evaluate_alarms(self.get_alarms(), now=now)
        for cname in self._channel_names:
            a = getattr(self, cname)
            row = a.display_row(label=prefix + cname, snapshot=snapshot or {}, now=now)
            rows.append(row)
            states.append(a.get_state_info(snapshot=snapshot or {}, evaluate=False)["state"])
        return rows, states

    def get_display_str(self, tablefmt="simple", **kwargs):
        now = time.time()
        snap = monitor_snapshot()
        rows, states = self._rows(snap, now)
        return summary_line(states) + "\n" + _render_alarm_table(rows, tablefmt=tablefmt)


class AlarmPanel(Assembly):
    """A full alarm-overview panel: named `AlarmGroup` sections."""

    _default_widget = "_widget_alarms"

    def _widget_alarms(self, **kwargs):
        """Alarm table with acknowledge/shelve (Qt)."""
        return _qt_or_generic(self, "table", **kwargs)

    def __init__(self, sections, name=None, alarm_defaults=None):
        """`sections`: iterable of ``(section_label, channels)`` pairs,
        `channels` as for `AlarmGroup`."""
        super().__init__(name=name)
        self._group_names = []
        for label, channels in sections:
            gname = _safe_attr_name(label)
            while gname in self._group_names:
                gname += "_"
            self._append(
                AlarmGroup,
                channels,
                name=gname,
                label=label,
                alarm_defaults=alarm_defaults,
                is_display="recursive",
            )
            self._group_names.append(gname)

    def get_groups(self):
        return [getattr(self, g) for g in self._group_names]

    def get_alarms(self):
        return [a for g in self.get_groups() for a in g.get_alarms()]

    def get_severities(self):
        out = {}
        for gname in self._group_names:
            for cname, sev in getattr(self, gname).get_severities().items():
                out[f"{gname}.{cname}"] = sev
        return out

    def worst_severity(self):
        sevs = list(self.get_severities().values())
        return max(sevs) if sevs else AlarmSeverity.UNKNOWN

    def display_rows(self, snapshot, now, with_section=True):
        if not snapshot:
            evaluate_alarms(self.get_alarms(), now=now)
        rows, keys, states = [], [], []
        for g in self.get_groups():
            for cname in g._channel_names:
                a = getattr(g, cname)
                label = f"{g.label}.{cname}" if with_section else cname
                rows.append(a.display_row(label=label, snapshot=snapshot or {}, now=now))
                keys.append(g.label)
                states.append(
                    a.get_state_info(snapshot=snapshot or {}, evaluate=False)["state"]
                )
        return rows, keys, states

    def get_display_str(self, tablefmt="simple", **kwargs):
        now = time.time()
        snap = monitor_snapshot()
        rows, keys, states = self.display_rows(snap, now)
        return summary_line(states) + "\n" + _render_alarm_table(
            rows, group_keys=keys, tablefmt=tablefmt
        )

    # ---- clickable panel -------------------------------------------------
    def _widget_svg_panel(self, live=False, cols=6, path=None, **kwargs):
        """Tile panel SVG (one tile per alarm: value, state colour, time in
        state), grouped by section; returns its file path. Click a tile to
        inspect that alarm."""
        from eco.alarms.svg import build_alarm_svg
        from eco.utilities.tempfiles import user_temp_svg_path

        now = time.time()
        snap = monitor_snapshot() if live else None
        if live and not snap:
            evaluate_alarms(self.get_alarms(), now=now)
        tiles = []
        for gname in self._group_names:
            g = getattr(self, gname)
            for cname in g._channel_names:
                a = getattr(g, cname)
                tiles.append(alarm_tile(a, f"{gname}.{cname}", g.label, snap, now, live))
        svg_text = build_alarm_svg(tiles, title=self.name or "alarms", cols=cols)
        if path is None:
            path = user_temp_svg_path("eco_alarms", self.name or id(self))
        with open(path, "w") as f:
            f.write(svg_text)
        return path


def alarm_tile(alarm, relpath, section, snapshot, now, live=True):
    """Dict for :func:`eco.alarms.svg.build_alarm_svg`."""
    s = alarm.get_settings()
    if live:
        info = alarm.get_state_info(snapshot=snapshot or {}, evaluate=False)
        state = info.get("state")
        value = info.get("last_value")
        if state in tr.RAISED_STATES:
            since = info.get("active_since")
        elif state == tr.PENDING:
            since = info.get("violating_since")
        else:
            since = info.get("state_since")
    else:
        state, value, since, info = None, None, None, {}
    unit = f" {alarm.unit}" if alarm.unit else ""
    return {
        "relpath": relpath,
        "label": alarm.label or alarm.name,
        "section": section,
        "state": state,
        "value": None if not live else _fmt_num(value) + unit,
        "range": _fmt_range(s["min"], s["max"]),
        "level": s["level"],
        "duration": fmt_duration(now - since) if since else "",
        "count": info.get("count") or 0,
    }
