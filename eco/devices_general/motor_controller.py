from ..elements.assembly import Assembly
from ..devices_general.adjustable import PvRecord


class MforceChannel(Assembly):
    def __init__(self, pv_base, port):
        self.pv_base = pv_base  # Example SARES20-MF1:
        self.port = port  # Example 15
        self._append(
            PvRecord,
            self.pv_base + self.port + "_RC",
            name="no_idea_what_this_is",
            is_setting=True,
        )  
        self._append(
            PvRecord,
            self.pv_base + "MOT_" + self.port + ".DESC",
            name="display_name",
            is_setting=True,
        )  
        self._append(
            PvRecord,
            self.pv_base + "MOT_" + self.port + ".EGU",
            name="units",
            is_setting=True,
        )  
        self._append(
            PvRecord,
            self.pv_base + "MOT_" + self.port + ".MRES",
            name="motor_resolution",
            is_setting=True,
        )  
        self._append(
            PvRecord,
            self.pv_base + "MOT_" + self.port + ".ERES",
            name="encoder_resolution",
            is_setting=True,
        )  
        self._append(
            PvRecord,
            self.pv_base + "MOT_" + self.port + ".VELO",
            name="velocity",
            is_setting=True,
        ) 

        self._append(
            PvRecord,
            self.pv_base + self.port + "_set",
            name="limit_switch_I",
            is_setting=True,
        )  # IS=1,2,0
        # IS=1,3,0 set wire 1 (1,3) to high limit, active when at 0
        self._append(
            PvRecord,
            self.pv_base + self.port + "_set",
            name="limit_switch_II",
            is_setting=True,
        )  # IS=2,3,0
        # IS=2,2,0 set wire 2 (2,2) to low limit, active when at 0
