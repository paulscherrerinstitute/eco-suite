import itertools
from pathlib import Path
from datetime import datetime
import weakref
from .adjustable import AdjustableFS
from ..utilities.tables import format_table, section_row_styles
from ..utilities.datafiles import ensure_dir
import colorama

try:
    from inspect import getargspec
except:  # for python 3.12
    from inspect import getfullargspec as getargspec

import eco
from ansi2html import Ansi2HTMLConverter
from simple_term_menu import TerminalMenu

conv = Ansi2HTMLConverter()

global_memory_dir = None


def _menu_supports_preview_callable():
    """Whether the installed simple-term-menu accepts a Python callable (not
    just a shell-command string) for `TerminalMenu(preview_command=...)`
    (added in 1.4.0). Checked once, defensively -- an unknown/unparseable
    version is treated as unsupported so the picker never risks breaking."""
    try:
        from importlib.metadata import version as _pkg_version

        parts = _pkg_version("simple-term-menu").split(".")
        return (int(parts[0]), int(parts[1])) >= (1, 4)
    except Exception:
        return False


_MENU_SUPPORTS_PREVIEW = _menu_supports_preview_callable()

# top-level keys of a *per-entry* memory dict (as returned by
# `Memory.get_memory()`, i.e. one `<timestamp>.json` file's contents) that
# are bookkeeping, never a `status_collection` selection name -- every other
# top-level key has always been exactly one captured selection's data, for
# every entry ever written (see `Memory.entry_selection_names`). Distinct
# from the *index* (`_memories.json`/`Memory.memories()`), whose per-key
# records use "message"/"categories"/"date" and are unaffected by this.
_RESERVED_ENTRY_KEYS = {"status", "memorized_attributes", "date"}


def set_global_memory_dir(dirpath, mode="w"):
    globals()["global_memory_dir"] = Path(dirpath).expanduser()


def get_memory(name):
    if not (global_memory_dir is None):
        return Memory(name)


class Memory:
    def __init__(
        self,
        obj,
        memory_dir=global_memory_dir,
        categories={"recall": ["settings"], "track": ["display"]},
        change_serially=False,
    ):
        self.obj_parent = weakref.ref(obj)
        self.categories = categories
        # per-instance default for recall()'s own `change_serially` -- see
        # recall()'s docstring. `False` here matches recall()'s original
        # hardcoded default, so any Memory that doesn't opt in (the vast
        # majority) behaves exactly as before this existed. Devices whose
        # underlying comms can't handle several near-simultaneous writes
        # (e.g. an HTTP PTZ camera juggling one connection per axis, or a
        # hexapod controller that drops overlapping axis setpoints) set this
        # via `Assembly(..., memory_change_serially=True)` instead of every
        # caller having to remember `recall(change_serially=True)`.
        self.change_serially = change_serially
        if not memory_dir:
            memory_dir = global_memory_dir
        self.base_dir = Path(memory_dir)
        self.obj_parent().presets = Presets(self)

    def setup_path(self):
        name = self.obj_parent().alias.get_full_name(joiner=None)
        self.dir = Path(self.base_dir) / Path("/".join(reversed(name)))
        try:
            # group-writable + setgid, so another pgroup member can store their
            # own memories/presets for the same device -- eco.utilities.datafiles
            ensure_dir(self.dir)
        except:
            print("Could not create memory directory")
        self._memories = AdjustableFS(
            self.dir / Path("memories.json"), default_value={}
        )
        self._presets = AdjustableFS(self.dir / Path("presets.json"), default_value={})

    def memories(self, indices=None, search_key=None):
        self.setup_path()
        mem = self._memories()
        memkeys = list(mem.keys())
        if indices is None:
            indices = range(len(mem))
        mems = []
        for index in indices:
            tkey = memkeys[index]
            tmem = mem[tkey]
            cats = list(itertools.chain.from_iterable(tmem["categories"].values()))
            tmem_all = self.get_memory(key=tkey)
            if search_key is not None:
                tmem_sel = {
                    tk: {ttk: ttv for ttk, ttv in tv.items() if search_key in ttk}
                    for tk, tv in tmem_all.items()
                    if tk in cats
                }
            else:
                tmem_sel = tmem_all
            tmem.update(tmem_sel)
            mems.append(tmem)
        return mems

    def plot_parameter(self, parameter_name, group_name="settings"):
        mem = self.memories(search_key=parameter_name)
        date = []
        value = []
        message = []
        for tmem in mem:
            try:
                tdate = datetime.fromisoformat(tmem["date"])
                tval = tmem[group_name][parameter_name]
                tmess = tmem["message"]
                date.append(tdate)
                value.append(tval)
                message.append(tmess)
            except:
                pass

        return date, value, message

    def __str__(self):
        self.setup_path()
        mem = self._memories()
        a = []
        for n, (key, content) in enumerate(mem.items()):
            row = [n]
            t = datetime.fromisoformat(key)
            row.append(t.strftime("%Y-%m-%d: %a %-H:%M"))
            row.append(content["message"])
            a.append(row)

        return format_table(a, headers=["Index", "Time", "Message"])

    def __call__(self, index=None, include_parents=False, **kwargs):
        """Interactive terminal picker over stored memories -- recalls
        whichever one is picked (or `index` directly, non-interactively,
        same as before `include_parents` existed).

        include_parents (bool, optional): also list memories saved on an
            ancestor Assembly that happen to cover this object's own
            components (see `ancestor_memories`/`get_ancestor_recall_dict`),
            prefixed with the ancestor's name -- the terminal equivalent of
            the memory widget's "show parent memories too" toggle. Picking
            one of those recalls just this object's own slice of it,
            through this object's own `.memory` (never the ancestor's),
            going through the normal interactive per-row review/confirm
            just like any other recall. Defaults to False, so existing
            callers see no change.
        """
        if index is None:
            self.setup_path()
            mem = self._memories()
            keys = list(mem.keys())
            a = []
            # entries[i] resolves row i: None for one of this object's own
            # stored memories (index into `keys`), or (ancestor,
            # ancestor_key) for a parent-sourced one (only ever populated
            # when include_parents=True).
            entries = [None] * len(keys)
            # recall_dicts[i]: that row's recall dict, computed once up
            # front (cheap -- reads one small JSON file per entry, no live
            # device I/O) rather than inside `_preview` on every highlight
            # change -- see the prefetch below for why.
            recall_dicts = [None] * len(keys)
            for n, (key, content) in enumerate(mem.items()):
                t = datetime.fromisoformat(key)
                row = t.strftime("%Y-%m-%d: %a %H:%M") + "   " + content["message"]
                a.append(row)
                try:
                    recall_dicts[n] = self.get_recall_dict(self.get_memory(key=key))
                except Exception:
                    recall_dicts[n] = {}

            if include_parents:
                from .assembly import iter_ancestor_assemblies

                for ancestor in iter_ancestor_assemblies(self.obj_parent()):
                    ancestor_memory = getattr(ancestor, "memory", None)
                    if ancestor_memory is None:
                        continue
                    ancestor_memory.setup_path()
                    for anc_key, anc_content in ancestor_memory._memories().items():
                        try:
                            anc_entry = ancestor_memory.get_memory(key=anc_key)
                            sliced = self.get_ancestor_recall_dict(ancestor, anc_entry)
                        except Exception:
                            continue
                        if not sliced:
                            continue
                        t = datetime.fromisoformat(anc_key)
                        row = (
                            f"[{ancestor.alias.get_full_name()}] "
                            + t.strftime("%Y-%m-%d: %a %H:%M")
                            + "   " + anc_content.get("message", "")
                        )
                        a.append(row)
                        entries.append((ancestor, anc_key))
                        recall_dicts.append(sliced)

            ind_cancel = len(a)
            a.append("--> do nothing")

            # Prefetch every component any listed entry might touch, once,
            # concurrently, *before* the interactive picker opens -- so
            # moving the highlight (which redraws the preview on every
            # keystroke) never re-reads a live device. Without this, each
            # arrow-key press re-reads every component of the newly
            # highlighted entry from scratch, which is what makes the
            # picker feel laggy for a large assembly or many stored
            # memories -- see `_prefetch_present_values`.
            all_names = itertools.chain.from_iterable(
                d.keys() for d in recall_dicts if d
            )
            prefetched = self._prefetch_present_values(all_names)

            def _preview(entry, rows=a, recs=recall_dicts, cache=prefetched):
                # best-effort preview for the highlighted entry, from the
                # prefetched cache only -- never raises into the menu's
                # render loop, never touches a live device on its own.
                try:
                    idx = rows.index(entry)
                    if idx >= len(recs):
                        return ""
                    return self.get_memory_difference_str(
                        recs[idx], show_changes_only=True, tablefmt="plain",
                        present_values=cache,
                    )
                except Exception as e:
                    return f"(preview unavailable: {e})"

            title_text = "↑/↓ navigate   enter: select   q/esc: quit"
            extra_kwargs = {"show_search_hint": True, "title": title_text}
            if _MENU_SUPPORTS_PREVIEW:
                extra_kwargs["preview_command"] = _preview
            try:
                menu = TerminalMenu(a, cursor_index=ind_cancel, **extra_kwargs)
            except TypeError:
                # installed simple-term-menu doesn't like one of the new
                # kwargs despite the version check above -- fall back to
                # exactly the picker that worked before this existed, plus
                # the quit-key hint (`title` is a basic, long-stable param,
                # safe on any version unlike the ones guarded above).
                menu = TerminalMenu(a, cursor_index=ind_cancel, title=title_text)
            print("Select memory to recall")
            picked = menu.show()
            if picked is None or picked == ind_cancel:
                return
            source = entries[picked] if picked < len(entries) else None
            if source is not None:
                ancestor, anc_key = source
                anc_entry = ancestor.memory.get_memory(key=anc_key)
                sliced = self.get_ancestor_recall_dict(ancestor, anc_entry)
                self.recall(input_obj={"settings": sliced}, selection="settings", **kwargs)
                return
            index = picked
        self.recall(memory_index=index, **kwargs)

    def widget(self, parent=None):
        """Open the memory browser GUI for this object -- Qt/ipywidgets/Tk,
        picked the same way `<assembly>.widget()` picks its own backend
        (see `eco.widgets.memory_widget.make_memory_browser`). Equivalent
        to (and reachable from) the "memories" button on the assembly
        display widget, but callable directly, e.g. from a terminal
        session: `my_assembly.memory.widget()`.
        """
        from eco.widgets.memory_widget import make_memory_browser

        return make_memory_browser(self.obj_parent(), parent=parent)

    def _get_elog(self):
        if hasattr(self, "_elog") and self._elog:
            return self._elog
        elif hasattr(self, "__elog") and self.__elog:
            return self.__elog
        elif eco.defaults.ELOG:
            return eco.defaults.ELOG
        else:
            return None

    def _resolve_capture_selection(self, selection):
        """Resolve `memorize`'s `selection=` argument, when explicitly given
        (see `memorize`'s own docstring for the `None` default, which never
        reaches here), into the concrete list of `status_collection`
        selection names to capture.

        `"all"` discovers every selection currently registered on the
        parent object *live*, via `status_collection.get_selections_names()`
        -- nothing needs to be pre-declared in `self.categories` for this to
        work, so a brand new selection tagged only moments ago via
        `Assembly._append(..., setting_groups=...)` is picked up immediately.
        """
        if selection == "all":
            return list(self.obj_parent().status_collection.get_selections_names())
        if isinstance(selection, str):
            return [selection]
        return list(selection)

    def entry_selection_names(self, mem):
        """Which `status_collection` selection names a *per-entry* memory
        dict `mem` (as returned by `get_memory()`, i.e. the contents of one
        `<timestamp>.json` file) actually captured.

        Every such file, for every entry ever written (old or new), has
        always had exactly one top-level key per captured selection, plus a
        handful of fixed bookkeeping keys (`_RESERVED_ENTRY_KEYS`) -- so this
        needs no format-specific fallback chain and works identically on the
        oldest stored memory and the newest: just the entry's own top-level
        keys, minus the reserved ones. Never touches the file on disk --
        purely a read-side derivation.
        """
        return [k for k in mem if k not in _RESERVED_ENTRY_KEYS]

    def _prefetch_present_values(self, names, max_workers=8):
        """Read every named component's current value concurrently, once.
        Returns {name: value_or_None} -- a read that raises is recorded as
        None rather than propagating, since this is only ever used for
        best-effort preview display (see `__call__`'s picker), never for an
        actual recall decision. Mirrors the same prefetch-before-interacting
        pattern `eco.widgets.display_qt`/`display_widget`'s own
        `_prefetch_values` already use, for the same reason: without it,
        each interactive step (there: opening the widget; here: moving the
        picker's highlight to a new entry) re-reads every live device from
        scratch, which is what makes a large assembly / many stored
        memories feel laggy to navigate.
        """
        from concurrent.futures import ThreadPoolExecutor

        names = list(dict.fromkeys(names))  # dedupe, keep first-seen order
        values = {}
        if not names:
            return values

        def _read(name):
            try:
                return name2obj(self.obj_parent(), name).get_current_value()
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=min(max_workers, len(names))) as ex:
            futures = {ex.submit(_read, n): n for n in names}
            for fut, n in futures.items():
                values[n] = fut.result()
        return values

    def ancestor_memories(self):
        """[(ancestor, ancestor.memory), ...] nearest-first for every
        ancestor Assembly of this object (see
        `eco.elements.assembly.iter_ancestor_assemblies`) that itself has a
        `.memory`. An object can have more than one direct parent (the same
        component appended into more than one Assembly), so this can surface
        ancestors from more than one branch. Used to find memories saved
        *for a parent* that happen to also cover (some of) this object's own
        components -- see `get_ancestor_recall_dict`.
        """
        obj = self.obj_parent()
        if obj is None:
            return []
        from .assembly import iter_ancestor_assemblies

        out = []
        for ancestor in iter_ancestor_assemblies(obj):
            ancestor_memory = getattr(ancestor, "memory", None)
            if ancestor_memory is not None:
                out.append((ancestor, ancestor_memory))
        return out

    def get_ancestor_recall_dict(self, ancestor, ancestor_mem, selection=None):
        """The slice of an ancestor assembly's own stored, per-entry memory
        dict `ancestor_mem` (as returned by `ancestor.memory.get_memory()`)
        that belongs to *this* object -- every key of
        `ancestor.memory.get_recall_dict(ancestor_mem, selection=selection)`
        that is prefixed by this object's dotted path under `ancestor`
        (`self.obj_parent().alias.get_full_name(base=ancestor)`), re-keyed
        relative to this object the same way this object's own recall dicts
        already are keyed -- so the result is usable exactly like a normal
        recall dict: previewable with `get_memory_difference_str`, or
        recalled through *this* object's own `recall(input_obj={"settings":
        result})`, never through the ancestor's `Memory` (the whole point is
        applying it only to this object's own components).

        Empty (not raising) if `ancestor_mem` doesn't cover this object at
        all -- callers use that to skip listing an ancestor's memory that's
        irrelevant to this object.
        """
        obj = self.obj_parent()
        prefix = obj.alias.get_full_name(base=ancestor) + "."
        full = ancestor.memory.get_recall_dict(ancestor_mem, selection=selection)
        return {
            key[len(prefix) :]: val
            for key, val in full.items()
            if key.startswith(prefix)
        }

    def memorize(
        self,
        message=None,
        attributes={},
        force_message=True,
        preset_varname=None,
        to_elog=True,
        selection=None,
    ):
        """Save the current state of this object's memorizable components as
        a new, timestamped memory entry (message/attributes/force_message/
        preset_varname/to_elog: unchanged from before `selection` existed).

        selection (str, "all", iterable of str, or None, optional): which
            `status_collection` selection(s) to capture into this memory.
            `None` (the default) preserves this method's original,
            unconditional behavior -- capture every group in
            `self.categories` (both "recall" and "track") -- so any existing
            caller that never passes this writes a byte-identical entry to
            before this parameter existed. Pass an explicit tag (e.g.
            "motor_settings"), `"all"` (every selection currently registered
            on the parent object, discovered live), or a list to opt into a
            more surgical, single-purpose memory instead.
        """
        self.setup_path()
        if selection is None:
            # unchanged legacy default: capture every configured group,
            # exactly as before `selection` existed.
            resolved = list(itertools.chain.from_iterable(self.categories.values()))
            categories_field = self.categories
        else:
            resolved = self._resolve_capture_selection(selection)
            categories_field = {"recall": resolved, "track": []}
        stat_now = {}
        allstat = self.obj_parent().get_status(
            base=self.obj_parent(), selections=resolved
        )
        stat_now["status"] = allstat["status"]
        for trec in resolved:
            stat_now[trec] = allstat["selections"][trec]

        stat_now["memorized_attributes"] = attributes
        key = datetime.now().isoformat()
        stat_now["date"] = key
        mem = self._memories()
        if force_message:
            while not message:
                message = input(
                    "Please enter a message associated to this memory entry:\n>>> "
                )
        mem[key] = {
            "message": message,
            "categories": categories_field,
            "date": key,
        }
        if preset_varname:
            mem[key].update({"presetname": preset_varname})
        tmp = AdjustableFS(self.dir / Path(key + ".json"))
        tmp(stat_now)
        self._memories(mem)
        print(f"Saved memory for {self.obj_parent().alias.get_full_name()}: {message}")
        print(f"memory file:  {tmp.file_path.as_posix()}")
        if to_elog:
            elog = self._get_elog()
            elog.post(
                f"Saved memory for {self.obj_parent().alias.get_full_name()}: {message}",
                tmp.file_path,
                text_encoding="markdown",
            )

    def get_memory(self, input_obj=None, index=None, key=None, filter_existing=True):
        if not input_obj is None:
            if type(input_obj) is dict:
                mem_full = input_obj
            else:
                tmp = AdjustableFS(Path(input_obj))
                mem_full = tmp()
        else:
            self.setup_path()
            if not (index is None):
                key = list(self._memories().keys())[index]
            tmp = AdjustableFS(self.dir / Path(key + ".json"))
            mem_full = tmp()
        if filter_existing:
            mem_filt = {}
            for tkey, tval in mem_full.items():
                if tkey in ["settings", "status_indicators"]:
                    mem_filt[tkey] = {}
                    for ttkey, ttval in tval.items():
                        try:
                            name2obj(self.obj_parent(), ttkey)
                            mem_filt[tkey][ttkey] = ttval
                        except KeyError:
                            ...
                else:
                    mem_filt[tkey] = tval

            return mem_filt
        else:
            return mem_full

    def clear_memory(self, index=None, key=None):
        if not (index is None):
            key = list(self._memories().keys())[index]
        if key is None:
            raise Exception("memory key or index to be deleted needs to be specified!")
        mem = self._memories.get_current_value()
        mem.pop(key)
        self._memories.set_target_value(mem).wait()

    def get_recall_dict(self, mem, selection=None):
        """Merge the selection(s) of a stored, per-entry memory dict `mem`
        (as returned by `get_memory`) into one flat {name: value} dict.

        `selection`:
            - `None` (the default -- and, before `selection` existed, the
              only behavior `recall()` had): merge every group in
              `self.categories["recall"]`.
            - `"all"`: merge every selection this *specific* entry actually
              captured (see `entry_selection_names`) -- can be a superset of
              `self.categories["recall"]`, e.g. for a memory saved with
              `memorize(selection="all")`.
            - a string or iterable of strings: merge just that name/those
              names.

        Only groups actually present on `mem` are used: an older memory may
        predate a group added later, or use a since-renamed group name (e.g.
        real stored memories from before this codebase's "display" group was
        named that use "status_indicators" instead) -- such groups are
        silently skipped rather than raising.
        """
        if selection is None:
            names = self.categories["recall"]
        elif selection == "all":
            names = self.entry_selection_names(mem)
        elif isinstance(selection, str):
            names = [selection]
        else:
            names = list(selection)
        rec = {}
        for name in names:
            rec.update(mem.get(name, {}))
        return rec

    def recall(
        self,
        memory_index=None,
        input_obj=None,
        key=None,
        wait=True,
        show_changes_only=True,
        set_changes_only=True,
        check_limits=True,
        change_serially=None,
        force=False,
        selection=None,
    ):
        """Recall a memory_index, from an index in the default meory list, from a
        dictionary containing the memory information, or from a path to a file containing the memory.

        Args:
            memory_index (integer, optional): index in memory list. Defaults to None.
            input_obj (dictionary or string, optional): direct passing memory as dict or s filepath (string) to the memory file. Defaults to None.
            key (string, optional): key of memory in memory list (if not defined by the index). Defaults to None.
            wait (bool, optional): Wait for the memory recall changes to complete. Defaults to True.
            show_changes_only (bool, optional): in rpreview show only changes that are different to present setting. Defaults to True.
            set_changes_only (bool, optional): setting only the changes that changed. Defaults to True.
            check_limits (bool, optional): check limits before changing. Defaults to True.
            change_serially (bool or None, optional): change and wait each change
                after each other, not simultaneously. `None` (the default) falls
                back to this `Memory`'s own instance default (`self.change_serially`,
                itself `False` unless the assembly was built with
                `memory_change_serially=True` -- see `Assembly.__init__`), so any
                existing caller that never passes this recalls exactly as it
                always has. Passing `True`/`False` explicitly always overrides the
                instance default for that one call, same as before this existed.
            force (bool, optional): force the change without previous preview. Defaults to False.
            selection (str, "all", iterable of str, or None, optional): which
                `status_collection` selection(s) to recall (see
                `get_recall_dict`). `None` (default) preserves this method's
                original behavior -- merge every group in
                `self.categories["recall"]` -- so any existing caller that
                never passes this recalls exactly what it always has. `"all"`
                recalls every selection this *specific* stored memory
                actually captured (which can be a superset of
                `self.categories["recall"]`, e.g. for a memory saved with
                `memorize(selection="all")`). A string or list recalls just
                that name/those names.

        Returns:
            _type_: _description_
        """
        if change_serially is None:
            change_serially = self.change_serially
        # if input_obj:
        mem = self.get_memory(
            index=memory_index,
            key=key,
            input_obj=input_obj,
        )

        # a memory with more than one recall-eligible selection and no
        # explicit `selection=` from the caller gets an extra "which one?"
        # prompt here, so a terminal recall can target e.g. just
        # "motor_settings" instead of always merging every recall group.
        # With today's default config (a single "settings" group) there is
        # never more than one available selection, so this never fires
        # unless an assembly actually opts into extra groups via
        # `memory_categories`/`setting_groups`.
        if selection is None and not force:
            available = [
                g for g in self.categories["recall"]
                if g in self.entry_selection_names(mem)
            ]
            if len(available) > 1:
                entries = list(available) + ["(all groups)"]
                group_menu = TerminalMenu(
                    entries,
                    cursor_index=len(entries) - 1,
                    title="Select which selection to recall:   (q/esc: quit)",
                )
                gidx = group_menu.show()
                if gidx is None:
                    return
                if gidx < len(available):
                    selection = available[gidx]

        rec = self.get_recall_dict(mem, selection=selection)

        if force:
            select = [True] * len(rec.items())
        else:
            select = self.select_from_memory(
                rec,
                show_changes_only=show_changes_only,
            )
            if not select:
                return
            if not input("would you really like to do the change? (y/n):") == "y":
                return

        changes = []
        for sel, (key, val) in zip(select, rec.items()):
            if sel:
                to = name2obj(self.obj_parent(), key)
                if set_changes_only:
                    if to.get_current_value() == val:
                        continue
                print(f"Changing {key} from {to.get_current_value()} to {val}")
                if "check" in getargspec(to.set_target_value).args:
                    changes.append(to.set_target_value(val, check=check_limits))
                else:
                    changes.append(to.set_target_value(val))
                if change_serially:
                    changes[-1].wait()
        if wait:
            for change in changes:
                change.wait()
            return
        else:
            return changes

    def recall_from_runtable(self): ...

    def get_memory_difference_str(
        self,
        recall_dict,
        select=None,
        ask_select=True,
        show_changes_only=False,
        tablefmt="plain",
        present_values=None,
    ):
        """Render a present-vs-recall diff table (recall_dict/select/
        ask_select/show_changes_only/tablefmt: unchanged from before
        `present_values` existed).

        present_values (dict, optional): {name: value} to use instead of a
        fresh `name2obj(...).get_current_value()` live read, for any name
        present in it -- lets a caller that already prefetched live values
        (e.g. the terminal picker's preview, see `_prefetch_present_values`)
        render this table without any further device I/O. `None` (default)
        always reads live, exactly as before this parameter existed.
        """

        if not select:
            select = [True] * len(recall_dict)
        table = []
        # top-level sub-component each row belongs to (same grouping key
        # `Assembly.get_display_str()` uses), so a multi-component recall's
        # rows visually group by section like the assembly display already
        # does -- built only from rows that actually end up in `table`
        # (post `show_changes_only` filtering), same as get_display_str.
        group_keys = []
        for n, (tsel, (key, recall_value)) in enumerate(
            zip(select, recall_dict.items())
        ):
            if present_values is not None and key in present_values:
                present_value = present_values[key]
            else:
                present_value = name2obj(self.obj_parent(), key).get_current_value()
            if tsel:
                tselstr = "x"
            else:
                tselstr = " "
            if present_value == recall_value:
                changed = False
                if tablefmt == "html":
                    comp_indicator = "=="
                else:
                    comp_indicator = (
                        colorama.Fore.GREEN
                        + colorama.Style.BRIGHT
                        + "=="
                        + colorama.Style.RESET_ALL
                    )
            else:
                changed = True
                if not tsel:
                    try:
                        comp_indicator = (
                            f"not changed ({recall_value-present_value:+g})"
                        )
                    except:
                        comp_indicator = f"not changed"
                else:
                    try:
                        tdiff = f"{recall_value - present_value:+g}"
                    except TypeError:
                        tdiff = "special"
                    if tablefmt == "html":
                        comp_indicator = f"{tdiff:s}"
                    else:
                        comp_indicator = (
                            colorama.Fore.RED
                            + colorama.Style.BRIGHT
                            + f"{tdiff:s}"
                            + colorama.Style.RESET_ALL
                        )
            if show_changes_only and (not changed):
                continue

            table.append([n, tselstr, key, present_value, comp_indicator, recall_value])
            group_keys.append(key.split(".", 1)[0])

        if len(table) == 0:
            return "No changes compared to memory!"
        return format_table(
            table,
            headers=[
                "",
                "",
                "name",
                "present",
                "difference",
                "memory",
            ],
            colalign=("decimal", "center", "left", "decimal", "center", "decimal"),
            tablefmt=tablefmt,
            row_styles=section_row_styles(group_keys),
        )

    def select_from_memory(self, recall_dict, show_changes_only=True):
        """Interactively choose which of `recall_dict`'s entries to
        actually apply before `recall()` sets anything -- a real terminal
        checkbox list (arrow keys to move, space to toggle, `a`/`n`/`i` to
        select all/none/invert, enter to confirm, q/escape to cancel),
        using `simple_term_menu`'s built-in multi-select mode. Replaces the
        previous letter-command flow (o/a/e followed by typed,
        comma-separated row numbers) with the same idea made directly
        interactive instead of addressed by typing numbers -- the
        index-number and static "x" columns the old table had are both
        gone; cursor position and the native "[x]"/"[ ]" prefix take over
        that job. Every row starts pre-selected, matching the old flow's
        default (`select = [True] * len`).

        Returns a list of bools, same length/order as `recall_dict` (True
        = recall this one), or a falsy value if nothing should be
        recalled (cancelled, or confirmed with nothing checked) -- same
        contract as before this rewrite, so `recall()` itself needed no
        changes. Entries hidden by `show_changes_only` are not shown as
        rows to toggle, but stay implicitly selected in the result (they
        are unchanged, so `recall()`'s own `set_changes_only` skip makes
        their selection state moot either way).
        """
        names = list(recall_dict.keys())
        present_values = self._prefetch_present_values(names)

        # rows actually shown, post show_changes_only filtering: (orig
        # index into `names`, name, present, recall_value, changed,
        # available)
        rows = []
        for i, (name, recall_value) in enumerate(recall_dict.items()):
            available = name in present_values
            present_value = present_values.get(name)
            changed = available and present_value != recall_value
            if show_changes_only and not changed:
                continue
            rows.append((i, name, present_value, recall_value, changed, available))

        if not rows:
            print("No changes compared to memory!")
            return [True] * len(names)

        name_w = max(len(r[1]) for r in rows)
        present_w = max(len(str(r[2])) for r in rows)

        def _row_text(name, present, recall_value, changed, available):
            if not available:
                diff = "?"
            elif not changed:
                diff = "=="
            else:
                try:
                    diff = f"{recall_value - present:+g}"
                except TypeError:
                    diff = "changed"
            present_str = str(present) if available else "?"
            return (
                f"{name:<{name_w}}  present: {present_str:>{present_w}}  "
                f"diff: {diff:^9}  memory: {recall_value}"
            )

        entries = [_row_text(r[1], r[2], r[3], r[4], r[5]) for r in rows]

        # `simple_term_menu` has no built-in bulk-toggle action, and its
        # `.show()` is a single blocking call with no hook to intercept
        # arbitrary keys mid-interaction -- so "select all"/"none"/"invert"
        # are built by making 'a'/'n'/'i' *additional* accept keys (see
        # `chosen_accept_key`), applying the requested bulk change to the
        # preselection, and re-showing a fresh menu with that updated
        # preselection -- a real "enter" is the only accept key that
        # actually breaks out of this loop.
        preselected = list(range(len(entries)))
        picked = None
        try:
            while True:
                menu = TerminalMenu(
                    entries,
                    multi_select=True,
                    multi_select_select_on_accept=False,
                    multi_select_empty_ok=True,
                    show_multi_select_hint=True,
                    preselected_entries=preselected,
                    accept_keys=("enter", "a", "n", "i"),
                    title=(
                        "space: toggle   a: all   n: none   i: invert   "
                        "enter: confirm & recall   q/esc: quit"
                    ),
                )
                picked = menu.show()
                accept_key = menu.chosen_accept_key
                if accept_key is None:
                    return None  # quit
                if accept_key == "enter":
                    break
                picked_now = set(picked or ())
                if accept_key == "a":
                    preselected = list(range(len(entries)))
                elif accept_key == "n":
                    preselected = []
                elif accept_key == "i":
                    preselected = [i for i in range(len(entries)) if i not in picked_now]
        except Exception:
            # a simple-term-menu too old for multi_select/preselected_entries/
            # accept_keys/chosen_accept_key (all foundational, long-stable
            # APIs, so this should be unreachable in practice) -- fail safe
            # by recalling everything shown, rather than leaving the caller
            # stuck with no way to choose at all.
            return [True] * len(names)

        if not picked:
            return None

        picked_set = set(picked)
        select = [True] * len(names)
        for vis_idx, row in enumerate(rows):
            select[row[0]] = vis_idx in picked_set
        return select

    def __repr__(self):
        return self.__str__()


class Presets:
    def __init__(
        self,
        memory,
    ):
        self._memory = memory
        self._setup_presets()

    def __dir__(self):
        return self._setup_presets()

    def __getattr__(self, name):
        self._setup_presets()
        if not name in self.__dict__.keys():
            raise AttributeError
        return self.__dict__[name]

    def _setup_presets(self):
        self._memory.setup_path()
        mem = self._memory._memories()
        presets = []
        for key, dat in mem.items():
            if "presetname" in dat.keys():
                self.__dict__[dat["presetname"]] = Preset(
                    self._memory, key, name=dat["presetname"]
                )
                presets.append(dat["presetname"])
        return presets

    def __str__(self):
        self._memory.setup_path()
        mem = self._memory._memories()
        table = []
        for key, dat in mem.items():
            if "presetname" in dat.keys():
                table.append([dat["presetname"], key, dat["message"]])

        return format_table(
            table,
            headers=[
                "Preset",
                "Date",
                "Message",
            ],
            colalign=("left", "left", "left"),
        )

    def __repr__(self):
        return self.__str__()


class Preset:
    def __init__(self, memory, key, name=None):
        self._memory = memory
        self._key = key
        self._name = name

    def get_memory(self):
        return self._memory.get_memory(key=self._key)

    def __call__(self, force=True):
        self._memory.recall(key=self._key, force=force)

    def __str__(self):
        s = f"Preset {self._name} - saved values compared to the present status\n"
        tmem = self._memory.get_memory(key=self._key)
        s += self._memory.get_memory_difference_str(tmem)
        return s

    def __repr__(self):
        return self.__str__()


def name2obj(obj_parent, name, delimiter="."):
    if type(name) is str:
        name = name.split(delimiter)
    obj = obj_parent
    for tn in name:
        if not tn or tn == "self":
            obj = obj
        else:
            obj = obj.__dict__[tn]

    return obj


class SelectionCatalog:
    """A named catalog of reusable presets for one `status_collection`
    ``selection`` tag (e.g. ``"schneider_motor_settings"``), independent of
    any single device instance -- unlike :class:`Memory`, which is bound to
    (and its storage path keyed by) one specific object's own namespace
    path.

    Where a `Memory` entry answers "what did *this* device look like at
    time T", a `SelectionCatalog` entry answers "what values should *any*
    object exposing this selection have" -- a reusable template/preset
    library, not a per-device history. Both read exactly the same
    `status_collection` selection machinery (`Assembly._append(...,
    setting_groups=...)`, `get_status(selections=[...])`), and both persist
    via :class:`~eco.elements.adjustable.AdjustableFS` JSON files -- same
    mechanism, different key.

    Each stored entry is a flat ``{relative_dotted_name: value}`` dict, in
    the same shape `Memory` already uses for one selection group -- paths
    relative to whatever *target* object you `apply()` it to (e.g.
    ``"schneider_settings.microstep_resolution"``, not tied to any one
    motor's full alias path) -- so a preset captured from one
    `MotorRecord(schneider=True)` instance applies cleanly to any other one
    that exposes the same selection with the same relative attribute names.

    Storage: one JSON file per catalog, ``<catalog_dir>/<selection_name>.json``.

    Usage manual (worked example: ``"schneider_motor_settings"``, seeded from
    ``eco.devices_general.schneider_mcode_presets.STAGE_PRESETS`` via
    ``seed_schneider_motor_settings_catalog`` -- see that module)
    ---------------------------------------------------------------------
    Prerequisite: the target object must actually expose the selection --
    e.g. a motor built with ``schneider=True``/``schneider={...}``
    (``eco.devices_general.motors.MotorRecord``)::

        from eco.devices_general.motors import MotorRecord
        m = MotorRecord("SARES20-MF1:MOT_14", name="my_zoom", schneider=True)

    1. Open the catalog (``catalog_dir=None`` resolves to
       ``<global_memory_dir>/_selections`` once
       ``set_global_memory_dir(...)`` has been called for the session, e.g.
       already done in production via ``bernina.py``'s ``path_memory``)::

           from eco.elements.memory import SelectionCatalog
           cat = SelectionCatalog("schneider_motor_settings")

    2. Browse what's available::

           cat.names()                              # ['qioptic_fusion_zoom', ...]
           print(cat)                                # table: preset / date / message
           cat.get_values("qioptic_fusion_zoom")      # the raw {name: value} dict

    3. Apply a preset to your motor::

           cat.apply(m, "qioptic_fusion_zoom")

       Writes every stored value (``description``, ``unit``, ``direction``,
       ``schneider_settings.run_current``, ``schneider_settings.
       microstep_resolution``, ...) onto ``m``, resolving each dotted name
       against ``m`` itself -- works on *any* compatible instance, not just
       the one it was captured from. ``set_changes_only=True`` (default)
       skips anything already matching, so re-applying is a safe no-op;
       ``wait=False`` returns without blocking for completion. A name that
       doesn't resolve on ``m`` (e.g. applying a schneider-flavoured preset
       to a plain motor) raises `KeyError` rather than partially applying.

    4. Save your own preset from a live object::

           cat.capture(m, "my_new_stage_name", message="what/why")

       Snapshots every item currently tagged with this selection on ``m``
       (both its own top-level fields and any tagged sub-assembly's, e.g.
       ``m.schneider_settings``) into a new named entry. Raises if the name
       already exists -- pass ``overwrite=True`` to replace.

    5. Register a preset without live hardware (what the seeding helper
       above uses internally)::

           cat.register("bench_test", {"schneider_settings.run_current": 20,
                                        "description": "bench"}, message="...")
    """

    def __init__(self, selection_name, catalog_dir=None):
        self.selection_name = selection_name
        if catalog_dir is None:
            if global_memory_dir is None:
                raise ValueError(
                    "no catalog_dir given and no global_memory_dir set "
                    "(eco.elements.memory.set_global_memory_dir)"
                )
            catalog_dir = Path(global_memory_dir) / "_selections"
        self.catalog_dir = Path(catalog_dir).expanduser()
        self.catalog_dir.mkdir(exist_ok=True, parents=True)
        try:
            self.catalog_dir.chmod(0o775)
        except Exception:
            pass
        self._file = AdjustableFS(
            self.catalog_dir / f"{selection_name}.json", default_value={}
        )

    def names(self):
        """Preset names currently stored in this catalog."""
        return list(self._file().keys())

    def get(self, name):
        """Raw stored entry for `name`: `{"values": {...}, "message":
        ..., "date": ...}`."""
        return self._file()[name]

    def get_values(self, name):
        """Just the `{relative_dotted_name: value}` dict for preset
        `name`."""
        return self.get(name)["values"]

    def register(self, name, values, message=None, overwrite=False):
        """Register a preset directly from a plain `{relative_dotted_name:
        value}` dict, without needing a live target object -- e.g. to seed
        a catalog from a hand-written reference table (see
        `eco.devices_general.schneider_mcode_presets.STAGE_PRESETS` for the
        source this was built to hold).
        """
        data = self._file()
        if name in data and not overwrite:
            raise ValueError(
                f"preset {name!r} already exists in catalog "
                f"{self.selection_name!r} (overwrite=True to replace)"
            )
        data[name] = {
            "values": dict(values),
            "message": message,
            "date": datetime.now().isoformat(),
        }
        self._file(data)

    def capture(self, target_obj, name, message=None, overwrite=False):
        """Capture `target_obj`'s *current live values* for this catalog's
        selection into a new named preset -- the live-object counterpart to
        `register()`. `target_obj` must have a `get_status()`
        (any `Assembly`) exposing `self.selection_name`.
        """
        allstat = target_obj.get_status(base=target_obj, selections=[self.selection_name])
        self.register(
            name,
            allstat["selections"][self.selection_name],
            message=message,
            overwrite=overwrite,
        )

    def apply(self, target_obj, name, wait=True, set_changes_only=True):
        """Apply preset `name`'s stored values onto `target_obj`, which
        must expose the same relative attribute names as whatever this
        selection was captured from (e.g. any `MotorRecord(schneider=True)`
        instance, once `motors.py`'s `schneider=` tagging is in place).
        Unresolvable names raise `KeyError` (via `name2obj`) rather than
        being silently skipped, so a preset built for the wrong kind of
        target fails loudly instead of partially applying.
        """
        values = self.get_values(name)
        changes = []
        for key, val in values.items():
            to = name2obj(target_obj, key)
            if set_changes_only and to.get_current_value() == val:
                continue
            changes.append(to.set_target_value(val))
        if wait:
            for change in changes:
                change.wait()
        return changes

    def remove(self, name):
        data = self._file()
        data.pop(name)
        self._file(data)

    def __str__(self):
        table = []
        for name, entry in self._file().items():
            table.append([name, entry.get("date", ""), entry.get("message", "") or ""])
        return format_table(table, headers=["Preset", "Date", "Message"])

    def __repr__(self):
        return self.__str__()
