import sys

sys.path.append("..")

from ..devices_general.motors import MotorRecord
from ..devices_general.smaract import SmarActRecord
from ..epics.adjustable import AdjustablePv
from ..aliases import Alias, append_object_to_object
from ..elements.assembly import Assembly
from .profile_monitors import Pprm_dsd
from .intensity_monitors import SolidTargetDetectorPBPS_new_assembly
import numpy as np


class DownstreamDiagnostic(Assembly):
    def __init__(
        self,
        name=None,
    ):
        super().__init__(name=name)
        self._append(
            MotorRecord, "SARES20-DSD:MOTOR_DSDX", name="xbase", is_setting=True
        )
        self._append(
            MotorRecord, "SARES20-DSD:MOTOR_DSDY", name="ybase", is_setting=True
        )
        self._append(
            Pprm_dsd,
            "SARES20-DSDPPRM",
            "SARES20-DSDPPRM",
            name="prof_dsd",
            is_setting=True,
            is_status="recursive",
            view_toplevel_only=False,
        )
        self._append(
            SolidTargetDetectorPBPS_new_assembly,
            pvname="SARES20-DSDPBPS",
            name="mon_dsd",
            is_setting=True,
            is_status="recursive",
            view_toplevel_only=False,
        )

    def get_xyposition_for_kb_angles_in_rad(self, theta_kbver, theta_kbhor):
        y = np.tan(2 * theta_kbver) * 7075
        x = np.tan(2 * theta_kbhor) / np.cos(2 * theta_kbver) * 6325
        return x, y
