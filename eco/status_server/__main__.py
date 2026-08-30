import argparse
import faulthandler
import logging
import signal
import time

logger = logging.getLogger(__name__)


def _make_server(app, host, port, attempts=15, delay=1.0):
    """Bind the WSGI server, retrying a briefly-occupied port.

    Uses werkzeug's make_server rather than Flask's app.run(): app.run() goes
    through run_simple(), which does `srv.socket.set_inheritable(True)` so the
    dev reloader can hand the socket to a child. That flag makes the listening
    socket survive os.execv() - so /admin/restart's re-exec came back to
    "Address already in use", the freshly exec'd process gave up, and the
    server was simply gone. make_server leaves the socket non-inheritable
    (and /admin/restart closes it explicitly as well). The retry loop covers
    the remaining case of a still-draining socket from a previous instance.
    """
    from werkzeug.serving import make_server

    last = None
    for attempt in range(attempts):
        try:
            return make_server(host, port, app, threaded=True)
        except OSError as exc:
            last = exc
            logger.warning(
                "could not bind %s:%s (%s); retry %d/%d",
                host, port, exc, attempt + 1, attempts,
            )
            time.sleep(delay)
    raise last


def main():
    parser = argparse.ArgumentParser(description="eco namespace status/monitor server")
    parser.add_argument(
        "--mode",
        choices=["registry", "namespace"],
        default="registry",
        help=(
            "'registry' (default): bare alias/PV list, no eco import - "
            "see channel_registry.py/monitor_store.py/server.py. "
            "'namespace': hosts the live eco namespace (e.g. bernina) - "
            "matches namespace.get_status() 1:1 but is slower to start and "
            "imports the full eco stack - see namespace_store.py/"
            "namespace_server.py and DESIGN.md section 12 before using this."
        ),
    )
    parser.add_argument("--config", required=True, help="path to a config JSON file")
    parser.add_argument("--port", type=int, default=None, help="override config port")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level)

    # `kill -USR1 <pid>` dumps every thread's stack to stderr (i.e. into the
    # service log). This server spends minutes inside device __init__ code
    # that talks to hardware over CA/SSH/HTTP, and "which component is it
    # stuck in?" is otherwise unanswerable on a machine without py-spy.
    # chain=False is not a detail: SIGUSR1's default disposition is Term, so
    # chaining to the previous handler dumps the stacks and then kills the
    # server. Verified the hard way - one diagnostic dump took the process
    # down mid-initialization.
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)

    if args.mode == "registry":
        from .config import ServerConfig
        from .server import create_app

        config = ServerConfig.from_file(args.config)
        if args.port is not None:
            config.port = args.port
        app = create_app(config)
        server = _make_server(app, config.host, config.port)
    else:
        from .config import NamespaceServerConfig
        from .namespace_server import create_namespace_app

        config = NamespaceServerConfig.from_file(args.config)
        if args.port is not None:
            config.port = args.port
        # Build the app but do NOT start the namespace init yet: binding the
        # port first means a port clash costs a failed bind, not a wasted
        # multi-minute init_all() that nobody can talk to.
        app = create_namespace_app(config, start_store=False)
        server = _make_server(app, config.host, config.port)
        app.config["ECO_WSGI_SERVER"] = server
        # Returns immediately: namespace.init_all() (minutes) runs on a
        # background thread, so /health can report state="initializing" plus
        # live progress from the first request instead of the port simply
        # refusing connections until it is done.
        app.config["ECO_STORE"].start()

    logger.info("serving on %s:%s", config.host, config.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
