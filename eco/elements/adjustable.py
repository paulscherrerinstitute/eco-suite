import datetime
import json
import logging
import time
from json import load, dump
from pathlib import Path
from threading import Thread
import itertools
import colorama
import numpy as np

import eco
from eco.aliases import Alias
from eco.devices_general.utilities import Changer


from eco.utilities.keypress import KeyPress

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
    def tweak(self, interval, *args, **kwargs):
        self._tweak_instance = Tweak((self, interval))
        self._tweak_instance.single_adjustable_tweak()

    Obj.tweak = tweak
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
            except KeyboardInterrupt:
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
                self._currentChange.wait()
            except KeyboardInterrupt:
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
        self.mvr(value)
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
        except KeyboardInterrupt:
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


@spec_convenience
@update_changes
@tweak_option
@value_property
class AdjustableMemory:
    def __init__(self, value=0, name="adjustable_memory"):
        self.name = name
        self.alias = Alias(name)
        self.current_value = value

    def get_current_value(self):
        return deepcopy(self.current_value)

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
    def __init__(self, file_path, name=None, default_value=None, max_read_period=0.2):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            if not self.file_path.parent.exists():
                self.file_path.parent.mkdir(parents=True)
                try:
                    self.file_path.parent.chmod(0o775)
                except:
                    pass
            self._write_value(default_value)
        self.alias = Alias(name)
        self.max_read_period = max_read_period
        self.name = name

    def get_current_value(self):
        return self._read_value()

    # @cache_file_access
    @cachebox.cached(cachebox.TTLCache(maxsize=0, ttl=ADJUSTABLEFS_MAX_READ_PERIOD))
    def _read_value(self):
        with open(self.file_path, "r") as f:
            res = load(f)
        return res["value"]

    def _cache_file_access(self, foo):
        @cachebox.cached(cachebox.TTLCache(maxsize=0, ttl=self.max_read_period))
        def wrapper(*args, **kwargs):
            return foo(*args, **kwargs)

        return wrapper

    def _write_value(self, value):
        self._read_value.cache_clear()
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
        check_limits=False,
    ):
        self.name = name
        self.alias = Alias(name)
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
        if reset_current_value_to:
            for adj in self._adjustables:
                if not hasattr(adj, "reset_current_value_to"):
                    raise Exception(f"No reset_current_value_to method found in {adj}")
        if unit:
            self.unit = AdjustableMemory(unit, name="unit")

    def set_target_value(self, value, hold=False):
        vals = self._foo_set_target_value_current_value(value)
        if not hasattr(vals, "__iter__"):
            vals = (vals,)

        def changer(value):
            if self._check_limits:
                if not self.check_target_value_within_limits(value):
                    raise Exception(
                        f"Target value of virtual adjustable {self.name} is higher than limit values of {[adj.name for adj in self._adjustables]}!"
                    )

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

    def check_target_value_within_limits(self, value):
        in_lims = []
        values = self._foo_set_target_value_current_value(value)
        if not hasattr(values, "__iter__"):
            values = (values,)

        for val, adj in zip(values, self._adjustables):
            lim_low, lim_high = adj.get_limits()
            in_lims.append((lim_low < val) and (val < lim_high))
        return all(in_lims)

    def reset_current_value_to(self, value):
        if not self._reset_current_value_to:
            raise NotImplementedError(
                "There is no value setting implemented for this virtual adjuster!"
            )
        else:
            vals = self._foo_set_target_value_current_value(value)
            for adj, val in zip(self._adjustables, vals):
                adj.reset_current_value_to(val)


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

    def __repr__(self):
        name = self.name
        cv = self.get_current_value()
        s = f"{name} (enum) at value: {cv}" + "\n"
        s += "{:<5}{:<5}{:<}\n".format("Num.", "Sel.", "Name")
        # s+= '_'*40+'\n'
        for name, val in self.value_enum.__members__.items():
            if val == cv:
                sel = "x"
            else:
                sel = " "
            s += "{:>4}   {}  {}\n".format(val, sel, name)
        return s


class Tweak:
    def __init__(self, *args):
        """usage: Tweak((adj0,startstepsize0),(adj1,startstepsize1))"""
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

    def wait(self, sleeptime=0.02):
        if self._changers:
            changing = True
            while changing:
                for changer in self._changers:
                    changing = changer.is_alive()
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
                if self.thread and self.thread.isAlive():
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
