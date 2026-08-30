"""Bernina robot server -- the replacement for the pshell robot deployment.

Runs the Stäubli TX200 detector arm as a standalone service: it owns the single
TCP connection to the VAL3 controller, polls it, and exposes the arm to clients
over HTTP + server-sent events and over EPICS channel access.

Run it::

    python -m eco.bernina_robot_server --simulated       # no hardware needed
    python -m eco.bernina_robot_server --config /path/to/robot.json

Talk to it from eco::

    bernina.rob            # eco.endstations.bernina_robots.StaeubliTx200

The HTTP surface is deliberately wire-compatible with the pshell server the
eco client was written against, so no eco-side change is needed to point at
this one. See ``README.md`` in this package for the full manual, and
:mod:`eco.robots` for the hardware driver underneath.
"""

from .app import VERSION, RobotServerApp
from .config import RobotServerConfig
from .events import EventBus
from .server import create_app

__all__ = ["RobotServerApp", "RobotServerConfig", "EventBus", "create_app",
           "VERSION"]
