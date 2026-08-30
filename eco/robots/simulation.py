"""A fake VAL3 controller, good enough to exercise the whole stack.

The pshell deployment could only be developed with the real arm attached (or
against a Stäubli emulator on localhost, which its own code notes was crashy --
``is_emulation()`` exists purely to route around a ``dist_pnt`` that killed it).
This module removes that constraint: it answers the same line protocol with
consistent, self-updating state, so the driver, the server, the HTTP contract
and the client can all be tested end to end with nothing plugged in.

It is a *protocol* simulator, not a physics one. Moves complete after a fixed
settling time rather than being integrated, and inverse kinematics is faked by
storing whatever pose was commanded. Anything that depends on the real arm's
reachability, singularities or timing still has to be tried on the hardware.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time

from .protocol import BaseTransport, Val3Error

logger = logging.getLogger(__name__)

#: How long a simulated move stays "unsettled".
MOVE_DURATION = 0.6


class SimulatedController(BaseTransport):
    """In-process stand-in for the VAL3 TCP dispatcher."""

    simulated = True

    def __init__(self, joints=None, frame="f_4mRad", tool="t_JF01T03",
                 mode=1, status_code=6, powered=False, speed=100):
        self._connected = False
        self._lock = threading.RLock()

        # Pose. Cartesian is derived from a stored pose rather than solved.
        self.joints = list(joints or [-78.88, 105.0, -12.57, -0.57, 10.2, 3.12])
        self.cartesian = [2359.25, 1888.67, -199.18, 3.0, -10.88, 103.13]
        self.z_lin = 846.72

        self.frame = frame
        self.tool = tool
        self.frame_trsf = [20.8, 26.8, -8.8, -0.46, 0.46, 0.0]
        self.tool_trsf = [51.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        self.mode = mode
        self.status_code = status_code
        self.powered = powered
        self.speed = speed
        self.tool_open = False
        self.calibrated = True
        self.emergency_stop = 0

        self.move_id = 0
        self._move_ends_at = 0.0
        self.tasks = {}                    # name -> (code, started)
        self.task_ret = 0
        self.pending_event = ""

        #: VAL3 scratch variables, including the ``tcp_p_spherical[i]`` points.
        self.variables = {"n_actPosLinAx": self.z_lin, "n_moveAbsPos": self.z_lin}
        self.points = {}                   # name -> 6-list
        self.named_points = {"park": [0.0] * 6, "home": [100.0] * 6}
        self.profile = "default"
        self.log = []                      # every (cmd, args) seen, for tests

    # ------------------------------------------------------------ transport

    def connect(self):
        self._connected = True

    def close(self):
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def request(self, line: str, timeout: float) -> str:
        if not self._connected:
            self.connect()
        msg_id, _, body = line.rstrip("\r\n").partition(" ")
        cmd, _, rest = body.partition(" ")
        args = rest.split("|") if rest else []
        with self._lock:
            self.log.append((cmd, args))
            try:
                payload = self._dispatch(cmd, args)
            except Val3Error as exc:
                return f"{msg_id}*{exc}"
            except Exception as exc:                       # pragma: no cover
                logger.exception("simulator failed on %r", line)
                return f"{msg_id}*{type(exc).__name__}: {exc}"
        return f"{msg_id} {payload}"

    # --------------------------------------------------------------- state

    @property
    def settled(self) -> bool:
        return time.time() >= self._move_ends_at

    @property
    def empty(self) -> bool:
        return self.settled

    def _start_move(self):
        self.move_id += 1
        self._move_ends_at = time.time() + MOVE_DURATION
        return self.move_id

    def _finish_due_tasks(self):
        for name, (code, started) in list(self.tasks.items()):
            if code >= 0 and time.time() - started > MOVE_DURATION:
                del self.tasks[name]
                self.task_ret = 0

    # ------------------------------------------------------------ dispatch

    @staticmethod
    def _fmt(values):
        return "|".join(
            f"{v:g}" if isinstance(v, float) else str(v) for v in values
        ) + "|"

    def _dispatch(self, cmd, args):
        self._finish_due_tasks()
        handler = getattr(self, "_cmd_" + cmd, None)
        if handler is None:
            raise Val3Error(f"unknown command: {cmd}")
        return handler(args)

    # --- status polls -------------------------------------------------

    def _cmd_get_status_fast(self, args):
        return self._fmt(list(self.joints) + list(self.cartesian) + [self.z_lin])

    def _cmd_get_status_env(self, args):
        task_name = args[0] if args else "None"
        code, _ = self.tasks.get(task_name, (-1, 0))
        event, self.pending_event = self.pending_event, ""
        return self._fmt(
            [self.mode, self.status_code, int(self.powered), self.speed,
             int(self.empty), int(self.settled), code, self.task_ret,
             self.frame, self.tool]
            + list(self.frame_trsf) + list(self.tool_trsf) + [event]
        )

    # --- variables ----------------------------------------------------

    def _cmd_get_var(self, args):
        name = args[0]
        if name == "n_actPosLinAx":
            return f"{self.z_lin:g}"
        value = self.variables.get(name, 0)
        return f"{value:g}" if isinstance(value, float) else str(value)

    def _cmd_get_str(self, args):
        name = args[0]
        return str(self.variables.get(name, ""))

    def _cmd_get_bool(self, args):
        return "1" if self.variables.get(args[0], False) else "0"

    def _cmd_get_arr(self, args):
        name, size = args[0], int(args[1])
        value = self.variables.get(name, [0] * size)
        if not isinstance(value, (list, tuple)):
            value = [value] * size
        return self._fmt(list(value)[:size] + [0] * max(0, size - len(value)))

    def _cmd_get_jnt(self, args):
        return self._fmt(self.variables.get(args[0], self.joints))

    def _cmd_get_trf(self, args):
        name = args[0]
        if name.endswith(".trsf"):
            base = name[:-5]
            if base in ("f_actualFrame", self.frame):
                return self._fmt(self.frame_trsf)
            if base in ("t_actualTool", self.tool):
                return self._fmt(self.tool_trsf)
            if base in self.points:
                return self._fmt(self.points[base])
            if base.startswith("f_"):
                return self._fmt(self.frame_trsf)
            if base.startswith("t_"):
                return self._fmt(self.tool_trsf)
        return self._fmt(self.variables.get(name, [0.0] * 6))

    _ASSIGN = re.compile(r"^\s*([A-Za-z_][\w\[\]\.]*)\s*=\s*(.+)$", re.S)

    def _cmd_eval(self, args):
        statement = args[0] if args else ""
        match = self._ASSIGN.match(statement)
        if match:
            self._assign(match.group(1).strip(), match.group(2).strip())
            return ""
        self._statement(statement)
        return ""

    # --- evaluation ---------------------------------------------------

    def _assign(self, name, expr):
        # Point assignment: `tcp_p_spherical[1].trsf={1,2,3,4,5,6}`
        if expr.startswith("{") and expr.endswith("}"):
            values = [float(v) for v in expr[1:-1].split(",")]
            if name.endswith(".trsf"):
                self.points[name[:-5]] = values
                if name[:-5] in ("f_actualFrame", self.frame):
                    self.frame_trsf = values
                elif name[:-5] in ("t_actualTool", self.tool):
                    self.tool_trsf = values
            else:
                self.variables[name] = values
            return
        if expr.startswith('"') and expr.endswith('"'):
            text = expr[1:-1]
            self.variables[name] = text
            if name == "s_actualFrame":
                self.frame = text
            elif name == "s_actualTool":
                self.tool = text
            return
        self.variables[name] = self._evaluate(expr, name)

    def _statement(self, statement):
        if statement.startswith("taskCreate"):
            name = statement.split('"')[1]
            self.tasks[name] = (1, time.time())
            return
        for prefix, action in (
            ("enablePower", lambda: setattr(self, "powered", True)),
            ("disablePower", lambda: setattr(self, "powered", False)),
            ("stopMove", lambda: setattr(self, "_move_ends_at", 0.0)),
            ("restartMove", lambda: None),
            ("resetMotion", lambda: setattr(self, "_move_ends_at", 0.0)),
        ):
            if statement.startswith(prefix):
                action()
                return
        # setEcAo(ecao_posSet, n_moveAbsPos) commits the linear-axis setpoint
        if "ecao_posSet" in statement:
            self.z_lin = float(self.variables.get("n_moveAbsPos", self.z_lin))

    def _evaluate(self, expr, target=None):
        """Resolve the handful of VAL3 predicates the driver actually calls."""
        if expr.startswith("herej()"):
            return list(self.joints)
        if expr.startswith("isPowered"):
            return self.powered
        if expr.startswith("isCalibrated"):
            return self.calibrated
        if expr.startswith("isEmpty"):
            return self.empty
        if expr.startswith("isSettled"):
            return self.settled
        if expr.startswith("isInRange"):
            return True
        if expr.startswith("pointToJoint"):
            return True                      # everything is reachable
        if expr.startswith("safetyFault"):
            return False
        if expr.endswith(".gripper"):
            return self.tool_open
        if expr.startswith("esStatus"):
            return self.emergency_stop
        if expr.startswith("getMonitorSpeed"):
            return self.speed
        if expr.startswith("setMonitorSpeed"):
            self.speed = int(re.findall(r"-?\d+", expr)[0])
            return 0
        if expr.startswith("workingMode"):
            self.variables["tcp_a"] = [self.status_code] + [0] * 5
            return self.mode
        if expr.startswith("getMoveId"):
            return self.move_id
        if expr.startswith("taskStatus"):
            name = expr.split('"')[1]
            return self.tasks.get(name, (-1, 0))[0]
        if expr.startswith("getJointForce"):
            self.variables["tcp_a"] = [0.0] * 6
            return 0
        if expr.startswith(("movej", "movel", "movec")):
            target_point = expr[expr.index("(") + 1:].split(",")[0].strip()
            pose = self.points.get(target_point)
            if pose:
                self.cartesian = list(pose)
            elif target_point in self.variables:
                self.joints = list(self.variables[target_point])
            return self._start_move()
        if expr.startswith("distance"):
            names = re.findall(r"[\w\[\]]+", expr[expr.index("(") + 1:])
            poses = [self.points.get(n) or self.named_points.get(n) for n in names[:2]]
            if all(p is not None for p in poses):
                return math.dist(poses[0][:3], poses[1][:3])
            return 10000.0
        if expr.startswith("jointToPoint") or expr.startswith("here("):
            return list(self.cartesian)
        if expr.startswith("compose") or expr.startswith("position"):
            return list(self.cartesian)
        if expr.startswith("align"):
            return list(self.cartesian)
        try:
            return float(expr)
        except ValueError:
            return 0

    # --- opcodes ------------------------------------------------------

    def _cmd_get_pnt(self, args):
        name = args[0]
        return self._fmt(self.points.get(name, self.cartesian))

    def _cmd_get_profile(self, args):
        return self.profile

    def _cmd_set_profile(self, args):
        self.profile = args[0]
        return "0"

    def _cmd_task_sts(self, args):
        return self._fmt([self.tasks.get(n, (-1, 0))[0] for n in args])

    def _cmd_kill(self, args):
        self.tasks.pop(args[0], None)
        return "0"

    def _cmd_save(self, args):
        return "0"

    def _cmd_dist_pnt(self, args):
        current = self.cartesian[:3]
        return self._fmt([
            math.dist(current, (self.named_points.get(n) or [0.0] * 6)[:3])
            for n in args
        ])

    def _cmd_get_movel_interpolation(self, args):
        fraction = float(args[0])
        coordinates = args[1] if len(args) > 1 else "joint"
        i1 = f"tcp_p_spherical[{args[2] if len(args) > 2 else 0}]"
        i2 = f"tcp_p_spherical[{args[3] if len(args) > 3 else 1}]"
        return self._interpolate(i1, i2, fraction, coordinates)

    def _cmd_get_movec_interpolation(self, args):
        fraction = float(args[0])
        coordinates = args[1] if len(args) > 1 else "joint"
        # A circle through 1-2-3 is approximated by two straight segments.
        if fraction <= 0.5:
            return self._interpolate("tcp_p_spherical[1]", "tcp_p_spherical[2]",
                                     fraction * 2, coordinates)
        return self._interpolate("tcp_p_spherical[2]", "tcp_p_spherical[3]",
                                 (fraction - 0.5) * 2, coordinates)

    def _interpolate(self, name1, name2, fraction, coordinates):
        start = self.points.get(name1, self.cartesian)
        end = self.points.get(name2, self.cartesian)
        pose = [a + (b - a) * fraction for a, b in zip(start, end)]
        if coordinates == "cartesian":
            return self._fmt(pose)
        # Fake a joint solution: a fixed offset from the cartesian pose keeps
        # the values distinct and monotonic, which is all a test needs.
        return self._fmt([j + (p - s) * 0.01
                          for j, p, s in zip(self.joints, pose, start)])


#: Backwards-compatible alias; the class was called this while it lived in
#: protocol.py.
SimulatedTransport = SimulatedController
