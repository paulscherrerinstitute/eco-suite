"""eco.logs_scilog -- SciLog read with your own PSI account, for %ei.

eco's SciLog client (eco.utilities.elog_scilog) logs in as the beamline's
functional account, which sees that account's logbooks. Personal accounts
can only log in through the browser (PSI single sign-on), so this module
works with the token SciLog issues after that login:

  1. log in at https://scilog.psi.ch,
  2. in the browser's developer console (F12):  copy(localStorage.id_token)
  3. in eco:  %ei --scilog-token   (asks for it without echoing it and
     keeps it in ~/.eco/scilog_token, mode 600), or set $ECO_SCILOG_TOKEN.

The token carries your PSI groups, so everything below sees what you see
in the web interface. It lasts about two weeks; then do the same again.
Never store a personal token under a shared account (gac-bernina): every
user of that account would read SciLog as you.

Plain `requests` rather than the `scilog` package: that package deletes
its token cache file (which also holds the functional account's token) on
any 401, and reads more than an index needs.
"""

import base64
import datetime
import json
import os
import re
import stat
import time
from pathlib import Path

from eco import logs_ask, logs_sources

URL = "https://scilog.psi.ch/api/v1"
TOKEN_PATH = Path.home() / ".eco" / "scilog_token"
PAGE = 500  # snippets per request

TOKEN_STEPS = """\
  1. log in at https://scilog.psi.ch
  2. browser developer console (F12):  copy(localStorage.id_token)"""
TOKEN_HELP = f"""\
eco.ei: no personal SciLog token. To read SciLog as yourself:
{TOKEN_STEPS}
  3. here:  %ei --scilog-token   (paste it at the hidden prompt)
It lasts about two weeks. Only on your own account, never on a shared one."""


class TokenError(RuntimeError):
    """No token, or SciLog refused it (expired)."""


# -- the token ----------------------------------------------------------------------


def _clean(token):
    token = (token or "").strip().strip('"').strip("'")
    return token[len("Bearer "):].strip() if token.startswith("Bearer ") else token


def token(path=None):
    """(token, where it came from) or (None, None): $ECO_SCILOG_TOKEN, then
    ~/.eco/scilog_token."""
    env = _clean(os.environ.get("ECO_SCILOG_TOKEN"))
    if env:
        return env, "$ECO_SCILOG_TOKEN"
    path = Path(path or TOKEN_PATH)
    try:
        text = _clean(path.read_text())
    except OSError:
        return None, None
    return (text, str(path)) if text else (None, None)


def save_token(value, path=None):
    """Keep a token in ~/.eco/scilog_token, readable only by you."""
    value = _clean(value)
    if value.count(".") != 2:
        raise ValueError("that is not a SciLog token (expected three dot-separated parts)")
    path = Path(path or TOKEN_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def claims(value):
    """The token's payload (name, email, roles, exp) -- read, not verified;
    SciLog verifies it on every request."""
    try:
        part = _clean(value).split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def describe(value):
    """'Name <email>, 12 groups, valid until 2026-11-14 10:00 (13.9 days)'."""
    c = claims(value)
    who = c.get("name") or "?"
    if c.get("email"):
        who += f" <{c['email']}>"
    out = f"{who}, {len(c.get('roles') or [])} groups"
    if c.get("exp"):
        left = (c["exp"] - time.time()) / 86400
        until = datetime.datetime.fromtimestamp(c["exp"]).strftime("%Y-%m-%d %H:%M")
        out += f", {'valid until' if left > 0 else 'EXPIRED since'} {until}" + (f" ({left:.1f} days)" if left > 0 else "")
    return out


# -- reading ------------------------------------------------------------------------


class Reader:
    """Read-only SciLog access with one personal token."""

    def __init__(self, token_value=None, url=URL, timeout=30, session=None):
        if token_value is None:
            token_value, _where = token()
        if not token_value:
            raise TokenError(TOKEN_HELP)
        self.token = _clean(token_value)
        self.url = url.rstrip("/")
        self.timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self._locations = None

    def get(self, path, filt=None):
        params = {"filter": json.dumps(filt)} if filt else None
        r = self.session.get(
            self.url + path, params=params, timeout=self.timeout,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"},
        )
        if r.status_code == 401:
            raise TokenError(
                f"SciLog refused the token ({describe(self.token)}) -- get a new one:\n{TOKEN_STEPS}\n"
                "  3. %ei --scilog-token new"
            )
        r.raise_for_status()
        return r.json()

    def me(self):
        return self.get("/users/me")

    def locations(self):
        """{location id: name}."""
        if self._locations is None:
            rows = self.get("/locations", {"fields": {"id": True, "name": True}})
            self._locations = {r["id"]: r.get("name") or "" for r in rows}
        return self._locations

    def logbooks(self, location=None, pgroup=None, name=None):
        """Logbooks you can read, newest activity first, as dicts {id, name,
        pgroup, location, private, touched}; each filter is a case-
        insensitive substring of that field (pgroup: of the owner group)."""
        rows = self.get("/logbooks", {"fields": {
            "id": True, "name": True, "ownerGroup": True, "location": True,
            "isPrivate": True, "touchedAt": True, "updatedAt": True, "deleted": True,
        }})
        locs = self.locations() if rows else {}
        out = []
        for r in rows:
            if r.get("deleted"):
                continue
            lb = {
                "id": r["id"],
                "name": r.get("name") or "",
                # many logbooks have no owner group but the pgroup in their name
                "pgroup": r.get("ownerGroup") or next(iter(re.findall(r"p\d{5}", r.get("name") or "")), ""),
                "location": locs.get(r.get("location"), "") if r.get("location") else "",
                "private": bool(r.get("isPrivate")),
                "touched": r.get("touchedAt") or r.get("updatedAt") or "",
            }
            if all(
                not want or want.lower() in lb[field].lower()
                for field, want in (("location", location), ("pgroup", pgroup), ("name", name))
            ):
                out.append(lb)
        out.sort(key=lambda lb: lb["touched"], reverse=True)
        return out

    def paragraphs(self, logbook_id, after_iso=None, limit=100000):
        """Text posts of one logbook (or of a list of them) created after
        `after_iso`, oldest first, as dicts {id, t, created_at, author,
        tags, text, logbook}."""
        parent = {"inq": list(logbook_id)} if isinstance(logbook_id, (list, tuple, set)) else logbook_id
        where = {"snippetType": "paragraph", "parentId": parent}
        if after_iso:
            where["createdAt"] = {"gt": after_iso}
        out, skip = [], 0
        while len(out) < limit:
            rows = self.get("/basesnippets", {
                "where": where, "order": ["createdAt ASC"], "limit": PAGE, "skip": skip,
                "fields": {"id": True, "createdAt": True, "createdBy": True, "tags": True, "textcontent": True, "deleted": True, "parentId": True},
            })
            out += [p for p in (_record(r) for r in rows if not r.get("deleted")) if p]
            if len(rows) < PAGE:
                break
            skip += PAGE
        return out[:limit]

    def search(self, text, limit=20):
        """SciLog's own search over everything you can read, newest first.
        The server matches one case-insensitive regular expression, so
        word order counts there; several words are therefore searched for
        by the longest one, keeping the posts that contain all of them."""
        words = text.split()
        if len(words) <= 1:
            return self._search(text, limit)
        longest = max(words, key=len)
        found = self._search(re.escape(longest), max(limit * 25, 500))
        want = [w.lower() for w in words]
        return [p for p in found if all(w in p["text"].lower() for w in want)][:limit]

    def _search(self, pattern, limit):
        rows = self.get(f"/basesnippets/search={_quote(pattern)}", {
            "where": {"snippetType": "paragraph"}, "order": ["createdAt DESC"], "limit": limit,
        })
        return [p for p in (_record(r) for r in rows if not r.get("deleted")) if p]


def parse_filters(text):
    """'--location bernina --pgroup p23618 --name kern' (or a bare word,
    taken as part of the name; --all for no filter) -> dict for
    Reader.logbooks(), plus "all": True when --all was given."""
    import shlex

    words = shlex.split(text or "")
    out, i = {}, 0
    while i < len(words):
        w = words[i]
        if w == "--all":
            out["all"] = True
        elif w in ("--location", "--pgroup", "--name") and i + 1 < len(words):
            out[w[2:]] = words[i + 1]
            i += 1
        elif not w.startswith("--"):
            out["name"] = (out.get("name", "") + " " + w).strip()
        else:
            raise ValueError(f"unknown option {w} (--location, --pgroup, --name, --all)")
        i += 1
    return out


def _quote(text):
    from urllib.parse import quote

    return quote(text, safe="")


def _record(r):
    created = r.get("createdAt")
    if not created:
        return None
    try:
        t = datetime.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    return {
        "id": r.get("id"),
        "t": t,
        "created_at": created,
        "author": r.get("createdBy") or "",
        "tags": list(r.get("tags") or ()),
        "text": logs_sources.html_to_text(r.get("textcontent") or ""),
        "logbook": r.get("parentId") or "",
    }


# -- indexing -----------------------------------------------------------------------


def index_logbooks(reader, logbooks, db_path=logs_ask.DEFAULT_DB, report=print):
    """Add the posts of `logbooks` (Reader.logbooks() dicts) that are new
    since the last fetch to the %ei index, labelled with the logbook.

    A logbook seen for the first time is read completely; all others are
    checked together, in one request for posts newer than the newest post
    the last run saw anywhere. (A logbook's touchedAt is no help: opening
    it in the browser changes it.) Returns {"logbooks", "new_posts", "items"}."""
    con = logs_ask._connect(db_path)
    try:
        known = {
            k.split(":", 1)[1]: v
            for k, v in con.execute("SELECT key, value FROM meta WHERE key LIKE 'scilog_logbook_last:%'")
        }
        by_id = {lb["id"]: lb for lb in logbooks}
        posts = {lb_id: [] for lb_id in by_id}
        fresh = [lb for lb in logbooks if lb["id"] not in known]
        for i, lb in enumerate(fresh, 1):
            posts[lb["id"]] = reader.paragraphs(lb["id"])
            if report:
                report(f"  [{i}/{len(fresh)}] {_label(lb)}: {len(posts[lb['id']])} posts")
        seen = [lb_id for lb_id in by_id if lb_id in known]
        if seen:
            for p in reader.paragraphs(seen, after_iso=min(known[lb_id] for lb_id in seen)):
                if p["logbook"] in posts and p["created_at"] > known[p["logbook"]]:
                    posts[p["logbook"]].append(p)
            if report:
                for lb_id in seen:
                    if posts[lb_id]:
                        report(f"  {_label(by_id[lb_id])}: {len(posts[lb_id])} new posts")
        newest = max([p["created_at"] for ps in posts.values() for p in ps] + [known[lb_id] for lb_id in seen] + [""])
        with con:
            for lb_id, ps in posts.items():
                if ps:
                    _forget_posts(con, {p["id"] for p in ps})
                label = _label(by_id[lb_id])
                for p in ps:
                    path = f"scilog:{lb_id}:{p['id']}"
                    context = f"{p['author']} [{label}]" + (f" [{', '.join(p['tags'])}]" if p["tags"] else "")
                    source = f"scilog {label}"
                    con.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)", (p["text"], context, "scilog", "ok", "", p["t"], source, path))
                    for cmd in logs_sources.command_lines(p["text"]):
                        con.execute(
                            "INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                            (cmd, p["text"][:300], "note_command", "ok", "", p["t"], source, path),
                        )
            if newest:
                # also for logbooks without (new) posts: next time they are
                # checked together with the rest, from the same point
                con.executemany(
                    "INSERT OR REPLACE INTO meta VALUES (?, ?)",
                    [(f"scilog_logbook_last:{lb_id}", newest) for lb_id in by_id],
                )
        total = con.execute("SELECT count(*) FROM items").fetchone()[0]
    finally:
        con.close()
    return {"logbooks": len(logbooks), "new_posts": sum(map(len, posts.values())), "items": total}


def _label(lb):
    return lb["name"] + (f" ({lb['pgroup']})" if lb["pgroup"] else "")


def _forget_posts(con, ids):
    """Drop already indexed copies of these posts (fetched again, or indexed
    through the functional account as scilog:<pgroup>:<id>): one scan of
    the scilog paths per logbook, then deletes by rowid. A per-post
    `path LIKE` scans the whole full-text table every time."""
    rows = con.execute("SELECT rowid, path FROM items WHERE kind IN ('scilog', 'note_command') AND path LIKE 'scilog:%'")
    stale = [(rowid,) for rowid, path in rows if path.rsplit(":", 1)[-1] in ids]
    con.executemany("DELETE FROM items WHERE rowid = ?", stale)


def save_selection(filters, db_path=logs_ask.DEFAULT_DB):
    """Remember which logbooks --scilog-index chose, for a plain repeat."""
    con = logs_ask._connect(db_path)
    with con:
        con.execute("INSERT OR REPLACE INTO meta VALUES ('scilog_selection', ?)", (json.dumps(filters),))
    con.close()


def load_selection(db_path=logs_ask.DEFAULT_DB):
    con = logs_ask._connect(db_path)
    row = con.execute("SELECT value FROM meta WHERE key = 'scilog_selection'").fetchone()
    con.close()
    return json.loads(row[0]) if row else None
