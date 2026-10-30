"""The HTTP/websocket server behind the eco web client (PWA).

Runs tornado on its own thread with its own event loop: it never needs the
session's main thread (so eco.utilities.mainthread does not concern it) and
never touches hardware on its loop -- ClientSession does all of that in its
pools.

Security model (web_frontend/ARCHITECTURE.md §6/§7): binds 127.0.0.1 by
default, so reaching it takes an SSH login to the host (``ssh -L``); a
per-gateway token is required on top, since every account on a shared
console host can reach 127.0.0.1. The token comes once in the URL
(``/?token=...``) and is then kept in an HttpOnly cookie, so the installed
app reopens without it. Writes are off unless the gateway was started with
them.
"""
import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor

import tornado.web
import tornado.websocket
from tornado.ioloop import IOLoop, PeriodicCallback

from eco.web.backends import NotFound
from eco.web.client_session import FLUSH_INTERVAL, ClientSession

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
COOKIE = "eco_web_token"
DEFAULT_PORT = 8097
_ROOT_FILES = ("sw.js", "manifest.webmanifest", "icon.svg")  # served at / for the PWA scope


class _Base(tornado.web.RequestHandler):
    @property
    def gateway(self):
        return self.application.settings["gateway"]

    def authorized(self):
        given = self.get_query_argument("token", None) or self.get_cookie(COOKIE)
        return bool(given) and hmac.compare_digest(given, self.gateway.token)

    def set_default_headers(self):
        self.set_header("Cache-Control", "no-cache")
        self.set_header("X-Content-Type-Options", "nosniff")


class _Index(_Base):
    def get(self):
        token = self.get_query_argument("token", None)
        if token is not None:
            if not hmac.compare_digest(token, self.gateway.token):
                raise tornado.web.HTTPError(403)
            self.set_cookie(COOKIE, token, httponly=True, samesite="Strict", expires_days=30)
            self.redirect("/")  # keep the token out of history and the installed app's start URL
            return
        if not self.authorized():
            self.set_status(403)
            self.finish("eco web: open the URL with ?token=... printed by eco.web.start_web()")
            return
        with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
            self.set_header("Content-Type", "text/html; charset=utf-8")
            self.finish(f.read())


class _RootFile(_Base):
    def get(self, filename):
        path = os.path.join(STATIC_DIR, filename)
        types = {".js": "text/javascript", ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml"}
        self.set_header("Content-Type", types[os.path.splitext(filename)[1]])
        with open(path, "rb") as f:
            self.finish(f.read())


class _Svg(_Base):
    async def get(self):
        if not self.authorized():
            raise tornado.web.HTTPError(403)
        name = self.get_query_argument("name")
        live = self.get_query_argument("live", "1") != "0"
        try:
            text, prefix = await IOLoop.current().run_in_executor(
                self.gateway.svg_pool, self.gateway.svg_for, name, live
            )
        except NotFound as exc:
            raise tornado.web.HTTPError(404, reason=str(exc)[:200])
        self.set_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.set_header("X-Eco-Prefix", prefix)
        self.finish(text)


class _Socket(tornado.websocket.WebSocketHandler):
    @property
    def gateway(self):
        return self.application.settings["gateway"]

    def open(self):
        given = self.get_query_argument("token", None) or self.get_cookie(COOKIE)
        if not given or not hmac.compare_digest(given, self.gateway.token):
            self.close(4403, "not authorized")
            return
        loop = IOLoop.current()

        def send(message):  # any thread
            try:
                loop.add_callback(self._send, message)
            except RuntimeError:  # loop closed: the gateway is stopping
                pass

        self.session = ClientSession(
            self.gateway.backend, send, writes=self.gateway.writes,
            poll_interval=self.gateway.poll_interval,
        )
        self.gateway.sessions.add(self)

    def _send(self, message):
        if self.ws_connection is None:
            return
        try:
            self.write_message(json.dumps(message, default=str))
        except tornado.websocket.WebSocketClosedError:
            pass

    def on_message(self, raw):
        session = getattr(self, "session", None)
        if session is None:
            return
        try:
            message = json.loads(raw)
        except ValueError:
            message = None
        session.handle(message)

    def flush_values(self):
        session = getattr(self, "session", None)
        if session is not None:
            event = session.flush()
            if event is not None:
                self._send(event)

    def on_close(self):  # also called by WebGateway.stop: idempotent
        self.gateway.sessions.discard(self)
        session = self.__dict__.pop("session", None)
        if session is not None:
            session.close()


TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".eco", "web_token")


def persistent_token(path=TOKEN_FILE):
    """This account's token, kept in `path` (mode 0600) so that a bookmarked
    or installed client keeps working across gateway restarts. Delete the
    file to invalidate every client that has it. Falls back to a fresh token
    if the file cannot be written."""
    try:
        with open(path) as f:
            token = f.read().strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(18)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
    except OSError:
        logger.warning("could not store the web token in %s; it changes with every start", path)
    return token


class WebGateway:
    """`token`: None (default) uses this account's persistent token
    (persistent_token), "random" a fresh one per start, anything else is
    used as given."""

    def __init__(self, backend, host="127.0.0.1", port=DEFAULT_PORT, token=None,
                 writes=False, poll_interval=1.0):
        self.backend = backend
        self.host = host
        self.port = port
        if token is None:
            token = persistent_token()
        elif token == "random":
            token = secrets.token_urlsafe(18)
        self.token = token
        self.writes = writes
        self.poll_interval = poll_interval
        self.sessions = set()
        self.svg_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="eco-web-svg")
        self._svg_objects = {}
        self._loop = None
        self._thread = None
        self._server = None
        self._ready = threading.Event()
        self._error = None

    # -- lifecycle ------------------------------------------------------------------------

    def make_app(self):
        return tornado.web.Application(
            [
                (r"/", _Index),
                (r"/(%s)" % "|".join(re.escape(f) for f in _ROOT_FILES), _RootFile),
                (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": STATIC_DIR}),
                (r"/api/svg", _Svg),
                (r"/ws", _Socket),
            ],
            gateway=self,
            websocket_ping_interval=20,
        )

    def start(self, timeout=10.0):
        """Serve on a background thread; returns once listening."""
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._serve, name="eco-web-gateway", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("eco web gateway did not start")
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
        PeriodicCallback(self._flush_all, FLUSH_INTERVAL * 1000).start()
        self._ready.set()
        self._loop.start()

    def _flush_all(self):
        for handler in list(self.sessions):
            handler.flush_values()

    def stop(self):
        loop = self._loop
        if loop is None:
            return

        async def _shutdown():
            if self._server is not None:
                self._server.stop()
            for handler in list(self.sessions):
                handler.close(1001, "gateway stopped")  # clients see it at once and reconnect
            await asyncio.sleep(0.2)  # let the close frames go out
            for handler in list(self.sessions):
                handler.on_close()
            loop.stop()

        loop.add_callback(_shutdown)
        if self._thread is not None:
            self._thread.join(5)
        try:
            loop.close(all_fds=True)
        except Exception:
            pass
        self._thread = None
        self._loop = None
        self.svg_pool.shutdown(wait=False, cancel_futures=True)

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    # -- helpers ----------------------------------------------------------------------------

    @property
    def url(self):
        return f"http://localhost:{self.port}/?token={self.token}"

    def svg_for(self, name, live):
        """Resolve `name` -- any name a client has seen (top-level or a row)
        -- and build its panel. Row names come from client sessions."""
        obj = None
        for handler in list(self.sessions):
            session = getattr(handler, "session", None)
            if session is not None:
                obj = session._objects.get(name)
                if obj is not None:
                    break
        if obj is None:
            obj = self.backend.resolve(name)
        return self.backend.svg(obj, live=live)
