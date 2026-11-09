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
import json
import logging
import mimetypes
import os
import re
import secrets
import threading
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
        self._lock = threading.Lock()
        self.cache_dir = Path(cache_dir or logs_ask.index_dir() / "scilog_files")
        self.config = config if config is not None else load_config()
        #: where set_write_logbook() saves (none for a store given its config, e.g. in tests)
        self.config_path = CONFIG_PATH if config is None else None
        self.uploads = {}  # fileId -> file container, uploaded while editing
        self._me = None

    @property
    def reader(self):
        if self._reader is None:
            self._reader = logs_scilog.reader(timeout=60)
        return self._reader

    def list_logbooks(self):
        rows = self.reader.logbooks()
        for lb in rows:
            lb["url"] = f"{SCILOG_WEB}/logbooks/{lb['id']}/dashboard"
        return rows

    # -- who is reading, and may they write --------------------------------------

    def me(self):
        """{"identity", "email", "roles", "writes", "why_not", "write_logbooks"}."""
        if self._me is None:
            reader = self.reader
            claims = logs_scilog.claims(reader.token)
            writes, why = True, ""
            if reader.identity.startswith("the beamline account"):
                writes, why = False, "SciLog is read as the beamline account here; writing needs your personal token"
            elif logs_scilog._shared_account():
                writes, why = False, "writing is off on a shared account"
            self._me = {"identity": reader.identity, "email": claims.get("email", ""), "roles": set(claims.get("roles") or ()),
                        "writes": writes, "why_not": why, "write_logbooks": self.config.get("write_logbooks")}
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
        return {"id": raw["id"], "t": _epoch(raw.get("createdAt") or ""), "author": raw.get("createdBy") or "",
                "html": content.clean_post(raw.get("textcontent") or "", raw.get("files")), "text": text}

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

    def _check_write(self, logbook_id, raw=None):
        ok, why = self.may_write_to(logbook_id)
        if not ok:
            raise PermissionError(why)
        if raw is not None and not self.may_edit(raw):
            raise PermissionError("SciLog lets you edit only your own posts (or those of a group you are in)")

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
        """The post as the editor opens it."""
        raw = self._raw(logbook_id, post_id)
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

    def _insert_order(self, logbook_id, after_id):
        """A defaultOrder between `after_id` and the post that follows it
        in SciLog's order: a post placed there after the fact."""
        book = self.logbooks.get(logbook_id) or {}
        orders = sorted(r.get("defaultOrder") for r in (book.get("raw") or {}).values()
                        if isinstance(r.get("defaultOrder"), (int, float)))
        after = (book.get("raw") or {}).get(after_id)
        if after is None or not isinstance(after.get("defaultOrder"), (int, float)):
            raise ValueError("the post to insert after is not known")
        a = after["defaultOrder"]
        later = [o for o in orders if o > a]
        if not later:
            return None  # the last post: a new post goes after it anyway
        b = later[0]
        if b - a < 2:
            raise ValueError("there is no room between these two posts")
        return int(a + (b - a) // 2)

    def save(self, logbook_id, post_id, html, tags, expected_updated=None, edit_days=None, after=None):
        """Create (post_id None) or update a post from the editor's HTML;
        returns the post as the page shows it. With expected_updated, an
        update is refused if the post was changed meanwhile. A new post can
        be given an edit period (days; SciLog's own default is 3) and be
        placed right after another post (`after`, a post id)."""
        raw = self._raw(logbook_id, post_id) if post_id else None
        self._check_write(logbook_id, raw)
        textcontent, files = content.to_scilog(html, self._known_files(raw))
        if not textcontent.strip():
            raise ValueError("the post is empty")
        tags = [t.strip() for t in tags or () if t and t.strip()]
        if raw is None:
            payload = {"snippetType": "paragraph", "linkType": "paragraph", "parentId": logbook_id,
                       "textcontent": textcontent, "files": files, "tags": tags}
            if edit_days is not None:
                days = float(edit_days)
                if not 0 < days <= self.MAX_EDIT_DAYS:
                    raise ValueError(f"the edit period must be between 1 and {self.MAX_EDIT_DAYS} days")
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
                payload["expiresAt"] = until.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if after:
                if not _ID.match(str(after)):
                    raise ValueError("not a post id")
                order = self._insert_order(logbook_id, after)
                if order is not None:
                    payload["defaultOrder"] = order
            new = self.reader.create_post(payload)
            post_id = new["id"]
        else:
            current = self.reader.post(post_id)
            if expected_updated and current.get("updatedAt") and current["updatedAt"] != expected_updated:
                raise RuntimeError(f"the post was changed meanwhile (by {current.get('updatedBy')} at "
                                   f"{current.get('updatedAt')}); reload it before saving")
            self.reader.update_post(post_id, {"textcontent": textcontent, "files": files, "tags": tags})
        for f in files:
            self.uploads.pop(f["fileId"], None)
        return self._reread(logbook_id, post_id)

    def _reread(self, logbook_id, post_id):
        """The post as the page shows it, after a write: what changed since
        the last look is read (the whole logbook if it was not read yet)."""
        book = self.logbooks.get(logbook_id)
        self.posts(logbook_id, book["since"] if book else None)
        return self.logbooks[logbook_id]["posts"][post_id]

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

    def prepare(self):
        if not self.authorized():
            raise tornado.web.HTTPError(403)

    def set_default_headers(self):
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("Referrer-Policy", "no-referrer")

    async def blocking(self, fn, *args):
        return await IOLoop.current().run_in_executor(self.app.pool, fn, *args)

    def write_error(self, status_code, **kwargs):
        exc = kwargs.get("exc_info", (None, None))[1]
        self.finish({"error": f"{type(exc).__name__}: {exc}" if exc and status_code >= 500 else self._reason})


class _Index(_Base):
    def prepare(self):
        pass  # checks the token itself, to set the cookie

    def get(self):
        token = self.get_query_argument("token", None)
        if token is not None:
            if not hmac.compare_digest(token, self.app.token):
                raise tornado.web.HTTPError(403)
            self.set_cookie(COOKIE, token, httponly=True, samesite="Strict", expires_days=30)
            self.redirect("/" + (f"#{self.get_query_argument('go')}" if self.get_query_argument("go", None) else ""))
            return
        if not self.authorized():
            self.set_status(403)
            self.finish("eco scilog: open the URL with ?token=... that eco.start_scilog() printed")
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
                       None, b.get("edit_days"), b.get("after"))

    async def patch(self, post_id):
        b = self.body()
        await self.run(self.app.store.save, b.get("logbook"), post_id, b.get("html", ""), b.get("tags"), b.get("updated"))


class _Heading(_Write):
    async def post(self, post_id):
        b = self.body()
        await self.run(self.app.store.set_heading, b.get("logbook"), post_id, int(b.get("index", -1)), b.get("level", ""))


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
                (r"/api/logbooks", _Logbooks),
                (r"/api/posts", _Posts),
                (r"/api/edit/([0-9a-f]{24})", _Edit),
                (r"/api/upload", _Upload),
                (r"/api/save", _Save),
                (r"/api/save/([0-9a-f]{24})", _Save),
                (r"/api/heading/([0-9a-f]{24})", _Heading),
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
