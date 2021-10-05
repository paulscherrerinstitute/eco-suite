import time
from enum import IntEnum

import numpy as np
from epics import PV

from eco.aliases import Alias
from eco.elements.adjustable import tweak_option, spec_convenience, value_property
from . import get_from_archive
from eco.devices_general.utilities import Changer
from ..elements import Assembly

@spec_convenience
@get_from_archive
@tweak_option
@value_property
class AdjustablePv:
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

        self._pv = PV(self.Id, connection_timeout=0.05)
        self._currentChange = None
        self.accuracy = accuracy

        if pvreadbackname is None:
            self._pvreadback = PV(self.Id, connection_timeout=0.05)
            pvreadbackname = self.Id
            self.pvname = self.Id
        else:
            self._pvreadback = PV(pvreadbackname, connection_timeout=0.05)
            self.pvname = pvreadbackname
        self.alias = Alias(name, channel=pvreadbackname, channeltype="CA")

    def get_current_value(self, readback=True):
        if readback:
            currval = self._pvreadback.get()
        if not readback:
            currval = self._pv.get()
        return currval

    def get_change_done(self):
        """Adjustable convention"""
        """ 0: moving 1: move done"""
        change_done = 1
        if self.accuracy is not None:
            if (
                np.abs(
                    self.get_current_value(readback=False)
                    - self.get_current_value(readback=True)
                )
                > self.accuracy
            ):
                change_done = 0
        return change_done

    def change(self, value):
        self._pv.put(value)
        time.sleep(0.1)
        while self.get_change_done() == 0:
            time.sleep(0.1)

    def set_target_value(self, value, hold=False):
        """Adjustable convention"""

        changer = lambda value: self.change(value)
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


@spec_convenience
@get_from_archive
@value_property
class AdjustablePvEnum:
    def __init__(self, pvname, pvname_set=None, name=None):
        self.Id = pvname
        self.pvname = pvname
        self._pv = PV(pvname, connection_timeout=0.05)
        self.name = name
        self.enum_strs = self._pv.enum_strs

        if pvname_set:
            self._pv_set = PV(pvname_set, connection_timeout=0.05)
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
        """Adjustable convention"""
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


class AdjustablePvString:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.pvname = pvname
        self._pv = PV(pvname, connection_timeout=0.05)
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
