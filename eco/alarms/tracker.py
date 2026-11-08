"""Time-aware alarm state machine.

Pure logic, no I/O and no clock of its own (every call takes ``now``), so it
is tested by feeding conditions and timestamps. One `AlarmTracker` per alarm.

States::

    disabled   alarm switched off                       (no notification)
    shelved    temporarily silenced until a time        (no notification)
    inhibited  an "inhibit_by" alarm is not OK (e.g. no beam)
    unknown    value unreadable, and unknown is not treated as alarm
    ok         condition fine
    pending    condition bad, but not yet for `delay` seconds
    active     raised, not acknowledged                 -> notifications
    acked      raised and acknowledged, still bad
    clearing   raised, condition fine again, waiting `clear_delay`

`update()` returns the events of that step: ``"raised"`` (pending -> active),
``"cleared"`` (active/acked/clearing -> back to normal). ``ack()`` returns
``"acked"``.
"""

from __future__ import annotations

OK, BAD, UNKNOWN = "ok", "bad", "unknown"  # instantaneous condition

DISABLED = "disabled"
SHELVED = "shelved"
INHIBITED = "inhibited"
STATE_UNKNOWN = "unknown"
STATE_OK = "ok"
PENDING = "pending"
ACTIVE = "active"
ACKED = "acked"
CLEARING = "clearing"

RAISED_STATES = (ACTIVE, ACKED, CLEARING)
#: display order of "how much attention does this need"
ATTENTION = {
    ACTIVE: 0,
    CLEARING: 1,
    ACKED: 2,
    PENDING: 3,
    STATE_UNKNOWN: 4,
    INHIBITED: 5,
    SHELVED: 6,
    DISABLED: 7,
    STATE_OK: 8,
}

_FIELDS = (
    "state",
    "state_since",
    "violating_since",
    "ok_since",
    "active_since",
    "acked",
    "acked_by",
    "acked_at",
    "count",
    "total_active_s",
    "last_raised",
    "last_cleared",
    "last_value",
    "last_condition",
    "last_update",
)


class AlarmTracker:
    def __init__(self):
        self.state = STATE_OK
        self.state_since = None
        self.violating_since = None
        self.ok_since = None
        self.active_since = None
        self.acked = False
        self.acked_by = None
        self.acked_at = None
        self.count = 0
        self.total_active_s = 0.0
        self.last_raised = None
        self.last_cleared = None
        self.last_value = None
        self.last_condition = None
        self.last_update = None

    # ------------------------------------------------------------------
    @property
    def raised(self):
        return self.state in RAISED_STATES

    def _set_state(self, state, now):
        if state != self.state:
            self.state = state
            self.state_since = now
        elif self.state_since is None:
            self.state_since = now

    def _clear(self, now, events):
        if self.active_since is not None:
            self.total_active_s += max(0.0, now - self.active_since)
        self.active_since = None
        self.ok_since = None
        self.acked = False
        self.acked_by = None
        self.acked_at = None
        self.last_cleared = now
        events.append("cleared")

    def update(
        self,
        condition,
        now,
        value=None,
        delay=0.0,
        clear_delay=0.0,
        enabled=True,
        shelved_until=None,
        inhibited=False,
        unknown_is_alarm=False,
    ):
        events = []
        self.last_value = value
        self.last_condition = condition
        self.last_update = now
        delay = float(delay or 0.0)
        clear_delay = float(clear_delay or 0.0)

        # states that switch the alarm off entirely; an alarm that was raised
        # is cleared by them, so nobody keeps being paged for it
        off = None
        if not enabled:
            off = DISABLED
        elif shelved_until is not None and now < shelved_until:
            off = SHELVED
        elif inhibited:
            off = INHIBITED
        if off is not None:
            if self.raised:
                self._clear(now, events)
            self.violating_since = None
            self._set_state(off, now)
            return events

        bad = condition == BAD or (condition == UNKNOWN and unknown_is_alarm)

        if self.raised:
            if bad:
                self.ok_since = None
                self._set_state(ACKED if self.acked else ACTIVE, now)
            else:
                if self.ok_since is None:
                    self.ok_since = now
                if now - self.ok_since >= clear_delay:
                    self._clear(now, events)
                    self.violating_since = None
                    self._set_state(
                        STATE_UNKNOWN if condition == UNKNOWN else STATE_OK, now
                    )
                else:
                    self._set_state(CLEARING, now)
            return events

        if bad:
            if self.violating_since is None:
                self.violating_since = now
            if now - self.violating_since >= delay:
                self.active_since = now
                self.count += 1
                self.last_raised = now
                self.acked = False
                self.ok_since = None
                self._set_state(ACTIVE, now)
                events.append("raised")
            else:
                self._set_state(PENDING, now)
        else:
            self.violating_since = None
            self._set_state(STATE_UNKNOWN if condition == UNKNOWN else STATE_OK, now)
        return events

    def ack(self, now, by=None):
        if not self.raised or self.acked:
            return []
        self.acked = True
        self.acked_by = by
        self.acked_at = now
        if self.state == ACTIVE:
            self._set_state(ACKED, now)
        return ["acked"]

    def reset_counters(self):
        self.count = 0
        self.total_active_s = 0.0

    # ------------------------------------------------------------------
    def time_in_state(self, now):
        return None if self.state_since is None else max(0.0, now - self.state_since)

    def time_in_alarm(self, now):
        """Total seconds spent raised since the counters were reset."""
        t = self.total_active_s
        if self.active_since is not None:
            t += max(0.0, now - self.active_since)
        return t

    def to_dict(self):
        return {k: getattr(self, k) for k in _FIELDS}

    @classmethod
    def from_dict(cls, d):
        t = cls()
        for k in _FIELDS:
            if k in d:
                setattr(t, k, d[k])
        return t
