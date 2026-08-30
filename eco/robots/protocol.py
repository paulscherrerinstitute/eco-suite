"""Stäubli VAL3 line protocol over TCP.

The robot controller runs a VAL3 program (``$exec``-style dispatcher) listening
on a plain TCP socket. It speaks one request/response pair per line:

    TX:  "<nnn> <command> <arg0>|<arg1>|...\\n"
    RX:  "<nnn><sep><payload>\\n"

``<nnn>`` is a zero-padded 3-digit message id that the controller echoes back,
``<sep>`` is a space on success and ``'*'`` on error (payload is then the error
text). A payload containing ``'|'`` is a list; VAL3 pads its fields to fixed
width, so every field needs stripping before conversion.

Verified against the live Bernina controller (129.129.243.106:1234)::

    TX '000 get_status_fast None\\n'
    RX '000 -78.88|105   |-12.57|-0.57|10.2 |3.12|2359.25|...|846.72|\\n'
    TX '003 eval tcp_b=isPowered()\\n'
    RX '003 \\n'                     # empty payload == success
    TX '004 get_bool tcp_b\\n'
    RX '004 0\\n'

Note the trailing ``'|'``: a list reply always ends with the separator, so
``split('|')`` yields one extra empty field. Callers index fixed positions or
slice, so the extra field is harmless -- but never rely on ``len()``.

This module is deliberately free of eco imports: the robot server must come up
without dragging in EPICS/cam_server, and the driver must be unit-testable
against :mod:`eco.robots.simulation` with no hardware.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

#: The controller's dispatcher rejects longer frames outright.
MAX_MESSAGE_SIZE = 150
#: VAL3 ``$exec`` argument-list limit.
MAX_NUMBER_PARAMETERS = 20


class Val3Error(Exception):
    """The controller answered with an error frame (``'*'`` separator)."""


class Val3ProtocolError(Exception):
    """The reply did not parse: wrong id, short frame, socket desync."""


class Val3Timeout(Val3ProtocolError):
    """No reply within the timeout."""


class Cancelled(Exception):
    """A cancellation token fired between/inside protocol calls."""


class CancellationToken:
    """Cooperative cancellation.

    Python cannot safely interrupt a thread the way the Java/Jython pshell
    context did (``Thread.interrupt()``), so aborting a long command is
    cooperative instead: every protocol round trip and every wait loop calls
    :meth:`raise_if_cancelled`. Because the socket read is bounded by the
    per-call timeout (1 s by default), worst-case abort latency is one
    timeout -- which is what the old ``:abort`` achieved in practice anyway.
    """

    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    def reset(self):
        self._event.clear()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self):
        if self._event.is_set():
            raise Cancelled("operation cancelled")

    def sleep(self, seconds: float, interval: float = 0.02):
        """Interruptible sleep. Raises :class:`Cancelled` promptly."""
        end = time.time() + seconds
        while True:
            self.raise_if_cancelled()
            remaining = end - time.time()
            if remaining <= 0:
                return
            time.sleep(min(interval, remaining))


class BaseTransport:
    """Byte-level line transport. Split out so tests can fake the controller."""

    #: True on fakes. The driver skips or shortcuts controller-only
    #: behaviour when this is set, exactly as pshell's isSimulated() did.
    simulated = False

    def connect(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    @property
    def connected(self) -> bool:
        raise NotImplementedError

    def request(self, line: str, timeout: float) -> str:
        """Send one line, return one line (both including no trailing \\n)."""
        raise NotImplementedError


class TcpTransport(BaseTransport):
    """Blocking socket transport with lazy connect and reconnect-on-failure."""

    def __init__(self, host: str, port: int, connect_timeout: float = 5.0):
        self.host = host
        self.port = int(port)
        self.connect_timeout = connect_timeout
        self._sock = None
        self._file = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self):
        self.close()
        logger.info("connecting to VAL3 controller %s:%d", self.host, self.port)
        self._sock = socket.create_connection(
            (self.host, self.port), timeout=self.connect_timeout
        )
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._file = self._sock.makefile("rb")

    def close(self):
        for obj in (self._file, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except OSError:
                pass
        self._file = None
        self._sock = None

    def request(self, line: str, timeout: float) -> str:
        if self._sock is None:
            self.connect()
        self._sock.settimeout(timeout)
        try:
            self._sock.sendall(line.encode("ascii", "replace"))
            reply = self._file.readline()
        except socket.timeout as exc:
            # A timed-out read leaves an unread reply in flight: the socket is
            # desynchronised and must not be reused, or the next call would
            # get *this* call's answer.
            self.close()
            raise Val3Timeout(f"no reply within {timeout} s to {line!r}") from exc
        except OSError as exc:
            self.close()
            raise Val3ProtocolError(f"socket error on {line!r}: {exc}") from exc
        if not reply:
            self.close()
            raise Val3ProtocolError("controller closed the connection")
        return reply.decode("ascii", "replace").rstrip("\r\n")


class Val3Link:
    """Typed, thread-safe access to one VAL3 controller.

    All traffic is serialised by :attr:`_lock`: the message id only
    disambiguates replies within a single exchange, so send and receive must
    stay paired. :meth:`transaction` upgrades that to a whole multi-call
    sequence, which the original driver lacked -- there, the 1 Hz poller could
    interleave between the ``set_pnt``/``movel`` calls of a motion and observe
    (or worse, act on) half-written controller state.
    """

    def __init__(self, transport: BaseTransport, timeout: float = 1.0,
                 retries: int = 1, latency: float = 0.0):
        self.transport = transport
        self.timeout = timeout
        self.retries = retries
        #: Optional minimum spacing between frames (s); the old driver used
        #: this to throttle a controller that choked on back-to-back frames.
        self.latency = latency
        self.header = None
        self.trailer = "\n"
        self.array_separator = "|"
        self.cmd_separator = " "
        self._lock = threading.RLock()
        self._msg_id = 0
        self._last_msg_timestamp = 0.0
        self.cancellation = CancellationToken()
        self.simulated = getattr(transport, "simulated", False)

    # ------------------------------------------------------------------ core

    @contextmanager
    def transaction(self):
        """Hold the link for a whole sequence of calls.

        Use around any multi-call operation whose intermediate controller
        state must not be observed or disturbed by the poller.
        """
        with self._lock:
            yield self

    @property
    def connected(self) -> bool:
        return self.transport.connected

    def close(self):
        with self._lock:
            self.transport.close()

    def call(self, msg: str, timeout: float | None = None) -> str:
        """One request/response round trip. Returns the raw payload."""
        timeout = self.timeout if timeout is None else timeout
        with self._lock:
            self.cancellation.raise_if_cancelled()
            last_exc = None
            for attempt in range(self.retries + 1):
                msg_id = "%03d" % self._msg_id
                self._msg_id = (self._msg_id + 1) % 1000
                try:
                    return self._exchange(msg_id, msg, timeout)
                except Val3Error:
                    raise  # a controller-level error is an answer, not a fault
                except Val3ProtocolError as exc:
                    last_exc = exc
                    if attempt < self.retries:
                        logger.warning("retrying %r after %s", msg, exc)
                        continue
                    raise last_exc

    def _exchange(self, msg_id: str, msg: str, timeout: float) -> str:
        if self.latency > 0:
            elapsed = time.time() - self._last_msg_timestamp
            if elapsed < self.latency:
                time.sleep(self.latency - elapsed)
        tx = (self.header or "") + msg_id + " " + msg
        if len(tx) > MAX_MESSAGE_SIZE:
            raise Val3ProtocolError(
                f"message of {len(tx)} chars exceeds the controller's "
                f"{MAX_MESSAGE_SIZE}-char frame limit: {tx!r}"
            )
        logger.log(5, "TX %r", tx)
        if self.trailer:
            tx += self.trailer
        rx = self.transport.request(tx, timeout)
        self._last_msg_timestamp = time.time()
        logger.log(5, "RX %r", rx)

        if len(rx) < 4:
            raise Val3ProtocolError(f"reply too short ({len(rx)} chars): {rx!r}")
        if rx[:3] != msg_id:
            # The original raised NameError here (it referenced an undefined
            # `start`), masking every desync as a confusing traceback. A
            # mismatched id means the stream is out of step; drop the socket so
            # the next call starts clean.
            self.transport.close()
            raise Val3ProtocolError(
                f"reply id {rx[:3]!r} does not match request id {msg_id!r} "
                f"(link desynchronised, reconnecting)"
            )
        if rx[3] == "*":
            raise Val3Error(rx[4:].strip())
        return rx[4:]

    def execute(self, command, *args, timeout: float | None = None):
        """Send ``command arg0|arg1|...``; split list replies on ``'|'``."""
        if len(args) > MAX_NUMBER_PARAMETERS:
            raise Val3ProtocolError(
                f"{len(args)} parameters exceeds the limit of "
                f"{MAX_NUMBER_PARAMETERS}"
            )
        msg = str(command)
        for i, arg in enumerate(args):
            msg += (self.cmd_separator if i == 0 else self.array_separator) + str(arg)
        rx = self.call(msg, timeout)
        if self.array_separator in rx:
            return rx.split(self.array_separator)
        return rx

    # ------------------------------------------------------------- variables

    def evaluate(self, cmd: str, timeout: float | None = None):
        """Evaluate a VAL3 statement. Empty reply == success."""
        ret = self.execute("eval", cmd, timeout=timeout)
        if isinstance(ret, str) and ret.strip():
            raise Val3Error(ret.strip())

    def get_var(self, name: str) -> str:
        return self.execute("get_var", name)

    def get_str(self, name: str = "tcp_s") -> str:
        return self.execute("get_str", name)

    def set_str(self, value, name: str = "tcp_s"):
        self.evaluate(f'{name}="{value}"')

    def get_arr(self, name: str, size: int):
        return self.execute("get_arr", name, size)

    def get_bool(self, name: str = "tcp_b") -> bool:
        return self.execute("get_bool", name).strip() == "1"

    def get_int(self, name: str = "tcp_n") -> int:
        return int(self.get_var(name).strip())

    def get_float(self, name: str = "tcp_n") -> float:
        return float(self.get_var(name).strip())

    def get_int_arr(self, size: int, name: str = "tcp_a"):
        return [int(v.strip()) for v in self.get_arr(name, size)[:size]]

    def get_float_arr(self, size: int, name: str = "tcp_a"):
        return [float(v.strip()) for v in self.get_arr(name, size)[:size]]

    def get_trsf(self, name: str = "tcp_t"):
        return [float(v.strip()) for v in self.execute("get_trf", name)[:6]]

    def set_trsf(self, values, name: str = "tcp_t"):
        v = [round(float(x), 3) for x in values]
        self.evaluate("%s={%s}" % (name, ",".join(str(x) for x in v)))

    def get_jnt(self, name: str = "tcp_j"):
        return [float(v.strip()) for v in self.execute("get_jnt", name)[:6]]

    def set_jnt(self, values, name: str = "tcp_j"):
        self.evaluate("%s={%s}" % (name, ",".join(str(float(x)) for x in values)))

    def get_pnt(self, name: str = "tcp_p"):
        return self.get_trsf(name + ".trsf")

    def set_pnt(self, trsf, name: str = "tcp_p"):
        self.set_trsf(trsf, name + ".trsf")

    # --------------------------------------------------- evaluate-and-return

    def eval_int(self, cmd: str) -> int:
        self.evaluate("tcp_n=" + cmd)
        return self.get_int()

    def eval_float(self, cmd: str) -> float:
        self.evaluate("tcp_n=" + cmd)
        return self.get_float()

    def eval_bool(self, cmd: str) -> bool:
        self.evaluate("tcp_b=" + cmd)
        return self.get_bool()

    def eval_str(self, cmd: str) -> str:
        self.evaluate("tcp_s=" + cmd)
        return self.get_str()

    def eval_jnt(self, cmd: str):
        self.evaluate("tcp_j=" + cmd)
        return self.get_jnt()

    def eval_trf(self, cmd: str):
        self.evaluate("tcp_t=" + cmd)
        return self.get_trsf()

    def eval_pnt(self, cmd: str):
        self.evaluate("tcp_p=" + cmd)
        return self.get_pnt()
