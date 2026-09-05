import threading
import time
from enum import IntEnum

import numpy as np
from epics import PV
from eco.epics_utils.ca_tuning import (
    CA_CONNECTION_TIMEOUT,
    CA_INIT_CONNECTION_TIMEOUT,
    has_ever_succeeded,
    note_successful_read,
    report_none_read,
)

from eco.aliases import Alias
from eco.elements.adjustable import (
    AdjustableMemory,
    tweak_option,
    spec_convenience,
    value_property,
)
from eco.elements.protocols import enum_repr
from . import get_from_archive
from eco.devices_general.utilities import Changer
from ..elements.assembly import Assembly
from .utilities_epics import CallbackEpics


# Opt-in switch for the deferred/lazy enum-resolution speedup in
# AdjustablePvEnum (this module) and DetectorPvEnum (eco.epics_utils.detector,
# eco.bs.detector -- both read this same flag via the module object, not a
# copied `from ... import`, so flipping it here takes effect everywhere).
# Default False: enum resolution happens eagerly in __init__, exactly like
# before this speedup existed, so a construction-time failure is caught by
# Assembly._append(optional=True) as it always was. Found (2026-08-16) to
# cause broader real-namespace init failures than expected when on by
# default -- flip to True only to deliberately study/benchmark the lazy
# path; see eco/epics/adjustable.py AdjustablePvEnum's docstring and
# project_vacuum_and_aramis_vacuum.md memory for the full story.
LAZY_ENUM_RESOLUTION = False




# Both spellings of each auxiliary PV attribute. The classes here store them
# as `_pvreadback` / `_pvlowlim` / `_pvhighlim`, but `_wait_for_initialisation`
# had always asked for `_pv_readback` / `_pv_lowlim` / `_pv_highlim` (extra
# underscore), so every one of those `hasattr` guards was silently False and
# **only the setpoint PV was ever waited for** - never the readback, which is
# the channel `get_current_value()` actually reads. Accepting both spellings
# fixes that without betting on which convention any given class (or an older
# checkout) uses.
_AUX_PV_ATTRS = (
    ("_pvreadback", "_pv_readback"),
    ("_pvlowlim", "_pv_lowlim"),
    ("_pvhighlim", "_pv_highlim"),
)


def _iter_auxiliary_pvs(obj):
    """The readback/limit PVs of `obj`, under either attribute spelling.

    Yields each distinct PV once; missing or None attributes are skipped.
    """
    seen = set()
    for names in _AUX_PV_ATTRS:
        for attr in names:
            pv = getattr(obj, attr, None)
            if pv is not None and id(pv) not in seen:
                seen.add(id(pv))
                yield pv


def _wait_for_pvs(obj, timeout=None):
    """Best-effort "are the channels up yet" over an object's setpoint PV and
    its auxiliary PVs. Never raises: this is a readiness check during
    namespace init, not a guarantee."""
    if timeout is None:
        timeout = CA_INIT_CONNECTION_TIMEOUT
    seen = set()
    for pv in [getattr(obj, "_pv", None), *_iter_auxiliary_pvs(obj)]:
        # dedup across the setpoint too: AdjustablePv points `_pvreadback` at
        # the setpoint PV when no separate readback name was given.
        if pv is None or id(pv) in seen:
            continue
        seen.add(id(pv))
        try:
            pv.wait_for_connection(timeout=timeout)
        except Exception:
            pass


def _read_pv(pv, name=None):
    """`pv.get()` plus the silent-None diagnostics, with one behavioural
    nuance: a channel that has never returned a value in this session is
    read with the old short budget instead of the raised
    CA_CONNECTION_TIMEOUT.

    Without that, the trial timeout would make every read of an absent PV
    cost a full second - and bernina's namespace has plenty of them, read
    on every get_status() fan-out, i.e. once per scan step. The longer
    budget exists for a channel that *was* working and is momentarily
    unreachable (a dropped virtual circuit), which is exactly the case
    `has_ever_succeeded` identifies.
    """
    pvname = getattr(pv, "pvname", None)
    if not pv.connected and not has_ever_succeeded(pvname):
        value = pv.get(timeout=CA_INIT_CONNECTION_TIMEOUT)
    else:
        value = pv.get()
    if value is None:
        report_none_read(pv, name=name)
    else:
        note_successful_read(pvname)
    return value


def wait_for_enum_strs(pv, retries=10, delay=0.05):
    """Wait until `pv` (already connected, or connecting) reports its
    CTRL_ENUM metadata (`enum_strs`), retrying a few times rather than
    trusting a single read.

    A connected channel does not guarantee `enum_strs` is populated yet on
    this control system -- observed historically as "gateway slowness" (see
    the retry hack this replaces, previously commented out in
    `AdjustablePvEnum`) where a channel connects but its enum metadata lags
    behind by a beat. An unconditional single read of `enum_strs` right after
    connecting can then get `None`, which fails downstream (`enumerate(None)`)
    with a confusing `TypeError` instead of a clear message -- since that read
    happens inside `Assembly._append(optional=True)`, the failure is silently
    swallowed there rather than raised, so a device can end up with a quietly
    missing component with no obvious cause. Bounded (default: at most 0.5 s
    extra, only when actually needed) so a PV that simply isn't an enum record
    at all doesn't hang -- it just returns `None`/empty as before, so callers
    still get the same clear failure they would have gotten anyway.
    """
    for _ in range(retries):
        if pv.enum_strs:
            return pv.enum_strs
        time.sleep(delay)
    return pv.enum_strs


def read_pv_value(adjustable, timeout=3.0, attempts=3, description=None):
    """Read `adjustable`'s current value, treating a timed-out get as a
    failure instead of as the value `None`.

    Why this is needed at all: pyepics' ``PV.get()`` returns ``None`` when
    ``wait_for_connection()`` fails - it never raises (see
    ``epics/pv.py``'s ``get_with_metadata``). eco's ``AdjustablePv`` builds
    every PV with ``connection_timeout=CA_CONNECTION_TIMEOUT`` and its readback with
    ``auto_monitor=False``, so a read issued shortly after construction has
    a 50 ms budget on a brand-new channel and no monitor cache to fall back
    on. During a concurrent ``Namespace.init_all()`` pass - thousands of
    channels connecting at once - that budget is regularly missed, and the
    ``None`` then flows on as if it were data: an event code of ``None``, a
    pulser number of ``None``. The resulting failure surfaces far from its
    cause, or does not surface at all (see ``EvrPulser``/``EvrOutput``).

    This is the same failure shape as `wait_for_enum_strs` above (a value
    that is simply not there yet under load), and the same shape as the
    ``Daq.get_pulse_id()`` fix - both boil down to "never let a timed-out
    CA get become a value".

    Uses the underlying PV directly so a real timeout can be given, rather
    than inheriting the 50 ms connection timeout. Raises ``TimeoutError``
    naming the PV once the attempts are used up, so the caller fails with
    something readable instead of an ``AttributeError`` three frames later.
    """
    pv = getattr(adjustable, "_pvreadback", None)
    if pv is None:
        pv = getattr(adjustable, "_pv", None)
    what = description or getattr(adjustable, "name", None) or repr(adjustable)
    pvname = getattr(pv, "pvname", None) or getattr(adjustable, "pvname", what)
    for _ in range(max(int(attempts), 1)):
        if pv is not None:
            pv.wait_for_connection(timeout=timeout)
            value = pv.get(timeout=timeout)
        else:
            value = adjustable.get_current_value()
        if value is not None:
            return value
    raise TimeoutError(
        f"could not read '{what}' ({pvname}) after {attempts} attempts of "
        f"{timeout}s each - the channel did not connect or did not return a "
        f"value. pyepics reports this as None rather than raising, so it is "
        f"raised here to keep it from being used as if it were data."
    )


# Work in progress! TODO
@spec_convenience
@get_from_archive
@tweak_option
@value_property
class AdjustableAtomicPv:
    def __init__(
        self,
        pvsetname,
    ):
        #        alias_fields={"setpv": pvsetname, "readback": pvreadbackname},
        #    ):
        self.pvname = pvsetname
        self.name = name
        #        for an, af in alias_fields.items():
        #            self.alias.append(
        #                Alias(an, channel=".".join([pvname, af]), channeltype="CA")
        #            )

        self._pv = PV(self.pvname, connection_timeout=CA_CONNECTION_TIMEOUT, count=element_count, auto_monitor=False)
        self._currentChange = None
        self.accuracy = accuracy

        if pvreadbackname is None:
            self._pvreadback = PV(
                self.pvname, count=element_count, connection_timeout=CA_CONNECTION_TIMEOUT, auto_monitor=False
            )
            pvreadbackname = self.pvname
            self.pvname = self.pvname
        else:
            self._pvreadback = PV(
                pvreadbackname, count=element_count, connection_timeout=CA_CONNECTION_TIMEOUT, auto_monitor=False
            )
            self.pvname = pvreadbackname

        if pvlowlimname:
            self._pvlowlim = PV(
                pvlowlimname, count=element_count, connection_timeout=CA_CONNECTION_TIMEOUT, auto_monitor=False
            )
        else:
            self._pvlowlim = None
        if pvhighlimname:
            self._pvhighlim = PV(
                pvhighlimname, count=element_count, connection_timeout=CA_CONNECTION_TIMEOUT, auto_monitor=False
            )
        else:
            self._pvhighlim = None
        self.alias = Alias(name, channel=pvreadbackname, channeltype="CA")

    def _wait_for_initialisation(self):
        # Explicit short budget rather than the (now much longer)
        # CA_CONNECTION_TIMEOUT these PVs were built with: this is a
        # best-effort "is it there yet" during namespace init, and waiting a
        # full second per absent device would multiply init time by the
        # number of them. See eco.epics_utils.ca_tuning.
        _wait_for_pvs(self)

    def get_current_value(self, readback=True):
        pv = self._pvreadback if readback else self._pv
        return _read_pv(pv, name=self.name)

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
        if self._pvlowlim:
            if value < self._pvlowlim.get():
                raise Exception(
                    f"Target value of {self.name} is smaller than limit value!"
                )
        if self._pvhighlim:
            if self._pvhighlim.get() < value:
                raise Exception(
                    f"Target value of {self.name} is higher than limit value!"
                )

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


@spec_convenience
@get_from_archive
@tweak_option
@value_property
class AdjustablePv:
    def __init__(
        self,
        pvsetname,
        pvreadbackname=None,
        pvlowlimname=None,
        pvhighlimname=None,
        accuracy=None,
        name=None,
        elog=None,
        element_count=None,
        unit=None,
    ):
        #        alias_fields={"setpv": pvsetname, "readback": pvreadbackname},
        #    ):
        self.Id = pvsetname
        self.name = name
        #        for an, af in alias_fields.items():
        #            self.alias.append(
        #                Alias(an, channel=".".join([pvname, af]), channeltype="CA")
        #            )

        self._pv = PV(self.Id, connection_timeout=CA_CONNECTION_TIMEOUT, count=element_count)
        self._currentChange = None
        self.accuracy = accuracy
        if unit:
            self.unit = AdjustableMemory(unit, name="unit")

        if pvreadbackname is None:
            self._pvreadback = PV(self.Id, count=element_count, connection_timeout=CA_CONNECTION_TIMEOUT, auto_monitor=False)
            pvreadbackname = self.Id
            self.pvname = self.Id
        else:
            self._pvreadback = PV(
                pvreadbackname, count=element_count, connection_timeout=CA_CONNECTION_TIMEOUT, auto_monitor=False
            )
            self.pvname = pvreadbackname

        if pvlowlimname:
            self._pvlowlim = PV(
                pvlowlimname, count=element_count, connection_timeout=CA_CONNECTION_TIMEOUT, auto_monitor=False
            )
        else:
            self._pvlowlim = None
        if pvhighlimname:
            self._pvhighlim = PV(
                pvhighlimname, count=element_count, connection_timeout=CA_CONNECTION_TIMEOUT, auto_monitor=False
            )
        else:
            self._pvhighlim = None
        self.alias = Alias(name, channel=pvreadbackname, channeltype="CA")

    def _wait_for_initialisation(self):
        # Explicit short budget rather than the (now much longer)
        # CA_CONNECTION_TIMEOUT these PVs were built with: this is a
        # best-effort "is it there yet" during namespace init, and waiting a
        # full second per absent device would multiply init time by the
        # number of them. See eco.epics_utils.ca_tuning.
        _wait_for_pvs(self)

    def get_current_value(self, readback=True):
        pv = self._pvreadback if readback else self._pv
        return _read_pv(pv, name=self.name)

    def get_severity(self):
        """EPICS alarm severity of the last readback `.get()`: 0=NO_ALARM,
        1=MINOR, 2=MAJOR, 3=INVALID -- or None if it couldn't be read. A plain
        `.get()` is enough to populate it (pyepics requests it as part of the
        normal get), no special ctrlvars call needed."""
        try:
            self._pvreadback.get()
            return self._pvreadback.severity
        except Exception:
            return None

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
        if self._pvlowlim:
            if value < self._pvlowlim.get():
                raise Exception(
                    f"Target value of {self.name} is smaller than limit value!"
                )
        if self._pvhighlim:
            if self._pvhighlim.get() < value:
                raise Exception(
                    f"Target value of {self.name} is higher than limit value!"
                )

        self._pv.put(value)
        if self.accuracy is not None:
            while (
                np.abs(self.get_current_value(readback=False) - value) > self.accuracy
            ):
                time.sleep(0.01)

        while self.get_change_done() == 0:
            time.sleep(0.01)

    def set_target_value(self, value, hold=False):
        """Adjustable convention"""

        changer = lambda value: self.change(value)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

    def get_limits(self):
        return (self._pvlowlim.get(), self._pvhighlim.get())

    def set_limits(self, lowlim, highlim):
        return (self._pvlowlim.put(lowlim), self._pvhighlim.put(highlim))

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

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        """Monitor the readback PV (same channel get_current_value() reads
        by default)."""
        return CallbackEpics(
            self._pvreadback,
            func=func,
            run_once=run_once,
            print_output=print_output,
            **kwargs,
        )


@enum_repr
@spec_convenience
@get_from_archive
@value_property
class AdjustablePvEnum:
    """Enum-valued PV Adjustable.

    Connecting and resolving the enum choice list (`enum_strs`/`PvEnum`) is
    deferred from `__init__` to first real use (or an explicit
    `_wait_for_initialisation()` call) -- see `_resolve()`. This is the
    difference between initializing a component with many sibling
    `AdjustablePvEnum`/`DetectorPvEnum` fields (e.g. `EvrPulser`, with several
    enum fields per pulser, times dozens of pulsers per EVR) taking seconds
    (each one fully connects+queries before the next is even created) versus
    a small fraction of that (all of them start connecting -- pyepics/CA
    connects in its own background thread regardless of Python -- during the
    now-trivially-fast `__init__` calls, so by the time anything actually
    *waits* on one, most are already connected). Measured on real EVR PVs:
    ~30x for construction-then-batch-wait with no threading at all, vs. the
    old eager-in-__init__ pattern; seconds, not hundreds of milliseconds, for
    a busy EVR. See `Assembly._wait_for_initialisation` for the (optional,
    also-parallel) batch-wait side of this.
    """

    def __init__(self, pvname, pvname_set=None, name=None):
        self.Id = pvname
        self.pvname = pvname
        self._pv = PV(pvname, connection_timeout=CA_CONNECTION_TIMEOUT * 2, auto_monitor=False)
        self.name = name
        self._pv_set = PV(pvname_set, connection_timeout=CA_CONNECTION_TIMEOUT * 2) if pvname_set else None
        self.alias = Alias(name, channel=self.Id, channeltype="CA")
        self._resolve_lock = threading.Lock()
        self._resolved = False
        self._enum_strs = None
        self._pv_enum = None
        self._get2set = None
        if not LAZY_ENUM_RESOLUTION:
            # default: resolve now, like before this speedup existed, so a
            # bad PV fails construction here and is caught by
            # Assembly._append(optional=True) as always -- see
            # LAZY_ENUM_RESOLUTION's module-level docstring.
            self._resolve()

    def _resolve(self):
        """Connect and read enum metadata, once (idempotent, thread-safe --
        see class docstring for why this is deferred out of `__init__`).

        Never raises for an unreachable/non-enum PV: `enum_strs` (and
        `pvname_set`'s, if any) fall back to `()` rather than the `None`
        `wait_for_enum_strs` returns on failure, so `self._pv_enum` still
        ends up a real (zero-member) `IntEnum` instead of crashing on
        `enumerate(None)`. That matters specifically because -- unlike the
        old eager-in-`__init__` version, whose *construction*-time failure
        was caught by `Assembly._append(optional=True)` and turned into a
        contained `FailedComponent` -- this now runs *after* construction
        (lazily, or via `_wait_for_initialisation()`), somewhere `_append`'s
        safety net no longer applies. A disconnected PV should still fail
        (clearly, e.g. `KeyError`/`ValueError` out of `validate()`) the
        moment someone genuinely tries to read/set a value through it -- just
        not out of merely constructing or waiting on it.
        """
        if self._resolved:
            return
        with self._resolve_lock:
            if self._resolved:  # lost the race to another thread; already done
                return
            self._pv.wait_for_connection()
            enum_strs = wait_for_enum_strs(self._pv) or ()

            get2set = None
            if self._pv_set is not None:
                # must wait before reading enum_strs -- without it this races
                # the PV's (async) connection and can read None/stale strings,
                # depending on how fast the IOC/gateway responds.
                self._pv_set.wait_for_connection()
                tstrs = wait_for_enum_strs(self._pv_set) or ()
                # Combine readback + setter enum strings into one enum,
                # instead of requiring the setter's strings to be a strict
                # subset of the readback's: controllers commonly show more
                # states on readback than they accept as a command (e.g.
                # "opening"/"closing" transients), but occasionally a setter
                # also accepts a state the readback never actually settles on
                # -- that used to be a hard construction failure; now it just
                # extends the enum. Readback strings keep their original (raw
                # PV value) order and index, so get_current_value()'s raw int
                # from the readback PV is unaffected; any setter-only strings
                # are appended after.
                enum_strs = tuple(enum_strs) + tuple(
                    tstr for tstr in tstrs if tstr not in enum_strs
                )
                get2set = {}
                for nset, tstr in enumerate(tstrs):
                    get2set[enum_strs.index(tstr)] = nset

            enumname = self.name if self.name else self.Id
            self._enum_strs = enum_strs
            self._pv_enum = IntEnum(enumname, {tstr: n for n, tstr in enumerate(enum_strs)})
            self._get2set = get2set
            self._resolved = True

    def _wait_for_initialisation(self):
        # best-effort, like PV.wait_for_connection() itself: a component that
        # can't be resolved (disconnected/non-enum PV) must not turn into a
        # hard failure of the *entire* containing assembly just because
        # someone did a "is everything ready" pass over it -- see _resolve()
        # and this class's docstring. Genuine use (get_current_value/
        # validate/set_target_value) still raises clearly when it can't
        # complete; only this readiness *check* stays silent on failure.
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

    @property
    def get2set(self):
        self._resolve()
        return self._get2set

    def validate(self, value):
        self._resolve()
        if type(value) is str:
            return self._pv_enum.__members__[value]
        else:
            return self._pv_enum(value)

    def get_current_value(self):
        return self.validate(_read_pv(self._pv, name=self.name))

    def set_target_value(self, value, hold=False):
        """Adjustable convention"""
        value = self.validate(value)
        if self._pv_set:
            tpv = self._pv_set
            if self._get2set is not None:
                value = self._get2set[value]
        else:
            tpv = self._pv
        changer = lambda value: tpv.put(value, wait=True)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

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


class AdjustablePvString:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.pvname = pvname
        self._pv = PV(pvname, connection_timeout=CA_CONNECTION_TIMEOUT, auto_monitor=False)
        self._elog = elog
        self.alias = Alias(name, channel=self.pvname, channeltype="CA")

    def get_current_value(self):
        return _read_pv(self._pv, name=self.name)

    def set_target_value(self, value, hold=False):
        changer = lambda value: self._pv.put(bytes(value, "utf8"), wait=True)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

    def __repr__(self):
        return str(self.get_current_value())

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
