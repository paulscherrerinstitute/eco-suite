"""Serial driver for Schneider Electric / IMS MDrive & MForce motors (MCode).

This is a modern, EPICS-independent driver for the family of integrated
stepper motor + drive units historically sold by Intelligent Motion Systems
(IMS) and now by Schneider Electric Motion (MDrive, MForce, Lexium MDrive).
They speak the **MCode** ASCII language over an RS-232 / RS-422 / RS-485
serial line.

The driver supports both wiring topologies:

* **Single mode** - exactly one drive on the port.  Command lines are
  terminated with a carriage return (``\\r``) and no address prefix is used.
* **Party mode** - several drives share one RS-485 multi-drop bus, each with a
  one-character *device name* (``DN``, e.g. ``'1'`` .. ``'9'``, ``'a'`` .. ).
  Every command is prefixed with the device name and terminated with a line
  feed (``\\n`` / Ctrl-J), which is the byte that makes the addressed drive
  execute.  Use :class:`SchneiderBus` to share one serial port among several
  :class:`SchneiderMotor` instances.

Design goals
------------
* Pure standard library + ``pyserial``; no EPICS, no channel access.
* Full type hints, dataclass configuration, context-manager lifecycle.
* A small typed convenience API for the common moves *plus* generic
  :meth:`SchneiderMotor.get` / :meth:`SchneiderMotor.set` accessors so the
  entire MCode variable/flag space stays reachable.

Example
-------
Single drive on ``/dev/ttyUSB0``::

    from eco.devices_general.schneider_mcode import SchneiderMotor

    with SchneiderMotor.open_single("/dev/ttyUSB0", baudrate=9600) as m:
        print(m.firmware_version())
        m.move_absolute(10000)      # steps / microsteps
        m.wait()
        print(m.position)

Several party-mode drives on one RS-485 bus::

    from eco.devices_general.schneider_mcode import SchneiderBus

    with SchneiderBus("/dev/ttyUSB0", baudrate=115200, party=True) as bus:
        x = bus.motor("1")
        y = bus.motor("2")
        x.move_relative(500)
        y.move_relative(-500)
        bus.wait_all(x, y)

.. note::
   MCode details vary slightly between firmware revisions and product lines.
   The command mnemonics used here are the ones common to MForce/MDrive; if a
   particular unit disagrees, reach it through :meth:`SchneiderMotor.get` /
   :meth:`SchneiderMotor.set` and adjust the thin convenience wrappers.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

try:
    import serial  # type: ignore[import-untyped]
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    raise ModuleNotFoundError(
        "schneider_mcode requires pyserial. Install it with 'pip install pyserial'."
    ) from exc

__all__ = [
    "SchneiderError",
    "SchneiderTimeout",
    "SchneiderCommandError",
    "SerialConfig",
    "SchneiderBus",
    "SchneiderMotor",
]

log = logging.getLogger(__name__)

# --- MCode control characters -------------------------------------------------
_CR: Final = "\r"          # single-mode line terminator
_LF: Final = "\n"          # party-mode line terminator (Ctrl-J)
_ESC: Final = "\x1b"       # immediate motion stop
_CTRL_C: Final = "\x03"    # soft reset
_PROMPT_ERROR: Final = "?"  # MCode emits '?' when a command is rejected


class SchneiderError(Exception):
    """Base class for all Schneider/MCode driver errors."""


class SchneiderTimeout(SchneiderError):
    """No (complete) reply arrived from the drive within the read timeout."""


class SchneiderCommandError(SchneiderError):
    """The drive rejected a command or reported a non-zero error flag."""


@dataclass(slots=True)
class SerialConfig:
    """Serial-port settings for a Schneider MCode drive.

    Defaults match the IMS/Schneider factory configuration (9600 8N1).  Party
    mode buses are frequently reconfigured to 115200; set ``baudrate`` to match
    whatever ``BD`` the drives were saved with.
    """

    port: str
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    timeout: float = 1.0          # per-read timeout, seconds
    write_timeout: float = 1.0

    def open(self) -> "serial.Serial":
        """Open and return a configured :class:`serial.Serial` port."""
        return serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=self.bytesize,
            parity=self.parity,
            stopbits=self.stopbits,
            timeout=self.timeout,
            write_timeout=self.write_timeout,
        )


class SchneiderBus:
    """Owns one serial port shared by one or more MCode drives.

    In *party mode* several drives share the RS-485 bus and are addressed by
    their one-character device name.  In *single mode* the bus still works but
    holds a single unaddressed drive.

    The bus serialises all I/O with an internal lock so motors on the same port
    can be driven from multiple threads safely.
    """

    def __init__(
        self,
        port: str | SerialConfig,
        *,
        baudrate: int = 9600,
        party: bool = False,
        echo: bool = True,
        read_settle: float = 0.05,
        **serial_kwargs: object,
    ) -> None:
        """Create (but do not yet open) a bus.

        Parameters
        ----------
        port:
            Serial device path (e.g. ``"/dev/ttyUSB0"``, ``"COM3"``) or a
            fully populated :class:`SerialConfig`.
        baudrate:
            Ignored when *port* is a :class:`SerialConfig`.
        party:
            ``True`` for a multi-drop party-mode bus (commands are address
            prefixed and terminated with LF); ``False`` for a single drive.
        echo:
            Whether the drives echo received characters (MCode ``EM=0``,
            the factory default).  When ``True`` the echoed command is stripped
            from replies.  Set ``False`` if you have configured ``EM=1``.
        read_settle:
            Seconds to keep polling for more bytes after the first chunk of a
            reply arrives.  Covers the gap between the echo line and the value.
        """
        self.config = (
            port
            if isinstance(port, SerialConfig)
            else SerialConfig(port=port, baudrate=baudrate, **serial_kwargs)  # type: ignore[arg-type]
        )
        self.party = party
        self.echo = echo
        self.read_settle = read_settle
        self._ser: serial.Serial | None = None
        self._lock = threading.RLock()

    # -- lifecycle ------------------------------------------------------------
    def open(self) -> "SchneiderBus":
        """Open the serial port (idempotent)."""
        if self._ser is None or not self._ser.is_open:
            self._ser = self.config.open()
            log.info("Opened Schneider bus on %s @ %d baud",
                     self.config.port, self.config.baudrate)
        return self

    def close(self) -> None:
        """Close the serial port."""
        if self._ser is not None and self._ser.is_open:
            self._ser.close()
            log.info("Closed Schneider bus on %s", self.config.port)
        self._ser = None

    def __enter__(self) -> "SchneiderBus":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def serial(self) -> "serial.Serial":
        if self._ser is None or not self._ser.is_open:
            raise SchneiderError("Bus is not open; call open() or use a 'with' block.")
        return self._ser

    # -- motor factory --------------------------------------------------------
    def motor(self, address: str | None = None, *, name: str = "") -> "SchneiderMotor":
        """Return a :class:`SchneiderMotor` bound to this bus.

        ``address`` is the device-name character required in party mode and must
        be ``None`` in single mode.
        """
        if self.party and not address:
            raise ValueError("Party-mode bus requires a one-character address.")
        if not self.party and address:
            raise ValueError("Single-mode bus must not be given an address.")
        return SchneiderMotor(self, address, name=name or (address or "single"))

    # -- raw transaction ------------------------------------------------------
    def transact(self, address: str | None, command: str,
                 *, expect_reply: bool = True) -> str:
        """Send one MCode command line and return the parsed reply text.

        Handles address prefixing, terminator selection and echo stripping.
        Raises :class:`SchneiderCommandError` if the drive answers with ``'?'``.
        """
        term = _LF if self.party else _CR
        prefix = address if (self.party and address) else ""
        line = f"{prefix}{command}{term}"
        with self._lock:
            ser = self.serial
            ser.reset_input_buffer()
            ser.write(line.encode("ascii"))
            ser.flush()
            if not expect_reply:
                return ""
            raw = self._read_reply()
        return self._parse(raw, command)

    def send_immediate(self, address: str | None, ch: str) -> None:
        """Send a single control byte (ESC / Ctrl-C) with no terminator."""
        with self._lock:
            self.serial.write(((address or "") + ch).encode("ascii"))
            self.serial.flush()

    def _read_reply(self) -> str:
        """Read bytes until the drive goes quiet or a full line is buffered."""
        ser = self.serial
        chunks: list[bytes] = []
        first = ser.read(1)  # blocks up to timeout
        if not first:
            raise SchneiderTimeout("No response from drive.")
        chunks.append(first)
        deadline = time.monotonic() + self.read_settle
        while time.monotonic() < deadline:
            waiting = ser.in_waiting
            if waiting:
                chunks.append(ser.read(waiting))
                deadline = time.monotonic() + self.read_settle
            else:
                time.sleep(0.005)
        return b"".join(chunks).decode("ascii", errors="replace")

    def _parse(self, raw: str, command: str) -> str:
        """Strip echo, whitespace and prompts; validate; return the payload."""
        lines = [ln.strip() for ln in raw.replace(_CR, _LF).split(_LF)]
        lines = [ln for ln in lines if ln]
        if self.echo and lines and lines[0] == command.strip():
            lines = lines[1:]
        if any(ln == _PROMPT_ERROR for ln in lines):
            raise SchneiderCommandError(
                f"Drive rejected command {command!r} (reply: {raw!r})."
            )
        return " ".join(lines).strip()


class SchneiderMotor:
    """High-level MCode interface to a single Schneider/IMS/MForce drive.

    Instances are normally created via :meth:`SchneiderBus.motor` or the
    :meth:`open_single` convenience constructor.  All positions and velocities
    are in the drive's native (micro)step units unless you have scaled them on
    the controller.
    """

    def __init__(self, bus: SchneiderBus, address: str | None = None, *, name: str = "") -> None:
        self.bus = bus
        self.address = address
        self.name = name or (address or "schneider")

    # -- convenience constructor ---------------------------------------------
    @classmethod
    def open_single(cls, port: str, *, baudrate: int = 9600, echo: bool = True,
                    name: str = "schneider", **serial_kwargs: object) -> "SchneiderMotor":
        """Open a dedicated single-drive bus and return its motor.

        Closing the returned motor (``with`` block or :meth:`close`) closes the
        underlying port.
        """
        bus = SchneiderBus(port, baudrate=baudrate, party=False, echo=echo,
                           **serial_kwargs).open()  # type: ignore[arg-type]
        motor = cls(bus, None, name=name)
        motor._owns_bus = True
        return motor

    _owns_bus: bool = False

    def __enter__(self) -> "SchneiderMotor":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the bus if this motor owns it (single-drive convenience)."""
        if self._owns_bus:
            self.bus.close()

    def __repr__(self) -> str:
        addr = f" @{self.address}" if self.address else ""
        return f"<SchneiderMotor {self.name!r}{addr}>"

    # -- generic MCode access -------------------------------------------------
    def command(self, command: str, *, expect_reply: bool = True) -> str:
        """Send an arbitrary MCode command line and return the reply text."""
        return self.bus.transact(self.address, command, expect_reply=expect_reply)

    def get(self, param: str) -> str:
        """Read any MCode variable or flag via ``PR`` (e.g. ``get("P")``)."""
        return self.command(f"PR {param}")

    def get_int(self, param: str) -> int:
        return int(self.get(param))

    def get_float(self, param: str) -> float:
        return float(self.get(param))

    def set(self, param: str, value: object) -> None:
        """Assign any writable MCode variable (e.g. ``set("VM", 200000)``)."""
        self.command(f"{param}={value}", expect_reply=False)

    # -- identification -------------------------------------------------------
    def firmware_version(self) -> str:
        """Return the firmware/version string (``PR VR``)."""
        return self.get("VR")

    def part_number(self) -> str:
        """Return the part number (``PR PN``)."""
        return self.get("PN")

    def serial_number(self) -> str:
        """Return the serial number (``PR SN``)."""
        return self.get("SN")

    # -- position & motion state ---------------------------------------------
    @property
    def position(self) -> int:
        """Commanded position counter (``PR P``), in (micro)steps."""
        return self.get_int("P")

    @position.setter
    def position(self, value: int) -> None:
        """Redefine the current position without moving (``P=<value>``)."""
        self.set("P", int(value))

    @property
    def encoder(self) -> int:
        """Encoder counter (``PR C2``); requires an encoder-equipped drive."""
        return self.get_int("C2")

    @property
    def is_moving(self) -> bool:
        """``True`` while the drive reports motion (``PR MV``)."""
        return self.get_int("MV") != 0

    @property
    def velocity(self) -> int:
        """Current running velocity (``PR V``), steps/s."""
        return self.get_int("V")

    # -- moves ----------------------------------------------------------------
    def move_absolute(self, target: int) -> None:
        """Move to an absolute position (``MA <target>``)."""
        self.command(f"MA {int(target)}", expect_reply=False)

    def move_relative(self, distance: int) -> None:
        """Move by a relative distance (``MR <distance>``)."""
        self.command(f"MR {int(distance)}", expect_reply=False)

    def slew(self, velocity: int) -> None:
        """Run continuously at ``velocity`` steps/s (``SL <velocity>``)."""
        self.command(f"SL {int(velocity)}", expect_reply=False)

    def stop(self) -> None:
        """Immediately decelerate and stop motion (ESC)."""
        self.bus.send_immediate(self.address, _ESC)

    def soft_reset(self) -> None:
        """Issue a soft reset (Ctrl-C).  Reverts to power-up state."""
        self.bus.send_immediate(self.address, _CTRL_C)

    # -- motion parameters ----------------------------------------------------
    @property
    def initial_velocity(self) -> int:
        """Starting velocity ``VI`` (steps/s)."""
        return self.get_int("VI")

    @initial_velocity.setter
    def initial_velocity(self, value: int) -> None:
        self.set("VI", int(value))

    @property
    def max_velocity(self) -> int:
        """Maximum/slew velocity ``VM`` (steps/s)."""
        return self.get_int("VM")

    @max_velocity.setter
    def max_velocity(self, value: int) -> None:
        self.set("VM", int(value))

    @property
    def acceleration(self) -> int:
        """Acceleration ``A`` (steps/s^2)."""
        return self.get_int("A")

    @acceleration.setter
    def acceleration(self, value: int) -> None:
        self.set("A", int(value))

    @property
    def deceleration(self) -> int:
        """Deceleration ``D`` (steps/s^2)."""
        return self.get_int("D")

    @deceleration.setter
    def deceleration(self, value: int) -> None:
        self.set("D", int(value))

    @property
    def run_current(self) -> int:
        """Run current ``RC`` (percent of drive maximum)."""
        return self.get_int("RC")

    @run_current.setter
    def run_current(self, value: int) -> None:
        self.set("RC", int(value))

    @property
    def hold_current(self) -> int:
        """Hold current ``HC`` (percent of drive maximum)."""
        return self.get_int("HC")

    @hold_current.setter
    def hold_current(self, value: int) -> None:
        self.set("HC", int(value))

    @property
    def microstep_resolution(self) -> int:
        """Microstep resolution ``MS`` (microsteps per full step)."""
        return self.get_int("MS")

    @microstep_resolution.setter
    def microstep_resolution(self, value: int) -> None:
        self.set("MS", int(value))

    # -- homing ---------------------------------------------------------------
    def home(self, mode: int = 1) -> None:
        """Start a homing routine (``HM <mode>``).

        ``mode`` selects the drive's home sequence (see the MCode reference for
        the numbering on your firmware).  Follow with :meth:`wait`.
        """
        self.command(f"HM {int(mode)}", expect_reply=False)

    # -- error handling -------------------------------------------------------
    @property
    def error_flag(self) -> bool:
        """``True`` if the drive's error flag ``EF`` is set."""
        return self.get_int("EF") != 0

    @property
    def error_code(self) -> int:
        """Last error number ``ER`` (0 == no error)."""
        return self.get_int("ER")

    def clear_error(self) -> None:
        """Clear the error flag (``ER=0``)."""
        self.set("ER", 0)

    # -- persistence ----------------------------------------------------------
    def save(self) -> None:
        """Persist current parameters to non-volatile memory (``S``)."""
        self.command("S", expect_reply=False)

    # -- blocking helpers -----------------------------------------------------
    def wait(self, *, poll: float = 0.1, timeout: float | None = 60.0) -> None:
        """Block until motion completes.

        Raises :class:`SchneiderTimeout` if ``timeout`` seconds elapse first,
        and :class:`SchneiderCommandError` if the drive raises its error flag.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.is_moving:
            if deadline is not None and time.monotonic() > deadline:
                raise SchneiderTimeout(f"{self.name}: move did not finish in {timeout}s.")
            time.sleep(poll)
        if self.error_flag:
            raise SchneiderCommandError(f"{self.name}: error {self.error_code} after move.")

    def move_absolute_and_wait(self, target: int, **wait_kwargs: object) -> None:
        self.move_absolute(target)
        self.wait(**wait_kwargs)  # type: ignore[arg-type]

    def move_relative_and_wait(self, distance: int, **wait_kwargs: object) -> None:
        self.move_relative(distance)
        self.wait(**wait_kwargs)  # type: ignore[arg-type]


def wait_all(motors: Iterable[SchneiderMotor], *, poll: float = 0.1,
             timeout: float | None = 60.0) -> None:
    """Block until every motor in *motors* has stopped moving."""
    motors = list(motors)
    deadline = None if timeout is None else time.monotonic() + timeout
    while any(m.is_moving for m in motors):
        if deadline is not None and time.monotonic() > deadline:
            raise SchneiderTimeout("Timed out waiting for motors to stop.")
        time.sleep(poll)


# Attach wait_all to the bus for ergonomic use: bus.wait_all(x, y)
def _bus_wait_all(self: SchneiderBus, *motors: SchneiderMotor,
                  poll: float = 0.1, timeout: float | None = 60.0) -> None:
    wait_all(motors, poll=poll, timeout=timeout)


SchneiderBus.wait_all = _bus_wait_all  # type: ignore[attr-defined]


def _main() -> None:  # pragma: no cover - manual bench utility
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m eco.devices_general.schneider_mcode",
        description="Schneider/IMS MCode serial probe.",
    )
    p.add_argument("port", help="serial device, e.g. /dev/ttyUSB0")
    p.add_argument("-b", "--baud", type=int, default=9600)
    p.add_argument("-a", "--address", default=None, help="party-mode device name")
    p.add_argument("--no-echo", action="store_true", help="drive has EM=1 (echo off)")
    p.add_argument("cmd", nargs="*", help="MCode command to run, e.g. PR VR")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    party = args.address is not None
    with SchneiderBus(args.port, baudrate=args.baud, party=party,
                      echo=not args.no_echo) as bus:
        m = bus.motor(args.address)
        if args.cmd:
            print(m.command(" ".join(args.cmd)))
        else:
            print("firmware:", m.firmware_version())
            print("position:", m.position)
            print("moving:  ", m.is_moving)


if __name__ == "__main__":  # pragma: no cover
    _main()
