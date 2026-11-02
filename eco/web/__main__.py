"""python -m eco.web -s <scope> [--writes] [--port N]    a beamline namespace
python -m eco.web --demo [--writes] [--port N]          the simulated one

Serves a namespace to the web client from this process alone, without an
IPython session (`eco web --no-console`). Normally `eco web` starts an IPython
session with the gateway inside it instead, so the terminal and the browser
share one session.
"""
import argparse
import importlib
import signal
import sys
import time

from eco.web import connection_help
from eco.web.gateway import DEFAULT_PORT


def scope_namespace(scope, lazy=True):
    """`eco.<scope>.namespace`, imported the way `eco console -s <scope>` does."""
    if lazy:
        from eco import ecocnf

        ecocnf.startup_lazy = True
    module = importlib.import_module(f"eco.{scope}")
    namespace = getattr(module, "namespace", None)
    if namespace is None:
        raise SystemExit(f"eco.{scope} has no `namespace` to serve")
    return namespace


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m eco.web", description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-s", "--scope", help="serve eco.<scope>.namespace, e.g. bernina")
    source.add_argument("--demo", action="store_true", help="serve the simulated namespace (no beamline needed)")
    lazy = parser.add_mutually_exclusive_group()
    lazy.add_argument("-l", "--lazy", dest="lazy", action="store_true", default=True,
                      help="lazy initialisation of the scope [default]")
    lazy.add_argument("--no-lazy", dest="lazy", action="store_false", help="initialize everything at startup")
    parser.add_argument("--writes", action="store_true", help="allow writes from the browser")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--token", default=None, help="fixed token; \"random\" for a new one; default: ~/.eco/web_token")
    args = parser.parse_args(argv)

    from eco.web.backends import SessionBackend
    from eco.web.gateway import WebGateway

    if args.demo:
        from eco.web.demo import DemoNamespace

        from eco.web.demo import demo_queues

        namespace = DemoNamespace()
        backend = SessionBackend(namespace, queues=demo_queues(namespace))
    else:
        backend = SessionBackend(scope_namespace(args.scope, args.lazy))
    gateway = WebGateway(backend, host=args.host, port=args.port,
                         token=args.token, writes=args.writes).start()
    print(connection_help(gateway))
    # a terminated server must still stop the jogs its clients hold: SIGTERM
    # (kill), and SIGHUP -- the connection it was started over is gone (an
    # SSH command with a terminal, e.g. started by a phone app on connect)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, lambda *_: sys.exit(0))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        gateway.stop()


if __name__ == "__main__":
    main()
