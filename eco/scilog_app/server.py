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
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
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

    def __init__(self, reader=None, cache_dir=None):
        self._reader = reader
        self.logbooks = {}  # id -> {"posts": {id: record}, "since": iso}
        self._locks = {}
        self._lock = threading.Lock()
        self.cache_dir = Path(cache_dir or logs_ask.index_dir() / "scilog_files")

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

    @staticmethod
    def record(raw):
        """What the page gets of one post."""
        text = logs_sources.html_to_text(raw.get("textcontent") or "")
        created, updated = raw.get("createdAt") or "", raw.get("updatedAt") or ""
        return {
            "id": raw["id"],
            "t": _epoch(created),
            "created": created,
            "updated": updated,
            "edited": bool(updated and created and _epoch(updated) - _epoch(created) > 60),
            "author": raw.get("createdBy") or "",
            "editor": raw.get("updatedBy") or "",
            "tags": list(raw.get("tags") or ()),
            "html": content.clean_post(raw.get("textcontent") or "", raw.get("files")),
            "text": text,
            "title": _first_line(text),
        }

    def posts(self, logbook_id, since=None):
        """{"posts": [...], "removed": [ids], "since": iso}: everything the
        first time (since=None), afterwards what changed after `since`."""
        with self._lock:
            lock = self._locks.setdefault(logbook_id, threading.Lock())
        with lock:
            book = self.logbooks.get(logbook_id)
            if book is None:
                raw = self.reader.posts(logbook_id)
                book = {"posts": {}, "since": ""}
                changed = raw
                self.logbooks[logbook_id] = book
            else:
                changed = self.reader.posts(logbook_id, updated_after=book["since"]) if book["since"] else []
            removed = []
            for r in changed:
                book["since"] = max(book["since"], r.get("updatedAt") or "", r.get("createdAt") or "")
                if r.get("deleted"):
                    book["posts"].pop(r["id"], None)
                    removed.append(r["id"])
                else:
                    book["posts"][r["id"]] = self.record(r)
            if since is None:
                posts = sorted(book["posts"].values(), key=lambda p: p["t"])
            else:
                ids = {r["id"] for r in changed if not r.get("deleted")}
                posts = sorted((book["posts"][i] for i in ids), key=lambda p: p["t"])
            return {"posts": posts, "removed": removed, "since": book["since"]}

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
        reader = await self.blocking(lambda: self.app.store.reader)
        self.finish({"identity": reader.identity})


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
            self._server = self.make_app().listen(self.port, address=self.host)
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
