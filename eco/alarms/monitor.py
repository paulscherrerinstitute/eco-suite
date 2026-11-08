"""The alarm monitor: the only place that pages phones.

Runs as a thread inside the status server (or standalone,
``python -m eco.alarms monitor``). Every `period` seconds it

1. evaluates all alarms of an `AlarmSystem` (reading their detectors),
2. works out who is on call (`eco.alarms.shifts`),
3. pages people for alarms that were raised (level warning/critical),
   escalates unacknowledged critical alarms, polls phone acknowledgements,
   tells people when an alarm cleared or was acknowledged,
4. writes ``state.json`` (what every display shows) and the event log,
5. pings an external heartbeat URL, if configured, so a dead monitor is
   noticed from outside.

Nothing is sent while the system is disarmed (``alarms.arm()``); the messages
that *would* have gone out are logged.

Policy, per alarm level:

* ``critical`` -- on call: emergency (repeats until acknowledged, gets
  through silent mode on phones where the person enabled that in Pushover).
  Nobody on call: backup, else the escalation contacts. Not acknowledged
  after ``escalate_after`` s: backup too; after twice that: escalation
  contacts. Off shift: per person's ``Off-shift alerts`` (quiet/normal).
* ``warning`` -- on call (else backup): one normal message. Off shift: only
  people with ``Off-shift alerts = normal``, quietly.
* ``display`` -- nobody.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import threading
import time
from datetime import datetime

from eco.alarms import notify as nt
from eco.alarms import tracker as tr
from eco.alarms.shifts import TZ, load_plan
from eco.alarms.store import EVENTS_LOG, PEOPLE, STATE, SYSTEM, append_jsonl

logger = logging.getLogger(__name__)

SYSTEM_DEFAULTS = {
    "armed": False,
    "escalate_after": 600.0,
    "max_raises_per_hour": 6,
    "retry": 60,
    "expire": 3600,
    "sound_critical": "persistent",
    "sound_warning": None,
    "notify_cleared": True,
    "heartbeat_url": None,
    "heartbeat_period": 60.0,
    "receipt_poll_period": 20.0,
    "plan_refresh": 120.0,
}


def system_settings():
    s = dict(SYSTEM_DEFAULTS)
    try:
        s.update({k: v for k, v in SYSTEM.read().items() if k in SYSTEM_DEFAULTS})
    except Exception:
        pass
    return s


def _tag(aid, count):
    return "a" + hashlib.sha1(f"{aid}#{count}".encode()).hexdigest()[:12]


def _hm(t):
    return datetime.fromtimestamp(t, TZ).strftime("%H:%M")


class MonitorAlreadyRunning(RuntimeError):
    pass


class AlarmMonitor:
    def __init__(self, system, backend=None, period=2.0, plan_loader=None, clock=time.time):
        self.system = system
        self.period = period
        self._backend = backend
        self._backend_error = None
        self._plan_loader = plan_loader or (lambda now: load_plan(max_age=system_settings()["plan_refresh"], now=now))
        self._clock = clock
        self._plan = None
        self._plan_t = 0.0
        self._pages = {}  # aid -> paging bookkeeping
        self._raise_times = {}  # aid -> [t, ...] (flapping guard)
        self._last_poll = 0.0
        self._last_heartbeat = 0.0
        self._heartbeat_error = None
        self._errors = {}
        self._started = clock()
        self._thread = None
        self._stop = threading.Event()
        self.host = socket.gethostname()
        self.pid = os.getpid()
        self.steps = 0
        self.last_step_s = None

    # ------------------------------------------------------------------
    # lifecycle
    def check_single_instance(self, force=False):
        doc = STATE.read(max_age=0)
        m = doc.get("monitor", {})
        hb = m.get("heartbeat")
        if (
            not force
            and hb
            and self._clock() - hb < 30
            and (m.get("host"), m.get("pid")) != (self.host, self.pid)
        ):
            raise MonitorAlreadyRunning(
                f"an alarm monitor is already running on {m.get('host')} pid {m.get('pid')} "
                f"(heartbeat {self._clock() - hb:.0f} s ago); not starting a second one"
            )

    def restore(self):
        """Take over trackers and paging state from the last state.json, so
        a restart neither forgets counters nor re-pages a raised alarm."""
        doc = STATE.read(max_age=0)
        saved = doc.get("alarms", {})
        for a in self.system.get_all_alarms():
            d = saved.get(a.alarm_id)
            if d:
                a.tracker = tr.AlarmTracker.from_dict(d)
        self._pages = {k: v for k, v in doc.get("pages", {}).items()}

    def start(self, force=False):
        self.check_single_instance(force=force)
        self.restore()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="eco-alarm-monitor", daemon=True)
        self._thread.start()
        logger.info("alarm monitor started (%d alarms)", len(self.system.get_all_alarms()))
        return self

    def stop(self, timeout=10):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self):
        try:
            import epics

            epics.ca.use_initial_context()
        except Exception:
            pass
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self.step()
            except Exception as e:  # never let the loop die
                logger.exception("alarm monitor step failed")
                self._errors["step"] = f"{type(e).__name__}: {e}"
            self._stop.wait(max(0.1, self.period - (time.monotonic() - t0)))

    # ------------------------------------------------------------------
    @property
    def backend(self):
        """The real backend while armed (created on first use; a test may
        pass its own), the shared dry-run recorder while disarmed."""
        if not system_settings()["armed"]:
            return _DRY
        if self._backend is None:
            try:
                self._backend = nt.make_backend()
                self._backend_error = None
            except Exception as e:
                self._backend_error = str(e)
                return _DRY
        return self._backend

    def plan(self, now):
        if self._plan is None or now - self._plan_t > 30:
            try:
                self._plan = self._plan_loader(now)
                self._errors.pop("plan", None)
            except Exception as e:
                self._errors["plan"] = f"{type(e).__name__}: {e}"
            self._plan_t = now
        return self._plan

    # ------------------------------------------------------------------
    def step(self, now=None):
        from eco.elements.alarm import evaluate_alarms

        t_start = time.monotonic()
        now = self._clock() if now is None else now
        self.system.reload_custom(max_age=30)
        alarms = self.system.get_all_alarms()
        by_id = {a.alarm_id: a for a in alarms}
        events = evaluate_alarms(alarms, now=now)
        plan = self.plan(now)
        oncall = plan.on_call(now) if plan is not None else None

        for aid, evs in events.items():
            a = by_id[aid]
            for ev in evs:
                self._log(ev, a, now)
                if ev == "raised":
                    self._on_raised(a, now, oncall, plan)
                elif ev == "cleared":
                    self._on_cleared(a, now)
                elif ev == "acked":
                    self._on_acked(a, now, a.tracker.acked_by)

        self._escalate(now, by_id, oncall, plan)
        if now - self._last_poll > system_settings()["receipt_poll_period"]:
            self._last_poll = now
            self._poll_receipts(now, by_id)
        # pages whose alarm is gone (removed custom alarm)
        for aid in [k for k in self._pages if k not in by_id]:
            self._pages.pop(aid, None)

        self.steps += 1
        self.last_step_s = time.monotonic() - t_start
        self._write_state(now, alarms, oncall, plan)
        self._heartbeat(now)

    # ------------------------------------------------------------------
    # paging
    def _keys(self):
        try:
            people = PEOPLE.read()
        except Exception:
            people = {}
        return {k.lower(): (k, v.get("pushover") if isinstance(v, dict) else v) for k, v in people.items()}

    def _recipients(self, level, stage, oncall, plan):
        """[(person, priority)] for a new raise (stage 0) or escalation."""
        if oncall is None or plan is None:
            return []
        out = []
        if level == "critical":
            if stage == 0:
                first = oncall.primary or oncall.backup or plan.escalation_contacts()
                out += [(p, nt.EMERGENCY) for p in first]
                for p in plan.people:
                    if p in first:
                        continue
                    pref = plan.preference(p, "off_shift", "none")
                    if pref == "normal":
                        out.append((p, nt.NORMAL))
                    elif pref == "quiet":
                        out.append((p, nt.QUIET))
            elif stage == 1:
                out += [(p, nt.EMERGENCY) for p in oncall.backup]
            elif stage == 2:
                out += [(p, nt.EMERGENCY) for p in plan.escalation_contacts()]
        elif level == "warning" and stage == 0:
            first = oncall.primary or oncall.backup
            out += [(p, nt.NORMAL) for p in first]
            for p in plan.people:
                if p not in first and plan.preference(p, "off_shift") == "normal":
                    out.append((p, nt.QUIET))
        return out

    def _message(self, a, level, priority, stage, now):
        info = a.tracker
        s = a.get_settings()
        from eco.elements.alarm import _fmt_num, _fmt_range

        unit = f" {a.unit}" if a.unit else ""
        sysset = system_settings()
        head = "ALARM" if level == "critical" else "Warning"
        text = (
            f"{a.label or a.name} = {_fmt_num(info.last_value)}{unit}, range {_fmt_range(s['min'], s['max'])}\n"
            f"raised {_hm(info.active_since or now)}"
            + (f" (bad for {int(s['delay'])} s)" if s["delay"] else "")
        )
        if stage:
            text += f"\nescalated: not acknowledged for {int(now - (info.active_since or now)) // 60} min"
        if s["note"]:
            text += f"\n{s['note']}"
        text += f"\n{a.alarm_id}"
        return nt.Message(
            title=f"Bernina {head}: {a.label or a.name}",
            text=text,
            priority=priority,
            sound=sysset["sound_critical"] if priority == nt.EMERGENCY else sysset["sound_warning"],
            tag=_tag(a.alarm_id, info.count),
            retry=sysset["retry"],
            expire=sysset["expire"],
        )

    def _send(self, person, priority_msg, aid, page):
        keys = self._keys()
        canon, key = keys.get(person.lower(), (person, None))
        backend = self.backend
        rec = {"t": self._clock(), "event": "send", "alarm": aid, "person": canon, "priority": priority_msg.priority,
               "backend": backend.name, "title": priority_msg.title}
        if not key:
            rec["error"] = "no phone key registered"
            self._errors[f"key:{canon}"] = f"{canon} is due to get alarms but has no phone key (alarms.set_phone_key)"
            append_jsonl(EVENTS_LOG, rec)
            return
        try:
            receipt = backend.send(canon, key, priority_msg)
            if receipt:
                page.setdefault("receipts", {})[canon] = receipt
            page.setdefault("notified", {})[canon] = priority_msg.priority
            self._errors.pop("send", None)
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            self._errors["send"] = rec["error"]
        append_jsonl(EVENTS_LOG, rec)

    def _flapping(self, aid, now):
        lim = system_settings()["max_raises_per_hour"]
        ts = [t for t in self._raise_times.get(aid, []) if now - t < 3600] + [now]
        self._raise_times[aid] = ts
        return lim and len(ts) > lim

    def _on_raised(self, a, now, oncall, plan):
        level = a.get_setting("level")
        if level not in ("warning", "critical"):
            return
        aid = a.alarm_id
        page = {"tag": _tag(aid, a.tracker.count), "raised_at": now, "stage": 0, "level": level,
                "receipts": {}, "notified": {}}
        self._pages[aid] = page
        if self._flapping(aid, now):
            page["suppressed"] = "flapping"
            if not self._raise_times.get(aid + "#told"):
                self._raise_times[aid + "#told"] = [now]
                for p, prio in self._recipients(level, 0, oncall, plan):
                    if prio >= nt.NORMAL:
                        msg = self._message(a, level, nt.NORMAL, 0, now)
                        msg.title = f"Bernina: {a.label or a.name} keeps flapping"
                        msg.text = ("raised more than %d times in the last hour; no more messages for it "
                                    "this hour. Consider a longer delay or shelving it." % system_settings()["max_raises_per_hour"])
                        self._send(p, msg, aid, page)
            return
        self._raise_times.pop(aid + "#told", None)
        for p, prio in self._recipients(level, 0, oncall, plan):
            self._send(p, self._message(a, level, prio, 0, now), aid, page)

    def _escalate(self, now, by_id, oncall, plan):
        after = system_settings()["escalate_after"]
        for aid, page in self._pages.items():
            a = by_id.get(aid)
            if a is None or page.get("level") != "critical" or page.get("suppressed") or page.get("acked"):
                continue
            if not a.tracker.raised or a.tracker.acked:
                continue
            stage = page.get("stage", 0)
            if stage >= 2 or now - page["raised_at"] < after * (stage + 1):
                continue
            stage += 1
            page["stage"] = stage
            self._log("escalated", a, now, stage=stage)
            for p, prio in self._recipients("critical", stage, oncall, plan):
                if page.get("notified", {}).get(p) == nt.EMERGENCY:
                    continue
                self._send(p, self._message(a, "critical", prio, stage, now), aid, page)

    def _poll_receipts(self, now, by_id):
        backend = self.backend
        keys = {v[1]: v[0] for v in self._keys().values() if v[1]}
        for aid, page in list(self._pages.items()):
            if page.get("acked") or not page.get("receipts"):
                continue
            a = by_id.get(aid)
            for person, receipt in page["receipts"].items():
                try:
                    r = backend.poll(receipt)
                except Exception as e:
                    self._errors["poll"] = f"{type(e).__name__}: {e}"
                    continue
                if r.get("acknowledged"):
                    who = keys.get(r.get("acknowledged_by"), person)
                    if a is not None:
                        a.tracker.ack(now, by=f"{who} (phone)")
                        self._log("acked", a, now, by=f"{who} (phone)")
                    self._on_acked(a, now, f"{who} (phone)", aid=aid)
                    break

    def _on_acked(self, a, now, by, aid=None):
        aid = aid or a.alarm_id
        page = self._pages.get(aid)
        if not page or page.get("acked"):
            return
        page["acked"] = by
        self._cancel(page)
        for p, prio in page.get("notified", {}).items():
            if prio >= nt.NORMAL and not (by or "").startswith(p):
                msg = nt.Message(title=f"Bernina: acknowledged: {a.label or a.name if a else aid}",
                                 text=f"acknowledged by {by} at {_hm(now)}", priority=nt.QUIET)
                self._send(p, msg, aid, {})

    def _on_cleared(self, a, now):
        page = self._pages.pop(a.alarm_id, None)
        if not page:
            return
        self._cancel(page)
        if not system_settings()["notify_cleared"] or page.get("suppressed"):
            return
        from eco.elements.alarm import _fmt_num, fmt_duration

        dur = fmt_duration(now - page["raised_at"])
        for p, prio in page.get("notified", {}).items():
            if prio >= nt.NORMAL:
                msg = nt.Message(
                    title=f"Bernina: cleared: {a.label or a.name}",
                    text=f"back in range ({_fmt_num(a.tracker.last_value)}) at {_hm(now)} after {dur}",
                    priority=nt.QUIET,
                )
                self._send(p, msg, a.alarm_id, {})

    def _cancel(self, page):
        if page.get("receipts"):
            try:
                self.backend.cancel(page["tag"])
            except Exception as e:
                self._errors["cancel"] = f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    def _log(self, event, a, now, **extra):
        rec = {"t": now, "event": event, "alarm": a.alarm_id, "label": a.label or a.name,
               "value": a.tracker.last_value, "level": a.get_setting("level"), **extra}
        if event == "acked":
            rec.setdefault("by", a.tracker.acked_by)
        append_jsonl(EVENTS_LOG, rec)

    def _write_state(self, now, alarms, oncall, plan):
        sysset = system_settings()
        alarm_docs = {}
        for a in alarms:
            d = a.tracker.to_dict()
            d["label"] = a.label or a.name
            d["level"] = a.get_setting("level")
            alarm_docs[a.alarm_id] = d
        problems = list(self._errors.values())
        if self._backend_error and sysset["armed"]:
            problems.insert(0, f"ARMED but cannot send: {self._backend_error}")
        if plan is not None:
            problems += plan.problems
        doc = {
            "monitor": {
                "heartbeat": now,
                "host": self.host,
                "pid": self.pid,
                "started": self._started,
                "armed": bool(sysset["armed"]),
                "backend": self.backend.name,
                "period": self.period,
                "steps": self.steps,
                "last_step_s": self.last_step_s,
                "problems": problems,
                "heartbeat_error": self._heartbeat_error,
            },
            "oncall": None if oncall is None else {
                "primary": oncall.primary,
                "backup": oncall.backup,
                "off": oncall.off,
                "next_change": oncall.next_change.timestamp() if oncall.next_change else None,
                "plan_fetched_at": plan.fetched_at if plan else None,
                "plan_stale": plan.stale if plan else None,
            },
            "alarms": alarm_docs,
            "pages": self._pages,
        }
        try:
            STATE.write(doc)
        except Exception as e:
            logger.warning("cannot write alarm state: %s", e)

    def _heartbeat(self, now):
        sysset = system_settings()
        url = sysset["heartbeat_url"]
        if not url or now - self._last_heartbeat < sysset["heartbeat_period"]:
            return
        self._last_heartbeat = now
        try:
            import requests

            requests.get(url, timeout=5)
            self._heartbeat_error = None
        except Exception as e:
            self._heartbeat_error = f"{type(e).__name__}: {e}"


_DRY = nt.DryRunBackend()


def start_in_status_server(store, wait=True):
    """Start a monitor on the ``alarms`` component of a status server's
    namespace once it is ready. Used by the namespace status server when
    ``ECO_ALARM_MONITOR=1``. Returns the thread that does the waiting."""

    def _go():
        try:
            store.wait_ready()
            ns = store.namespace
            system = ns.get_obj("alarms")
            # resolve a lazy proxy
            system.get_all_alarms()
            mon = AlarmMonitor(system)
            mon.start()
            store.alarm_monitor = mon
        except Exception:
            logger.exception("alarm monitor could not be started")

    t = threading.Thread(target=_go, name="eco-alarm-monitor-start", daemon=True)
    t.start()
    return t
