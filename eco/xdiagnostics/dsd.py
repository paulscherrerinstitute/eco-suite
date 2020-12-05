import sys

sys.path.append("..")

from ..devices_general.motors import MotorRecord
from ..devices_general.smaract import SmarActRecord
from ..devices_general.adjustable import PvRecord
from ..aliases import Alias, append_object_to_object
from ..elements.assembly import Assembly
from .profile_monitors import Pprm_dsd
from .intensity_monitors import SolidTargetDetectorPBPS_new_assembly

class DownstreamDiagnostic(Assembly):
    def __init__(
        self,
        name=None,
    ):
        super().__init__(name=name)
        self._append(MotorRecord, "SARES20-DSD:MOTOR_DSDX", name="xbase", is_setting=True)
        self._append(MotorRecord, "SARES20-DSD:MOTOR_DSDY", name="ybase", is_setting=True)
        self._append(Pprm_dsd, "SARES20-DSDPPRM", "SARES20-DSDPPRM", name="prof_dsd", is_setting=True, view_toplevel_only = False)
        self._append(SolidTargetDetectorPBPS_new_assembly, pvname= "SARES20-DSDPBPS", name="mon_dsd", is_setting = True, view_toplevel_only=False)
