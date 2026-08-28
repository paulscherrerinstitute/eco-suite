"""Controller-box Assemblies for Bernina, grouped in one lazy branch.

See `README.md` in this package for the rationale (many controller PVs are
today wired up one channel at a time, scattered across unrelated
experiment-specific assemblies, with no object representing the physical
controller box a real caqtdm panel shows) and `bernina_controllers.py` for
the concrete Bernina boxes and their provenance.
"""

from .bernina_controllers import (
    MOTOR_CONTROLLER_BOXES,
    WAGO_CONTROLLER_BOXES,
    build_bernina_controllers,
)
from .lazy_container import LazyControllers
from .motor_box import MotorControllerBox
from .wago_controller import WagoController

__all__ = [
    "build_bernina_controllers",
    "MOTOR_CONTROLLER_BOXES",
    "WAGO_CONTROLLER_BOXES",
    "LazyControllers",
    "MotorControllerBox",
    "WagoController",
]
