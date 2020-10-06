from ..elements.assembly import Assembly
from ..devices_general.adjustable import PvRecord


class MforceChannel(Assembly):
    def __init__(self, pv_base=None):
        self.pv_base = pv_base  # Example SARES20-MF1:
        self.motor_nb = motor_nb  # Example 15
        self._append(
            PvRecord,
            self.pv_base + self.motor_nb + "_RC",
            name="no_idea_what_this_is",
            is_setting=True,
        )  # 8
        self._append(
            PvRecord,
            self.pv_base + "MOT_" + self.motor_nb + ".DESC",
            name="display_name",
            is_setting=True,
        )  # X-ray Eye
        self._append(
            PvRecord,
            self.pv_base + "MOT_" + self.motor_nb + ".EGU",
            name="units",
            is_setting=True,
        )  # %
        self._append(
            PvRecord,
            self.pv_base + "MOT_" + self.motor_nb + ".MRES",
            name="motor_resolution",
            is_setting=True,
        )  # 0.00002886
        self._append(
            PvRecord,
            self.pv_base + "MOT_" + self.motor_nb + ".ERES",
            name="encoder_resolution",
            is_setting=True,
        )  # 0.00002886
        self._append(
            PvRecord,
            self.pv_base + "MOT_" + self.motor_nb + ".VELO",
            name="velocity",
            is_setting=True,
        )  # 10

        self._append(
            PvRecord,
            self.pv_base + self.motor_nb + "_set",
            name="limit_switch_I",
            is_setting=True,
        )  # IS=1,2,0
        # IS=1,3,0 set wire 1 (1,3) to high limit, active when at 0
        self._append(
            PvRecord,
            self.pv_base + self.motor_nb + "_set",
            name="limit_switch_II",
            is_setting=True,
        )  # IS=2,3,0
        # IS=2,2,0 set wire 2 (2,2) to low limit, active when at 0
