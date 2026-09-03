"""Line transport abstraction: read/write newline-delimited messages over
*something*. The rest of the remote stack depends only on read_line /
write_line, so the same server/client run over an in-process socket pair
(tests, local dev) today and over a Bluetooth RFCOMM serial device on the
Pi with no logic change.

Bluetooth wiring (outside this module): pair Pi<->PC once, bind an RFCOMM
channel so a serial device appears (e.g. /dev/rfcomm0 on both ends), then
use SerialLineTransport("/dev/rfcomm0"). No IP/WiFi involved.
"""

import os
import socket
import threading
import time


class LineTransport:
    def read_line(self):
        raise NotImplementedError

    def write_line(self, line):
        raise NotImplementedError

    def close(self):
        pass


class SocketLineTransport(LineTransport):
    def __init__(self, sock):
        self._sock = sock
        self._buf = b""
        self._wlock = threading.Lock()

    def read_line(self):
        while b"\n" not in self._buf:
            try:
                chunk = self._sock.recv(4096)
            except OSError:
                return None
            if not chunk:
                return None
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode()

    def write_line(self, line):
        if not line.endswith("\n"):
            line += "\n"
        with self._wlock:
            self._sock.sendall(line.encode())

    def close(self):
        # shutdown() BEFORE close(): a plain close() on a socket that another
        # thread is blocked in recv() on does not tear the connection down
        # (the file description stays open for the duration of that syscall),
        # so no FIN reaches the peer and the other end never notices the link
        # died. shutdown() sends it immediately and wakes the blocked reader.
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


def socketpair_transports():
    """Two connected transports in-process (for tests / local loopback)."""
    a, b = socket.socketpair()
    return SocketLineTransport(a), SocketLineTransport(b)


# --- TCP: the production link for an Ethernet/PoE-connected box (the PSI
# Motor Control Unit box is powered and networked over one PoE cable), and
# the two-process local testing link. Serial (Bluetooth RFCOMM / USB-gadget)
# remains for battery-powered pendants with no network. ---
def listen_tcp(host="127.0.0.1", port=0):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    return srv


def accept_tcp(listen_sock):
    conn, _ = listen_sock.accept()
    return SocketLineTransport(conn)


def connect_tcp(host, port):
    return SocketLineTransport(socket.create_connection((host, port)))


def connect_tcp_retry(host, port, interval=3.0, attempts=0, log=print):
    """Connect, retrying until it succeeds (attempts=0 means forever).

    The box autostarts on power-up over PoE, usually before - or between -
    the eco sessions it talks to, so a one-shot connect would just die at
    boot. Retrying makes "plug the cable in" the whole startup procedure.
    """
    tries = 0
    while True:
        tries += 1
        try:
            return connect_tcp(host, port)
        except OSError as exc:
            if attempts and tries >= attempts:
                raise
            if log:
                log(f"connect to {host}:{port} failed ({exc}); retrying in {interval}s")
            time.sleep(interval)


class SerialLineTransport(LineTransport):
    """For a Bluetooth RFCOMM (or any) serial device such as /dev/rfcomm0.

    Untested without hardware, but intentionally the same tiny interface
    as SocketLineTransport so it is a drop-in on the Pi.
    """

    def __init__(self, path):
        self._fd = os.open(path, os.O_RDWR)
        self._buf = b""
        self._wlock = threading.Lock()

    def read_line(self):
        while b"\n" not in self._buf:
            chunk = os.read(self._fd, 4096)
            if not chunk:
                return None
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode()

    def write_line(self, line):
        if not line.endswith("\n"):
            line += "\n"
        data = line.encode()
        with self._wlock:
            while data:
                data = data[os.write(self._fd, data):]

    def close(self):
        try:
            os.close(self._fd)
        except OSError:
            pass
