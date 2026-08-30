"""EPICS channel-access server for the robot axes.

The pshell deployment published every pseudo-motor as a fake motor record
(``make_archivable`` in ``RobotBernina.py``) using pshell's own Java CA server.
The point was never a full motor record: it was to make the axes look enough
like one that

* the PSI **archiver** would accept and log them (it keys off ``.RTYP``,
  ``.EGU``, ``.ADEL``, ``.MDEL``, ``.PREC``), and
* the existing **caqtdm** panel
  (``/sf/bernina/config/src/caqtdm/robot/robot.ui``) would drive them -- it
  binds ``$(P):$(M).VAL`` / ``.RBV`` with ``P=SARES20-ROB``,
  ``M=J1..J6, X, Y, Z, RX, RY, RZ, R, GAMMA, DELTA, Z_LIN``.

so the same field set and the same **upper-case** names are reproduced here on
top of :mod:`pcaspy`. Writing ``.VAL`` moves the axis; everything else is a
constant or a readback.

pcaspy is optional: if it is missing, or if a CA server cannot be started, the
robot server logs and carries on. HTTP clients (eco's ``bernina.rob``) do not
use these PVs at all -- they read positions from the SSE ``polling`` payload.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from pcaspy import Driver, SimpleServer
    from pcaspy.tools import ServerThread
    PCASPY_AVAILABLE = True
except ImportError:                                    # pragma: no cover
    Driver = object
    SimpleServer = ServerThread = None
    PCASPY_AVAILABLE = False


#: Constant motor-record fields, copied from the original ``make_archivable``
#: ``pv_def``. Values that never change; present so archiver and caqtdm see a
#: record shaped the way they expect.
STATIC_FIELDS = {
    ".ADEL": ("float", -1.0),   # archive deadband; -1 == archive every value
    ".MDEL": ("float", 0.0),
    ".RTYP": ("string", "motor"),
    ".SCAN": ("string", "passive"),
    ".PINI": ("string", "No"),
    ".PHAS": ("short", 0),
    ".PREC": ("short", 3),
    ".EVNT": ("string", ""),
    ".PRIO": ("short", 0),
    ".DISV": ("short", 1),
    ".DISA": ("short", 0),
    ".SDIS": ("string", ""),
    ".PROC": ("short", 0),
    ".DISS": ("short", 0),
    ".LCNT": ("short", 0),
    ".PACT": ("short", 0),
    ".ASG": ("string", ""),
    ".TSE": ("short", 0),
    ".TSEL": ("string", ""),
    ".DTYP": ("string", "asynMotor"),
    ".DISP": ("float", 0.0),
    ".PUTF": ("float", 0.0),
    ".PPRO": ("float", 0.0),
    ".TPRO": ("float", 0.0),
    ".STAT": ("short", 0),
    ".SEVR": ("short", 0),
    ".NSTA": ("short", 0),
    ".NSEV": ("short", 0),
    ".ACKS": ("short", 2),
    ".ACKT": ("short", 0),
}

#: Fields backed by live robot values rather than constants.
LIVE_FIELDS = ("", ".VAL", ".RBV", ".EGU", ".NAME", ".DESC", ".FLNK")

#: Only ``.VAL`` actually commands a motion.
WRITABLE_FIELDS = (".VAL",)


def build_pvdb(motor_names, prefix):
    """PV name (without prefix) -> pcaspy field spec, for every axis."""
    pvdb = {}
    for name in motor_names:
        base = name.upper()
        pvdb[base] = {"type": "float", "prec": 3}          # readback alias
        pvdb[base + ".VAL"] = {"type": "float", "prec": 3}
        pvdb[base + ".RBV"] = {"type": "float", "prec": 3}
        pvdb[base + ".EGU"] = {"type": "string"}
        pvdb[base + ".NAME"] = {"type": "string"}
        pvdb[base + ".DESC"] = {"type": "string"}
        pvdb[base + ".FLNK"] = {"type": "string"}
        for field, (ftype, _) in STATIC_FIELDS.items():
            pvdb[base + field] = {"type": ftype}
    return pvdb


class RobotDriver(Driver):
    """Serves axis readbacks and turns ``.VAL`` writes into moves."""

    def __init__(self, robot, motor_names, prefix):
        super().__init__()
        self.robot = robot
        self.prefix = prefix
        self.motor_names = list(motor_names)
        for name in self.motor_names:
            base = name.upper()
            motor = self.robot.motors.get(name)
            unit = getattr(motor, "unit", None) or ""
            self.setParam(base + ".EGU", unit)
            self.setParam(base + ".NAME", prefix + name)
            self.setParam(base + ".DESC", prefix + name)
            self.setParam(base + ".FLNK", (prefix + name + ".RBV").upper())
            for field, (_, value) in STATIC_FIELDS.items():
                self.setParam(base + field, value)
        self.updatePVs()

    def publish(self, positions):
        """Push one poll's worth of readbacks into the CA server."""
        for name in self.motor_names:
            value = positions.get(name)
            if value is None:
                continue
            base = name.upper()
            self.setParam(base, value)
            self.setParam(base + ".RBV", value)
            motor = self.robot.motors.get(name)
            # .VAL tracks the setpoint, so a client that reads it back sees
            # where the axis was told to go, not where it currently is.
            setpoint = getattr(motor, "setpoint", None)
            if setpoint is not None and setpoint == setpoint:   # not NaN
                self.setParam(base + ".VAL", float(setpoint))
        self.updatePVs()

    def write(self, reason, value):
        for field in WRITABLE_FIELDS:
            if not reason.endswith(field):
                continue
            axis = reason[: -len(field)].lower()
            motor = self.robot.motors.get(axis)
            if motor is None:
                logger.warning("CA write to %s: no such enabled motor", reason)
                return False
            try:
                motor.move(float(value))
            except Exception:
                logger.exception("CA write %s = %r failed", reason, value)
                return False
            self.setParam(reason, value)
            return True
        logger.debug("CA write to read-only field %s ignored", reason)
        return False


class EpicsPublisher:
    """Owns the CA server thread. A no-op when pcaspy is unavailable."""

    def __init__(self, robot, prefix="SARES20-ROB:"):
        self.robot = robot
        self.prefix = prefix
        self.server = None
        self.thread = None
        self.driver = None
        self.motor_names = []

    @property
    def running(self) -> bool:
        return self.thread is not None

    def start(self):
        if not PCASPY_AVAILABLE:
            logger.warning("pcaspy is not installed; EPICS PVs are disabled")
            return False
        self.motor_names = sorted(self.robot.motors)
        if not self.motor_names:
            logger.warning("no motors are enabled; EPICS PVs are disabled")
            return False
        try:
            self.server = SimpleServer()
            self.server.createPV(self.prefix,
                                 build_pvdb(self.motor_names, self.prefix))
            self.driver = RobotDriver(self.robot, self.motor_names, self.prefix)
            self.thread = ServerThread(self.server)
            self.thread.start()
        except Exception:
            logger.exception("could not start the EPICS CA server")
            self.server = self.driver = self.thread = None
            return False
        logger.info("EPICS CA server up: %s{%s}",
                    self.prefix, ",".join(n.upper() for n in self.motor_names))
        return True

    def publish(self, positions):
        if self.driver is not None:
            try:
                self.driver.publish(positions)
            except Exception:
                logger.exception("publishing EPICS values failed")

    def stop(self):
        if self.thread is not None:
            try:
                self.thread.stop()
            except Exception:
                logger.exception("stopping the EPICS CA server failed")
            self.thread = None
        self.server = self.driver = None

    def pv_names(self):
        return [self.prefix + n.upper() for n in self.motor_names]
