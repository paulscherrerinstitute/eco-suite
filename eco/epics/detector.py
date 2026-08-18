import threading
from enum import IntEnum
from time import time, sleep

import numpy as np
from epics import PV

from eco.acquisition.utilities import Acquisition
from eco.acquisition.decorators import scannable


from eco.aliases import Alias
from eco.elements.adjustable import AdjustableMemory
from eco.elements.assembly import Assembly
from eco.elements.detector import call_convenience, value_property
from eco.elements.protocols import enum_repr
from eco.epics.adjustable import AdjustablePvString, AdjustablePv, wait_for_enum_strs
from eco.epics import adjustable as _adjustable_module
from eco.epics import get_from_archive
from eco.epics.utilities_epics import CallbackEpics

from eco.acquisition.decorators import scannable


# @call_convenience
# @value_property
@get_from_archive
@scannable
class DetectorPvData(Assembly):
    def __init__(self, pvname, name=None, unit=None, has_unit=False, has_unit_pv=False):
        super().__init__(name=name)

        self.pvname = pvname
        singular = (unit is None) and (not has_unit)

        # if name == "aramis_undulator_photon_energy":
        #     print(f"singular is {singular}", unit, has_unit)
        if unit:
            self._append(AdjustableMemory, unit, name="unit")
            has_unit = False
        if not singular:
            self._append(AdjustablePv, pvname, name="readback", is_setting=False)
            # self.status_collection.append(self)
        else:
            self._pv = PV(pvname, auto_monitor=False)
            self.alias = Alias(self.name, channel=self.pvname, channeltype="CA")
            self.status_collection.append(self)
            self.status_collection.append(self, selection="settings", recursive=False)
            self.status_collection.append(self, selection="display", recursive=False)

        self.name = name
        if has_unit:
            self._append(
                AdjustablePvString, self.pvname + ".EGU", name="unit", is_setting=False
            )
        if has_unit_pv:
            self._append(AdjustablePv, has_unit_pv, name="unit", is_setting=False)

    def get_current_value(self):
        if hasattr(self, "_pv"):
            return self._pv.get()
        else:
            return self.readback.get_current_value()

    def get_severity(self):
        """EPICS alarm severity of the underlying PV: 0=NO_ALARM, 1=MINOR,
        2=MAJOR, 3=INVALID -- or None if it couldn't be read."""
        try:
            if hasattr(self, "_pv"):
                self._pv.get()
                return self._pv.severity
            return self.readback.get_severity()
        except Exception:
            return None

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        if hasattr(self, "_pv"):
            return CallbackEpics(
                self._pv,
                func=func,
                run_once=run_once,
                print_output=print_output,
                **kwargs,
            )
        # else:
        #     raise Exception('the object does not have a _pv')

    def __call__(self):
        return self.get_current_value()


# @call_convenience
# @value_property
@enum_repr
@get_from_archive
class DetectorPvEnum(Assembly):
    """Enum-valued PV Detector. Connecting and resolving the enum choice list
    is deferred to first use / an explicit `_wait_for_initialisation()` call
    -- see `AdjustablePvEnum`'s class docstring (`eco.epics.adjustable`) for
    why: it's what lets many sibling enum fields constructed back-to-back
    (e.g. a Valve's several readbacks, an EVR's many pulsers) connect
    concurrently in pyepics's own CA background thread instead of each fully
    blocking before the next is even created."""

    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._pv = PV(pvname, connection_timeout=0.05, auto_monitor=False)
        self.name = name
        self.alias = Alias(name, channel=self.pvname, channeltype="CA")
        self._resolve_lock = threading.Lock()
        self._resolved = False
        self._enum_strs = None
        self._pv_enum = None
        if not _adjustable_module.LAZY_ENUM_RESOLUTION:
            # default: resolve now, like before this speedup existed -- see
            # eco.epics.adjustable.LAZY_ENUM_RESOLUTION's docstring.
            self._resolve()

    def _resolve(self):
        # never raises for an unreachable/non-enum PV -- see AdjustablePvEnum
        # ._resolve()'s docstring for why (enumerate(None) used to crash this
        # after construction, somewhere Assembly._append's safety net no
        # longer applies).
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

    def _wait_for_initialisation(self):
        # best-effort -- see AdjustablePvEnum._wait_for_initialisation
        try:
            self._resolve()
        except Exception:
            pass

    @property
    def enum_strs(self):
        self._resolve()
        return self._enum_strs

    @property
    def PvEnum(self):
        self._resolve()
        return self._pv_enum

    def validate(self, value):
        self._resolve()
        if type(value) is str:
            return self._pv_enum.__members__[value]
        else:
            return self._pv_enum(value)

    def get_current_value(self):
        return self.validate(self._pv.get())

    def __call__(self):
        return self.get_current_value()

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        return CallbackEpics(
            self._pv,
            func=func,
            run_once=run_once,
            print_output=print_output,
            **kwargs,
        )


# @call_convenience
@value_property
class DetectorPvString:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.pvname = pvname
        self._pv = PV(pvname, connection_timeout=0.05, auto_monitor=False)
        self._elog = elog
        self.alias = Alias(name, channel=self.pvname, channeltype="CA")

    def get_current_value(self):
        return self._pv.get()

    def __repr__(self):
        return self.get_current_value()

    def __call__(self, string=None):
        if not string is None:
            self.set_target_value(string)
        else:
            return self.get_current_value()

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        return CallbackEpics(
            self._pv,
            func=func,
            run_once=run_once,
            print_output=print_output,
            **kwargs,
        )


# @call_convenience
# @value_property
@get_from_archive
@scannable
class DetectorPvDataStream(Assembly):
    def __init__(self, pvname, name=None, has_fields=False):
        super().__init__(name=name)
        self.Id = pvname
        self.pvname = pvname
        self._pv = PV(pvname, auto_monitor=False)
        self.alias = Alias(self.name, channel=self.pvname, channeltype="CA")
        if has_fields:
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

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        if hasattr(self, "_pv"):
            return CallbackEpics(
                self._pv,
                func=func,
                run_once=run_once,
                print_output=print_output,
                **kwargs,
            )
        # else:
        #     raise Exception('the object does not have a _pv')

    def get_current_value(self, **kwargs):
        return self._pv.get(**kwargs)
