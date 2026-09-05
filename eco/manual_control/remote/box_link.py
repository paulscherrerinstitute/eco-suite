"""Box side of the link: the box listens, eco sessions connect to it.

The box belongs to no particular console. Any machine that holds the shared
token may offer itself; the operator standing at the box decides who gets
it, which is the only decision that can be made safely - only the person
holding the box knows whether they want this session or the other one.

    listener = BoxListener(port=8791, token=...)
    req = listener.pending()          # polled from the Tk loop
    if req:
        transport = req.accept()      # -> hand it to RemoteControlClient
        # or req.reject("not now")

Eco-free (stdlib only), so it ships in the Pi bundle.
"""

import queue
import socket
import threading

from . import protocol as p
from .transport import SocketLineTransport, _enable_keepalive

HANDSHAKE_TIMEOUT = 10.0  # seconds to wait for the caller's hello


class ConnectionRequest:
    """One eco session asking to drive the box, awaiting the operator."""

    def __init__(self, transport, identity, box_name):
        self.transport = transport
        self.identity = identity or {}
        self.box_name = box_name
        self.answered = False

    # --- what the operator is shown ---
    @property
    def who(self):
        user = self.identity.get("user", "?")
        host = self.identity.get("host", "?")
        return f"{user}@{host}"

    @property
    def namespace(self):
        return self.identity.get("namespace") or "?"

    def describe(self):
        pid = self.identity.get("pid")
        return f"{self.who}  ({self.namespace}{f', pid {pid}' if pid else ''})"

    # --- the operator's decision ---
    def accept(self):
        """Tell the caller it may drive the box; returns its transport."""
        self.answered = True
        self.transport.write_line(p.encode(p.MSG_ACCEPTED, box=self.box_name))
        return self.transport

    def reject(self, reason="declined at the box"):
        self.answered = True
        try:
            self.transport.write_line(p.encode(p.MSG_REJECTED, reason=reason))
        except OSError:
            pass
        self.transport.close()

    def __repr__(self):
        return f"<ConnectionRequest {self.describe()}>"


def say_bye(transport, reason):
    """Tell a session why it is being dropped, then close it.

    Without this the displaced session would just see its socket die and sit
    there looking connected; with it, it closes itself and says why.
    """
    try:
        transport.write_line(p.encode(p.MSG_BYE, reason=reason))
    except OSError:
        pass
    try:
        transport.close()
    except OSError:
        pass


class BoxListener:
    """Accepts eco sessions on the box, one pending request at a time."""

    def __init__(self, port=8791, token=None, bind="0.0.0.0", box_name=None):
        self.port = port
        self.token = token
        self.bind = bind
        self.box_name = box_name or socket.gethostname()
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((bind, port))
        self._sock.listen(2)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        print(f"box listening for eco sessions on {bind}:{port} "
              f"({'token required' if token else 'NO token'})")

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except OSError:
                break
            if self._stop.is_set():
                conn.close()
                break
            threading.Thread(target=self._greet, args=(conn, addr),
                             daemon=True).start()

    def _greet(self, conn, addr):
        """Read the caller's hello and check the token, off the accept loop.

        Done in its own thread so a caller that connects and then says
        nothing cannot block anyone else from reaching the box.
        """
        _enable_keepalive(conn)
        conn.settimeout(HANDSHAKE_TIMEOUT)
        transport = SocketLineTransport(conn)
        try:
            line = transport.read_line()
            t, d = p.decode(line) if line else (None, {})
        except Exception:
            transport.close()
            return
        if t != p.EV_HELLO:
            try:
                transport.write_line(p.encode(p.MSG_REJECTED, reason="expected hello"))
            except OSError:
                pass
            transport.close()
            return
        if self.token is not None and d.get("token") != self.token:
            print(f"rejected {addr[0]}: bad or missing token")
            try:
                transport.write_line(p.encode(p.MSG_REJECTED, reason="bad or missing token"))
            except OSError:
                pass
            transport.close()
            return
        conn.settimeout(None)  # back to blocking for the session itself
        d.setdefault("host", addr[0])
        print(f"connection request from {d.get('user', '?')}@{d.get('host', '?')}")
        self._queue.put(ConnectionRequest(transport, d, self.box_name))

    def pending(self):
        """The next request awaiting an answer, or None. Non-blocking."""
        while True:
            try:
                request = self._queue.get_nowait()
            except queue.Empty:
                return None
            # skip callers that gave up while waiting for the operator
            try:
                request.transport._sock.setblocking(False)
                if request.transport._sock.recv(1, socket.MSG_PEEK) == b"":
                    request.transport.close()
                    continue
            except BlockingIOError:
                pass
            except OSError:
                request.transport.close()
                continue
            finally:
                try:
                    request.transport._sock.setblocking(True)
                except OSError:
                    pass
            return request

    def close(self):
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
