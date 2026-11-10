"""The local server behind the SciLog viewer: SciLog's API as the database,
seen through your own token, and the page in eco/scilog_app/static.

Same security model as eco web (eco/web/gateway.py): it binds 127.0.0.1, so
reaching it from elsewhere takes an SSH login to the host (``ssh -L``), and
a per-server token is required on top, since every account on a shared
console can reach 127.0.0.1. The token comes once in the URL and is then
kept in an HttpOnly cookie. The SciLog token never leaves the server: the
page gets cleaned HTML (content.clean_post), and images and attachments
through img/<id> and file/<id>, cached in a private directory.

An HTML attachment (an interactive plot) is served with
``Content-Security-Policy: sandbox allow-scripts``: it runs in an origin of
its own, without the page's cookie or access to anything else, and the page
loads it only when asked to.
"""

import asyncio
import datetime
import hashlib
import hmac
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import tornado.web
from tornado.ioloop import IOLoop

from eco import logs_ask, logs_scilog, logs_sources
from eco.scilog_app import content

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
COOKIE = "eco_scilog_token"
DEFAULT_PORT = 8798
SCILOG_WEB = "https://scilog.psi.ch"
_ID = re.compile(r"^[0-9a-f]{24}$")
#: which logbooks may be written to: ~/.eco/scilog_app.json {"write_logbooks": [ids] or "all"}
CONFIG_PATH = Path.home() / ".eco" / "scilog_app.json"
#: until a user says otherwise, writing goes only to the test logbook (p22891)
DEFAULT_WRITE_LOGBOOKS = ["68afe9fe6f90f96e7370f9b3"]
MAX_UPLOAD = 50 * 1024 * 1024


def load_config(path=None):
    try:
        cfg = json.loads(Path(path or CONFIG_PATH).read_text())
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("write_logbooks", DEFAULT_WRITE_LOGBOOKS)
    return cfg


def save_config(cfg, path=None):
    """Write the config (only this user may read it)."""
    path = Path(path or CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def view_version(raw):
    """A fingerprint of what a view says: legacy views carry no updatedAt."""
    keep = {k: raw.get(k) for k in ("name", "description", "configuration", "parentId", "location")}
    return hashlib.sha1(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


def view_summary(raw, logbook_id, names):
    """A view as the page shows it: name, when made (from its id), panels in words."""
    panels = []
    for i, w in enumerate((raw.get("configuration") or {}).get("widgetConfig") or []):
        cfg = w.get("config") or {}
        general, filt = cfg.get("general") or {}, cfg.get("filter") or {}
        others = []
        for lid in filt.get("additionalLogbooks") or []:
            if lid not in [o["id"] for o in others]:
                others.append({"id": lid, "name": names.get(lid, ""), "readable": lid in names})
        panels.append({"index": i, "type": general.get("type") or "", "title": general.get("title") or "",
                       "target": filt.get("targetId") or "", "target_name": names.get(filt.get("targetId"), ""),
                       "tags": list(filt.get("tags") or []), "excludeTags": list(filt.get("excludeTags") or []),
                       "snippetType": list(filt.get("snippetType") or []), "logbooks": others,
                       "duplicates": len(filt.get("additionalLogbooks") or []) - len(others),
                       "size": [w.get("cols"), w.get("rows")], "at": [w.get("x"), w.get("y")]})
    name = raw.get("name") or ""
    return {"id": raw["id"], "name": name, "description": raw.get("description") or "", "owner": raw.get("ownerGroup") or "",
            "created": int(raw["id"][:8], 16) if _ID.match(raw.get("id", "")) else 0,
            "on": "logbook" if raw.get("parentId") == logbook_id else "location",
            "personal": name.endswith("_personal"), "template": bool((raw.get("configuration") or {}).get("isTemplate")),
            "panels": panels, "version": view_version(raw)}


def _tag_list(value):
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValueError("tags must be a list of words")
    return [t.strip() for t in value if t.strip()]


def apply_view_changes(raw, changes):
    """The PATCH payload for a view: name/description, and per panel
    {"index", "title", "tags", "excludeTags", "logbooks": [ids]}. Everything
    else of the view (layout, order, widget kinds) is kept as it is."""
    config = json.loads(json.dumps(raw.get("configuration") or {}))
    widgets = config.get("widgetConfig") or []
    payload = {}
    if "name" in changes:
        name = str(changes["name"]).strip()
        if not name:
            raise ValueError("a view needs a name")
        payload["name"] = name[:120]
    if "description" in changes:
        payload["description"] = str(changes["description"])[:500]
    for ch in changes.get("panels") or []:
        i = ch.get("index")
        if not isinstance(i, int) or not 0 <= i < len(widgets):
            raise ValueError("no such panel")
        cfg = widgets[i].setdefault("config", {})
        if "title" in ch:
            cfg.setdefault("general", {})["title"] = str(ch["title"])[:120]
        filt = cfg.setdefault("filter", {})
        for key in ("tags", "excludeTags"):
            if key in ch:
                filt[key] = _tag_list(ch[key])
        if "logbooks" in ch:
            ids = ch["logbooks"]
            if not isinstance(ids, list) or not all(isinstance(x, str) and _ID.match(x) for x in ids):
                raise ValueError("logbooks must be logbook ids")
            filt["additionalLogbooks"] = list(dict.fromkeys(ids))
    if changes.get("panels"):
        payload["configuration"] = config
    if not payload:
        raise ValueError("nothing to change")
    return payload


#: SciLog's role of every logged-in user: in a readACL, readable by everyone with a PSI account
EVERYONE_LOGGED_IN = "any-authenticated-user"


def _epoch(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


def _first_line(text):
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:120]
    return ""


class Store:
    """Posts of the logbooks looked at, kept up to date incrementally (posts
    created or edited since the last look), and a file cache."""

    def __init__(self, reader=None, cache_dir=None, config=None):
        self._reader = reader
        self.logbooks = {}  # id -> {"posts": {id: record}, "raw": {id: post as SciLog has it}, "since": iso}
        self._locks = {}
        self._tasks = {}  # logbook id -> {task id: task as SciLog has it}, as last read
        self._lock = threading.Lock()
        self.cache_dir = Path(cache_dir or logs_ask.index_dir() / "scilog_files")
        self.config = config if config is not None else load_config()
        #: where set_write_logbook() saves (none for a store given its config, e.g. in tests)
        self.config_path = CONFIG_PATH if config is None else None
        self.uploads = {}  # fileId -> file container, uploaded while editing
        self._me = None
        # whose SciLog access: "beamline" (the functional account), "stored" (the personal token kept
        # in ~/.eco/scilog_token), "temporary" (a token held in this process only) or "given" (tests);
        # None: not chosen yet -- on a shared account nothing is read until it is
        self.access = "given" if reader is not None else None
        self._temporary = None  # (token, until): never written anywhere

    #: the longest a temporary token is used (hours), whatever its own expiry says
    TEMPORARY_HOURS = 8

    class NotChosen(logs_scilog.TokenError):
        """On a shared account, whose access to use has not been chosen yet."""

    @property
    def reader(self):
        if self.access == "temporary" and self._temporary and self._temporary[1] < time.time():
            self._forget_access()
            raise logs_scilog.TokenError(f"the temporary token was used for {self.TEMPORARY_HOURS} hours and is forgotten now")
        if self._reader is None:
            access = self.access
            if access is None:
                if logs_scilog._shared_account():
                    raise self.NotChosen("choose whose SciLog access to use")
                access = "stored" if logs_scilog.token()[0] else "beamline"
            if access == "temporary":
                value, until = self._temporary
                r = logs_scilog.Reader(value, timeout=60)
                hhmm = datetime.datetime.fromtimestamp(until).strftime("%H:%M")
                r.identity = f"{logs_scilog.describe(value).split(',')[0]} (temporary, until {hhmm})"
            else:
                r = logs_scilog.reader(timeout=60, personal=access == "stored")
            self._reader, self.access = r, access
        return self._reader

    def _forget_access(self):
        with self._lock:
            self._reader = None
            self._me = None
            self.access = None if self.access != "given" else "given"
            self._temporary = None
            self.logbooks.clear()  # what may be read depends on whose access it is
            self._tasks.clear()

    def access_options(self):
        """What can be chosen here: the beamline account (if its password is readable), the
        stored personal token (not on a shared account), a temporary token (always)."""
        shared = bool(logs_scilog._shared_account())
        stored, _ = (None, None) if shared else logs_scilog.token()
        out = {"shared": shared, "temporary_hours": self.TEMPORARY_HOURS,
               "beamline": {"available": logs_scilog.functional_available(), "who": logs_scilog.FUNCTIONAL_USER},
               "stored": {"available": bool(stored)}}
        if stored:
            exp = logs_scilog.claims(stored).get("exp")
            out["stored"].update(who=logs_scilog.describe(stored).split(",")[0], expires=exp,
                                 available=not (exp and exp < time.time()))
        return out

    def choose_access(self, kind):
        """Read SciLog from now on as the beamline account ("beamline") or with the stored
        personal token ("stored"); returns the new token_status()."""
        opts = self.access_options()
        if kind not in ("beamline", "stored"):
            raise ValueError("choose 'beamline' or 'stored' (a temporary token is entered instead)")
        if not opts[kind]["available"]:
            raise ValueError("the beamline account's password is not readable here" if kind == "beamline"
                             else "there is no valid personal token kept here")
        self._forget_access()
        self.access = kind
        return self.token_status()

    def list_logbooks(self):
        rows = self.reader.logbooks()
        for lb in rows:
            lb["url"] = f"{SCILOG_WEB}/logbooks/{lb['id']}/dashboard"
        return rows

    # -- the token ------------------------------------------------------------------------

    def token_status(self):
        """{"ok", "identity", "personal", "expires" (epoch), "why", "can_set" (a token may be
        kept here), "access", "must_choose", "options"}: whether SciLog can be read, as whom,
        until when, and what else could be chosen."""
        can_set = not logs_scilog._shared_account()
        base = {"can_set": can_set, "options": self.access_options()}
        try:
            r = self.reader
        except self.NotChosen:
            return {**base, "ok": False, "must_choose": True, "why": "Choose whose SciLog access to use"}
        except logs_scilog.TokenError as exc:
            text = str(exc)
            why = (text if "temporary" in text else "SciLog refused the token (expired?)" if "refused" in text
                   else "There is no SciLog token here yet")
            return {**base, "ok": False, "why": why, "must_choose": logs_scilog._shared_account() is not None}
        exp = logs_scilog.claims(r.token).get("exp")
        personal = not r.identity.startswith("the beamline account")
        if self.access == "temporary":
            exp = min(exp or self._temporary[1], self._temporary[1])
        if personal and exp and exp < time.time():
            return {**base, "ok": False, "why": "the SciLog token has expired", "expires": exp}
        return {**base, "ok": True, "identity": r.identity, "personal": personal, "access": self.access,
                "expires": exp if personal else None}

    def set_token(self, value, keep=True):
        """Check a token with SciLog and read SciLog with it from now on; returns the new
        token_status(). keep=True keeps it (mode 600, ~/.eco/scilog_token); otherwise, and
        always on a shared account, it is held in this process only, for at most
        TEMPORARY_HOURS."""
        value = logs_scilog._clean(value)
        if value.count(".") != 2:
            raise ValueError("that is not a SciLog token (expected three dot-separated parts)")
        exp = logs_scilog.claims(value).get("exp")
        if exp and exp < time.time():
            raise ValueError("this token has already expired: log in to SciLog again and copy the new one")
        probe = logs_scilog.Reader(value, timeout=30)
        probe.get("/logbooks", {"limit": 1, "fields": {"id": True}})  # TokenError if SciLog refuses it
        if keep and not logs_scilog._shared_account():
            logs_scilog.save_token(value)
            self._forget_access()
            self.access = "stored"
        else:
            self._forget_access()
            self._temporary = (value, min(exp or float("inf"), time.time() + self.TEMPORARY_HOURS * 3600))
            self.access = "temporary"
        return self.token_status()

    # -- who is reading, and may they write --------------------------------------

    def me(self):
        """{"identity", "email", "roles", "writes", "why_not", "write_logbooks"}."""
        if self._me is None:
            reader = self.reader
            claims = logs_scilog.claims(reader.token)
            # as the beamline account too (as scilog.post() does): the page says so before saving
            as_beamline = reader.identity.startswith("the beamline account")
            email = claims.get("email", "") or (logs_scilog.FUNCTIONAL_USER if as_beamline else "")
            self._me = {"identity": reader.identity, "email": email, "roles": set(claims.get("roles") or ()),
                        "writes": True, "why_not": "", "write_logbooks": self.config.get("write_logbooks"),
                        "as_beamline": as_beamline, "access": self.access}
        return self._me

    def may_write_to(self, logbook_id):
        me = self.me()
        allowed = me["write_logbooks"]
        if not me["writes"]:
            return False, me["why_not"]
        if allowed != "all" and logbook_id not in (allowed or ()):
            return False, f"writing to this logbook is not switched on ({CONFIG_PATH}: \"write_logbooks\")"
        return True, ""

    def set_write_logbook(self, logbook_id, on=True):
        """Switch writing to one logbook on or off (in CONFIG_PATH): what the
        page's "Allow writing here" does, after the user confirmed it."""
        if not isinstance(logbook_id, str) or not _ID.match(logbook_id):
            raise ValueError("not a logbook id")
        me = self.me()
        if not me["writes"]:
            raise PermissionError(me["why_not"])
        allowed = self.config.get("write_logbooks")
        if allowed == "all":
            if on:
                return self._public_me()
            raise ValueError(f"all logbooks are switched on in {CONFIG_PATH}; edit it to switch single ones off")
        allowed = [x for x in (allowed or ()) if x != logbook_id] + ([logbook_id] if on else [])
        self.config["write_logbooks"] = allowed
        saved = load_config(self.config_path) if self.config_path else {}
        saved["write_logbooks"] = allowed
        if self.config_path:
            save_config(saved, self.config_path)
        self._me = None
        return self._public_me()

    def _public_me(self):
        return {k: v for k, v in self.me().items() if k != "roles"}

    def may_edit(self, raw):
        """As SciLog decides: the author, or a member of one of the post's update groups."""
        me = self.me()
        if not me["writes"]:
            return False
        return raw.get("createdBy") == me["email"] or bool(me["roles"] & set(raw.get("updateACL") or ()))

    def record(self, raw):
        """What the page gets of one post."""
        text = logs_sources.html_to_text(raw.get("textcontent") or "")
        created, updated = raw.get("createdAt") or "", raw.get("updatedAt") or ""
        written = _epoch(created)
        order = raw.get("defaultOrder")
        at = order / 1e6 if isinstance(order, (int, float)) and order > 0 else written
        expires = _epoch(raw.get("expiresAt") or "")
        kids = [k for k in raw.get("subsnippets") or () if not k.get("deleted")]
        quote = next((k for k in kids if k.get("linkType") == "quote"), None)
        return {
            "id": raw["id"],
            "t": at,  # where SciLog places it (defaultOrder): the creation time, unless it was inserted
            "written": written,
            "inserted": abs(at - written) > 600,
            "order": order if isinstance(order, (int, float)) else None,
            "expires": expires,
            "comments": [self.child_record(k) for k in sorted((k for k in kids if k.get("linkType") == "comment"),
                                                              key=lambda k: k.get("createdAt") or "")],
            "quote": dict(self.child_record(quote), of=raw.get("_quote_of")) if quote else None,
            "shared": EVERYONE_LOGGED_IN in (raw.get("readACL") or ()),
            "groups": sorted(raw.get("readACL") or ()),
            "can_delete": self._safe_may_delete(raw),
            "created": created,
            "updated": updated,
            "edited": bool(updated and created and _epoch(updated) - _epoch(created) > 60),
            "author": raw.get("createdBy") or "",
            "editor": raw.get("updatedBy") or "",
            "tags": list(raw.get("tags") or ()),
            "html": content.clean_post(raw.get("textcontent") or "", raw.get("files")),
            "text": text,
            "title": _first_line(text),
            "can_edit": self._safe_may_edit(raw),
        }

    def child_record(self, raw):
        """A comment, or the copy of the post a reply quotes."""
        text = logs_sources.html_to_text(raw.get("textcontent") or "")
        created, updated = raw.get("createdAt") or "", raw.get("updatedAt") or ""
        return {"id": raw["id"], "t": _epoch(created), "author": raw.get("createdBy") or "",
                "html": content.clean_post(raw.get("textcontent") or "", raw.get("files")), "text": text,
                "created": created, "updated": updated, "editor": raw.get("updatedBy") or "",
                "edited": bool(updated and created and _epoch(updated) - _epoch(created) > 60),
                "expires": _epoch(raw.get("expiresAt") or ""), "tags": list(raw.get("tags") or ()),
                "can_edit": raw.get("linkType") == "comment" and self._safe_may_edit(raw),
                "can_delete": raw.get("linkType") == "comment" and self._safe_may_delete(raw)}

    @staticmethod
    def _link_quotes(book, raws):
        """A reply's quote is a copy without a link to what it copies: find
        the original by its text, else by the copied expiry (SciLog's
        default, creation + 3 days)."""
        by_text = None
        for raw in raws:
            quote = next((k for k in raw.get("subsnippets") or () if k.get("linkType") == "quote" and not k.get("deleted")), None)
            if not quote:
                continue
            if by_text is None:
                by_text = {}
                for r in book["raw"].values():
                    by_text.setdefault((r.get("textcontent") or "").strip(), r["id"])
            found = by_text.get((quote.get("textcontent") or "").strip())
            if not found and quote.get("expiresAt"):
                born = _epoch(quote["expiresAt"]) - 3 * 86400
                found = next((r["id"] for r in book["raw"].values() if abs(_epoch(r.get("createdAt") or "") - born) < 2), None)
            raw["_quote_of"] = found if found != raw["id"] else None

    def _changed_by_children(self, book, since, already):
        """Posts of this logbook whose comments (or quote) changed since
        `since`: a comment does not touch its post's updatedAt."""
        if not since or not hasattr(self.reader, "children_since"):
            return []
        out = {}
        for kid in self.reader.children_since(since):
            parent = book["raw"].get(kid.get("parentId"))
            if parent is None:
                continue
            book["since"] = max(book["since"], kid.get("updatedAt") or "", kid.get("createdAt") or "")
            kids = [k for k in parent.get("subsnippets") or () if k["id"] != kid["id"]]
            parent["subsnippets"] = kids + [kid]
            if parent["id"] not in already:
                out[parent["id"]] = parent
        return list(out.values())

    def may_delete(self, raw):
        """As SciLog decides: a member of one of the post's delete groups."""
        me = self.me()
        return me["writes"] and bool(me["roles"] & set(raw.get("deleteACL") or ()))

    def _safe_may_delete(self, raw):
        try:
            return self.may_delete(raw)
        except Exception:
            return False

    def delete(self, logbook_id, post_id):
        """Delete a post in SciLog (it goes to the logbook's history, where
        SciLog can restore it); returns {"removed": id}."""
        raw, parent = self._raw_or_comment(logbook_id, post_id)
        ok, why = self.may_write_to(logbook_id)
        if not ok:
            raise PermissionError(why)
        if not self.may_delete(raw):
            raise PermissionError("SciLog lets only the members of this post's delete groups delete it")
        self.reader.delete_post(post_id)
        book = self.logbooks[logbook_id]
        if parent is not None:  # a comment: its post is shown again without it
            parent["subsnippets"] = [k for k in parent.get("subsnippets") or () if k["id"] != post_id]
            book["posts"][parent["id"]] = self.record(parent)
            return {"removed": post_id, "post": book["posts"][parent["id"]]}
        book["raw"].pop(post_id, None)
        book["posts"].pop(post_id, None)
        return {"removed": post_id}

    def _safe_may_edit(self, raw):
        try:
            return self.may_edit(raw)
        except Exception:
            return False

    def posts(self, logbook_id, since=None):
        """{"posts": [...], "removed": [ids], "since": iso}: everything the
        first time (since=None), afterwards what changed after `since`."""
        with self._lock:
            lock = self._locks.setdefault(logbook_id, threading.Lock())
        with lock:
            book = self.logbooks.get(logbook_id)
            if book is None:
                raw = self.reader.posts(logbook_id, children=True)
                book = {"posts": {}, "raw": {}, "since": ""}
                changed = raw
                self.logbooks[logbook_id] = book
            else:
                old_since = book["since"]
                changed = self.reader.posts(logbook_id, updated_after=old_since, children=True) if old_since else []
                changed = list(changed) + self._changed_by_children(book, old_since, {r["id"] for r in changed})
            removed = []
            for r in changed:
                book["since"] = max(book["since"], r.get("updatedAt") or "", r.get("createdAt") or "")
                if r.get("deleted"):
                    book["posts"].pop(r["id"], None)
                    book["raw"].pop(r["id"], None)
                    removed.append(r["id"])
                else:
                    book["raw"][r["id"]] = r
                    book["posts"][r["id"]] = self.record(r)
            self._link_quotes(book, [r for r in changed if not r.get("deleted")])
            for r in changed:
                if not r.get("deleted") and r["id"] in book["posts"]:
                    book["posts"][r["id"]] = self.record(r)
            if since is None:
                posts = sorted(book["posts"].values(), key=lambda p: p["t"])
            else:
                ids = {r["id"] for r in changed if not r.get("deleted")}
                posts = sorted((book["posts"][i] for i in ids), key=lambda p: p["t"])
            return {"posts": posts, "removed": removed, "since": book["since"]}

    # -- writing ----------------------------------------------------------------------

    def _raw(self, logbook_id, post_id):
        if logbook_id not in self.logbooks:
            self.posts(logbook_id)
        raw = self.logbooks[logbook_id]["raw"].get(post_id)
        if raw is None:
            raise KeyError(f"no post {post_id} in this logbook")
        return raw

    def _raw_or_comment(self, logbook_id, snippet_id):
        """(snippet, None) for a post, (comment, its post) for a comment."""
        try:
            return self._raw(logbook_id, snippet_id), None
        except KeyError:
            pass
        for post in self.logbooks[logbook_id]["raw"].values():
            for kid in post.get("subsnippets") or ():
                if kid["id"] == snippet_id and kid.get("linkType") == "comment" and not kid.get("deleted"):
                    return kid, post
        raise KeyError(f"no post or comment {snippet_id} in this logbook")

    def _check_write(self, logbook_id, raw=None):
        ok, why = self.may_write_to(logbook_id)
        if not ok:
            raise PermissionError(why)
        if raw is not None and not self.may_edit(raw):
            raise PermissionError("SciLog lets you edit only your own posts (or those of a group you are in)")

    # -- tasks ----------------------------------------------------------------------------------
    # A logbook's to-do list. SciLog applies no edit period to tasks, so they can be changed
    # at any time; their order is SciLog's defaultOrder (as for posts), which the viewer sets
    # when a task is dragged. SciLog's own page lists them in creation order for now.

    #: room left between two tasks' orders when they are spread out again (µs, as SciLog's)
    TASK_STEP = 1_000_000

    def task_record(self, raw):
        order = raw.get("defaultOrder")
        if not isinstance(order, (int, float)):
            order = _epoch(raw.get("createdAt") or "") * 1e6
        return {"id": raw["id"], "text": raw.get("content") or "", "done": bool(raw.get("isDone")), "order": order,
                "author": raw.get("createdBy") or "", "created": raw.get("createdAt") or "",
                "editor": raw.get("updatedBy") or "", "updated": raw.get("updatedAt") or "",
                "can_edit": self._safe_may_edit(raw), "can_delete": self._safe_may_delete(raw)}

    def tasks(self, logbook_id):
        """{"tasks": [...]}: the logbook's tasks in their order."""
        if not _ID.match(logbook_id or ""):
            raise ValueError("not a logbook id")
        raws = self.reader.tasks(logbook_id)
        self._tasks[logbook_id] = {r["id"]: r for r in raws}
        records = sorted((self.task_record(r) for r in raws), key=lambda t: (t["order"], t["created"]))
        return {"tasks": records}

    def _task(self, logbook_id, task_id, edit=True):
        if not _ID.match(str(task_id)):
            raise ValueError("not a task id")
        ok, why = self.may_write_to(logbook_id)
        if not ok:
            raise PermissionError(why)
        if logbook_id not in self._tasks or task_id not in self._tasks[logbook_id]:
            self.tasks(logbook_id)
        raw = self._tasks[logbook_id].get(task_id)
        if raw is None:
            raise KeyError(f"no task {task_id} in this logbook")
        if edit and not self.may_edit(raw):
            raise PermissionError("SciLog lets you change only your own tasks (or those of a group you are in)")
        return raw

    def add_task(self, logbook_id, text, first=False):
        """A new task, at the end of the list (or at its top)."""
        text = (text or "").strip()
        if not text:
            raise ValueError("the task is empty")
        if not _ID.match(logbook_id or ""):
            raise ValueError("not a logbook id")
        ok, why = self.may_write_to(logbook_id)
        if not ok:
            raise PermissionError(why)
        book = self.reader.logbook(logbook_id)
        payload = {k: book[k] for k in ("ownerGroup", "accessGroups", "isPrivate") if book.get(k) is not None}
        payload.update(parentId=logbook_id, snippetType="task", content=text, isDone=False)
        current = self.tasks(logbook_id)["tasks"]
        if first and current:
            payload["defaultOrder"] = int(current[0]["order"] - self.TASK_STEP)
        elif current:  # after the last one, also when the list was reordered into the future
            payload["defaultOrder"] = int(max(current[-1]["order"] + self.TASK_STEP, time.time() * 1e6))
        self.reader.create_task(payload)
        return self.tasks(logbook_id)

    def update_task(self, logbook_id, task_id, text=None, done=None):
        """Change a task's text and/or whether it is done."""
        self._task(logbook_id, task_id)
        payload = {}
        if text is not None:
            if not text.strip():
                raise ValueError("the task is empty")
            payload["content"] = text.strip()
        if done is not None:
            payload["isDone"] = bool(done)
        if payload:
            self.reader.update_task(task_id, payload)
        return self.tasks(logbook_id)

    def move_task(self, logbook_id, task_id, before=None, after=None):
        """Put a task right before `before` (or after `after`, a task id) by giving it an order in
        between; where there is no room left, the list is spread out again first."""
        self._task(logbook_id, task_id)
        tasks = [t for t in self.tasks(logbook_id)["tasks"] if t["id"] != task_id]
        ids = [t["id"] for t in tasks]
        anchor = before or after
        if anchor not in ids:
            raise KeyError(f"no task {anchor} in this logbook")
        i = ids.index(anchor) + (0 if before else 1)  # the moved task's place in `tasks`
        lo = tasks[i - 1]["order"] if i > 0 else None
        hi = tasks[i]["order"] if i < len(tasks) else None
        if lo is not None and hi is not None and hi - lo < 2:
            # spread out, keeping the order: only the tasks whose order changes are written --
            # which needs the right to change each of them (all of them, or none is touched)
            if not all(t["can_edit"] for t in tasks):
                raise PermissionError("there is no room between these two tasks, and making room would change "
                                      "tasks SciLog does not let you change; move them the other way round")
            for n, t in enumerate(tasks):
                new = int(tasks[0]["order"]) + n * self.TASK_STEP
                if t["order"] != new:
                    self.reader.update_task(t["id"], {"defaultOrder": new})
                    t["order"] = new
            lo = tasks[i - 1]["order"] if i > 0 else None
            hi = tasks[i]["order"] if i < len(tasks) else None
        if lo is None:
            order = hi - self.TASK_STEP
        elif hi is None:
            order = lo + self.TASK_STEP
        else:
            order = lo + (hi - lo) // 2
        self.reader.update_task(task_id, {"defaultOrder": int(order)})
        return self.tasks(logbook_id)

    def delete_task(self, logbook_id, task_id):
        raw = self._task(logbook_id, task_id, edit=False)
        if not self.may_delete(raw):
            raise PermissionError("SciLog lets only the members of this task's delete groups delete it")
        self.reader.delete_task(task_id)
        return self.tasks(logbook_id)

    # -- SciLog's views (dashboard layouts) -------------------------------------------------

    def views(self, logbook_id):
        """The views SciLog offers for this logbook (its own and its
        location's), each with its panels in words, plus the logbooks this
        reader knows by name."""
        if not _ID.match(logbook_id or ""):
            raise ValueError("not a logbook id")
        lb = self.reader.logbook(logbook_id)
        parents = [logbook_id] + ([lb["location"]] if _ID.match(str(lb.get("location") or "")) else [])
        names = {row["id"]: row["name"] for row in self.list_logbooks()}
        rows = [r for r in self.reader.views(parents) if not r.get("deleted")]
        return {"views": [view_summary(r, logbook_id, names) for r in sorted(rows, key=lambda r: r["id"])],
                "logbooks": [{"id": k, "name": v} for k, v in sorted(names.items(), key=lambda kv: kv[1].lower())],
                "can_edit": self.may_write_to(logbook_id)[0]}

    def _view_for_change(self, logbook_id, view_id, version):
        self._check_write(logbook_id)
        if not _ID.match(view_id or ""):
            raise ValueError("not a view id")
        raw = self.reader.view(view_id)
        if raw.get("parentId") not in (logbook_id, self.reader.logbook(logbook_id).get("location")):
            raise PermissionError("this view does not belong to this logbook")
        if version and view_version(raw) != version:
            raise RuntimeError("the view was changed meanwhile (in SciLog or by someone else): reload the list")
        self._backup_view(raw)
        return raw

    def _backup_view(self, raw):
        folder = self.cache_dir.parent / "scilog_view_backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = folder / f"{raw['id']}-{stamp}.json"
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump(raw, f, indent=1)
        return path

    def change_view(self, logbook_id, view_id, version, changes):
        """Rename a view, or change its panels' titles, tags and other
        logbooks; returns the view as the page shows it."""
        raw = self._view_for_change(logbook_id, view_id, version)
        payload = apply_view_changes(raw, changes or {})
        self.reader.update_view(view_id, payload)
        names = {row["id"]: row["name"] for row in self.list_logbooks()}
        return view_summary(self.reader.view(view_id), logbook_id, names)

    def remove_view(self, logbook_id, view_id, version):
        raw = self._view_for_change(logbook_id, view_id, version)
        self.reader.delete_view(view_id)
        return {"deleted": view_id, "name": raw.get("name")}

    def for_editor(self, logbook_id, post_id):
        """The post (or comment) as the editor opens it."""
        raw, _ = self._raw_or_comment(logbook_id, post_id)
        self._check_write(logbook_id, raw)
        return {"html": content.editor_html(raw.get("textcontent") or "", raw.get("files")), "tags": list(raw.get("tags") or ()),
                "updated": raw.get("updatedAt") or ""}

    def upload(self, logbook_id, post_id, filename, data, content_type=None):
        """Upload a file to SciLog at once (not only when the post is saved),
        with the access of the post or else the logbook; returns what the
        editor inserts."""
        raw = self._raw(logbook_id, post_id) if post_id else None
        self._check_write(logbook_id, raw)
        if len(data) > MAX_UPLOAD:
            raise ValueError(f"{filename} is larger than {MAX_UPLOAD // 2**20} MB")
        content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        acl = raw or self.reader.logbook(logbook_id)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else content_type.split("/")[-1]
        is_image = content_type.startswith("image/")
        fields = {k: acl[k] for k in ("ownerGroup", "accessGroups", "isPrivate") if acl.get(k) is not None}
        fields["fileExtension"] = content_type.split("/")[1] if is_image else ext
        snippet = self.reader.upload_file(data, filename, content_type, fields)
        container = {"fileHash": str(uuid.uuid4()), "fileId": snippet["id"], "accessHash": snippet.get("accessHash"),
                     "fileExtension": content_type if is_image else f"file/{ext}"}
        self.uploads[snippet["id"]] = container
        return {"fileId": snippet["id"], "url": f"{'img' if is_image else 'file'}/{snippet['id']}", "name": filename,
                "image": is_image}

    def _known_files(self, raw):
        known = {f["fileId"]: f for f in (raw or {}).get("files") or () if f.get("fileId")}
        return {**self.uploads, **known}

    #: the longest edit period a new post may be given, in days
    MAX_EDIT_DAYS = 3650

    def _insert_order(self, logbook_id, after_id=None, before_id=None):
        """A defaultOrder right after `after_id` (or right before
        `before_id`) in SciLog's order: a post placed there after the fact."""
        book = self.logbooks.get(logbook_id) or {}
        orders = sorted(r.get("defaultOrder") for r in (book.get("raw") or {}).values()
                        if isinstance(r.get("defaultOrder"), (int, float)))
        anchor = (book.get("raw") or {}).get(after_id or before_id)
        if anchor is None or not isinstance(anchor.get("defaultOrder"), (int, float)):
            raise ValueError("the post to insert next to is not known")
        if before_id:
            b = anchor["defaultOrder"]
            earlier = [o for o in orders if o < b]
            a = earlier[-1] if earlier else b - 2_000_000  # before the very first post: a second earlier
        else:
            a = anchor["defaultOrder"]
            later = [o for o in orders if o > a]
            if not later:
                return None  # the last post: a new post goes after it anyway
            b = later[0]
        if b - a < 2:
            raise ValueError("there is no room between these two posts")
        return int(a + (b - a) // 2)

    def save(self, logbook_id, post_id, html, tags, expected_updated=None, edit_days=None, after=None, before=None,
             comment_on=None, reply_to=None):
        """Create (post_id None) or update a post from the editor's HTML;
        returns the post as the page shows it. With expected_updated, an
        update is refused if the post was changed meanwhile. A new post can
        be given an edit period (days; SciLog's own default is 3) and be
        placed right after another post (`after`, a post id).

        As SciLog's own page writes them: `comment_on` (a post id) makes it a
        comment under that post (the post is returned); `reply_to` a new post
        at the end that quotes that post, i.e. carries a copy of it."""
        raw, parent = self._raw_or_comment(logbook_id, post_id) if post_id else (None, None)
        self._check_write(logbook_id, raw)
        for target in (comment_on, reply_to):
            if target and not _ID.match(str(target)):
                raise ValueError("not a post id")
        on = self._raw(logbook_id, comment_on or reply_to) if (comment_on or reply_to) else None
        textcontent, files = content.to_scilog(html, self._known_files(raw))
        if not textcontent.strip():
            raise ValueError("the post is empty")
        tags = [t.strip() for t in tags or () if t and t.strip()]
        if raw is None:
            payload = {"snippetType": "paragraph", "linkType": "paragraph", "parentId": logbook_id,
                       "textcontent": textcontent, "files": files, "tags": tags}
            if comment_on:
                payload.update(linkType="comment", parentId=comment_on)
            if edit_days is not None:
                days = float(edit_days)
                if not 0 < days <= self.MAX_EDIT_DAYS:
                    raise ValueError(f"the edit period must be between 1 and {self.MAX_EDIT_DAYS} days")
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
                payload["expiresAt"] = until.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if (after or before) and not comment_on:
                if not _ID.match(str(after or before)):
                    raise ValueError("not a post id")
                order = self._insert_order(logbook_id, after_id=after, before_id=before)
                if order is not None:
                    payload["defaultOrder"] = order
            new = self.reader.create_post(payload)
            post_id = new["id"]
            if reply_to:
                quoted = self.reader.post(reply_to)
                copy = {k: quoted[k] for k in ("textcontent", "files", "tags", "expiresAt", "isPrivate") if k in quoted}
                self.reader.create_post({**copy, "snippetType": "paragraph", "linkType": "quote", "parentId": post_id})
        else:
            current = self.reader.post(post_id)
            if expected_updated and current.get("updatedAt") and current["updatedAt"] != expected_updated:
                raise RuntimeError(f"the post was changed meanwhile (by {current.get('updatedBy')} at "
                                   f"{current.get('updatedAt')}); reload it before saving")
            self.reader.update_post(post_id, {"textcontent": textcontent, "files": files, "tags": tags})
        for f in files:
            self.uploads.pop(f["fileId"], None)
        if comment_on or parent is not None:  # a comment: the post it belongs to, with it
            return self._reread(logbook_id, comment_on or parent["id"])
        return self._reread(logbook_id, post_id)

    def _reread(self, logbook_id, post_id):
        """The post as the page shows it, after a write: what changed since
        the last look is read (the whole logbook if it was not read yet)."""
        book = self.logbooks.get(logbook_id)
        self.posts(logbook_id, book["since"] if book else None)
        return self.logbooks[logbook_id]["posts"][post_id]

    def share(self, logbook_id, post_id, on=True):
        """Make a post readable by everyone logged in to SciLog (any PSI
        account), or no longer: its attachments, its comments and the post
        itself, in that order, so the post is never visible with a broken
        image. SciLog allows this to whoever may change the post, also after
        its edit period."""
        raw = self._raw(logbook_id, post_id)
        self._check_write(logbook_id, raw)
        current = self.reader.post(post_id)

        def flip(acl):
            acl = list(acl or ())
            new = sorted(set(acl) | {EVERYONE_LOGGED_IN}) if on else [g for g in acl if g != EVERYONE_LOGGED_IN]
            return new if set(new) != set(acl) else None

        for f in {f.get("fileId") for f in current.get("files") or () if f.get("fileId")}:
            new = flip(self.reader.file_snippet(f).get("readACL"))
            if new is not None:
                self.reader.set_read_acl("filesnippet", f, new)
        for kid in raw.get("subsnippets") or ():
            new = flip(kid.get("readACL"))
            if new is not None:
                self.reader.set_read_acl("basesnippets", kid["id"], new)
                kid["readACL"] = new
        new = flip(current.get("readACL"))
        if new is not None:
            self.reader.set_read_acl("basesnippets", post_id, new)
            raw["readACL"] = new
        elif current.get("readACL") is not None:
            raw["readACL"] = list(current["readACL"])
        record = self.record(raw)
        self.logbooks[logbook_id]["posts"][post_id] = record
        return record

    def set_heading(self, logbook_id, post_id, index, level):
        """Change one heading's level in place (from the outline)."""
        raw = self._raw(logbook_id, post_id)
        self._check_write(logbook_id, raw)
        current = self.reader.post(post_id)
        text = content.set_heading(current.get("textcontent") or "", index, level)
        self.reader.update_post(post_id, {"textcontent": text})
        return self._reread(logbook_id, post_id)

    def _cached(self, kind, file_id, fetch):
        """(bytes, content type, name) from the cache, else fetched and kept."""
        path = self.cache_dir / file_id
        meta = path.with_suffix(".json")
        if path.exists() and meta.exists():
            info = json.loads(meta.read_text())
            return path.read_bytes(), info["type"], info.get("name")
        data, ctype, name = fetch(file_id)
        try:
            self.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            for target, payload in ((path, data), (meta, json.dumps({"type": ctype, "name": name}).encode())):
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(payload)
        except OSError:
            pass
        return data, ctype, name

    def image(self, file_id):
        return self._cached("img", file_id, lambda i: (*self.reader.image(i), None))

    def file(self, file_id):
        return self._cached("file", file_id, self.reader.file)


# -- handlers ---------------------------------------------------------------------


class _Base(tornado.web.RequestHandler):
    @property
    def app(self):
        return self.settings["scilog_app"]

    def authorized(self):
        given = self.get_query_argument("token", None) or self.get_cookie(COOKIE)
        return bool(given) and hmac.compare_digest(given, self.app.token)

    def local_host(self):
        """The page was asked for by a local name. A web site that points its
        own domain at 127.0.0.1 (DNS rebinding) is turned away here: its
        requests carry its own name in Host."""
        host = self.request.host.rsplit(":", 1)[0] if not self.request.host.startswith("[") else self.request.host.split("]")[0] + "]"
        return host in ("localhost", "127.0.0.1", "[::1]")

    def prepare(self):
        if not self.local_host():
            raise tornado.web.HTTPError(403, reason="not a local address: open the viewer as http://localhost:<port>/")
        if not self.authorized():
            raise tornado.web.HTTPError(403, reason="no valid key: open the address the viewer printed when it started")

    def set_default_headers(self):
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("Referrer-Policy", "no-referrer")

    async def blocking(self, fn, *args):
        return await IOLoop.current().run_in_executor(self.app.pool, fn, *args)

    def write_error(self, status_code, **kwargs):
        exc = kwargs.get("exc_info", (None, None))[1]
        self.finish({"error": f"{type(exc).__name__}: {exc}" if exc and status_code >= 500 else self._reason})


_REFUSED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>SciLog viewer: not opened</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:40em;margin:3em auto;padding:0 16px;color:#1d1f23;background:#fff}}
code{{background:#f2f3f5;padding:0 4px;border-radius:3px}}@media(prefers-color-scheme:dark){{body{{color:#e3e5e8;background:#15171b}}
code{{background:#22252b}}}}</style></head><body><h1>The SciLog viewer did not open</h1>{body}</body></html>"""


class _Index(_Base):
    """The page. It refuses with an explanation rather than a bare 403: the two refusals that
    happen in practice are an address with an old key and an address by the machine's name."""

    def prepare(self):
        if not self.local_host():  # the token it checks itself, to set the cookie
            self.refuse(
                f"<p>It was asked for as <code>{html.escape(self.request.host)}</code>. For your safety it only "
                "answers to <code>localhost</code> (or <code>127.0.0.1</code>), so that no web site can reach it.</p>"
                f"<p>Open <code>http://localhost:{self.app.port}/?token=…</code> as the viewer printed it. "
                "From another computer, forward the port first: "
                f"<code>ssh -L {self.app.port}:localhost:{self.app.port} {html.escape(socket.gethostname())}</code>, "
                "then open the printed address there.</p>")

    def refuse(self, body):
        self.set_status(403)
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.set_header("Cache-Control", "no-store")
        self.finish(_REFUSED_PAGE.format(body=body))

    def stale(self):
        self.refuse(
            "<p>This address carries a key this viewer does not know. Every start of the viewer has a new key, "
            "so this is most likely an address from an earlier start: from the browser's history, a bookmark or "
            "a tab left open. On a shared console it can also be another user's viewer on this port.</p>"
            "<p>Open the address the viewer printed <b>this time</b> (<code>scilog.viewer()</code> prints it "
            "again; it may be on another port than before). Bookmark the page itself, not that address.</p>")

    def get(self):
        if self._finished:
            return
        token = self.get_query_argument("token", None)
        if token is not None:
            if not hmac.compare_digest(token, self.app.token):
                self.stale()
                return
            self.set_cookie(COOKIE, token, httponly=True, samesite="Strict", expires_days=30)
            self.redirect("/" + (f"#{self.get_query_argument('go')}" if self.get_query_argument("go", None) else ""))
            return
        if not self.authorized():
            self.stale()
            return
        self.set_header("Cache-Control", "no-store")
        with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
            self.finish(f.read())


class _Static(tornado.web.StaticFileHandler):
    def set_extra_headers(self, path):
        # checked again on every load (ETag): an updated eco reaches the page at once
        self.set_header("Cache-Control", "no-cache")


class _Me(_Base):
    async def get(self):
        self.finish(await self.blocking(self.app.store._public_me))


class _Write(_Base):
    """Writes need a header a plain form or another site's page cannot set,
    and must come from this server's own page."""

    def prepare(self):
        super().prepare()
        if self.request.headers.get("X-Eco-Scilog") != "1":
            raise tornado.web.HTTPError(403, reason="missing X-Eco-Scilog header")
        origin = self.request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != self.request.host:
            raise tornado.web.HTTPError(403, reason="foreign origin")

    async def run(self, fn, *args):
        try:
            self.finish(await self.blocking(fn, *args))
        except content.Unresolved as exc:
            self.set_status(400)
            self.finish({"error": f"not saved: {exc}"})
        except PermissionError as exc:
            self.set_status(403)
            self.finish({"error": str(exc)})
        except (KeyError, ValueError, RuntimeError, logs_scilog.TokenError) as exc:
            self.set_status(409 if "changed meanwhile" in str(exc) else 400)
            self.finish({"error": str(exc).strip("'")})

    def body(self):
        try:
            return json.loads(self.request.body or b"{}")
        except ValueError:
            raise tornado.web.HTTPError(400, reason="not JSON")


class _Edit(_Base):
    async def get(self, post_id):
        logbook = self.get_query_argument("logbook")
        try:
            self.finish(await self.blocking(self.app.store.for_editor, logbook, post_id))
        except PermissionError as exc:
            self.set_status(403)
            self.finish({"error": str(exc)})
        except KeyError as exc:
            self.set_status(404)
            self.finish({"error": str(exc).strip("'")})


class _Upload(_Write):
    async def post(self):
        files = self.request.files.get("file") or []
        if not files:
            raise tornado.web.HTTPError(400, reason="no file")
        f = files[0]
        await self.run(self.app.store.upload, self.get_query_argument("logbook"), self.get_query_argument("post", None),
                       f["filename"] or "file", f["body"], f.get("content_type"))


class _Save(_Write):
    async def post(self):
        b = self.body()
        await self.run(self.app.store.save, b.get("logbook"), None, b.get("html", ""), b.get("tags"),
                       None, b.get("edit_days"), b.get("after"), b.get("before"), b.get("comment_on"), b.get("reply_to"))

    async def patch(self, post_id):
        b = self.body()
        await self.run(self.app.store.save, b.get("logbook"), post_id, b.get("html", ""), b.get("tags"), b.get("updated"))


class _Tasks(_Base):
    async def get(self):
        try:
            self.finish(await self.blocking(self.app.store.tasks, self.get_query_argument("logbook")))
        except ValueError as exc:
            self.set_status(400)
            self.finish({"error": str(exc)})


class _TaskWrite(_Write):
    async def post(self, task_id=None, action=None):
        b = self.body()
        store, lb = self.app.store, b.get("logbook")
        if task_id is None:
            await self.run(store.add_task, lb, b.get("text", ""), bool(b.get("first")))
        elif action == "delete":
            await self.run(store.delete_task, lb, task_id)
        elif action == "move":
            await self.run(store.move_task, lb, task_id, b.get("before"), b.get("after"))
        else:
            await self.run(store.update_task, lb, task_id, b.get("text"), b.get("done"))


class _Heading(_Write):
    async def post(self, post_id):
        b = self.body()
        await self.run(self.app.store.set_heading, b.get("logbook"), post_id, int(b.get("index", -1)), b.get("level", ""))


class _Delete(_Write):
    async def post(self, post_id):
        await self.run(self.app.store.delete, self.body().get("logbook"), post_id)


class _Share(_Write):
    async def post(self, post_id):
        b = self.body()
        await self.run(self.app.store.share, b.get("logbook"), post_id, bool(b.get("on", True)))


class _Views(_Base):
    async def get(self):
        try:
            self.finish(await self.blocking(self.app.store.views, self.get_query_argument("logbook")))
        except (ValueError, PermissionError, RuntimeError, logs_scilog.TokenError) as exc:
            self.set_status(400)
            self.finish({"error": str(exc)})


class _View(_Write):
    async def post(self, view_id, action=None):
        b = self.body()
        if action == "delete":
            await self.run(self.app.store.remove_view, b.get("logbook"), view_id, b.get("version"))
        else:
            await self.run(self.app.store.change_view, b.get("logbook"), view_id, b.get("version"), b.get("changes"))


class _Token(_Base):
    async def get(self):
        self.finish(await self.blocking(self.app.store.token_status))


class _TokenSet(_Write):
    """The token comes from the page: the same checks as for writes."""

    async def post(self):
        b = self.body()
        await self.run(self.app.store.set_token, str(b.get("token") or ""), bool(b.get("keep", True)))


class _Access(_Write):
    """Read SciLog as the beamline account or with the stored personal token."""

    async def post(self):
        await self.run(self.app.store.choose_access, str(self.body().get("kind") or ""))


class _WriteLogbook(_Write):
    async def post(self):
        b = self.body()
        await self.run(self.app.store.set_write_logbook, b.get("logbook"), bool(b.get("on", True)))


class _Logbooks(_Base):
    async def get(self):
        self.finish({"logbooks": await self.blocking(self.app.store.list_logbooks)})


class _Posts(_Base):
    async def get(self):
        logbook = self.get_query_argument("logbook")
        if not _ID.match(logbook):
            raise tornado.web.HTTPError(400, reason="not a logbook id")
        since = self.get_query_argument("since", None)
        self.set_header("Cache-Control", "no-store")
        self.finish(await self.blocking(self.app.store.posts, logbook, since))


class _Image(_Base):
    async def get(self, file_id):
        data, ctype, _name = await self.blocking(self.app.store.image, file_id)
        if not ctype.startswith("image/"):
            raise tornado.web.HTTPError(415)
        self.set_header("Content-Type", ctype)
        self.set_header("Cache-Control", "private, max-age=86400")
        self.finish(data)


class _File(_Base):
    async def get(self, file_id):
        data, ctype, name = await self.blocking(self.app.store.file, file_id)
        self.set_header("Cache-Control", "private, max-age=86400")
        if self.get_query_argument("download", None) or "html" not in ctype:
            self.set_header("Content-Type", ctype if not ctype.startswith("text/html") else "application/octet-stream")
            self.set_header("Content-Disposition", f'attachment; filename="{(name or file_id).replace(chr(34), "")}"')
        else:
            # a plot: scripts run, but in an origin of its own (no cookie, no page)
            self.set_header("Content-Type", "text/html; charset=utf-8")
            self.set_header("Content-Security-Policy", "sandbox allow-scripts")
        self.finish(data)


class ScilogApp:
    """The server, on its own thread and event loop."""

    def __init__(self, port=DEFAULT_PORT, host="127.0.0.1", store=None, token=None):
        self.port, self.host = port, host
        self.token = token or secrets.token_urlsafe(24)
        self.store = store or Store()
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="eco-scilog")
        self._thread = self._loop = self._server = self._error = None
        self._ready = threading.Event()

    def make_app(self):
        return tornado.web.Application(
            [
                (r"/", _Index),
                (r"/static/(.*)", _Static, {"path": STATIC_DIR}),
                (r"/api/me", _Me),
                (r"/api/token", _Token),
                (r"/api/token/set", _TokenSet),
                (r"/api/access", _Access),
                (r"/api/logbooks", _Logbooks),
                (r"/api/posts", _Posts),
                (r"/api/edit/([0-9a-f]{24})", _Edit),
                (r"/api/upload", _Upload),
                (r"/api/save", _Save),
                (r"/api/save/([0-9a-f]{24})", _Save),
                (r"/api/heading/([0-9a-f]{24})", _Heading),
                (r"/api/share/([0-9a-f]{24})", _Share),
                (r"/api/delete/([0-9a-f]{24})", _Delete),
                (r"/api/tasks", _Tasks),
                (r"/api/task", _TaskWrite),
                (r"/api/task/([0-9a-f]{24})", _TaskWrite),
                (r"/api/task/([0-9a-f]{24})/(delete|move)", _TaskWrite),
                (r"/api/write-logbook", _WriteLogbook),
                (r"/api/views", _Views),
                (r"/api/view/([0-9a-f]{24})", _View),
                (r"/api/view/([0-9a-f]{24})/(delete)", _View),
                (r"/img/([0-9a-f]{24})", _Image),
                (r"/file/([0-9a-f]{24})", _File),
            ],
            scilog_app=self,
            compress_response=True,
        )

    @property
    def url(self):
        return f"http://localhost:{self.port}/?token={self.token}"

    def start(self, timeout=10.0):
        """Serve on a background thread; returns once listening."""
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._serve, name="eco-scilog", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("eco scilog server did not start")
        if self._error is not None:
            self._thread = None
            raise self._error
        return self

    def _serve(self):
        asyncio.set_event_loop(asyncio.new_event_loop())
        self._loop = IOLoop.current()
        try:
            self._server = self.make_app().listen(self.port, address=self.host, max_body_size=MAX_UPLOAD + 2**20)
            if self.port == 0:  # tests: an ephemeral port
                self.port = next(iter(self._server._sockets.values())).getsockname()[1]
        except Exception as exc:
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        self._loop.start()

    def stop(self):
        loop = self._loop
        if loop is None:
            return
        loop.add_callback(lambda: (self._server.stop(), loop.stop()))
        if self._thread is not None:
            self._thread.join(5)
        self._thread = self._loop = None
        self.pool.shutdown(wait=False, cancel_futures=True)
