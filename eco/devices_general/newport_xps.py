"""TCP driver for Newport XPS motion controllers (C / D / Q / RL).

A modern, EPICS-independent driver for the Newport **XPS** family of network
motion controllers.  The XPS exposes an ASCII socket API on TCP port ``5001``:
each call is sent as a function-call string (``FunctionName(arg1,arg2,...)``)
and the controller answers with

    ``<errorCode>,<value1>,<value2>,...,EndOfAPI``

where ``errorCode == 0`` means success.  Output parameters are represented in
the request by the literal placeholder ``double *`` / ``int *`` / ``char *``,
mirroring the C prototype - this driver inserts those placeholders for you.

The XPS organises axes into **groups**; a positioner is addressed as
``GroupName.PositionerName`` and moves are issued per group.  A single-axis
group (the common case) can therefore be driven with just the group name.

Design goals
------------
* Standard library only (``socket``) - no vendor DLL, no EPICS.
* Full type hints, context-manager lifecycle, structured errors that carry the
  controller's own error strings.
* Both a low-level :meth:`NewportXPS.send` escape hatch and typed convenience
  methods covering initialise / home / move / status / velocity / abort, plus
  an :class:`XPSAxis` helper bound to one group.

Example
-------
::

    from eco.devices_general.newport_xps import NewportXPS

    with NewportXPS("192.168.0.254") as xps:
        print(xps.firmware_version())
        xps.initialize("Group1")
        xps.home("Group1")
        xps.move_absolute("Group1", 10.0)       # mm or deg, per stage config
        print(xps.position("Group1"))

    # Or via the per-axis helper:
    with NewportXPS("192.168.0.254") as xps:
        stage = xps.axis("Group1", "Pos")       # Group1.Pos
        stage.enable()
        stage.move_relative(1.5)
        print(stage.position)
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

__all__ = [
    "XPSError",
    "XPSTimeout",
    "XPSCommandError",
    "GroupStatus",
    "NewportXPS",
    "XPSAxis",
]

log = logging.getLogger(__name__)

_END_OF_API: Final = ",EndOfAPI"
_DEFAULT_PORT: Final = 5001
_RECV_SIZE: Final = 4096


class XPSError(Exception):
    """Base class for all XPS driver errors."""


class XPSTimeout(XPSError):
    """The controller did not return a complete reply in time."""


class XPSCommandError(XPSError):
    """The controller returned a non-zero error code.

    The numeric :attr:`code` and the controller's own :attr:`description`
    (fetched via ``ErrorStringGet``) are attached for diagnosis.
    """

    def __init__(self, command: str, code: int, description: str = "") -> None:
        self.command = command
        self.code = code
        self.description = description
        detail = f" ({description})" if description else ""
        super().__init__(f"XPS command {command!r} failed: error {code}{detail}")


class GroupStatus(IntEnum):
    """A few frequently-checked XPS group status codes.

    The XPS defines ~70 status codes; only the ones this driver reasons about
    are enumerated.  Any other value is returned as a plain ``int`` by
    :meth:`NewportXPS.group_status`.
    """

    NOT_INITIALIZED = 0
    NOT_INIT_AFTER_KILL = 7
    NOT_INIT_AFTER_MECH_STOP = 10
    READY_FROM_HOMING = 11
    READY_FROM_MOTION = 12
    READY = 13
    DISABLED = 20
    HOMING = 41
    MOVING = 44


# Status codes that mean "homed / enabled / ready to move".
_READY_STATES: Final = frozenset({11, 12, 13, 14, 15, 16, 17, 18, 19})
# Status codes that mean "motion or homing in progress".
_BUSY_STATES: Final = frozenset({41, 42, 43, 44, 45, 46})


@dataclass(slots=True)
class _Connection:
    """Thin owned TCP socket with a read-until-terminator helper."""

    host: str
    port: int = _DEFAULT_PORT
    timeout: float = 10.0
    _sock: socket.socket | None = None

    def open(self) -> None:
        if self._sock is not None:
            return
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        # Motion command/response latency matters far more than throughput.
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def request(self, command: str) -> str:
        if self._sock is None:
            raise XPSError("Connection is not open.")
        self._sock.sendall(command.encode("ascii"))
        buf = ""
        while _END_OF_API not in buf:
            try:
                chunk = self._sock.recv(_RECV_SIZE)
            except socket.timeout as exc:
                raise XPSTimeout(f"No reply to {command!r} within {self.timeout}s.") from exc
            if not chunk:
                raise XPSError("Controller closed the connection.")
            buf += chunk.decode("ascii", errors="replace")
        return buf


class NewportXPS:
    """Socket client for a Newport XPS motion controller.

    One instance owns a single TCP session.  All public calls are serialised
    with an internal lock, so a shared instance may be used from several
    threads.  Positions are in the engineering units configured for each stage
    on the controller (mm for linear, deg for rotary).
    """

    def __init__(self, host: str, port: int = _DEFAULT_PORT, *,
                 timeout: float = 10.0) -> None:
        self._conn = _Connection(host=host, port=port, timeout=timeout)
        self._lock = threading.RLock()

    # -- lifecycle ------------------------------------------------------------
    def open(self) -> "NewportXPS":
        """Open the TCP session (idempotent)."""
        self._conn.open()
        log.info("Connected to XPS at %s:%d", self._conn.host, self._conn.port)
        return self

    def close(self) -> None:
        """Close the TCP session."""
        self._conn.close()
        log.info("Disconnected from XPS at %s", self._conn.host)

    def __enter__(self) -> "NewportXPS":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low-level protocol ---------------------------------------------------
    def send(self, command: str, *, check: bool = True) -> list[str]:
        """Send a raw XPS command and return the reply values as strings.

        The error code is stripped; on a non-zero code (and ``check``) an
        :class:`XPSCommandError` is raised, annotated with the controller's
        error string.  Use ``check=False`` for calls whose non-zero codes you
        want to inspect yourself (e.g. polling).
        """
        with self._lock:
            raw = self._conn.request(command)
            code, values = self._split(raw)
            if check and code != 0:
                raise XPSCommandError(command, code, self._error_string(code))
            return values

    @staticmethod
    def _split(raw: str) -> tuple[int, list[str]]:
        """Split ``<code>,<v1>,...,EndOfAPI`` into (code, [values])."""
        body = raw[: raw.index(_END_OF_API)] if _END_OF_API in raw else raw
        head, _, tail = body.partition(",")
        try:
            code = int(head)
        except ValueError as exc:
            raise XPSError(f"Malformed XPS reply: {raw!r}") from exc
        values = tail.split(",") if tail else []
        return code, values

    def _error_string(self, code: int) -> str:
        """Best-effort lookup of the controller's text for an error code."""
        try:
            with self._lock:
                raw = self._conn.request(f"ErrorStringGet({code},char *)")
            rc, values = self._split(raw)
            if rc == 0 and values:
                return values[0]
        except XPSError:
            pass
        return ""

    # -- identification & health ---------------------------------------------
    def firmware_version(self) -> str:
        """Return the controller firmware version (``FirmwareVersionGet``)."""
        return self.send("FirmwareVersionGet(char *)")[0]

    def elapsed_time(self) -> float:
        """Controller uptime in seconds (``ElapsedTimeGet``)."""
        return float(self.send("ElapsedTimeGet(double *)")[0])

    def login(self, username: str, password: str) -> None:
        """Authenticate the session (``Login``); most setups do not need this."""
        self.send(f"Login({username},{password})")

    # -- group lifecycle ------------------------------------------------------
    def initialize(self, group: str) -> None:
        """Initialise a group so it can be homed (``GroupInitialize``)."""
        self.send(f"GroupInitialize({group})")

    def home(self, group: str) -> None:
        """Run the group's home search (``GroupHomeSearch``). Blocks on the XPS."""
        self.send(f"GroupHomeSearch({group})")

    def kill(self, group: str) -> None:
        """Kill (disable + de-initialise) a group (``GroupKill``)."""
        self.send(f"GroupKill({group})")

    def enable(self, group: str) -> None:
        """Enable a group after it was disabled (``GroupMotionEnable``)."""
        self.send(f"GroupMotionEnable({group})")

    def disable(self, group: str) -> None:
        """Disable motion on a group (``GroupMotionDisable``)."""
        self.send(f"GroupMotionDisable({group})")

    # -- status ---------------------------------------------------------------
    def group_status(self, group: str) -> int:
        """Return the numeric group status code (``GroupStatusGet``)."""
        return int(self.send(f"GroupStatusGet({group},int *)")[0])

    def group_status_string(self, group: str) -> str:
        """Return the human-readable text for a group's current status."""
        code = self.group_status(group)
        return self.send(f"GroupStatusStringGet({code},char *)")[0]

    def is_ready(self, group: str) -> bool:
        """``True`` if the group is homed/enabled and ready to move."""
        return self.group_status(group) in _READY_STATES

    def is_moving(self, group: str) -> bool:
        """``True`` if the group is currently homing or moving."""
        return self.group_status(group) in _BUSY_STATES

    # -- position -------------------------------------------------------------
    def position(self, target: str, count: int = 1) -> list[float]:
        """Current position(s) of ``target`` (``GroupPositionCurrentGet``).

        ``target`` is a group (returns every positioner) or a single
        ``Group.Positioner``.  ``count`` is the number of positioners to read.
        """
        placeholders = ",".join(["double *"] * count)
        values = self.send(f"GroupPositionCurrentGet({target},{placeholders})")
        return [float(v) for v in values]

    def setpoint(self, target: str, count: int = 1) -> list[float]:
        """Commanded setpoint position(s) (``GroupPositionSetpointGet``)."""
        placeholders = ",".join(["double *"] * count)
        values = self.send(f"GroupPositionSetpointGet({target},{placeholders})")
        return [float(v) for v in values]

    # -- moves ----------------------------------------------------------------
    def move_absolute(self, target: str, *positions: float) -> None:
        """Move to absolute position(s) (``GroupMoveAbsolute``).

        Pass one value per positioner in the group (or one for a
        ``Group.Positioner`` target).
        """
        args = ",".join(_fmt(p) for p in positions)
        self.send(f"GroupMoveAbsolute({target},{args})")

    def move_relative(self, target: str, *deltas: float) -> None:
        """Move by relative delta(s) (``GroupMoveRelative``)."""
        args = ",".join(_fmt(d) for d in deltas)
        self.send(f"GroupMoveRelative({target},{args})")

    def jog(self, target: str, velocity: float, acceleration: float) -> None:
        """Set jog velocity/acceleration (``GroupJogParametersSet``).

        The group must already be in jog mode (``GroupJogModeEnable``).
        """
        self.send(f"GroupJogParametersSet({target},{_fmt(velocity)},{_fmt(acceleration)})")

    def abort_move(self, group: str) -> None:
        """Abort motion on a group without disabling it (``GroupMoveAbort``)."""
        self.send(f"GroupMoveAbort({group})")

    # -- motion parameters ----------------------------------------------------
    def get_velocity(self, positioner: str) -> tuple[float, float, float, float]:
        """Return (velocity, acceleration, min_jerk, max_jerk) for a positioner.

        Uses ``PositionerSGammaParametersGet``; ``positioner`` must be a full
        ``Group.Positioner`` name.
        """
        v = self.send(f"PositionerSGammaParametersGet({positioner},double *,double *,double *,double *)")
        return (float(v[0]), float(v[1]), float(v[2]), float(v[3]))

    def set_velocity(self, positioner: str, velocity: float, acceleration: float,
                     min_jerk: float, max_jerk: float) -> None:
        """Set the S-curve motion profile (``PositionerSGammaParametersSet``)."""
        self.send(
            f"PositionerSGammaParametersSet({positioner},"
            f"{_fmt(velocity)},{_fmt(acceleration)},{_fmt(min_jerk)},{_fmt(max_jerk)})"
        )

    def travel_limits(self, positioner: str) -> tuple[float, float]:
        """Return (min, max) user travel limits (``PositionerUserTravelLimitsGet``)."""
        v = self.send(f"PositionerUserTravelLimitsGet({positioner},double *,double *)")
        return (float(v[0]), float(v[1]))

    # -- blocking helpers -----------------------------------------------------
    def wait(self, group: str, *, poll: float = 0.05, timeout: float | None = 120.0) -> None:
        """Block until ``group`` finishes moving/homing.

        Raises :class:`XPSTimeout` on timeout, or :class:`XPSCommandError` if
        the group drops into a not-initialised / disabled state mid-move.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            status = self.group_status(group)
            if status in _READY_STATES:
                return
            if status not in _BUSY_STATES:
                text = self.send(f"GroupStatusStringGet({status},char *)")[0]
                raise XPSCommandError(f"wait({group})", status, text)
            if deadline is not None and time.monotonic() > deadline:
                raise XPSTimeout(f"{group}: motion did not finish in {timeout}s.")
            time.sleep(poll)

    def move_absolute_and_wait(self, group: str, *positions: float,
                               **wait_kwargs: object) -> None:
        self.move_absolute(group, *positions)
        self.wait(group, **wait_kwargs)  # type: ignore[arg-type]

    def move_relative_and_wait(self, group: str, *deltas: float,
                               **wait_kwargs: object) -> None:
        self.move_relative(group, *deltas)
        self.wait(group, **wait_kwargs)  # type: ignore[arg-type]

    # -- axis helper ----------------------------------------------------------
    def axis(self, group: str, positioner: str = "Pos") -> "XPSAxis":
        """Return an :class:`XPSAxis` bound to ``group.positioner``."""
        return XPSAxis(self, group, positioner)


def _fmt(value: float) -> str:
    """Format a float for the XPS ASCII protocol (repr keeps full precision)."""
    return repr(float(value))


class XPSAxis:
    """Convenience wrapper for one positioner in a single-axis group.

    Delegates to a :class:`NewportXPS`, pre-binding the ``Group.Positioner``
    name so single-axis stages read like a simple motor object.
    """

    def __init__(self, xps: NewportXPS, group: str, positioner: str = "Pos") -> None:
        self.xps = xps
        self.group = group
        self.positioner = positioner

    @property
    def name(self) -> str:
        """Full ``Group.Positioner`` identifier."""
        return f"{self.group}.{self.positioner}"

    def __repr__(self) -> str:
        return f"<XPSAxis {self.name!r}>"

    # lifecycle
    def initialize(self) -> None:
        self.xps.initialize(self.group)

    def home(self) -> None:
        self.xps.home(self.group)

    def enable(self) -> None:
        self.xps.enable(self.group)

    def disable(self) -> None:
        self.xps.disable(self.group)

    def kill(self) -> None:
        self.xps.kill(self.group)

    # state
    @property
    def position(self) -> float:
        return self.xps.position(self.name, 1)[0]

    @property
    def status(self) -> int:
        return self.xps.group_status(self.group)

    @property
    def is_moving(self) -> bool:
        return self.xps.is_moving(self.group)

    @property
    def is_ready(self) -> bool:
        return self.xps.is_ready(self.group)

    @property
    def limits(self) -> tuple[float, float]:
        return self.xps.travel_limits(self.name)

    # moves
    def move_absolute(self, position: float, *, wait: bool = False,
                      **wait_kwargs: object) -> None:
        self.xps.move_absolute(self.group, position)
        if wait:
            self.xps.wait(self.group, **wait_kwargs)  # type: ignore[arg-type]

    def move_relative(self, delta: float, *, wait: bool = False,
                      **wait_kwargs: object) -> None:
        self.xps.move_relative(self.group, delta)
        if wait:
            self.xps.wait(self.group, **wait_kwargs)  # type: ignore[arg-type]

    def stop(self) -> None:
        self.xps.abort_move(self.group)

    def wait(self, **wait_kwargs: object) -> None:
        self.xps.wait(self.group, **wait_kwargs)  # type: ignore[arg-type]

    # motion profile
    def get_velocity(self) -> tuple[float, float, float, float]:
        return self.xps.get_velocity(self.name)

    def set_velocity(self, velocity: float, acceleration: float,
                     min_jerk: float = 0.0, max_jerk: float = 0.0) -> None:
        self.xps.set_velocity(self.name, velocity, acceleration, min_jerk, max_jerk)


def _main() -> None:  # pragma: no cover - manual bench utility
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m eco.devices_general.newport_xps",
        description="Newport XPS TCP probe.",
    )
    p.add_argument("host", help="controller IP / hostname")
    p.add_argument("-p", "--port", type=int, default=_DEFAULT_PORT)
    p.add_argument("-g", "--group", help="group to query, e.g. Group1")
    p.add_argument("cmd", nargs="?", help="raw XPS command, e.g. 'FirmwareVersionGet(char *)'")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with NewportXPS(args.host, args.port) as xps:
        if args.cmd:
            print(xps.send(args.cmd))
        else:
            print("firmware:", xps.firmware_version())
            if args.group:
                print("status:  ", xps.group_status_string(args.group))
                print("position:", xps.position(args.group))


if __name__ == "__main__":  # pragma: no cover
    _main()
