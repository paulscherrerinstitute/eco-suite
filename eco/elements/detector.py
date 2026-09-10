from copy import deepcopy
from threading import Thread
from eco.acquisition.decorators import scannable
from eco.elements.adjustable import (
    AdjustableMemory,
    CallbackComposedValue,
    default_representation,
    spec_convenience,
)
from eco.elements.assembly import Assembly
from eco.elements.protocols import MonitorableValueUpdate
from eco.aliases import Alias
import time


def value_property(Det, value_name="_value"):
    setattr(
        Det,
        value_name,
        property(
            Det.get_current_value,
        ),
    )
    return Det


def call_convenience(Det, value=None):
    # spec-inspired convenience methods

    def wm(self, *args, **kwargs):
        return self.get_current_value(*args, **kwargs)

    Det.wm = wm

    def call(self, value=value):
        if value is None:
            return self.wm()
        else:
            raise ValueError(f"{self.name} is just a readback, which cannot be set.")

    Det.__call__ = call

    return Det


@call_convenience
@value_property
@default_representation
class DetectorVirtual(Assembly):
    def __init__(
        self,
        detectors,
        foo_get_current_value,
        append_aliases=False,
        name=None,
        unit=None,
    ):
        super().__init__(name=name)
        if append_aliases:
            for det in detectors:
                try:
                    self.alias.append(det.alias)
                except Exception as e:
                    print(f"could not find alias in {det}")
                    print(str(e))
        self._detectors = detectors
        self._foo_get_current_value = foo_get_current_value
        if unit:
            self.unit = AdjustableMemory(unit, name="unit")
        self.status_collection.append(self)
        self.status_collection.append(self, selection="settings", recursive=False)
        self.status_collection.append(self, selection="display", recursive=False)

    def get_current_value(self):
        return self._foo_get_current_value(
            *[det.get_current_value() for det in self._detectors]
        )

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        """Only possible if every underlying detector is itself a
        MonitorableValueUpdate (e.g. a PV-backed Detector, or another
        DetectorVirtual/AdjustableVirtual whose own parents all are) - same
        rule and same mechanism as AdjustableVirtual.set_current_value_
        callback, independent of whether the chain bottoms out in CA or
        something else (a DetectorGet with monitor_frequency=, say): in
        that case the combined value can be kept up to date by recomputing
        get_current_value() whenever any parent reports an update, with no
        polling."""
        non_monitorable = [
            det for det in self._detectors if not isinstance(det, MonitorableValueUpdate)
        ]
        if non_monitorable:
            names = [getattr(det, "name", repr(det)) for det in non_monitorable]
            raise NotImplementedError(
                f"Cannot monitor virtual detector '{self.name}': parent(s) "
                f"{names} do not implement MonitorableValueUpdate "
                f"(set_current_value_callback)."
            )
        return CallbackComposedValue(
            self, self._detectors, func=func, run_once=run_once,
            print_output=print_output, **kwargs
        )


@call_convenience
@value_property
@default_representation
@scannable
class DetectorGet:
    def __init__(
        self, foo_get, cache_get_seconds=None, monitor_frequency=None, name=None
    ):
        """ """
        self.alias = Alias(name)
        self.name = name
        self._get = foo_get
        self._cache_get_seconds = cache_get_seconds
        self._accumulate_frequency = monitor_frequency
        if monitor_frequency:
            self.set_current_value_callback = self._set_current_value_callback

    def get_current_value(self):
        ts = time.time()
        if self._cache_get_seconds and hasattr(self, "_get_cache"):
            if ts - self._get_cache[0] < self._cache_get_seconds:
                value = self._get_cache[1]
            else:
                value = self._get()
        else:
            value = self._get()
        if self._cache_get_seconds:
            self._get_cache = (ts, value)
        return value

    def _set_current_value_callback(self):
        return CallbackTimedelta(
            self, frequency=self._accumulate_frequency, func="accumulate"
        )


@call_convenience
@value_property
class DetectorMemory:
    def __init__(self, value=0, name="detector_memory", return_deep_copy=True):
        self.name = name
        self.alias = Alias(name)
        self.current_value = value
        self._return_deep_copy = return_deep_copy

    def get_current_value(self):
        if self._return_deep_copy:
            return deepcopy(self.current_value)
        else:
            return self.current_value

    def __repr__(self):
        name = self.name
        cv = self.get_current_value()
        s = f"{name} at value: {cv}" + "\n"
        return s


class CallbackTimedelta:
    def __init__(
        self, detector, frequency=10, func="accumulate", collector=None, run_once=True
    ):
        self.detector = detector
        self.frequency = frequency
        # self.data = collector
        if func == "accumulate":
            func = self.accumulate_values
            if collector is None:
                collector = {"timestamps": [], "values": []}
            self.data = (
                collector  # {"timestamps": [], "values": [], "timestamps_ioc": []}
            )
        self.foo = func
        self.run_once = run_once
        self.running = False
        self.thread = None

    def start(self, add_current_value=True):
        if add_current_value:
            ts_local = time.time()
            self.data["timestamps"].append(ts_local)
            self.data["values"].append(self.detector.get_current_value())
        self.running = True
        self.thread = Thread(target=self.accumulate_values)
        self.thread.start()

    def accumulate_values(self, *args, **kwargs):
        while self.running:
            ts_local = time.time()
            self.data["timestamps"].append(ts_local)
            self.data["values"].append(self.detector.get_current_value())
            time.sleep(1 / self.frequency)

    def stop(self):
        self.running = False
        self.thread.join()
