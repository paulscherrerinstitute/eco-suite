"""Command execution -- the port of pshell's script context.

pshell let a client POST an arbitrary Jython statement and ran it against a
namespace containing the device pool and every function the startup scripts had
defined. eco's robot client depends on exactly that: it sends strings like
``robot.general_motion(**{'gamma': 20})`` and ``gamma.moveAsync(20.0)``, so the
capability has to survive the port.

What it looks like here:

* One :class:`CommandExecutor` owning a *foreground* slot and a small
  background pool. A statement ending in ``&`` goes to the background pool and
  leaves the server READY; anything else takes the foreground slot and puts the
  server in BUSY, which is precisely the gate eco's client polls before issuing
  a move.
* Commands get integer ids, and results are retrievable by id until they
  expire -- the ``/result/<id>`` contract.
* Aborting is cooperative. Java's ``Thread.interrupt()`` has no Python
  equivalent, so ``:abort`` fires a :class:`CancellationToken` that every
  protocol round trip and every wait loop in the driver checks. Worst-case
  latency is one controller timeout (1 s by default).

.. warning::
   ``/eval`` is remote code execution by design, exactly as pshell's was. The
   namespace below is restricted (no ``__import__``, no ``open``, no file or
   network builtins) which raises the bar but is **not** a security boundary --
   anything reachable from ``robot`` can move a two-tonne arm. Bind the server
   to a trusted network, as the pshell one was.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..robots.protocol import Cancelled, CancellationToken

logger = logging.getLogger(__name__)

#: Builtins reachable from an evaluated statement. Deliberately short: enough
#: for the dict/list/number manipulation clients actually send, nothing that
#: opens a file, imports a module or spawns a process.
SAFE_BUILTINS = {
    name: __builtins__[name] if isinstance(__builtins__, dict)
    else getattr(__builtins__, name)
    for name in (
        "abs", "all", "any", "bool", "callable", "dict", "divmod",
        "enumerate", "filter",
        "float", "format", "frozenset", "getattr", "hasattr", "hash", "int",
        "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min",
        "next", "print", "range", "repr", "reversed", "round", "set", "slice",
        "sorted", "str", "sum", "tuple", "type", "zip",
        "True", "False", "None", "Exception", "ValueError", "TypeError",
    )
    if (name in __builtins__ if isinstance(__builtins__, dict)
        else hasattr(__builtins__, name))
}

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_ABORTED = "aborted"


class CommandResult:
    __slots__ = ("id", "statement", "background", "status", "value",
                 "exception", "started", "finished")

    def __init__(self, command_id, statement, background):
        self.id = command_id
        self.statement = statement
        self.background = background
        self.status = STATUS_RUNNING
        self.value = None
        self.exception = None
        self.started = time.time()
        self.finished = None

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            # Field names are the pshell ones; eco reads exactly these.
            "return": self.value,
            "exception": self.exception,
            "statement": self.statement,
            "background": self.background,
            "started": self.started,
            "finished": self.finished,
        }


class CommandExecutor:
    """Runs statements against a namespace, one foreground at a time."""

    def __init__(self, namespace, cancellation: CancellationToken,
                 max_background=8, time_to_live=600.0,
                 on_state_change=None):
        self.namespace = namespace
        self.cancellation = cancellation
        self.time_to_live = time_to_live
        self._on_state_change = on_state_change
        self._ids = itertools.count(1)
        self._results = {}
        self._lock = threading.Lock()
        self._foreground = None          # CommandResult currently in front
        self._foreground_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=max_background + 1, thread_name_prefix="robot-cmd"
        )

    # ------------------------------------------------------------- state

    @property
    def busy(self) -> bool:
        return self._foreground is not None

    def _set_foreground(self, result):
        self._foreground = result
        if self._on_state_change:
            self._on_state_change("Busy" if result is not None else "Ready")

    # --------------------------------------------------------- submission

    @staticmethod
    def parse(statement):
        """Split off pshell's trailing ``&`` background marker."""
        statement = statement.strip()
        background = statement.endswith("&")
        if background:
            statement = statement[:-1].strip()
        return statement, background

    def submit(self, statement) -> CommandResult:
        """Start a statement; returns immediately with a RUNNING result."""
        code, background = self.parse(statement)
        result = CommandResult(next(self._ids), code, background)
        with self._lock:
            self._results[result.id] = result
            self._expire_locked()

        if not background:
            # Refuse to queue behind a running foreground command: pshell's
            # context was single-threaded in front, and eco's client relies on
            # BUSY meaning "your command would not run now".
            with self._foreground_lock:
                if self._foreground is not None:
                    result.status = STATUS_FAILED
                    result.exception = (
                        f"server is busy with command {self._foreground.id}: "
                        f"{self._foreground.statement!r}"
                    )
                    result.finished = time.time()
                    return result
                self._set_foreground(result)

        self._pool.submit(self._run, result)
        return result

    def _run(self, result):
        try:
            if not result.background:
                self.cancellation.reset()
            result.value = self.evaluate(result.statement)
            result.status = STATUS_COMPLETED
        except Cancelled as exc:
            result.status = STATUS_ABORTED
            result.exception = str(exc) or "aborted"
            logger.info("command %d aborted: %s", result.id, result.statement)
        except BaseException as exc:
            result.status = STATUS_FAILED
            result.exception = f"{type(exc).__name__}: {exc}"
            logger.exception("command %d failed: %s", result.id, result.statement)
        finally:
            result.finished = time.time()
            if not result.background:
                with self._foreground_lock:
                    if self._foreground is result:
                        self._set_foreground(None)

    def evaluate(self, statement):
        """Evaluate as an expression, falling back to exec for statements."""
        globals_ = {"__builtins__": SAFE_BUILTINS}
        try:
            compiled = compile(statement, "<eval>", "eval")
        except SyntaxError:
            exec(compile(statement, "<exec>", "exec"), globals_, self.namespace)
            return None
        return eval(compiled, globals_, self.namespace)

    def run_sync(self, statement, timeout=None):
        """Submit and wait. Used by the blocking ``/eval`` endpoint."""
        result = self.submit(statement)
        start = time.time()
        while result.status == STATUS_RUNNING:
            if timeout is not None and (time.time() - start) > timeout:
                raise TimeoutError(
                    f"command {result.id} still running after {timeout} s"
                )
            time.sleep(0.01)
        return result

    # ------------------------------------------------------------ control

    def abort(self, command_id=None):
        """Cancel the foreground command (or a specific one, if running)."""
        target = self._foreground
        if command_id is not None:
            target = self._results.get(int(command_id))
        if target is None or target.status != STATUS_RUNNING:
            return False
        logger.warning("aborting command %d: %s", target.id, target.statement)
        # There is one cancellation token, shared with the driver's link, so an
        # abort stops whichever command is currently touching the controller.
        self.cancellation.cancel()
        return True

    def clear_cancellation(self):
        self.cancellation.reset()

    # ------------------------------------------------------------ results

    def get(self, command_id):
        with self._lock:
            return self._results.get(int(command_id))

    def _expire_locked(self):
        cutoff = time.time() - self.time_to_live
        stale = [
            cid for cid, r in self._results.items()
            if r.finished is not None and r.finished < cutoff
        ]
        for cid in stale:
            del self._results[cid]

    def snapshot(self):
        with self._lock:
            return [r.to_dict() for r in self._results.values()]

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)


def build_namespace(robot):
    """The names an evaluated statement can see.

    Mirrors what the pshell startup scripts put in scope: the robot device,
    every pseudo-motor under its bare axis name (``gamma``, ``j3``, ``z_lin``,
    ...), and the high-level motion functions from ``script/motion/*.py``.
    """
    namespace = {"robot": robot}
    namespace.update(robot.motors)
    namespace.update(_motion_functions(robot))
    return namespace


def _motion_functions(robot):
    """Ports of ``script/motion/{tools,move_home,move_park,tweak}.py``."""

    def is_manual_mode():
        return robot.working_mode == "manual"

    def release_safety():
        """Deployment hook; the pshell version was an intentional no-op."""

    def system_check(robot_move=True):
        """Deployment hook; the pshell version was an intentional no-op."""

    def enable_motion():
        """Check safety and power the arm, unless the operator holds it."""
        release_safety()
        system_check(robot_move=True)
        if not is_manual_mode():
            if robot.state.value not in ("Ready", "Busy", "Paused"):
                raise RuntimeError(
                    f"cannot enable power: robot state is {robot.state}"
                )
            robot.enable()

    def wait_end_of_move():
        robot.update()
        while (not robot.settled) or (not robot.empty) or (not robot.is_ready()):
            robot.cancellation.raise_if_cancelled()
            time.sleep(0.01)

    def _prepare():
        robot.assert_no_task()
        robot.reset_motion()
        robot.wait_ready(timeout=1.0)
        enable_motion()

    def move_home():
        _prepare()
        if not robot.is_home():
            robot.move_home()

    def move_park():
        _prepare()
        if not robot.is_park():
            robot.move_park()

    def tweak_x(offset):
        _prepare()
        return robot.tweak_x(offset)

    def tweak_y(offset):
        _prepare()
        return robot.tweak_y(offset)

    return {
        "is_manual_mode": is_manual_mode,
        "release_safety": release_safety,
        "system_check": system_check,
        "enable_motion": enable_motion,
        "wait_end_of_move": wait_end_of_move,
        "move_home": move_home,
        "move_park": move_park,
        "tweak_x": tweak_x,
        "tweak_y": tweak_y,
    }
