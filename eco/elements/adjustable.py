import datetime
import json
import logging
import sys
import time
from json import load, dump
from pathlib import Path
from threading import Thread
import itertools
import colorama
import numpy as np

import eco
from eco.acquisition.scan_data import run_status_convenience
from eco.aliases import Alias
from eco.devices_general.utilities import Changer
from eco.elements.protocols import MonitorableValueUpdate, enum_repr


from eco.elements import memory
from eco.utilities.keypress import KeyPress
from eco.utilities.datafiles import ensure_dir, open_group_writable

# from .assembly import Assembly
from copy import deepcopy
from enum import IntEnum
from eco.aliases import Alias

from functools import partial
import cachebox

# for python 3.8
# from typing import Protocol
# runtime_checkable

logger = logging.getLogger(__name__)


# @runtime_checkable
# class Adjustable(Protocol):
#     def set_target_value(self):
#         ...

#     def get_current_value(self):
#         ...


class AdjustableError(Exception):
    pass


# >>> wrapper decorators >>>


def tweak_option(Obj):
    def tweak(self, interval, *args, detectors=None, **kwargs):
        """Interactive tweak; `detectors` (a Detector or a list) are recorded
        and plotted along the tweak, see Tweak."""
        self._tweak_instance = Tweak((self, interval), detectors=detectors)
        self._tweak_instance._recorder_label = f"{self.name}._tweak_instance.recorder"
        self._tweak_instance.tweak()

    def _widget_tweak(self, interval=1.0, backend=None, display=False, detectors=None):
        """Tweak panel (Qt or ipywidgets) with keypress control, see
        eco.widgets.tweak_panel."""
        self._tweak_instance = Tweak((self, interval), detectors=detectors)
        return self._tweak_instance.widget(backend=backend, display=display)

    Obj.tweak = tweak
    Obj._widget_tweak = _widget_tweak
    return Obj


def jog_option(Obj):
    """Class decorator giving an Adjustable a hold-to-move ``jog()``.

    The jog analogue of :func:`tweak_option`: where ``tweak_option`` adds an
    interactive ``.tweak()``, ``jog_option`` adds ``jog(direction,
    start=True)`` and ``jog_stop()`` so the adjustable can be driven
    continuously ("hold a button/joystick to move") - see
    ``eco.manual_control`` - without any external wrapper.

    - The signature matches the native ``MotorRecord.jog`` so a decorated
      adjustable is picked up as "natively joggable" by
      ``eco.manual_control.Jogger`` / ``ManualControlBox`` (which duck-type
      on the presence of a callable ``jog``), and its software jog is used
      instead of the Jogger's own generic fallback.
    - If ``Obj`` already defines ``jog`` (e.g. ``MotorRecord``, which jogs
      via the motor record's JOGF/JOGR fields on the IOC), that hardware
      implementation is left untouched - the decorator never shadows a real
      jog with the software one - and only ``jog_stop`` is supplied if it is
      missing.
    - Otherwise it installs a software jog: a background thread that nudges
      ``set_target_value()`` by ``jog_step_size`` (class default 1.0,
      overridable per instance or per call) and accelerates the longer it is
      held, reusing the ``_HeldAction`` mechanism from ``tweak_action``.
    """
    if callable(getattr(Obj, "jog", None)):
        # Native jog (e.g. MotorRecord); only fill in jog_stop if absent.
        if not callable(getattr(Obj, "jog_stop", None)):

            def jog_stop(self):
                self.jog(1, start=False)
                self.jog(-1, start=False)

            Obj.jog_stop = jog_stop
        return Obj

    def jog(self, direction, start=True, step_size=None):
        """Continuously jog in `direction` (+1/-1) while held: `start=True`
        begins, `jog(..., start=False)` or `jog_stop()` ends it. Unlike
        set_target_value this is unbounded - it keeps stepping until stopped
        (or a limit/error). `step_size` overrides `self.jog_step_size` for
        this jog only."""
        if not start:
            self.jog_stop()
            return
        if self.__dict__.get("_jog_action") is not None:
            return  # already jogging; jog_stop() first
        direction = 1 if direction > 0 else -1
        step = self.jog_step_size if step_size is None else step_size

        def _step():
            self.set_target_value(self.get_current_value() + direction * step)

        from eco.manual_control.tweak_action import _HeldAction

        action = _HeldAction(_step, interval=0.12, growth=0.85, min_interval=0.03)
        self.__dict__["_jog_action"] = action
        action.start()

    def jog_stop(self):
        action = self.__dict__.pop("_jog_action", None)
        if action is not None:
            action.stop()

    Obj.jog = jog
    Obj.jog_stop = jog_stop
    if not hasattr(Obj, "jog_step_size"):
        Obj.jog_step_size = 1.0
    return Obj


def spec_convenience(Adj):
    # spec-inspired convenience methods

    def wm(self, *args, **kwargs):
        return self.get_current_value(*args, **kwargs)

    Adj.wm = wm
    if hasattr(Adj, "update_change"):

        def umv(self, *args, **kwargs):
            self.update_change(*args, **kwargs)

        def umvr(self, *args, **kwargs):
            self.update_change_relative(*args, **kwargs)

        Adj.mv = umv
        Adj.mvr = umvr
        Adj.umv = umv
        Adj.umvr = umvr
    else:

        def mv(self, value, check_limits=True):
            try:
                self._currentChange = self.set_target_value(value)
                self._currentChange.wait()
            except (KeyboardInterrupt, SystemExit):
                self._currentChange.stop()
            return self._currentChange

        def mvr(self, value, *args, **kwargs):
            if (
                hasattr(self, "_currentChange")
                and self._currentChange
                and not (self._currentChange.status() == "done")
            ):
                startvalue = self._currentChange.target
            elif hasattr(self, "get_moveDone") and (self.get_change_done == 1):
                startvalue = self.get_current_value(readback=True, *args, **kwargs)
            else:
                startvalue = self.get_current_value(*args, **kwargs)
            try:
                self._currentChange = self.set_target_value(
                    value + startvalue, *args, **kwargs
                )
                print("spec conven")
                self._currentChange.wait()

            except (KeyboardInterrupt, SystemExit):
                self._currentChange.stop()

            return self._currentChange

        Adj.mv = mv
        Adj.mvr = mvr

    def wm_elog(self, premessage=None, tags=[]):
        elog = self._get_elog()
        tname = self.alias.get_full_name()
        value = self.get_current_value()

        if premessage:
            messages = [
                premessage,
                f"{tname} is at {value}.",
            ]
        else:
            messages = [f"{tname} is at {value}."]
        elog.post(*messages, tags=tags)

    def mv_elog(self, value, premessage=None, tags=[]):
        elog = self._get_elog()
        tname = self.alias.get_full_name()
        start = self.get_current_value()
        end = value
        rel_str = ""
        try:
            rel_change = end - start
            rel_str = f"by {rel_change} "
        except:
            pass

        if premessage:
            messages = [
                premessage,
                f"Changing {tname} from {start} {rel_str} to {end}.",
            ]
        else:
            messages = [f"Changing {tname} from {start} {rel_str} to {end}."]
        self.mv(value)
        elog.post(*messages, tags=tags)

    def mvr_elog(self, value, premessage=None, tags=[]):
        elog = self._get_elog()
        tname = self.alias.get_full_name()
        start = self.get_current_value()
        end = start + value
        rel_change = value
        if premessage:
            messages = [
                premessage,
                f"Changing {tname} from {start} by {rel_change} to {end}.",
            ]
        else:
            messages = [f"Changing {tname} from {start} by {rel_change} to {end}."]
        self.mvr(value)
        elog.post(*messages, tags=tags)

    if hasattr(Adj, "wm"):
        Adj.wm_elog = wm_elog
    if hasattr(Adj, "mv"):
        Adj.mv_elog = mv_elog
    if hasattr(Adj, "mvr"):
        Adj.mvr_elog = mvr_elog

    def call(self, value=None):
        if not value is None:
            return self.mv(value)
        else:
            return self.wm()

    Adj.__call__ = call

    def _get_elog(self):
        if hasattr(self, "_elog") and self._elog:
            return self._elog
        elif hasattr(self, "__elog") and self.__elog:
            return self.__elog
        elif eco.defaults.ELOG:
            return eco.defaults.ELOG
        else:
            return None

    Adj._get_elog = _get_elog

    return Adj


class ValueInRange:
    def __init__(self, start_value, end_value, bar_width=30, unit="", fmt="1.5g"):
        self.start_value = start_value
        self.end_value = end_value
        self.unit = unit
        self.bar_width = bar_width
        self._blocks = " ▏▎▍▌▋▊▉█"
        self._fmt = fmt

    def get_str(self, value):
        if self.start_value == self.end_value:
            frac = 1
        else:
            frac = (value - self.start_value) / (self.end_value - self.start_value)
        return (
            f"{self.start_value:{self._fmt}}"
            + self.get_unit_str()
            + "|"
            + self.bar_str(frac)
            + "|"
            + f"{self.end_value:{self._fmt}}"
            + self.get_unit_str()
        )

    def get_unit_str(self):
        if not self.unit:
            return ""
        else:
            return " " + self.unit

    def bar_str(self, frac):
        blocks = self._blocks
        if 0 < frac and frac <= 1:
            whole = int(self.bar_width // (1 / frac))
            part = int((frac * self.bar_width - whole) // (1 / (len(blocks) - 1)))
            return (
                colorama.Fore.GREEN
                + whole * blocks[-1]
                + blocks[part]
                + (self.bar_width - whole - 1) * blocks[0]
                + colorama.Fore.RESET
            )
        elif frac == 0:
            return self.bar_width * blocks[0]
        elif frac < 0:
            return colorama.Fore.RED + "<" * self.bar_width + colorama.Fore.RESET
        elif frac > 1:
            return colorama.Fore.RED + ">" * self.bar_width + colorama.Fore.RESET


def update_changes(Adj):
    def get_position_str(start, end, value):
        vals = [v if hasattr(v, "__iter__") else [v] for v in [start, end, value]]
        #        bars = []
        bars = ""
        for s, v, e in zip(*vals):
            s = float(s)
            v = float(v)
            e = float(e)
            s = ValueInRange(s, e, bar_width=30, unit="", fmt="1.5g").get_str(v)
            bars = bars + (
                colorama.Style.BRIGHT
                + f"{v:1.5}".rjust(10)
                + colorama.Style.RESET_ALL
                + "  "
                + s
                + 2 * "\t"
            )
        #            bars.append((
        #                colorama.Style.BRIGHT
        #                + f"{v:1.5}".rjust(10)
        #                + colorama.Style.RESET_ALL
        #                + "  "
        #                + s
        #                + 2 * "\t"
        #            ))
        return bars

    def update_change(self, value, elog=None):
        start = self.get_current_value()
        try:
            print(
                f"Changing {self.name} from {start:1.5g} by {value-start:1.5g} to {value:1.5g}"
            )
        except TypeError:
            print(f"Changing {self.name} from {start} to {value}")
        # for pos in get_position_str(start, value, start):
        #    print(pos, end="\r")
        print(get_position_str(start, value, start), end="\r")
        try:
            if hasattr(self, "add_value_callback"):

                def cbfoo(**kwargs):
                    present_value = self.get_current_value()
                    # for pos in get_position_str(start, value, present_value):
                    #    print(pos, end="\r")
                    print(get_position_str(start, value, present_value), end="\r")

                cb_id = self.add_value_callback(cbfoo)
            self._currentChange = self.set_target_value(value)
            self._currentChange.wait()
        except (KeyboardInterrupt, SystemExit):
            self._currentChange.stop()
            print(f"\nAborted change at (~) {self.get_current_value():1.5g}")
        finally:
            if hasattr(self, "add_value_callback"):
                self.clear_value_callback(cb_id)
        if elog:
            if not hasattr(self, "elog"):
                raise Exception("No elog defined!")

            elog_str = f"Changing {self.name} from {start:1.5g} by {value-start:1.5g} to {value:1.5g}"
            elog_title = f"Adjusting {self.name}"
            if type(elog) is str:
                elog = {"message": elog}
            elif type(elog) is dict:
                pass
            else:
                elog = {}
            self.elog.post(**elog)

        return self._currentChange

    def update_change_relative(self, value, *args, **kwargs):
        if (
            hasattr(self, "_currentChange")
            and self._currentChange
            and not (self._currentChange.status() == "done")
        ):
            startvalue = self._currentChange.target
        elif hasattr(self, "get_moveDone") and (self.get_moveDone == 1):
            startvalue = self.get_current_value(readback=True, *args, **kwargs)
        else:
            startvalue = self.get_current_value(*args, **kwargs)
        self._currentChange = self.update_change(value + startvalue, *args, **kwargs)
        return self._currentChange

    Adj.update_change = update_change
    Adj.update_change_relative = update_change_relative

    return Adj


def value_property(Adj, wait_for_change=True, value_name="_value"):
    if wait_for_change:

        def set_target_value_wait(self, value):
            try:
                self.set_target_value(value, hold=False).wait()
            except:
                self.set_target_value(value).wait()

        def get_current_value(self):
            o = self.get_current_value()
            if hasattr(o, "__setitem__"):
                # print("overwriting output class")

                class TempObj(o.__class__):
                    def __setitem__(oself, *args):
                        o.__class__.__setitem__(oself, *args)
                        self._set_target_value_wait(oself)

                return TempObj(o)
            else:
                return o

        Adj._set_target_value_wait = set_target_value_wait
        Adj._get_current_value = get_current_value

        setattr(
            Adj,
            value_name,
            property(
                Adj._get_current_value,
                Adj._set_target_value_wait,
            ),
        )
    return Adj


# <<< wrapper decorators <<<


@spec_convenience
@tweak_option
@value_property
class DummyAdjustable:
    def __init__(self, name="no_adjustable", limits=[-100, 100]):
        self.name = name
        self.alias = Alias(name)

        self.current_value = 0
        self.limits = tuple(limits)

    def get_current_value(self):
        return self.current_value

    def set_target_value(self, value, hold=False):
        def changer(value):
            self.current_value = value

        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

    def get_limits(self):
        return self.limits

    def set_limits(self, lowlim, highlim):
        self.limits = (lowlim, highlim)

    def __repr__(self):
        name = self.name
        cv = self.get_current_value()
        s = f"{name} at value: {cv}" + "\n"
        return s


def _keywordChecker(kw_key_list_tups):
    for tkw, tkey, tlist in kw_key_list_tups:
        assert tkey in tlist, "Keyword %s should be one of %s" % (tkw, tlist)


def _is_notebook():
    try:
        from IPython import get_ipython

        ip = get_ipython()
        return ip is not None and ip.__class__.__name__ == "ZMQInteractiveShell"
    except Exception:
        return False


def valueprop(Obj):
    @property
    def value(self):
        return self.get_current_value()

    @value.setter
    def value(self, val):
        self.set_target_value(val).wait()

    Obj.value = value
    return Obj


@spec_convenience
@update_changes
@tweak_option
@value_property
@valueprop
class AdjustableMemory:
    def __init__(self, value=0, name="adjustable_memory", return_deep_copy=True):
        self.name = name
        self.alias = Alias(name)
        self.current_value = value
        self._return_deep_copy = return_deep_copy
        if memory.global_memory_dir:
            self.memory = memory.Memory(self)

    def get_current_value(self):
        if self._return_deep_copy:
            return deepcopy(self.current_value)
        else:
            return self.current_value

    def set_target_value(self, value, hold=False):
        def changer(value):
            self.current_value = value

        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

    def __repr__(self):
        name = self.name
        cv = self.get_current_value()
        s = f"{name} at value: {cv}" + "\n"
        return s


def default_representation(Obj):
    def get_name(Obj):
        if hasattr(Obj, "alias") and Obj.alias:
            return Obj.alias.get_full_name()
        elif Obj.name:
            return Obj.name
        elif hasattr(Obj, "Id") and Obj.Id:
            return Obj.Id
        else:
            return ""

    def get_repr(Obj):
        s = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S") + ": "
        s += f"{colorama.Style.BRIGHT}{Obj._get_name()}{colorama.Style.RESET_ALL} at {colorama.Style.BRIGHT}{str(Obj.get_current_value())}{colorama.Style.RESET_ALL}"
        return s

    Obj._get_name = get_name
    Obj.__repr__ = get_repr
    return Obj


ADJUSTABLEFS_MAX_READ_PERIOD = 0.2


@default_representation
@spec_convenience
@value_property
class AdjustableFS:
    def __init__(
        self,
        file_path,
        name=None,
        default_value=None,
        max_read_period=0.2,
        group_writable=True,
    ):
        # group_writable=False: for state that is genuinely private to one
        # account (e.g. eco.elements.recent.RecentComponents,
        # eco.widgets.component_selector.ComponentBookmarks -- both default
        # to a path under Path.home()), skip the shared-tree group-writable
        # dance entirely rather than have it fail and warn every time. That
        # machinery targets shared results/config trees
        # (`/sf/<instrument>/data/.../res`, `eco_cnf_bernina/...`); applied
        # to a personal ~/.eco/ cache it can only ever chmod a file nobody
        # else is a legitimate writer of, and typically can't even do that
        # (a 0700 home directory keeps other accounts out regardless).
        self.file_path = Path(file_path)
        self._group_writable = group_writable
        if not self.file_path.exists():
            if not self.file_path.parent.exists():
                if group_writable:
                    ensure_dir(self.file_path.parent)
                else:
                    self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_value(default_value)
        self.alias = Alias(name)
        self.max_read_period = max_read_period
        # if memory.global_memory_dir:
        #     self.memory = memory.Memory(self)
        self.name = name

    def get_current_value(self):
        return self._read_value()

    # @cache_file_access
    @cachebox.cached(cachebox.TTLCache(0, ADJUSTABLEFS_MAX_READ_PERIOD))
    def _read_value(self):
        with open(self.file_path, "r") as f:
            res = load(f)
        return res["value"]

    def _cache_file_access(self, foo):
        @cachebox.cached(cachebox.TTLCache(0, self.max_read_period))
        def wrapper(*args, **kwargs):
            return foo(*args, **kwargs)

        return wrapper

    def _write_value(self, value):
        """Rewrite the backing file, leaving it group-writable.

        `open_group_writable` rather than `open`: `open(path, "w")` needs write
        permission on the *file*, and the writer becomes its owner. On a
        shared beamline path that means the first account to write a value
        locks every other account out of it -- the failure mode documented for
        `bernina_robot/adjustables_fs`, where a write from a personal account
        took ownership of the robot server's state files and left the real
        (gac-bernina) server raising PermissionError on every poll. Keeping the
        group-write bit on means the next account can still rewrite it.
        """
        self._read_value.cache_clear()
        if self._group_writable:
            with open_group_writable(self.file_path, "w") as f:
                dump({"value": value}, f, indent=4)
        else:
            with open(self.file_path, "w") as f:
                dump({"value": value}, f, indent=4)

    def set_target_value(self, value, hold=False):
        return Changer(
            target=value,
            parent=self,
            changer=self._write_value,
            hold=hold,
            stopper=None,
        )

    def write_value_direct(self, value):
        """Write `value` immediately, bypassing Changer (and therefore the
        write chokepoints hung off it -- access control, recent-component
        tracking). For internal app bookkeeping that happens to reuse this
        class for its fs-persisted-json storage (e.g. eco.elements.recent,
        eco.widgets.component_selector.ComponentBookmarks) and isn't itself
        a namespace component -- routing that through Changer's device-write
        machinery is both wrong (it isn't a device write) and, for
        self-referential storage like the recent-component cache, a feedback
        loop (its own write would be recorded as a "use" of itself)."""
        self._write_value(value)


# class AdjustableObject(Assembly):
#     def __init__(self, adjustable_dict, name=None):
#         super().__init__(name=name)
#         self._base_dict = adjustable_dict

#     def set_field(self, fieldname, value):
#         d = self._base_dict.get_current_value()
#         if fieldname not in d.keys():
#             raise Exception(f"{fieldname} is not in dictionary")
#         d[fieldname] = value
#         self._base_dict.set_target_value(d)

#     def get_field(self, fieldname):
#         d = self._base_dict.get_current_value()
#         if fieldname not in d.keys():
#             raise Exception(f"{fieldname} is not in dictionary")
#         return d[fieldname]

#     def init_object(self):
#         for k, v in self._base_dict.get_cuurent_value().items():
#             tadj = AdjustableGetSet(
#                 lambda: self.get_field(k), lambda val: self.set_field(k, val), name=k
#             )
#             if type(v) is dict:
#                 self._append(
#                     AdjustableObject(tadj),
#                     call_obj=False,
#                     is_display=False,
#                     is_display="recursive",
#                 )
#             else:
#                 self._append(tadj, call_obj=False, is_setting=False, is_display=True)


@default_representation
@spec_convenience
@tweak_option
@value_property
@run_status_convenience
class AdjustableVirtual:
    def __init__(
        self,
        adjustables,
        foo_get_current_value,
        foo_set_target_value_current_value,
        change_simultaneously=True,
        reset_current_value_to=False,
        append_aliases=False,
        name=None,
        unit=None,
        callbacks_before_change=[],
        callbacks_after_change=[],
        check_limits=False,
    ):
        self.name = name
        self.alias = Alias(name)
        # A virtual adjustable is only as real as its controlling adjustables.
        # Validate them up front so that, if any parent does not exist (None,
        # or not an adjustable, or an unresolved/failed lazy proxy), this
        # virtual fails to build and shows up as a failed item instead of
        # silently "working" on non-existent parents.
        _invalid = []
        for i, adj in enumerate(adjustables):
            if adj is None:
                _invalid.append(f"index {i}: None")
                continue
            try:
                getval = getattr(adj, "get_current_value", None)
                setval = getattr(adj, "set_target_value", None)
            except Exception as e:
                # e.g. a lazy proxy whose target failed to initialize
                _invalid.append(f"index {i}: unresolved ({type(e).__name__}: {e})")
                continue
            if not (callable(getval) and callable(setval)):
                _invalid.append(
                    f"index {i}: {type(adj).__name__} is not an adjustable "
                    f"(missing get_current_value/set_target_value)"
                )
        if _invalid:
            raise ValueError(
                f"AdjustableVirtual '{name}' cannot be built: controlling "
                f"adjustable(s) missing or invalid: " + "; ".join(_invalid)
            )
        if append_aliases:
            for adj in adjustables:
                try:
                    self.alias.append(adj.alias)
                except Exception as e:
                    logger.warning(f"could not find alias in {adj}")
                    print(str(e))
        self._adjustables = adjustables
        self._foo_set_target_value_current_value = foo_set_target_value_current_value
        self._foo_get_current_value = foo_get_current_value
        self._reset_current_value_to = reset_current_value_to
        self._change_simultaneously = change_simultaneously
        self._check_limits = check_limits
        self._callbacks_before_change = callbacks_before_change
        self._callbacks_after_change = callbacks_after_change
        self.last_change = None
        if reset_current_value_to:
            for adj in self._adjustables:
                if not hasattr(adj, "reset_current_value_to"):
                    raise Exception(f"No reset_current_value_to method found in {adj}")
        if unit:
            self.unit = AdjustableMemory(unit, name="unit")

    def set_target_value(self, value, hold=False):
        for cb in self._callbacks_before_change:
            cb()
        vals = self._foo_set_target_value_current_value(value)

        if not hasattr(vals, "__iter__"):
            vals = (vals,)

        vals_before = [adj.get_current_value() for adj in self._adjustables]
        value_before = self._foo_get_current_value(*vals_before)
        self.last_change = {
            "value_before": value_before,
            "value_requested": value,
            "values_children_before": vals_before,
            "values_children_requested:": vals,
        }

        def changer(value):
            if self._check_limits:
                if not self.check_target_value_within_limits(value):
                    raise Exception(
                        f"Target value of virtual adjustable {self.name} is outside limit values some of {[adj.name for adj in self._adjustables]}!"
                    )
            # TODO try except for stopping all on any exception + option to return to old values before the change.
            if self._change_simultaneously:
                self._active_changers = [
                    adj.set_target_value(val, hold=False)
                    for val, adj in zip(vals, self._adjustables)
                    if val is not None
                ]
                for tc in self._active_changers:
                    tc.wait()
            else:
                for val, adj in zip(vals, self._adjustables):
                    if val is not None:
                        self._active_changers = [adj.set_target_value(val, hold=False)]
                        self._active_changers[0].wait()
            for cb in self._callbacks_after_change:
                cb()

        def stopper():
            for tc in self._active_changers:
                tc.stop()

        self._currentChange = Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=stopper
        )
        return self._currentChange

    def get_current_value(self):
        return self._foo_get_current_value(
            *[adj.get_current_value() for adj in self._adjustables]
        )

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        """Only possible if every parent adjustable is itself a
        MonitorableValueUpdate (e.g. a PV-backed Adjustable, or another
        AdjustableVirtual whose own parents all are) - in that case the
        combined value can be kept up to date by recomputing
        get_current_value() whenever any parent reports an update, with no
        polling."""
        non_monitorable = [
            adj for adj in self._adjustables if not isinstance(adj, MonitorableValueUpdate)
        ]
        if non_monitorable:
            names = [getattr(adj, "name", repr(adj)) for adj in non_monitorable]
            raise NotImplementedError(
                f"Cannot monitor virtual adjustable '{self.name}': parent(s) "
                f"{names} do not implement MonitorableValueUpdate "
                f"(set_current_value_callback)."
            )
        return CallbackComposedValue(
            self, self._adjustables, func=func, run_once=run_once,
            print_output=print_output, **kwargs
        )

    def check_target_value_within_limits(self, value):
        in_lims = [True]
        values = self._foo_set_target_value_current_value(value)
        if not hasattr(values, "__iter__"):
            values = (values,)

        for val, adj in zip(values, self._adjustables):
            lim_low, lim_high = adj.get_limits()
            if not val is None:
                in_lims.append((lim_low < val) and (val < lim_high))
        return all(in_lims)

    def reset_current_value_to(self, value):
        if not self._reset_current_value_to:
            raise NotImplementedError(
                "There is no value setting implemented for this virtual adjuster!"
            )
        else:
            vals = self._foo_set_target_value_current_value(value)
            if not hasattr(vals, "__iter__"):
                vals = (vals,)
            for adj, val in zip(self._adjustables, vals):
                if val is not None:
                    adj.reset_current_value_to(val)


class CallbackComposedValue:
    """set_current_value_callback() implementation for a value computed from
    other Monitorable* objects - used by both AdjustableVirtual and
    DetectorVirtual (eco.elements.detector), which is why `composed`/
    `children` are generic rather than hardcoded to "virtual adjustable" and
    its `._adjustables`.

    Subscribes to every child's own set_current_value_callback() and
    recomputes `composed.get_current_value()` whenever any child reports an
    update - so a derived/calculated value can be monitored with the same
    push-based, no-polling contract as a plain PV-backed value. Mirrors
    eco.epics_utils.utilities_epics.CallbackEpics (same .data shape,
    .start()/.stop(), context-manager support), and forwards the same
    `func`/`run_once` convention to each child, so this also works when a
    child is itself another composed value (nested composition, e.g. a
    virtual built from other virtuals).
    """

    def __init__(self, composed, children, func="accumulate", run_once=True,
                 print_output=False):
        self.composed = composed
        self.children = children
        self.print = print_output
        if func == "accumulate":
            func = self._accumulate
            self.data = {"timestamps": [], "values": [], "timestamps_ioc": []}
        elif func == "latest":
            # Keeps only the most recent value instead of an ever-growing
            # list - for monitoring meant to run indefinitely (e.g. a
            # long-lived status cache) rather than for the duration of one
            # scan. See eco.epics_utils.utilities_epics.CallbackEpics.
            func = self._set_latest
            self.data = {"value": None, "timestamp": None, "timestamp_local": None}
        self.foo = func
        self.run_once = run_once
        self._child_monitors = []

    def _accumulate(self, pvname=None, value=None, timestamp=None, **kwargs):
        ts_local = time.time()
        self.data["timestamps"].append(ts_local)
        self.data["values"].append(value)
        self.data["timestamps_ioc"].append(timestamp)
        if self.print:
            print(
                f"{self.composed.name}:  {value};  time_ioc: {timestamp}; time_local: {ts_local}"
            )

    def _set_latest(self, pvname=None, value=None, timestamp=None, **kwargs):
        ts_local = time.time()
        self.data["value"] = value
        self.data["timestamp"] = timestamp
        self.data["timestamp_local"] = ts_local
        if self.print:
            print(
                f"{self.composed.name}:  {value};  time_ioc: {timestamp}; time_local: {ts_local}"
            )

    def _on_parent_update(self, pvname=None, value=None, timestamp=None, **kwargs):
        # Ignore the individual child's own value/pvname - recompute the
        # combined value from every child's current value instead.
        new_value = self.composed.get_current_value()
        self.foo(pvname=self.composed.name, value=new_value, timestamp=timestamp)

    def start(self, add_current_value=True, with_ctrlvars=True, auto_monitor=True):
        """with_ctrlvars/auto_monitor exist so this is a drop-in match for
        CallbackEpics.start()'s interface (a caller that does not know
        whether a given channel is CA-backed or composed should not have to
        care) - both are simply forwarded to every child, recursively, in
        case a child is itself a composed value with children of its own.
        add_current_value is deliberately NOT forwarded the same way: each
        child starts without seeding (add_current_value=False), and this
        object seeds itself once at the end from the fully-composed
        get_current_value() instead of from each child's seed individually
        - the same recompute _on_parent_update always does, just triggered
        once up front rather than per child.
        """
        for child in self.children:
            mon = child.set_current_value_callback(
                func=self._on_parent_update, run_once=self.run_once
            )
            mon.start(add_current_value=False, with_ctrlvars=with_ctrlvars,
                     auto_monitor=auto_monitor)
            self._child_monitors.append(mon)
        if add_current_value:
            self._on_parent_update(timestamp=time.time())

    def is_running(self):
        return len(self._child_monitors) > 0

    def stop(self):
        for mon in self._child_monitors:
            mon.stop()
        self._child_monitors = []

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


class AdjustableInterpolate(AdjustableVirtual):
    def __init__(
        self,
        adjustable,
        filename_calib=None,
        deadband=None,
        interp_method="linear",
        callbacks_before_change=[],
        callbacks_after_change=[],
        unit=None,
        name=None,
    ):
        self._adjustable = adjustable

        if filename_calib:
            self.calibration = AdjustableFS(
                filename_calib, default_value=[], name="calibration"
            )
        else:
            self.calibration = AdjustableMemory(value=[], name="calibration")
        self._interp_method = interp_method
        self.deadband = deadband

        super().__init__(
            [adjustable],
            self.get_interp_value,
            lambda v: [self.get_raw_value(v)],
            callbacks_before_change=callbacks_before_change,
            callbacks_after_change=callbacks_after_change,
            unit=unit,
            name=name,
        )

    def get_interp_value(self, value):
        x, y = np.asarray(self.calibration.get_current_value()).T
        if self._interp_method == "linear":
            return np.interp(value, x, y)
        elif self._interp_method == "next":
            if self.deadband:
                if not (np.min(np.abs(x - value)) < self.deadband):
                    raise Exception(
                        "position not within deadband of any calibration value !"
                    )
            return y[np.argmin(np.abs(x - value))]

    def get_raw_value(self, value):
        x, y = np.asarray(self.calibration.get_current_value()).T
        if self._interp_method == "linear":
            return np.interp(value, y, x)
        elif self._interp_method == "next":
            return x[np.argmin(np.abs(y - value))]

    def reset_calibration(self):
        self.calibration.set_target_value([]).wait()

    def add_calibration_value_here(self, value):
        c = self.calibration.get_current_value()
        c.append([self._adjustable.get_current_value(), value])
        self.calibration.set_target_value(c).wait()


@default_representation
@spec_convenience
@tweak_option
@value_property
class AdjustableGetSet:
    def __init__(
        self,
        foo_get,
        foo_set,
        set_returns_changer=False,
        precision=0,
        check_interval=None,
        cache_get_seconds=None,
        unit=None,
        name=None,
    ):
        """assumes a waiting setterin function, in case no check_interval parameter is supplied.
        if returns_changer, does not create an additional thread"""

        self.alias = Alias(name)
        self.name = name
        self._set = foo_set
        self._get = foo_get
        self._set_returns_changer = set_returns_changer
        self._check_interval = check_interval
        self.precision = precision
        self._cache_get_seconds = cache_get_seconds
        if unit:
            self.unit = AdjustableMemory(unit, name="unit")

    def set_and_wait(self, value):
        if self._check_interval:
            self._set(value)
            while abs(self.get_current_value() - value) > self.precision:
                time.sleep(self._check_interval)
        else:
            self._set(value)

    def set_target_value(self, value, hold=False):
        if self._set_returns_changer:
            return self._set(value)
        else:
            return Changer(
                target=value,
                parent=self,
                changer=self.set_and_wait,
                hold=False,
                stopper=None,
            )

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


class AdjustableTrigger:
    """A stateless one-shot action (e.g. "go home", "clear a fault") with
    no value to read or set. Appended the same way as any other element::

        self._append(AdjustableTrigger, self.home, name="home")

    It shows up as a plain push button in the interactive widgets
    (eco.widgets.display_qt/display_widget) -- a clickable panel is what
    it's for. The plain terminal repr/display table
    (Assembly.get_display_str()/repr()) leaves it out by default instead
    (nothing to click there, and no value to show); pass
    get_display_str(show_triggers=True) for the rare case that wants it
    listed anyway (e.g. an elog status snapshot documenting what the
    assembly has). Since it has no get_current_value(), it's also
    automatically excluded from Assembly.get_status()'s status reporting
    (which only polls items satisfying the Detector protocol) and never
    shows up in the "settings" selection (there's nothing to set).

    `action` is any zero-argument callable (bind args with functools.
    partial/a lambda first if it needs them); calling the AdjustableTrigger
    itself, or .trigger(), fires it -- from a widget button this runs on a
    background thread so a blocking hardware call never freezes the GUI.
    `button_label` (optional) overrides the generic "Trigger" button
    caption, e.g. button_label="Home". `doc` (optional, defaults to
    `action.__doc__`) is shown as a tooltip.

    trigger()/__call__ silently accept (and ignore) any positional/keyword
    arguments rather than raising -- converting an existing momentary-PV
    "command" pattern (e.g. `mot.home_forward(1)`, the `1` just a
    conventional "do it" value the .PROC write itself already hardcodes)
    into an AdjustableTrigger this way is common; this keeps every such
    existing call site working unchanged instead of requiring a sweep to
    drop the now-meaningless argument everywhere it's still called."""

    def __init__(self, action, name=None, button_label=None, doc=None):
        self.name = name
        self.alias = Alias(name)
        self._action = action
        self.button_label = button_label
        self.doc = doc if doc is not None else getattr(action, "__doc__", None)

    def trigger(self, *args, **kwargs):
        return self._action()

    def __call__(self, *args, **kwargs):
        return self.trigger(*args, **kwargs)

    def mv(self, *args, **kwargs):
        """Alias for trigger(), for existing call sites converted from a
        real Adjustable's spec_convenience `.mv()` sugar."""
        return self.trigger(*args, **kwargs)

    def __repr__(self):
        extra = f" -- {self.doc}" if self.doc else ""
        return f"{self.name} (trigger){extra}"


@enum_repr
@spec_convenience
class AdjustableEnum:
    def __init__(self, adjustable_instance, enum_strs_ordered, name=None):
        self.name = name
        self._base = adjustable_instance
        self.name = name
        self.enum_strs = enum_strs_ordered
        self.value_enum = IntEnum(
            name, {tstr: n for n, tstr in enumerate(self.enum_strs)}
        )
        self.alias = Alias(name)

    def validate(self, value):
        if type(value) is str:
            return self.value_enum.__members__[value]
        else:
            return self.value_enum(value)

    def get_current_value(self):
        return self.validate(self._base.get_current_value())

    def set_target_value(self, value, hold=False):
        value = self.validate(value)
        return self._base.set_target_value(value, hold=hold)

class Tweak:
    def __init__(self, *args, detectors=None):
        """usage: Tweak((adj0,startstepsize0),(adj1,startstepsize1))

        detectors: optional Detector or list of Detectors. Their values are
        recorded for every tweak step (averaged while the positions stay
        constant) and live-plotted -- against the position for one
        adjustable, as a stack over the step number for several (see
        eco.widgets.tweak_recorder). The data stays in .recorder."""
        self.detectors = detectors
        self.recorder = None
        self.adjs = []
        startsteps = []
        for adj, startstep in args:
            self.adjs.append(adj)
            startsteps.append(startstep)

        self.startpositions = [adj.get_current_value() for adj in self.adjs]
        self.step_sizes = startsteps
        self.target_positions = []
        self.target_positions.append(self.startpositions)
        self._changers = []

    def get_current_values(self):
        return [adj.get_current_value() for adj in self.adjs]

    def set_target_step_increment(self, *args):
        """usage: set_target_value((adj0,+1),(adj2,-1))"""
        indexes = []
        directions = []
        for obj, direction in args:
            directions.append(direction)
            if type(obj) is int:
                indexes.append(obj)
            else:
                indexes.append(self.adjs.index(obj))
        new_target = self.target_positions[-1].copy()
        for index, direction in zip(indexes, directions):
            new_target[index] += direction * self.step_sizes[index]
        self.change_to_targets(new_target)

    def change_to_targets(self, targets):
        self.target_positions.append(targets)
        self._changers = [
            adj.set_target_value(target) for adj, target in zip(self.adjs, targets)
        ]

    def reset_current_value_to(self, value, *objs):
        if objs:
            indexes = []
            for obj in objs:
                indexes.append(obj if type(obj) is int else self.adjs.index(obj))
        else:
            indexes = list(range(len(self.adjs)))
        for index in indexes:
            adj = self.adjs[index]
            if hasattr(adj, "reset_current_value_to"):
                adj.reset_current_value_to(value)
            else:
                adj.set_target_value(value).wait()

    def xy_adjustable_tweak(self):
        if len(self.adjs) != 2:
            raise AdjustableError("xy_adjustable_tweak requires exactly two adjustables")

        if self._open_panel_instead():
            return

        x_adj, y_adj = self.adjs
        i_x, i_y = 0, 1
        help = (
            "q = exit; left/right = x +/-; up/down = y +/-\n"
            "ctrl+right = x step*2; ctrl+left = x step/2;\n"
            "ctrl+up = y step*2; ctrl+down = y step/2;\n"
            "s = reset both axes to origin"
        )
        print(f"tweaking x={x_adj.name}, y={y_adj.name}")
        print(help)
        print(f"Starting at x={self.target_positions[0][i_x]}, y={self.target_positions[0][i_y]}")
        k = KeyPress()
        cll = colorama.ansi.clear_line()

        class Printer:
            def __init__(self, tweak=self):
                self.tweak = tweak
                self.thread = None

            def print(self):
                if self.thread and self.thread.is_alive():
                    return
                else:
                    self.thread = Thread(target=self.print_foo)
                    self.thread.daemon = True
                    self.thread.start()

            def print_foo(self, **kwargs):
                if self.tweak._changers:
                    print(
                        cll
                        + f"x step: {self.tweak.step_sizes[i_x]}; y step: {self.tweak.step_sizes[i_y]}; current: changing",
                        end="\r",
                    )
                self.tweak.wait()
                current = self.tweak.get_current_values()
                print(
                    cll
                    + f"x step: {self.tweak.step_sizes[i_x]}; y step: {self.tweak.step_sizes[i_y]}; x: {current[i_x]:1.5g}; y: {current[i_y]:1.5g}",
                    end="\r",
                )

        p = Printer()
        print(" ")
        p.print()
        while k.isq() is False:
            if k.iscr():
                self.set_step_size((x_adj, self.step_sizes[i_x] * 2.0))
                p.print()
            elif k.iscl():
                self.set_step_size((x_adj, self.step_sizes[i_x] / 2.0))
                p.print()
            elif k.iscu():
                self.set_step_size((y_adj, self.step_sizes[i_y] * 2.0))
                p.print()
            elif k.iscd():
                self.set_step_size((y_adj, self.step_sizes[i_y] / 2.0))
                p.print()
            elif k.isu():
                self.set_target_step_increment((y_adj, +1))
                p.print()
            elif k.isd():
                self.set_target_step_increment((y_adj, -1))
                p.print()
            elif k.isr():
                self.set_target_step_increment((x_adj, +1))
                p.print()
            elif k.isl():
                self.set_target_step_increment((x_adj, -1))
                p.print()
            elif k.iskey("s"):
                self.change_to_targets(self.startpositions)
                p.print()
            elif k.isq():
                break

            k.waitkey()

    # Keyboard rows for stacked_adjustable_tweak, bottom to top. Within a row
    # the keys mirror the arrow layout of the single tweak:
    # (left = neg dir, up = step*2, down = step/2, right = pos dir).
    STACKED_KEY_ROWS = (
        ("m", ",", ".", "/"),
        ("j", "k", "l", ";"),
        ("u", "i", "o", "p"),
        ("7", "8", "9", "0"),
    )

    def tweak(self, stacked=None):
        """Interactive keyboard tweak of all adjustables of this Tweak.

        1 adjustable: arrow keys, 2: arrow keys in x/y, 3-4: stacked keyboard
        rows (see stacked_adjustable_tweak). stacked=True forces the stacked
        rows for 1 or 2 adjustables too. Outside a terminal (notebook, eco
        desktop console) this opens the tweak panel instead."""
        n = len(self.adjs)
        if stacked is None:
            stacked = n > 2
        if self._open_panel_instead(stacked=stacked):
            return
        if not stacked and n > 2:
            raise AdjustableError(f"cannot tweak {n} adjustables without stacked keys")
        from eco.widgets.tweak_panel import axes_from_tweak
        from eco.widgets.tweak_recorder import terminal_recording

        with terminal_recording(
            axes_from_tweak(self) if self.detectors else [],
            self.detectors,
            label=getattr(self, "_recorder_label", "<tweak>.recorder"),
        ) as recorder:
            if recorder is not None:
                self.recorder = recorder
            if stacked:
                return self.stacked_adjustable_tweak()
            if n == 1:
                return self.single_adjustable_tweak()
            return self.xy_adjustable_tweak()

    def widget(self, stacked=None, backend=None, display=False, **kwargs):
        """Tweak panel for these adjustables (Qt or ipywidgets), with the same
        keys as the terminal tweak under "keypress control" -- see
        eco.widgets.tweak_panel."""
        from eco.widgets.tweak_panel import axes_from_tweak, tweak_panel

        panel = tweak_panel(
            axes_from_tweak(self),
            stacked=stacked,
            backend=backend,
            display=display,
            detectors=self.detectors,
            **kwargs,
        )
        if getattr(panel, "recorder", None) is not None:
            self.recorder = panel.recorder
        return panel

    def _open_panel_instead(self, stacked=None):
        """Where there is no terminal to read keys from (notebook, eco desktop
        console), show the tweak panel instead. True if it did."""
        from eco.widgets.tweak_panel import frontend

        if frontend() == "terminal":
            return False
        self._panel = self.widget(stacked=stacked, display=True)
        return True

    def _stacked_key_rows(self):
        n = len(self.adjs)
        if not 1 <= n <= len(self.STACKED_KEY_ROWS):
            raise AdjustableError(
                f"stacked tweak supports 1 to {len(self.STACKED_KEY_ROWS)} adjustables, got {n}"
            )
        rows = self.STACKED_KEY_ROWS
        return list(rows[:4] if n == 4 else rows[1 : 1 + n])

    def stacked_adjustable_tweak(self):
        """Tweak up to 4 adjustables, one keyboard row per adjustable.

        Per row: 1st key = neg dir, 2nd = step*2, 3rd = step/2, 4th = pos dir.
        Rows used: 1 -> j k l ;   2 -> + u i o p   3 -> + 7 8 9 0
        4 -> m , . /  j k l ;  u i o p  7 8 9 0 (first adjustable lowest)."""
        rows = self._stacked_key_rows()
        keymap = {}
        for i_adj, (neg, dbl, half, pos) in enumerate(rows):
            keymap[neg] = (i_adj, "move", -1)
            keymap[dbl] = (i_adj, "step", 2.0)
            keymap[half] = (i_adj, "step", 0.5)
            keymap[pos] = (i_adj, "move", +1)

        names = [str(getattr(adj, "name", adj)) for adj in self.adjs]
        width = max(len(name) for name in names)
        print("tweaking " + ", ".join(names))
        print("q = exit; s = all axes back to start values")
        for i_adj in reversed(range(len(rows))):
            neg, dbl, half, pos = rows[i_adj]
            print(
                f"  {names[i_adj]:<{width}} :  {neg} = neg dir, {dbl} = step*2, "
                f"{half} = step/2, {pos} = pos dir"
            )
        print(
            "Starting at "
            + ", ".join(
                f"{name}={value}" for name, value in zip(names, self.target_positions[0])
            )
        )
        k = KeyPress()
        cll = colorama.ansi.clear_line()

        def _fmt(value):
            try:
                return f"{value:1.5g}"
            except Exception:
                return str(value)

        class Printer:
            def __init__(self, tweak=self):
                self.tweak = tweak
                self.thread = None

            def print(self):
                if self.thread and self.thread.is_alive():
                    return
                else:
                    self.thread = Thread(target=self.print_foo)
                    self.thread.daemon = True
                    self.thread.start()

            def _line(self, values):
                return "; ".join(
                    f"{name}: {_fmt(value)} (step {_fmt(step)})"
                    for name, value, step in zip(names, values, self.tweak.step_sizes)
                )

            def print_foo(self, **kwargs):
                if self.tweak._changers:
                    print(cll + "changing ...", end="\r")
                self.tweak.wait()
                print(cll + self._line(self.tweak.get_current_values()), end="\r")

        p = Printer()
        print(" ")
        p.print()
        while k.isq() is False:
            action = keymap.get(k.last_key)
            if action is not None:
                i_adj, kind, factor = action
                if kind == "step":
                    self.set_step_size((i_adj, self.step_sizes[i_adj] * factor))
                else:
                    self.set_target_step_increment((i_adj, factor))
                p.print()
            elif k.iskey("s"):
                self.change_to_targets(self.startpositions)
                p.print()

            k.waitkey()
        print("")

    def wait(self, sleeptime=0.02):
        if self._changers:
            while any(changer.is_alive() for changer in self._changers):
                time.sleep(sleeptime)

    def set_step_size(self, *args):
        """usage: set_step_size((adj0,step),(adj2,step))"""
        indexes = []
        stepsizes = []
        for obj, stepsize in args:
            stepsizes.append(stepsize)
            if type(obj) is int:
                indexes.append(obj)
            else:
                indexes.append(self.adjs.index(obj))
        new_steps = self.step_sizes.copy()
        for index, stepsize in zip(indexes, stepsizes):
            new_steps[index] = stepsize
        self.step_sizes = new_steps

    def single_adjustable_tweak(self):
        if self._open_panel_instead():
            return
        i_adj = 0
        adj = self.adjs[i_adj]
        # step_value = float(self.step_sizes[0])
        help = "q = exit; up = step*2; down = step/2, left = neg dir, right = pos dir\n"
        help = help + "g = go abs, s = start value, r = reset current value to"
        print(f"tweaking {adj.name}")
        print(help)
        print(f"Starting at {self.target_positions[0][i_adj]}")
        oldstep = 0
        k = KeyPress()
        cll = colorama.ansi.clear_line()

        class Printer:
            def __init__(self, tweak=self):
                self.tweak = tweak
                self.thread = None

            def print(self):
                if self.thread and self.thread.is_alive():
                    return
                else:
                    self.thread = Thread(target=self.print_foo)
                    self.thread.daemon = True
                    self.thread.start()

            def print_foo(self, **kwargs):
                if self.tweak._changers:
                    print(
                        cll
                        + f"stepsize: {self.tweak.step_sizes[i_adj]}; current: changing",
                        end="\r",
                    )
                self.tweak.wait()
                print(
                    cll
                    + f"stepsize: {self.tweak.step_sizes[i_adj]}; current: {self.tweak.get_current_values()[i_adj]}",
                    end="\r",
                )

        p = Printer()
        print(" ")
        p.print()
        while k.isq() is False:
            if k.isu():
                self.set_step_size((adj, self.step_sizes[i_adj] * 2.0))
                p.print()
            elif k.isd():
                self.set_step_size((adj, self.step_sizes[i_adj] / 2.0))
                p.print()
            elif k.isr():
                self.set_target_step_increment((adj, +1))
                p.print()
            elif k.isl():
                self.set_target_step_increment((adj, -1))
                p.print()
            elif k.iskey("s"):
                self.change_to_targets(self.target_positions[0])
                p.print()
            elif k.iskey("g"):
                print("enter absolute position (char to abort go to)")
                sys.stdout.flush()
                v = sys.stdin.readline()
                try:
                    v = float(v.strip())
                    adj.set_target_value(v)
                except:
                    print("value cannot be converted to float, exit go to mode ...")
                    sys.stdout.flush()
            elif k.iskey("r"):
                print("enter value to reset current to (char to abort setting)")
                sys.stdout.flush()
                v = sys.stdin.readline()
                try:
                    v = float(v[0:-1])
                    self.reset_current_value_to(v)
                except:
                    print(
                        "value cannot be converted to float, exit reset-value-tomode ..."
                    )
                    sys.stdout.flush()
            elif k.isq():
                break

            k.waitkey()


class NumpyEncoder(json.JSONEncoder):
    """Special json encoder for numpy types"""

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)
