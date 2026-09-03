"""PC-side entry point: run the eco session behind a remote control server.

Examples:
    # serve the offline fake beamline over TCP (local testing, two processes)
    python -m eco.manual_control.remote.serve --fake --tcp 8765

    # serve the real bernina namespace over a Bluetooth RFCOMM serial device
    python -m eco.manual_control.remote.serve --bernina --serial /dev/rfcomm0

    # serve over a USB-gadget serial device (Pi Zero/4/5; PC side often ttyACM0)
    python -m eco.manual_control.remote.serve --fake --serial /dev/ttyACM0

TCP is for local development only (no WiFi/IP in production); the real link
is --serial (Bluetooth RFCOMM or USB-gadget serial).
"""

import argparse
import os
import threading

from .server import RemoteControlServer
from .transport import SerialLineTransport, accept_tcp, listen_tcp

DEFAULT_PORT = 8791
DEFAULT_TOKEN_FILE = "~/.eco/pendant_token"


def read_token(token=None, token_file=DEFAULT_TOKEN_FILE):
    """Explicit token wins; otherwise the first line of token_file if it exists."""
    if token:
        return token
    if token_file:
        path = os.path.expanduser(token_file)
        if os.path.exists(path):
            with open(path) as fh:
                return fh.read().strip()
    return None


class BoxServer:
    """A listening manual-control server running in a background thread.

    Started from inside a live eco session, so the control box drives the
    very same Adjustables/Assemblies as the shell (a separate `serve`
    process would be a second eco instance with its own objects and its own
    cold import). Accepts one box at a time and keeps listening, so the box
    can reboot or reconnect freely.

        from eco.manual_control import start_box_server
        box = start_box_server(bernina.namespace)   # -> prints where it listens
        box.stop()
    """

    def __init__(self, root, listener, root_name=None, token=None, **box_kwargs):
        self.root = root
        self.root_name = root_name
        self.listener = listener
        self.token = token
        self.box_kwargs = box_kwargs
        self.server = None
        self.host, self.port = listener.getsockname()[:2]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                transport = accept_tcp(self.listener)
            except OSError:
                break
            if self._stop.is_set():
                break
            print(f"manual-control box connected on {self.host}:{self.port}")
            self.server = RemoteControlServer(
                self.root, transport, root_name=self.root_name,
                token=self.token, **self.box_kwargs
            )
            self.server.start()
            self.server.wait()
            print("manual-control box disconnected")

    @property
    def connected(self):
        return self.server is not None and not self.server._stop.is_set()

    def stop(self):
        self._stop.set()
        if self.server is not None:
            self.server.stop()
        try:
            self.listener.close()
        except OSError:
            pass

    def __repr__(self):
        return (f"<BoxServer {self.host}:{self.port} root={self.root_name!r} "
                f"connected={self.connected}>")


def start_box_server(root, root_name=None, port=DEFAULT_PORT, bind="0.0.0.0",
                     token=None, token_file=DEFAULT_TOKEN_FILE, **box_kwargs):
    """Serve `root` (e.g. bernina.namespace) to the control box, in the background."""
    token = read_token(token, token_file)
    if bind not in ("127.0.0.1", "localhost") and not token:
        raise ValueError(
            f"a token is required to listen on {bind} (this port can drive motors): "
            f"put the box's token in {token_file} or pass token=..."
        )
    if root_name is None:
        root_name = getattr(root, "name", None) or getattr(root, "alias", None) or "eco"
    try:
        listener = listen_tcp(bind, port)
    except OSError as exc:
        raise OSError(
            f"cannot listen on {bind}:{port} ({exc}). Another eco session is "
            f"probably already serving the control box - only one can. Use that "
            f"session, stop its server (box.stop()), or pass port=<other>."
        ) from exc
    server = BoxServer(root, listener, root_name=str(root_name),
                       token=token, **box_kwargs)
    print(f"manual-control server listening on {bind}:{port} "
          f"({'token required' if token else 'NO token'}), serving '{root_name}'")
    return server


def build_root(args):
    if args.bernina:
        import eco.bernina as bernina

        return bernina.namespace, "bernina"
    return _fake()


def _fake():
    from ..demo import build_fake_beamline

    return build_fake_beamline(), "beamline"


def main():
    ap = argparse.ArgumentParser(description="eco manual-control remote server (PC side)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--bernina", action="store_true", help="serve the real bernina namespace")
    src.add_argument("--fake", action="store_true", help="serve the offline fake beamline (default)")
    link = ap.add_mutually_exclusive_group(required=True)
    link.add_argument("--tcp", type=int, metavar="PORT", help="listen on PORT for a networked box")
    link.add_argument("--serial", metavar="DEV", help="serial device, e.g. /dev/rfcomm0 or /dev/ttyACM0")
    ap.add_argument("--bind", default="127.0.0.1", metavar="HOST",
                    help="address to listen on with --tcp (default 127.0.0.1; use 0.0.0.0 for a networked box)")
    ap.add_argument("--token", metavar="STR", help="shared secret the box must send; required for a non-loopback --bind")
    ap.add_argument("--token-file", metavar="PATH", help="read the shared secret from PATH (first line)")
    args = ap.parse_args()

    token = args.token
    if args.token_file:
        with open(os.path.expanduser(args.token_file)) as fh:
            token = fh.read().strip()
    if args.tcp and args.bind not in ("127.0.0.1", "localhost") and not token:
        ap.error("--bind on a network address requires --token or --token-file "
                 "(this port can drive motors)")

    root, name = build_root(args)

    if args.serial:
        print(f"serving '{name}' over {args.serial} ...")
        server = RemoteControlServer(root, SerialLineTransport(args.serial), root_name=name, token=token).start()
        server.wait()
        return

    listener = listen_tcp(args.bind, args.tcp)
    print(f"serving '{name}' on {args.bind}:{args.tcp}"
          f"{' (token required)' if token else ''} (Ctrl-C to stop) ...")
    while True:
        tr = accept_tcp(listener)
        print("client connected")
        server = RemoteControlServer(root, tr, root_name=name, token=token).start()
        server.wait()
        print("client disconnected")


if __name__ == "__main__":
    main()
