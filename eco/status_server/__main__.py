import argparse
import logging


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

    if args.mode == "registry":
        from .config import ServerConfig
        from .server import create_app

        config = ServerConfig.from_file(args.config)
        if args.port is not None:
            config.port = args.port
        app = create_app(config)
    else:
        from .config import NamespaceServerConfig
        from .namespace_server import create_namespace_app

        config = NamespaceServerConfig.from_file(args.config)
        if args.port is not None:
            config.port = args.port
        # This call blocks until namespace.init_all() finishes - see
        # DESIGN.md section 12.3: can be anywhere from ~1.5 minutes
        # (init_all_parallel-style, dependency-aware, shared CA context)
        # to much longer for a naive serial init over many components.
        app = create_namespace_app(config)

    # Single-process Flask dev server - fine for internal traffic at this
    # scale; for a real systemd deployment, run behind a production WSGI
    # server instead (e.g. `waitress-serve` or `gunicorn`), see systemd/
    # in this directory.
    app.run(host=config.host, port=config.port, threaded=True)


if __name__ == "__main__":
    main()
