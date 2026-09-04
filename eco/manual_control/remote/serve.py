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
import subprocess
import threading

from .server import RemoteControlServer
from .transport import SerialLineTransport, accept_tcp, listen_tcp

DEFAULT_PORT = 8791
DEFAULT_TOKEN_FILE = "~/.eco/pendant_token"


def who_has_port(port):
    """[(pid, user, cmdline)] of the processes listening on `port`.

    Best effort: psutil if available, else `ss -ltnp`. Only processes the
    caller owns report a pid (kernel restriction), so a port held by another
    account comes back with pid None - which is itself the answer to "why
    can't I stop it".
    """
    found = []
    try:
        import psutil

        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            if conn.laddr.port != port or conn.pid is None:
                continue
            try:
                proc = psutil.Process(conn.pid)
                found.append((conn.pid, proc.username(), " ".join(proc.cmdline())[:120]))
            except Exception:
                found.append((conn.pid, "?", "?"))
        if found:
            return found
    except Exception:
        pass
    try:
        out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if f":{port} " in line or line.rstrip().endswith(f":{port}"):
                found.append((None, "?", line.strip()[:160]))
    except (OSError, subprocess.SubprocessError):
        pass
    return found


def describe_port_holder(port):
    """One-line-per-holder description for an error message."""
    holders = who_has_port(port)
    if not holders:
        return (f"Could not identify the process (it likely belongs to another "
                f"account): try 'sudo ss -ltnp | grep {port}'.")
    lines = ["Held by:"]
    for pid, user, cmd in holders:
        lines.append(f"  PID {pid} (user {user}): {cmd}" if pid is not None else f"  {cmd}")
    pids = [pid for pid, _, _ in holders if pid is not None]
    if pids:
        lines.append("If that is a stale session of yours, stop it with: "
                     f"kill {' '.join(str(p) for p in pids)}")
    return "\n".join(lines)


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
            # Last connection wins. The box is a single device: a new
            # connection means the previous one is stale (it rebooted, or its
            # old TCP connection died without a FIN). Waiting for the old one
            # to end would leave a rebooted box unserved forever, which is
            # exactly the "box and server cannot find each other" symptom.
            previous = self.server
            if previous is not None and not previous._stop.is_set():
                print("a box reconnected - dropping the previous connection")
                previous.stop()
            print(f"manual-control box connected on {self.host}:{self.port}")
            server = RemoteControlServer(
                self.root, transport, root_name=self.root_name,
                token=self.token, **self.box_kwargs
            )
            self.server = server
            server.start()
            threading.Thread(target=self._watch_disconnect, args=(server,),
                             daemon=True).start()

    def _watch_disconnect(self, server):
        server.wait()
        if server is self.server and not self._stop.is_set():
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
            f"already serving the control box - only one can.\n"
            f"{describe_port_holder(port)}\n"
            f"Options: use that session, stop its server there (box.stop()), "
            f"kill the process, or pass port=<other>."
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

    try:
        listener = listen_tcp(args.bind, args.tcp)
    except OSError as exc:
        ap.error(f"cannot listen on {args.bind}:{args.tcp} ({exc}).\n"
                 f"{describe_port_holder(args.tcp)}")
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
