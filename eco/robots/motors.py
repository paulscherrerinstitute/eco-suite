"""Pseudo-motors -- the port of pshell's ``RobotMotors.py``.

Each motor is a single-axis view onto a coordinate system the arm does not
actually have motors for. Writing one axis has to be turned into a whole-pose
move, which is where all the subtlety lives:

* **Setpoint memory.** ``target_pos`` is the union of every axis' last
  setpoint, defaulting to the live readback for axes never written. Moving
  ``gamma`` alone must not reset ``r`` to wherever the arm happens to be
  mid-flight.
* **Move coalescing.** A client scanning two axes writes them back-to-back.
  If the previous motion was in the *same* coordinate system, the second write
  discards the queued motion (``resetMotion``) and re-issues a single combined
  move to the merged target -- "Multiple remote motion commands are now merged
  if issued in the same coordinate system" in the pshell history. Without it
  the arm would trace an L instead of a diagonal.
* **Coordinate-system switches.** Crossing from e.g. spherical to cartesian
  drops any queued motion first, because the queued one was computed in a
  frame the new command knows nothing about.

Relative to the original these classes take their robot from ``self.robot``
throughout. The pshell version reached for a module-global ``robot`` in about
half its methods (``robot.move_cartesian(...)``, ``robot.evaluate(...)``,
``robot.cartesian_motors`` inside ``target_pos``), which happened to work
because exactly one robot existed in the process.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

#: Axes are considered "changed" beyond this many mm/deg. Matches the
#: original's `round(a, 2) != round(b, 2)`.
CHANGE_EPSILON = 0.005


class PseudoMotor:
    """One axis of a robot coordinate system.

    Subclasses supply :attr:`group` (the robot's motor-group key) and
    :meth:`_issue_move`.
    """

    group = None
    #: axis name -> engineering unit, published as the EPICS ``.EGU`` field and
    #: used by clients for display. The pshell version left these empty.
    units = {}
    default_unit = None

    def __init__(self, robot, name, unit=None):
        self.robot = robot
        self.name = name
        self.unit = unit or self.units.get(name, self.default_unit)
        self.setpoint = float("nan")
        self._value = float("nan")
        self._callbacks = []

    def __repr__(self):
        return (f"<{type(self).__name__} {self.name}="
                f"{self.get_readback():.4g} -> {self.setpoint:.4g}>")

    # ------------------------------------------------------------- readback

    @property
    def _positions(self):
        """Live readbacks for this motor's coordinate system."""
        return getattr(self.robot, f"{self.group}_pos")

    @property
    def _destination(self):
        """The robot's last commanded pose for this coordinate system."""
        return getattr(self.robot, f"{self.group}_destination")

    def get_readback(self) -> float:
        positions = self._positions
        if not positions or self.name not in positions:
            return float("nan")
        return float(positions[self.name])

    def get_current_value(self) -> float:
        return self.get_readback()

    def update_readback(self):
        value = self.get_readback()
        changed = not (
            math.isnan(value) and math.isnan(self._value)
        ) and value != self._value
        self._value = value
        if changed:
            for callback in list(self._callbacks):
                try:
                    callback(value)
                except Exception:
                    logger.exception("value callback failed for %s", self.name)

    def add_value_callback(self, callback):
        self._callbacks.append(callback)
        return callback

    def clear_value_callback(self, callback=None):
        if callback is None:
            self._callbacks.clear()
        elif callback in self._callbacks:
            self._callbacks.remove(callback)

    def initialize(self):
        """Re-seed the setpoint from the readback.

        Must run before scanning, and after anything that invalidates the
        queued motions (``reset_motion``, re-enabling the group).
        """
        self.setpoint = self.get_readback()
        self._value = self.setpoint

    # -------------------------------------------------------------- targets

    @property
    def target_pos(self):
        """Merged target across the whole coordinate system."""
        return {
            name: motor.setpoint
            for name, motor in self.robot.motor_groups[self.group].items()
        }

    @target_pos.setter
    def target_pos(self, value):
        positions = self._positions
        for name, motor in self.robot.motor_groups[self.group].items():
            if name in value:
                motor.setpoint = value[name]
            else:
                motor.setpoint = positions[name]

    # ----------------------------------------------------------------- move

    def move(self, value, sync=False):
        """Write one axis; coalesce or switch coordinate systems as needed."""
        if self.group != "joint" and self._destination is None:
            raise RuntimeError(
                f"{self.group} motors are not enabled; "
                f"call robot.set_motors_enabled(True, ['{self.group}']) first"
            )
        value = float(value)
        self.setpoint = value
        positions = self._positions
        merged = self.target_pos
        changed = {
            name: [positions[name], merged[name]]
            for name in merged
            if abs(positions[name] - merged[name]) > CHANGE_EPSILON
        }
        last = self.robot.last_remote_motion

        if len(changed) > 1 and last == self.group:
            # Same coordinate system, several axes pending: replace whatever is
            # queued with one combined move to the merged target. The queue-only
            # reset is essential -- the full reset_motion() re-seeds setpoints
            # from the readbacks and would discard the merge in progress.
            self.robot.reset_motion_queue()
            target = merged
            self.on_combined_motion(changed)
        else:
            if last != self.group:
                if not self.robot.empty:
                    self.robot.reset_motion_queue()
                self.on_coordinate_system_change(last, self.group)
            target = {self.name: value}
            self.target_pos = target
        logger.info("%s: moving to %s", self.group, target)
        result = self._issue_move(target, sync=sync)
        self.robot.last_remote_motion = self.group
        return result

    def moveAsync(self, value):
        """pshell-compatible alias; the move is queued, not awaited."""
        return self.move(value, sync=False)

    def _issue_move(self, target, sync=False):
        raise NotImplementedError

    def stop(self):
        self.robot.stop()
        self.robot.reset_motion()
        self.robot.resume()

    # ---------------------------------------------------------------- hooks

    def on_combined_motion(self, changed):
        lines = "".join(
            f"{name:8} {old:8.3f}  -->  {new}\n" for name, (old, new) in changed.items()
        )
        self.robot.emit(
            "motion", f"Combining {len(changed)} motions to new target:\n{lines}"
        )

    def on_coordinate_system_change(self, old, new):
        self.robot.emit(
            "motion", f"User changed coordinate system {old}  -->  {new}:\n"
        )


class CartesianMotor(PseudoMotor):
    group = "cartesian"
    units = {"x": "mm", "y": "mm", "z": "mm",
             "rx": "deg", "ry": "deg", "rz": "deg"}

    def _issue_move(self, target, sync=False):
        return self.robot.move_cartesian(sync=sync, **target)


class SphericalMotor(PseudoMotor):
    group = "spherical"
    #: `r` is the sample-to-detector distance; eco surfaces it as `t_det`.
    units = {"r": "mm", "gamma": "deg", "delta": "deg"}

    def _issue_move(self, target, sync=False):
        return self.robot.move_spherical(sync=sync, **target)


class JointMotor(PseudoMotor):
    group = "joint"
    default_unit = "deg"

    def _issue_move(self, target, sync=False):
        return self.robot.move_joint(sync=sync, **target)


class LinearAxisMotor(PseudoMotor):
    """The rail the arm is mounted on -- a real servo, driven over EtherCAT.

    Unlike the other three this is not a pseudo-motor at all: there is no
    kinematics, the setpoint goes straight to an analogue output. It shares
    the class only for a uniform interface.
    """

    group = "linear_axis"
    default_unit = "mm"

    def move(self, value, sync=False):
        if self._destination is None:
            raise RuntimeError(
                "linear axis motor is not enabled; call "
                "robot.set_motors_enabled(True, ['linear']) first"
            )
        value = float(value)
        self.setpoint = value
        self.robot.linear_axis_destination[self.name] = value
        # Setpoint and strobe are two separate VAL3 statements; the
        # transaction keeps the poller from reading a half-applied setpoint.
        with self.robot.link.transaction():
            self.robot.link.evaluate(f"n_moveAbsPos = {value}")
            self.robot.link.evaluate("setEcAo(ecao_posSet,n_moveAbsPos)")
        self.robot.last_remote_motion = self.group
        return value

    def _issue_move(self, target, sync=False):  # pragma: no cover - unused
        raise NotImplementedError

    def stop(self):
        """Pulse the drive's stop input; it is edge- not level-triggered."""
        with self.robot.link.transaction():
            self.robot.link.evaluate("setEcDo(ecdo_linMotStop,true)")
        self.robot.cancellation.sleep(0.1)
        with self.robot.link.transaction():
            self.robot.link.evaluate("setEcDo(ecdo_linMotStop,false)")


MOTOR_CLASSES = {
    "cartesian": CartesianMotor,
    "spherical": SphericalMotor,
    "joint": JointMotor,
    "linear_axis": LinearAxisMotor,
}
