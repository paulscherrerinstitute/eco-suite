# from ..eco_epics.motor import Motor as _Motor
from functools import partial
from epics.motor import Motor as _Motor
from epics import PV

from eco.acquisition.scan_data import run_status_convenience
from .utilities import Changer
from ..aliases import Alias
from ..elements.adjustable import (
    AdjustableError,
    AdjustableFS,
    AdjustableMemory,
    spec_convenience,
    ValueInRange,
    update_changes,
    value_property,
    tweak_option,
)
from ..devices_general.pv_adjustable import PvRecord
from ..elements.detector import DetectorGet
from ..epics import get_from_archive
from ..utilities.keypress import KeyPress
import sys, colorama
from .. import global_config
from ..elements.assembly import Assembly
import time
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum, AdjustablePvString
from ..epics.detector import DetectorPvData
import numpy as np
from .motor_controller import MforceChannel
from .detectors import DetectorVirtual
from ..epics.detector import DetectorPvData
import json
from .powerbrick import PowerBrickChannelPars
from time import sleep

if hasattr(global_config, "elog"):
    elog = global_config.elog
else:
    elog = None

if hasattr(global_config, "archiver"):
    archiver = global_config.archiver
else:
    archiver = None


_MotorRocordStandardProperties = {}
_posTypes = ["user", "dial", "raw"]
_guiTypes = ["xdm"]


_status_messages = {
    -13: "invalid value (cannot convert to float).  Move not attempted.",
    -12: "target value outside soft limits.         Move not attempted.",
    -11: "drive PV is not connected:                Move not attempted.",
    -8: "move started, but timed-out.",
    -7: "move started, timed-out, but appears done.",
    -5: "move started, unexpected return value from PV.put()",
    -4: "move-with-wait finished, soft limit violation seen",
    -3: "move-with-wait finished, hard limit violation seen",
    0: "move-with-wait finish OK.",
    1: "move-without-wait executed, not confirmed",
    2: "move-without-wait executed, move confirmed",
    3: "move-without-wait finished, hard limit violation seen",
    4: "move-without-wait finished, soft limit violation seen",
}


def _keywordChecker(kw_key_list_tups):
    for tkw, tkey, tlist in kw_key_list_tups:
        assert tkey in tlist, "Keyword %s should be one of %s" % (tkw, tlist)


@spec_convenience
@update_changes
@get_from_archive
@value_property
class SmaractStreamdevice(Assembly):
    def __init__(
        self,
        pvname,
        accuracy=1e-3,
        name=None,
        elog=None,
        alias_fields={
            "readback_raw": "MOTRBV",
            "user_set_pos": "SET_POS",
            "user_direction": "DIR",
        },
        offset_file=None,
    ):
        super().__init__(name=name)
        # self.settings.append(self)
        self.status_collection.append(self, selection="settings")

        self.pvname = pvname
        self._elog = elog
        for an, af in alias_fields.items():
            self.alias.append(
                Alias(an, channel=":".join([pvname, af]), channeltype="CA")
            )
        self._currentChange = None
        # self.description = EpicsString(pvname + ".DESC")
        self._append(
            AdjustablePvEnum, self.pvname + ":DIR", name="direction", is_setting=True
        )
        # self._append(
        #    PvRecord, self.pvname + ":SET_POS", name="set_pos", is_setting=True
        # )
        self._append(
            AdjustablePv,
            self.pvname + ":FRM_BACK.PROC",
            name="home_backward",
            is_setting=False,
            is_status=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":FRM_FORW.PROC",
            name="home_forward",
            is_setting=False,
            is_status=False,
            is_display=False,
        )
        self._append(
            AdjustablePv, self.pvname + ":GET_HOMED", name="is_homed", is_setting=False
        )
        self._append(
            AdjustablePv,
            self.pvname + ":CALIBRATE.PROC",
            name="calibrate_sensor",
            is_setting=False,
            is_status=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":CL_MAX_FREQ",
            name="speed",
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":HOLD",
            name="holding_time_ms",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":AMPLITUDE",
            name="voltage_4KADU_per_100V",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":DRIVE",
            name="_drive",
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":MOTRBV",
            name="_readback",
            is_setting=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":STAGE_TYPE",
            name="stage_type",
            is_setting=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":STATUS",
            name="status_channel",
            is_setting=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":GET_SENSOR_TYPE",
            pvname_set=self.pvname + ":SET_SENSOR_TYPE",
            name="sensor_type",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":GET_SENSOR_TYPE",
            name="sensor_type_getter_number",
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":SET_SENSOR_TYPE",
            name="sensor_type_setter_number",
            is_setting=False,
        )
        self._append(
            AdjustablePv, self.pvname + ":LLM", name="limit_low", is_setting=False
        )
        self._append(
            AdjustablePv, self.pvname + ":HLM", name="limit_high", is_setting=False
        )
        self._append(
            AdjustablePv, self.pvname + ":NAME", name="caqtdm_name", is_setting=True
        )
        self.accuracy = accuracy
        self._stop_pv = PV(self.pvname + ":STOP.PROC")
        self.stop = lambda: self._stop_pv.put(1)
        if offset_file:
            self._append(
                AdjustableFS,
                offset_file,
                name="offset",
                default_value=0,
                is_setting=True,
                is_display=False,
            )
        else:
            self._append(
                AdjustableMemory,
                value=0,
                name="offset",
                is_setting=True,
                is_display=False,
            )

        self._append(DetectorGet, self.get_current_value, name="readback")

    def update_name_in_panel(self):
        self.caqtdm_name(self.alias.get_full_name())

    def set_target_value(self, value, hold=False, check=True):
        changer = lambda value: self.move(value, check=check)

        return Changer(
            target=value,
            parent=self,
            changer=changer,
            hold=hold,
            stopper=self.stop,
        )

    def get_current_value(self):
        return self._readback.get_current_value() - self.offset.get_current_value()

    def reset_current_value_to(self, reset_value):
        self.offset.set_target_value(
            self._readback.get_current_value() - reset_value
        ).wait()

    def init_stage(self):
        self.calibrate_sensor.set_target_value(1)
        time.sleep(3)
        if True:  # not self.is_homed.get_current_value():
            self.home_forward.set_target_value(1)
            homed = 0
            while not homed:
                homed = self.is_homed.get_current_value()
                time.sleep(0.1)

    def get_close_to(self, value, accuracy):
        movedone = 1
        if np.abs(value - self.get_current_value()) > accuracy:
            movedone = 0
        return movedone

    def move(self, value, check=True, update_value_time=0.05, timeout=120):
        if check:
            lim_low, lim_high = self.get_limits()

            if not ((lim_low < value) and (value < lim_high)):
                raise AdjustableError("Soft limits violated!")
        t_start = time.time()
        # waiter = WaitPvConditions(
        #     self.status_channel._pv,
        #     lambda **kwargs: not kwargs["value"] == 0,
        #     lambda **kwargs: kwargs["value"] == 0,
        # )
        self._drive.set_target_value(value + self.offset.get_current_value())
        # waiter.wait_until_done(check_interval=update_value_time)

        while not self.get_close_to(value, self.accuracy):
            if (time.time() - t_start) > timeout:
                print(
                    f"Present position: {self.get_current_value()}, target position: {value}, accuracy: {self.accuracy}"
                )
                raise AdjustableError(
                    f"motion timeout reached in smaract {self.name}:{self.pvname}"
                )
            time.sleep(update_value_time)

    def add_value_callback(self, callback, index=None):
        return self._readback._pv.add_callback(callback=callback, index=index)

    def clear_value_callback(self, index=None):
        if index:
            self._readback._pv.remove_callback(index)
        else:
            self._readback._pv.clear_callbacks()

    def get_limits(self):
        return (
            self.limit_low.get_current_value() - self.offset.get_current_value(),
            self.limit_high.get_current_value() - self.offset.get_current_value(),
        )

    def set_limits(self, low_limit, high_limit, relative_to_present=False):
        if relative_to_present:
            tval = self.get_current_value()
            low_limit += tval
            high_limit += tval
        self.limit_low.set_target_value(low_limit + self.offset.get_current_value())
        self.limit_high.set_target_value(high_limit + self.offset.get_current_value())

    # return string with motor value as variable representation
    def __str__(self):
        # """ return short info for the current motor"""
        s = f"{self.name}"
        s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL}"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{:1.6g}\n".format(*self.get_limits())
        s += f"\n{colorama.Style.DIM}low limit {colorama.Style.RESET_ALL}"
        s += ValueInRange(*self.get_limits()).get_str(self.get_current_value())
        s += f" {colorama.Style.DIM}high limit{colorama.Style.RESET_ALL}"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{1.6g}".format(self.get_limits())
        return s

    def __repr__(self):
        print(str(self))
        return object.__repr__(self)

    def __call__(self, value):
        self._currentChange = self.set_target_value(value)

    def _tweak_ioc(self, step_value=None):
        pv = PV(self.pvname + ":TWV")
        pvf = PV(self.pvname + ":TWF.PROC")
        pvr = PV(self.pvname + ":TWR.PROC")
        if not step_value:
            step_value = pv.get()
        print(f"Tweaking {self.name} at step size {step_value}", end="\r")

        help = "q = exit; up = step*2; down = step/2, left = neg dir, right = pos dir\n"
        help = help + "g = go abs, s = set"
        print(f"tweaking {self.name}")
        print(help)
        print(f"Starting at {self.get_current_value()}")
        step_value = float(step_value)
        oldstep = 0
        k = KeyPress()
        cll = colorama.ansi.clear_line()

        class Printer:
            def print(self, **kwargs):
                print(
                    cll + f"stepsize: {self.stepsize}; current: {kwargs['value']}",
                    end="\r",
                )

        p = Printer()
        print(" ")
        p.stepsize = step_value
        p.print(value=self.get_current_value())
        ind_callback = self.add_value_callback(p.print)
        pv.put(step_value)
        try:
            while k.isq() is False:
                if oldstep != step_value:
                    p.stepsize = step_value
                    p.print(value=self.get_current_value())
                    oldstep = step_value
                k.waitkey()
                if k.isu():
                    step_value = step_value * 2.0
                    pv.put(step_value)
                elif k.isd():
                    step_value = step_value / 2.0
                    pv.put(step_value)
                elif k.isr():
                    pvf.put(1)
                elif k.isl():
                    pvr.put(1)
                elif k.iskey("g"):
                    print("enter absolute position (char to abort go to)")
                    sys.stdout.flush()
                    v = sys.stdin.readline()
                    try:
                        v = float(v.strip())
                        self.set_target_value(v)
                    except:
                        print("value cannot be converted to float, exit go to mode ...")
                        sys.stdout.flush()
                elif k.iskey("s"):
                    print("enter new set value (char to abort setting)")
                    sys.stdout.flush()
                    v = sys.stdin.readline()
                    try:
                        v = float(v[0:-1])
                        self.reset_current_value_to(v)
                    except:
                        print("value cannot be converted to float, exit go to mode ...")
                        sys.stdout.flush()
                elif k.isq():
                    break
                else:
                    print(help)
        except (KeyboardInterrupt, SystemExit):
            print("NB: aborted tweak !")
        self.clear_value_callback(index=ind_callback)
        print(f"final position: {self.get_current_value()}")
        print(f"final tweak step: {pv.get()}")

    def tweak(self, *args, **kwargs):
        return self._tweak_ioc(*args, **kwargs)

    def gui(self):
        num = ""
        for s in self.pvname[::-1]:
            if s.isdigit():
                num = s + num
            else:
                break
        nam = self.pvname[: -len(num)]
        num = int(num)

        self._run_cmd(
            f'caqtdm -macro "P={nam},M={num}" /ioc/qt/ESB_MX_SmarAct_mot_exp.ui'
        )


# @get_from_archive
@spec_convenience
@update_changes
@value_property
@tweak_option
class PshellMotor(Assembly):
    def __init__(
        self,
        robot=None,
        name=None,
        name_pshell=None,
        unit=None,
        elog=None,
    ):
        super().__init__(name=name)
        if not name_pshell:
            name_pshell = name
        self.name_pshell = name_pshell
        self.robot = robot
        self.pc = self.robot.pc
        self.status_collection.append(self, selection="settings")
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/robot_{name}_limit_high.json",
            default_value=0,
            name="limit_high",
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/robot_{name}_limit_low.json",
            default_value=0,
            name="limit_low",
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/robot_{name}_unit.json",
            default_value=unit,
            name="unit",
            is_setting=True,
        )
        self._append(
            DetectorGet,
            self.get_current_value,
            name="readback",
        )
        self._cb = None

    def move(self, value, check=True, wait=False, update_value_time=0.05, timeout=240):
        if self.robot.info.server_status == "Busy":
            raise AdjustableError(
                "The server is busy with a recording motion. To abort it, type: rob.abort_record()"
            )
        if not self.robot.config.powered():
            if self.robot.info.mode() == "remote":
                print(
                    "Robot is not powered (rob.config.powered), motion will be queued."
                )
        if check:
            lim_low, lim_high = self.get_limits()
            if not ((lim_low <= value) and (value <= lim_high)):
                raise AdjustableError("Soft limits violated!")
        cid = self.pc.start_eval(f"{self.name_pshell}.moveAsync({float(value)})&")
        if wait:
            t_start = time.time()
            time.sleep(update_value_time)
            while not self.get_moveDone(cid, value):
                if (time.time() - t_start) > timeout:
                    raise AdjustableError(
                        f"motion timeout reached in robot motor {self.name}"
                    )
                time.sleep(update_value_time)

    def get_moveDone(self, cid, value):
        res = self.pc.get_result(cid)
        if res["status"] == "failed":
            raise AdjustableError(res["exception"])
        current_value = self.get_current_value()
        if self._cb:
            self._cb()
        if (res["status"] == "completed") & (abs(value - current_value) < 0.5):
            # elif (res["status"] == "completed")&(abs(value-self.get_current_value()) < 0.5):
            return True
        else:
            return False

    def stop(self):
        """Adjustable convention"""
        self.pc.start_eval(f"{self.name_pshell}.stop()&")
        pass

    def get_limits(self):
        return (self.limit_low(), self.limit_high())

    def set_limits(self, limit_low, limit_high):
        self.limit_low(limit_low)
        self.limit_high(limit_high)

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
        return self.robot._cache["pos"][self.name_pshell]

    def add_value_callback(self, cb):
        self._cb = cb
        return 0

    def clear_value_callback(self, id):
        self._cb = None

    # return string with motor value as variable representation
    def __str__(self):
        # """ return short info for the current motor"""
        s = f"{self.name}"
        if self.get_current_value() != None:
            # s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL} stat: {self.status_flag().name}"
            s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL}"
            # # s +=  "\tuser limits      (low,high) : {:1.6g},{:1.6g}\n".format(*self.get_limits())
            s += f"\n{colorama.Style.DIM}low limit {colorama.Style.RESET_ALL}"
            s += ValueInRange(*self.get_limits()).get_str(self.get_current_value())
            s += f" {colorama.Style.DIM}high limit{colorama.Style.RESET_ALL}"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{1.6g}".format(self.get_limits())
        else:
            s += f"\t@ {colorama.Style.BRIGHT}{'NOT CONNECTED'}{colorama.Style.RESET_ALL}"

        return s

    def __repr__(self):
        print(str(self))
        return object.__repr__(self)


class AdjustablePiHex(AdjustablePv):
    def __init__(
        self, pvname=None, pvreadbackname=None, accuracy=None, unit=None, name=None
    ):
        super().__init__(
            pvname,
            pvreadbackname=pvreadbackname,
            accuracy=accuracy,
            unit=unit,
            name=name,
        )
        self.limit_high = AdjustableFS(
            f"/sf/bernina/config/eco/reference_values/hex_pi_{name}_limit_high.json",
            default_value=0,
        )
        self.limit_low = AdjustableFS(
            f"/sf/bernina/config/eco/reference_values/hex_pi_{name}_limit_low.json",
            default_value=0,
        )

    def move(self, value, check=False):
        if check:
            if self.limit_low:
                if value < self.limit_low():
                    raise Exception(
                        f"Target value of {self.name} is smaller than limit value!"
                    )
            if self.limit_high:
                if self.limit_high() < value:
                    raise Exception(
                        f"Target value of {self.name} is higher than limit value!"
                    )
        self._pv.put(value)
        time.sleep(0.1)
        while self.get_change_done() == 0:
            time.sleep(0.1)

    def change(self, value):
        return self.move(value)

    def get_limits(self):
        return (self.limit_low(), self.limit_high())

    def set_limits(self, limit_low, limit_high):
        self.limit_low(limit_low)
        self.limit_high(limit_high)


@spec_convenience
# @get_from_archive
@value_property
@tweak_option
class SmarActOpenLoopRecord(Assembly):
    def __init__(self, pvname=None, channel=None, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self.channel = channel

        self._append(
            AdjustablePv,
            self.pvname.split(":")[0] + f":MOT_{self.channel}.DESC",
            name="description",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ".AOUT",
            name="_com_set",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ".TINP",
            name="_com_get",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/smaract_openloop_{name}_limit_high.json",
            default_value=-1e6,
            name="limit_high",
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/smaract_openloop_{name}_limit_low.json",
            default_value=1e6,
            name="limit_low",
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/smaract_openloop_{name}_voltage.json",
            name="voltage",
            default_value=25,
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/smaract_openloop_{name}_frequency.json",
            name="frequency",
            default_value=250,
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/smaract_openloop_{name}_position.json",
            name="position",
            default_value=0,
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/smaract_openloop_{name}_direction.json",
            name="direction",
            default_value=1,
            is_setting=True,
            is_display=True,
        )

    def eval_command(self, cmd):
        self._com_set(cmd)
        sleep(0.2)
        return self._com_get()

    def stop(self):
        self._com_set(f":S{self.channel-1}")

    def move(self, value, check=True, wait=False):
        value = int(value) * self.direction()
        pos = int(self.position() * self.direction())
        target_rel = value - pos
        if check:
            if self.limit_low:
                if value < self.limit_low():
                    raise Exception(
                        f"Target value of {self.name} is smaller than limit value!"
                    )
            if self.limit_high:
                if self.limit_high() < value:
                    raise Exception(
                        f"Target value of {self.name} is higher than limit value!"
                    )
        res = self.eval_command(
            f":MST{self.channel-1},{target_rel},{int(self.voltage()*40.95)},{int(self.frequency())}"
        )
        if res[-1] == "0":
            self.position.mv(value)
        else:
            raise Exception(
                f"Motion of SmarAct Motor {self.name} failed with error code {res}"
            )

    def get_limits(self):
        return (self.limit_low(), self.limit_high())

    def set_limits(self, limit_low, limit_high):
        self.limit_low.set_target_value(limit_low)
        self.limit_high.set_target_value(limit_high)

    def get_current_value(self):
        return self.position.get_current_value()

    def set_target_value(self, value, hold=False, check=True, **kwargs):
        return Changer(
            target=value,
            parent=self,
            changer=self.move,
            hold=hold,
            stopper=self.stop,
        )

    # return string with motor value as variable representation
    def __str__(self):
        # """ return short info for the current motor"""
        s = f"{self.name}"
        # s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL} stat: {self.status_flag().name}"
        s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL}"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{:1.6g}\n".format(*self.get_limits())
        s += f"\n{colorama.Style.DIM}low limit {colorama.Style.RESET_ALL}"
        s += ValueInRange(*self.get_limits()).get_str(self.get_current_value())
        s += f" {colorama.Style.DIM}high limit{colorama.Style.RESET_ALL}"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{1.6g}".format(self.get_limits())
        return s

    def __repr__(self):
        print(str(self))
        return object.__repr__(self)


@spec_convenience
@update_changes
# @get_from_archive
@value_property
@tweak_option
class ThorlabsPiezoRecord(Assembly):
    def __init__(self, pvname=None, accuracy=0.2, unit=None, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._cb = None

        self._append(
            AdjustablePv,
            self.pvname + ":DRIVE",
            pvreadbackname=self.pvname + ":MOTRBV",
            accuracy=accuracy,
            unit=unit,
            name="position",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":HLM",
            name="limit_high",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":LLM",
            name="limit_low",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":FRM_FORW.PROC",
            name="home_forward",
            is_setting=False,
            is_status=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":FRM_BACK.PROC",
            name="home_backward",
            is_setting=False,
            is_status=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":STOP.PROC",
            name="_stop_pv",
            is_setting=False,
            is_status=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":OFFS",
            name="offset",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":DIRN",
            name="direction",
            is_setting=True,
        )

    def reset_current_value_to(self, value):
        new_offset = (
            -(self.get_current_value() - self.offset.get_current_value()) + value
        )
        self.offset.set_target_value(new_offset).wait()

    def stop(self):
        self._stop_pv(1)

    def move(self, value, check=True, wait=False):
        if check:
            if self.limit_low:
                if value < self.limit_low():
                    raise Exception(
                        f"Target value of {self.name} is smaller than limit value!"
                    )
            if self.limit_high:
                if self.limit_high() < value:
                    raise Exception(
                        f"Target value of {self.name} is higher than limit value!"
                    )
        return self.position.set_target_value(value)

    def get_limits(self):
        return (self.limit_low(), self.limit_high())

    def set_limits(self, limit_low, limit_high):
        self.limit_low.set_target_value(limit_low).wait()
        self.limit_high.set_target_value(limit_high).wait()

    def get_current_value(self):
        return self.position.get_current_value(readback=True)

    def set_target_value(self, value, hold=False, check=True, **kwargs):
        return self.move(value, check=check, **kwargs)

    # return string with motor value as variable representation
    def __str__(self):
        # """ return short info for the current motor"""
        s = f"{self.name}"
        # s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL} stat: {self.status_flag().name}"
        s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL}"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{:1.6g}\n".format(*self.get_limits())
        s += f"\n{colorama.Style.DIM}low limit {colorama.Style.RESET_ALL}"
        s += ValueInRange(*self.get_limits()).get_str(self.get_current_value())
        s += f" {colorama.Style.DIM}high limit{colorama.Style.RESET_ALL}"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{1.6g}".format(self.get_limits())
        return s

    def __repr__(self):
        print(str(self))
        return object.__repr__(self)


@spec_convenience
@update_changes
@get_from_archive
@value_property
# @run_status_convenience
class MotorRecord(Assembly):
    def __init__(
        self,
        pvname,
        name=None,
        elog=None,
        # alias_fields={"readback": "RBV"},
        alias_fields={},
        backlash_definition=False,
        resolution_pars=False,
        is_psi_mforce=False,
        schneider_config=None,
        expect_bad_limits=True,
        has_park_pv=False,
        pb_conf=None,
        **kwargs,
    ):
        super().__init__(name=name)
        # self.settings.append(self)

        self.status_collection.append(self, selection="settings")

        self.pvname = pvname
        self._motor = _Motor(pvname)
        self._elog = elog
        for an, af in alias_fields.items():
            self.alias.append(
                Alias(an, channel=".".join([pvname, af]), channeltype="CA")
            )
        self._currentChange = None
        # self.description = EpicsString(pvname + ".DESC")
        self._append(
            AdjustablePvEnum,
            self.pvname + ".STAT",
            name="status_flag",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustablePvEnum, self.pvname + ".DIR", name="direction", is_setting=True
        )
        self._append(AdjustablePv, self.pvname + ".OFF", name="offset", is_setting=True)
        self._append(
            DetectorPvData,
            self.pvname + ".RBV",
            name="readback",
            is_setting=False,
            is_display=True,
            is_status=True,
        )
        self._append(
            AdjustablePv, self.pvname + ".VELO", name="speed", is_setting=False
        )
        self._append(
            AdjustablePv,
            self.pvname + ".ACCL",
            name="acceleration_time",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ".LLM",
            name="limit_low",
            is_setting=True,
        )
        self._append(
            AdjustablePv, self.pvname + ".HLM", name="limit_high", is_setting=True
        )
        self._append(
            AdjustablePvEnum, self.pvname + ".SPMG", name="mode", is_setting=False
        )
        self._append(
            DetectorPvData,
            self.pvname + ".MSTA",
            name="_flags",
            is_setting=False,
            is_display=False,
        )
        self._append(
            MotorRecordFlags,
            self._flags,
            name="flags",
            is_display="recursive",
            is_setting=False,
            is_status=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ".SPMG",
            name="motor_state",
            is_setting=False,
        )
        self._append(
            AdjustablePvString, self.pvname + ".EGU", name="unit", is_setting=True
        )
        self._append(
            AdjustablePvString,
            self.pvname + ".DESC",
            name="description",
            is_setting=True,
        )

        if resolution_pars:
            self._append(
                AdjustablePv,
                self.pvname + ".MRES",
                name="motor_resolution",
                is_setting=True,
                is_display=False,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".ERES",
                name="encoder_resolution",
                is_setting=True,
                is_display=False,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".RRES",
                name="readback_resolution",
                is_setting=True,
                is_display=False,
            )
            self._append(
                AdjustablePvEnum,
                self.pvname + ".UEIP",
                name="use_encoder",
                is_setting=True,
                is_display=False,
            )
            self._append(
                AdjustablePvEnum,
                self.pvname + ".URIP",
                name="use_readback",
                is_setting=True,
                is_display=False,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".RDBD",
                name="retry_deadband",
                is_setting=True,
                is_display=False,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".RTRY",
                name="retry_max",
                is_setting=True,
                is_display=False,
            )
            self._append(
                DetectorPvData,
                self.pvname + ".RCNT",
                name="retry_count",
                is_setting=False,
                is_display=False,
            )

        if has_park_pv:
            self._append(
                AdjustablePv,
                self.pvname + "_KILL",
                name="parked",
                is_setting=True,
            )
        if backlash_definition:
            self._append(
                AdjustablePv,
                self.pvname + ".BVEL",
                name="backlash_velocity",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".BACC",
                name="backlash_acceleration",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".BDST",
                name="backlash_distance",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".FRAC",
                name="backlash_fraction",
                is_setting=True,
            )

        if is_psi_mforce:
            controller_base, tmp = self.pvname.split(":")
            channel = int(tmp.split("_")[1])
            mforce_base = controller_base + ":" + str(channel)

            self._append(
                AdjustablePv,
                mforce_base + "_RC",
                name="run_current",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                mforce_base + "_HC",
                name="hold_current",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                mforce_base + "_set",
                name="m_code_set",
                is_setting=False,
                is_display=False,
            )
            self._append(
                AdjustablePv,
                mforce_base + "_get",
                name="m_code_get",
                is_setting=False,
                is_display=False,
            )

        if schneider_config:
            pv_base, port = schneider_config
            self._append(
                MforceChannel,
                pv_base,
                port,
                name="controller_settings",
                is_setting=True,
                is_display=False,
            )
        if expect_bad_limits:
            self.check_bad_limits()

        self._append(
            DetectorGet, lambda: self._motor._pvs["VAL"].host.split(":")[0], name="host"
        )
        self._append(
            DetectorGet, lambda: self._motor._pvs["VAL"].host.split(":")[1], name="port"
        )

        if pb_conf:
            self._append(
                PowerBrickChannelPars,
                self.host(),
                **pb_conf,
                name="cnf_pb",
            )

    def check_bad_limits(self, abs_set_value=2**53):
        ll, hl = self.get_limits()
        if ll == 0 and hl == 0:
            self.set_limits(-abs_set_value, abs_set_value)

    def set_target_value(self, value, hold=False, check=True):
        """Adjustable convention"""

        def changer(value):
            statflag_start = self.status_flag.get_current_value()
            if not statflag_start.value == 0:
                raise AdjustableError(
                    f"Motor {self.alias.get_full_name()}({self.pvname}) cannot start moving with status flag {statflag_start.name} ."
                )
            self._status = self._motor.move(value, ignore_limits=(not check), wait=True)
            self._status_message = _status_messages[self._status]
            statflag_end = self.status_flag.get_current_value()
            if not statflag_end.value == 0:
                raise AdjustableError(
                    f"Motor {self.alias.get_full_name()}({self.pvname}) cannot finish move with status flag {statflag_end.name} ."
                )

            if self._status < 0:
                raise AdjustableError(self._status_message)
            elif self._status > 0:
                print("\n")
                print(self._status_message)

        #        changer = lambda value: self._motor.move(\
        #                value, ignore_limits=(not check),
        #                wait=True)
        return Changer(
            target=value,
            parent=self,
            changer=changer,
            hold=hold,
            stopper=self._motor.stop,
        )

    def stop(self):
        """Adjustable convention"""
        try:
            self._currentChange.stop()
        except:
            self._motor.stop()
        pass

    def get_current_value(self, posType="user", readback=True):
        """Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        if posType == "user":
            return self._motor.get_position(readback=readback)
        if posType == "dial":
            return self._motor.get_position(readback=readback, dial=True)
        if posType == "raw":
            return self._motor.get_position(readback=readback, raw=True)

    def reset_current_value_to(self, value, posType="user"):
        """Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        if posType == "user":
            return self._motor.set_position(value)
        if posType == "dial":
            return self._motor.set_position(value, dial=True)
        if posType == "raw":
            return self._motor.set_position(value, raw=True)

    def get_moveDone(self):
        """Adjustable convention"""
        """ 0: moving 1: move done"""
        return PV(str(self.pvname + ".DMOV")).value

    def set_limits(
        self, low_limit, high_limit, posType="user", relative_to_present=False
    ):
        """
        set limits. usage: set_limits(low_limit, high_limit)

        """
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType == "dial":
            ll_name, hl_name = "DLLM", "DHLM"
        if relative_to_present:
            v = self.get_current_value(posType=posType)
            low_limit = v + low_limit
            high_limit = v + high_limit
        self._motor.put(ll_name, low_limit)
        self._motor.put(hl_name, high_limit)

    def add_value_callback(self, callback, index=None):
        return self._motor.get_pv("RBV").add_callback(callback=callback, index=index)

    def clear_value_callback(self, index=None):
        if index:
            self._motor.get_pv("RBV").remove_callback(index)
        else:
            self._motor.get_pv("RBV").clear_callbacks()

    def get_limits(self, posType="user"):
        """Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType == "dial":
            ll_name, hl_name = "DLLM", "DHLM"
        return self._motor.get(ll_name), self._motor.get(hl_name)

    def gui(self):
        pv, m = tuple(self.pvname.split(":"))
        self._run_cmd(f'caqtdm -macro "P={pv}:,M={m}" motorx_all.ui')

    # return string with motor value as variable representation
    def __str__(self):
        # """ return short info for the current motor"""
        s = f"{self.name}"
        s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL} (dial @ {self.get_current_value(posType='dial'):1.6g}; stat: {self.status_flag().name})"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{:1.6g}\n".format(*self.get_limits())
        s += f"\n{colorama.Style.DIM}low limit {colorama.Style.RESET_ALL}"
        s += ValueInRange(*self.get_limits()).get_str(self.get_current_value())
        s += f" {colorama.Style.DIM}high limit{colorama.Style.RESET_ALL}"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{1.6g}".format(self.get_limits())
        return s

    def __repr__(self):
        print(str(self))
        return object.__repr__(self)

    def __call__(self, value):
        self._currentChange = self.set_target_value(value)

    def _tweak_ioc(self, step_value=None):
        pv = self._motor.get_pv("TWV")
        pvf = self._motor.get_pv("TWF")
        pvr = self._motor.get_pv("TWR")
        if not step_value:
            step_value = pv.get()
        print(f"Tweaking {self.name} at step size {step_value}", end="\r")
        start_value = self.get_current_value()

        # help = "q = exit; up = step*2; down = step/2, left = neg dir, right = pos dir\n"
        # help = help + "g = go abs, s = set"
        help = "q = exit; up = step*2; down = step/2, left = neg dir, right = pos dir\n"
        help = help + "g = go abs, s = start value, r = reset current value to"
        print(f"tweaking {self.name}")
        print(help)
        print(f"Starting at {start_value}")
        step_value = float(step_value)
        oldstep = 0
        k = KeyPress()
        cll = colorama.ansi.clear_line()

        class Printer:
            def print(self, **kwargs):
                print(
                    cll + f"stepsize: {self.stepsize}; current: {kwargs['value']}",
                    end="\r",
                )

        p = Printer()
        print(" ")
        p.stepsize = step_value
        p.print(value=self.get_current_value())
        ind_callback = self.add_value_callback(p.print)
        pv.put(step_value)
        while k.isq() is False:
            if oldstep != step_value:
                p.stepsize = step_value
                p.print(value=self.get_current_value())
                oldstep = step_value
            k.waitkey()
            if k.isu():
                step_value = step_value * 2.0
                pv.put(step_value)
            elif k.isd():
                step_value = step_value / 2.0
                pv.put(step_value)
            elif k.isr():
                pvf.put(1)
            elif k.isl():
                pvr.put(1)
            elif k.iskey("s"):
                self.set_target_value(start_value)
                p.print(value=self.get_current_value())
            elif k.iskey("g"):
                print("enter absolute position (char to abort go to)")
                sys.stdout.flush()
                v = sys.stdin.readline()
                try:
                    v = float(v.strip())
                    self.set_target_value(v)
                except:
                    print("value cannot be converted to float, exit go to mode ...")
                    sys.stdout.flush()
            elif k.iskey("r"):
                print("enter new set value (char to abort setting)")
                sys.stdout.flush()
                v = sys.stdin.readline()
                try:
                    v = float(v[0:-1])
                    self.reset_current_value_to(v)
                except:
                    print("value cannot be converted to float, exit reset mode ...")
                    sys.stdout.flush()
            elif k.isq():
                break
            else:
                print(help)
        self.clear_value_callback(index=ind_callback)
        print("\r", end="")
        print(f"final position: {self.get_current_value()}")
        print(f"final tweak step: {pv.get()}")
        # print('\033[K',"the info",sep='',flush=True)

    def tweak(self, *args, **kwargs):
        return self._tweak_ioc(*args, **kwargs)


MotorRecord = MotorRecord

flag_names_motor_record = [
    "direction",
    "motion_complete",
    "pos_limit_switch",
    "home_switch",
    "unused",
    "closed_loop_position",
    "slipstall_detected",
    "at_home_position",
    "encoder_is_present",
    "problem",
    "moving",
    "gain_support",
    "communication_error",
    "neg_limit_switch",
    "is_homed",
]


class MotorRecordFlags(Assembly):
    def __init__(self, flags, name="flags"):
        super().__init__(name=name)
        self._flags = flags
        for flag_name in flag_names_motor_record:
            self._append(
                DetectorVirtual,
                [self._flags],
                partial(self._get_flag_name_value, flag_name=flag_name),
                name=flag_name,
                is_status=True,
                is_display=True,
            )

    def _get_flag_name_value(self, value, flag_name=None):
        index = flag_names_motor_record.index(flag_name)
        return int("{0:015b}".format(int(value))[-1 * (index + 1)]) == 1


class MotorRecordMForceUser(MotorRecord):
    def __init__(self, channel_no, pv_controller="SARES20-MF1", **kwargs):
        pv_motor = f"{pv_controller}:MOT_{port_number}"
        super().__init__(pv_motor, **kwargs)
        self._append(MForceSettings, pv_controller, channel_no, name="mforce_settings")


class MForceSettings(Assembly):
    def __init__(self, pv_controller, port_number, name="motor_parameters"):
        super().__init__(name=name)
        self.pv_motor = f"{pv_controller}:MOT_{port_number}"
        self.pv_channel = f"{pv_controller}:{port_number}"
        self._append(AdjustablePv, self.pv_motor + ".EGU", name="unit", is_setting=True)
        self._append(
            AdjustablePv,
            self.pv_motor + ".MRES",
            name="motor_resolution",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pv_motor + ".ERES",
            name="encoder_resolution",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pv_channel + "_set",
            name="set_controller_command",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pv_channel + "_get",
            name="get_controller_command",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePv, self.pv_channel + "_RC", name="run_current", is_setting=True
        )

    def set_limit_switch_config(self, invert_switches=False, invert_polarities=False):
        if not invert_switches:
            switch1 = 2
            switch2 = 3
        else:
            switch1 = 3
            switch2 = 2
        if not invert_polarities:
            polarity = 0
        else:
            polarity = 1
        self.set_controller_command(f"IS=1,{switch1},{polarity}")
        self.set_controller_command(f"IS=2,{switch2},{polarity}")


class SmaractSettings(Assembly):
    def __init__(
        self,
        pvname,
        name=None,
    ):
        super().__init__(name=name)
        self.pvname = pvname

        self._append(
            AdjustablePv,
            pvsetname=self.pvname + "_PTYP",
            pvreadbackname=self.pvname + "_PTYP_RB",
            name="sensor_type_num",
            is_setting=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + "_PTYP_SP",
            name="sensor_type_name",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + "_AUTOZERO",
            name="autozero_on_homing",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + "_MCLF",
            name="max_frequency",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + "_HOLD",
            name="holding_time",
            is_setting=True,
        )

        self._append(
            AdjustableFS,
            file_path="/photonics/home/gac-bernina/eco/configuration/smaract/setting_table",
            name="_setting_table",
            is_setting=False,
            is_display=False,
        )

    def recall(self, stage_alias_or_model=None):
        setting_table = self._setting_table()
        stages = np.array(
            [(alias, settings["models"]) for alias, settings in setting_table.items()],
            dtype=object,
        )
        if stage_alias_or_model is not None:
            if stage_alias_or_model in stages.T[0]:
                alias = stage_alias_or_model
            else:
                idx = [stage_alias_or_model in a for a in stages.T[1]]
                if np.sum(idx) == 1:
                    alias = stages.T[0][idx][0]
                if np.sum(idx) > 1:
                    print(
                        "Multiple entries found for model {stage_alias_or_model}. Please check _settings_table"
                    )
                    return
                if np.sum(idx == 0):
                    print(
                        "No entries found for model {stage_alias_or_model}. Please check _settings_table or model number / alias."
                    )
                    return
        else:
            stages = [
                (alias, settings["models"]) for alias, settings in setting_table.items()
            ]
            input_message = "\nSelect the stage to load setting:\n  q) quit\n"
            input_message += f'{"Idx":>3}    {"Alias":<30}   {"Models"}\n'
            for index, (alias, model) in enumerate(stages):
                input_message += f"{index:>3})   {alias:<30}   {model}\n"
            input_message += "Input: "
            idx = ""
            while idx not in range(len(stages)):
                idx = input(input_message)
                if idx == "q":
                    return
                else:
                    try:
                        idx = int(idx)
                    except:
                        continue
            print(f"Selected stage: {stages[idx]}")
            alias = stages[idx][0]
        stage_settings = setting_table[alias]
        if np.any([mcs in self.pvname for mcs in ["SARES20-MCS1", "SARES20-MCS2"]]):
            mcs_code = stage_settings["MCS"]
        else:
            mcs_code = stage_settings["MCS2"]
        stage_settings["settings"]["sensor_type_num"] = mcs_code
        self.memory.recall(input_obj=stage_settings)


@spec_convenience
@update_changes
@get_from_archive
@value_property
class SmaractRecord(Assembly):
    def __init__(
        self,
        pvname,
        name=None,
        elog=None,
        # alias_fields={"readback": "RBV"},
        alias_fields={},
        backlash_definition=False,
        expect_bad_limits=True,
        preferred_home_direction="forward",
        **kwargs,
    ):
        super().__init__(name=name)
        # self.settings.append(self)
        self.status_collection.append(self, selection="settings")

        self.pvname = pvname
        self._motor = _Motor(pvname)
        self._append(
            AdjustableMemory,
            preferred_home_direction,
            name="preferred_home_direction",
            is_setting=True,
        )
        self._elog = elog
        for an, af in alias_fields.items():
            self.alias.append(
                Alias(an, channel=".".join([pvname, af]), channeltype="CA")
            )
        self._currentChange = None

        self._append(
            SmaractSettings, self.pvname, name="motor_parameters", is_setting=True
        )
        self._append(
            AdjustablePv, self.pvname + ".LLM", name="limit_low", is_setting=True
        )
        self._append(
            AdjustablePv, self.pvname + ".HLM", name="limit_high", is_setting=True
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ".STAT",
            name="status_flag",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ".ACCL",
            name="acceleration_time",
            is_setting=True,
        )
        self._append(
            AdjustablePvEnum, self.pvname + ".DIR", name="direction", is_setting=True
        )
        self._append(AdjustablePv, self.pvname + ".OFF", name="offset", is_setting=True)
        self._append(
            AdjustablePv, self.pvname + ".FOFF", name="force_offset", is_setting=True
        )
        self._append(AdjustablePv, self.pvname + ".VELO", name="speed", is_setting=True)

        self._append(
            AdjustablePv,
            self.pvname + ".HOMF",
            name="home_forward",
            is_setting=False,
            is_status=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ".HOMR",
            name="home_reverse",
            is_setting=False,
            is_status=False,
            is_display=False,
        )

        self._append(
            DetectorPvData,
            self.pvname + ".RBV",
            name="readback",
            is_setting=False,
            is_display=True,
        )

        self._append(
            AdjustablePvEnum, self.pvname + ".SPMG", name="mode", is_setting=False
        )
        self._append(
            DetectorPvData, self.pvname + ".MSTA", name="_flags", is_setting=False
        )
        self._append(
            SmaractRecordFlags,
            self.pvname,
            self._flags,
            name="flags",
            is_display="recursive",
            is_status=True,
        )

        self._append(
            AdjustablePvString, self.pvname + ".EGU", name="unit", is_setting=True
        )
        self._append(
            AdjustablePvString,
            self.pvname + ".DESC",
            name="description",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + "_CAL",
            name="_calibrate_sensor",
            is_setting=True,
            is_status=False,
            is_display=False,
        )
        if backlash_definition:
            self._append(
                AdjustablePv,
                self.pvname + ".BVEL",
                name="backlash_velocity",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".BACC",
                name="backlash_acceleration",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".BDST",
                name="backlash_distance",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".FRAC",
                name="backlash_fraction",
                is_setting=True,
            )
        if expect_bad_limits:
            self.check_bad_limits()

    def check_bad_limits(self, abs_set_value=2**53):
        ll, hl = self.get_limits()
        if ll == 0 and hl == 0:
            self.set_limits(-abs_set_value, abs_set_value)

    def home(self):
        if (
            self.preferred_home_direction.get_current_value() == "forward"
            or self.preferred_home_direction.get_current_value() is None
            or self.preferred_home_direction.get_current_value()
        ):
            self.home_forward(1)
        elif (
            self.preferred_home_direction.get_current_value() == "reverse"
            or self.preferred_home_direction.get_current_value() is False
        ):
            self.home_reverse(1)
        time.sleep(0.1)
        while not self.flags.is_homed.get_current_value():
            time.sleep(0.1)

    def calibrate_sensor(self):
        self._calibrate_sensor(1)
        time.sleep(0.1)
        while not self.flags.motion_complete.get_current_value():
            time.sleep(0.1)

    def set_target_value(self, value, hold=False, check=True):
        """Adjustable convention"""

        def changer(value):
            self._status = self._motor.move(value, ignore_limits=(not check), wait=True)
            self._status_message = _status_messages[self._status]
            if self._status < 0:
                raise AdjustableError(self._status_message)
            elif self._status > 0:
                print("\n")
                print(self._status_message)

        #        changer = lambda value: self._motor.move(\
        #                value, ignore_limits=(not check),
        #                wait=True)
        return Changer(
            target=value,
            parent=self,
            changer=changer,
            hold=hold,
            stopper=self._motor.stop,
        )

    def stop(self):
        """Adjustable convention"""
        try:
            self._currentChange.stop()
        except:
            self.mode.set_target_value(0)
        pass

    def get_current_value(self, posType="user", readback=True):
        """Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        if posType == "user":
            return self._motor.get_position(readback=readback)
        if posType == "dial":
            return self._motor.get_position(readback=readback, dial=True)
        if posType == "raw":
            return self._motor.get_position(readback=readback, raw=True)

    def reset_current_value_to(self, value, posType="user"):
        """Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        if posType == "user":
            return self._motor.set_position(value)
        if posType == "dial":
            return self._motor.set_position(value, dial=True)
        if posType == "raw":
            return self._motor.set_position(value, raw=True)

    def get_moveDone(self):
        """Adjustable convention"""
        """ 0: moving 1: move done"""
        return PV(str(self.pvname + ".DMOV")).value

    def set_limits(
        self, low_limit, high_limit, posType="user", relative_to_present=False
    ):
        """
        set limits. usage: set_limits(low_limit, high_limit)

        """
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType == "dial":
            ll_name, hl_name = "DLLM", "DHLM"
        if relative_to_present:
            v = self.get_current_value(posType=posType)
            low_limit = v + low_limit
            high_limit = v + high_limit
        self._motor.put(ll_name, low_limit)
        self._motor.put(hl_name, high_limit)

    def add_value_callback(self, callback, index=None):
        return self._motor.get_pv("RBV").add_callback(callback=callback, index=index)

    def clear_value_callback(self, index=None):
        if index:
            self._motor.get_pv("RBV").remove_callback(index)
        else:
            self._motor.get_pv("RBV").clear_callbacks()

    def get_limits(self, posType="user"):
        """Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType == "dial":
            ll_name, hl_name = "DLLM", "DHLM"
        return self._motor.get(ll_name), self._motor.get(hl_name)

    def gui(self):
        pv, m = tuple(self.pvname.split(":"))
        self._run_cmd(
            f'caqtdm -macro "P={pv},M=:{m}, T=MCS" /sf/controls/config/qt/motorx_all.ui'
        )

    def gui_extra(self):
        pv, m = tuple(self.pvname.split(":"))
        self._run_cmd(f'caqtdm -macro "P={pv},M={m}" /ioc/modules/qt/MCS_extra.ui')

    # return string with motor value as variable representation
    def __str__(self):
        # """ return short info for the current motor"""
        s = f"{self.name}"
        s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL} (dial @ {self.get_current_value(posType='dial'):1.6g}; stat: {self.status_flag().name})"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{:1.6g}\n".format(*self.get_limits())
        s += f"\n{colorama.Style.DIM}low limit {colorama.Style.RESET_ALL}"
        s += ValueInRange(*self.get_limits()).get_str(self.get_current_value())
        s += f" {colorama.Style.DIM}high limit{colorama.Style.RESET_ALL}"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{1.6g}".format(self.get_limits())
        return s

    def __repr__(self):
        print(str(self))
        return object.__repr__(self)

    def __call__(self, value):
        self._currentChange = self.set_target_value(value)

    def _tweak_ioc(self, step_value=None):
        pv = self._motor.get_pv("TWV")
        pvf = self._motor.get_pv("TWF")
        pvr = self._motor.get_pv("TWR")
        if not step_value:
            step_value = pv.get()
        print(f"Tweaking {self.name} at step size {step_value}", end="\r")

        help = "q = exit; up = step*2; down = step/2, left = neg dir, right = pos dir\n"
        help = help + "g = go abs, s = set"
        print(f"tweaking {self.name}")
        print(help)
        print(f"Starting at {self.get_current_value()}")
        step_value = float(step_value)
        oldstep = 0
        k = KeyPress()
        cll = colorama.ansi.clear_line()

        class Printer:
            def print(self, **kwargs):
                print(
                    cll + f"stepsize: {self.stepsize}; current: {kwargs['value']}",
                    end="\r",
                )

        p = Printer()
        print(" ")
        p.stepsize = step_value
        p.print(value=self.get_current_value())
        ind_callback = self.add_value_callback(p.print)
        pv.put(step_value)
        while k.isq() is False:
            if oldstep != step_value:
                p.stepsize = step_value
                p.print(value=self.get_current_value())
                oldstep = step_value
            k.waitkey()
            if k.isu():
                step_value = step_value * 2.0
                pv.put(step_value)
            elif k.isd():
                step_value = step_value / 2.0
                pv.put(step_value)
            elif k.isr():
                pvf.put(1)
            elif k.isl():
                pvr.put(1)
            elif k.iskey("g"):
                print("enter absolute position (char to abort go to)")
                sys.stdout.flush()
                v = sys.stdin.readline()
                try:
                    v = float(v.strip())
                    self.set_target_value(v)
                except:
                    print("value cannot be converted to float, exit go to mode ...")
                    sys.stdout.flush()
            elif k.iskey("s"):
                print("enter new set value (char to abort setting)")
                sys.stdout.flush()
                v = sys.stdin.readline()
                try:
                    v = float(v[0:-1])
                    self.reset_current_value_to(v)
                except:
                    print("value cannot be converted to float, exit go to mode ...")
                    sys.stdout.flush()
            elif k.isq():
                break
            else:
                print(help)
        self.clear_value_callback(index=ind_callback)
        print(f"final position: {self.get_current_value()}")
        print(f"final tweak step: {pv.get()}")

    def tweak(self, *args, **kwargs):
        return self._tweak_ioc(*args, **kwargs)


@spec_convenience
@update_changes
@get_from_archive
@value_property
class SmaractRecord_old(Assembly):
    # Note: this is the one that works with the old SmarAct IOCs before Thierry made changes in 09/2023
    def __init__(
        self,
        pvname,
        name=None,
        elog=None,
        # alias_fields={"readback": "RBV"},
        alias_fields={},
        backlash_definition=False,
        expect_bad_limits=True,
    ):
        super().__init__(name=name)
        # self.settings.append(self)
        self.settings_collection.append(self, force=True)

        self.pvname = pvname
        self._motor = _Motor(pvname)
        self._elog = elog
        for an, af in alias_fields.items():
            self.alias.append(
                Alias(an, channel=".".join([pvname, af]), channeltype="CA")
            )
        self._currentChange = None

        self._append(
            AdjustablePvEnum,
            self.pvname + "_POS_TYPE_RB",
            pvname_set=self.pvname + "_POS_TYPE_SP",
            name="sensor_type",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + "_MAX_FREQ",
            name="max_frequency",
            is_setting=True,
        )
        self._append(AdjustablePv, self.pvname + ".VELO", name="speed", is_setting=True)
        self._append(
            AdjustablePv,
            self.pvname + ".ACCL",
            name="acceleration_time",
            is_setting=True,
        )
        self._append(
            AdjustablePv, self.pvname + ".LLM", name="limit_low", is_setting=True
        )
        self._append(
            AdjustablePv, self.pvname + ".HLM", name="limit_high", is_setting=True
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ".STAT",
            name="status_flag",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustablePvEnum, self.pvname + ".DIR", name="direction", is_setting=True
        )
        self._append(AdjustablePv, self.pvname + ".OFF", name="offset", is_setting=True)
        self._append(
            AdjustablePv, self.pvname + ".FOFF", name="force_offset", is_setting=True
        )
        self._append(
            AdjustablePv,
            self.pvname + "_AUTO_SET_EGU",
            name="autoset_unit",
            is_setting=True,
        )

        self._append(
            AdjustablePv,
            self.pvname + ".HOMR",
            name="home_forward",
            is_setting=False,
            is_status=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ".HOMR",
            name="home_reverse",
            is_setting=False,
            is_status=False,
            is_display=False,
        )

        self._append(
            DetectorPvData,
            self.pvname + ".RBV",
            name="readback",
            is_setting=False,
            is_display=True,
        )

        self._append(
            AdjustablePvEnum, self.pvname + ".SPMG", name="mode", is_setting=False
        )
        self._append(
            DetectorPvData, self.pvname + ".MSTA", name="_flags", is_setting=False
        )
        self._append(
            SmaractRecordFlags,
            self.pvname,
            self._flags,
            name="flags",
            is_display="recursive",
            is_status=True,
        )

        self._append(
            AdjustablePvString, self.pvname + ".EGU", name="unit", is_setting=True
        )
        self._append(
            AdjustablePvString,
            self.pvname + ".DESC",
            name="description",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + "_CAL_CMD",
            name="_calibrate_sensor",
            is_setting=True,
            is_status=False,
            is_display=False,
        )
        if backlash_definition:
            self._append(
                AdjustablePv,
                self.pvname + ".BVEL",
                name="backlash_velocity",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".BACC",
                name="backlash_acceleration",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".BDST",
                name="backlash_distance",
                is_setting=True,
            )
            self._append(
                AdjustablePv,
                self.pvname + ".FRAC",
                name="backlash_fraction",
                is_setting=True,
            )
        if expect_bad_limits:
            self.check_bad_limits()

    def check_bad_limits(self, abs_set_value=2**53):
        ll, hl = self.get_limits()
        if ll == 0 and hl == 0:
            self.set_limits(-abs_set_value, abs_set_value)

    def home(self):
        self.home_forward(1)
        time.sleep(0.1)
        while not self.flags.is_homed.get_current_value():
            time.sleep(0.1)

    def calibrate_sensor(self):
        self._calibrate_sensor(1)
        time.sleep(0.1)
        while not self.flags.motion_complete.get_current_value():
            time.sleep(0.1)

    def set_target_value(self, value, hold=False, check=True):
        """Adjustable convention"""

        def changer(value):
            self._status = self._motor.move(value, ignore_limits=(not check), wait=True)
            self._status_message = _status_messages[self._status]
            if self._status < 0:
                raise AdjustableError(self._status_message)
            elif self._status > 0:
                print("\n")
                print(self._status_message)

        #        changer = lambda value: self._motor.move(\
        #                value, ignore_limits=(not check),
        #                wait=True)
        return Changer(
            target=value,
            parent=self,
            changer=changer,
            hold=hold,
            stopper=self._motor.stop,
        )

    def stop(self):
        """Adjustable convention"""
        try:
            self._currentChange.stop()
        except:
            self.mode.set_target_value(0)
        pass

    def get_current_value(self, posType="user", readback=True):
        """Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        if posType == "user":
            return self._motor.get_position(readback=readback)
        if posType == "dial":
            return self._motor.get_position(readback=readback, dial=True)
        if posType == "raw":
            return self._motor.get_position(readback=readback, raw=True)

    def reset_current_value_to(self, value, posType="user"):
        """Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        if posType == "user":
            return self._motor.set_position(value)
        if posType == "dial":
            return self._motor.set_position(value, dial=True)
        if posType == "raw":
            return self._motor.set_position(value, raw=True)

    def get_moveDone(self):
        """Adjustable convention"""
        """ 0: moving 1: move done"""
        return PV(str(self.pvname + ".DMOV")).value

    def set_limits(
        self, low_limit, high_limit, posType="user", relative_to_present=False
    ):
        """
        set limits. usage: set_limits(low_limit, high_limit)

        """
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType == "dial":
            ll_name, hl_name = "DLLM", "DHLM"
        if relative_to_present:
            v = self.get_current_value(posType=posType)
            low_limit = v + low_limit
            high_limit = v + high_limit
        self._motor.put(ll_name, low_limit)
        self._motor.put(hl_name, high_limit)

    def add_value_callback(self, callback, index=None):
        return self._motor.get_pv("RBV").add_callback(callback=callback, index=index)

    def clear_value_callback(self, index=None):
        if index:
            self._motor.get_pv("RBV").remove_callback(index)
        else:
            self._motor.get_pv("RBV").clear_callbacks()

    def get_limits(self, posType="user"):
        """Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType == "dial":
            ll_name, hl_name = "DLLM", "DHLM"
        return self._motor.get(ll_name), self._motor.get(hl_name)

    def gui(self):
        pv, m = tuple(self.pvname.split(":"))
        self._run_cmd(f'caqtdm -macro "S={pv},M={m}" MCS_expert.ui')

    # return string with motor value as variable representation
    def __str__(self):
        # """ return short info for the current motor"""
        s = f"{self.name}"
        s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL} (dial @ {self.get_current_value(posType='dial'):1.6g}; stat: {self.status_flag().name})"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{:1.6g}\n".format(*self.get_limits())
        s += f"\n{colorama.Style.DIM}low limit {colorama.Style.RESET_ALL}"
        s += ValueInRange(*self.get_limits()).get_str(self.get_current_value())
        s += f" {colorama.Style.DIM}high limit{colorama.Style.RESET_ALL}"
        # # s +=  "\tuser limits      (low,high) : {:1.6g},{1.6g}".format(self.get_limits())
        return s

    def __repr__(self):
        print(str(self))
        return object.__repr__(self)

    def __call__(self, value):
        self._currentChange = self.set_target_value(value)

    def _tweak_ioc(self, step_value=None):
        pv = self._motor.get_pv("TWV")
        pvf = self._motor.get_pv("TWF")
        pvr = self._motor.get_pv("TWR")
        if not step_value:
            step_value = pv.get()
        print(f"Tweaking {self.name} at step size {step_value}", end="\r")

        help = "q = exit; up = step*2; down = step/2, left = neg dir, right = pos dir\n"
        help = help + "g = go abs, s = set"
        print(f"tweaking {self.name}")
        print(help)
        print(f"Starting at {self.get_current_value()}")
        step_value = float(step_value)
        oldstep = 0
        k = KeyPress()
        cll = colorama.ansi.clear_line()

        class Printer:
            def print(self, **kwargs):
                print(
                    cll + f"stepsize: {self.stepsize}; current: {kwargs['value']}",
                    end="\r",
                )

        p = Printer()
        print(" ")
        p.stepsize = step_value
        p.print(value=self.get_current_value())
        ind_callback = self.add_value_callback(p.print)
        pv.put(step_value)
        while k.isq() is False:
            if oldstep != step_value:
                p.stepsize = step_value
                p.print(value=self.get_current_value())
                oldstep = step_value
            k.waitkey()
            if k.isu():
                step_value = step_value * 2.0
                pv.put(step_value)
            elif k.isd():
                step_value = step_value / 2.0
                pv.put(step_value)
            elif k.isr():
                pvf.put(1)
            elif k.isl():
                pvr.put(1)
            elif k.iskey("g"):
                print("enter absolute position (char to abort go to)")
                sys.stdout.flush()
                v = sys.stdin.readline()
                try:
                    v = float(v.strip())
                    self.set_target_value(v)
                except:
                    print("value cannot be converted to float, exit go to mode ...")
                    sys.stdout.flush()
            elif k.iskey("s"):
                print("enter new set value (char to abort setting)")
                sys.stdout.flush()
                v = sys.stdin.readline()
                try:
                    v = float(v[0:-1])
                    self.reset_current_value_to(v)
                except:
                    print("value cannot be converted to float, exit go to mode ...")
                    sys.stdout.flush()
            elif k.isq():
                break
            else:
                print(help)
        self.clear_value_callback(index=ind_callback)
        print(f"final position: {self.get_current_value()}")
        print(f"final tweak step: {pv.get()}")

    def tweak(self, *args, **kwargs):
        return self._tweak_ioc(*args, **kwargs)


flag_names_smaract_record = {
    0: "direction",
    1: "motion_complete",
    # 2:"pos_limit_switch",
    # 3:"home_switch",
    # 4:"unused",
    # 5:"closed_loop_position",
    # 6:"slipstall_detected",
    # 7:"at_home_position",
    # 8:"encoder_is_present",
    # 9:"problem",
    # 10:"moving",
    # 11:"gain_support",
    # 12:"communication_error",
    # 13:"neg_limit_switch",
    14: "is_homed",
}


class SmaractRecordFlags(Assembly):
    def __init__(self, pvname, flags, name="flags"):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(DetectorPvData, self.pvname + ".MISS", name="has_reached")
        self._flags = flags
        for i, flag_name in flag_names_smaract_record.items():
            self._append(
                DetectorVirtual,
                [self._flags],
                partial(self._get_flag_index_value, index=i),
                name=flag_name,
                is_display=True,
            )

    def _get_flag_index_value(self, value, index):
        return int("{0:015b}".format(int(value))[-1 * (index + 1)]) == 1
