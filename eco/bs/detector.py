import threading
from enum import IntEnum
from time import time, sleep

import numpy as np
from epics import PV
from eco.epics_utils import ca_tuning
from eco.epics_utils.ca_tuning import (
    CA_CONNECTION_TIMEOUT,
    CA_INIT_CONNECTION_TIMEOUT,
)
# The CA-backed classes in this module read through the same chokepoint as
# eco.epics_utils: it is what applies the retry for a channel that has
# worked before, and the silent-None diagnostics. Importing the diagnostics
# without ever calling them (as this module did) left these reads with no
# None handling at all.
from eco.epics_utils.adjustable import _read_pv

from eco.acquisition.utilities import Acquisition
from eco.aliases import Alias
from eco.elements.assembly import Assembly
from eco.epics_utils.adjustable import AdjustablePvString, wait_for_enum_strs
from eco.epics_utils import adjustable as _adjustable_module
from eco.epics_utils import get_from_archive
from eco.elements.protocols import enum_repr


@get_from_archive
class DetectorBsData(Assembly):
    def __init__(self, bschannel, name=None):
        super().__init__(name=name)
        self.status_collection.append(self)
        self.bschannel = bschannel
        if epics_pv_available & epics_pv_availabe == "same":
            self._pv = ca_tuning.make_pv(pvname)
            self._append(
                AdjustablePvString, self.pvname + ".EGU", name="unit", is_setting=False
            )
        self.name = name
        self.alias = Alias(self.name, channel=self.pvname, channeltype="BS")

    def get_current_value(self):
        return _read_pv(self._pv, name=getattr(self, "name", None))

    def __call__(self):
        return self.get_current_value()


@enum_repr
@get_from_archive
class DetectorPvEnum(Assembly):
    """See eco.epics_utils.detector.DetectorPvEnum's docstring -- same class,
    duplicated here; enum resolution is likewise deferred to first use."""

    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._pv = ca_tuning.make_pv(pvname, connection_timeout=CA_CONNECTION_TIMEOUT)
        self.name = name
        self.alias = Alias(name, channel=self.pvname, channeltype="CA")
        self._resolve_lock = threading.Lock()
        self._resolved = False
        self._enum_strs = None
        self._pv_enum = None
        if not _adjustable_module.LAZY_ENUM_RESOLUTION:
            # default: resolve now, like before this speedup existed -- see
            # eco.epics_utils.adjustable.LAZY_ENUM_RESOLUTION's docstring.
            self._resolve()

    def _resolve(self):
        # never raises for an unreachable/non-enum PV -- see
        # eco.epics_utils.adjustable.AdjustablePvEnum._resolve()'s docstring
        if self._resolved:
            return
        with self._resolve_lock:
            if self._resolved:
                return
            self._pv.wait_for_connection()
            self._enum_strs = wait_for_enum_strs(self._pv) or ()
            self._pv_enum = IntEnum(
                self.name, {tstr: n for n, tstr in enumerate(self._enum_strs)}
            )
            self._resolved = True

    @property
    def enum_strs(self):
        self._resolve()
        return self._enum_strs

    @property
    def PvEnum(self):
        self._resolve()
        return self._pv_enum

    def _wait_for_initialisation(self):
        # best-effort -- see eco.epics_utils.adjustable.AdjustablePvEnum
        try:
            self._resolve()
        except Exception:
            pass

    def validate(self, value):
        self._resolve()
        if type(value) is str:
            return self._pv_enum.__members__[value]
        else:
            return self._pv_enum(value)

    def get_current_value(self):
        return self.validate(_read_pv(self._pv, name=getattr(self, "name", None)))

    def __call__(self):
        return self.get_current_value()


class DetectorPvString:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.pvname = pvname
        self._pv = ca_tuning.make_pv(pvname, connection_timeout=CA_CONNECTION_TIMEOUT)
        self._elog = elog
        self.alias = Alias(name, channel=self.pvname, channeltype="CA")

    def get_current_value(self):
        return _read_pv(self._pv, name=getattr(self, "name", None))

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


@get_from_archive
class DetectorPvDataStream(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.Id = pvname
        self.pvname = pvname
        self._pv = ca_tuning.make_pv(pvname)
        self.alias = Alias(self.name, channel=self.pvname, channeltype="CA")
        self._append(
            AdjustablePvString, self.pvname + ".EGU", name="unit", is_setting=False
        )
        # self._append(
        #     PvString, self.pvname + ".DESC", name="description", is_setting=False
        # )

    def collect(self, seconds=None, samples=None):
        if (not seconds) and (not samples):
            raise Exception(
                "Either a time interval or number of samples need to be defined."
            )
        try:
            self._pv.callbacks.pop(self._collection["ix_cb"])
        except:
            pass
        self._collection = {"done": False}
        self.data_collected = []
        if seconds:
            self._collection["start_time"] = time()
            self._collection["seconds"] = seconds
            stopcond = (
                lambda: (time() - self._collection["start_time"])
                > self._collection["seconds"]
            )

            def addData(**kw):
                if not stopcond():
                    self.data_collected.append(kw["value"])
                else:
                    self._pv.callbacks.pop(self._collection["ix_cb"])
                    self._collection["done"] = True

        elif samples:
            self._collection["samples"] = samples
            stopcond = lambda: len(self.data_collected) >= self._collection["samples"]

            def addData(**kw):
                self.data_collected.append(kw["value"])
                if stopcond():
                    self._pv.callbacks.pop(self._collection["ix_cb"])
                    self._collection["done"] = True

        self._collection["ix_cb"] = self._pv.add_callback(addData)
        time_wait_start = time()
        while not self._collection["done"]:
            sleep(0.005)
            if seconds:
                if (time() - time_wait_start) > seconds:
                    if len(self.data_collected) == 0:
                        print(
                            f"No {self.name}({self.Id}) data update in time interval, reporting last value"
                        )
                        self._pv.callbacks.pop(self._collection["ix_cb"])
                        self.data_collected.append(self.get_current_value())
                        break

        return self.data_collected

    def acquire(self, hold=False, seconds=None, samples=None, **kwargs):
        return Acquisition(
            acquire=lambda: self.collect(seconds=seconds, samples=samples, **kwargs),
            hold=hold,
            stopper=None,
            get_result=lambda: self.data_collected,
        )

    def accumulate_ring_buffer(self, n_buffer):
        if not hasattr(self, "_accumulate"):
            self._accumulate = {"n_buffer": n_buffer, "ix": 0, "n_cb": -1}
        else:
            self._accumulate["n_buffer"] = n_buffer
            self._accumulate["ix"] = 0
        self._pv.callbacks.pop(self._accumulate["n_cb"], None)
        self._data = np.squeeze(np.zeros([n_buffer * 2, self._pv.count])) * np.nan

        def addData(**kw):
            self._accumulate["ix"] = (self._accumulate["ix"] + 1) % self._accumulate[
                "n_buffer"
            ]
            self._data[self._accumulate["ix"] :: self._accumulate["n_buffer"]] = kw[
                "value"
            ]

        self._accumulate["n_cb"] = self._pv.add_callback(addData)

    def accumulate_start(self):
        if not hasattr(self, "_accumulate_inf"):
            self._accumulate_inf = {"n_cb": -1}
        self._pv.callbacks.pop(self._accumulate_inf["n_cb"], None)
        self._data_inf = []

        def addData(**kw):
            self._data_inf.append(kw["value"])

        self._accumulate_inf["n_cb"] = self._pv.add_callback(addData)

    def accumulate_stop(self):
        self._pv.callbacks.pop(self._accumulate_inf["n_cb"], None)
        return self._data_inf

    def get_data(self):
        return self._data[
            self._accumulate["ix"]
            + 1 : self._accumulate["ix"]
            + 1
            + self._accumulate["n_buffer"]
        ]

    data = property(get_data)

    def get_current_value(self):
        return _read_pv(self._pv, name=getattr(self, "name", None))
