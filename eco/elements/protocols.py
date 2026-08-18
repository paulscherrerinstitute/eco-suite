import enum
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Adjustable(Protocol):
    def get_current_value(self):
        ...

    def set_target_value(self, value):
        ...

    # def set_target_value(self,value) -> Changer:...


@runtime_checkable
class Detector(Protocol):
    def get_current_value(self):
        ...


@runtime_checkable
class AdjustableEnum(Protocol):
    """An Adjustable whose discrete choices are given by `enum_strs` (e.g.
    `eco.epics.adjustable.AdjustablePvEnum`, `eco.elements.adjustable.AdjustableEnum`).
    Structural: any Adjustable that exposes a truthy `enum_strs` satisfies this
    without subclassing it -- see widget layers' `_enum_options()` for the
    consumer (falls back to a plain `enum.Enum` current value for adjustables,
    like vacuum `Valve`, that don't expose `enum_strs` at all)."""

    enum_strs: Any

    def get_current_value(self):
        ...

    def set_target_value(self, value):
        ...


@runtime_checkable
class DetectorEnum(Protocol):
    """Read-only counterpart of `AdjustableEnum` (e.g.
    `eco.epics.detector.DetectorPvEnum`)."""

    enum_strs: Any

    def get_current_value(self):
        ...


def enum_repr(cls):
    """Class decorator: stamps a `__repr__` rendering the enum-enabled
    class's discrete choices as a Num./Sel./Name table, built from nothing
    but the AdjustableEnum/DetectorEnum protocol contract (`enum_strs` +
    `get_current_value()`) -- no dependence on any particular backing
    IntEnum attribute name.

    Replaces what used to be four independently hand-copied, drifting
    `__repr__` implementations: `eco.epics.adjustable.AdjustablePvEnum`,
    `eco.epics.detector.DetectorPvEnum`, `eco.bs.detector.DetectorPvEnum`
    and `eco.elements.adjustable.AdjustableEnum`."""

    def __repr__(self):
        name = self.name or getattr(self, "Id", None)
        cv = self.get_current_value()
        cv_val = cv.value if isinstance(cv, enum.Enum) else cv
        s = f"{name} (enum) at value: {cv}\n"
        s += "{:<5}{:<5}{:<}\n".format("Num.", "Sel.", "Name")
        for val, ename in enumerate(self.enum_strs):
            sel = "x" if val == cv_val else " "
            s += "{:>4}   {}  {}\n".format(val, sel, ename)
        return s

    cls.__repr__ = __repr__
    return cls


@runtime_checkable
class MonitorableValueUpdate(Protocol):
    def set_current_value_callback(self):
        ...


@runtime_checkable
class InitialisationWaitable(Protocol):
    def _wait_for_initialisation(self):
        ...

@runtime_checkable
class Counter(Protocol):
    def acquire(self):
        ...
    def start(self):
        ...
    def stop(self):
        ...

    def __enter__(self):
        self.start()
    
    def __exit__(self, type, value, traceback):
        self.stop()
 
        
        # file_name=fina, Npulses=self.pulses_per_step[0], acq_pars=acq_pars):
                
        

# class Callback:
#     self.__init__(self, func=None, *args, **kwargs):
#         self.func = func
#         self.args = args
#         self.kwargs = kwargs
    
#     def start(self,func=None):
#         if func is not None:
#             self.func = func
        

    
    
#     def 
    