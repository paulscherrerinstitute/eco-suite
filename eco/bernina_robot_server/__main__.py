"""Entry point: ``python -m eco.bernina_robot_server``.

    python -m eco.bernina_robot_server --simulated          # no hardware
    python -m eco.bernina_robot_server --config robot.json  # deployment
    python -m eco.bernina_robot_server --robot-host 129.129.243.106 --no-epics
"""

from __future__ import annotations

import argparse
import faulthandler
import logging
import signal
import sys
import time

from .app import RobotServerApp
from .config import RobotServerConfig
from .server import create_app

logger = logging.getLogger(__name__)


def _make_server(flask_app, host, port, attempts=15, delay=1.0):
    """Bind, retrying a still-draining port from a previous instance."""
    from werkzeug.serving import make_server

    last = None
    for attempt in range(attempts):
        try:
            return make_server(host, port, flask_app, threaded=True)
        except OSError as exc:
            last = exc
            logger.warning("could not bind %s:%s (%s); retry %d/%d",
                           host, port, exc, attempt + 1, attempts)
            time.sleep(delay)
    raise last


def build_config(args) -> RobotServerConfig:
    config = (RobotServerConfig.from_file(args.config) if args.config
              else RobotServerConfig())
    for name in ("robot_host", "robot_port", "host", "port",
                 "polling_interval", "adjustables_path"):
        value = getattr(args, name, None)
        if value is not None:
            setattr(config, name, value)
    if args.simulated:
        config.simulated = True
    if args.no_epics:
        config.epics_enabled = False
    if args.override_remote_safety:
        config.override_remote_safety = True
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m eco.bernina_robot_server",
        description="Bernina Stäubli TX200 robot server (replaces the pshell "
                    "deployment in /sf/bernina/config/src/python/bernina_robot)",
    )
    parser.add_argument("--config", help="path to a JSON config file")
    parser.add_argument("--robot-host", help="VAL3 controller hostname/IP")
    parser.add_argument("--robot-port", type=int, help="VAL3 controller port")
    parser.add_argument("--host", help="HTTP bind address")
    parser.add_argument("--port", type=int, help="HTTP port (default 8080)")
    parser.add_argument("--polling-interval", type=float,
                        help="seconds between position polls")
    parser.add_argument("--adjustables-path",
                        help="directory holding the persisted frame/tool/"
                             "recorded-motion JSON files")
    parser.add_argument("--simulated", action="store_true",
                        help="run against a fake controller, no hardware")
    parser.add_argument("--no-epics", action="store_true",
                        help="do not start the EPICS CA server")
    parser.add_argument(
        "--override-remote-safety", action="store_true",
        help="START WITH THE RECORDED-TRAJECTORY WHITELIST DISABLED. Every "
             "remote motion is then permitted. Operator decision only.")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-7s %(name)-32s %(message)s",
    )
    # `kill -USR1 <pid>` dumps every thread's stack -- the quickest way to see
    # which thread is stuck on a controller round trip.
    try:
        faulthandler.register(signal.SIGUSR1)
    except (AttributeError, ValueError):
        pass

    config = build_config(args)
    if config.override_remote_safety:
        logger.warning("STARTING WITH THE REMOTE-MOTION SAFETY WHITELIST "
                       "DISABLED (--override-remote-safety)")

    server_app = RobotServerApp(config).start()
    flask_app = create_app(server_app)
    http = _make_server(flask_app, config.host, config.port)

    def shutdown(signum, _frame):
        logger.info("signal %s received, shutting down", signum)
        server_app.stop()
        http.shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)

    logger.info("robot server listening on http://%s:%d (robot %s:%d%s)",
                config.host, config.port, config.robot_host, config.robot_port,
                ", SIMULATED" if config.simulated else "")
    try:
        http.serve_forever()
    finally:
        server_app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
