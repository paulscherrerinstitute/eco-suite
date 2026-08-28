"""Concrete Bernina controller boxes, grouped into one lazy `controllers` branch.

Wire this into `eco.bernina.bernina` as a single lazy, deselectable
top-level namespace entry::

    from eco.devices_general.controllers import build_bernina_controllers
    namespace.append_obj(build_bernina_controllers, lazy=True, name="controllers")

Every box here is additional: none of it replaces or removes any of the
individually-named, function-specific components already registered
elsewhere in `bernina.py` for the same underlying PVs (see
`eco.devices_general.controllers.motor_box`/`wago_controller`'s docstrings)
-- this only adds a second, physical-box-shaped view of the same hardware,
opt-in via `bernina.controllers.<name>`.

Axis counts (`MOTOR_CONTROLLER_BOXES`) come from each box's own caqtdm panel
`numberOfItems`, found via `$CAQTDM_DISPLAY_PATH`:

=====================  =====  =======  ==========================================
prefix                 axes   family   source panel
=====================  =====  =======  ==========================================
SARES20-MF1            16     MForce   /ioc/qt/ESB_MFORCE_motors.ui
SARES20-MF2            16     MForce   /ioc/qt/ESB_MFORCE_motors.ui
SARES20-XPS1            6     XPS      /ioc/qt/ESB_XPS1.ui
SARES20-EXP             8     Deltatau /ioc/qt/_obsolete/ESB_UsrExp.ui (*)
=====================  =====  =======  ==========================================

(*) The launcher (`S_motion.json`, "Deltatau User Stages (SARES20-EXP)")
points at a bare filename `ESB_UsrExp.ui` that only resolves under an
`_obsolete/` subdirectory of `$CAQTDM_DISPLAY_PATH`, not at any current
top-level location -- so its axis count is lower-confidence than the other
three. Unwired/incorrect axis indices fail softly (`optional=True`), so a
wrong count here just shows as extra/missing `FailedComponent` entries
rather than breaking anything -- adjust `n_axes` once confirmed.

IOC names are looked up lazily and cached (see `IOCMixin`) rather than
hardcoded here, except for the MForce boxes: `eco.epics_utils.iocinfo`'s
module docstring already identifies their IOC names explicitly
(`SARES20-CSSU-MF1`/`-MF2`, confirmed to also be the restart-capable
console), so those are pinned directly to skip the search.
"""

from .lazy_container import LazyControllers
from .motor_box import MotorControllerBox
from .wago_controller import WagoController

#: (prefix, n_axes, ioc_name or None) -- see module docstring.
MOTOR_CONTROLLER_BOXES = {
    "mforce_mf1": ("SARES20-MF1", 16, "SARES20-CSSU-MF1"),
    "mforce_mf2": ("SARES20-MF2", 16, "SARES20-CSSU-MF2"),
    "xps1": ("SARES20-XPS1", 6, None),
    "deltatau_exp": ("SARES20-EXP", 8, None),
}

#: name -> (prefix, ioc_name or None)
WAGO_CONTROLLER_BOXES = {
    "wago_gps01": ("SARES20-CWAG-GPS01", None),
}


def build_bernina_controllers(name=None):
    """Factory for the `controllers` namespace branch -- cheap to call (just
    registers lazy proxies, builds nothing) so it's safe as a `Namespace.
    append_obj(..., lazy=True)` target."""
    controllers = LazyControllers(name=name)
    for cname, (prefix, n_axes, ioc_name) in MOTOR_CONTROLLER_BOXES.items():
        controllers.add(MotorControllerBox, prefix, n_axes, ioc_name=ioc_name, name=cname)
    for cname, (prefix, ioc_name) in WAGO_CONTROLLER_BOXES.items():
        controllers.add(WagoController, prefix, ioc_name=ioc_name, name=cname)
    return controllers
