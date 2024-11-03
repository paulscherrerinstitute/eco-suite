from eco.loptics.position_monitors import CameraPositionMonitor
from ..elements.assembly import Assembly
from functools import partial
from ..devices_general.motors import (
    SmaractStreamdevice,
    MotorRecord,
    SmaractRecord,
    ThorlabsPiezoRecord,
)
from ..elements.adjustable import (
    AdjustableMemory,
    AdjustableVirtual,
    AdjustableFS,
    spec_convenience,
    update_changes,
    value_property,
    tweak_option,
)
from eco.devices_general.utilities import Changer

from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from ..epics.detector import DetectorPvData, DetectorPvString
from ..devices_general.detectors import DetectorVirtual
from ..timing.lasertiming_edwin import XltEpics, LaserRateControl
import colorama
import datetime
from pint import UnitRegistry
import numpy as np
import time
from epics import PV

# from time import sleep

ureg = UnitRegistry()


class IncouplingCleanBernina(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(SmaractRecord, "SARES23-LIC:MOT_17", name="tilt")
        self._append(SmaractRecord, "SARES23-LIC:MOT_18", name="rotation")
        self._append(SmaractRecord, "SARES23-LIC:MOT_16", name="transl_vertical")
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
        self._append(DetectorPvData, pvname + ":BASE_HEIGHT", name="base_value")
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


class StageLxtDelay(Assembly):
    def __init__(self, fine_delay_adj, las, direction=1, name=None):
        super().__init__(name=name)
        self._append(fine_delay_adj, name="_fine_delay_adj", is_setting=True)
        self._append(las.xlt, name="_coarse_delay_adj", is_setting=True)
        self._append(AdjustableMemory, direction, name="_direction", is_setting=True)
        self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/{name}_combined_delay_phase_shifter_threshold",
            name="switch_threshold",
            default_value=50e-12,
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/{name}_conbined_fine_adj_offset",
            name="offset_fine_adj",
            default_value=0.0,
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/{name}_combined_coarse_adj_offset",
            name="offset_coarse_adj",
            default_value=0.0,
            is_setting=True,
        )

        self._append(
            AdjustableVirtual,
            [self._fine_delay_adj, self._coarse_delay_adj],
            self._get_comb_delay,
            self._set_comb_delay,
            name="delay",
            unit="s",
        )

    def _get_comb_delay(self, pd, ps):
        ps_rel = ps - self.offset_coarse_adj()
        pd_rel = pd - self.offset_fine_adj()
        return (ps_rel + pd_rel) * self._direction.get_current_value()

    def _set_comb_delay(self, delay):
        if abs(delay) < abs(self.switch_threshold.get_current_value()):
            ### check to prevent slow phaseshifter corrections <50fs
            if (
                np.abs(
                    self._coarse_delay_adj.get_current_value()
                    - self.offset_coarse_adj.get_current_value()
                )
                > 50e-15
            ):
                outcoarse = self.offset_coarse_adj.get_current_value()
            else:
                outcoarse = None
            outfine = (
                self.offset_fine_adj.get_current_value()
                + self._direction.get_current_value() * delay
            )
        else:
            outcoarse = (
                self.offset_coarse_adj.get_current_value()
                + self._direction.get_current_value() * delay
            )
            outfine = self.offset_fine_adj.get_current_value()
        return (outfine, outcoarse)


class LxtCompStageDelay(Assembly):
    def __init__(self, comp_adj, delay_adj, direction=1, name=None):
        super().__init__(name=name)
        self._comp_delay_adj = comp_adj
        self._delay_adj = delay_adj
        # self._append(AdjustableMemory, direction, name="_direction", is_setting=True)

        self._append(
            AdjustableVirtual,
            [self._delay_adj, self._comp_delay_adj],
            self._get_comp_delay,
            self._set_comp_delay,
            name="delay",
            unit="s",
        )

    def _get_comp_delay(self, delay, delaycomp):
        return -delay

    def _set_comp_delay(self, delay):
        if self._comp_delay_adj.check_target_value_within_limits(delay):
            outcomp = delay
        else:
            outcomp = self._comp_delay_adj.get_current_value()
        return (-delay, outcomp)


class Stage_LXT_Delay(AdjustableVirtual):
    def __init__(self, fine_delay_adj, coarse_delay_adj, direction=1, name=None):
        self._fine_delay_adj = fine_delay_adj
        self._coarse_delay_adj = coarse_delay_adj
        self._direction = direction
        self.switch_threshold = AdjustableFS(
            f"/photonics/home/gac-bernina/eco/configuration/{name}_combined_delay_phase_shifter_threshold",
            name="switch_threshold",
            default_value=50e-12,
        )
        self.offset_fine_adj = AdjustableFS(
            f"/photonics/home/gac-bernina/eco/configuration/{name}_conbined_fine_adj_offset",
            name="offset_fine_adj",
            default_value=0.0,
        )
        self.offset_coarse_adj = AdjustableFS(
            f"/photonics/home/gac-bernina/eco/configuration/{name}_combined_coarse_adj_offset",
            name="offset_coarse_adj",
            default_value=0.0,
        )

        AdjustableVirtual.__init__(
            self,
            [self._fine_delay_adj, self._coarse_delay_adj],
            self._get_comb_delay,
            self._set_comb_delay,
            name=name,
            unit="s",
        )

    def _get_comb_delay(self, pd, ps):
        ps_rel = ps - self.offset_coarse_adj()
        pd_rel = pd - self.offset_fine_adj()
        return (ps_rel + pd_rel) * self._direction

    def _set_comb_delay(self, delay):
        if abs(delay) < abs(self.switch_threshold()):
            print("setting delay stage")
            ### check to prevent slow phaseshifter corrections <50fs
            if np.abs(self._coarse_delay_adj() - self.offset_coarse_adj()) > 50e-15:
                ps_pos = self.offset_coarse_adj()
            else:
                ps_pos = None
            pd_pos = self.offset_fine_adj() + delay
        else:
            ps_pos = self.offset_coarse_adj() + delay
            pd_pos = self.offset_fine_adj()
        return self._direction * pd_pos, self._direction * ps_pos


@spec_convenience
@value_property
@tweak_option
class PhaseshifterOrig(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        # self._cb = None
        self._append(
            AdjustablePv,
            pvname + ":NEW_DELTA_T",
            name="_set_val",
            unit="ps",
            is_setting=False,
            is_status=False,
            is_display=False,
        )
        self._append(
            DetectorPvData,
            pvname + ":CURR_DELTA_T",
            name="_readback",
            unit="ps",
            is_setting=False,
            is_display=False,
        )

        self._append(
            DetectorVirtual,
            [self._readback],
            lambda val: val / 1e12,
            name="readback",
            unit="s",
        )

        self._append(
            DetectorPvString,
            pvname + ":INFO_LINE2",
            name="status_string",
            is_setting=False,
            is_display=True,
        )

        self._set_new_delay = PV(pvname + ":SET_NEW_PHASE.PROC")
        self._append(AdjustableMemory, "s", name="unit", is_display=False)

    # def _monitor_to_new_delay_set(*args,**kwargs,timeout=30):

    def get_current_value(self):
        return self._readback.get_current_value() * 1e-12

    def _change_value_and_wait(self, value, check_interval=0.03, accuracy=100e-15):
        if np.abs(value) > 0.1:
            raise Exception("Very large value! This value is counted in seconds!")

        is_no_change = np.abs(self.get_current_value() - value) < accuracy
        if is_no_change:
            return

        def set_is_moving_state(**kwargs):
            self.is_moving = not kwargs["value"] == "->New delay was set...OK"

        if not is_no_change:
            self._set_val.set_target_value(value * 1e12).wait()
            cbno = self.status_string._pv.add_callback(callback=set_is_moving_state)
            self.is_moving = True
            self._set_new_delay.put(1)
            while self.is_moving:
                time.sleep(check_interval)
            else:
                self.status_string._pv.clear_callbacks()

    def set_target_value(self, value, hold=False):
        return Changer(
            target=value,
            parent=self,
            changer=self._change_value_and_wait,
            hold=hold,
        )


@spec_convenience
@value_property
class Phaseshifter_MK2(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._cb = None
        self._append(
            AdjustablePv,
            pvname + ":PULSE_TIME_END",
            pvreadbackname=pvname + ":PULSE_TIME_NOW",
            name="target",
            unit="ps",
            is_setting=True,
        )
        self._append(
            AdjustablePv, pvname + ":PULSE_TIME_UPDATE", name="enabled", is_setting=True
        )
        self._append(
            AdjustablePv,
            pvname + ":PULSE_TIME_RATE",
            name="speed",
            unit="ps/s",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvname + ":ACTIVE_PROCESS",
            name="_abort",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/{name}_limit_high.json",
            default_value=1,
            name="limit_high",
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/{name}_limit_low.json",
            default_value=0,
            name="limit_low",
            is_setting=True,
        )

    ######### Motion commands ########
    def get_limits(self):
        return (self.limit_low(), self.limit_high())

    def set_limits(self, limit_low, limit_high):
        self.limit_low(limit_low)
        self.limit_high(limit_high)

    def stop(self):
        """Adjustable convention"""
        self._abort(1)
        pass

    def get_moveDone(self, value):
        if self._cb:
            self._cb()
        if abs(value - self.target.get_current_value()) < 0.05:
            return True
        else:
            return False

    def move(self, value, check=True, wait=True, update_value_time=0.1, timeout=120):
        if check:
            lim_low, lim_high = self.get_limits()
            if not ((lim_low <= value) and (value <= lim_high)):
                raise AdjustableError("Soft limits violated!")
        self.enabled(1)
        self.target.set_target_value(value * 1e12)
        if wait:
            t_start = time.time()
            time.sleep(update_value_time)
            while not self.get_moveDone(value * 1e12):
                if (time.time() - t_start) > timeout:
                    raise AdjustableError(
                        f"motion timeout reached in phaseshifter motion"
                    )
                time.sleep(update_value_time)

    def set_target_value(self, value, hold=False, check=True):
        changer = lambda value: self.move(value, check=check, wait=True)
        return Changer(
            target=value,
            parent=self,
            changer=changer,
            hold=hold,
            stopper=self.stop,
        )

    def get_current_value(self):
        return self.target() * 1e-12


class LaserBernina(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname

        self._append(
            PhaseshifterOrig,
            pvname="SLAAR02-TSPL-EPL",
            name="phaseshifter_orig",
            is_setting=True,
        )

        self._append(
            Phaseshifter_MK2,
            pvname="SLAAR-CSOC-DLL3-PYIOC",
            name="phaseshifter_mk2",
            is_setting=True,
        )

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
        # self._append(
        #     MotorRecord, self.pvname + "-M533:MOT", name="wp_pol", is_setting=True
        # )

        # self._append(
        #     MotorRecord, self.pvname + "-M534:MOT", name="wp_att", is_setting=True
        # )
        try:
            self.motor_configuration_thorlabs = {
                "waveplate_lambda_half": {
                    "pvname": "SLAAR21-LMOT-ELL3",
                },
                "waveplate_lambda_fourth": {
                    "pvname": "SLAAR21-LMOT-ELL4",
                },
            }

            ### thorlabs piezo motors ###
            for name, config in self.motor_configuration_thorlabs.items():
                self._append(
                    ThorlabsPiezoRecord,
                    pvname=config["pvname"],
                    name=name,
                    is_setting=True,
                )
        except Exception as e:
            print(e)

        ######## Implementation segmented ND filter wheel in rotation stage #########
        self._append(
            MotorRecord, "SARES20-MF1:MOT_16", name="nd_filt_stg", is_setting=True
        )

        filters = np.array(
            [
                [1, 330],
                [0.872863247863248, 15],
                [0.692521367521367, 60],
                [0.549038461538462, 105],
                [0.432051282051282, 150],
                [0.333653846153846, 195],
                [0.251188643, 240],
                [0.111538461538462, 285],
            ]
        )

        def set_transmission(t):
            idx = np.argmin(abs(filters.T[0] - t))
            stg = filters[idx][1]
            t = filters[idx][0]
            print(f"Setting ND filter transmission to {t:.3} at position {stg}")
            return stg

        def get_transmission(stg):
            idx = np.argmin(abs(filters.T[1] - stg))
            t = filters[idx][0]
            return t

        self._append(
            AdjustableVirtual,
            [self.nd_filt_stg],
            get_transmission,
            set_transmission,
            name="nd_filt",
        )

        ######## END Implementation segmented ND filter wheel in rotation stage #########

        self._append(
            AdjustableFS,
            "/photonics/home/gac-bernina/eco/configuration/wp_att_calibration",
            name="wp_att_calibration",
            is_display=False,
            is_setting=True,
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

        # self._append(
        #    MotorRecord,
        #    self.pvname + "-M522:MOTOR_1",
        #    name="delaystage_pump",
        #    is_setting=True,
        # )wp_att
        # self._append(pump
        self._append(
            LaserRateControl, name="rate", is_setting=True, is_display="recursive"
        )
        self._append(
            XltEpics,
            pvname="SLAAR02-LTIM-PDLY2",
            name="xlt",
            is_setting=True,
            is_display="recursive",
        )
        # Upstairs, Laser 1 LAM
        # self._append(
        #     MotorRecord,
        #     "SLAAR21-LMOT-M521:MOTOR_1",
        #     name="delaystage_pump",
        #     is_setting=True,
        # )
        # self._append(pump
        #     MotorRecord,
        #     "SLAAR21-LMOT-M521:MOTOR_1",
        #     name="delaystage_pump",
        #     is_setting=True,
        # )
        # self._append(
        #     DelayTime,
        #     self.delaystage_pump,
        #     name="delay_pump",
        #     is_setting=True,
        # )
        self._append(
            SmaractRecord,
            "SARES23-USR:MOT_10",
            name="switch_ir_wl",
            is_setting=True,
        )
        self._append(
            ThorlabsPiezoRecord,
            "SLAAR21-LMOT-ELL5",
            name="wp_ir",
            is_setting=True,
        )
        self._append(
            ThorlabsPiezoRecord,
            "SLAAR21-LMOT-ELL2",
            name="rot_block_ir",
            is_setting=False,
        )

        self._append(
            SmaractRecord,
            "SARES23-USR:MOT_11",
            name="delaystage_ir",
            is_setting=True,
        )
        self._append(
            DelayTime,
            self.delaystage_ir,
            name="delay_ir",
            is_setting=True,
        )

        self._append(
            AdjustableVirtual, [self.wp_ir], wp2uJ, uJ2wp, name="pulse_energy_ir"
        )


class DelayTime(AdjustableVirtual):
    def __init__(
        self,
        stage,
        direction=1,
        passes=2,
        group_velo=299792458,
        offset_detector=None,
        reset_current_value_to=True,
        name=None,
    ):
        self._direction = direction
        self._group_velo = group_velo  # m/s
        self._passes = passes

        # self.Id = stage.Id + "_delay"
        self._stage = stage

        if offset_detector is not None:
            self._offset_detector = offset_detector
            AdjustableVirtual.__init__(
                self,
                [stage],
                lambda posmm: self._mm_to_s(posmm)
                + self._offset_detector.get_current_value(),
                lambda vals: self._s_to_mm(
                    vals - self._offset_detector.get_current_value()
                ),
                reset_current_value_to=reset_current_value_to,
                name=name,
                unit="s",
            )
        else:
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
            "SLAAR02-LPMO02-C322",
            name="lhx_angle",
            is_display="recursive",
            is_status=True,
        )
        self._append(
            CameraPositionMonitor,
            "SLAAR02-LPMO01-C321",
            name="lhx_position",
            is_display="recursive",
            is_status=True,
        )
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
            "SLAAR21-LCAM-CT1C1",
            # name="table2_position",
            name="table2_angle_fb",
            is_display="recursive",
            is_status=True,
        )
        self._append(
            CameraPositionMonitor,
            "SLAAR21-LCAM-CT1C2",
            # name="table2_position",
            name="table2_position_fb",
            is_display="recursive",
            is_status=True,
        )
        # self._append(
        #     CameraPositionMonitor,
        #     "SLAAR21-LCAM-CS842",
        #     name="table2_angle",
        #     is_display="recursive",
        #     is_status=True,
        # )
        self._append(
            CameraPositionMonitor,
            "SLAAR21-LCAM-CS841",
            # name="table2_position",
            name="timing_drift",
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
