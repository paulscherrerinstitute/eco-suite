"""Where is it stored? Bernina's storage spreadsheet from the session.

The beamline keeps a Google spreadsheet of what is stored where: the tab
"Storage location" lists items (``Accessoires PI Hexapod -> BC6``), the tab
"Storage map" the cabinets, drawers and shelves themselves (``BC6: EH.002f,
PSI standard cabinet, solid doors, key``). This module reads both, joins
them, and answers::

    from eco.utilities import storage_locations as storage
    storage.where("hexapod")     # items matching, with cabinet and room
    storage.contents("BC6")      # everything stored in BC6
    storage.check()              # entries the map cannot place

or in the session ``%storage hexapod``, ``%storage BC6``, ``%storage --check``.
``%ei where are the vacuum stages?`` gets the matching rows as context.

The sheet is read over HTTPS (its CSV export, public with the link) at most
once an hour and kept in eco's index directory; without network the copy
kept last is used. The spreadsheet stays the place to edit: this is a view.

The map's lock-code column is never read: codes stay in the spreadsheet,
out of the cache, the terminal and the %ei model's input.
"""

import csv
import difflib
import io
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field

SHEET_ID = os.environ.get("ECO_STORAGE_SHEET", "1UZ3huHARZKjpqglKFb5fsWW_IKFDkBdN5Z8QSxf7Tvw")
#: tab ids (gid) in that sheet
ITEMS_GID = "1073252383"  # "Storage location"
MAP_GID = "608494214"  # "Storage map"
SHEET_URL = "https://docs.google.com/spreadsheets/d/{sheet}/edit#gid={gid}"
CSV_URL = "https://docs.google.com/spreadsheets/d/{sheet}/export?format=csv&gid={gid}"
MAX_AGE = 3600
TIMEOUT = 10
CACHE_NAME = "storage_sheet.json"
#: map columns kept, by their header in the sheet; anything else (the lock
#: codes) is dropped while reading
_MAP_COLUMNS = {
    "name": "name", "room number": "room", "description": "description",
    "details / special": "details", "available space": "space", "locking system": "lock",
}
_ITEM_COLUMNS = {"item": "item", "space needed": "space", "location": "location",
                 "sub-location": "sub_location", "comment": "comment"}


@dataclass
class Location:
    name: str  # the code: BC6, SD1, EH.050, z527
    room: str = ""
    description: str = ""
    details: str = ""
    space: str = ""  # free space, as noted
    lock: str = ""  # Key, Code lock, Not lockable (never the code)
    label: str = ""  # the full name if it has more ("SC1 (Alvrina 1)")

    def __str__(self):
        where = ", ".join(x for x in (self.room, self.description) if x)
        return f"{self.name} ({where})" if where else self.name


@dataclass
class Item:
    item: str
    location: str  # as written in the sheet
    sub_location: str = ""
    comment: str = ""
    space: str = ""
    places: list = field(default_factory=list)  # [Location] it resolves to
    unknown: list = field(default_factory=list)  # parts of `location` not in the map

    def where(self):
        parts = [str(p) for p in self.places] + self.unknown
        text = " or ".join(parts) or "(no location given)"
        if self.sub_location:
            text += f", {self.sub_location}"
        return text


class Storage:
    def __init__(self, items, locations, fetched=0.0, stale=None):
        self.items = items
        self.locations = locations  # {code: Location}
        self.fetched = fetched
        self.stale = stale  # why the copy is old, or None

    # -- lookups -----------------------------------------------------------------------

    def location(self, name):
        key = _norm(name)
        return self.locations.get(key) or next(
            (loc for loc in self.locations.values() if _norm(loc.label) == key), None
        )

    def contents(self, name):
        loc = self.location(name)
        key = loc.name if loc else _norm(name)
        return [i for i in self.items if any(p.name == key for p in i.places)
                or any(_norm(u) == key for u in i.unknown)]

    def where(self, query, n=10):
        """Items matching `query` (words, typos forgiven), best first."""
        words = _words(query)
        if not words:
            return []
        scored = []
        for it in self.items:
            text_words = _words(" ".join((it.item, it.comment, it.sub_location)))
            if not text_words:
                continue
            score = sum(_word_score(w, text_words) for w in words) / len(words)
            if score >= 0.5:
                scored.append((score, it))
        scored.sort(key=lambda x: -x[0])
        return [it for _s, it in scored[:n]]

    def lookup(self, query, n=10):
        """A cabinet's contents if `query` names one, else where()."""
        loc = self.location(query.strip())
        if loc is not None:
            return loc, self.contents(loc.name)
        return None, self.where(query, n=n)

    def check(self):
        """[str]: what the sheet itself gets wrong -- locations the map does
        not know, items without a location, the same item twice."""
        out = []
        unknown = {}
        for it in self.items:
            if not it.location.strip():
                out.append(f"no location: {it.item}")
            for u in it.unknown:
                unknown.setdefault(u, []).append(it.item)
        for u, items in sorted(unknown.items()):
            close = difflib.get_close_matches(_norm(u), list(self.locations), n=1, cutoff=0.6)
            hint = f" (did you mean {close[0]}?)" if close else ""
            out.append(f"location {u!r} is not in the storage map{hint}: " + "; ".join(items[:4])
                       + (f" and {len(items) - 4} more" if len(items) > 4 else ""))
        seen = {}
        for it in self.items:
            seen.setdefault(it.item.strip().lower(), []).append(it)
        for name, same in seen.items():
            if len(same) > 1:
                out.append(f"listed {len(same)} times: {same[0].item} ({', '.join(i.location for i in same)})")
        return out

    # -- text --------------------------------------------------------------------------

    def describe(self, items, limit=20):
        lines = []
        for it in items[:limit]:
            extra = f"  -- {it.comment}" if it.comment else ""
            lines.append(f"{it.item}: {it.where()}{extra}")
        if len(items) > limit:
            lines.append(f"... {len(items) - limit} more")
        return "\n".join(lines)

    def age_note(self):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.fetched)) if self.fetched else "never"
        return f"storage sheet as of {when}" + (f" ({self.stale})" if self.stale else "")


# -- reading the sheet -----------------------------------------------------------------


def _norm(name):
    """'BC 13' -> 'BC13', 'BC14 (FF)' -> 'BC14', 'bc1*' -> 'BC1'."""
    name = re.sub(r"\(.*?\)", "", str(name or "")).strip().rstrip("*").strip()
    name = re.sub(r"^([A-Za-z]+)\s+(\d)", r"\1\2", name)
    return name.upper() if re.fullmatch(r"[A-Za-z]+\d+[A-Za-z]?", name) else name


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 1]


def _word_score(word, words):
    if word in words:
        return 1.0
    if any(w.startswith(word) or (len(w) > 3 and word.startswith(w)) for w in words):
        return 0.9
    if len(word) >= 4 and difflib.get_close_matches(word, words, n=1, cutoff=0.8):
        return 0.75
    return 0.0


def _header(rows, wanted):
    """(row index, {field: column}) of the first row holding the headers."""
    for i, row in enumerate(rows):
        cols = {}
        for j, cell in enumerate(row):
            key = cell.strip().lower()
            if key in wanted:
                cols[wanted[key]] = j
        if "name" in cols and "room" in cols or "item" in cols and "location" in cols:
            return i, cols
    return None, {}


def parse_map(text):
    rows = list(csv.reader(io.StringIO(text)))
    start, cols = _header(rows, _MAP_COLUMNS)
    locations = {}
    if start is None:
        return locations
    for row in rows[start + 1:]:
        cell = lambda f: row[cols[f]].strip() if f in cols and cols[f] < len(row) else ""  # noqa: E731
        label = cell("name")
        if not label or not (cell("room") or cell("description")):
            continue  # photo captions and blank rows below the table
        name = _norm(label)
        locations[name] = Location(name, cell("room"), cell("description"), cell("details"), cell("space"),
                                   cell("lock"), label if label != name else "")
    return locations


def parse_items(text, locations):
    rows = list(csv.reader(io.StringIO(text)))
    start, cols = _header(rows, _ITEM_COLUMNS)
    items = []
    if start is None:
        return items
    for row in rows[start + 1:]:
        cell = lambda f: row[cols[f]].strip() if f in cols and cols[f] < len(row) else ""  # noqa: E731
        if not cell("item"):
            continue
        it = Item(cell("item"), cell("location"), cell("sub_location"), cell("comment"), cell("space"))
        for part in re.split(r"[/,]", it.location):
            part = part.strip()
            if not part:
                continue
            loc = locations.get(_norm(part))
            if loc is not None:
                if loc not in it.places:
                    it.places.append(loc)
            elif re.fullmatch(r"(top|bottom|lower|upper|left|right)( shelf)?", part, re.I):
                it.sub_location = ", ".join(x for x in (it.sub_location, part) if x)
            else:
                it.unknown.append(part)
        items.append(it)
    return items


def _fetch(gid):
    req = urllib.request.Request(CSV_URL.format(sheet=SHEET_ID, gid=gid), headers={"User-Agent": "eco"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _cache_path():
    from eco import logs_ask

    return logs_ask.index_dir() / CACHE_NAME


def _from_cache(data, stale=None):
    locations = {k: Location(**v) for k, v in data["locations"].items()}
    items = parse_items(data["items_csv"], locations)
    return Storage(items, locations, data.get("fetched", 0), stale)


_loaded = None


def load(refresh=False, max_age=MAX_AGE):
    """The Storage, read from the sheet if the kept copy is older than
    max_age (or refresh), else from the copy."""
    global _loaded
    if _loaded is not None and not refresh and time.time() - _loaded.fetched < max_age and not _loaded.stale:
        return _loaded
    path = _cache_path()
    cached = None
    try:
        cached = json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    if cached and not refresh and time.time() - cached.get("fetched", 0) < max_age:
        _loaded = _from_cache(cached)
        return _loaded
    try:
        locations = parse_map(_fetch(MAP_GID))
        items_csv = _fetch(ITEMS_GID)
    except Exception as exc:
        if cached:
            _loaded = _from_cache(cached, stale=f"sheet not reachable: {type(exc).__name__}")
            return _loaded
        raise RuntimeError(f"the storage sheet could not be read ({type(exc).__name__}: {exc}) "
                           "and there is no copy yet") from exc
    data = {"fetched": time.time(), "items_csv": items_csv,
            "locations": {k: vars(v) for k, v in locations.items()}}
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        os.chmod(path, 0o600)
    except OSError:
        pass
    _loaded = Storage(parse_items(items_csv, locations), locations, data["fetched"])
    return _loaded


# -- the session -------------------------------------------------------------------------


def where(query, n=10):
    return load().where(query, n=n)


def contents(location):
    return load().contents(location)


def check():
    return load().check()


def sheet_url():
    return SHEET_URL.format(sheet=SHEET_ID, gid=ITEMS_GID)


def show(query):
    """Print what `query` finds: a cabinet's contents, or matching items."""
    st = load()
    loc, found = st.lookup(query)
    if loc is not None:
        extra = ", ".join(x for x in (loc.details, f"free: {loc.space}" if loc.space else "", loc.lock) if x)
        print(f"{loc}" + (f" -- {extra}" if extra else ""))
        print(st.describe(found, limit=100) or "  (nothing listed there)")
    elif found:
        print(st.describe(found))
    else:
        print(f"nothing in the storage sheet matches {query!r} -- {sheet_url()}")
    print(f"({st.age_note()})")


#: words that make a %ei question one about storage
QUESTION_RX = re.compile(r"\b(where (is|are|do|can|to)|stored|storage|store|cabinet|shelf|shelves|drawer|kept)\b", re.I)


def context_for(question, n=8):
    """Text for %ei: the sheet rows matching a storage question, or ""."""
    if not QUESTION_RX.search(question):
        return ""
    try:
        st = load()
    except Exception:
        return ""
    stop = {"where", "is", "are", "the", "do", "we", "can", "find", "stored", "store", "storage", "kept", "our",
            "to", "put", "a", "an", "of", "in", "which", "cabinet", "shelf", "drawer", "what"}
    words = " ".join(w for w in _words(question) if w not in stop)
    loc, found = st.lookup(words) if words else (None, [])
    if not found:
        return ""
    head = f"Storage sheet ({st.age_note()}, {sheet_url()})"
    if loc is not None:
        head += f" -- contents of {loc}"
    return f"[storage] {head}:\n{st.describe(found, limit=n * 2 if loc else n)}"


def storage_magic(line):
    """%storage TEXT -- where it is stored (the beamline's storage sheet);
    %storage BC6 -- what is in BC6; --check: entries the map cannot place;
    --refresh: read the sheet again; --url: the sheet's address."""
    line = line.strip()
    try:
        if line in ("", "-h", "--help"):
            print(storage_magic.__doc__)
        elif line == "--url":
            print(sheet_url())
        elif line == "--check":
            problems = load().check()
            print("\n".join(problems) if problems else "every item has a location the storage map knows")
        elif line == "--refresh":
            st = load(refresh=True)
            print(f"{len(st.items)} items in {len(st.locations)} places ({st.age_note()})")
        else:
            show(line)
    except RuntimeError as exc:
        print(f"eco: {exc}")


def register_magic(shell):
    shell.register_magic_function(storage_magic, "line", "storage")
