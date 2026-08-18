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
        try:
            self._sock.close()
        except OSError:
            pass


def socketpair_transports():
    """Two connected transports in-process (for tests / local loopback)."""
    a, b = socket.socketpair()
    return SocketLineTransport(a), SocketLineTransport(b)


# --- TCP: for local two-process testing only. Production uses serial/BT
# (WiFi/IP is off the table here); TCP over localhost or a USB-ethernet
# gadget is fine for development. ---
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
