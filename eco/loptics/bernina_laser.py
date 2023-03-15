from eco.loptics.position_monitors import CameraPositionMonitor
from ..elements.assembly import Assembly
from functools import partial
from ..devices_general.motors import SmaractStreamdevice, MotorRecord, SmaractRecord
from ..elements.adjustable import AdjustableMemory, AdjustableVirtual, AdjustableFS
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from ..epics.detector import DetectorPvData
from ..devices_general.detectors import DetectorVirtual
from ..timing.lasertiming_edwin import XltEpics
import colorama
import datetime
from pint import UnitRegistry
import numpy as np
import time

# from time import sleep

ureg = UnitRegistry()


class IncouplingCleanBernina(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(SmaractRecord, "SARES23:LIC17", name="tilt")
        self._append(SmaractRecord, "SARES23:LIC18", name="rotation")
        self._append(SmaractRecord, "SARES23:LIC16", name="transl_vertical")
        self._append(MotorRecord, "SARES20-MF2:MOT_5", name="transl_horizontal")


class Spectrometer(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            AdjustablePvEnum,
            pvname + ":TRIGGER",
            name="trigger_mode",
            is_setting=True,
        )
        self._append(AdjustablePvEnum, pvname + ":INIT", name="state", is_setting=True)
        self._append(
            AdjustablePv,
            pvname + ":EXPOSURE",
            name="exposure_time",
            is_setting=True,
        )
        self._append(DetectorPvData, pvname + ":CENTRE", name="center")
        self._append(DetectorPvData, pvname + ":FWHM", name="fwhm")
        self._append(DetectorPvData, pvname + ":AMPLITUDE", name="amplitude")
        self._append(DetectorPvData, pvname + ":INTEGRAL", name="integral")
        self._append(DetectorPvData, pvname + ":BASER_HEIGHT", name="base_value")
        self._append(
            AdjustablePv, pvname + ":XVAL1", name="spectrum_min", is_setting=True
        )
        self._append(
            AdjustablePv, pvname + ":XVAL2", name="spectrum_max", is_setting=True
        )
        # SLAAR02-LSPC-OSC:SERIALNR


flag_names_filter_wheel = [
    "error",
    "proc_tongle",
    "connected",
    "moving",
    "homed",
    "remote_operation",
]


class FilterWheelFlags(Assembly):
    def __init__(self, flags, name="flags"):
        super().__init__(name=name)
        self._flags = flags
        for flag_name in flag_names_filter_wheel:
            self._append(
                DetectorVirtual,
                [self._flags],
                partial(self._get_flag_name_value, flag_name=flag_name),
                name=flag_name,
                is_status=True,
                is_display=True,
            )

    def _get_flag_name_value(self, value, flag_name=None):
        index = flag_names_filter_wheel.index(flag_name)
        return int("{0:015b}".format(int(value))[-1 * (index + 1)]) == 1


class FilterWheel(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(AdjustablePvEnum, f"{pvname}.VAL", name="_val", is_setting=True)
        self._append(AdjustablePvEnum, f"{pvname}.RBV", name="_rb", is_setting=True)
        self._append(AdjustablePv, f"{pvname}.CMD", name="_cmd", is_setting=False)
        self.set_remote_operation()
        self._append(
            DetectorPvData,
            self.pvname + ".STA",
            name="_flags",
            is_setting=False,
            is_display=False,
        )
        self._append(
            FilterWheelFlags,
            self._flags,
            name="flags",
            is_display="recursive",
            is_setting=False,
            is_status=True,
        )

    def set_remote_operation(self):
        self._val(7)

    def set_manual_operation(self):
        self._val(8)

    def home(self):
        self.set_remote_operation()
        self._val(6)

    def is_moving(self):
        pass


class FilterWheelAttenuator(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self._append(FilterWheel, pvname=pvname + "IFW_A", name="wheel_1")
        self._append(FilterWheel, pvname=pvname + "IFW_B", name="wheel_2")

        self.targets_1 = {
            "t": 10 ** -np.array([0.2, 0.3, 0.5, 0.6, 1.0]),
            "pos": np.array([1, 2, 3, 4, 5]),
        }
        self.targets_2 = {
            "t": 10 ** -np.array([0.2, 0.3, 0.4, 0.5, 0.6]),
            "pos": np.array([1, 2, 3, 4, 5]),
        }

        self._calc_transmission()

    def _calc_transmission(self):
        t1 = self.targets_1["t"]
        t2 = self.targets_2["t"]
        t_comb = (
            (np.expand_dims(t1, axis=0)).T * (np.expand_dims(t2, axis=0))
        ).flatten()
        pos_comb = np.array(
            [[p1, p2] for p1 in self.targets_1["pos"] for p2 in self.targets_2["pos"]]
        )
        self.transmissions = {"t": t_comb, "pos": pos_comb}

    def home(self):
        self.wheel_1.home()
        self.wheel_2.home()


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
        self._append(
            MotorRecord, self.pvname + "-M533:MOT", name="wp_pol", is_setting=True
        )

        self._append(
            MotorRecord, self.pvname + "-M534:MOT", name="wp_att", is_setting=True
        )

        # ad hoc for 500nm setup
        # self._append(SmaractRecord, "SARES23:ESB3", name="wp_att", is_setting=True)

        self._append(
            AdjustableFS,
            "/photonics/home/gac-bernina/eco/configuration/wp_att_calibration",
            name="wp_att_calibration",
            is_display=False,
        )
        tmptime = time.time()
        while (time.time() - tmptime) < 10:
            try:
                self._append(
                    Spectrometer,
                    "SLAAR02-LSPC-OSC",
                    name="oscillator_spectrum",
                    is_setting=False,
                    is_display=True,
                )
                print("SUCCESS: oscillator spectrometer configured!")
                break
            except:
                pass

        def uJ2wp(uJ):
            direction = 1
            if np.mean(np.diff(np.asarray(self.wp_att_calibration()).T[1])) < 0:
                direction = -1
            return np.interp(
                uJ, *np.asarray(self.wp_att_calibration())[::direction].T[::-1]
            )

        def wp2uJ(wp):
            try:
                return np.interp(wp, *np.asarray(self.wp_att_calibration()).T)
            except:
                return np.nan

        self._append(
            AdjustableVirtual, [self.wp_att], wp2uJ, uJ2wp, name="pulse_energy_pump"
        )

        # self._append(
        #    MotorRecord,
        #    self.pvname + "-M522:MOTOR_1",
        #    name="delaystage_pump",
        #    is_setting=True,
        # )
        # self._append(
        #    DelayTime, self.delaystage_pump, name="delay_pump", is_setting=True
        # )
        self._append(XltEpics, name="xlt", is_setting=True, is_display="recursive")
        # Upstairs, Laser 1 LAM
        # self._append(
        #     MotorRecord,
        #     "SLAAR21-LMOT-M521:MOTOR_1",
        #     name="delaystage_pump",
        #     is_setting=True,
        # )
        self._append(
            MotorRecord,
            "SLAAR21-LMOT-M521:MOTOR_1",
            name="delaystage_pump",
            is_setting=True,
        )
        self._append(
            DelayTime,
            self.delaystage_pump,
            name="delay_pump",
            is_setting=True,
        )
        # self._append(
        #     SmaractStreamdevice,
        #     pvname="SARES23-ESB18",
        #     name="delaystage_thz",
        #     is_setting=True,
        # )



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


class PositionMonitors(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            CameraPositionMonitor,
            "SLAAR21-LCAM-CS844",
            name="table1_angle",
            is_display="recursive",
            is_status=True,
        )
        self._append(
            CameraPositionMonitor,
            "SLAAR21-LCAM-CS843",
            name="table1_position",
            is_display="recursive",
            is_status=True,
        )
        self._append(
            CameraPositionMonitor,
            "SLAAR21-LCAM-CS842",
            name="table2_angle",
            is_display="recursive",
            is_status=True,
        )
        self._append(
            CameraPositionMonitor,
            "SLAAR21-LCAM-CS841",
            name="table2_position",
            is_display="recursive",
            is_status=True,
        )
        # self._append(
        #     CameraPositionMonitor,
        #     "SLAAR21-LCAM-C511",
        #     name="opaout_focus",
        #     is_display="recursive",
        #     is_status=True,
        # )
        # self._append(CameraPositionMonitor, 'SLAAR21-LCAM-C541', name='cam541')
        # self._append(CameraPositionMonitor, 'SLAAR21-LCAM-C542', name='cam542')
