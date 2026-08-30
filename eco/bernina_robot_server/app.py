"""The robot server application -- what pshell's ``Context`` used to be.

Owns exactly four things and wires them together:

===================  =========================================================
robot                a :class:`~eco.robots.bernina.BerninaRobot` and its poller
executor             the command queue behind ``/eval`` and ``/evalAsync``
events               the SSE fan-out
epics                the optional CA server publishing the axes
===================  =========================================================

Threads (all daemons, so a Ctrl-C actually exits):

* **poller** -- inside the robot; calls ``update()`` every
  ``polling_interval``, and re-broadcasts each result.
* **command workers** -- one foreground slot plus a small background pool.
* **CA server** -- pcaspy's own thread.
* **HTTP** -- werkzeug's threaded WSGI server, one thread per request; SSE
  requests hold theirs for the life of the connection.

The only shared mutable state between them is the robot, and every access to
the controller funnels through the single lock inside
:class:`~eco.robots.protocol.Val3Link`.
"""

from __future__ import annotations

import collections
import logging
import threading
import time

from ..robots import connect_bernina_robot
from ..robots.protocol import Val3Link
from ..robots.staeubli import RobotState
from .commands import CommandExecutor, build_namespace
from .config import RobotServerConfig
from .epics_pvs import EpicsPublisher
from .events import EventBus

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

#: Robot events forwarded verbatim to SSE clients. "robot_state" is included;
#: "state" is reserved for the *server* state, which is what eco's client
#: gates its commands on.
FORWARDED_EVENTS = ("polling", "motion", "reset_motion", "stop", "shell",
                    "robot_state")


class RingLogHandler(logging.Handler):
    """Keeps the last N log records for the ``/logs`` endpoint.

    pshell served its log file over HTTP; this keeps the same affordance
    without owning a file, in the same 5-tuple shape the old client's
    ``print_logs`` expects: ``[date, time, origin, level, message]``.
    """

    def __init__(self, size=500):
        super().__init__()
        self.records = collections.deque(maxlen=size)

    def emit(self, record):
        try:
            stamp = time.localtime(record.created)
            self.records.append([
                time.strftime("%d/%m/%y", stamp),
                time.strftime("%H:%M:%S", stamp) + ".%03d" % (record.msecs),
                record.name,
                record.levelname.capitalize(),
                record.getMessage(),
            ])
        except Exception:
            pass

    def snapshot(self):
        return list(self.records)


class RobotServerApp:

    def __init__(self, config: RobotServerConfig):
        self.config = config
        self.version = VERSION
        self.started = time.time()
        self.events = EventBus()
        self.log_handler = RingLogHandler(config.log_ring_size)
        logging.getLogger().addHandler(self.log_handler)

        self._state = "Initializing"
        self._state_lock = threading.Lock()
        self._fault_reason = None
        self._shutdown = threading.Event()

        self.robot = connect_bernina_robot(
            host=None if config.simulated else config.robot_host,
            port=config.robot_port,
            simulated=config.simulated,
            timeout=config.robot_timeout,
            retries=config.robot_retries,
            adjustables_path=config.adjustables_path,
            persist=config.persist_state,
            override_remote_safety=config.override_remote_safety,
            env_polling_interval=config.env_polling_interval,
        )
        self.robot.polling_interval = config.polling_interval
        self.robot.link.latency = config.robot_latency
        self.robot.add_listener(self._on_robot_event)

        self.executor = CommandExecutor(
            namespace={},
            cancellation=self.robot.cancellation,
            max_background=config.max_background_commands,
            time_to_live=config.command_time_to_live,
            on_state_change=self.set_state,
        )
        self.epics = EpicsPublisher(self.robot, prefix=config.epics_prefix)

    # --------------------------------------------------------------- state

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, value):
        with self._state_lock:
            if value == self._state:
                return
            self._state = value
        logger.info("server state -> %s", value)
        # The pshell contract: this event name carries the *application* state.
        self.events.publish("state", value)

    # -------------------------------------------------------------- events

    def _on_robot_event(self, kind, payload):
        if kind not in FORWARDED_EVENTS:
            return
        if kind == "polling":
            self.epics.publish(payload.get("pos") or {})
        self.events.publish(kind, payload)

    # ------------------------------------------------------------ lifecycle

    def start(self):
        """Connect, set the arm up, and begin polling.

        A controller that is unreachable at startup is not fatal: the server
        comes up in FAULT and the poller keeps retrying, so clients get a
        meaningful state instead of a refused connection. That is a deliberate
        change from pshell, where a failing startup script left the whole
        context in Fault with the ``robot`` device simply absent from the
        device pool -- which is exactly the condition the live server is in
        today, and it makes the failure look like a missing feature rather
        than a down link.
        """
        self.set_state("Initializing")
        try:
            self.robot.link.transport.connect()
            if self.config.setup_on_start:
                self.robot.setup()
            self._fault_reason = None
        except Exception as exc:
            self._fault_reason = f"{type(exc).__name__}: {exc}"
            logger.error("robot setup failed: %s", self._fault_reason)
            logger.info("continuing in Fault; the poller will keep retrying")

        # Build the eval namespace after setup, so the motors exist in it.
        self.executor.namespace = build_namespace(self.robot)

        if self.config.epics_enabled:
            self.epics.start()

        self.robot.start_polling()
        threading.Thread(target=self._recovery_loop, name="robot-recovery",
                         daemon=True).start()
        self.set_state("Fault" if self._fault_reason else "Ready")
        return self

    def _recovery_loop(self):
        """Re-run setup once the controller comes back after a dropout.

        The pshell version handled this by calling ``get_context().restart()``
        from ``on_reconnected`` -- restarting the entire application, dropping
        every client connection with it. Re-running setup in place is enough:
        the motors are re-created, the frame and tool are re-applied, and the
        namespace picks up the new motor objects.
        """
        was_offline = True
        while not self._shutdown.wait(2.0):
            offline = self.robot.state == RobotState.OFFLINE or not self.robot.connected
            if offline:
                was_offline = True
                if self._state != "Fault":
                    self.set_state("Fault")
                continue
            if was_offline:
                was_offline = False
                try:
                    logger.info("controller reachable again; re-running setup")
                    self.robot.setup()
                    self.executor.namespace = build_namespace(self.robot)
                    if self.config.epics_enabled and not self.epics.running:
                        self.epics.start()
                    self._fault_reason = None
                    if not self.executor.busy:
                        self.set_state("Ready")
                except Exception as exc:
                    self._fault_reason = f"{type(exc).__name__}: {exc}"
                    logger.error("setup after reconnect failed: %s",
                                 self._fault_reason)
                    was_offline = True

    def stop(self):
        self._shutdown.set()
        self.robot.stop_polling()
        self.executor.shutdown()
        self.epics.stop()
        try:
            self.robot.link.close()
        except Exception:
            pass
        logging.getLogger().removeHandler(self.log_handler)
        self.set_state("Closing")

    def restart(self):
        """``:restart`` -- re-run setup without dropping client connections."""
        logger.info("restart requested")
        self.robot.stop_polling()
        try:
            self.robot.link.close()
            self.robot.link.transport.connect()
            self.robot.setup()
            self.executor.namespace = build_namespace(self.robot)
            self._fault_reason = None
            self.set_state("Ready")
        except Exception as exc:
            self._fault_reason = f"{type(exc).__name__}: {exc}"
            self.set_state("Fault")
            raise
        finally:
            self.robot.start_polling()
        return "restarted"

    # -------------------------------------------------------------- status

    def status(self):
        return {
            "version": self.version,
            "state": self.state,
            "fault_reason": self._fault_reason,
            "uptime_s": round(time.time() - self.started, 1),
            "robot": {
                "state": str(self.robot.state),
                "connected": self.robot.connected,
                "mode": self.robot.working_mode,
                "status": self.robot.status,
                "powered": self.robot.powered,
                "speed": self.robot.speed,
                "task": self.robot.current_task,
                "frame": self.robot.frame(),
                "tool": self.robot.tool(),
                "simulated": self.robot.simulated,
                "override_remote_safety": self.robot.override_remote_safety,
            },
            "controller": {
                "host": self.config.robot_host,
                "port": self.config.robot_port,
            },
            "motors": sorted(self.robot.motors),
            "epics": {"running": self.epics.running,
                      "pvs": self.epics.pv_names()},
            "subscribers": self.events.subscriber_count,
        }
