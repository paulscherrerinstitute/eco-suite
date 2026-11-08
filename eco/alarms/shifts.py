"""Shift plan: who gets phone alarms when.

Source is a Google Sheet (same service account as the run tables) with
three tabs:

``Shifts`` -- the normal plan, one row per shift::

    Date        From   To     On call          Backup   Note
    2026-11-12  07:00  15:00  Anna, Ben        Henrik
    2026-11-12  15:00  23:00  Chris            Henrik
    2026-11-12  23:00  07:00  Dana             Ben      night (To <= From: next day)

``Deviations`` -- individual times that differ from the plan::

    Person   From              To                Type     Note
    Ben      2026-11-12 10:00  2026-11-12 12:00  off      dentist
    Henrik   2026-11-13 00:00  2026-11-13 06:00  on       watching the night
    Anna     2026-11-14                          backup   whole day

  ``off`` removes the person (even if listed in Shifts), ``on`` adds them as
  on call, ``backup`` as backup. A date without a time means the whole day; an
  empty To means until the end of that day.

``People`` -- preferences (never phone keys; those stay in eco's own file)::

    Name     Off-shift alerts   Escalation   Note
    Henrik   quiet              yes

  Off-shift alerts: ``none`` (default) | ``quiet`` | ``normal`` -- what a
  person gets for critical alarms while *not* on shift. Escalation ``yes``:
  paged when nobody on shift acknowledges in time, or nobody is on shift.

Times are Europe/Zurich wall-clock. Dates may be ``2026-11-12`` or
``12.11.2026``. Names are matched case-insensitively.

Shift changes made from eco (`take_shift`, `leave_shift`) are deviations kept
in eco's ``system.json`` and combined with the sheet.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from eco.alarms.store import SHIFT_CACHE, SYSTEM

TZ = ZoneInfo("Europe/Zurich")

DEFAULT_CREDENTIALS = "/sf/bernina/config/src/python/gspread/pandas_push"
#: Drive folder of the run tables; the shift plan sits next to them
DEFAULT_FOLDER_ID = "1F7DgF0HW1O71nETpfrTvQ35lRZCs5GvH"

SHIFTS_HEADER = ["Date", "From", "To", "On call", "Backup", "Note"]
DEVIATIONS_HEADER = ["Person", "From", "To", "Type", "Note"]
PEOPLE_HEADER = ["Name", "Off-shift alerts", "Escalation", "Note"]
DEVIATION_TYPES = ("off", "on", "backup")
OFF_SHIFT_CHOICES = ("none", "quiet", "normal")

#: refetch the sheet after this many seconds
REFRESH_S = 120.0


class ShiftPlanError(ValueError):
    pass


# ---------------------------------------------------------------------------
# parsing


def _parse_date(s):
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ShiftPlanError(f"cannot read date {s!r} (use 2026-11-12 or 12.11.2026)")


def _parse_time(s):
    """-> (hours, minutes); '24:00' allowed; '8' means 08:00."""
    s = str(s).strip().lower().replace("h", ":")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?", s.rstrip(":"))
    if not m:
        raise ShiftPlanError(f"cannot read time {s!r} (use 07:00)")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if h > 24 or mi > 59 or (h == 24 and mi):
        raise ShiftPlanError(f"invalid time {s!r}")
    return h, mi


def _at(d, hm):
    h, mi = hm
    dt = datetime(d.year, d.month, d.day, 0, 0, tzinfo=TZ)
    return dt + timedelta(hours=h, minutes=mi)


def _parse_datetime(s, end=False):
    """'2026-11-12 08:00' / '12.11.2026 8:00' / date only (start or end of
    that day, depending on `end`)."""
    s = str(s).strip()
    parts = s.split()
    d = _parse_date(parts[0])
    if len(parts) == 1:
        return _at(d, (24, 0) if end else (0, 0))
    return _at(d, _parse_time(parts[1]))


def split_names(s):
    if not s:
        return []
    parts = re.split(r"\s*(?:,|;|/|&|\+|\band\b|\bund\b)\s*", str(s).strip())
    return [p.strip() for p in parts if p.strip()]


@dataclass
class Interval:
    start: datetime
    end: datetime
    people: list
    role: str  # "on" | "backup" | "off"
    note: str = ""
    source: str = "sheet"
    row: int | None = None

    def covers(self, t):
        return self.start <= t < self.end


@dataclass
class OnCall:
    time: datetime
    primary: list
    backup: list
    off: list
    next_change: datetime | None
    shifts: list = field(default_factory=list)

    def __str__(self):
        p = ", ".join(self.primary) or "NOBODY"
        b = ", ".join(self.backup) or "—"
        nxt = f", next change {self.next_change:%a %H:%M}" if self.next_change else ""
        return f"on call: {p} · backup: {b}{nxt}"


def _rows_as_dicts(table, header):
    """`table`: list of rows (lists of cell strings) incl. header row.
    Returns list of (row_number, dict) for non-empty rows, keyed by our
    header names (matched case-insensitively, extra columns ignored)."""
    if not table:
        return []
    head = [str(h).strip().lower() for h in table[0]]
    idx = {}
    for name in header:
        try:
            idx[name] = head.index(name.lower())
        except ValueError:
            idx[name] = None
    out = []
    for n, row in enumerate(table[1:], start=2):
        d = {}
        for name, i in idx.items():
            d[name] = str(row[i]).strip() if i is not None and i < len(row) else ""
        if any(d.values()):
            out.append((n, d))
    return out


def parse_shifts(table):
    intervals, problems = [], []
    for n, r in _rows_as_dicts(table, SHIFTS_HEADER):
        if not r["Date"]:
            continue
        try:
            d = _parse_date(r["Date"])
            start = _at(d, _parse_time(r["From"] or "0:00"))
            end = _at(d, _parse_time(r["To"] or "24:00"))
            if end <= start:
                end += timedelta(days=1)
        except ShiftPlanError as e:
            problems.append(f"Shifts row {n}: {e}")
            continue
        if r["On call"]:
            intervals.append(Interval(start, end, split_names(r["On call"]), "on", r["Note"], row=n))
        if r["Backup"]:
            intervals.append(Interval(start, end, split_names(r["Backup"]), "backup", r["Note"], row=n))
    return intervals, problems


def parse_deviations(table, source="sheet"):
    intervals, problems = [], []
    for n, r in _rows_as_dicts(table, DEVIATIONS_HEADER):
        if not r["Person"]:
            continue
        typ = r["Type"].lower() or "off"
        if typ not in DEVIATION_TYPES:
            problems.append(f"Deviations row {n}: type {r['Type']!r} is not one of {DEVIATION_TYPES}")
            continue
        try:
            start = _parse_datetime(r["From"])
            if r["To"]:
                end = _parse_datetime(r["To"], end=True)
            else:
                end = _at(start.date(), (24, 0))
            if end <= start:
                raise ShiftPlanError("To is before From")
        except ShiftPlanError as e:
            problems.append(f"Deviations row {n}: {e}")
            continue
        intervals.append(Interval(start, end, split_names(r["Person"]), typ, r["Note"], source, n))
    return intervals, problems


def parse_people(table):
    people, problems = {}, []
    for n, r in _rows_as_dicts(table, PEOPLE_HEADER):
        name = r["Name"]
        if not name:
            continue
        off = (r["Off-shift alerts"] or "none").lower()
        if off not in OFF_SHIFT_CHOICES:
            problems.append(f"People row {n}: off-shift alerts {off!r} not one of {OFF_SHIFT_CHOICES}")
            off = "none"
        esc = (r["Escalation"] or "no").strip().lower() in ("yes", "y", "1", "true", "ja", "x")
        people[name] = {"off_shift": off, "escalation": esc, "note": r["Note"]}
    return people, problems


# ---------------------------------------------------------------------------


class ShiftPlan:
    def __init__(self, intervals, people, fetched_at=None, source="", problems=(), stale=False):
        self.intervals = list(intervals)
        self.people = dict(people)
        self.fetched_at = fetched_at
        self.source = source
        self.problems = list(problems)
        self.stale = stale
        self._canon = {n.lower(): n for n in self.people}

    @classmethod
    def from_tables(cls, tables, overrides=(), **kw):
        """`tables`: {"Shifts": rows, "Deviations": rows, "People": rows}
        (rows incl. header). `overrides`: deviations from eco
        (list of dicts with person/from/to/type/note)."""
        sh, p1 = parse_shifts(tables.get("Shifts") or [])
        dev, p2 = parse_deviations(tables.get("Deviations") or [])
        people, p3 = parse_people(tables.get("People") or [])
        ov, p4 = overrides_to_intervals(overrides)
        return cls(sh + dev + ov, people, problems=p1 + p2 + p3 + p4, **kw)

    def canonical(self, name):
        return self._canon.get(name.strip().lower(), name.strip())

    def on_call(self, t=None):
        t = _as_dt(t)
        primary, backup, off, shifts = [], [], set(), []

        def add(lst, names):
            for nm in names:
                c = self.canonical(nm)
                if c not in lst:
                    lst.append(c)

        for iv in self.intervals:
            if not iv.covers(t):
                continue
            if iv.source == "sheet" and iv.row is not None and iv.role in ("on", "backup"):
                if iv.role == "on" and not any(s is iv for s in shifts):
                    shifts.append(iv)
            if iv.role == "on":
                add(primary, iv.people)
            elif iv.role == "backup":
                add(backup, iv.people)
            else:
                off.update(self.canonical(n) for n in iv.people)
        primary = [p for p in primary if p not in off]
        backup = [p for p in backup if p not in off and p not in primary]
        return OnCall(t, primary, backup, sorted(off), self.next_change(t), shifts)

    def next_change(self, t=None):
        t = _as_dt(t)
        cand = [x for iv in self.intervals for x in (iv.start, iv.end) if x > t]
        return min(cand) if cand else None

    def timeline(self, start=None, hours=48):
        """[(from, to, OnCall)] for every change in the window."""
        start = _as_dt(start)
        end = start + timedelta(hours=hours)
        points = sorted(
            {start}
            | {x for iv in self.intervals for x in (iv.start, iv.end) if start < x < end}
        )
        out = []
        for i, p in enumerate(points):
            q = points[i + 1] if i + 1 < len(points) else end
            oc = self.on_call(p)
            if out and out[-1][2].primary == oc.primary and out[-1][2].backup == oc.backup:
                out[-1] = (out[-1][0], q, out[-1][2])
            else:
                out.append((p, q, oc))
        return out

    def escalation_contacts(self):
        return [n for n, p in self.people.items() if p.get("escalation")]

    def preference(self, person, key, default=None):
        return self.people.get(self.canonical(person), {}).get(key, default)


def _as_dt(t):
    if t is None:
        return datetime.now(TZ)
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t, TZ)
    if t.tzinfo is None:
        return t.replace(tzinfo=TZ)
    return t


def overrides_to_intervals(overrides):
    out, problems = [], []
    now = time.time()
    for o in overrides or ():
        try:
            if o.get("to") and o["to"] < now - 86400:
                continue  # long over
            out.append(
                Interval(
                    datetime.fromtimestamp(o["from"], TZ),
                    datetime.fromtimestamp(o["to"], TZ),
                    [o["person"]],
                    o.get("type", "on"),
                    o.get("note", ""),
                    source="eco",
                )
            )
        except Exception as e:
            problems.append(f"eco shift override {o!r}: {e}")
    return out, problems


# ---------------------------------------------------------------------------
# Google Sheet source


def _gspread_client(credentials=None):
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        credentials or DEFAULT_CREDENTIALS, scope
    )
    return gspread.authorize(creds)


def fetch_sheet_tables(sheet_key, credentials=None):
    gc = _gspread_client(credentials)
    sh = gc.open_by_key(sheet_key)
    tables = {}
    for ws in sh.worksheets():
        if ws.title in ("Shifts", "Deviations", "People"):
            tables[ws.title] = ws.get_all_values()
    return tables


def sheet_config():
    s = SYSTEM.read()
    return s.get("shift_sheet_key"), s.get("shift_sheet_credentials") or DEFAULT_CREDENTIALS


def load_plan(max_age=REFRESH_S, fetch=True, now=None):
    """The current `ShiftPlan`: from the cache if younger than `max_age`,
    else fetched from the sheet (cache updated). If fetching fails the last
    cached copy is used and the plan is marked `stale`."""
    now = time.time() if now is None else now
    cache = SHIFT_CACHE.read()
    key, creds = sheet_config()
    overrides = SYSTEM.read().get("shift_overrides", [])
    problems = []
    tables, fetched_at, stale = cache.get("tables"), cache.get("fetched_at"), False
    if key and fetch and (not fetched_at or now - fetched_at > max_age or cache.get("key") != key):
        try:
            tables = fetch_sheet_tables(key, creds)
            fetched_at = now
            SHIFT_CACHE.write({"tables": tables, "fetched_at": now, "key": key})
        except Exception as e:
            problems.append(f"shift sheet not readable ({type(e).__name__}: {e}); using cached copy")
            stale = True
    if not key:
        problems.append("no shift sheet configured (alarms.shift_sheet(key))")
    plan = ShiftPlan.from_tables(
        tables or {}, overrides, fetched_at=fetched_at, source=key or "", stale=stale
    )
    plan.problems = problems + plan.problems
    return plan


def add_override(person, start, end, typ="on", note="", by=None):
    if typ not in DEVIATION_TYPES:
        raise ValueError(f"type must be one of {DEVIATION_TYPES}")
    rec = {"person": person, "from": float(start), "to": float(end), "type": typ, "note": note, "by": by, "at": time.time()}

    def upd(doc):
        lst = [o for o in doc.get("shift_overrides", []) if o.get("to", 0) > time.time() - 86400]
        lst.append(rec)
        doc["shift_overrides"] = lst

    SYSTEM.update(upd)
    return rec


def clear_overrides(person=None):
    def upd(doc):
        doc["shift_overrides"] = [
            o
            for o in doc.get("shift_overrides", [])
            if person is not None and o.get("person", "").lower() != person.lower()
        ]

    SYSTEM.update(upd)


# ---------------------------------------------------------------------------
# creating the sheet

_HOWTO = [
    ["Bernina shift plan for phone alarms"],
    [""],
    ["eco reads this sheet every 2 minutes. Whoever is 'On call' gets critical alarms as repeating"],
    ["emergency messages (until someone acknowledges). 'Backup' is paged when nobody on call"],
    ["acknowledges within the escalation time. Phone keys are NOT kept here."],
    [""],
    ["Shifts: one row per shift. Date 2026-11-12 or 12.11.2026, times 07:00. To <= From means the next day."],
    ["        Several names: separate with commas."],
    ["Deviations: individual times differing from the plan."],
    ["        Type off = not available (overrides Shifts), on = additionally on call, backup = additionally backup."],
    ["        From/To: '2026-11-12 08:00'. Date only = whole day. Empty To = end of that day."],
    ["People: Off-shift alerts none/quiet/normal (critical alarms while not on shift), Escalation yes/no."],
    [""],
    ["In eco: alarms.oncall(), alarms.upcoming(), alarms.take_shift(hours=8), alarms.leave_shift(hours=2)"],
]


def create_shift_sheet(title="bernina_shift_plan", folder_id=DEFAULT_FOLDER_ID, credentials=None, example=True):
    """Create a new shift-plan spreadsheet (owned by the service account,
    inside the run-table Drive folder) with the three tabs, header
    formatting, drop-downs and a few example rows. Returns the spreadsheet;
    store its key with ``alarms.shift_sheet(key)``."""
    gc = _gspread_client(credentials)
    sh = gc.create(title, folder_id=folder_id)
    first = sh.get_worksheet(0)
    ws_s = sh.add_worksheet("Shifts", 200, len(SHIFTS_HEADER))
    ws_d = sh.add_worksheet("Deviations", 200, len(DEVIATIONS_HEADER))
    ws_p = sh.add_worksheet("People", 50, len(PEOPLE_HEADER))
    ws_h = sh.add_worksheet("How to", 30, 1)
    sh.del_worksheet(first)

    today = datetime.now(TZ).date()
    d = lambda k: (today + timedelta(days=k)).isoformat()  # noqa: E731
    shifts = [SHIFTS_HEADER]
    devs = [DEVIATIONS_HEADER]
    people = [PEOPLE_HEADER]
    if example:
        shifts += [
            [d(0), "07:00", "15:00", "Example Person A", "Example Person C", "example - replace"],
            [d(0), "15:00", "23:00", "Example Person B", "Example Person C", ""],
            [d(0), "23:00", "07:00", "Example Person A", "Example Person B", "night"],
        ]
        devs += [[ "Example Person B", f"{d(0)} 17:00", f"{d(0)} 18:00", "off", "example - replace"]]
        people += [
            ["Example Person A", "none", "no", ""],
            ["Example Person B", "quiet", "no", ""],
            ["Example Person C", "normal", "yes", "beamline scientist"],
        ]
    ws_s.update("A1", shifts, value_input_option="RAW")
    ws_d.update("A1", devs, value_input_option="RAW")
    ws_p.update("A1", people, value_input_option="RAW")
    ws_h.update("A1", _HOWTO, value_input_option="RAW")

    reqs = []
    for ws, widths in (
        (ws_s, [110, 70, 70, 260, 200, 260]),
        (ws_d, [180, 150, 150, 90, 260]),
        (ws_p, [180, 130, 100, 260]),
        (ws_h, [900]),
    ):
        reqs.append({
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                               "backgroundColor": {"red": 0.9, "green": 0.92, "blue": 0.95}}},
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        })
        reqs.append({
            "updateSheetProperties": {
                "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        })
        for i, w in enumerate(widths):
            reqs.append({
                "updateDimensionProperties": {
                    "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                    "properties": {"pixelSize": w},
                    "fields": "pixelSize",
                }
            })
    # keep dates/times as typed text, so the sheet never turns 07:00 into 0.29
    for ws, ncols in ((ws_s, 3), (ws_d, 3)):
        reqs.append({
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": ncols},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    def dropdown(ws, col, values):
        return {
            "setDataValidation": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": col, "endColumnIndex": col + 1},
                "rule": {
                    "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in values]},
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        }

    reqs.append(dropdown(ws_d, 3, DEVIATION_TYPES))
    reqs.append(dropdown(ws_p, 1, OFF_SHIFT_CHOICES))
    reqs.append(dropdown(ws_p, 2, ("yes", "no")))
    sh.batch_update({"requests": reqs})
    return sh
