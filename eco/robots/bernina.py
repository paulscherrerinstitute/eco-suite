"""The Bernina detector arm -- the port of pshell's ``RobotBernina.py``.

A Stäubli TX200 hangs from the ceiling on a linear rail and carries a Jungfrau
detector. On top of the generic driver this module adds:

* **Spherical detector coordinates** (r, gamma, delta) -- see
  :mod:`eco.robots.kinematics`. A spherical move is decomposed into a radial
  ``movel`` followed by a ``movec`` along the sphere, so the detector sweeps an
  arc at constant distance instead of cutting a chord through the sample.
* **Frame/tool hierarchy** -- the dotted paths clients display and select.
* **The remote-motion whitelist** -- in ``remote`` working mode the arm will
  only execute a motion whose start *and* end lie on a trajectory an operator
  previously drove by hand with the pendant. See :meth:`remote_allowed`.
* **Motion simulation** -- interpolated poses obtained from the controller's
  own kinematics, used to preview a move in the URDF viewer before running it.
"""

from __future__ import annotations

import logging

import numpy as np

from .kinematics import cart2sph, deg2rad, rad2deg, sph2cart
from .motors import (
    CartesianMotor,
    JointMotor,
    LinearAxisMotor,
    SphericalMotor,
)
from .persistence import JsonValue
from .staeubli import (
    CARTESIAN_AXES,
    JOINT_AXES,
    RobotError,
    RobotState,
    StaeubliRobot,
)

logger = logging.getLogger(__name__)

PVBASE = "SARES20-ROB:"
P_PARK, P_HOME = "park", "home"
KNOWN_POINTS = [P_PARK, P_HOME]
MOVE_PARK, MOVE_HOME, TWEAK_X, TWEAK_Y = "movePark", "moveHome", "tweakX", "tweakY"
TASKS = [MOVE_PARK, MOVE_HOME, TWEAK_X, TWEAK_Y]

DESC_FAST = DESC_SLOW = "mNomSpeed"
DESC_DEFAULT = DESC_FAST

DEFAULT_ROBOT_POLLING = 0.2      # s; pshell said 200 ms
TASK_WAIT_ROBOT_POLLING = 0.05   # s; pshell said 50 ms
DEFAULT_SPEED = 100

SPHERICAL_AXES = ["r", "gamma", "delta"]
LINEAR_AXES = ["z_lin"]

#: Dotted parent chains, used to present a frame/tool as a path. A bare symbol
#: that appears in one of these expands to everything up to and including it.
FRAME_HIERARCHY = [
    "world.f_SwissFELCoord.f_ESBWorld.f_linearAxis." + leaf
    for leaf in ("f_direct.", "f_2mRad.", "f_4mRad.", "f_6mRad.", "f_8mRad.")
] + [
    "t_flange.t_Adapter." + leaf
    for leaf in ("t_JF01T03det.t_JF01T03.", "t_JF07T32det.t_JF07T32.")
]

DEFAULT_REMOTE_ALLOWED_DELTAS = {
    "spherical": {"r": 50.0, "gamma": 1.0, "delta": 1.0},
    "cartesian": {"x": 1.0, "y": 1.0, "z": 1.0, "rx": 1.0, "ry": 1.0, "rz": 1.0},
    "joint": {f"j{i}": 1.0 for i in range(1, 7)},
}
#: Tolerance on each of the six frame/tool transformation components when
#: matching a recorded trajectory. The original reused the *motion* deltas
#: here, which for spherical meant three tolerances for six components -- so
#: rx/ry/rz of the frame were silently never compared. See remote_allowed().
DEFAULT_TRSF_TOLERANCE = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]

#: Shape of one coordinate system's recording store. Built by a factory, never
#: shared: the original wrote
#:     temp = {"frame": Array([]), ...}
#:     {"spherical": temp, "cartesian": temp, "joint": temp}
#: i.e. all three coordinate systems aliased ONE dict holding ONE set of lists.
#: Appending a spherical recording therefore also registered it as a cartesian
#: and a joint one; after the next JSON round trip those became three real
#: copies, so a 3-column spherical path ended up being range-checked against
#: 6-element joint coordinates. See remote_allowed() for the guard.
def empty_recording():
    return {"frame": [], "tool": [], "pos": []}

#: Points per interval when densifying a recorded trajectory.
RECORDING_INTERPOLATION = 10


def _empty_recordings():
    return {kind: empty_recording()
            for kind in ("spherical", "cartesian", "joint")}


class BerninaRobot(StaeubliRobot):

    def __init__(self, name="robot", link=None, adjustables_path=None,
                 override_remote_safety=False, value_factory=JsonValue,
                 **kwargs):
        self._value_factory = value_factory
        self._adjustables_path = adjustables_path

        tool = self._value("tool", "t_JF01T03")
        frame = self._value("frame", "f_4mRad")
        super().__init__(name=name, link=link, tool=tool, frame=frame,
                         default_desc=DESC_DEFAULT,
                         default_speed=DEFAULT_SPEED,
                         polling_interval=DEFAULT_ROBOT_POLLING, **kwargs)
        self._tool_persistent = self._value("tool_persistent", "t_JF01T03")
        self._frame_persistent = self._value("frame_persistent", "f_4mRad")

        self.set_tasks(TASKS)
        self.set_known_points(KNOWN_POINTS)

        self.override_remote_safety = bool(override_remote_safety)
        self.last_remote_motion = "None"
        self.spherical_pos = {}
        self.cartesian_destination = None
        self.spherical_destination = None
        self.linear_axis_destination = None
        self.joint_destination = None

        self.remote_allowed_recorded = self._value(
            "remote_allowed", _empty_recordings()
        )
        self.remote_allowed_deltas = self._value(
            "remote_allowed_deltas", DEFAULT_REMOTE_ALLOWED_DELTAS
        )
        self.trsf_tolerance = list(DEFAULT_TRSF_TOLERANCE)

        for group in ("cartesian", "spherical", "joint", "linear_axis"):
            self.motor_groups[group] = {}
            self.motors_enabled[group] = False

    def _value(self, name, default):
        if self._value_factory is JsonValue:
            return JsonValue(name=name, default_value=default,
                             base_path=self._adjustables_path)
        return self._value_factory(name=name, default_value=default)

    # ------------------------------------------------------------ hierarchy

    def get_frame_tree(self, frame):
        """Expand a bare frame/tool symbol to its dotted path."""
        for chain in FRAME_HIERARCHY:
            if frame in chain:
                return chain.split("." + frame + ".")[0] + "." + frame
        return frame

    # ---------------------------------------------------------- coordinates

    def get_spherical_pos(self, frame=None, return_dict=True):
        current = self.get_cartesian_pos(frame=frame, return_dict=True)
        return self.cart2sph(return_dict=return_dict, **current)

    def update_spherical_pos(self):
        if self.cartesian_pos:
            self.spherical_pos = self.cart2sph(return_dict=True,
                                               **self.cartesian_pos)

    def sph2cart(self, r=None, gamma=None, delta=None, return_dict=True, **kwargs):
        return sph2cart(r=r, gamma=gamma, delta=delta,
                        return_dict=return_dict, **kwargs)

    def cart2sph(self, x=None, y=None, z=None, return_dict=True, **kwargs):
        # On the y axis gamma is undefined; fall back to the live ry readback
        # as the original did, but without the KeyError when no poll has landed.
        kwargs.setdefault("ry_fallback", self.cartesian_pos.get("ry", 0.0))
        return cart2sph(x=x, y=y, z=z, return_dict=return_dict, **kwargs)

    def deg2rad(self, angles):
        return deg2rad(angles)

    def rad2deg(self, angles):
        return rad2deg(angles)

    def get_linear_axis_pos(self, return_dict=True):
        value = self.link.get_float("n_actPosLinAx")
        return {"z_lin": value} if return_dict else value

    def get_spherical_destination(self, tool=None, frame=None):
        return self.get_spherical_pos(frame=frame)

    def on_positions_updated(self):
        self.update_spherical_pos()

    def on_environment_updated(self):
        self.update_spherical_pos()

    def positions(self):
        pos = super().positions()
        pos.update(self.spherical_pos)
        return pos

    def check_calculations(self):
        """Cross-check the local kinematics against the controller's own."""
        spherical = self.get_spherical_pos()
        calculated = self.sph2cart(**spherical)
        actual = self.get_cartesian_pos(return_dict=True)
        return {k: actual[k] - calculated[k] for k in calculated}

    def check_software_limits(self, j1=0, j2=0, j3=0, j4=0, j5=0, j6=0) -> bool:
        with self.link.transaction():
            self.link.set_jnt([j1, j2, j3, j4, j5, j6], name="tcp_j")
            self.link.evaluate("tcp_b=isInRange(tcp_j)")
            return self.link.get_bool(name="tcp_b")

    def point_reachable(self, point, tool=None, verbose=True) -> bool:
        tool = self.tool() if tool is None else tool
        self.herej()
        reachable = self.link.eval_bool(
            f"pointToJoint({tool},tcp_j,{point},j)"
        )
        if verbose and not reachable:
            self.emit("motion", "Point is not in range")
            logger.warning("point %s is not in range", point)
        return reachable

    # ------------------------------------------------------- interpolation

    def get_movel_interpolation(self, fraction, coordinates="joint",
                                idx_pnt1=0, idx_pnt2=1):
        """Pose at ``fraction`` along the straight line between two scratch points.

        ``coordinates`` is "joint" or "cartesian"; 0 is the start, 1 the end.
        Computed by the controller, so it reflects the real kinematics.
        """
        raw = self.link.execute("get_movel_interpolation", fraction, coordinates,
                                idx_pnt1, idx_pnt2)
        return [float(v.strip()) for v in raw[:6]]

    def get_movec_interpolation(self, fraction, coordinates="joint"):
        """Pose at ``fraction`` along the arc through scratch points 1, 2, 3."""
        raw = self.link.execute("get_movec_interpolation", fraction, coordinates)
        return [float(v.strip()) for v in raw[:6]]

    # ------------------------------------------------------ remote safety

    def set_override_remote_safety(self, value):
        """Disable the recorded-trajectory whitelist. Operator decision."""
        self.override_remote_safety = bool(value)
        if self.override_remote_safety:
            logger.warning("remote motion safety whitelist DISABLED for %s",
                           self.name)
            self.emit("motion",
                      "Remote motion safety check has been OVERRIDDEN. Every "
                      "remote motion is now permitted regardless of recorded "
                      "trajectories.")
        return self.override_remote_safety

    def reset_recorded_motions(self, index=None, motion=None):
        if index is None or motion is None:
            self.remote_allowed_recorded(_empty_recordings())
            return "removed all recorded motions"
        recorded = self.remote_allowed_recorded()
        for key in ("pos", "tool", "frame"):
            # The original wrote `.pop[index]` (subscript, not call) here,
            # which raised TypeError for every single-motion removal.
            recorded[motion][key].pop(index)
        self.remote_allowed_recorded(recorded)
        return f"removed recorded {motion} motion with index {index}"

    def _axes_for(self, motion):
        return {"cartesian": CARTESIAN_AXES, "spherical": SPHERICAL_AXES,
                "joint": JOINT_AXES}[motion]

    def remote_allowed(self, from_coordinates, to_coordinates, frame_trsf,
                       tool_trsf, motion) -> bool:
        """Is this motion covered by a hand-recorded trajectory?

        A motion is permitted when some recorded trajectory -- taken with a
        matching frame and tool -- passes within ``remote_allowed_deltas`` of
        *both* the start and the target pose. That is deliberately weaker than
        "the whole path is recorded": the controller interpolates between the
        two poses itself, and a recorded trajectory that brackets both ends is
        the practical evidence the operator has driven that corridor by hand.

        Deviations from the pshell version, both bugs there:

        * Frame/tool components are compared against
          :attr:`trsf_tolerance` (6 values) rather than against the *motion*
          deltas. For spherical motions the original supplied only three
          tolerances for six components, so a recorded trajectory matched
          regardless of the frame's rx/ry/rz.
        * Joint motions skip frame/tool matching entirely. The original passed
          ``frame_trsf=None`` for joint motions and then subtracted it from an
          array, so any joint motion with a recording present raised TypeError,
          and with no recording present was simply always refused.
        """
        if self.override_remote_safety:
            return True
        axes = self._axes_for(motion)
        start = np.array([from_coordinates[k] for k in axes], dtype=float)
        target = np.array([to_coordinates[k] for k in axes], dtype=float)
        deltas = np.array([self.remote_allowed_deltas()[motion][k] for k in axes],
                          dtype=float)

        recorded = self.remote_allowed_recorded().get(motion) or empty_recording()
        positions = recorded.get("pos") or []
        if not positions:
            return False
        frames = recorded.get("frame") or [None] * len(positions)
        tools = recorded.get("tool") or [None] * len(positions)
        trsf_tol = np.array(self.trsf_tolerance, dtype=float)

        for trajectory, rec_frame, rec_tool in zip(positions, frames, tools):
            if motion != "joint":
                if not self._trsf_matches(rec_frame, frame_trsf, trsf_tol):
                    continue
                if not self._trsf_matches(rec_tool, tool_trsf, trsf_tol):
                    continue
            path = np.asarray(trajectory, dtype=float)
            if path.ndim != 2 or path.shape[1] != len(axes):
                logger.warning("skipping recorded %s trajectory of shape %s "
                               "(expected (N, %d))", motion, path.shape, len(axes))
                continue
            near_start = np.all(np.abs(path - start) < deltas, axis=1).any()
            near_target = np.all(np.abs(path - target) < deltas, axis=1).any()
            if near_start and near_target:
                return True
        return False

    @staticmethod
    def _trsf_matches(recorded, current, tolerance) -> bool:
        if recorded is None or current is None:
            # Nothing recorded to compare against: do not use it as evidence.
            return False
        return bool(np.all(np.abs(np.asarray(recorded, dtype=float)
                                  - np.asarray(current, dtype=float)) < tolerance))

    def _assert_remote_allowed(self, current, target, motion, frame=None,
                               tool=None):
        """Returns True when the move may proceed."""
        if self.working_mode != "remote":
            return True
        frame_trsf = self.link.get_trsf(frame + ".trsf") if frame else None
        tool_trsf = self.link.get_trsf(tool + ".trsf") if tool else None
        if self.remote_allowed(current, target, frame_trsf=frame_trsf,
                               tool_trsf=tool_trsf, motion=motion):
            return True
        self.emit("motion",
                  "Requested remote motion is not in recorded trajectories. "
                  "Use rob.record_motion(*args) to record the motion in manual "
                  "working mode first.")
        return False

    # ------------------------------------------------------------- motions

    def _dispatch_motion(self, kwargs):
        """Pick the coordinate system from the keywords, refusing a mix."""
        matches = {
            "joint": any(k in kwargs for k in JOINT_AXES),
            "cartesian": any(k in kwargs for k in CARTESIAN_AXES),
            "spherical": any(k in kwargs for k in SPHERICAL_AXES),
        }
        chosen = [name for name, hit in matches.items() if hit]
        if len(chosen) != 1:
            self.emit("motion",
                      "\nNo unique coordinate system found for these motions, "
                      "use only unique keywords out of:\n"
                      f" {JOINT_AXES}\n {CARTESIAN_AXES}\n {SPHERICAL_AXES}.")
            return None, None
        name = chosen[0]
        return name, {"joint": self.move_joint,
                      "cartesian": self.move_cartesian,
                      "spherical": self.move_spherical}[name]

    def general_motion(self, **kwargs):
        """Move in whichever coordinate system the keywords name, and wait."""
        self.reset_motion()
        name, move = self._dispatch_motion(kwargs)
        if move is None:
            return None
        # Force the next pseudo-motor write to be treated as a coordinate
        # system change, so it re-seeds instead of coalescing onto this move.
        self.last_remote_motion = "recording"
        move(sync=True, **kwargs)
        return True

    def move_cartesian(self, x=None, y=None, z=None, rx=None, ry=None, rz=None,
                       tool=None, frame=None, desc=None, sync=False,
                       simulate=False, coordinates="joint"):
        """Straight-line motion in cartesian coordinates.

        ``simulate=True`` returns 11 interpolated poses (in ``coordinates``,
        "joint" or "cartesian") from the controller instead of moving.
        """
        frame = self.frame() if frame is None else frame
        tool = self.tool() if tool is None else tool
        desc = self.default_desc if desc is None else desc
        self.set_frame(frame, name="tcp_f_spherical", change_default=False)

        current = self.get_cartesian_pos(frame=frame, return_dict=True)
        target = {"x": x, "y": y, "z": z, "rx": rx, "ry": ry, "rz": rz}
        target = {k: (current[k] if v is None else v) for k, v in target.items()}

        # `& (~simulate)` in the original was a bitwise not on a bool: ~False
        # is -1 and ~True is -2, both truthy, so the safety check also ran on
        # simulated moves -- and refused them in remote mode.
        if not simulate and not self._assert_remote_allowed(
            current, target, "cartesian", frame=frame, tool=tool
        ):
            return False

        self.cartesian_destination = target
        with self.link.transaction():
            self.link.set_pnt([current[k] for k in CARTESIAN_AXES],
                              name="tcp_p_spherical[0]")
            self.link.set_pnt([target[k] for k in CARTESIAN_AXES],
                              name="tcp_p_spherical[1]")
            if not self.point_reachable("tcp_p_spherical[1]", verbose=True):
                return False
            if simulate:
                return [self.get_movel_interpolation(i / 10.0,
                                                     coordinates=coordinates)
                        for i in range(11)]
            self.set_motion_queue_empty(False)
            return self.movel("tcp_p_spherical[1]", tool=tool, desc=None,
                              sync=sync)

    def move_spherical(self, r=None, gamma=None, delta=None, tool=None,
                       frame=None, desc=None, sync=False, simulate=False,
                       coordinates="joint"):
        """Motion in spherical detector coordinates.

        Decomposed into a radial ``movel`` (change r at constant direction)
        followed by a ``movec`` along the sphere through a mid-point (change
        gamma/delta at constant r). If the arm is not currently sitting on a
        spherical pose -- because a cartesian move left the detector pointing
        somewhere else -- the whole thing degrades to a single cartesian move
        to the equivalent pose, since arcing from an off-sphere start is
        meaningless.
        """
        frame = self.frame() if frame is None else frame
        tool = self.tool() if tool is None else tool
        desc = self.default_desc if desc is None else desc
        self.set_frame(frame, name="tcp_f_spherical", change_default=False)

        current_cart = self.get_cartesian_pos(frame=frame, return_dict=True)
        current_sph = self.get_spherical_pos(frame=frame, return_dict=True)
        target_sph = {"r": r, "gamma": gamma, "delta": delta}
        target_sph = {k: (current_sph[k] if v is None else v)
                      for k, v in target_sph.items()}
        self.spherical_destination = target_sph

        if not simulate and not self._assert_remote_allowed(
            current_sph, target_sph, "spherical", frame=frame, tool=tool
        ):
            return False

        # Is the current pose actually on the sphere (position AND orientation)?
        expected = self.sph2cart(**self.cart2sph(**current_cart))
        on_sphere = all(
            abs(current_cart[k] - expected[k]) < 1.0 for k in ("x", "y", "z")
        ) and all(
            # Orientation is compared modulo a half turn: rz = +179.9 and
            # -179.9 are the same detector pose.
            abs(abs(abs(current_cart[k] - expected[k]) - 180) - 180) < 1.0
            for k in ("rx", "ry", "rz")
        )
        if not on_sphere:
            logger.info("start pose is off the sphere; moving linearly instead")
            target_cart = dict(current_cart)
            target_cart.update(self.sph2cart(return_dict=True, **target_sph))
            return self.move_cartesian(tool=tool, frame=frame, desc=desc,
                                       sync=sync, simulate=simulate,
                                       coordinates=coordinates, **target_cart)

        sim = []
        result = None
        with self.link.transaction():
            # --- leg 1: radial, at constant direction ---------------------
            target_sph_radial = dict(current_sph, r=target_sph["r"])
            target_cart_radial = dict(current_cart)
            target_cart_radial.update({
                k: v for k, v in
                self.sph2cart(return_dict=True, **target_sph_radial).items()
                if k in ("x", "y", "z")
            })
            self.link.set_pnt([current_cart[k] for k in CARTESIAN_AXES],
                              name="tcp_p_spherical[0]")
            self.link.set_pnt([target_cart_radial[k] for k in CARTESIAN_AXES],
                              name="tcp_p_spherical[1]")
            if (self.point_reachable("tcp_p_spherical[1]")
                    and self.distance_p("tcp_p_spherical[0]",
                                        "tcp_p_spherical[1]") > 0.1):
                if simulate:
                    sim += [self.get_movel_interpolation(i / 10.0,
                                                         coordinates=coordinates)
                            for i in range(11)]
                else:
                    self.set_motion_queue_empty(False)
                    result = self.movel("tcp_p_spherical[1]", tool=tool,
                                        desc=None, sync=sync)
                    logger.info("moving radius")

            # --- leg 2: angular, at constant radius -----------------------
            target_cart = dict(current_cart)
            target_cart.update(self.sph2cart(return_dict=True, **target_sph))
            self.cartesian_destination = target_cart
            self.link.set_pnt([target_cart[k] for k in CARTESIAN_AXES],
                              name="tcp_p_spherical[3]")
            intermediate_sph = dict(
                target_sph,
                gamma=(target_sph["gamma"] + current_sph["gamma"]) / 2,
                delta=(target_sph["delta"] + current_sph["delta"]) / 2,
            )
            intermediate_cart = dict(current_cart)
            intermediate_cart.update(self.sph2cart(return_dict=True,
                                                   **intermediate_sph))
            self.link.set_pnt([intermediate_cart[k] for k in CARTESIAN_AXES],
                              name="tcp_p_spherical[2]")
            distance = self.distance_p("tcp_p_spherical[1]", "tcp_p_spherical[3]")
            if self.point_reachable("tcp_p_spherical[3]") and distance > 0.1:
                if self.point_reachable("tcp_p_spherical[2]") and distance > 1:
                    if simulate:
                        # Skip the duplicated start sample when leg 1 already
                        # contributed one.
                        start = 0 if not sim else 1
                        sim += [self.get_movec_interpolation(i / 10.0,
                                                             coordinates=coordinates)
                                for i in range(start, 11)]
                    else:
                        result = self.movec("tcp_p_spherical[2]",
                                            "tcp_p_spherical[3]", tool=None,
                                            desc=None, sync=sync)
                        logger.info("moving angle on a circle")
                else:
                    # Arc too short to define a circle; go straight there.
                    if simulate:
                        start = 0 if not sim else 1
                        sim += [self.get_movel_interpolation(
                            i / 10.0, coordinates=coordinates,
                            idx_pnt1=1, idx_pnt2=3) for i in range(start, 11)]
                    else:
                        self.set_motion_queue_empty(False)
                        result = self.movel("tcp_p_spherical[3]", tool=None,
                                            desc=None, sync=sync)
                        logger.info("moving angle linearly")
        return sim if simulate else result

    def move_joint(self, j1=None, j2=None, j3=None, j4=None, j5=None, j6=None,
                   tool=None, desc=None, sync=False, simulate=False,
                   coordinates="joint"):
        """Direct joint-space motion. Simulation is a plain linear ramp."""
        desc = self.default_desc if desc is None else desc
        tool = self.tool() if tool is None else tool
        current = self.get_joint_pos(return_dict=True)
        target = dict(zip(JOINT_AXES, [j1, j2, j3, j4, j5, j6]))
        target = {k: (current[k] if v is None else v) for k, v in target.items()}
        self.joint_destination = target

        if not simulate and not self._assert_remote_allowed(
            current, target, "joint"
        ):
            return False

        if simulate:
            start = np.array([current[k] for k in JOINT_AXES], dtype=float)
            end = np.array([target[k] for k in JOINT_AXES], dtype=float)
            return [(start + (end - start) * i / 10.0).tolist() for i in range(11)]

        with self.link.transaction():
            self.link.set_jnt([target[k] for k in JOINT_AXES], name="tcp_j")
            self.set_motion_queue_empty(False)
            return self.movej("tcp_j", tool=tool, desc=desc, sync=sync)

    def simulate_stored_commands(self):
        """Replay whatever motion the pseudo-motors currently have queued."""
        group = self.last_remote_motion
        if group not in ("cartesian", "spherical", "joint"):
            return None
        motors = self.motor_groups.get(group) or {}
        if not motors:
            return None
        target = next(iter(motors.values())).target_pos
        return {"cartesian": self.move_cartesian,
                "spherical": self.move_spherical,
                "joint": self.move_joint}[group](simulate=True, **target)

    # ---------------------------------------------------------- recording

    def record_motion(self, **kwargs):
        """Drive a motion by hand and add its trajectory to the whitelist.

        Must be run in ``manual`` working mode: the command sets the target,
        then blocks while the operator holds the pendant's dead-man switch and
        drives the arm along. The densified path is appended to
        ``remote_allowed``, and remote motions bracketed by it become legal.
        """
        self.reset_motion(
            msg="RECORDING TRAJECTORY TO LIST OF ALLOWED REMOTE MOTIONS - "
                "USE HANDHELD TO FINISH MOTION"
        )
        if self.working_mode != "manual":
            self.emit("motion",
                      f"Current working mode is {self.working_mode}. Change it "
                      f"to manual using the keyswitch to record motions.")
        group, move = self._dispatch_motion(kwargs)
        if move is None:
            return None

        if group == "spherical":
            # The controller can only interpolate in joint/cartesian space, so
            # sample in cartesian and convert each sample back to spherical.
            samples = move(simulate=True, coordinates="cartesian", **kwargs)
            path = np.array([
                [v for v in self.cart2sph(return_dict=False,
                                          **dict(zip(CARTESIAN_AXES, sample)))]
                for sample in samples
            ], dtype=float)
        else:
            path = np.array(move(simulate=True, coordinates=group, **kwargs),
                            dtype=float)
        dense = self._densify(path, RECORDING_INTERPOLATION)

        self.last_remote_motion = "recording"
        move(sync=True, **kwargs)

        recorded = self.remote_allowed_recorded()
        entry = recorded.setdefault(group, empty_recording())
        entry.setdefault("frame", []).append(list(self.frame_trsf))
        entry.setdefault("tool", []).append(list(self.tool_trsf))
        entry.setdefault("pos", []).append(dense.tolist())
        self.remote_allowed_recorded(recorded)
        self.on_recording_finished()
        return True

    @staticmethod
    def _densify(path, points_per_interval, round_to=2):
        """Linearly interpolate each column: N samples -> (N-1)*ppi + 1."""
        path = np.asarray(path, dtype=float)
        n = path.shape[0]
        if n < 2:
            return np.round(path, round_to)
        fractions = np.linspace(0, n - 1, (n - 1) * points_per_interval + 1)
        lower = np.floor(fractions).astype(int).clip(0, n - 2)
        weight = (fractions - lower)[:, None]
        return np.round(path[lower] * (1 - weight) + path[lower + 1] * weight,
                        round_to)

    def on_recording_finished(self):
        self.emit("motion", "Finished, recording")

    # ------------------------------------------------------- named motions

    def move_home(self):
        if not self.is_in_point(P_HOME):
            self.start_task(MOVE_HOME)
            self.wait_task_finished(TASK_WAIT_ROBOT_POLLING)
            self.assert_home()

    def move_park(self):
        if not self.is_in_point(P_PARK):
            self.start_task(MOVE_PARK)
            self.wait_task_finished(TASK_WAIT_ROBOT_POLLING)
            self.assert_park()

    def tweak_x(self, offset):
        self.start_task(TWEAK_X, offset)
        return self.wait_task_finished(TASK_WAIT_ROBOT_POLLING)

    def tweak_y(self, offset):
        self.start_task(TWEAK_Y, offset)
        return self.wait_task_finished(TASK_WAIT_ROBOT_POLLING)

    def is_park(self):
        return self.is_in_point(P_PARK)

    def is_home(self):
        return self.is_in_point(P_HOME)

    def is_cleared(self):
        return self.get_current_point() is not None

    def assert_park(self):
        self.assert_in_point(P_PARK)

    def assert_home(self):
        self.assert_in_point(P_HOME)

    def assert_cleared(self):
        if not self.is_cleared():
            raise RobotError("robot not in a cleared position")

    def set_remote_mode(self):
        self.set_profile("remote")

    def set_local(self):
        self.set_profile("default")

    # -------------------------------------------------------------- motors

    def set_motors_enabled(self, value, pseudos=("cartesian", "spherical",
                                                 "linear")):
        """Create or drop the pseudo-motors of the named coordinate systems.

        Re-enabling an already-enabled group is not a no-op: it re-reads the
        destination and re-seeds every setpoint, which is what makes it safe
        to call after changing the frame or tool.
        """
        if isinstance(pseudos, str):
            pseudos = [pseudos]
        value = bool(value)
        aliases = {"linear": "linear_axis"}
        for name in pseudos:
            group = aliases.get(name, name)
            self._set_group_enabled(group, value)
        return {g: self.motors_enabled[g] for g in self.motor_groups}

    _GROUP_SPEC = {
        "cartesian": (CARTESIAN_AXES, CartesianMotor, "cartesian_destination"),
        "spherical": (SPHERICAL_AXES, SphericalMotor, "spherical_destination"),
        "joint": (JOINT_AXES, JointMotor, "joint_destination"),
        "linear_axis": (LINEAR_AXES, LinearAxisMotor, "linear_axis_destination"),
    }

    def _read_destination(self, group):
        return {
            "cartesian": lambda: self.get_cartesian_pos(return_dict=True),
            "spherical": lambda: self.get_spherical_pos(),
            "joint": lambda: self.get_joint_pos(return_dict=True),
            "linear_axis": lambda: self.get_linear_axis_pos(return_dict=True),
        }[group]()

    def _set_group_enabled(self, group, value):
        axes, cls, destination_attr = self._GROUP_SPEC[group]
        if value == self.motors_enabled.get(group):
            if value:
                # Same-state re-enable: refresh destination and setpoints.
                destination = self._read_destination(group)
                setattr(self, destination_attr, destination)
                for motor in self.motor_groups[group].values():
                    motor.initialize()
            return
        self.motors_enabled[group] = value
        if value:
            self.motor_groups[group] = {a: cls(self, a) for a in axes}
            destination = self._read_destination(group)
            setattr(self, destination_attr, destination)
            if group in ("cartesian", "spherical", "linear_axis"):
                setattr(self, f"{group}_pos", dict(destination))
            for motor in self.motor_groups[group].values():
                motor.initialize()
        else:
            self.motor_groups[group] = {}
            # The original set this to [] for the linear axis while treating it
            # as a dict everywhere else.
            setattr(self, destination_attr, None)

    def set_joint_motors_enabled(self, value):
        return self._set_group_enabled("joint", bool(value))

    @property
    def motors(self):
        """Every enabled motor, flat, keyed by axis name."""
        flat = {}
        for motors in self.motor_groups.values():
            flat.update(motors)
        return flat

    # ------------------------------------------------------------- status

    def environment_status(self):
        status = super().environment_status()
        status.update({
            "override_remote_safety": self.override_remote_safety,
            "frame": self.get_frame_tree(self.frame()),
            "tool": self.get_frame_tree(self.tool()),
            "cartesian_motors_enabled": self.motors_enabled.get("cartesian", False),
            "spherical_motors_enabled": self.motors_enabled.get("spherical", False),
            "joint_motors_enabled": self.motors_enabled.get("joint", False),
            "linear_axis_motors_enabled": self.motors_enabled.get("linear_axis",
                                                                  False),
        })
        return status

    #: Settable fields, and the command a client issues to change them. Clients
    #: build the call as ``cmd(<value>, <def_kwargs>)`` -- see the eco-side
    #: ``StaeubliTx200._set_config``. Keep the rendering stable.
    ON_POLL_CONFIG_METHODS = {
        "powered": {"cmd": "robot.set_powered", "def_kwargs": ""},
        "override_remote_safety": {"cmd": "robot.set_override_remote_safety",
                                   "def_kwargs": ""},
        "speed": {"cmd": "robot.set_monitor_speed", "def_kwargs": ""},
        "frame": {"cmd": "robot.set_frame", "def_kwargs": "change_default=True"},
        "frame_coordinates": {"cmd": "robot.set_frame_coordinates",
                              "def_kwargs": ""},
        "tool": {"cmd": "robot.set_tool", "def_kwargs": ""},
        "tool_coordinates": {"cmd": "robot.set_tool_coordinates",
                             "def_kwargs": ""},
        "cartesian_motors_enabled": {"cmd": "robot.set_motors_enabled",
                                     "def_kwargs": "pseudos=['cartesian']"},
        "spherical_motors_enabled": {"cmd": "robot.set_motors_enabled",
                                     "def_kwargs": "pseudos=['spherical']"},
    }

    def on_poll_info(self):
        """Tell clients which broadcast fields are read-only and which settable."""
        config = self.ON_POLL_CONFIG_METHODS
        info = [k for k in self.on_poll_status
                if k not in list(config) + ["pos"]]
        return {"info": info, "config": config}

    # -------------------------------------------------------------- startup

    def setup(self):
        """Bring the arm to a usable state. Safe to re-run after a reconnect."""
        self.set_state(RobotState.INITIALIZING)
        self.default_desc = DESC_DEFAULT
        self.default_speed = DEFAULT_SPEED
        self.set_frame(self._frame_persistent())
        self.set_frame(self.frame(), name="tcp_f_spherical", change_default=False)
        self.set_tool(self._tool_persistent())
        self.update()
        self.get_current_point()
        self.set_motors_enabled(True, ["cartesian", "spherical", "linear"])
        self.set_joint_motors_enabled(True)
        logger.info("%s setup complete: frame=%s tool=%s", self.name,
                    self.frame(), self.tool())

    def set_default_desc(self, desc):
        self.default_desc = desc

    def get_default_desc(self):
        return self.default_desc
