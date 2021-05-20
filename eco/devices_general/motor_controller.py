from ..elements.assembly import Assembly
from ..devices_general.adjustable import PvRecord


class MforceChannel(Assembly):
    def __init__(self, pv_motor, pv_mcodebase, name=None):
        super().__init__(name=name)
        self.pv_motor = pv_motor  # Example SARES20-MF1:
        self.pv_mcodebase = pv_mcodebase
        self._append(
            PvRecord,
            self.pv_mcodebase + "_RC",
            name="run_current",
            is_setting=True,
        )
        self._append(
            PvRecord,
            self.pv_mcodebase + "_HC",
            name="holding_current",
            is_setting=True,
        )
        self._append(
            PvRecord,
            self.pv_motor + ".DESC",
            name="display_name",
            is_setting=True,
        )
        self._append(
            PvRecord,
            self.pv_motor + ".EGU",
            name="units",
            is_setting=True,
        )
        self._append(
            PvRecord,
            self.pv_motor + ".MRES",
            name="motor_resolution",
            is_setting=True,
        )
        self._append(
            PvRecord,
            self.pv_motor + ".ERES",
            name="encoder_resolution",
            is_setting=True,
        )
        self._append(
            PvRecord,
            self.pv_motor + ".VELO",
            name="velocity",
            is_setting=True,
        )

        # self._append(
        # PvRecord,
        # self.pv_base + self.port + "_set",
        # name="limit_switch_I",
        # is_setting=True,
        # )  # IS=1,2,0
        # # IS=1,3,0 set wire 1 (1,3) to high limit, active when at 0
        # self._append(
        # PvRecord,
        # self.pv_base + self.port + "_set",
        # name="limit_switch_II",
        # is_setting=True,
        # )  # IS=2,3,0
        # # IS=2,2,0 set wire 2 (2,2) to low limit, active when at 0
