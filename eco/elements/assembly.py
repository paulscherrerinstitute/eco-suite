from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime
from inspect import isclass
import json
from pathlib import Path
from tkinter import W
import weakref
from markdown import markdown

from numpy import isin
import numpy as np


from eco.acquisition.scan_data import run_status_convenience
from eco.elements.protocols import Detector, InitialisationWaitable
from eco.epics_utils import get_from_archive

from ..aliases import Alias
from ..utilities.tables import format_table, section_row_styles
import colorama
from . import memory
from enum import Enum
import os
import subprocess
from rich.progress import track
from eco import Adjustable, Detector

import eco


_initializing_assemblies = []

# Default for the `optional` flag of Assembly._append. When True, a component
# whose instantiation raises is skipped with a warning (recorded in
# `_failed_appends`) instead of killing the whole assembly. Individual _append
# calls can still override this explicitly with optional=True/False.
OPTIONAL_APPEND_DEFAULT = True


def _register_parent_assembly(obj, parent):
    """Record that `obj` was appended into `parent` (called from every
    `Assembly._append`, the single chokepoint essentially everything in this
    codebase's object trees is built through -- `Namespace.append_obj` funnels
    into it too -- so this gives complete coverage of the whole tree, top to
    bottom).

    `obj._parent_assemblies` is a *list* of weakrefs, not a single one: the
    same object can legitimately be appended into more than one Assembly (a
    shared component referenced from two different parent contexts), so it
    can have more than one direct parent. Only takes effect going forward --
    an object built before this existed, in an already-running session, has
    no such list until that session is reinitialized.
    """
    existing = obj.__dict__.setdefault("_parent_assemblies", [])
    for ref in existing:
        if ref() is parent:
            return
    existing.append(weakref.ref(parent))


def iter_ancestor_assemblies(obj):
    """Yield every ancestor Assembly of `obj`, reachable via the
    `_parent_assemblies` back-references `_register_parent_assembly` sets --
    breadth-first, nearest first, each ancestor yielded exactly once even if
    reachable through more than one parent branch (an object can have more
    than one direct parent; see `_register_parent_assembly`). Dead weakrefs
    (a former parent since garbage-collected) are silently skipped."""
    seen = set()
    frontier = list(getattr(obj, "_parent_assemblies", []) or [])
    while frontier:
        ref = frontier.pop(0)
        parent = ref()
        if parent is None or id(parent) in seen:
            continue
        seen.add(id(parent))
        yield parent
        frontier.extend(getattr(parent, "_parent_assemblies", []) or [])


class IncompleteInitialisationError(Exception):
    """Raised when an assembly is used (e.g. get_status) while one or more of
    its optional components failed to initialize.

    The message names the missing alias(es) so the culprit is obvious. The
    assembly itself stays partially usable: everything that *did* append works.
    """

    pass


class FailedComponent:
    """Placeholder left in an assembly slot when an optional component failed
    to initialize (see Assembly._append(optional=True)).

    It marks the slot as *present but failed*: ``name in obj.__dict__`` and
    ``isinstance(obj.name, FailedComponent)`` both work, so code can detect the
    failure explicitly instead of seeing the attribute simply missing. Any
    attempt to actually *use* it (get_current_value, alias, ...) raises with the
    original cause chained in, so a failure never passes silently.
    """

    def __init__(self, name, exception):
        # set via __dict__ so __getattr__ (below) never intercepts these
        self.__dict__["name"] = name
        self.__dict__["exception"] = exception

    def __repr__(self):
        exc = self.__dict__["exception"]
        return f"<FAILED {self.__dict__['name']}: {type(exc).__name__}: {exc}>"

    def __getattr__(self, item):
        # any attribute access other than name/exception is a real use attempt
        raise AttributeError(
            f"'{self.__dict__['name']}' failed to initialize"
        ) from self.__dict__["exception"]

    def __bool__(self):
        return False


class StatusCollection:
    def __init__(self, parent, name="status_collection"):
        self.parent = weakref.ref(parent)
        self.selections = {}

        if name is None:
            raise Exception("A name of collection is required")
        self.name = name
        self._list = []

    def get_list(self, selection=None, **kwargs):
        rec_list_items = kwargs.get("rec_list_items", [])
        ls = []
        for witem in self._list:
            item = witem()
            if item is None:
                continue

            if item is self.parent:
                continue

            if item in ls:
                continue

            if selection is not None:
                if selection not in self.selections.keys():
                    continue
                item_name = item.alias.get_full_name(base=self.parent())
                if item_name not in self.selections[selection].keys():
                    continue
                recurse = self.selections[selection][item_name]["recurse"]
            else:
                recurse = True

                ls.append(item)

                # important to get field in case no recursion is defined.
                if item is self.parent():
                    recurse = False

            if hasattr(item, f"{self.name}") and isinstance(
                item.__dict__[self.name], self.__class__
            ):
                if recurse:
                    # if hasattr(item, "recursing") and item.recursing:
                    #     print(
                    #         f"recursing detected loop at {item.alias.get_full_name()}"
                    #     )
                    # item.recursing = True

                    for titem in item.__dict__[self.name].get_list(
                        selection=selection, ls=[]
                    ):

                        if titem not in ls:
                            ls.append(titem)

                else:

                    if item not in ls:
                        ls.append(item)

            else:
                if item not in ls:
                    ls.append(item)

        return ls

    def get_names(self, selection=None):
        return [
            item.alias.get_full_name(base=self.parent())
            for item in self.get_list(selection=selection)
        ]

    def get_selections_names(self):
        return self.selections.keys()

    def append(self, obj, selection=None, recursive=True):

        if selection is not None:
            if selection not in self.selections:
                self.selections[selection] = {}
            obj_name = obj.alias.get_full_name(base=self.parent())
            self.selections[selection][obj_name] = {"recurse": recursive}
        if obj not in [tl() for tl in self._list]:
            self._list.append(weakref.ref(obj))

    def remove(self, obj, selection=None):
        """Remove an object from the collection. If selection is given, only remove from that selection."""
        if obj in [wobj() for wobj in self._list]:
            obj_name = obj.alias.get_full_name(base=self.parent())
            if selection is None:
                ix = [wobj() for wobj in self._list].index(obj)
                self._list.remove(self._list[ix])
        else:
            raise ValueError("Item not in list")
        if selection is not None:
            selections = [selection]
        else:
            selections = self.selections.keys()
        for selection in selections:
            if obj_name in self.selections[selection]:
                del self.selections[selection][obj_name]

    def __call__(self):
        return self.get_list()


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


@get_from_archive
@run_status_convenience
class Assembly:
    def __init__(
        self,
        name=None,
        parent=None,
        is_alias=True,
        elog=None,
        memory_categories=None,
        memory_change_serially=None,
    ):
        self.name = name
        self.alias = Alias(name, parent=parent)
        # self.settings = []
        # self.status_indicators = []
        self.status_collection = StatusCollection(self, name="status_collection")
        # name -> exception, for optional components (_append(..., optional=True))
        # whose instantiation raised. Kept so the assembly stays partially
        # usable while the failure remains visible/inspectable.
        self._failed_appends = {}
        # subset of _failed_appends names that were marked is_display, so the
        # repr can show them (in red) alongside the working components.
        self._failed_appends_display = set()
        # name -> _BeamlinePosition, for mark_beamline()/beamline_view() (see
        # eco.elements.beamline_view) -- purely additive bookkeeping, decoupled
        # from _append/status_collection on purpose so a position can be
        # registered (and shown by beamline_view()) for a component that is
        # lazy/not-yet-constructed or that failed to initialize, not just a
        # live one. Empty and inert unless mark_beamline() is actually called.
        self._beamline_positions = {}

        if memory.global_memory_dir:
            memory_kwargs = {}
            if memory_categories is not None:
                memory_kwargs["categories"] = memory_categories
            if memory_change_serially is not None:
                memory_kwargs["change_serially"] = memory_change_serially
            self.memory = memory.Memory(self, **memory_kwargs)
        if elog:
            self.__elog = elog
        # else:
        #     self.__class__.__elog = property(lambda dum: ELOG)

    # TODO: Lazy an threaded append! (for PVs, should be quite a speedup).
    def _append(
        self,
        foo_obj_init,
        *args,
        name=None,
        is_setting=False,
        is_display=True,
        is_status=True,
        setting_groups=None,
        # recursive=None,
        call_obj=True,
        overwrite=False,
        optional=None,
        **kwargs,
    ):
        """This hidden method appends an object to the assembly. It can take either an object instance, or a class (in which case it will be called with the provided args and kwargs).
        Parameters
        ----------
        foo_obj_init : Adjustable, Detector, Assembly, class, callable
            The object to append, or a class/callable to instantiate.
            name : str, optional
            The name of the object within the assembly. If None, the name attribute of the object will be used.
        is_setting : bool or str "recursive", optional
        setting_groups : str, iterable of str, or None, optional
            Additional named `status_collection` selections to tag this
            component under, alongside whatever `is_setting`/`is_display`
            already do -- e.g. `setting_groups="motor_settings"` so it shows
            up in a `Memory` recall group named "motor_settings" (see
            `eco.elements.memory.Memory`'s `categories` argument), independent
            of the plain "settings" group `is_setting` controls. A group name
            ending in "" is treated the same as `is_setting`'s bare bool: not
            recursive. Pass a name suffixed the same way `is_setting` accepts
            "recursive" (e.g. `"motor_settings:recursive"`) to recurse into
            sub-assemblies for that group too. Purely additive bookkeeping --
            `None` (the default) changes nothing.
        optional : bool or None, optional
            If True, a failure while instantiating/appending this component is
            not fatal: the exception is recorded in ``self._failed_appends``,
            a warning is printed, and the rest of the assembly keeps building.
            The assembly is then "incomplete" and ``get_status`` will raise
            :class:`IncompleteInitialisationError` naming the missing alias.
            If None (the default), the module-level ``OPTIONAL_APPEND_DEFAULT``
            is used."""
        if optional is None:
            optional = OPTIONAL_APPEND_DEFAULT
        if overwrite:

            if name in self.__dict__:
                old = self.__dict__[name]
                self.status_collection.remove(old)
                self.alias.pop_object(old.alias)
                del old

        try:
            if isinstance(foo_obj_init, Adjustable) and not isclass(foo_obj_init):
                # adj_copy = copy.copy(foo_obj_init)
                adj_copy = foo_obj_init
                obj = adj_copy
            elif isinstance(foo_obj_init, Detector) and not isclass(foo_obj_init):
                obj = foo_obj_init
            elif isinstance(foo_obj_init, Assembly) and not isclass(foo_obj_init):
                obj = foo_obj_init
            elif call_obj and callable(foo_obj_init):
                obj = foo_obj_init(*args, **kwargs, name=name)
            else:
                obj = foo_obj_init
        except Exception as e:
            if not optional:
                raise
            if not hasattr(self, "_failed_appends"):
                self._failed_appends = {}
            self._failed_appends[name] = e
            if is_display:
                if not hasattr(self, "_failed_appends_display"):
                    self._failed_appends_display = set()
                self._failed_appends_display.add(name)
            print(
                colorama.Fore.RED
                + colorama.Style.BRIGHT
                + f"WARNING: optional component '{name}' of "
                + f"'{self.alias.get_full_name()}' failed to initialize; kept as a "
                + f"FailedComponent placeholder:\n    {type(e).__name__}: {e}"
                + colorama.Style.RESET_ALL
            )
            # leave a sentinel in the slot so the component is *present but
            # failed*: attribute/isinstance checks see it, but any real use
            # re-raises. It is deliberately not added to status_collection/alias
            # (the red repr row is driven by _failed_appends instead). Retrying
            # is done at the namespace-component level (Namespace.reinitialize),
            # not per sub-component, so no retry closure is captured here.
            self.__dict__[name] = FailedComponent(name, e)
            return self.__dict__[name]

        # a previously-failed optional component now appended successfully
        getattr(self, "_failed_appends", {}).pop(name, None)
        getattr(self, "_failed_appends_display", set()).discard(name)

        # `obj` itself constructed without raising, but may carry failed
        # sub-components of its *own* (from optional `_append` calls inside
        # its __init__) -- e.g. a Valve built fine but one of its internal PVs
        # didn't connect. Without this, that failure is only ever visible one
        # level down (on `obj` itself); every ancestor above it -- including
        # this object once *it* in turn gets appended somewhere else -- would
        # see a fully "complete" component and never know. Mirror it here so
        # it keeps bubbling all the way up, however deep the tree is.
        nested_failed = getattr(obj, "_failed_appends", None)
        if nested_failed:
            if not hasattr(self, "_failed_appends"):
                self._failed_appends = {}
            missing = ", ".join(nested_failed)
            self._failed_appends[name] = IncompleteInitialisationError(
                f"'{name}' initialized with failed sub-component(s): {missing} "
                f"(see <obj>.{name}._failed_appends for the underlying exceptions)"
            )
            if is_display:
                if not hasattr(self, "_failed_appends_display"):
                    self._failed_appends_display = set()
                self._failed_appends_display.add(name)
            print(
                colorama.Fore.RED
                + colorama.Style.BRIGHT
                + f"WARNING: component '{name}' of "
                + f"'{self.alias.get_full_name()}' initialized incompletely; "
                + f"failed sub-component(s): {missing}"
                + colorama.Style.RESET_ALL
            )

        self.__dict__[name] = obj
        self.alias.append(self.__dict__[name].alias)
        _register_parent_assembly(self.__dict__[name], self)

        self.status_collection.append(self.__dict__[name])
        # if is_status == "auto":
        #     is_status = isinstance(self.__dict__[name], Detector)
        if is_setting:
            if isinstance(is_setting, str):
                recursive = is_setting.lower() == "recursive"
            else:
                recursive = True
            self.status_collection.append(
                self.__dict__[name], selection="settings", recursive=recursive
            )
            # self.status_collection.append(
            #     self.__dict__[name], selection="settings", recursive=True
            # )
        if is_display:
            if isinstance(is_display, str):
                recursive = is_display.lower() == "recursive"
            else:
                recursive = False
            self.status_collection.append(
                self.__dict__[name], selection="display", recursive=recursive
            )
        if setting_groups:
            if isinstance(setting_groups, str):
                setting_groups = [setting_groups]
            for group in setting_groups:
                recursive = group.lower().endswith(":recursive")
                group_name = group.split(":", 1)[0] if recursive else group
                self.status_collection.append(
                    self.__dict__[name], selection=group_name, recursive=recursive
                )

    def get_status(
        self,
        base="self",
        verbose=False,
        print_times=False,
        channeltypes=None,
        selections=[],
        threads=True,
        max_workers=20,
        raise_on_incomplete=True,
        # print_name=False,
    ):
        if base == "self":
            base = self
        if raise_on_incomplete and getattr(self, "_failed_appends", None):
            missing = ", ".join(
                f"{self.alias.get_full_name()}.{n}" for n in self._failed_appends
            )
            raise IncompleteInitialisationError(
                f"Incomplete initialisation of '{self.alias.get_full_name()}': "
                f"component(s) missing (failed to initialize): {missing}. "
                f"Inspect the underlying exception(s) via <obj>._failed_appends; "
                f"call get_status(raise_on_incomplete=False) to read partial status anyway."
            )
        # settings = {}
        # settings_channels = {}
        # settings_times = {}
        status = {}
        status_channels = {}
        status_times = {}
        nodet = []
        geterror = []

        def get_stat_one_detector(ts):
            tstart = time.time()
            try:
                if (not channeltypes) or (ts.alias.channeltype in channeltypes):
                    status[ts.alias.get_full_name(base=base)] = ts.get_current_value()
                    try:
                        status_channels[ts.alias.get_full_name(base=base)] = (
                            ts.alias.channel
                        )
                    except Exception:
                        pass
            except Exception:
                geterror.append(ts.alias.get_full_name(base=base))
            finally:
                status_times[ts.alias.get_full_name(base=base)] = time.time() - tstart

        detectors = []
        for ts in self.status_collection.get_list():
            if isinstance(ts, Detector):
                detectors.append(ts)
            else:
                nodet.append(ts.alias.get_full_name(base=base))

        if threads and max_workers > 1 and len(detectors) > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as exc:
                list(
                    track(
                        exc.map(get_stat_one_detector, detectors),
                        transient=True,
                        description="Reading status indicators ...",
                        total=len(detectors),
                    )
                )
        else:
            for ts in track(
                detectors,
                transient=True,
                description="Reading status indicators ...",
            ):
                get_stat_one_detector(ts)

        if verbose:
            if nodet:
                print("Could not retrieve status from:\n    " + ",\n    ".join(nodet))
            if geterror:
                print(
                    "Retrieved error while running get_current_value from:\n    "
                    + ",\n    ".join(geterror)
                )

        if print_times:
            from ascii_graph import Pyasciigraph

            gr = Pyasciigraph()
            for line in gr.graph(
                "Times required to get status",
                sorted(status_times.items(), key=lambda w: w[1]),
            ):
                print(line)

        # optional components that failed to initialize are reported as None so
        # it is clear they are missing (only reached when raise_on_incomplete is
        # False; otherwise get_status raises above).
        for fname in getattr(self, "_failed_appends", {}):
            try:
                fullname = self.alias.get_full_name(base=base) + "." + fname
            except Exception:
                fullname = fname
            status.setdefault(fullname, None)

        sel_dict = {}
        for selection_name in selections:
            sel = self.status_collection.get_names(selection=selection_name)
            sel_dict[selection_name] = {
                tname: status[tname] for tname in sel if tname in status.keys()
            }

        return {
            # "settings": settings,
            "status": status,
            # "settings_channels": settings_channels,
            "status_channels": status_channels,
            # "settings_times": settings_times,
            "status_times": status_times,
            "selections": sel_dict,
        }
    def get_tree(self, level=1, print_tree=True):
        """Return nested status_collection member names as a dictionary."""

        def build_tree(node, depth):
            result = {}
            for wref in node.status_collection._list:
                item = wref()
                if item is None or item is node:
                    continue
                try:
                    name = item.alias.get_full_name(base=node)
                except Exception:
                    name = getattr(item, 'name', str(item))
                if hasattr(item, 'status_collection') and (depth > 1 or depth <= 0):
                    next_depth = depth - 1 if depth > 0 else depth
                    result[name] = build_tree(item, next_depth)
                else:
                    result[name] = {}
            return result

        def format_tree(tree, prefix=''):
            lines = []
            items = list(tree.items())
            for index, (name, subtree) in enumerate(items):
                connector = '└── ' if index == len(items) - 1 else '├── '
                lines.append(f"{prefix}{connector}{name}")
                if subtree:
                    extension = '    ' if index == len(items) - 1 else '│   '
                    lines.extend(format_tree(subtree, prefix + extension))
            return lines

        tree = build_tree(self, level)
        if print_tree:
            if tree:
                for line in format_tree(tree):
                    print(line)
            else:
                print('(empty)')
        return tree

    def status(self, get_string=False):
        stat = self.get_status()
        s = format_table([[name, value] for name, value in stat["status"].items()])
        if get_string:
            return s
        else:
            print(s)

    def settings(self, get_string=False):
        stat = self.get_status()
        s = format_table(
            [
                [colorama.Style.BRIGHT + name + colorama.Style.RESET_ALL, value]
                for name, value in stat["settings"].items()
            ]
        )
        if get_string:
            return s
        else:
            print(s)

    def get_status_str(self, base=None, stat_fields=["settings"]):
        stat = self.get_status(base=base)
        stat_filt = {}
        for stat_field in stat_fields:
            tstat = stat[stat_field]
            for to in self.view_toplevel_only:
                tname = to.alias.get_full_name(base=base)
                tstat = filter_names(tname, tstat)
            stat_filt[stat_field] = tstat
        s = format_table([[name, value] for name, value in stat_filt[stat_field].items()])
        return s

    def get_display_str(
        self,
        tablefmt="simple",
        with_base_name=False,
        maxcolwidths=[None, None, None, 50, None],
        show_triggers=False,
    ):
        """show_triggers=False (default): AdjustableTrigger rows (the
        "(trigger)"/▶️ rows -- an action, not a value) are left out of this
        plain-text/HTML table. They're meant for a clickable panel (Qt or
        Jupyter widget -- see eco.widgets.display_qt/display_widget, where
        a trigger really does render as a button), not a quick terminal
        glance at an assembly's current values, where a row that can't be
        clicked and has no value to show is just noise. Pass True to
        include them anyway (e.g. an elog status snapshot documenting what
        the assembly has, not just its current readings)."""
        main_name = self.name
        stats = self.status_collection.get_list(selection="display")
        # stats_dict = {}
        tab = []
        # which top-level sub-component each row "unfolds" from (the first
        # segment of its dotted path relative to self, or the whole name for
        # a row that isn't nested under anything) -- see section_row_styles:
        # a sub-assembly contributing several rows gets its own background
        # block; an unrelated single-line entry doesn't claim a colour.
        # local import: eco.elements.adjustable imports plain `eco` at module
        # level, so importing it back at assembly.py's top level would risk
        # a circular import during `eco/__init__.py`'s own
        # `from eco.elements.assembly import Assembly` -- deferring it to
        # call time (well after the package has finished importing in any
        # real usage) sidesteps that entirely.
        from eco.elements.adjustable import AdjustableTrigger

        group_keys = []
        for to in stats:
            is_trigger = isinstance(to, AdjustableTrigger)
            if is_trigger and not show_triggers:
                continue

            name = to.alias.get_full_name(base=self)

            is_adjustable = isinstance(to, Adjustable)
            is_detector = isinstance(to, Detector)
            typechar = ""
            # colour-emoji glyphs (U+FE0F presentation selector). Our terminals
            # draw these 1 cell wide; _patch_rich_emoji_width() in
            # eco.utilities.tables makes rich measure them the same so columns
            # stay aligned.
            if is_trigger:
                typechar += "▶️"
            elif is_adjustable:
                typechar += "✏️"
            elif is_detector:
                typechar += "👁️"
            if hasattr(to, "status_collection"):
                typechar += " ↳"

            try:
                value = to.get_current_value()
            except AttributeError:
                if is_trigger:
                    value = "\x1b[3m(trigger)\x1b[0m"
                elif hasattr(to, "status_collection"):
                    value = "\x1b[3mhas lower level items\x1b[0m"
                else:
                    # safety net: anything else lacking get_current_value()
                    # (and not a nested sub-assembly) would otherwise leave
                    # `value` unbound and crash the whole repr() below
                    value = "\x1b[3m<no value>\x1b[0m"

            if isinstance(value, Enum):
                value = f"{value.value} ({value.name})"
            try:
                unit = to.unit.get_current_value()
            except:
                unit = ""
            try:
                description = to.description.get_current_value()
            except:
                description = ""

            if value is None:
                value = ""
            if with_base_name:
                tab.append(
                    [".".join([main_name, name]), value, unit, description, typechar]
                )
            else:
                tab.append([name, value, unit, description, typechar])
            # grouping key is always the *unprefixed* name -- with_base_name
            # only changes the displayed label, not which sub-component a row
            # unfolds from.
            group_keys.append(name.split(".", 1)[0])

        # optional components that failed to initialize (and were marked
        # is_display) are listed in red so the missing device is obvious.
        _red = colorama.Fore.RED + colorama.Style.BRIGHT
        _reset = colorama.Style.RESET_ALL
        for fname in getattr(self, "_failed_appends", {}):
            if fname not in getattr(self, "_failed_appends_display", set()):
                continue
            dispname = ".".join([main_name, fname]) if with_base_name else fname
            tab.append(
                [f"{_red}{dispname}{_reset}", f"{_red}FAILED{_reset}", "", "", "⚠️ "]
            )
            group_keys.append(fname.split(".", 1)[0])
        if tab:
            row_styles = section_row_styles(group_keys)
            s = format_table(tab, tablefmt=tablefmt, maxcolwidths=maxcolwidths, row_styles=row_styles)
        else:
            s = ""

        return s

    def status_to_elog(
        self,
        text="",
        elog=None,
        files=None,
        text_encoding="markdown",
        auto_title=True,
        attach_display=True,
        attach_status_file=True,
    ):
        if elog is None:
            elog = self._get_elog()
        message = ""

        if auto_title:
            message += markdown(f"#### Status {self.alias.get_full_name()}")

        if text:
            if text_encoding == "markdown":
                message += markdown(text)
        if attach_display:
            # show_triggers=True: unlike the terminal repr, a status
            # snapshot going into the logbook is documenting what the
            # assembly *has*, not just a quick glance at current values --
            # preserves this call's existing behavior, since
            # get_display_str's new default (False) is specifically about
            # the terminal case
            message += self.get_display_str(tablefmt="html", show_triggers=True)
        if files is None:
            files = []
        if attach_status_file:
            stat = self.get_status()
            tmppath = Path("/tmp")
            filepath = tmppath / Path(
                f"status_{self.alias.get_full_name}_{datetime.now().isoformat()}.json"
            )
            with open(filepath, "w") as f:
                # json.dump(stat, f, cls=NumpyEncoder, indent=4)
                json.dump(stat, f, indent=4, cls=NumpyEncoder)
            files.append(filepath)

        if len(files) > 1:
            print(files)

        return elog.post(
            message,
            *files,
            text_encoding="html",
        )
        # tags=[],

    def get_failed_components(self):
        """Return {dotted_name: exception} for every optional component that
        failed to initialize, in this assembly *and recursively* in its
        sub-assemblies. Names are relative to this assembly.

        Needed because a sub-assembly (e.g. a Pipeline) that fails only
        partially still comes up as a valid object, so its failures live in its
        own ``_failed_appends`` rather than this assembly's - a plain check of
        ``self._failed_appends`` would miss them.
        """
        out = {}
        for n, exc in getattr(self, "_failed_appends", {}).items():
            out[n] = exc
        try:
            members = self.status_collection.get_list()
        except Exception:
            members = []
        for item in members:
            if item is self:
                continue
            fa = getattr(item, "_failed_appends", None)
            if fa:
                try:
                    base = item.alias.get_full_name(base=self)
                except Exception:
                    base = getattr(item, "name", "?")
                for n, exc in fa.items():
                    out[f"{base}.{n}"] = exc
        return out

    def __repr__(self):
        fullname = self.alias.get_full_name()
        label = fullname + " display\n"
        banner = ""
        failed = self.get_failed_components()
        if failed:
            red = colorama.Fore.RED + colorama.Style.BRIGHT
            reset = colorama.Style.RESET_ALL
            names = ", ".join(failed)
            banner = (
                f"{red}⚠️  {fullname}: INITIALIZATION INCOMPLETE — "
                f"failed component(s): {names}{reset}\n"
            )
        return banner + label + self.get_display_str()

    # def _wait_for_initialisation(self, timeout=2):
    #     for ton, to in self.__dict__.items():
    #         try:
    #             iswaitable = isinstance(to, InitialisationWaitable)
    #             if iswaitable:
    #                 to._wait_for_initialisation()
    #         except:
    #             pass

    def _wait_for_initialisation(self):
        """Block until every child that needs it (anything implementing the
        `InitialisationWaitable` protocol -- see `eco.elements.protocols`)
        confirms it's actually ready (e.g. `AdjustablePvEnum`/`DetectorPvEnum`
        resolving their deferred enum connection, see those classes).

        Deliberately sequential, *not* a thread pool: this method recurses
        (an `Assembly` child's own `_wait_for_initialisation` may in turn call
        this same method on its own children), and it is itself commonly
        invoked from inside `Namespace.init_all(background=True)`'s own
        `ThreadPoolExecutor` (up to `background_max_workers` names in
        parallel, default 8). A thread pool at *every* level of that
        recursion multiplies out fast -- one `EventReceiver` alone has ~47
        pulser/output children, each in turn an `Assembly` with its own ~8
        fields; nesting a pool at each level, under 8 already-concurrent
        top-level components, peaks in the thousands of live OS threads for
        a single `init_all(background=True)` call. That's a real resource-
        exhaustion risk (thread-stack memory, OS thread-count limits) for a
        surprisingly small extra win: the actual speedup comes from
        `AdjustablePvEnum`/`DetectorPvEnum` deferring their connection wait
        out of `__init__` in the first place (see their class docstrings) --
        measured on real EVR PVs, that alone is ~30x with zero threading;
        *also* threading this wait loop only adds a further ~1.5x on top,
        not worth multiplying thread count by the tree's fan-out for.

        Each child's failure is still caught individually rather than
        aborting the whole pass: the *previous* version of this method (a
        plain `for` loop, still true today aside from this) stopped at the
        first exception and silently never even attempted the remaining
        siblings. This raises one exception naming every child that failed,
        once all of them -- not just the ones before the first failure --
        have been given the chance to resolve.
        """
        items = []
        for item in self.status_collection.get_list():
            if isinstance(item, Assembly) and (item in _initializing_assemblies):
                continue
            if isinstance(item, InitialisationWaitable):
                if isinstance(item, Assembly):
                    _initializing_assemblies.append(item)
                items.append(item)

        errors = {}
        for item in items:
            try:
                item._wait_for_initialisation()
            except Exception as e:
                errors[item] = e
        if errors:
            names = ", ".join(
                str(getattr(it, "name", None) or it) for it in errors
            )
            first_exc = next(iter(errors.values()))
            raise RuntimeError(
                f"_wait_for_initialisation failed for {len(errors)} "
                f"component(s) of '{self.alias.get_full_name()}': {names}"
            ) from first_exc

    def _run_cmd(self, line, silent=True):
        if silent:
            print(f"Starting following commandline silently:\n" + line)
            with open(os.devnull, "w") as FNULL:
                subprocess.Popen(
                    line, shell=True, stdout=FNULL, stderr=subprocess.STDOUT
                )
        else:
            subprocess.Popen(line, shell=True)

    def _get_elog(self):
        if hasattr(self, "_elog") and self._elog:
            return self._elog
        elif hasattr(self, "__elog") and self.__elog:
            return self.__elog
        elif eco.defaults.ELOG:
            return eco.defaults.ELOG
        else:
            return None

    # Subclasses with a purpose-built view (e.g. AxisPTZ's live video
    # stream, not just its generic property grid) set this to the name of
    # a zero-arg widget-building method on the instance -- by convention
    # named `_widget_<something>` (e.g. `_widget_viewer`), so every
    # widget a device offers is findable by that prefix alone; widget()
    # below calls the named one instead of the generic _widget_assembly()
    # dispatch. None (the default) means "no override -- use
    # _widget_assembly()". This is the "_default_widget string" hook: keep
    # the override method itself doing its own is_notebook()-style
    # dispatch to the right toolkit, same as _widget_assembly() does, so
    # callers get the same "just works in a notebook or a Qt session"
    # behavior either way.
    _default_widget = None

    def widget(self, show_hidden: bool = False, normal: bool = False, **kwargs):
        """The widget for this object: `_default_widget`'s override if one
        is set (and `normal` isn't True), else the generic property grid
        (`_widget_assembly()`). Any subclass may also override `widget()`
        itself directly instead of going through `_default_widget` --
        both are valid ways to change what plain `.widget()` returns.

        normal=True: skip any `_default_widget` override and always
        return the plain property-grid widget -- for a caller that
        specifically wants that regardless of what plain `.widget()`
        would otherwise dispatch to. Needed by e.g. a special-purpose
        viewer's own "Settings"/normal-view button (see
        eco.widgets.camera_stream_qt.AxisPTZStreamQt._open_settings):
        without this, `self.cam.widget()` there would just reopen the
        same special viewer it was clicked from, since `_default_widget`
        has no notion of "the widget that's asking already knows about
        the override and wants the other one" otherwise."""
        if not normal:
            override_name = getattr(type(self), "_default_widget", None)
            if override_name:
                override = getattr(self, override_name, None)
                if callable(override):
                    return override()

        return self._widget_assembly(show_hidden=show_hidden, **kwargs)

    def _widget_assembly(self, show_hidden: bool = False, **kwargs):
        """The generic property-grid widget -- what `widget()` falls back
        to whenever `_default_widget` isn't set (or `normal=True` was
        asked for). Named to match eco's other underscore-prefixed,
        `_widget_`-prefixed override hooks (see `widget()`'s own
        docstring, and `_widget_svg_panel` for the SVG-panel equivalent on
        `show()`); gives `eco.widgets.containers.assembly_widget()` (and
        anyone else) a stable way to ask for "the assembly widget, no
        matter what `widget()` itself currently does"."""
        from eco.utilities.utilities import is_notebook

        if is_notebook():
            from eco.widgets.display_widget import make_assembly_widget

            return make_assembly_widget(self, show_hidden=show_hidden)
        else:
            try:
                from eco.widgets.display_qt import make_assembly_qt_window

                return make_assembly_qt_window(self, show_hidden=show_hidden)
            except ImportError:
                from eco.widgets.display_tk import make_assembly_tk_window

                return make_assembly_tk_window(self, show_hidden=show_hidden)

    def mark_beamline(
        self, name, types, z_source=None, z_sample=None, kind="optic",
        description="", parent=None, z_source_end=None, z_sample_end=None,
    ):
        """Tag `name` (an attribute of this assembly -- a real appended
        component, a still-lazy/unresolved one, or a pure position marker
        with no control-system object at all) as belonging to one or more
        logical beamlines/views.

        `types` is one or more *paths*, each 1+ levels deep -- the first
        level is the "type", any further ones organisational "subtypes"
        (e.g. "front_end", "optics", "hutch") that `namespace.beamline`'s
        dotted attribute access navigates by prefix (`namespace.beamline.
        fel` matches every "fel" path regardless of subtype; `namespace.
        beamline.fel.front_end` matches only that subtype). A component can
        carry several paths at once -- e.g. a KB mirror tagged only
        ``("fel-device", "optics")`` and a valve tagged only
        ``("fel-vacuum", "optics")`` both show up in ``beamline_view(types=
        [("fel-device", "optics"), ("fel-vacuum", "optics")])``, while a
        shared umbrella tag (both *also* carrying plain "fel") lets
        ``namespace.beamline.fel`` show both with nothing further to ask
        for. See `eco.elements.beamline_view._as_type_paths` for the exact
        accepted shapes (bare string / tuple / list of either).

        Give either `z_source` [m, downstream of some beamline-wide
        reference the caller defines] or `z_sample` [mm, downstream of
        whatever local sample/reference point the caller defines] (or both).
        `kind` picks the glyph/colour (see `eco.elements.beamline_view`,
        e.g. "mirror", "slit", "valve", ...). `parent`: name of another
        already-`mark_beamline`-tagged position on *this same assembly* that
        this one nests under in the view (e.g. a KB pair's own ver/hor
        focus points nesting under the pair itself) -- for nesting under a
        position registered on a *different* (sub-)assembly, no `parent` is
        needed: the view's `unfold=True` (the default) already recurses
        into any matched component that is itself an `Assembly` with its
        own tagged positions.

        Purely additive bookkeeping -- does not append or construct
        anything itself (call this alongside `_append`/`add_component`/
        `append_obj`, not instead of them) and touches nothing else on this
        assembly: nothing changes here unless `beamline`/`beamline_view()`
        is actually used (on this assembly, or an ancestor unfolding into
        it).
        """
        from .beamline_view import register_beamline_position

        register_beamline_position(
            self._beamline_positions, name, types, z_source=z_source, z_sample=z_sample,
            kind=kind, description=description, parent=parent,
            z_source_end=z_source_end, z_sample_end=z_sample_end,
        )

    def beamline_view(self, types=None, ref="source", unfold=True):
        """Build a `eco.elements.beamline_view.BeamlineView` over this
        assembly's `mark_beamline`-tagged positions (and, if `unfold`
        (default), those of any sub-assembly reachable from a matched
        component that has tagged positions of its own -- see
        `mark_beamline`). `types`: one or more paths (see `mark_beamline`);
        a position matches if any of its own paths starts with any of
        these. `None` (default) matches everything -- equivalent to the
        `beamline` property below, minus its dotted navigation. `ref`: an
        arbitrary label for whatever frame the caller's `z_source`/
        `z_sample` values are actually in (this method doesn't interpret
        it, just passes it through for the view's own display/derivation
        logic).

        Explicit, one-shot alternative to `beamline` (below) for when you
        need to set `ref`/`unfold`, or match several unrelated paths at
        once, in one call rather than by dotted navigation. Lazily
        imported, like `widget()` above, so nothing about this assembly
        changes unless this is actually called.
        """
        from .beamline_view import BeamlineView, _as_type_paths

        prefixes = _as_type_paths(types) if types is not None else None
        return BeamlineView(self, prefixes=prefixes, ref=ref, unfold=unfold)

    @property
    def beamline(self):
        """The root `eco.elements.beamline_view.BeamlineView` over this
        assembly's `mark_beamline`-tagged positions (see there), matching
        *everything* by default -- but its whole point is dotted attribute
        navigation down into a specific type/subtype, e.g. ``namespace.
        beamline.fel`` (every "fel"-tagged position) or ``namespace.
        beamline.fel.front_end`` (just that subtype). Each step returns
        another `BeamlineView`, so it can be printed directly at any depth
        (its `__repr__` renders the diagram) or navigated further.

        A fresh, cheap view object every access (no caching) -- like
        `beamline_view()` above, lazily imported and inert unless actually
        used.
        """
        from .beamline_view import BeamlineView

        return BeamlineView(self)

    def _ipython_display_(self):
        """Show the interactive widget when this object is the result of a
        Jupyter cell, or passed to `display()`. Falls back to the normal
        repr for a terminal/non-notebook IPython session, or if building
        the widget fails.
        """
        from eco.utilities.utilities import is_notebook
        from IPython.display import display

        if is_notebook():
            try:
                display(self.widget())
                return
            except Exception as e:
                print(f"Could not build widget for {self.alias.get_full_name()}: {e}")
        print(repr(self))

    def show(self, in_window=False, exclude_group_ids=None, live=False, dock_in=None, sidecar_anchor=None):
        """Opens the interactive SVG viewer for this assembly.

        Two ways an assembly can have something to show, both ending up in
        `self._show_svg` (a filesystem path to the SVG to display):

        1. **Static panel**: `_show_svg` is set once, up front, to a
           hand-drawn SVG file that already exists on disk -- this is how
           the top-level beamline namespace works (see `bernina.py`'s
           `namespace._show_svg = ".../beamline_interact.svg"`). `show()`
           just displays it; there is nothing to build.
        2. **Dynamic panel**: the assembly instead implements a
           `_widget_svg_panel(self, live=False, **kwargs)` method that
           *builds* an SVG on demand (e.g. from its own live component
           tree) and returns the file path -- by convention, every SVG
           panel (now and in the future) is named `_widget_svg_panel`, the
           same `_widget_`-prefixed findability convention `widget()`
           itself uses for `_default_widget` overrides. See
           `PrepumpSystem._widget_svg_panel`/`XrayBeamline._widget_svg_panel`
           for real examples, both P&ID-style schematics generated from the
           assembly's own components. If `_show_svg` isn't already set (or
           `live=True` is requested, which always rebuilds so the snapshot
           is current -- building may touch EPICS for live valve/gauge
           state), `show()` calls `self._widget_svg_panel(live=live)`
           itself and caches the result in `_show_svg`. This means any
           assembly with a `_widget_svg_panel()` method works with a plain
           `.show()`/`.show(live=True)` call, no extra step required.

        Subclasses whose `_widget_svg_panel()` needs more than just `live=`
        (e.g. `XrayBeamline._widget_svg_panel(ref=, kinds=, live=)` to
        filter which component kinds are drawn) can still expose a
        friendlier `svg_panel(...)` wrapper that builds `_show_svg` itself
        with those extra arguments and then calls `self.show(...)` to
        actually launch the viewer -- `show()`'s own fallback only fires
        when `_show_svg` is still unset, so it won't clobber a panel a
        wrapper just custom-built. `show()` itself can also be overridden
        outright by a subclass (e.g. `PrepumpSystem.show()` defaulting
        `live=True`) -- same as `widget()`, both the method itself and its
        `_widget_`-prefixed hook are valid places to customize.

        Commands clicked in the SVG run against this assembly's own full
        alias name as namespace prefix, e.g. clicking a device labelled
        "slit_att" inside an assembly aliased "bernina.optics" runs
        "bernina.optics.slit_att" in the IPython session.

        Examples: `namespace.prepump.show()`, `namespace.prepump.show(live=True)`
        for green/red valve+gauge state, `namespace.beamline.svg_panel(live=True,
        kinds={"valve", "gauge"})` for a filtered, live-coloured beamline panel.

        dock_in (in_window=True only): an EcoDesktopApp instance -- embeds
        the viewer as a tiled dock in that window instead of a separate
        top-level one, e.g. `namespace.show(in_window=True, dock_in=app)`.
        sidecar_anchor (notebook only, e.g. "split-right"): opens the
        viewer in its own JupyterLab Sidecar panel instead of displaying
        inline in the current cell. See `eco.utilities.svg_interactor.
        launch_svg_viewer`'s docstring for both.

        With `live=True` and a `_widget_svg_panel()` hook, an already-open
        viewer (either `in_window=True`'s native window or the Jupyter/
        browser one) keeps redrawing every couple of seconds for as long
        as it stays open, by calling `_widget_svg_panel(live=True)` again
        each tick -- see `launch_svg_viewer`'s `refresh` -- so valve/gauge
        colours etc. stay current instead of
        freezing at whatever they were when the panel opened. Both viewers
        share one mechanism for this (a small script running inside the
        panel's own browser-engine process re-polls and reloads it, entirely
        independent of whatever the eco Python process' main thread happens
        to be doing at the time -- e.g. blocked inside a running macro's poll
        loop), so the panel keeps updating even while a long macro like
        `PrepumpSystem.pump_down()` is running against it.
        """
        build_svg = getattr(self, "_widget_svg_panel", None)
        if callable(build_svg) and (not getattr(self, "_show_svg", None) or live):
            self._show_svg = build_svg(live=live)

        svg_path = getattr(self, "_show_svg", None)
        if not svg_path:
            print(f"No _show_svg defined for {self.alias.get_full_name()}.")
            return

        from eco.utilities.svg_interactor import launch_svg_viewer

        return launch_svg_viewer(
            svg_path,
            in_window=in_window,
            namespace_prefix=self.alias.get_full_name(),
            exclude_group_ids=exclude_group_ids,
            refresh=(lambda: build_svg(live=True)) if (live and callable(build_svg)) else None,
            dock_in=dock_in,
            dock_name=self.alias.get_full_name(),
            sidecar_anchor=sidecar_anchor,
        )


import epics.pv
import time


class Monitor:
    def __init__(self, assembly):
        self.assembly = assembly
        self.data = {}
        self.callbacks = {}
        self.pvs = {}

    def start_monitoring(self):
        o = self.assembly.get_status(channeltypes=["CA"])
        # self.data = {k: [v] for k, v in o["status"].items()}
        self.channelkeys = {v: k for k, v in o["status_channels"].items()}
        self.pvs = {k: epics.pv.PV(v) for k, v in o["status_channels"].items()}
        # for cik, civ in epics.pv._PVcache_.items():
        #     if cik[0] in o["status_channels"].keys():
        #         tname = self.channelkeys[cik[0]]
        #         tpv = civ
        for tname, tpv in self.pvs.items():
            self.callbacks[tname] = tpv.add_callback(self.append)

    def stop_monitoring(self):
        for tname in self.pvs:
            self.pvs[tname].remove_callback(index=self.callbacks[tname])

    def append(self, pvname=None, value=None, timestamp=None, **kwargs):
        if not (self.channelkeys[pvname] in self.data):
            self.data[self.channelkeys[pvname]] = []
        ts_local = time.time()
        self.data[self.channelkeys[pvname]].append(
            {"value": value, "timestamp": timestamp, "timestamp_local": ts_local}
        )


def filter_names(name, stat_dict):
    out = {}
    for key, value in stat_dict.items():
        keys = key.split(".")
        if keys[0] == name:
            if len(keys) == 1:
                out[key] = value
        else:
            out[key] = value
    return out
