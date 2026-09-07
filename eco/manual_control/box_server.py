"""A small background service that holds this console's connection to the
physical manual-control box, so driving it does not require an interactive
eco session left open.

Mirrors eco.status_server's shape (a Flask app with /health plus /admin/*
actions, bound immediately, run with ``make_server`` so a restart can hand
the port back cleanly) but is much lighter: there is no namespace init to
run in the background - the box only touches components it is actually
walked into (see eco.manual_control.control_box's manual), so importing the
scope and connecting to the box are both fast.

Run with:

    python -m eco.manual_control.box_server --scope bernina

or via the wrapper script (``eco-box-server``, mirroring
``eco-status-server``), or from a checkout with ``eco-dev box-server -s
bernina`` for quick local testing against uncommitted device changes.

Endpoints:
    GET  /health           state, whether the box is connected, why not
    POST /admin/disconnect release the box now (it falls back to its
                           waiting screen) - the safety button
    POST /admin/reconnect  offer this session to the box again - the
                           operator at the box has to accept it, same as
                           any other session (no special privilege here)
    POST /admin/restart    re-exec the whole process, so a device module
                           edited since this process started is picked up
                           (Python does not re-read a module it already
                           imported) - same trade-off/mechanism as
                           eco.status_server's /admin/restart
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import signal
import sys
import time

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_HOST = "0.0.0.0"
DEFAULT_ADMIN_PORT = 8092


def _process_stats():
    """Cheap self-measurements for /health: CPU seconds, resident memory and
    thread count, straight from /proc. Mirrors
    eco.status_server.namespace_server._process_stats (kept as its own small
    copy rather than imported - box_server has no other reason to import
    status_server, and this is 15 lines of stdlib, not shared state).
    Silently empty where /proc is not Linux's.
    """
    try:
        with open("/proc/self/stat") as f:
            fields = f.read().rsplit(") ", 1)[1].split()
        ticks = os.sysconf("SC_CLK_TCK")
        utime, stime = int(fields[11]) / ticks, int(fields[12]) / ticks
        num_threads = int(fields[17])
        with open("/proc/self/statm") as f:
            rss_pages = int(f.read().split()[1])
        return {
            "cpu_seconds": utime + stime,
            "rss_mb": rss_pages * os.sysconf("SC_PAGE_SIZE") / 1e6,
            "n_threads": num_threads,
        }
    except Exception:
        return {}


def _restart_argv():
    """The argv to re-exec this process with - see
    eco.status_server.namespace_server._restart_argv for why the ``-m``
    form has to be reconstructed rather than just re-using sys.argv[0]."""
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if spec is not None and getattr(spec, "name", None):
        module = spec.parent or spec.name
        return [sys.executable, "-m", module] + sys.argv[1:]
    return [sys.executable] + sys.argv


def _make_server(app, host, port, attempts=15, delay=1.0):
    """Bind the WSGI server, retrying a briefly-occupied port - identical
    reasoning to eco.status_server.__main__._make_server (a restart's
    re-exec can race the old process's socket finishing its close)."""
    from werkzeug.serving import make_server

    last = None
    for attempt in range(attempts):
        try:
            return make_server(host, port, app, threaded=True)
        except OSError as exc:
            last = exc
            logger.warning("could not bind %s:%s (%s); retry %d/%d",
                           host, port, exc, attempt + 1, attempts)
            time.sleep(delay)
    raise last


class BoxServerState:
    """Tracks the connection across sessions, since
    Namespace.stop_eco_control_box() clears its own bookkeeping (by design -
    a fresh interactive .start() should not see a stale session) but /health
    should still be able to say why the last connection ended."""

    def __init__(self, namespace, box_host, box_port, token, token_file):
        self.namespace = namespace
        self.box_host = box_host
        self.box_port = box_port
        self.token = token
        self.token_file = token_file
        self.state = "idle"
        self.reason = None

    def _session(self):
        return getattr(self.namespace, "_control_box_server", None)

    def _remember(self):
        session = self._session()
        if session is not None:
            self.state = session.state
            self.reason = session.reason

    def connect(self):
        self.namespace.start_eco_control_box(
            box_host=self.box_host, port=self.box_port,
            token=self.token, token_file=self.token_file,
        )
        self._remember()

    def disconnect(self):
        self._remember()  # capture state before stop() clears it
        self.namespace.stop_eco_control_box()
        session = self._session()
        if session is None:  # the common case: stop() clears it
            self.state, self.reason = "idle", "disconnected (admin request)"

    @property
    def connected(self):
        session = self._session()
        return bool(session is not None and session.connected)

    def body(self):
        self._remember()
        return {
            "state": self.state,
            "connected": self.connected,
            "reason": self.reason,
            "box": f"{self.box_host}:{self.box_port}",
        }


def create_box_app(namespace, box_state, start_time=None, wsgi_server_holder=None):
    """Build the Flask app. Split out from main() so tests can drive it with
    Flask's test client, no HTTP socket involved - same shape as
    eco.status_server.namespace_server.create_namespace_app.

    wsgi_server_holder: a single-item dict {'server': ...} filled in by the
    caller once the WSGI server exists, so /admin/restart can close the
    listening socket before re-exec'ing (see _make_server's docstring on
    why: werkzeug marks it inheritable, so a re-exec without this closes
    over a "port already in use" error against itself).
    """
    from flask import Flask, jsonify, request

    start_time = start_time if start_time is not None else time.time()
    wsgi_server_holder = wsgi_server_holder if wsgi_server_holder is not None else {}

    app = Flask("eco-box-server")

    def _health_body():
        return {
            "status": "ok",
            "namespace": getattr(namespace, "name", None),
            "pid": os.getpid(),
            "uptime_s": time.time() - start_time,
            **_process_stats(),
            **box_state.body(),
        }

    @app.get("/health")
    def health():
        return jsonify(_health_body())

    @app.post("/admin/disconnect")
    def admin_disconnect():
        box_state.disconnect()
        return jsonify({**_health_body(), "message": "disconnected from the box"})

    @app.post("/admin/reconnect")
    def admin_reconnect():
        box_state.connect()
        return jsonify({**_health_body(),
                        "message": "offered this session to the box; "
                                   "accept it there"})

    @app.post("/admin/restart")
    def admin_restart():
        """Re-exec the whole process - see the module docstring."""
        import threading

        delay = float((request.get_json(force=True, silent=True) or {}).get("delay", 0.5))

        def _restart():
            time.sleep(delay)  # let this response flush first
            box_state.disconnect()  # leave the box in a clean waiting state
            server = wsgi_server_holder.get("server")
            if server is not None:
                try:
                    server.server_close()
                except Exception:
                    logger.warning("could not close the listening socket", exc_info=True)
            argv = _restart_argv()
            logger.warning("restarting process on /admin/restart request: %s", argv)
            os.execv(sys.executable, argv)

        threading.Thread(target=_restart, name="restart", daemon=True).start()
        return jsonify({**_health_body(), "message": "restarting"}), 202

    return app


def _handle_sigterm(signum, frame):
    # Default disposition for SIGTERM is immediate termination with no
    # Python-level cleanup; raising here turns it into an ordinary Python
    # exception (PEP 475: a syscall interrupted by a handler that raises
    # propagates the exception instead of being silently retried), so
    # serve_forever() unwinds through the same try/finally as SIGINT.
    raise SystemExit(0)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hold this console's connection to the physical "
                    "manual-control box as a background service.")
    parser.add_argument(
        "-s", "--scope", required=True,
        help="scope/instrument to serve, e.g. bernina (imports eco.<scope>.namespace)",
    )
    parser.add_argument("--box-host", default="ecobox", help="the box to call (default: %(default)s)")
    parser.add_argument("--box-port", type=int, default=8791)
    parser.add_argument("--token", default=None, help="shared secret (default: read --token-file)")
    parser.add_argument("--token-file", default="~/.eco/pendant_token")
    parser.add_argument("--host", default=DEFAULT_ADMIN_HOST,
                        help="admin/health HTTP host (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_ADMIN_PORT,
                        help="admin/health HTTP port (default: %(default)s)")
    parser.add_argument("--no-connect", action="store_true",
                        help="start the admin/health API without offering a "
                             "session to the box (connect later via "
                             "/admin/reconnect or the GUI)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sigterm)

    logger.warning("importing eco.%s ...", args.scope)
    module = importlib.import_module(f"eco.{args.scope}")
    namespace = getattr(module, "namespace")

    box_state = BoxServerState(namespace, args.box_host, args.box_port,
                               args.token, args.token_file)
    wsgi_server_holder = {}
    app = create_box_app(namespace, box_state, wsgi_server_holder=wsgi_server_holder)
    server = _make_server(app, args.host, args.port)
    wsgi_server_holder["server"] = server

    if not args.no_connect:
        box_state.connect()

    logger.warning("eco box-server for '%s' -> box %s:%s, admin/health on %s:%s",
                   args.scope, args.box_host, args.box_port, args.host, args.port)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.warning("shutting down - disconnecting from the box")
        try:
            box_state.disconnect()
        except Exception:
            logger.exception("error while disconnecting on shutdown")


if __name__ == "__main__":
    main()
