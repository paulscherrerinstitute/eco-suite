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
import getpass
import os
import socket
import subprocess
import threading
import time

from . import protocol as p_mod
from .server import RemoteControlServer
from .transport import SerialLineTransport, accept_tcp, connect_tcp, listen_tcp

DEFAULT_BOX_HOST = "ecobox"

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


class BoxSession:
    """This eco session's connection to the control box, in the background.

    The box listens and we call it, so the box is not tied to any console:
    every machine holding the shared token may offer itself, and whoever is
    standing at the box accepts or declines. That means a connection goes
    through three states - dialling, waiting for the operator, connected -
    and can end at any of them, so `.state` and `.reason` say where it got
    to instead of leaving you guessing.

        from eco.manual_control import connect_to_box
        box = connect_to_box(bernina.namespace)      # asks the box operator
        box.state        # 'connected' | 'waiting for the operator' | 'closed'
        box.stop()
    """

    def __init__(self, root, host, port, root_name=None, token=None,
                 identity=None, accept_timeout=120, **box_kwargs):
        self.root = root
        self.root_name = root_name
        self.host = host
        self.port = port
        self.token = token
        self.identity = identity or {}
        self.accept_timeout = accept_timeout
        self.box_kwargs = box_kwargs
        self.server = None
        self.state = "dialling"
        self.reason = None
        self.box_name = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # --- lifecycle ---
    def _run(self):
        try:
            transport = connect_tcp(self.host, self.port)
        except OSError as exc:
            self._finish("closed", f"cannot reach the box at {self.host}:{self.port} ({exc})")
            return
        try:
            transport.write_line(p_mod.encode(
                p_mod.EV_HELLO, token=self.token, **self.identity))
        except OSError as exc:
            self._finish("closed", f"box hung up during the handshake ({exc})")
            return

        self.state = "waiting for the operator"
        print(f"asked the control box at {self.host}:{self.port} to accept this session "
              f"- tap Accept on the box")
        verdict, payload = self._await_verdict(transport)
        if verdict != p_mod.MSG_ACCEPTED:
            self._finish("closed", payload.get("reason", "declined at the box"))
            transport.close()
            return

        self.box_name = payload.get("box")
        self.state = "connected"
        print(f"the control box ({self.box_name}) accepted this session")
        server = RemoteControlServer(
            self.root, transport, root_name=self.root_name, **self.box_kwargs)
        self.server = server
        server.start()
        server.wait()
        self._finish("closed", server.closed_reason or "the box closed the connection")

    def _await_verdict(self, transport):
        """Block until the operator answers, or the wait times out."""
        deadline = time.time() + self.accept_timeout
        while not self._stop.is_set():
            if time.time() > deadline:
                return None, {"reason": f"nobody answered at the box within "
                                        f"{self.accept_timeout:.0f}s"}
            try:
                line = transport.read_line()
            except OSError as exc:
                return None, {"reason": f"link lost while waiting ({exc})"}
            if line is None:
                return None, {"reason": "the box closed the connection"}
            try:
                t, d = p_mod.decode(line)
            except Exception:
                continue
            if t in (p_mod.MSG_ACCEPTED, p_mod.MSG_REJECTED, p_mod.MSG_ERROR):
                return t, d
        return None, {"reason": "stopped locally"}

    def _finish(self, state, reason):
        self.state = state
        self.reason = reason
        if not self._stop.is_set():
            print(f"control box session closed: {reason}")
        self._stop.set()

    @property
    def connected(self):
        return self.state == "connected" and self.server is not None \
            and not self.server._stop.is_set()

    def stop(self):
        self._stop.set()
        if self.server is not None:
            self.server.stop()
        self.state = "closed"
        self.reason = self.reason or "stopped in this session"

    def __repr__(self):
        return (f"<BoxSession {self.host}:{self.port} root={self.root_name!r} "
                f"[{self.state}{'' if not self.reason else ': ' + self.reason}]>")


def connect_to_box(root, root_name=None, host=DEFAULT_BOX_HOST, port=DEFAULT_PORT,
                   token=None, token_file=DEFAULT_TOKEN_FILE, **box_kwargs):
    """Offer this session to the control box; the operator there accepts it."""
    token = read_token(token, token_file)
    if root_name is None:
        root_name = getattr(root, "name", None) or getattr(root, "alias", None) or "eco"
    identity = {
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "namespace": str(root_name),
        "pid": os.getpid(),
    }
    return BoxSession(root, host, port, root_name=str(root_name), token=token,
                      identity=identity, **box_kwargs)


# The box used to be the caller and eco the listener; keep the old name
# working for anything that still uses it.
start_box_server = connect_to_box


def build_root(args):
    if args.bernina:
        import eco.bernina as bernina

        return bernina.namespace, "bernina"
    from ..demo import build_fake_beamline

    return build_fake_beamline(), "beamline"


def main():
    ap = argparse.ArgumentParser(
        description="offer this eco session to the manual-control box "
                    "(the box listens; the operator there accepts)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--bernina", action="store_true", help="serve the real bernina namespace")
    src.add_argument("--fake", action="store_true", help="serve the offline fake beamline (default)")
    ap.add_argument("--box", default=DEFAULT_BOX_HOST, metavar="HOST",
                    help=f"the control box to call (default {DEFAULT_BOX_HOST})")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--token", metavar="STR", help="shared secret the box requires")
    ap.add_argument("--token-file", default=DEFAULT_TOKEN_FILE, metavar="PATH")
    ap.add_argument("--serial", metavar="DEV",
                    help="serial link instead of the network (Bluetooth pendant build)")
    args = ap.parse_args()

    root, name = build_root(args)

    if args.serial:
        print(f"serving '{name}' over {args.serial} ...")
        server = RemoteControlServer(root, SerialLineTransport(args.serial),
                                     root_name=name).start()
        server.wait()
        return

    session = connect_to_box(root, root_name=name, host=args.box, port=args.port,
                             token=args.token, token_file=args.token_file)
    try:
        while session.state not in ("closed",):
            time.sleep(0.5)
    except KeyboardInterrupt:
        session.stop()
    print(session.reason or "session ended")


if __name__ == "__main__":
    main()
