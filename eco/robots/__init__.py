"""Direct Python control of Stäubli VAL3 robot arms.

Layered so each level is usable on its own:

===========================  ====================================================
:mod:`~eco.robots.protocol`  VAL3 line protocol over TCP. No robot semantics.
:mod:`~eco.robots.staeubli`  Generic arm: modes, power, motion, tasks, polling.
:mod:`~eco.robots.kinematics` Pure spherical<->cartesian detector conversion.
:mod:`~eco.robots.motors`    Pseudo-motors: one axis of a coordinate system.
:mod:`~eco.robots.bernina`   The Bernina TX200: frames, safety, recordings.
===========================  ====================================================

This replaces the PSI-pshell (Java/Jython) deployment described in
``/sf/bernina/config/src/python/bernina_robot``. It imports nothing from the
rest of eco, so the robot server can start without the full EPICS/cam_server
stack; see :mod:`eco.bernina_robot_server` for the service built on top.

Quick start against the real controller::

    from eco.robots import connect_bernina_robot

    robot = connect_bernina_robot("129.129.243.106", 1234)
    robot.setup()
    robot.start_polling()
    robot.get_spherical_pos()          # {'r': ..., 'gamma': ..., 'delta': ...}
    robot.move_spherical(gamma=20, sync=True)

and with no hardware at all::

    robot = connect_bernina_robot(simulated=True)
"""

from .bernina import (
    DEFAULT_REMOTE_ALLOWED_DELTAS,
    FRAME_HIERARCHY,
    KNOWN_POINTS,
    PVBASE,
    SPHERICAL_AXES,
    TASKS,
    BerninaRobot,
)
from .kinematics import cart2sph, sph2cart
from .motors import (
    CartesianMotor,
    JointMotor,
    LinearAxisMotor,
    PseudoMotor,
    SphericalMotor,
)
from .persistence import JsonValue, MemoryValue
from .protocol import (
    Cancelled,
    CancellationToken,
    TcpTransport,
    Val3Error,
    Val3Link,
    Val3ProtocolError,
    Val3Timeout,
)
from .simulation import SimulatedController, SimulatedTransport
from .staeubli import (
    CARTESIAN_AXES,
    JOINT_AXES,
    RobotError,
    RobotState,
    StaeubliRobot,
)

#: Where the pshell deployment kept its persisted adjustables. Reused verbatim
#: so a migrated server inherits the existing frame/tool selection and, more
#: importantly, the recorded remote-motion trajectories.
DEFAULT_ADJUSTABLES_PATH = (
    "/sf/bernina/config/src/python/bernina_robot/adjustables_fs/"
)


def connect_bernina_robot(host=None, port=1234, simulated=False, timeout=1.0,
                          retries=1, adjustables_path=None, name="robot",
                          persist=None, **kwargs):
    """Build a :class:`~eco.robots.bernina.BerninaRobot`, connected but idle.

    Call ``setup()`` and ``start_polling()`` yourself -- both have side effects
    on the controller, so they are not done implicitly.

    ``persist`` decides whether frame/tool/recorded-motion state is written to
    ``adjustables_path``. It defaults to False when ``simulated`` and True
    otherwise, so a simulated robot never touches the shared production files
    in :data:`DEFAULT_ADJUSTABLES_PATH`. Those files live under the
    ``gac-bernina`` account and are read-write for whichever account created
    them, so a stray write from a personal checkout leaves files the real
    server can no longer update -- the same shared-account hazard eco's
    ``utilities.tempfiles`` exists to avoid. Pass ``persist=True`` explicitly
    if a simulated run really should share that state.
    """
    if persist is None:
        persist = not simulated
    if simulated:
        transport = SimulatedController()
    else:
        if host is None:
            raise ValueError("host is required unless simulated=True")
        transport = TcpTransport(host, port)
    values = JsonValue if persist else MemoryValue
    link = Val3Link(transport, timeout=timeout, retries=retries)
    return BerninaRobot(
        name=name,
        link=link,
        adjustables_path=(adjustables_path or DEFAULT_ADJUSTABLES_PATH)
                         if persist else None,
        value_factory=values,
        **kwargs,
    )


__all__ = [
    "BerninaRobot", "StaeubliRobot", "RobotError", "RobotState",
    "Val3Link", "TcpTransport", "SimulatedController", "SimulatedTransport",
    "Val3Error", "Val3ProtocolError", "Val3Timeout",
    "Cancelled", "CancellationToken",
    "PseudoMotor", "CartesianMotor", "SphericalMotor", "JointMotor",
    "LinearAxisMotor",
    "JsonValue", "MemoryValue",
    "sph2cart", "cart2sph",
    "connect_bernina_robot", "DEFAULT_ADJUSTABLES_PATH",
    "JOINT_AXES", "CARTESIAN_AXES", "SPHERICAL_AXES",
    "KNOWN_POINTS", "TASKS", "FRAME_HIERARCHY", "PVBASE",
    "DEFAULT_REMOTE_ALLOWED_DELTAS",
]
