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
    `eco.epics_utils.adjustable.AdjustablePvEnum`, `eco.elements.adjustable.AdjustableEnum`).
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
    `eco.epics_utils.detector.DetectorPvEnum`)."""

    enum_strs: Any

    def get_current_value(self):
        ...


def resolve_lazy(obj):
    """The real object behind a lazy namespace `Proxy`, building it if needed.

    `eco.utilities.config.Proxy` is deliberately *shy*: while unresolved it
    reports `LazyComponent` as its `__class__` and `dir(LazyComponent)` as its
    attributes, so introspection (tab-completion, a widget deciding how to
    render a namespace entry) cannot accidentally initialize a device. The
    cost is that `isinstance(proxy, Adjustable)` is False for a *real*
    adjustable that simply hasn't been built yet: a runtime_checkable Protocol
    check reads class-level/static attributes only, which is exactly what the
    proxy hides. Constructor arguments injected by
    `config.replace_NamespaceComponents` are such proxies, so this bites any
    device that type-checks one of its own arguments -- e.g. `Daq.pgroup`,
    which used to hand the unresolved proxy straight to `json.dumps` and fail
    with "Object of type LazyComponent is not JSON serializable".

    Use this (or `is_adjustable`/`is_detector` below) wherever the answer is
    followed by actually *using* the object, so forcing the build costs
    nothing extra. Do NOT use it in introspection/display paths that walk a
    whole namespace -- there the shyness is the point.

    `__resolved__` is a boolean the underlying C proxy exposes without
    triggering the factory; reading `__wrapped__` is what forces the build. A
    non-proxy has neither and is returned unchanged.

    Loops rather than unwrapping once: a `NamespaceComponent`-supplied
    argument is itself `Proxy(component.get)` (built by
    `config.replace_NamespaceComponents`), and `NamespaceComponent.get()`
    returns `namespace.get_obj(...)` -- which, for a still-lazy target, is
    *itself* another unresolved `Proxy` (the namespace's own `lazy_items`
    handle). A single unwrap only reaches that inner proxy, not the real
    object, so `isinstance(..., Adjustable)` stayed False and callers fell
    through to treating it as a plain callable -- e.g. `_append` calling
    `foo_obj_init(*args, **kwargs, name=name)` on what was actually still a
    lazy adjustable proxy, hitting the adjustable's `spec_convenience`
    `__call__` sugar (`call() got an unexpected keyword argument 'name'`)
    instead of ever reaching the intended isinstance branch.
    """
    while True:
        try:
            object.__getattribute__(obj, "__resolved__")
        except AttributeError:
            return obj
        obj = object.__getattribute__(obj, "__wrapped__")


def is_adjustable(obj):
    """`isinstance(obj, Adjustable)`, resolving a lazy proxy first."""
    return isinstance(resolve_lazy(obj), Adjustable)


def is_detector(obj):
    """`isinstance(obj, Detector)`, resolving a lazy proxy first."""
    return isinstance(resolve_lazy(obj), Detector)


# Attached after the class body (not as a method defined inside it) and after
# @runtime_checkable has already run: a runtime_checkable Protocol's
# isinstance check treats every class-body attribute -- methods included,
# regardless of name or of having a real implementation -- as part of the
# required structural interface (`__protocol_attrs__`, cached at decoration
# time). A `resolve_isinstance` method written directly in the class body
# would silently start requiring every real Adjustable/Detector device class
# to also define `resolve_isinstance`, breaking `isinstance(real_device,
# Adjustable)` everywhere. Assigning it here, after that cache is already
# populated, gives call sites a more discoverable spelling
# (`Adjustable.resolve_isinstance(x)`) without that side effect.
Adjustable.resolve_isinstance = staticmethod(is_adjustable)
Detector.resolve_isinstance = staticmethod(is_detector)


def enum_repr(cls):
    """Class decorator: stamps a `__repr__` rendering the enum-enabled
    class's discrete choices as a Num./Sel./Name table, built from nothing
    but the AdjustableEnum/DetectorEnum protocol contract (`enum_strs` +
    `get_current_value()`) -- no dependence on any particular backing
    IntEnum attribute name.

    Replaces what used to be four independently hand-copied, drifting
    `__repr__` implementations: `eco.epics_utils.adjustable.AdjustablePvEnum`,
    `eco.epics_utils.detector.DetectorPvEnum`, `eco.bs.detector.DetectorPvEnum`
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
    