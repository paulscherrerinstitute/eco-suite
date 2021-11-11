from eco.elements.assembly import Assembly
from eco.aliases import Alias


def value_property(Det, value_name="_value"):
    setattr(
        Det,
        value_name,
        property(
            Det.get_current_value,
        ),
    )
    return Det


def call_convenience(Det):
    # spec-inspired convenience methods

    def wm(self, *args, **kwargs):
        return self.get_current_value(*args, **kwargs)

    Det.wm = wm

    def call(self):
        return self.wm()

    Det.__call__ = call

    return Det


@call_convenience
@value_property
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

    def get_current_value(self):
        return self._foo_get_current_value(
            *[det.get_current_value() for det in self._detectors]
        )


@call_convenience
@value_property
class DetectorGet:
    def __init__(self, foo_get, name=None):
        """ """
        self.alias = Alias(name)
        self.name = name
        self._get = foo_get

    def get_current_value(self):
        return self._get()
