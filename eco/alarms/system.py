"""`AlarmSystem`: the namespace object ``alarms``.

Holds the alarm panels plus ``custom`` -- alarms put on *any* namespace
detector from a session::

    alarms.add(mon_kb.intensity, min=0.9, delay=30, level="critical")
    alarms.add("las.xlt", max=2e-12, label="laser delay")       # by path
    alarms.custom.mon_kb__intensity.min(0.8)                      # change later
    alarms.remove("mon_kb.intensity")

and the operator commands: ``alarms`` (overview), ``alarms.active()``,
``alarms.ack()``, ``alarms.shelve(alarm, minutes)``, ``alarms.oncall()``,
``alarms.upcoming()``, ``alarms.take_shift()``, ``alarms.leave_shift()``,
``alarms.arm()``/``disarm()``, ``alarms.people()``, ``alarms.set_phone_key()``,
``alarms.send_test()``, ``alarms.events()``, ``alarms.show(live=True)``.

User-added alarms are stored in the shared ``custom_alarms.json`` so the
monitor (another process) builds the same alarms from its own namespace.
"""

from __future__ import annotations

import getpass
import re
import time
from datetime import datetime, timedelta

import colorama

from eco.alarms import shifts as sh
from eco.alarms import tracker as tr
from eco.alarms.store import CUSTOM, EVENTS_LOG, PEOPLE, SYSTEM, read_jsonl
from eco.elements.alarm import (
    Alarm,
    AlarmPanel,
    _render_alarm_table,
    alarm_tile,
    evaluate_alarms,
    fmt_duration,
    monitor_snapshot,
    summary_line,
)
from eco.elements.assembly import Assembly

_R = colorama.Style.RESET_ALL
_B = colorama.Style.BRIGHT
_DIM = colorama.Style.DIM
_RED = colorama.Fore.RED + colorama.Style.BRIGHT
_GREEN = colorama.Fore.GREEN
_YELLOW = colorama.Fore.YELLOW

_ALARM_KWARGS = ("min", "max", "delay", "clear_delay", "level", "enabled", "unknown_is_alarm", "inhibit_by", "note", "unit", "label")


def _attr_for_path(path):
    s = re.sub(r"\W", "_", path.replace(".", "__"))
    return s if not s[0].isdigit() else "a_" + s


def _hm(dt):
    return dt.strftime("%a %H:%M")


class CustomAlarms(Assembly):
    """Alarms added on arbitrary detectors (`AlarmSystem.add`)."""

    def __init__(self, name="custom", resolver=None):
        super().__init__(name=name)
        self._resolver = resolver
        self._paths = {}  # attr -> detector path
        self._loaded_t = 0.0
        self.reload()

    def reload(self, max_age=0.0):
        if time.time() - self._loaded_t < max_age:
            return
        self._loaded_t = time.time()
        doc = CUSTOM.read(max_age=max_age)
        wanted = {_attr_for_path(p): (p, spec) for p, spec in doc.items()}
        for attr in [a for a in self._paths if a not in wanted]:
            self._drop(attr)
        for attr, (path, spec) in wanted.items():
            if attr in self._paths:
                continue
            kw = {k: v for k, v in spec.items() if k in _ALARM_KWARGS}
            try:
                self._append(
                    Alarm,
                    spec.get("_obj", path),
                    name=attr,
                    resolver=self._resolver,
                    is_status=True,
                    **kw,
                )
                self._paths[attr] = path
            except Exception:
                pass

    def _drop(self, attr):
        obj = self.__dict__.get(attr)
        if obj is not None:
            try:
                self.status_collection.remove(obj)
            except Exception:
                pass
            del self.__dict__[attr]
        self._paths.pop(attr, None)

    def get_alarms(self):
        return [getattr(self, a) for a in self._paths if a in self.__dict__]

    def display_rows(self, snapshot, now):
        alarms = self.get_alarms()
        if not snapshot:
            evaluate_alarms(alarms, now=now)
        rows = [a.display_row(label=self._paths.get(a.name, a.name), snapshot=snapshot or {}, now=now) for a in alarms]
        states = [a.get_state_info(snapshot=snapshot or {}, evaluate=False)["state"] for a in alarms]
        return rows, ["custom"] * len(rows), states

    def get_display_str(self, tablefmt="simple", **kwargs):
        now = time.time()
        rows, keys, states = self.display_rows(monitor_snapshot(), now)
        return summary_line(states) + "\n" + _render_alarm_table(rows, tablefmt=tablefmt)


class AlarmSystem(Assembly):
    """Namespace-level alarm system.

    `panels`: ``{attr: AlarmPanel instance or (factory, args, kwargs)}``.
    `namespace`: the namespace that user-added alarm paths
    (``"mon_kb.intensity"``) are resolved in -- the object, or
    ``"module:attr"`` (imported on first use, e.g.
    ``"eco.bernina.bernina:namespace"``).
    """

    def __init__(self, panels=None, name="alarms", namespace=None):
        super().__init__(name=name)
        self._namespace = namespace
        self._panel_names = []
        for attr, p in (panels or {}).items():
            if isinstance(p, tuple):
                factory, args, kwargs = (list(p) + [(), {}])[:3]
                self._append(factory, *args, name=attr, is_display="recursive", **kwargs)
            else:
                self._append(p, name=attr, is_display="recursive")
            self._panel_names.append(attr)
        self._append(CustomAlarms, name="custom", resolver=self._resolve, is_display="recursive")

    # ------------------------------------------------------------------
    # namespace resolution for user-added alarms
    def _resolve(self, path):
        ns = self._namespace
        if isinstance(ns, str):
            import importlib

            mod, _, attr = ns.partition(":")
            ns = getattr(importlib.import_module(mod), attr or "namespace")
            self._namespace = ns
        if ns is None:
            raise RuntimeError("AlarmSystem: no namespace to resolve alarm paths in")
        parts = path.split(".")
        try:
            obj = ns.get_obj(parts[0])
        except Exception:
            obj = getattr(ns, parts[0])
        for p in parts[1:]:
            obj = getattr(obj, p)
        return obj

    def get_all_alarms(self):
        out = []
        for p in self._panel_names:
            out += getattr(self, p).get_alarms()
        out += self.custom.get_alarms()
        return out

    def find(self, alarm):
        """An `Alarm` from an object, its id, a path below ``alarms`` or a
        user-added detector path."""
        if isinstance(alarm, Alarm):
            return alarm
        if not isinstance(alarm, str):
            path = _full_name(alarm)
            if path is None:
                raise ValueError(f"cannot find an alarm for {alarm!r}")
            alarm = path
        all_ = self.get_all_alarms()
        for a in all_:
            if alarm in (a.alarm_id, a.name, a.label):
                return a
        for a in all_:
            if a.alarm_id.endswith("." + alarm) or a.alarm_id.lower().endswith("." + alarm.lower()):
                return a
        attr = _attr_for_path(alarm)
        if attr in self.custom._paths:
            return getattr(self.custom, attr)
        raise KeyError(f"no alarm {alarm!r}")

    def reload_custom(self, max_age=0.0):
        self.custom.reload(max_age=max_age)

    # ------------------------------------------------------------------
    # adding alarms on any detector
    def add(self, detector, min=None, max=None, level="display", delay=10.0, clear_delay=10.0,
            label=None, unit=None, unknown_is_alarm=False, inhibit_by=(), note=""):
        """Put an alarm on any namespace detector (object or dotted path).
        Stored for every session and the monitor. Returns the `Alarm`."""
        obj = None
        if isinstance(detector, str):
            path = detector
        else:
            obj = detector
            path = _full_name(detector)
            if not path:
                raise ValueError(
                    "the detector has no namespace name, so the monitor could not find it; "
                    "pass a namespace detector or its dotted path"
                )
        if obj is None:
            obj = self._resolve(path)  # fail now rather than in the monitor
        if not hasattr(obj, "get_current_value"):
            raise TypeError(f"{path} has no get_current_value(), it is not a Detector")
        spec = {"min": min, "max": max, "level": level, "delay": delay, "clear_delay": clear_delay,
                "label": label or path, "unit": unit, "unknown_is_alarm": unknown_is_alarm,
                "inhibit_by": list(inhibit_by), "note": note,
                "_added": {"by": _user(), "at": time.time()}}
        if unit is None:
            spec.pop("unit")
        # validate before storing
        Alarm(obj, name="_check", **{k: v for k, v in spec.items() if k in _ALARM_KWARGS})

        def upd(doc):
            doc[path] = spec

        CUSTOM.update(upd)
        self.custom.reload()
        return self.find(path)

    def remove(self, alarm):
        """Remove a user-added alarm."""
        a = self.find(alarm)
        path = self.custom._paths.get(a.name)
        if path is None:
            raise ValueError(f"{a.alarm_id} belongs to a panel; disable it instead (.enabled(False))")

        def upd(doc):
            doc.pop(path, None)

        CUSTOM.update(upd)
        self.custom.reload()

    # ------------------------------------------------------------------
    # operator commands
    def ack(self, alarm=None, by=None):
        """Acknowledge one alarm, or every raised one."""
        if alarm is not None:
            self.find(alarm).ack(by=by)
            return
        snap = monitor_snapshot()
        n = 0
        for a in self.get_all_alarms():
            st = a.get_state_info(snapshot=snap, evaluate=snap is None)
            if st.get("state") == tr.ACTIVE or (st.get("state") in tr.RAISED_STATES and not st.get("acked")):
                a.ack(by=by)
                n += 1
        print(f"acknowledged {n} alarm(s)")

    def shelve(self, alarm, minutes=60, reason=""):
        until = self.find(alarm).shelve(minutes, reason=reason)
        print(f"shelved until {datetime.fromtimestamp(until, sh.TZ):%a %H:%M}")

    def unshelve(self, alarm):
        self.find(alarm).unshelve()

    def arm(self):
        """Allow the monitor to send phone messages."""
        SYSTEM.update(lambda d: d.update(armed=True, armed_by={"by": _user(), "at": time.time()}))
        print("alarm system ARMED: raised warning/critical alarms are sent to phones")

    def disarm(self):
        SYSTEM.update(lambda d: d.update(armed=False, armed_by={"by": _user(), "at": time.time()}))
        print("alarm system disarmed: nothing is sent (messages are only logged)")

    def is_armed(self):
        return bool(SYSTEM.read().get("armed"))

    def configure(self, **kw):
        """System settings, e.g. ``escalate_after=600``, ``retry=60``,
        ``sound_critical="persistent"``, ``heartbeat_url=...``. Without
        arguments: show them."""
        from eco.alarms.monitor import SYSTEM_DEFAULTS, system_settings

        bad = [k for k in kw if k not in SYSTEM_DEFAULTS or k == "armed"]
        if bad:
            raise KeyError(f"unknown/forbidden settings {bad}; known: {sorted(set(SYSTEM_DEFAULTS) - {'armed'})}")
        if kw:
            SYSTEM.update(lambda d: d.update(kw))
        return system_settings()

    # -- shifts ---------------------------------------------------------
    def shift_sheet(self, key=None, credentials=None):
        """Set (or show) the Google Sheet key of the shift plan."""
        if key is None:
            k, _ = sh.sheet_config()
            return f"https://docs.google.com/spreadsheets/d/{k}" if k else None

        def upd(d):
            d["shift_sheet_key"] = key
            if credentials:
                d["shift_sheet_credentials"] = credentials

        SYSTEM.update(upd)
        return sh.load_plan(max_age=0)

    def _plan(self, fetch=True):
        return sh.load_plan(fetch=fetch)

    def oncall(self, t=None):
        """Who gets alarms now (or at `t`)."""
        plan = self._plan()
        oc = plan.on_call(t)
        print(self._oncall_text(oc, plan))
        return oc

    def _oncall_text(self, oc, plan):
        keys = {k.lower() for k in PEOPLE.read()}

        def fmt(names):
            out = []
            for n in names:
                out.append(n if n.lower() in keys else f"{n} {_RED}(no phone key){_R}")
            return ", ".join(out)

        prim = fmt(oc.primary) if oc.primary else f"{_RED}nobody{_R}"
        lines = [f"on call: {_B}{prim}{_R}   backup: {fmt(oc.backup) or '—'}"]
        if oc.off:
            lines.append(f"{_DIM}off (deviations): {', '.join(oc.off)}{_R}")
        if oc.next_change:
            lines.append(f"{_DIM}next change {_hm(oc.next_change)}{_R}")
        if not oc.primary:
            esc = plan.escalation_contacts()
            lines.append(f"{_YELLOW}critical alarms go to: {', '.join(oc.backup or esc) or 'NOBODY'}{_R}")
        for p in plan.problems:
            lines.append(f"{_YELLOW}{p}{_R}")
        return "\n".join(lines)

    def upcoming(self, hours=48):
        """Shift timeline for the next `hours`."""
        from eco.utilities.tables import format_table

        plan = self._plan()
        rows = []
        for a, b, oc in plan.timeline(hours=hours):
            rows.append([_hm(a), _hm(b), ", ".join(oc.primary) or f"{_RED}nobody{_R}", ", ".join(oc.backup),
                         ", ".join(oc.off)])
        print(format_table(rows, headers=["from", "to", "on call", "backup", "off"]))
        for p in plan.problems:
            print(f"{_YELLOW}{p}{_R}")

    def take_shift(self, person=None, hours=8.0, start=None, role="on", note=""):
        """Put `person` (default: you) on call from now (or `start`) for
        `hours`, on top of the sheet. ``role="backup"`` for backup."""
        person = person or _user()
        t0 = _ts(start) if start is not None else time.time()
        sh.add_override(person, t0, t0 + 3600 * hours, typ=role, note=note, by=_user())
        self.oncall()

    def leave_shift(self, person=None, hours=8.0, start=None, note=""):
        """Take `person` off call for `hours` (deviation type off)."""
        person = person or _user()
        t0 = _ts(start) if start is not None else time.time()
        sh.add_override(person, t0, t0 + 3600 * hours, typ="off", note=note, by=_user())
        self.oncall()

    def clear_shift_overrides(self, person=None):
        sh.clear_overrides(person)

    # -- people / phones ---------------------------------------------------
    def people(self):
        """Who is known (sheet) and who has a phone key (eco)."""
        from eco.utilities.tables import format_table

        plan = self._plan(fetch=False)
        keys = PEOPLE.read()
        names = sorted(set(plan.people) | set(keys), key=str.lower)
        rows = []
        for n in names:
            pref = plan.people.get(n, {})
            k = keys.get(n)
            rows.append([n, "yes" if k else f"{_RED}no{_R}", pref.get("off_shift", "—"),
                         "yes" if pref.get("escalation") else "", "" if n in plan.people else "not in sheet"])
        print(format_table(rows, headers=["name", "phone key", "off-shift", "escalation", ""]))

    def set_phone_key(self, person, user_key, validate=True):
        """Register a person's Pushover *user key* (from their Pushover app /
        pushover.net). The name must match the shift sheet."""
        if validate:
            from eco.alarms.notify import PushoverBackend

            try:
                ok = PushoverBackend().validate_user(user_key)
            except Exception as e:
                ok = None
                print(f"(not validated: {e})")
            if ok is False:
                raise ValueError("Pushover does not know this user key")

        def upd(d):
            d[person] = {"pushover": user_key, "set_by": _user(), "at": time.time()}

        PEOPLE.update(upd)

    def remove_phone_key(self, person):
        PEOPLE.update(lambda d: d.pop(person, None))

    def send_test(self, person=None, critical=False):
        """Send a test message right now (directly from this session, also
        when disarmed). `critical=True` sends an emergency message that
        repeats until acknowledged on the phone -- the real thing."""
        from eco.alarms import notify as nt

        keys = {k.lower(): (k, v.get("pushover")) for k, v in PEOPLE.read().items()}
        if person is None:
            targets = list(keys.values())
        else:
            targets = [keys.get(person.lower(), (person, None))]
        backend = nt.PushoverBackend()
        for name, key in targets:
            if not key:
                print(f"{name}: no phone key")
                continue
            msg = nt.Message(
                title="Bernina alarm TEST" + (" (critical)" if critical else ""),
                text=f"test from {_user()} at {datetime.now(sh.TZ):%H:%M}"
                + ("\nrepeats until you acknowledge it" if critical else ""),
                priority=nt.EMERGENCY if critical else nt.NORMAL,
                sound="persistent" if critical else None,
                retry=60,
                expire=600,
                tag="test",
            )
            r = backend.send(name, key, msg)
            print(f"{name}: sent" + (f" (receipt {r})" if r else ""))

    def events(self, n=30):
        """The last `n` alarm events (raised/cleared/acked/sent)."""
        from eco.utilities.tables import format_table

        rows = []
        for e in read_jsonl(EVENTS_LOG, last=n):
            t = datetime.fromtimestamp(e.get("t", 0), sh.TZ).strftime("%m-%d %H:%M:%S")
            what = e.get("event")
            detail = ""
            if what == "send":
                detail = f"-> {e.get('person')} prio {e.get('priority')} [{e.get('backend')}]"
                if e.get("error"):
                    detail += f" {_RED}{e['error']}{_R}"
            elif what == "acked":
                detail = f"by {e.get('by')}"
            elif "value" in e:
                detail = f"value {e.get('value')}"
            rows.append([t, what, e.get("label") or e.get("alarm"), detail])
        print(format_table(rows, headers=["time", "event", "alarm", ""]))

    # -- display ----------------------------------------------------------
    def _header(self, snap):
        now = time.time()
        lines = []
        armed = self.is_armed()
        arm = f"{_RED}● ARMED{_R}" if armed else f"{_DIM}○ disarmed (nothing sent){_R}"
        if snap:
            m = snap["monitor"]
            mon = f"{_GREEN}monitor ok{_R} {_DIM}({m.get('host', '?').split('.')[0]}, {now - m['heartbeat']:.0f} s ago){_R}"
        else:
            mon = f"{_YELLOW}no monitor running -- states are evaluated here, nobody is paged{_R}"
        lines.append(f"{arm}   {mon}")
        oc = (snap or {}).get("oncall")
        if oc is None:
            try:
                plan = self._plan(fetch=False)
                o = plan.on_call()
                oc = {"primary": o.primary, "backup": o.backup,
                      "next_change": o.next_change.timestamp() if o.next_change else None}
            except Exception:
                oc = None
        if oc is not None:
            prim = ", ".join(oc["primary"]) or f"{_RED}nobody{_R}"
            nxt = ""
            if oc.get("next_change"):
                nxt = f"  {_DIM}until {datetime.fromtimestamp(oc['next_change'], sh.TZ):%a %H:%M}{_R}"
            lines.append(f"on call: {_B}{prim}{_R}   backup: {', '.join(oc['backup']) or '—'}{nxt}")
        if snap:
            for p in snap["monitor"].get("problems", [])[:5]:
                lines.append(f"{_YELLOW}⚠ {p}{_R}")
        return "\n".join(lines)

    def _all_rows(self, snap, now, only_attention=False):
        rows, keys, states = [], [], []
        for p in self._panel_names:
            panel = getattr(self, p)
            if isinstance(panel, AlarmPanel):
                r, k, s = panel.display_rows(snap, now)
                k = [f"{p}: {x}" for x in k]
            else:
                r, k, s = [], [], []
            rows += r
            keys += k
            states += s
        r, k, s = self.custom.display_rows(snap, now)
        rows += r
        keys += k
        states += s
        if only_attention:
            sel = [i for i, st in enumerate(states) if st not in (tr.STATE_OK, tr.DISABLED)]
            rows, keys = [rows[i] for i in sel], [keys[i] for i in sel]
        return rows, keys, states

    def get_display_str(self, tablefmt="simple", only_attention=False, **kwargs):
        now = time.time()
        snap = monitor_snapshot()
        rows, keys, states = self._all_rows(snap, now, only_attention=only_attention)
        table = _render_alarm_table(rows, group_keys=keys, tablefmt=tablefmt) if rows else "(nothing needs attention)"
        return self._header(snap) + "\n" + summary_line(states) + "\n" + table

    def _display_text(self, tree=None):
        return f"{self.alias.get_full_name()}\n" + self.get_display_str()

    def active(self):
        """Only alarms that are not plainly ok."""
        print(self.get_display_str(only_attention=True))

    def _widget_svg_panel(self, live=False, cols=6, path=None, **kwargs):
        from eco.alarms.svg import build_alarm_svg
        from eco.utilities.tempfiles import user_temp_svg_path

        now = time.time()
        snap = monitor_snapshot() if live else None
        if live and not snap:
            evaluate_alarms(self.get_all_alarms(), now=now)
        tiles = []
        for p in self._panel_names:
            panel = getattr(self, p)
            for gname in getattr(panel, "_group_names", []):
                g = getattr(panel, gname)
                for cname in g._channel_names:
                    tiles.append(alarm_tile(getattr(g, cname), f"{p}.{gname}.{cname}", f"{p}: {g.label}", snap, now, live))
        for a in self.custom.get_alarms():
            tiles.append(alarm_tile(a, f"custom.{a.name}", "custom", snap, now, live))
        header = None
        if live:
            header = colorama_strip(self._header(snap)).replace("\n", "   ")
        svg = build_alarm_svg(tiles, title=self.name or "alarms", cols=cols, header=header)
        if path is None:
            path = user_temp_svg_path("eco_alarms", self.name or id(self))
        with open(path, "w") as f:
            f.write(svg)
        return path


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def colorama_strip(s):
    return _ANSI.sub("", s)


def _user():
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _ts(t):
    if isinstance(t, (int, float)):
        return float(t)
    if isinstance(t, str):
        return sh._parse_datetime(t).timestamp()
    if isinstance(t, datetime):
        return sh._as_dt(t).timestamp()
    raise TypeError(t)


def _full_name(obj):
    alias = getattr(obj, "alias", None)
    if alias is None:
        return None
    try:
        return alias.get_full_name()
    except Exception:
        return None
