from copy import deepcopy
from eco.elements.adjustable import (
    AdjustableMemory,
    default_representation,
    spec_convenience,
)
from eco.elements.assembly import Assembly
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
        self.status_collection.append(self, selection="settings")
        self.status_collection.append(self, selection="display")

    def get_current_value(self):
        return self._foo_get_current_value(
            *[det.get_current_value() for det in self._detectors]
        )


@call_convenience
@value_property
@default_representation
class DetectorGet:
    def __init__(self, foo_get, cache_get_seconds=None, name=None):
        """ """
        self.alias = Alias(name)
        self.name = name
        self._get = foo_get
        self._cache_get_seconds = cache_get_seconds

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
