import subprocess
from threading import Thread
from epics import PV
from .utilities import Changer
from ..aliases import Alias
from enum import IntEnum, auto
import colorama
import time
import logging
import datetime
import numpy as np
from pathlib import Path
from json import load, dump
from .. import ecocnf

logger = logging.getLogger(__name__)


# exceptions
class AdjustableError(Exception):
    pass


# wrappers for adjustables >>>>>>>>>>>


def get_from_archive(Obj, attribute_name="pvname"):
    def get_archiver_time_range(self, start=None, end=None, plot=True):
        """Try to retrieve data within timerange from archiver. A time delta from now is assumed if end time is missing. """
        channelname = self.__dict__[attribute_name]
        return ecocnf.archiver.get_data_time_range(
            channels=[channelname], start=start, end=end, plot=plot
        )

    Obj.get_archiver_time_range = get_archiver_time_range
    return Obj


def default_representation(Obj):
    def get_name(Obj):
        if hasattr(Obj, "alias") and Obj.alias:
            return Obj.alias.get_full_name()
        elif Obj.name:
            return Obj.name
        elif hasattr(Obj, "Id") and Obj.Id:
            return Obj.Id
        else:
            return ""

    def get_repr(Obj):
        s = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S") + ": "
        s += f"{colorama.Style.BRIGHT}{Obj._get_name()}{colorama.Style.RESET_ALL} at {colorama.Style.BRIGHT}{str(Obj.get_current_value())}{colorama.Style.RESET_ALL}"
        return s

    Obj._get_name = get_name
    Obj.__repr__ = get_repr
    return Obj


def spec_convenience(Adj):
    # spec-inspired convenience methods

    def wm(self, *args, **kwargs):
        return self.get_current_value(*args, **kwargs)

    Adj.wm = wm
    if hasattr(Adj, "update_change"):

        def umv(self, *args, **kwargs):
            self.update_change(*args, **kwargs)

        def umvr(self, *args, **kwargs):
            self.update_change_relative(*args, **kwargs)

        Adj.mv = umv
        Adj.mvr = umvr
        Adj.umv = umv
        Adj.umvr = umvr
    else:

        def mv(self, value, check_limits=True):
            try:
                self._currentChange = self.set_target_value(value)
                self._currentChange.wait()
            except KeyboardInterrupt:
                self._currentChange.stop()
            return self._currentChange

        def mvr(self, value, *args, **kwargs):
            if (
                hasattr(self, "_currentChange")
                and self._currentChange
                and not (self._currentChange.status() == "done")
            ):
                startvalue = self._currentChange.target
            elif hasattr(self, "get_moveDone") and (self.get_moveDone == 1):
                startvalue = self.get_current_value(readback=True, *args, **kwargs)
            else:
                startvalue = self.get_current_value(*args, **kwargs)
            try:
                self._currentChange = self.set_target_value(
                    value + startvalue, *args, **kwargs
                )
                self._currentChange.wait()
            except KeyboardInterrupt:
                self._currentChange.stop()
            return self._currentChange

        Adj.mv = mv
        Adj.mvr = mvr

    def call(self, value=None):
        if not value is None:
            return self.mv(value)
        else:
            return self.wm()

    Adj.__call__ = call

    return Adj


class ValueInRange:
    def __init__(self, start_value, end_value, bar_width=30, unit="", fmt="1.5g"):
        self.start_value = start_value
        self.end_value = end_value
        self.unit = unit
        self.bar_width = bar_width
        self._blocks = " ▏▎▍▌▋▊▉█"
        self._fmt = fmt

    def get_str(self, value):
        if self.start_value == self.end_value:
            frac = 1
        else:
            frac = (value - self.start_value) / (self.end_value - self.start_value)
        return (
            f"{self.start_value:{self._fmt}}"
            + self.get_unit_str()
            + "|"
            + self.bar_str(frac)
            + "|"
            + f"{self.end_value:{self._fmt}}"
            + self.get_unit_str()
        )

    def get_unit_str(self):
        if not self.unit:
            return ""
        else:
            return " " + self.unit

    def bar_str(self, frac):
        blocks = self._blocks
        if 0 < frac and frac <= 1:
            whole = int(self.bar_width // (1 / frac))
            part = int((frac * self.bar_width - whole) // (1 / (len(blocks) - 1)))
            return (
                colorama.Fore.GREEN
                + whole * blocks[-1]
                + blocks[part]
                + (self.bar_width - whole - 1) * blocks[0]
                + colorama.Fore.RESET
            )
        elif frac == 0:
            return self.bar_width * blocks[0]
        elif frac < 0:
            return colorama.Fore.RED + "<" * self.bar_width + colorama.Fore.RESET
        elif frac > 1:
            return colorama.Fore.RED + ">" * self.bar_width + colorama.Fore.RESET


def update_changes(Adj):
    def get_position_str(start, end, value):
        start = float(start)
        value = float(value)
        end = float(end)
        s = ValueInRange(start, end, bar_width=30, unit="", fmt="1.5g").get_str(value)
        return (
            colorama.Style.BRIGHT
            + f"{value:1.5}".rjust(10)
            + colorama.Style.RESET_ALL
            + "  "
            + s
            + 2 * "\t"
        )

    def update_change(self, value, elog=None):
        start = self.get_current_value()
        print(
            f"Changing {self.name} from {start:1.5g} by {value-start:1.5g} to {value:1.5g}"
        )
        print(get_position_str(start, value, start), end="\r")
        try:
            if hasattr(self, "add_value_callback"):

                def cbfoo(**kwargs):
                    print(get_position_str(start, value, kwargs["value"]), end="\r")

                cb_id = self.add_value_callback(cbfoo)
            self._currentChange = self.set_target_value(value)
            self._currentChange.wait()
        except KeyboardInterrupt:
            self._currentChange.stop()
            print(f"\nAborted change at (~) {self.get_current_value():1.5g}")
        finally:
            if hasattr(self, "add_value_callback"):
                self.clear_value_callback(cb_id)
        if elog:
            if not hasattr(self, "elog"):
                raise Exception("No elog defined!")

            elog_str = f"Changing {self.name} from {start:1.5g} by {value-start:1.5g} to {value:1.5g}"
            elog_title = f"Adjusting {self.name}"
            if type(elog) is str:
                elog = {"message": elog}
            elif type(elog) is dict:
                pass
            else:
                elog = {}
            self.elog.post(**elog)

        return self._currentChange

    def update_change_relative(self, value, *args, **kwargs):
        if (
            hasattr(self, "_currentChange")
            and self._currentChange
            and not (self._currentChange.status() == "done")
        ):
            startvalue = self._currentChange.target
        elif hasattr(self, "get_moveDone") and (self.get_moveDone == 1):
            startvalue = self.get_current_value(readback=True, *args, **kwargs)
        else:
            startvalue = self.get_current_value(*args, **kwargs)
        self._currentChange = self.update_change(value + startvalue, *args, **kwargs)
        return self._currentChange

    Adj.update_change = update_change
    Adj.update_change_relative = update_change_relative

    return Adj


# wrappers for adjustables <<<<<<<<<<<


@spec_convenience
@update_changes
class DummyAdjustable:
    def __init__(self, name="no_adjustable"):
        self.name = name
        self.current_value = 0

    def get_current_value(self):
        return self.current_value

    def set_target_value(self, value, hold=False):
        def changer(value):
            self.current_value = value

        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

    def __repr__(self):
        name = self.name
        cv = self.get_current_value()
        s = f"{name} at value: {cv}" + "\n"
        return s


def _keywordChecker(kw_key_list_tups):
    for tkw, tkey, tlist in kw_key_list_tups:
        assert tkey in tlist, "Keyword %s should be one of %s" % (tkw, tlist)


@spec_convenience
@update_changes
class AdjustableMemory:
    def __init__(self, value, name="adjustable_memory"):
        self.name = name
        self.alias = Alias(name)
        self.current_value = value

    def get_current_value(self):
        return self.current_value

    def set_target_value(self, value, hold=False):
        def changer(value):
            self.current_value = value

        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

    def __repr__(self):
        name = self.name
        cv = self.get_current_value()
        s = f"{name} at value: {cv}" + "\n"
        return s


@default_representation
@spec_convenience
class AdjustableFS:
    def __init__(self, file_path, name=None, default_value=None):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            if not self.file_path.parent.exists():
                self.file_path.parent.mkdir()
            self._write_value(default_value)
        self.alias = Alias(name)
        self.name = name

    def get_current_value(self):
        with open(self.file_path, "r") as f:
            res = load(f)
        return res["value"]

    def _write_value(self, value):
        with open(self.file_path, "w") as f:
            dump({"value": value}, f)

    def set_target_value(self, value, hold=False):
        return Changer(
            target=value,
            parent=self,
            changer=self._write_value,
            hold=hold,
            stopper=None,
        )


@spec_convenience
@get_from_archive
class PvRecord:
    def __init__(
        self, pvsetname, pvreadbackname=None, accuracy=None, name=None, elog=None
    ):

        #        alias_fields={"setpv": pvsetname, "readback": pvreadbackname},
        #    ):
        self.Id = pvsetname
        self.name = name
        #        for an, af in alias_fields.items():
        #            self.alias.append(
        #                Alias(an, channel=".".join([pvname, af]), channeltype="CA")
        #            )

        self._pv = PV(self.Id)
        self._currentChange = None
        self.accuracy = accuracy

        if pvreadbackname is None:
            self._pvreadback = PV(self.Id)
            pvreadbackname = self.Id
            self.pvname = self.Id
        else:
            self._pvreadback = PV(pvreadbackname)
            self.pvname = self.pvreadbackname
        self.alias = Alias(name, channel=pvreadbackname, channeltype="CA")

    def get_current_value(self, readback=True):
        if readback:
            currval = self._pvreadback.get()
        if not readback:
            currval = self._pv.get()
        return currval

    def get_moveDone(self):
        """ Adjustable convention"""
        """ 0: moving 1: move done"""
        movedone = 1
        if self.accuracy is not None:
            if (
                np.abs(
                    self.get_current_value(readback=False)
                    - self.get_current_value(readback=True)
                )
                > self.accuracy
            ):
                movedone = 0
        return movedone

    def move(self, value):
        self._pv.put(value)
        time.sleep(0.1)
        while self.get_moveDone() == 0:
            time.sleep(0.1)

    def set_target_value(self, value, hold=False):
        """ Adjustable convention"""

        changer = lambda value: self.move(value)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

    # spec-inspired convenience methods
    # def mv(self, value):
    # self._currentChange = self.set_target_value(value)

    # def wm(self, *args, **kwargs):
    # return self.get_current_value(*args, **kwargs)

    # def mvr(self, value, *args, **kwargs):

    # if self.get_moveDone == 1:
    # startvalue = self.get_current_value(readback=True, *args, **kwargs)
    # else:
    # startvalue = self.get_current_value(readback=False, *args, **kwargs)
    # self._currentChange = self.set_target_value(value + startvalue, *args, **kwargs)

    # def wait(self):
    # self._currentChange.wait()

    def __repr__(self):
        return "%s is at: %s" % (self.Id, self.get_current_value())


# @default_representation
@spec_convenience
@get_from_archive
class PvEnum:
    def __init__(self, pvname, pvname_set=None, name=None):
        self.Id = pvname
        self.pvname = pvname
        self._pv = PV(pvname)
        self.name = name
        self.enum_strs = self._pv.enum_strs

        if pvname_set:
            self._pv_set = PV(pvname_set)
            tstrs = self._pv_set.enum_strs
            if not (tstrs == self.enum_strs):
                raise Exception("pv enum setter strings do not match the values!")

        else:
            self._pv_set = None

        if name:
            enumname = self.name
        else:
            enumname = self.Id
        self.PvEnum = IntEnum(
            enumname, {tstr: n for n, tstr in enumerate(self.enum_strs)}
        )
        self.alias = Alias(name, channel=self.Id, channeltype="CA")

    def validate(self, value):
        if type(value) is str:
            return self.PvEnum.__members__[value]
        else:
            return self.PvEnum(value)

    def get_current_value(self):
        return self.validate(self._pv.get())

    def set_target_value(self, value, hold=False):
        """ Adjustable convention"""
        value = self.validate(value)
        if self._pv_set:
            tpv = self._pv_set
        else:
            tpv = self._pv
        changer = lambda value: tpv.put(value, wait=True)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

    def __repr__(self):
        if not self.name:
            name = self.Id
        else:
            name = self.name
        cv = self.get_current_value()
        s = f"{name} (enum) at value: {cv}" + "\n"
        s += "{:<5}{:<5}{:<}\n".format("Num.", "Sel.", "Name")
        # s+= '_'*40+'\n'
        for name, val in self.PvEnum.__members__.items():
            if val == cv:
                sel = "x"
            else:
                sel = " "
            s += "{:>4}   {}  {}\n".format(val, sel, name)
        return s


class PvString:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.pvname = pvname
        self._pv = PV(pvname)
        self._elog = elog
        self.alias = Alias(name, channel=self.pvname, channeltype="CA")

    def get_current_value(self):
        return self._pv.get()

    def set_target_value(self, value, hold=False):
        changer = lambda value: self._pv.put(bytes(value, "utf8"), wait=True)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

    def __repr__(self):
        return self.get_current_value()

    def __call__(self, string=None):
        if not string is None:
            self.set_target_value(string)
        else:
            return self.get_current_value()


@default_representation
@spec_convenience
class AdjustableVirtual:
    def __init__(
        self,
        adjustables,
        foo_get_current_value,
        foo_set_target_value_current_value,
        change_simultaneously=True,
        reset_current_value_to=False,
        append_aliases=False,
        name=None,
        unit=None,
    ):
        self.name = name
        self.alias = Alias(name)
        if append_aliases:
            for adj in adjustables:
                try:
                    self.alias.append(adj.alias)
                except Exception as e:
                    logger.warning(f"could not find alias in {adj}")
                    print(str(e))
        self._adjustables = adjustables
        self._foo_set_target_value_current_value = foo_set_target_value_current_value
        self._foo_get_current_value = foo_get_current_value
        self._reset_current_value_to = reset_current_value_to
        self._change_simultaneously = change_simultaneously
        if reset_current_value_to:
            for adj in self._adjustables:
                if not hasattr(adj, "reset_current_value_to"):
                    raise Exception(f"No reset_current_value_to method found in {adj}")
        if unit:
            self.unit = AdjustableMemory(unit, name="unit")

    def set_target_value(self, value, hold=False):
        vals = self._foo_set_target_value_current_value(value)
        if not hasattr(vals, "__iter__"):
            vals = (vals,)

        def changer(value):
            if self._change_simultaneously:
                self._active_changers = [
                    adj.set_target_value(val, hold=False)
                    for val, adj in zip(vals, self._adjustables)
                ]
                for tc in self._active_changers:
                    tc.wait()
            else:

                for val, adj in zip(vals, self._adjustables):
                    self._active_changers = [adj.set_target_value(val, hold=False)]
                    self._active_changers[0].wait()

        def stopper():
            for tc in self._active_changers:
                tc.stop()

        self._currentChange = Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=stopper
        )
        return self._currentChange

    def get_current_value(self):
        return self._foo_get_current_value(
            *[adj.get_current_value() for adj in self._adjustables]
        )

    def reset_current_value_to(self, value):
        if not self._reset_current_value_to:
            raise NotImplementedError(
                "There is no value setting implemented for this virtual adjuster!"
            )
        else:
            vals = self._foo_set_target_value_current_value(value)
            for adj, val in zip(self._adjustables, vals):
                adj.reset_current_value_to(val)


@default_representation
@spec_convenience
class AdjustableGetSet:
    def __init__(self, foo_get, foo_set, precision=0, check_interval=None, name=None):
        """ assumes a waiting setterin function, in case no check_interval parameter is supplied"""
        self.alias = Alias(name)
        self.name = name
        self._set = foo_set
        self._get = foo_get
        self._check_interval = check_interval
        self.precision = precision

    def set_and_wait(self, value):
        if self._check_interval:
            self._set(value)
            while abs(self.get_current_value() - value) > self.precision:
                time.sleep(self._check_interval)
        else:
            self._set(value)

    def set_target_value(self, value):
        return Changer(
            target=value,
            parent=self,
            changer=self.set_and_wait,
            hold=False,
            stopper=None,
        )

    def get_current_value(self):
        return self._get()
