"""python -m eco.scilog_app [--port N] [--no-browser]: the SciLog viewer's
server, in the foreground until Ctrl-C (or, with --parent, until that
process has ended)."""

import argparse
import os
import time

from eco.scilog_app import DEFAULT_PORT, TOKEN_ENV

parser = argparse.ArgumentParser(prog="python -m eco.scilog_app", description="The SciLog viewer's server.")
parser.add_argument("--port", type=int, default=DEFAULT_PORT)
parser.add_argument("--no-browser", action="store_true", help="only print the address")
parser.add_argument("--parent", type=int, help="end when this process has ended")
parser.add_argument("--quiet", action="store_true", help="no request log")
args = parser.parse_args()

if args.quiet:
    import logging

    logging.getLogger("tornado.access").setLevel(logging.WARNING)

from eco.scilog_app.server import ScilogApp  # noqa: E402

app = ScilogApp(port=args.port, token=os.environ.pop(TOKEN_ENV, None)).start()
print(f"eco scilog: {app.url}", flush=True)
if not args.no_browser:
    from eco.ei_page import can_open_browser

    if can_open_browser():
        import webbrowser

        webbrowser.open(app.url)


def parent_alive():
    if args.parent is None:
        return True
    try:
        os.kill(args.parent, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists, owned by someone else: cannot happen for our parent, keep going
    return True


try:
    while parent_alive():
        time.sleep(2)
except KeyboardInterrupt:
    pass
finally:
    app.stop()
