from eco.elements.adjustable import AdjustableMemory, default_representation
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
        self.settings_collection.append(self, force=True)
        self.status_collection.append(self, force=True)
        self.display_collection.append(self, force=True)

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
