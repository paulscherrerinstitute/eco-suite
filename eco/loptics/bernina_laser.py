from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice, MotorRecord
from ..elements.adjustable import AdjustableMemory, AdjustableVirtual
from ..timing.lasertiming_edwin import XltEpics
import colorama
import datetime
from pint import UnitRegistry

# from time import sleep

ureg = UnitRegistry()


class LaserBernina(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        # Table 1, Benrina hutch
        self._append(
            MotorRecord,
            "SLAAR21-LMOT-M523:MOTOR_1",
            name="delaystage_glob",
            is_setting=True,
        )
        self._append(
            DelayTime, self.delaystage_glob, name="delay_glob", is_setting=True
        )
        # Table 2, Bernina hutch
        self._append(
            MotorRecord, self.pvname + "-M532:MOT", name="compressor", is_setting=True
        )
        # Waveplate and Delay stage
        self._append(MotorRecord, self.pvname + "-M533:MOT", name="wp", is_setting=True)
        self._append(
            MotorRecord, self.pvname + "-M534:MOT", name="wp_aux1", is_setting=True
        )
        self._append(
            MotorRecord,
            self.pvname + "-M521:MOTOR_1",
            name="delaystage_pump",
            is_setting=True,
        )
        self._append(
            DelayTime, self.delaystage_pump, name="delay_pump", is_setting=True
        )
        self._append(XltEpics, name="xlt", is_setting=True, is_status="recursive")
        # Upstairs, Laser 1 LAM
        self._append(
            MotorRecord,
            "SLAAR01-LMOT-M252:MOT",
            name="delaystage_lam_upstairs",
            is_setting=True,
        )
        self._append(
            DelayTime,
            self.delaystage_lam_upstairs,
            name="delay_lam_upstairs",
            is_setting=True,
        )


class DelayTime(AdjustableVirtual):
    def __init__(
        self, stage, direction=1, passes=2, reset_current_value_to=True, name=None
    ):
        self._direction = direction
        self._group_velo = 299798458  # m/s
        self._passes = passes
        # self.Id = stage.Id + "_delay"
        self._stage = stage
        AdjustableVirtual.__init__(
            self,
            [stage],
            self._mm_to_s,
            self._s_to_mm,
            reset_current_value_to=reset_current_value_to,
            name=name,
            unit="s",
        )

    def _mm_to_s(self, mm):
        return mm * 1e-3 * self._passes / self._group_velo * self._direction

    def _s_to_mm(self, s):
        return s * self._group_velo * 1e3 / self._passes * self._direction

    def __repr__(self):
        s = ""
        s += f"{colorama.Style.DIM}"
        s += datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S") + ": "
        s += f"{colorama.Style.RESET_ALL}"
        s += f"{colorama.Style.BRIGHT}{self._get_name()}{colorama.Style.RESET_ALL} at "
        s += f"{self.get_current_value():g} s"
        s += f" ({(self.get_current_value()*ureg.second).to_compact():P~6.3f})"
        s += f"{colorama.Style.RESET_ALL}"
        return s

    def get_limits(self):
        return [self._mm_to_s(tl) for tl in self._stage.get_limits()]

    def set_limits(self, low_limit, high_limit):
        lims_stage = [self._s_to_mm(tl) for tl in [low_limit, high_limit]]
        lims_stage.sort()
        self._stage.set_limits(*lims_stage)

        return [self._mm_to_s(tl) for tl in self._stage.get_limits()]


class DelayCompensation(AdjustableVirtual):
    """Simple virtual adjustable for compensating delay adjustables. It assumes the first adjustable is the master for
    getting the current value."""

    def __init__(self, adjustables, directions, set_current_value=True, name=None):
        self._directions = directions
        self.Id = name
        AdjustableVirtual.__init__(
            self,
            adjustables,
            self._from_values,
            self._calc_values,
            set_current_value=set_current_value,
            name=name,
        )

    def _calc_values(self, value):
        return tuple(tdir * value for tdir in self._directions)

    def _from_values(self, *args):
        positions = [ta * tdir for ta, tdir in zip(args, self._directions)]
        return positions[0]

        tuple(tdir * value for tdir in self._directions)

    def __repr__(self):
        s = ""
        s += f"{colorama.Style.DIM}"
        s += datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S") + ": "
        s += f"{colorama.Style.RESET_ALL}"
        s += f"{colorama.Style.BRIGHT}{self._get_name()}{colorama.Style.RESET_ALL} at "
        s += f"{(self.get_current_value()*ureg.second).to_compact():P~6.3f}"
        s += f"{colorama.Style.RESET_ALL}"
        return s
