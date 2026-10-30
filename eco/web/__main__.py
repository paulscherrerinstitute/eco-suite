"""python -m eco.web --demo [--writes] [--port N]

Serves the simulated namespace of eco.web.demo, for developing the web
client anywhere. A real session uses eco.web.start_web() instead.
"""
import argparse
import time

from eco.web import connection_help
from eco.web.gateway import DEFAULT_PORT


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m eco.web", description=__doc__.split("\n\n")[0])
    parser.add_argument("--demo", action="store_true", help="serve the simulated namespace (required for now)")
    parser.add_argument("--writes", action="store_true", help="allow writes from the browser")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--token", default=None, help="fixed token; \"random\" for a new one; default: ~/.eco/web_token")
    args = parser.parse_args(argv)
    if not args.demo:
        parser.error("only --demo is supported from the command line; in a session use eco.web.start_web()")

    from eco.web.backends import SessionBackend
    from eco.web.demo import DemoNamespace
    from eco.web.gateway import WebGateway

    gateway = WebGateway(SessionBackend(DemoNamespace()), host=args.host, port=args.port,
                         token=args.token, writes=args.writes).start()
    print(connection_help(gateway))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        gateway.stop()


if __name__ == "__main__":
    main()
