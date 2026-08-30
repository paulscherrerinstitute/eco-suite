"""Generic Stäubli VAL3 arm driver -- the port of pshell's ``RobotTCP.py``.

Everything here is controller-generic: working modes, power, speed, motion
primitives (movej/movel/movec), tools and frames, named points, VAL3 task
control, and the two-rate polling loop. Beamline specifics -- the frame
hierarchy, spherical detector kinematics, the remote-motion safety
whitelist -- live in :mod:`eco.robots.bernina`.

Threading model
---------------
One socket, serialised by :class:`~eco.robots.protocol.Val3Link`. A single
poller thread calls :meth:`update` at ``polling_interval``; every other caller
(the server's command executor, an eco session) issues calls from its own
thread. That is the same arrangement pshell had, with two differences:

* Multi-call sequences that must not be observed half-applied take
  :meth:`~eco.robots.protocol.Val3Link.transaction` (an RLock), so the poller
  cannot slip between e.g. ``set_pnt`` and ``movel``.
* Long waits are cancellable (see :class:`CancellationToken`), because Python
  has no equivalent of the Java thread interrupt pshell's ``:abort`` used.
"""

from __future__ import annotations

import enum
import logging
import threading
import time

from .protocol import (
    Cancelled,
    CancellationToken,
    Val3Error,
    Val3Link,
    Val3ProtocolError,
)

logger = logging.getLogger(__name__)

JOINT_AXES = ["j1", "j2", "j3", "j4", "j5", "j6"]
CARTESIAN_AXES = ["x", "y", "z", "rx", "ry", "rz"]
FLANGE = "flange"

#: ``workingMode()`` returns (mode, status). Decoded exactly as the original.
WORKING_MODES = {
    1: ("manual", {0: "programmed", 1: "connection", 2: "joint",
                   3: "cartesian_frame", 4: "cartesian_tool", 5: "point",
                   6: "hold"}),
    2: ("test", {0: "programmed", 1: "connection", 2: "programmed_fast",
                 3: "hold"}),
    3: ("local", {0: "programmed", 1: "connection", 2: "hold"}),
    4: ("remote", {0: "programmed", 1: "connection", 2: "hold"}),
}

#: Manual-mode statuses that mean the operator has taken the handheld pendant
#: and is jogging the arm. Any queued remote motion is then stale and unsafe.
MANUAL_JOG_STATUSES = ("joint", "cartesian_frame", "cartesian_tool", "point")


class RobotState(str, enum.Enum):
    """Mirrors pshell's ``ch.psi.utils.State`` for the values we use."""

    OFFLINE = "Offline"
    INITIALIZING = "Initializing"
    READY = "Ready"
    BUSY = "Busy"
    PAUSED = "Paused"
    FAULT = "Fault"

    def __str__(self):
        return self.value


class RobotError(Exception):
    pass


class StaeubliRobot:
    """A Stäubli arm reachable over the VAL3 TCP dispatcher."""

    def __init__(self, name, link: Val3Link, default_tolerance=5.0,
                 default_speed=100, default_desc=None, polling_interval=0.2,
                 env_polling_interval=1.0, tool=None, frame=None):
        self.name = name
        self.link = link
        self.cancellation = link.cancellation

        # --- static configuration -------------------------------------
        self.default_tolerance = default_tolerance
        self.default_speed = default_speed
        self.default_desc = default_desc
        self.polling_interval = polling_interval
        #: The environment poll (modes, frames, tools, task status) is an order
        #: of magnitude more expensive than the position poll, so it runs at
        #: its own slower rate -- the "Split updates in slowly and rapidly
        #: polled variables" change from the pshell history.
        self.env_polling_interval = env_polling_interval
        self.task_start_retries = 3
        #: Tasks can start *and finish* between creation and the status check,
        #: which then looks like a failed start. Set False to tolerate that.
        self.exception_on_task_start_failure = True

        # --- live state -----------------------------------------------
        self._state = RobotState.OFFLINE
        self.working_mode = None
        self.status = None
        self.powered = None
        self.speed = None
        self.empty = None
        self.settled = None
        self.joint_forces = None
        self.connected = False

        self.tool = tool          # JsonValue/MemoryValue, set by subclass
        self.frame = frame
        self.tool_trsf = [0.0] * 6
        self.frame_trsf = [0.0] * 6
        self.tool_open = None

        self.joint_pos = {}
        self.cartesian_pos = {}
        self.linear_axis_pos = {}

        self.high_level_tasks = []
        self.known_points = []
        self.current_points = []
        self.current_task = None
        self.current_task_ret = None
        self.current_task_timestamp = 0.0
        self.simulated_point = ""

        self.motor_groups = {}     # group name -> {axis: motor}
        self.motors_enabled = {}   # group name -> bool

        self.on_poll_status = {}
        self._listeners = []
        self._reset = True
        self._last_env_update = 0.0
        self._poll_thread = None
        self._poll_stop = threading.Event()
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------ plumbing

    def __repr__(self):
        return (f"<{type(self).__name__} {self.name} state={self.state} "
                f"mode={self.working_mode}/{self.status}>")

    @property
    def simulated(self) -> bool:
        return self.link.simulated

    def is_emulation(self) -> bool:
        """True when talking to a VAL3 emulator rather than a real controller.

        Some commands (``dist_pnt``) crash the emulator and must be replaced
        by per-point round trips.
        """
        transport = self.link.transport
        return "localhost" in str(getattr(transport, "host", ""))

    # ------------------------------------------------------------- events

    def add_listener(self, callback):
        """Register ``callback(kind, payload)``. Never raises into the poller."""
        self._listeners.append(callback)
        return callback

    def remove_listener(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def emit(self, kind, payload):
        for callback in list(self._listeners):
            try:
                callback(kind, payload)
            except Exception:
                # A broken listener must never stall polling or abort a move.
                logger.exception("robot listener %r failed on %r", callback, kind)

    # -------------------------------------------------------------- state

    @property
    def state(self) -> RobotState:
        return self._state

    def set_state(self, value: RobotState):
        with self._state_lock:
            if value == self._state:
                return
            previous, self._state = self._state, value
        logger.info("%s state %s -> %s", self.name, previous, value)
        # NOT "state": in the pshell SSE contract that name is the
        # *application* state clients gate their commands on. The robot's
        # own state travels as "robot_state" and in the polling payload.
        self.emit("robot_state", {"state": str(value),
                                  "previous": str(previous)})

    def _update_state(self):
        if self._state == RobotState.OFFLINE:
            logger.info("communication with %s resumed", self.name)
            self.on_reconnected()
        if self._reset or self._state == RobotState.OFFLINE:
            self.check_task()
            if self.current_task is not None:
                logger.info("ongoing task on reconnect: %s", self.current_task)
        if (not self.settled) or (self.current_task is not None):
            self.set_state(RobotState.BUSY)
        elif not self.empty:
            self.set_state(RobotState.PAUSED)
        else:
            self.set_state(RobotState.READY)

    def is_ready(self) -> bool:
        return self._state == RobotState.READY

    def wait_ready(self, timeout=None):
        """Block until READY. Cancellable; raises on timeout.

        The state only advances when someone polls the controller. If the
        poller thread is not running -- a bare driver in an eco session, a
        test, a server that has not started polling yet -- this drives the
        update itself rather than waiting forever for a state change that
        nothing is going to produce.
        """
        start = time.time()
        last_self_update = 0.0
        while self._state != RobotState.READY:
            self.cancellation.raise_if_cancelled()
            if timeout is not None and (time.time() - start) > timeout:
                raise RobotError(
                    f"timed out after {timeout} s waiting for {self.name} to be "
                    f"ready (state={self._state})"
                )
            if not self._polling_active():
                now = time.time()
                if now - last_self_update >= self.polling_interval:
                    last_self_update = now
                    self.update()
            time.sleep(0.01)

    def _polling_active(self) -> bool:
        return self._poll_thread is not None and self._poll_thread.is_alive()

    # -------------------------------------------------------- working mode

    def _update_working_mode(self, mode, status):
        previous_mode, previous_status = self.working_mode, self.status
        if mode in WORKING_MODES:
            name, statuses = WORKING_MODES[mode]
            self.working_mode = name
            self.status = statuses.get(status, "invalid")
        else:
            self.working_mode = "invalid"
            self.status = "invalid"
        if self.working_mode != previous_mode:
            try:
                self.on_change_working_mode(self.working_mode, previous_mode)
            except Exception:
                logger.exception("on_change_working_mode failed")
        if self.status != previous_status:
            try:
                self.on_change_status(self.status, previous_status)
            except Exception:
                logger.exception("on_change_status failed")

    def read_working_mode(self):
        try:
            mode = self.link.eval_int("workingMode(tcp_a)")
            status = int(self.link.get_var("tcp_a[0]").strip())
            self._update_working_mode(mode, status)
            self._update_state()
        except (Val3Error, Val3ProtocolError, ValueError):
            logger.exception("reading working mode failed")
            self.working_mode = "invalid"
            self.status = "invalid"
        return self.working_mode

    # --------------------------------------------------------- power/speed

    def is_powered(self) -> bool:
        self.powered = self.link.eval_bool("isPowered()")
        return self.powered

    def enable(self, timeout=5.0):
        if not self.is_powered():
            self.link.evaluate("enablePower()")
            self.cancellation.sleep(timeout)
            if not self.is_powered():
                raise RobotError(f"enabling power timed out after {timeout} s")

    def disable(self):
        self.link.evaluate("disablePower()", timeout=5.0)

    def set_powered(self, value):
        self.enable() if value else self.disable()

    def get_monitor_speed(self) -> int:
        self.speed = self.link.eval_int("getMonitorSpeed()")
        return self.speed

    def set_monitor_speed(self, speed):
        ret = self.link.eval_int(f"setMonitorSpeed({int(speed)})")
        if ret == -1:
            raise RobotError("the robot is not in remote working mode")
        if ret == -2:
            raise RobotError("the monitor speed is under operator control")
        if ret == -3:
            raise RobotError(f"speed {speed} is not supported")

    def set_default_speed(self):
        # The original said `set_monitor_speed(...)` without `self.`, which was
        # a NameError every time it ran.
        self.set_monitor_speed(self.default_speed)

    def is_calibrated(self) -> bool:
        return self.link.eval_bool("isCalibrated()")

    def get_emergency_stop_sts(self) -> str:
        code = self.link.eval_int("esStatus()")
        return {1: "active", 2: "activated"}.get(code, "off")

    def get_safety_fault_signal(self):
        return self.link.eval_bool("safetyFault(s)")

    def save_program(self):
        ret = self.link.execute("save", timeout=5.0)
        if str(ret).strip() != "0":
            raise RobotError(f"error saving program: {ret}")

    # ------------------------------------------------------ motion control

    def stop(self, msg=""):
        """Halt motion. The queue is retained -- use resume() to continue."""
        self.link.evaluate("stopMove()")
        if msg:
            self.emit("stop", msg)

    def resume(self):
        self.link.evaluate("restartMove()")

    def reset_motion_queue(self, joint=None):
        """Discard queued motions on the controller, leaving setpoints alone.

        This is the half of ``reset_motion`` that the pseudo-motors want: when
        two writes to the same coordinate system are merged, the queued motion
        must go, but the setpoints being merged must survive -- re-seeding them
        from the readbacks mid-merge would throw away the very target being
        assembled. (The original called ``robot.evaluate("resetMotion()")``
        directly from the motors for exactly this reason.)
        """
        self.link.evaluate(
            "resetMotion()" if joint is None else f"resetMotion({joint})"
        )

    def reset_motion(self, joint=None, msg=""):
        """Discard every queued motion, and re-seed the motor setpoints."""
        self.reset_motion_queue(joint)
        # Motor setpoints now describe motions that will never happen; re-seed
        # them from the readbacks so the next relative move starts from truth.
        for motors in self.motor_groups.values():
            for motor in motors.values():
                motor.initialize()
        if msg:
            self.emit("reset_motion", msg)

    def is_empty(self) -> bool:
        self.empty = self.link.eval_bool("isEmpty()")
        self._update_state()
        return self.empty

    def is_settled(self) -> bool:
        self.settled = self.link.eval_bool("isSettled()")
        self._update_state()
        return self.settled

    def set_motion_queue_empty(self, state=False):
        """Mark the queue non-empty immediately after issuing a move.

        The controller only reports it a poll period later, and without this
        the state machine would briefly report READY mid-move.
        """
        self.empty = state
        self._update_state()

    def get_move_id(self) -> int:
        return self.link.eval_int("getMoveId()")

    def set_move_id(self, move_id):
        return self.link.evaluate(f"setMoveId({int(move_id)} )")

    def get_joint_forces(self):
        try:
            self.link.evaluate("getJointForce(tcp_a)")
            self.joint_forces = self.link.get_float_arr(6)
            return self.joint_forces
        except Exception:
            self.joint_forces = None
            raise

    def _resolve_move_target(self, target, scratch="tcp_p"):
        """A str is a VAL3 symbol; a sequence is written to a scratch point."""
        if isinstance(target, str):
            return target
        self.link.set_pnt(target, scratch)
        return scratch

    def movej(self, joint_or_point, tool=None, desc=None, sync=False):
        """Joint-interpolated move. Returns the controller's move id."""
        desc = self.default_desc if desc is None else desc
        tool = self.tool() if tool is None else tool
        if self.simulated:
            self.simulated_point = ""
        with self.link.transaction():
            target = self._resolve_move_target(joint_or_point)
            ret = self.link.eval_int(f"movej({target}, {tool}, {desc})")
        if sync:
            self.wait_end_of_move()
        if self.simulated:
            self.simulated_point = target
        return ret

    def movel(self, point, tool=None, desc=None, sync=False):
        """Straight-line move in cartesian space."""
        desc = self.default_desc if desc is None else desc
        tool = self.tool() if tool is None else tool
        if self.simulated:
            self.simulated_point = ""
        with self.link.transaction():
            target = self._resolve_move_target(point)
            ret = self.link.eval_int(f"movel({target}, {tool}, {desc})")
        if sync:
            self.wait_end_of_move()
        if self.simulated:
            self.simulated_point = target
        return ret

    def movec(self, point_interm, point_target, tool=None, desc=None, sync=False):
        """Circular move through an intermediate point."""
        desc = self.default_desc if desc is None else desc
        tool = self.tool() if tool is None else tool
        if self.simulated:
            self.simulated_point = ""
        ret = self.link.eval_int(
            f"movec({point_interm}, {point_target}, {tool}, {desc})"
        )
        if sync:
            self.wait_end_of_move()
        if self.simulated:
            self.simulated_point = point_target
        return ret

    def wait_end_of_move(self, timeout=None):
        """Wait for the queue to drain and the arm to settle. Cancellable."""
        self.cancellation.sleep(0.05)
        self.update()
        self.wait_ready(timeout)

    # ---------------------------------------------------------------- tool

    def set_tool(self, tool):
        self.tool(tool)
        if self.tool_persistent is not None:
            self.tool_persistent(tool)
        self.link.set_str(tool, name="s_actualTool")
        self.link.evaluate("t_actualTool=" + tool)
        if self.motors_enabled.get("cartesian"):
            self.update()
            self.set_motors_enabled(True, ["cartesian"])
        self.is_tool_open()

    def get_tool(self):
        return self.tool()

    def assert_tool(self, tool=None):
        if tool is None:
            if self.tool() is None:
                raise RobotError("tool is undefined")
        elif self.tool() != tool:
            raise RobotError(f"invalid tool: {self.tool()} (expected {tool})")

    def get_tool_trsf(self, name=None):
        if name is None:
            self.assert_tool()
            name = self.tool()
        return self.link.get_trsf(name + ".trsf")

    def set_tool_trsf(self, trsf, name=None):
        if name is None:
            self.assert_tool()
            name = self.tool()
        self.link.set_trsf(trsf, name + ".trsf")

    def set_tool_coordinates(self, values, name=None):
        name = self.tool() if name is None else name
        with self.link.transaction():
            self.link.set_trsf(values, name=name + ".trsf")
            if name == self.tool():
                self.link.set_trsf(values, name="t_actualTool.trsf")

    def open_tool(self, tool=None):
        tool = self.tool() if tool is None else tool
        self.link.evaluate(f"{tool}.gripper=true")
        self.tool_open = True

    def close_tool(self, tool=None):
        tool = self.tool() if tool is None else tool
        self.link.evaluate(f"{tool}.gripper=false")
        self.tool_open = False

    def is_tool_open(self, tool=None) -> bool:
        tool = self.tool() if tool is None else tool
        self.tool_open = self.link.eval_bool(f"{tool}.gripper")
        return self.tool_open

    # --------------------------------------------------------------- frame

    def set_frame(self, frame, name=None, change_default=True):
        """Bind a VAL3 frame symbol, optionally as the new active frame."""
        with self.link.transaction():
            if name is not None:
                self.link.evaluate(f"{name}={frame}")
            if change_default:
                self.link.set_str(frame, name="s_actualFrame")
                self.link.evaluate("f_actualFrame=" + frame)
                self.frame(frame)
                if self.frame_persistent is not None:
                    self.frame_persistent(frame)
        if change_default and self.motors_enabled.get("cartesian"):
            self.update()
            self.set_motors_enabled(True, ["cartesian"])

    def get_frame(self):
        return self.frame()

    def set_default_frame(self):
        self.set_frame(self.frame())

    def set_frame_coordinates(self, values, name=None):
        name = self.frame() if name is None else name
        with self.link.transaction():
            self.link.set_trsf(values, name=name + ".trsf")
            if name == self.frame():
                self.link.set_trsf(values, name="f_actualFrame.trsf")

    # ------------------------------------------------------------ geometry

    def herej(self):
        return self.link.eval_jnt("herej()")

    def distance_t(self, trsf1, trsf2) -> float:
        return self.link.eval_float(f"distance({trsf1}, {trsf2})")

    def distance_p(self, pnt1, pnt2) -> float:
        return self.link.eval_float(f"distance({pnt1}, {pnt2})")

    def compose(self, pnt, frame=None, trsf="tcp_t"):
        frame = self.frame() if frame is None else frame
        return self.link.eval_pnt(f"compose({pnt}, {frame}, {trsf})")

    def here(self, tool=None, frame=None):
        tool = self.tool() if tool is None else tool
        frame = self.frame() if frame is None else frame
        return self.link.eval_pnt(f"here({tool}, {frame})")

    def joint_to_point(self, tool=None, frame=None, joint="tcp_j"):
        tool = self.tool() if tool is None else tool
        frame = self.frame() if frame is None else frame
        return self.link.eval_pnt(f"jointToPoint({tool}, {frame}, {joint})")

    def point_to_joint(self, tool=None, initial_joint="tcp_j", point="tcp_p"):
        tool = self.tool() if tool is None else tool
        if self.link.eval_bool(
            f"pointToJoint({tool}, {initial_joint}, {point}, j)"
        ):
            return self.link.get_jnt()
        return None

    def position(self, point, frame=None):
        frame = self.frame() if frame is None else frame
        return self.link.eval_trf(f"position({point}, {frame})")

    def get_cartesian_pos(self, tool=None, frame=None, return_dict=True):
        if tool is None:
            self.assert_tool()
            tool = self.tool()
        frame = self.frame() if frame is None else frame
        # Two statements, not one: the controller freezes on the combined form.
        with self.link.transaction():
            self.link.evaluate("tcp_j=herej()")
            self.link.evaluate(f"tcp_p=jointToPoint({tool}, {frame}, tcp_j)")
            values = self.link.get_pnt()
        if return_dict:
            return dict(zip(CARTESIAN_AXES, values))
        return values

    def get_joint_pos(self, return_dict=True):
        values = self.herej()
        return dict(zip(JOINT_AXES, values)) if return_dict else values

    def get_flange_pos(self, frame=None, return_dict=True):
        return self.get_cartesian_pos(FLANGE, frame, return_dict=return_dict)

    def get_cartesian_destination(self, tool=None, frame=None, return_dict=True):
        # The original computed this and dropped it on the floor (no return).
        return self.get_cartesian_pos(tool=tool, frame=frame,
                                      return_dict=return_dict)

    # -------------------------------------------------------- known points

    def set_known_points(self, points):
        self.known_points = list(points)

    def get_known_points(self):
        return self.known_points

    def set_tasks(self, tasks):
        self.high_level_tasks = list(tasks)

    def get_tasks(self):
        return self.high_level_tasks

    def get_distance_to_pnt(self, name) -> float:
        if self.simulated:
            return 0.0 if (name and name == self.simulated_point) else 10000.0
        self.link.set_pnt(self.get_cartesian_pos(return_dict=False))
        return self.distance_p("tcp_p", name)

    def get_distance_to_pnts(self, *points):
        if self.simulated or self.is_emulation():
            # dist_pnt crashes the emulation controller; fall back per point.
            return [self.get_distance_to_pnt(p) for p in points]
        raw = self.link.execute("dist_pnt", *points)
        if isinstance(raw, str):
            raw = [raw]
        result = []
        for value in raw[:len(points)]:
            try:
                result.append(float(value.strip()))
            except (ValueError, AttributeError):
                result.append(None)
        return result

    def is_in_points(self, *points, tolerance=None):
        """Per-point 'within tolerance' flags; None where distance is unknown."""
        tolerance = self.default_tolerance if tolerance is None else tolerance
        distances = self.get_distance_to_pnts(*points)
        flags = []
        for distance in distances:
            # `None < 0` was True on Python 2 and is a TypeError on Python 3,
            # so the None case is now explicit rather than accidental.
            if distance is None or distance < 0:
                flags.append(None)
            else:
                flags.append(distance < tolerance)
        if tolerance == self.default_tolerance and set(self.known_points) <= set(points):
            self.current_points = [
                p for p, flag in zip(points, flags) if flag is True
            ]
        return flags

    def is_in_point(self, point, tolerance=None) -> bool:
        if tolerance is None and point in self.known_points:
            return point in self.get_current_points()
        tolerance = self.default_tolerance if tolerance is None else tolerance
        distance = self.get_distance_to_pnt(point)
        if distance is None or distance < 0:
            raise RobotError(f"error calculating distance to {point}: {distance}")
        return distance < tolerance

    def get_current_points(self, tolerance=None):
        if not self.known_points:
            return []
        flags = self.is_in_points(*self.known_points, tolerance=tolerance)
        return [p for p, flag in zip(self.known_points, flags) if flag is True]

    def get_current_point(self, tolerance=None):
        points = self.get_current_points(tolerance)
        return points[0] if points else None

    def get_current_points_cached(self):
        return self.current_points

    def get_current_point_cached(self):
        return self.current_points[0] if self.current_points else None

    def assert_in_point(self, point, tolerance=None):
        if not self.is_in_point(point, tolerance):
            raise RobotError(f"not in position {point}")

    def assert_in_known_point(self, tolerance=None):
        if self.get_current_point(tolerance) is None:
            raise RobotError("robot not in a known point")

    # ------------------------------------------------------------- profile

    def get_profile(self):
        return self.link.execute("get_profile", timeout=2.0)

    def set_profile(self, name, password=""):
        self.link.execute("set_profile", str(name), str(password), timeout=5.0)

    # ---------------------------------------------------------------- tasks

    def task_create(self, program, *args, priority=10, name=None):
        program = str(program)
        name = str(program if name is None else name)
        if self.get_task_status(name)[0] != -1:
            raise RobotError(f"task already exists: {name}")
        if not 1 <= priority <= 100:
            raise RobotError(f"invalid priority: {priority}")
        rendered = []
        for value in args:
            if isinstance(value, bool):
                rendered.append("true" if value else "false")
            else:
                rendered.append(str(value))
        call = f"{program}({','.join(rendered)})"
        # `taskCreate` via the `task_create` opcode freezes the controller, so
        # it goes through `eval` -- same workaround as the original.
        self.link.evaluate(f'taskCreate "{name}", {priority}, {call}')
        if self.simulated:
            self.simulated_point = ""

    def task_suspend(self, name):
        self.link.evaluate(f'taskSuspend("{name}")')

    def task_resume(self, name):
        self.link.evaluate(f'taskResume("{name}",0)', timeout=2.0)

    def task_kill(self, name):
        self.link.execute("kill", str(name), timeout=5.0)

    def get_task_status(self, name):
        if self.simulated:
            return (-1, "Stopped")
        code = self.link.eval_int(f'taskStatus("{name}")')
        status = {-1: "Stopped", 0: "Paused", 1: "Running"}.get(code, "Error")
        return (code, status)

    def get_tasks_status(self, *names):
        raw = self.link.execute("task_sts", *names)
        if isinstance(raw, str):
            raw = [raw]
        result = []
        for value in raw[:len(names)]:
            try:
                result.append(int(value.strip()))
            except (ValueError, AttributeError):
                result.append(None)
        return result

    def get_task(self):
        return self.current_task

    def get_task_ret(self, cached=True):
        return self.current_task_ret if cached else self.link.eval_int("tcp_ret")

    def clear_task_ret(self):
        return self.link.evaluate("tcp_ret=0")

    def check_task(self):
        """Adopt a task that was already running (e.g. after a reconnect)."""
        if self.current_task is None:
            for task in self.high_level_tasks:
                if self.get_task_status(task)[0] >= 0:
                    self.current_task, self.current_task_ret = task, None
                    logger.info("adopted running task: %s", task)
                    break
        return self.current_task

    def assert_no_task(self):
        task = self.check_task()
        if task is not None:
            raise RobotError(f"ongoing task: {task}")

    def start_task(self, program, *args, priority=10, name=None):
        """Start an exclusive high-level VAL3 task, refusing to overlap one."""
        tasks = list(self.high_level_tasks)
        if self.current_task is not None and self.current_task not in tasks:
            tasks.append(self.current_task)
        if program not in tasks:
            tasks.append(program)
        for task, status in zip(tasks, self.get_tasks_status(*tasks)):
            if status is not None and status > 0:
                raise RobotError(f"ongoing high-level task: {task}")

        self.clear_task_ret()
        code, status = -1, "Stopped"
        for attempt in range(self.task_start_retries):
            self.cancellation.raise_if_cancelled()
            self.task_create(program, *args, priority=priority, name=name)
            code, status = self.get_task_status(program)
            if code >= 0 or self.simulated:
                break
            if attempt < self.task_start_retries - 1:
                if self.get_task_ret(False) == 0:
                    logger.warning("retrying start of %s%s (status %s/%s)",
                                   program, args, status, code)
                    continue
                logger.warning("start of %s%s aborted, task already returned",
                               program, args)
                break
        else:
            logger.error("failed to start %s%s", program, args)
            if self.exception_on_task_start_failure:
                raise RobotError(f"cannot start task: {program}{args}")

        logger.info("task started: %s%s (status %s/%s)", program, args, status, code)
        self.current_task = program
        self.current_task_ret = None
        self.current_task_timestamp = time.time()
        self.update()
        return code

    def stop_task(self):
        tasks = list(self.high_level_tasks)
        if self.current_task is not None and self.current_task not in tasks:
            tasks.append(self.current_task)
        for task in tasks:
            self.task_kill(task)
        if self.simulated:
            self.current_task_timestamp = -1
        self.reset_motion()

    def wait_task_finished(self, poll_interval=0.05, timeout=None):
        """Block until the current task ends. Cancellable; returns its code."""
        start = time.time()
        while self.get_task() is not None:
            self.cancellation.raise_if_cancelled()
            if timeout is not None and (time.time() - start) > timeout:
                raise RobotError(
                    f"task {self.current_task} did not finish within {timeout} s"
                )
            time.sleep(poll_interval)
        return self.get_task_ret()

    # --------------------------------------------------------- pseudomotors

    def register_motor_group(self, group, motors):
        self.motor_groups[group] = motors
        self.motors_enabled[group] = bool(motors)

    def set_motors_enabled(self, value, pseudos=None):
        raise NotImplementedError("subclasses define their motor groups")

    # -------------------------------------------------------------- polling

    def start_polling(self):
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name=f"{self.name}-poll", daemon=True
        )
        self._poll_thread.start()

    def stop_polling(self, timeout=5.0):
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout)
            self._poll_thread = None

    def _poll_loop(self):
        while not self._poll_stop.is_set():
            started = time.time()
            try:
                self.update()
            except Exception:
                logger.exception("poll iteration failed")
            # Sleep the remainder of the period, so a slow round trip does not
            # accumulate delay the way a fixed sleep would.
            self._poll_stop.wait(
                max(0.0, self.polling_interval - (time.time() - started))
            )

    def update(self):
        """One poll iteration: always positions, periodically the environment."""
        try:
            self.update_fast()
            self.connected = True
        except Cancelled:
            raise
        except Exception as exc:
            self._on_update_error(exc)
        if time.time() - self._last_env_update >= self.env_polling_interval:
            self._last_env_update = time.time()
            try:
                self.update_env()
            except Cancelled:
                raise
            except Exception as exc:
                self._on_update_error(exc)
        self.on_polling()

    def _on_update_error(self, exc):
        self.connected = False
        if self._state != RobotState.OFFLINE:
            logger.error("update error: %s", exc)
            self.emit("shell", f"Update error: {exc}")
            self.set_state(RobotState.OFFLINE)

    def update_fast(self):
        """Positions only -- cheap, runs at the full polling rate."""
        current_task = self.current_task   # may change under us; snapshot it
        sts = self.link.execute("get_status_fast", current_task)
        self.joint_pos = {
            name: float(sts[i].strip()) for i, name in enumerate(JOINT_AXES)
        }
        self.cartesian_pos = {
            name: float(sts[6 + i].strip()) for i, name in enumerate(CARTESIAN_AXES)
        }
        self.linear_axis_pos = {"z_lin": float(sts[12].strip())}
        self.connected = True
        self.on_positions_updated()
        self.on_poll_status["pos"] = self.positions()
        for motors in self.motor_groups.values():
            for motor in motors.values():
                motor.update_readback()

    def positions(self):
        """Every axis in one flat dict, across all coordinate systems."""
        pos = {}
        pos.update(self.cartesian_pos)
        pos.update(self.joint_pos)
        pos.update(self.linear_axis_pos)
        return pos

    def update_env(self):
        """Modes, power, speed, task, frame/tool -- the slow poll."""
        current_task = self.current_task
        sts = self.link.execute("get_status_env", current_task)
        self._update_working_mode(int(sts[0].strip()), int(sts[1].strip()))
        self.powered = sts[2].strip() == "1"
        self.speed = int(sts[3].strip())
        self.empty = sts[4].strip() == "1"
        self.settled = sts[5].strip() == "1"

        if current_task is not None:
            if self.simulated:
                if time.time() - self.current_task_timestamp > 3.0:
                    ret = -1 if self.current_task_timestamp > 0 else None
                    self._finish_task(current_task, ret)
            elif int(sts[6].strip()) < 0:
                try:
                    ret = int(sts[7].strip())
                except ValueError:
                    ret = None
                self._finish_task(current_task, ret)

        # Write-through only on change: the original rewrote both JSON files
        # on every environment poll, i.e. twice a second on NFS, forever.
        self.frame.set_if_changed(sts[8].strip())
        self.tool.set_if_changed(sts[9].strip())
        self.frame_trsf = [float(sts[10 + i].strip()) for i in range(6)]
        self.tool_trsf = [float(sts[16 + i].strip()) for i in range(6)]
        self.on_environment_updated()

        event = sts[22].strip() if len(sts) > 22 else ""
        if event:
            logger.info("controller event: %s", event)
            self.on_event(event)

        self._update_state()
        self._reset = False
        self.on_poll_status.update(self.environment_status())

    def _finish_task(self, task, ret):
        logger.info("task %s finished with code %s", task, ret)
        self.current_task, self.current_task_ret = None, ret
        try:
            self.on_task_finished(task, ret)
        except Exception:
            logger.exception("on_task_finished failed")

    def environment_status(self):
        """The slow-poll half of the broadcast payload. Extended by subclasses."""
        return {
            "robot_state": str(self.state),
            "connected": self.connected,
            "powered": self.powered,
            "speed": self.speed,
            "settled": self.settled,
            "empty": self.empty,
            "task": self.current_task,
            "mode": self.working_mode,
            "status": self.status,
            "frame": self.frame(),
            "frame_coordinates": self.frame_trsf,
            "tool": self.tool(),
            "tool_coordinates": self.tool_trsf,
            "open": self.tool_open,
        }

    # ---------------------------------------------------------------- hooks

    def on_polling(self):
        self.emit("polling", self.on_poll_status)

    def on_positions_updated(self):
        pass

    def on_environment_updated(self):
        pass

    def on_event(self, event):
        pass

    def on_reconnected(self):
        pass

    def on_task_finished(self, task, ret):
        pass

    def on_change_working_mode(self, working_mode, previous):
        """Switch the controller user profile with the keyswitch position."""
        if working_mode == "remote":
            self.set_profile("remote")
            self.emit("motion",
                      f"Working mode changed from {previous} to {working_mode}, "
                      f"changing user.")
        else:
            self.set_profile("default")

    def on_change_status(self, status, previous):
        """Drop queued motions as soon as the operator jogs from the pendant."""
        if status in MANUAL_JOG_STATUSES:
            msg = f"Manual mode change to {status} mode detected"
            logger.warning(msg)
            self.reset_motion(msg=msg)
            self.emit(
                "reset_motion",
                f"Cancelling all stored movement commands on controller!\n{msg}",
            )

    # -------------------------------------------------------------- helpers

    @property
    def tool_persistent(self):
        return getattr(self, "_tool_persistent", None)

    @property
    def frame_persistent(self):
        return getattr(self, "_frame_persistent", None)

    # ------------------------------------------------- pshell compatibility

    def take(self):
        """The last broadcast status dict -- pshell's ``Device.take()``."""
        return dict(self.on_poll_status)

    def doUpdate(self):
        """pshell spelling of :meth:`update`; eco's client still sends it."""
        return self.update()

    def isReady(self) -> bool:
        return self.is_ready()

    def waitReady(self, timeout=-1):
        return self.wait_ready(None if timeout is None or timeout < 0 else timeout)

    def setPolling(self, milliseconds):
        self.polling_interval = float(milliseconds) / 1000.0

    def isSimulated(self) -> bool:
        return self.simulated
