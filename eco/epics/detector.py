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
from eco.epics.adjustable import AdjustablePvString, AdjustablePv
from eco.epics import get_from_archive

from eco.acquisition.decorators import scannable


# @call_convenience
# @value_property
@get_from_archive
@scannable
class DetectorPvData(Assembly):
    def __init__(self, pvname, name=None, unit=None, has_unit=False):
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
            self._pv = PV(pvname)
            self.alias = Alias(self.name, channel=self.pvname, channeltype="CA")
            self.status_collection.append(self)
            self.status_collection.append(self, selection="settings", recursive=False)
            self.status_collection.append(self, selection="display", recursive=False)

        self.name = name
        if has_unit:
            self._append(
                AdjustablePvString, self.pvname + ".EGU", name="unit", is_setting=False
            )

    def get_current_value(self):
        if hasattr(self, "_pv"):
            return self._pv.get()
        else:
            return self.readback.get_current_value()

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
@get_from_archive
class DetectorPvEnum(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._pv = PV(pvname, connection_timeout=0.05)
        self.name = name
        self.enum_strs = self._pv.enum_strs

        self.PvEnum = IntEnum(name, {tstr: n for n, tstr in enumerate(self.enum_strs)})
        self.alias = Alias(name, channel=self.pvname, channeltype="CA")

    def validate(self, value):
        if type(value) is str:
            return self.PvEnum.__members__[value]
        else:
            return self.PvEnum(value)

    def get_current_value(self):
        return self.validate(self._pv.get())

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

    def __call__(self):
        return self.get_current_value()

    def _wait_for_initialisation(self):
        self._pv.wait_for_connection()


# @call_convenience
@value_property
class DetectorPvString:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.pvname = pvname
        self._pv = PV(pvname, connection_timeout=0.05)
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


# @call_convenience
# @value_property
@get_from_archive
@scannable
class DetectorPvDataStream(Assembly):
    def __init__(self, pvname, name=None, has_fields=False):
        super().__init__(name=name)
        self.Id = pvname
        self.pvname = pvname
        self._pv = PV(pvname)
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


class CallbackEpics:
    def __init__(
        self,
        pv,
        func="accumulate",
        collector={"timestamps": [], "values": [], "timestamps_ioc": []},
        run_once=True,
        print_output=False,
    ):
        self.pv = pv
        # self.data = collector
        if func == "accumulate":
            func = self.accumulate_values
            self.data = {"timestamps": [], "values": [], "timestamps_ioc": []}
        self.foo = func
        self.run_once = run_once
        self.print = print_output

    def start(self, add_current_value=True):
        if add_current_value:
            ts_local = time()
            self.data["timestamps"].append(ts_local)
            self.data["values"].append(self.pv.get())
            self.data["timestamps_ioc"].append(self.pv.timestamp)
        self.cb_index = self.pv.add_callback(
            self.foo,
            run_once=True,
        )

    def stop(self):
        self.pv.remove_callback(self.cb_index)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def accumulate_values(self, pvname=None, value=None, timestamp=None, **kwargs):
        # if not self.data:
        #     self.data = []
        ts_local = time()
        assert (
            len(self.data["timestamps"])
            == len(self.data["values"])
            == len(self.data["timestamps_ioc"])
        )
        self.data["timestamps"].append(ts_local)
        self.data["values"].append(value)
        self.data["timestamps_ioc"].append(timestamp)

        if self.print:
            print(
                f"{pvname}:  {value};  time_ioc: {timestamp}; time_local: {ts_local}; diff: {ts_local-timestamp}"
            )
