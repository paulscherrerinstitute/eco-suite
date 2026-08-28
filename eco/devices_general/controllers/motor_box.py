"""One physical multi-axis EPICS motor-record controller box.

The "P=<prefix>, M=MOT_<n>" pattern shared by Bernina's MForce (Schneider
IMS), Deltatau/PowerBrick and Newport XPS motor controllers -- each is one
physical box exposing N EPICS motor records under one PV prefix, and each
has exactly one caqtdm panel showing the whole box (`/ioc/qt/
ESB_MFORCE_motors.ui`, `/ioc/qt/ESB_XPS1.ui`, ...). In `eco.bernina.bernina`
today, individual axes of these same boxes are instead wired up one at a
time under whatever function-specific name each experiment needed -- e.g.
`SARES20-MF1:MOT_13`/`_14` show up there as `x_target_totem`/`y_target_totem`,
`SARES20-XPS1:MOT_1`/`_2` as `slit_hor`/`slit_ver` -- with no single object
representing the box itself. Those existing, working, function-named
components are left exactly as they are; `MotorControllerBox` is an
additional, complete view of the same hardware by physical box instead of
by function, living in the separate `controllers` namespace branch (see
`eco.devices_general.controllers`).
"""

from eco.devices_general.motors import MotorRecord
from eco.elements.assembly import Assembly
from eco.epics_utils.ioc_mixin import IOCMixin


class MotorControllerBox(Assembly, IOCMixin):
    """`axis_1`..`axis_<n_axes>`, one `MotorRecord` each.

    `n_axes` is fixed per box (read off the box's own caqtdm panel's
    `numberOfItems`, not auto-discovered) since an axis PV that isn't
    physically wired up simply fails softly -- `Assembly._append`'s default
    `optional=True` -- rather than blocking the rest of the box.
    """

    def __init__(self, prefix, n_axes, name=None, ioc_name=None):
        super().__init__(name=name)
        self.prefix = prefix
        self.n_axes = n_axes
        if ioc_name:
            self._ioc_name = ioc_name
        for i in range(1, n_axes + 1):
            self._append(MotorRecord, f"{prefix}:MOT_{i}", name=f"axis_{i}", is_setting=True)

    def axes(self):
        return [getattr(self, f"axis_{i}") for i in range(1, self.n_axes + 1)]
