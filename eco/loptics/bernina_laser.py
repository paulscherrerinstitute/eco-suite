from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice, MotorRecord
from ..elements.adjustable import AdjustableMemory, AdjustableVirtual, AdjustableFS
from ..timing.lasertiming_edwin import XltEpics
import colorama
import datetime
from pint import UnitRegistry
import numpy as np

# from time import sleep

ureg = UnitRegistry()

class IncouplingCleanBernina(Assembly):
    def __init__(self,  name=None):
        super().__init__(name=name)
        self._append(SmaractStreamdevice,"SARES23-ESB13",name='tilt')
        self._append(SmaractStreamdevice,"SARES23-ESB14",name='rotation')
        self._append(SmaractStreamdevice,"SARES23-LIC15",name='transl_vertical')
        self._append(MotorRecord,"SARES20-MF2:MOT_5",name='transl_horizontal')



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
        self._append(MotorRecord, self.pvname + "-M533:MOT", name="wp_pol", is_setting=True)
        self._append(
            MotorRecord, self.pvname + "-M534:MOT", name="wp_att", is_setting=True
        )
        self._append(AdjustableFS,'/photonics/home/gac-bernina/eco/configuration/wp_att_calibration',name='wp_att_calibration')
        
        def uJ2wp(uJ):
            direction = 1
            if np.mean(np.diff(np.asarray(self.wp_att_calibration()).T[1]))<0:
                direction = -1
            return np.interp(uJ,*np.asarray(self.wp_att_calibration())[::direction].T[::-1])
        def wp2uJ(wp):
            return np.interp(wp,*np.asarray(self.wp_att_calibration()).T)
        
        self._append(AdjustableVirtual,[self.wp_att],wp2uJ,uJ2wp,name='pulse_energy_pump')
        
        self._append(
            MotorRecord,
            self.pvname + "-M522:MOTOR_1",
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
            "SLAAR21-LMOT-M521:MOTOR_1",
            name="delaystage_eos",
            is_setting=True,
        )
        self._append(
            DelayTime,
            self.delaystage_eos,
            name="delay_eos",
            is_setting=True,
        )
        self._append(
            SmaractStreamdevice,
            pvname="SARES23-ESB18",
            name="delaystage_thz",
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
