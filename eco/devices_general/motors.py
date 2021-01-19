# from ..eco_epics.motor import Motor as _Motor
from epics.motor import Motor as _Motor
from ..eco_epics.utilities_epics import EpicsString, WaitPvConditions
import subprocess
from threading import Thread
from epics import PV
from .utilities import Changer
from ..aliases import Alias
from .adjustable import spec_convenience, ValueInRange, update_changes, AdjustableError
import colorama
from ..utilities.KeyPress import KeyPress
import sys, colorama
from .. import global_config
from ..elements.assembly import Assembly
import time
from .adjustable import PvRecord, PvEnum, PvString
import numpy as np

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
class SmaractStreamdevice(Assembly):
    def __init__(
        self,
        pvname,
        accuracy=1e-3,
        name=None,
        elog=None,
        alias_fields={
            "readback": "MOTRBV",
            "user_set_pos": "SET_POS",
            "user_direction": "DIR",
        },
    ):
        super().__init__(name=name)
        self.settings.append(self)

        self.pvname = pvname
        self._elog = elog
        for an, af in alias_fields.items():
            self.alias.append(
                Alias(an, channel=":".join([pvname, af]), channeltype="CA")
            )
        self._currentChange = None
        # self.description = EpicsString(pvname + ".DESC")
        self._append(PvEnum, self.pvname + ":DIR", name="direction", is_setting=True)
        # self._append(
        #    PvRecord, self.pvname + ":SET_POS", name="set_pos", is_setting=True
        # )
        self._append(
            PvRecord,
            self.pvname + ":FRM_BACK.PROC",
            name="home_backward",
            is_setting=False,
        )
        self._append(
            PvRecord,
            self.pvname + ":FRM_FORW.PROC",
            name="home_forward",
            is_setting=False,
        )
        self._append(
            PvRecord, self.pvname + ":GET_HOMED", name="is_homed", is_setting=False
        )
        self._append(
            PvRecord,
            self.pvname + ":CALIBRATE.PROC",
            name="calibrate_sensor",
            is_setting=False,
        )
        self._append(
            PvRecord,
            self.pvname + ":CL_MAX_FREQ",
            name="speed",
            is_setting=False,
        )
        self._append(
            PvRecord,
            self.pvname + ":HOLD",
            name="holding_time_ms",
            is_setting=True,
        )
        self._append(
            PvRecord,
            self.pvname + ":AMPLITUDE",
            name="voltage_4KADU_per_100V",
            is_setting=True,
        )
        self._append(
            PvRecord,
            self.pvname + ":DRIVE",
            name="_drive",
            is_setting=False,
        )
        self._append(
            PvRecord,
            self.pvname + ":MOTRBV",
            name="_readback",
            is_setting=False,
        )
        self._append(
            PvEnum, self.pvname + ":STAGE_TYPE", name="stage_type", is_setting=True
        )
        self._append(
            PvEnum, self.pvname + ":STATUS", name="status_channel", is_setting=False
        )
        self._append(
            PvEnum,
            self.pvname + ":GET_SENSOR_TYPE",
            pvname_set=self.pvname + ":SET_SENSOR_TYPE",
            name="sensor_type",
            is_setting=True,
        )
        self._append(PvRecord, self.pvname + ":LLM", name="limit_low", is_setting=False)
        self._append(
            PvRecord, self.pvname + ":HLM", name="limit_high", is_setting=False
        )
        self._append(
            PvRecord, self.pvname + ":NAME", name="caqtdm_name", is_setting=True
        )
        self.accuracy = accuracy
        self._stop_pv = PV(self.pvname + ":STOP.PROC")
        self.stop = lambda: self._stop_pv.put(1)

    def set_target_value(self, value, hold=False, check=True):
        def changer(value):
            self._drive.set_target_value(value)

            # self._status_message = _status_messages[self._status]
            # if self._status < 0:
            # raise AdjustableError(self._status_message)
            # elif self._status > 0:
            # print("\n")
            # print(self._status_message)

        changer = lambda value: self.move(value, check=check)
        return Changer(
            target=value,
            parent=self,
            changer=changer,
            hold=hold,
            stopper=self.stop,
        )

    def get_current_value(self):
        return self._readback.get_current_value()

    def get_close_to(self, value, accuracy):
        movedone = 1
        if np.abs(value - self.get_current_value()) > accuracy:
            movedone = 0
        return movedone

    def move(self, value, check=True, update_value_time=0.05, timeout=120):
        if check:
            lim_low, lim_high = self.get_limits()
            if not (lim_low < value) and (value < lim_high):
                raise AdjustableError("Soft limits violated!")
        t_start = time.time()
        # waiter = WaitPvConditions(
        #     self.status_channel._pv,
        #     lambda **kwargs: not kwargs["value"] == 0,
        #     lambda **kwargs: kwargs["value"] == 0,
        # )
        self._drive.set_target_value(value)
        # waiter.wait_until_done(check_interval=update_value_time)

        while not self.get_close_to(value, self.accuracy):
            if (time.time() - t_start) > timeout:
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
        return self.limit_low.get_current_value(), self.limit_high.get_current_value()

    def set_limits(self, low_limit, high_limit, relative_to_present=False):
        if relative_to_present:
            tval = self.get_current_value()
            low_limit += tval
            high_limit += tval
        self.limit_low.set_target_value(low_limit)
        self.limit_high.set_target_value(high_limit)

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
class MotorRecord(Assembly):
    def __init__(
        self,
        pvname,
        name=None,
        elog=None,
        alias_fields={"readback": "RBV", "user_offset": "OFF", "user_direction": "DIR"},
        expect_bad_limits=True,
    ):
        super().__init__(name=name)
        self.settings.append(self)

        self.pvname = pvname
        self._motor = _Motor(pvname)
        self._elog = elog
        for an, af in alias_fields.items():
            self.alias.append(
                Alias(an, channel=".".join([pvname, af]), channeltype="CA")
            )
        self._currentChange = None
        # self.description = EpicsString(pvname + ".DESC")
        self._append(PvEnum, self.pvname + ".DIR", name="direction", is_setting=True)
        self._append(PvRecord, self.pvname + ".OFF", name="offset", is_setting=True)
        self._append(PvRecord, self.pvname + ".VELO", name="speed", is_setting=False)
        self._append(
            PvRecord, self.pvname + ".ACCL", name="acceleration_time", is_setting=False
        )
        self._append(PvRecord, self.pvname + ".LLM", name="limit_low", is_setting=False)
        self._append(
            PvRecord, self.pvname + ".HLM", name="limit_high", is_setting=False
        )
        self._append(
            PvEnum, self.pvname + ".SPMG", name="motor_state", is_setting=False
        )
        self._append(PvString, self.pvname + ".EGU", name="unit", is_setting=False)
        self._append(
            PvString, self.pvname + ".DESC", name="description", is_setting=False
        )
        if expect_bad_limits:
            self.check_bad_limits()

    def check_bad_limits(self, abs_set_value=2 ** 53):
        ll, hl = self.get_limits()
        if ll == 0 and hl == 0:
            self.set_limits(-abs_set_value, abs_set_value)

    def set_target_value(self, value, hold=False, check=True):
        """ Adjustable convention"""

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
        """ Adjustable convention"""
        try:
            self._currentChange.stop()
        except:
            self._motor.stop()
        pass

    def get_current_value(self, posType="user", readback=True):
        """ Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        if posType == "user":
            return self._motor.get_position(readback=readback)
        if posType == "dial":
            return self._motor.get_position(readback=readback, dial=True)
        if posType == "raw":
            return self._motor.get_position(readback=readback, raw=True)

    def reset_current_value_to(self, value, posType="user"):
        """ Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        if posType == "user":
            return self._motor.set_position(value)
        if posType == "dial":
            return self._motor.set_position(value, dial=True)
        if posType == "raw":
            return self._motor.set_position(value, raw=True)

    def get_moveDone(self):
        """ Adjustable convention"""
        """ 0: moving 1: move done"""
        return PV(str(self.Id + ".DMOV")).value

    def set_limits(
        self, low_limit, high_limit, posType="user", relative_to_present=False
    ):
        """
        set limits. usage: set_limits(low_limit, high_limit)

        """
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType is "dial":
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
        """ Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType is "dial":
            ll_name, hl_name = "DLLM", "DHLM"
        return self._motor.get(ll_name), self._motor.get(hl_name)

    def gui(self, guiType="xdm"):
        """ Adjustable convention"""
        cmd = ["caqtdm", "-macro"]

        cmd.append('"P=%s:,M=%s"' % tuple(self.pvname.split(":")))
        # cmd.append('/sf/common/config/qt/motorx_more.ui')
        cmd.append("motorx_more.ui")
        # os.system(' '.join(cmd))
        return subprocess.Popen(" ".join(cmd), shell=True, stdout=subprocess.DEVNULL)

    # return string with motor value as variable representation
    def __str__(self):
        # """ return short info for the current motor"""
        s = f"{self.name}"
        s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL} (dial @ {self.get_current_value(posType='dial'):1.6g})"
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


MotorRecord_new = MotorRecord


@spec_convenience
@update_changes
class MotorRecord_old:
    def __init__(
        self,
        pvname,
        name=None,
        elog=None,
        alias_fields={"readback": "RBV", "user_offset": "OFF"},
    ):
        self.Id = pvname
        self._motor = _Motor(pvname)
        self._elog = elog
        self.name = name
        self.alias = Alias(name)
        for an, af in alias_fields.items():
            self.alias.append(
                Alias(an, channel=".".join([pvname, af]), channeltype="CA")
            )
        self._currentChange = None
        self.description = EpicsString(pvname + ".DESC")

    # Conventional methods and properties for all Adjustable objects
    def set_target_value(self, value, hold=False, check=True):
        """ Adjustable convention"""

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
        """ Adjustable convention"""
        try:
            self._currentChange.stop()
        except:
            self._motor.stop()
        pass

    def get_current_value(self, posType="user", readback=True):
        """ Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        if posType == "user":
            return self._motor.get_position(readback=readback)
        if posType == "dial":
            return self._motor.get_position(readback=readback, dial=True)
        if posType == "raw":
            return self._motor.get_position(readback=readback, raw=True)

    def reset_current_value_to(self, value, posType="user"):
        """ Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        if posType == "user":
            return self._motor.set_position(value)
        if posType == "dial":
            return self._motor.set_position(value, dial=True)
        if posType == "raw":
            return self._motor.set_position(value, raw=True)

    def get_precision(self):
        """ Adjustable convention"""
        pass

    def set_precision(self):
        """ Adjustable convention"""
        pass

    precision = property(get_precision, set_precision)

    def set_speed(self):
        """ Adjustable convention"""
        pass

    def get_speed(self):
        """ Adjustable convention"""
        pass

    def set_speedMax(self):
        """ Adjustable convention"""
        pass

    def get_moveDone(self):
        """ Adjustable convention"""
        """ 0: moving 1: move done"""
        return PV(str(self.Id + ".DMOV")).value

    def set_limits(
        self, low_limit, high_limit, posType="user", relative_to_present=False
    ):
        """
        set limits. usage: set_limits(low_limit, high_limit)

        """
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType is "dial":
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
        """ Adjustable convention"""
        _keywordChecker([("posType", posType, _posTypes)])
        ll_name, hl_name = "LLM", "HLM"
        if posType is "dial":
            ll_name, hl_name = "DLLM", "DHLM"
        return self._motor.get(ll_name), self._motor.get(hl_name)

    def gui(self, guiType="xdm"):
        """ Adjustable convention"""
        cmd = ["caqtdm", "-macro"]

        cmd.append('"P=%s:,M=%s"' % tuple(self.Id.split(":")))
        # cmd.append('/sf/common/config/qt/motorx_more.ui')
        cmd.append("motorx_more.ui")
        # os.system(' '.join(cmd))
        return subprocess.Popen(" ".join(cmd), shell=True)

    # return string with motor value as variable representation
    def __str__(self):
        # """ return short info for the current motor"""
        s = f"{self.name}"
        s += f"\t@ {colorama.Style.BRIGHT}{self.get_current_value():1.6g}{colorama.Style.RESET_ALL} (dial @ {self.get_current_value(posType='dial'):1.6g})"
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


class ChangerOld:
    def __init__(self, target=None, parent=None, mover=None, hold=True, stopper=None):
        self.target = target
        self._mover = mover
        self._stopper = stopper
        self._thread = Thread(target=self._mover, args=(target,))
        if not hold:
            self._thread.start()

    def wait(self):
        self._thread.join()

    def start(self):
        self._thread.start()

    def status(self):
        if self._thread.ident is None:
            return "waiting"
        else:
            if self._thread.isAlive:
                return "changing"
            else:
                return "done"

    def stop(self):
        self._stopper()
