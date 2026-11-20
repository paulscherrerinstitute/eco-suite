from numbers import Number
import os
from pathlib import Path

# from eco.elements.assembly import Assembly
import json


from eco.utilities.tables import format_table


class RunData:
    def __init__(
        self,
        pgroup_adj,
        path_search="/sf/bernina/data/{pgroup:s}/raw",
        load_kwargs={},
        name="",
        patch_path="/sf/bernina/data/{pgroup:s}/res/eco/run_patches.py",
    ):
        # super().__init__(name=name)
        # self._append(pgroup_adj, name="pgroup")
        self.pgroup = pgroup_adj
        self.path_search = path_search
        self.load_kwargs = load_kwargs
        self.loaded_runs = {}
        # append callbacks: [(name, func)] applied to every loaded run, see
        # add_append_callback; persisted in the pgroup's `patch_path`
        self.patch_path = patch_path
        self._append_callbacks = []  # [(name, callback as applied)]
        self._patch_state = None  # (patch file, mtime) last replayed

    def get_available_run_numbers(self):
        pgroup = self.pgroup.get_current_value()
        p = Path(self.path_search.format(pgroup=pgroup))
        runs = []
        for tp in p.iterdir():
            if not tp.is_dir():
                continue
            if tp.name[:3] == "run":
                numstring = tp.name.split("run")[1]
                if numstring.isdecimal():
                    runs.append(int(numstring))
        runs.sort()
        return runs

    def load_run(self, run_number, **kwargs):
        # Deferred: escape.swissfel eagerly imports dask/pandas/scipy/
        # IPython/xarray (~14s on a cold NFS-mounted env), and this module
        # is reached from eco.elements.assembly - i.e. from `import eco`
        # itself - so a module-level import here made that cost mandatory
        # for literally every eco import, not just the one RunData method
        # that actually loads a dataset. See eco.status_server.gui, whose
        # own docstring claims "no eco namespace import... starts in under
        # a second" - not true before this, since `from .client import
        # StatusServerClient` still runs eco/__init__.py first regardless.
        from escape.swissfel import load_dataset_from_scan

        if run_number < 0:
            run_number = self.get_available_run_numbers()[run_number]
            print(f"Loading run number {run_number}")
        tkwargs = self.load_kwargs.copy()
        tkwargs.update(kwargs)

        tks = {}
        for tk, tv in tkwargs.items():
            if type(tv) is str:
                tv = tv.format(pgroup=self.pgroup.get_current_value())
            tks[tk] = tv

        trun = load_dataset_from_scan(
            pgroup=self.pgroup.get_current_value(), run_numbers=[run_number], **tks
        )

        ###
        # self.adjust_group()

        self.apply_append_callbacks(trun)
        self.loaded_runs[run_number] = {"dataset": trun}
        self.__setattr__(f"run{run_number:04d}", trun)
        return trun

    # --- append callbacks / run patches -----------------------------------
    # Recipes for derived items (`dataset.append(func(dataset), name=name)`)
    # that every run gets when it is loaded. `append_run_patch(obj, name)`
    # builds the recipe from how `obj` was computed in the session on an
    # example run; `add_append_callback(name, func)` takes a function. Saved
    # recipes go to the pgroup's run patch file (res/eco/run_patches.py,
    # next to the namespace's patches.py), which is replayed before the next
    # run is loaded -- in this session if another session changed it, and in
    # every later one. Same entry format as Assembly._append(...,
    # add_patch=True), see eco.utilities.patchfile.

    def get_patch_file(self):
        return Path(self.patch_path.format(pgroup=self.pgroup.get_current_value()))

    def load_append_callbacks(self, force=False):
        """Replay the run patch file if it changed (or the pgroup did) since
        it was last replayed, rebuilding the callback list from it. A
        callback that was not saved is kept only until then."""
        from eco.utilities.patchfile import is_replaying, run_patch_file

        if is_replaying():
            return
        path = self.get_patch_file()
        try:
            state = (path, path.stat().st_mtime)
        except OSError:
            state = (path, None)
        if not force and state == self._patch_state:
            return
        # set first: a replayed entry may load a run itself
        self._patch_state = state
        if state[1] is None:
            return
        self._append_callbacks = []
        run_patch_file(path, globals={"runs": self})

    @property
    def append_callbacks(self):
        """`[(name, func)]` applied, in this order, to every loaded run."""
        self.load_append_callbacks()
        return list(self._append_callbacks)

    def append_run_patch(self, obj, name, save=True, apply_to_loaded=True):
        """Give every run the item `name`, computed the way `obj` was
        computed in this session from an example run::

            d = runs[3]
            i0 = d.bernina.mon_mono.intensity / 4
            runs.append_run_patch(i0, name="i0")

        becomes the append callback ::

            def _run_patch_i0(dataset):
                d = dataset
                i0 = d.bernina.mon_mono.intensity / 4
                return i0

        Only the statements `obj` depends on are taken from the session
        history (not whole cells, so a plot in the same cell is not
        repeated), back to the names bound to a loaded run (`d`), which
        become the run the callback is applied to. Session functions and
        values it uses are taken along, modules become imports. `obj` can
        also be an expression: ``runs.append_run_patch(d.x / 4, name="x4")``.

        The callback is applied to the runs already loaded (so a wrong
        reconstruction shows now, not on replay) and, with `save`, written to
        the run patch file. Returns the generated source.
        """
        from eco.utilities.patchfile import (
            function_source_from_history,
            record_interactive_patch,
        )
        from eco.elements.assembly import _capture_current_input
        from IPython import get_ipython

        ip = get_ipython()
        if ip is None:
            raise RuntimeError("append_run_patch needs an IPython session")
        transform = ip.input_transformer_manager.transform_cell
        history = [transform(c) for c in ip.history_manager.input_hist_raw[1:-1]]
        expr = None
        cell = _capture_current_input()
        if cell:
            found = _find_call_argument(transform(cell), "append_run_patch", name)
            if found:
                expr, before = found
                history.append(before)
        if expr is None:
            expr = _session_name_of(obj)
            if expr is None:
                raise ValueError(
                    "append_run_patch: cannot tell how this object was computed "
                    "-- pass the variable it is bound to, or the expression."
                )
        fname = "_run_patch_" + "".join(c if c.isalnum() else "_" for c in name)
        source = function_source_from_history(
            expr, history, ip.user_ns, is_parameter=_is_dataset, func_name=fname
        )
        namespace = {"__name__": "run_patches"}
        exec(compile(source, f"<run patch {name}>", "exec"), namespace)
        self.add_append_callback(name, namespace[fname], apply_to_loaded=apply_to_loaded)
        print(source)
        if save:
            path = self.get_patch_file()
            if record_interactive_patch(
                path,
                f"runs.append_callbacks.{name}",
                code=f"{source}\nruns.add_append_callback({name!r}, {fname})",
                known_names={"runs"},
                replay=False,
                preamble=_RUN_PATCH_PREAMBLE,
            ):
                self._patch_state = (path, path.stat().st_mtime)
                print(f"eco: saved to {path}")
        return source

    def add_append_callback(
        self, name, func, add_patch=False, apply_to_loaded=True, replay_origin=True
    ):
        """Have every run loaded from now on get `dataset.append(func(dataset),
        name=name)`; with `apply_to_loaded` also the runs already loaded.

        `add_patch=True` writes this to the run patch file
        (`get_patch_file()`): if `func` is a name in the session, as
        `runs.add_append_callback(name, <that name>)` preceded by the cell(s)
        that defined it (with `replay_origin`, see `Assembly._append`'s
        `patch_replay_origin`), otherwise (an inline lambda) as the whole
        cell being executed. See also `append_run_patch`.
        """
        from eco.utilities.patchfile import is_replaying, record_interactive_patch

        if not callable(func):
            raise TypeError(f"append callback for {name!r} is not callable: {func!r}")
        self.load_append_callbacks()
        cbs = self._append_callbacks
        for i, (n, _) in enumerate(cbs):
            if n == name:
                cbs[i] = (name, func)
                break
        else:
            cbs.append((name, func))
        if apply_to_loaded and not is_replaying():
            for run_number, entry in self.loaded_runs.items():
                ds = entry["dataset"]
                if hasattr(ds, "add_append_callback"):
                    ds.add_append_callback(name, func, run=True)
        if add_patch and not is_replaying():
            varname = _session_name_of(func)
            code = f"runs.add_append_callback({name!r}, {varname})" if varname else None
            path = self.get_patch_file()
            if record_interactive_patch(
                path,
                f"runs.append_callbacks.{name}",
                code=code,
                known_names={"runs"},
                replay=replay_origin,
                preamble=_RUN_PATCH_PREAMBLE,
            ):
                # our own write is not a reason to replay the file
                self._patch_state = (path, path.stat().st_mtime)

    def remove_append_callback(self, name, add_patch=False):
        """Stop applying `name` to runs loaded from now on (items already
        appended stay). `add_patch=True` records the removal in the run patch
        file too; otherwise it is back after the file is next replayed."""
        from eco.utilities.patchfile import is_replaying, record_interactive_patch

        self.load_append_callbacks()
        self._append_callbacks = [(n, f) for n, f in self._append_callbacks if n != name]
        if add_patch and not is_replaying():
            path = self.get_patch_file()
            if record_interactive_patch(
                path,
                f"runs.append_callbacks.{name}",
                code=f"runs.remove_append_callback({name!r})",
                known_names={"runs"},
                preamble=_RUN_PATCH_PREAMBLE,
            ):
                self._patch_state = (path, path.stat().st_mtime)

    def apply_append_callbacks(self, dataset, names=None):
        """Give `dataset` this pgroup's callbacks and run them (see
        `escape.DataSet.run_append_callbacks`). Returns `(applied, failed)`."""
        callbacks = self.append_callbacks
        if not callbacks:
            return [], []
        if not hasattr(dataset, "run_append_callbacks"):
            print(
                "eco: runs has append callbacks but this escape version's "
                "DataSet cannot run them -- update escape-fel (>= 0.3.6)."
            )
            return [], [n for n, _ in callbacks]
        for n, f in callbacks:
            dataset.add_append_callback(n, f)
        return dataset.run_append_callbacks(names=names)

    # def __dir__(self):
    #     l = [
    #         "get_available_run_numbers",
    #         "get_run",
    #         "load_kwargs",
    #         "load_run",
    #         "loaded_runs",
    #         "path_search",
    #         "pgroup",
    #     ]
    #     #     l = dir(self)
    #     l += [f"run{runno:04d}" for runno in self.get_available_run_numbers()]
    #     return l

    # def __getattribute__(self, name):
    #     if name in [f"run{runno:04d}" for runno in self.get_available_run_numbers()]:
    #         return self.get_run(int(name.split("run")[1]))
    #     else:
    #         return getattr(self, name)

    def adjust_group(self, subdir_type="scratch/.escape_parse_result"):
        os.system(
            "chgrp -R "
            + self.pgroup.get_current_value()[1:]
            + f" /sf/bernina/data/{self.pgroup.get_current_value()}/{subdir_type}"
        )

    def get_run(self, run_number, **kwargs):
        if run_number < 0:
            run_number = self.get_available_run_numbers()[run_number]
            print(f"Finding run number {run_number}")
        if run_number in self.loaded_runs.keys():
            return self.loaded_runs[run_number]["dataset"]
        else:
            return self.load_run(run_number, **kwargs)

    def __getitem__(self, run_number):
        return self.get_run(run_number)

    def __repr__(self):
        s = "<%s.%s object at %s>" % (
            self.__class__.__module__,
            self.__class__.__name__,
            hex(id(self)),
        )
        runnos = self.get_available_run_numbers()
        s += "\n"
        s += f"{len(runnos)} available from {min(runnos)} to {max(runnos)}."
        return s


_RUN_PATCH_PREAMBLE = (
    "# Run patch file -- appended to by runs.add_append_callback(..., "
    "add_patch=True)\n"
    "# in interactive eco sessions and replayed (with `runs` defined) before "
    "runs are\n"
    "# loaded, so every run gets the same derived items. Safe to hand-edit "
    "as long as\n"
    "# it stays valid Python; entries run in order, a failing one is "
    "skipped."
)


def _is_dataset(obj):
    # by class name: no escape import, and type() never resolves a lazy Proxy
    return any(
        c.__name__ == "DataSet" and c.__module__.startswith("escape")
        for c in type(obj).__mro__
    )


def _find_call_argument(cell, method, name):
    """(source of the first argument, source of the statements before it) of
    the `.<method>(...)` call in `cell` whose `name=` is `name` (or the only
    such call), or None."""
    import ast

    try:
        tree = ast.parse(cell)
    except SyntaxError:
        return None
    hits = []
    for k, st in enumerate(tree.body):
        for node in ast.walk(st):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, (ast.Attribute, ast.Name))
                and getattr(node.func, "attr", getattr(node.func, "id", None)) == method
            ):
                continue
            arg = node.args[0] if node.args else next(
                (kw.value for kw in node.keywords if kw.arg == "obj"), None
            )
            if arg is None:
                continue
            kwname = next((kw.value for kw in node.keywords if kw.arg == "name"), None)
            if kwname is None and len(node.args) > 1:
                kwname = node.args[1]
            literal = kwname.value if isinstance(kwname, ast.Constant) else None
            before = "\n".join(ast.get_source_segment(cell, s) for s in tree.body[:k])
            hits.append((literal, ast.get_source_segment(cell, arg), before))
    matching = [h for h in hits if h[0] == name] or (hits if len(hits) == 1 else [])
    if not matching:
        return None
    return matching[0][1], matching[0][2]


def _session_name_of(obj):
    """A public name bound to `obj` in the IPython session, or None."""
    try:
        from IPython import get_ipython

        ns = get_ipython().user_ns
    except Exception:
        return None
    preferred = getattr(obj, "__name__", None)
    if preferred and ns.get(preferred) is obj:
        return preferred
    for k, v in ns.items():
        if v is obj and not k.startswith("_"):
            return k
    return None


STATUS_DATA = {}


class StatusData:
    """Run status of the current pgroup: one run's status.json
    (`get_run_status`), or all runs as a table (`table`, `plot`), kept in a
    parquet cache next to the run table (see eco.acquisition.run_store).

    Status files are looked up in `res_search` first - the DAQ client's copy,
    which has every run - then in `path_search` (raw/, which since 2025 holds
    only some runs, and is all there is for older pgroups).
    """

    def __init__(
        self,
        pgroup_adj,
        path_search="/sf/bernina/data/{pgroup:s}/raw",
        status_search="aux/status.json",
        load_kwargs={},
        name="",
        res_search="/sf/bernina/data/{pgroup:s}/res/run_data/daq",
        cache_path="/sf/bernina/data/{pgroup:s}/res/run_data/run_table/{pgroup:s}_runs.parquet",
    ):
        # super().__init__(name=name)
        # self._append(pgroup_adj, name="pgroup")
        self.pgroup = pgroup_adj
        self.path_search = path_search
        self.res_search = res_search
        self.cache_path = cache_path
        self.status_search = status_search
        self.load_kwargs = load_kwargs
        self.loaded_statii = {}
        self._stores = {}
        STATUS_DATA[self.pgroup.get_current_value()] = self

    def _status_dirs(self, pgroup):
        return [
            Path(s.format(pgroup=pgroup))
            for s in (self.res_search, self.path_search)
            if s
        ]

    def _status_file(self, run_number):
        pgroup = self.pgroup.get_current_value()
        for d in self._status_dirs(pgroup):
            p = d / f"run{run_number:04d}" / self.status_search
            if p.exists():
                return p
        raise FileNotFoundError(
            f"no {self.status_search} for run {run_number} of {pgroup} in "
            + " or ".join(str(d) for d in self._status_dirs(pgroup))
        )

    def get_available_run_numbers(self):
        pgroup = self.pgroup.get_current_value()
        runs = set()
        for p in self._status_dirs(pgroup):
            if not p.is_dir():
                continue
            for tp in p.iterdir():
                if tp.name[:3] == "run" and tp.name[3:].isdecimal():
                    if (tp / Path(self.status_search)).exists():
                        runs.add(int(tp.name[3:]))
        return sorted(runs)

    @property
    def store(self):
        """The RunStore of the current pgroup."""
        from eco.acquisition.run_store import RunStore

        pgroup = self.pgroup.get_current_value()
        if pgroup not in self._stores:
            cache = self.cache_path.format(pgroup=pgroup) if self.cache_path else None
            self._stores[pgroup] = RunStore(self._status_dirs(pgroup), cache)
        return self._stores[pgroup]

    def table(self, columns=None, prefix=None, runs=None):
        """All runs' status as a DataFrame, one row per run plus a "time"
        column; see RunStore.table. Names are full names as in status.json,
        e.g. prefix="bernina.kb" for everything below kb."""
        return self.store.table(columns=columns, prefix=prefix, runs=runs)

    def plot(self, columns, runs=None, x="time", ax=None, **plot_kwargs):
        """Plot status values against time (x="time", run numbers as a
        second x axis) or run number (x="run", time as the second axis)."""
        from eco.acquisition.run_store import plot_table

        return plot_table(self.table(columns=columns, runs=runs), x=x, ax=ax, **plot_kwargs)

    def get_run_status(self, run_number, **kwargs):
        if run_number < 0:
            run_number = self.get_available_run_numbers()[run_number]
            print(f"Finding run number {run_number}")
        if run_number in self.loaded_statii.keys():
            return self.loaded_statii[run_number]
        else:
            return self.load_run_status(run_number, **kwargs)

    def load_run_status(self, run_number, **kwargs):
        if run_number < 0:
            run_number = self.get_available_run_numbers()[run_number]
            print(f"Loading run number {run_number}")
        with open(self._status_file(run_number), "r") as fh:
            r = json.load(fh)
        self.loaded_statii[run_number] = r
        return r


def _walk_status_items(base_obj, node, depth, include_hidden, _seen):
    """Recursively collect (name relative to base_obj, item) pairs from
    node's status_collection, unfolding nested assemblies while depth
    allows (depth<=1 stops unfolding further). Mirrors the traversal used
    by Assembly.get_tree/get_display_str, but callable at arbitrary depth
    and independent of the "display" selection when include_hidden=True.
    """
    rows = []
    display_names = set(node.status_collection.selections.get("display", {}).keys())
    for wref in node.status_collection._list:
        item = wref()
        if item is None or item is node:
            continue
        if id(item) in _seen:
            continue
        try:
            local_name = item.alias.get_full_name(base=node)
        except Exception:
            continue
        if not include_hidden and local_name not in display_names:
            continue
        _seen.add(id(item))
        try:
            name = item.alias.get_full_name(base=base_obj)
        except Exception:
            name = local_name
        is_nested = hasattr(item, "status_collection")
        if is_nested and depth > 1:
            sub_rows = _walk_status_items(base_obj, item, depth - 1, include_hidden, _seen)
            if sub_rows:
                rows.extend(sub_rows)
                continue
        rows.append((name, item))
    return rows


class RunStatusView:
    """Historical, run-scoped counterpart to an Assembly's live display
    repr (`get_display_str`/`__repr__`). Returned by RunStatusAccessor,
    e.g. `obj.run_status(1234)` or `obj.run_status[1234]` - not meant to
    be constructed directly.
    """

    def __init__(
        self, obj, run_number, dic, depth=None, include_hidden=False, compare_live=False,
        pgroup="auto", status_type="status_run_start", force_reload=False,
    ):
        self.obj = obj
        self.run_number = run_number
        self.dic = dic
        self.depth = depth
        self.include_hidden = include_hidden
        self.compare_live = compare_live
        # kept only so `_build_table` can look up the "settings" par_type
        # for this same run/pgroup/status_type on demand (see the selector
        # column below) -- cheap: `RunData.get_run_status` caches the whole
        # per-run status blob, so this hits that cache rather than
        # re-fetching, regardless of which par_type the view itself is for.
        self.pgroup = pgroup
        self.status_type = status_type
        self.force_reload = force_reload

    def _rows(self):
        obj = self.obj
        if not hasattr(obj, "status_collection"):
            return [(name, None) for name in self.dic]
        if not self.include_hidden and self.depth is None:
            # default: exactly the items the live repr would show
            items = obj.status_collection.get_list(selection="display")
            return [(item.alias.get_full_name(base=obj), item) for item in items]
        return _walk_status_items(obj, obj, self.depth or 1, self.include_hidden, set())

    def to_dict(self):
        """Raw stored values for this run, as a flat dict (no display formatting)."""
        return dict(self.dic)

    def _settings_names(self):
        """Names (relative to `self.obj`, same shape as `self.dic`'s keys)
        that are "settings" for this same run/pgroup/status_type -- used to
        mark the selector column: `x` for a recallable setting (matches
        what `apply_run_settings`/`Memory.recall` would actually apply),
        blank for a status-only value shown for context. Best-effort: `None`
        if it can't be determined, in which case the selector column is
        dropped entirely rather than shown always-blank. Cheap even though
        it's a second `_fetch_run_status_dict` call -- `RunData.
        get_run_status` caches the whole per-run status blob, so this reads
        from that cache instead of re-fetching, regardless of which
        par_type the view itself was built with.
        """
        try:
            settings_stat = self.obj._fetch_run_status_dict(
                run_number=[self.run_number],
                par_type="settings",
                force_reload=self.force_reload,
                pgroup=self.pgroup,
                status_type=self.status_type,
            )
            return set(settings_stat[self.run_number].keys())
        except Exception:
            return None

    def _live_present_values(self, names):
        """{name: live_current_value} for `names`, read concurrently via
        `Memory._prefetch_present_values` when `self.obj` has a `.memory`
        (so a run status with many components doesn't block on one live
        read at a time -- the same fix applied to the memory picker's own
        preview); falls back to a plain sequential read if there's no
        `.memory` to borrow the prefetch helper from. Best-effort: a read
        that fails is just absent from the returned dict, not raised.
        """
        memory = getattr(self.obj, "memory", None)
        if memory is not None:
            try:
                return memory._prefetch_present_values(names)
            except Exception:
                pass
        values = {}
        items_by_name = dict(self._rows())
        for name in names:
            item = items_by_name.get(name)
            if item is None:
                continue
            try:
                values[name] = item.get_current_value()
            except Exception:
                pass
        return values

    def _build_table(self, tablefmt="simple"):
        settings_names = self._settings_names()

        present_values = {}
        if self.compare_live:
            live_names = [
                name for name, item in self._rows()
                if item is not None and self.dic.get(name) is not None
            ]
            present_values = self._live_present_values(live_names)

        tab = []
        for name, item in self._rows():
            value = self.dic.get(name)
            is_placeholder = value is None
            if is_placeholder:
                prefix = name + "."
                if any(k.startswith(prefix) for k in self.dic):
                    value = "\x1b[3mnested - unfold with depth=/include_hidden=\x1b[0m"
                else:
                    value = ""
            typechar = ""
            unit = ""
            description = ""
            if item is not None:
                if hasattr(item, "status_collection"):
                    typechar = "↳"
                try:
                    unit = item.unit.get_current_value()
                except Exception:
                    pass
                try:
                    description = item.description.get_current_value()
                except Exception:
                    pass

            selector = "x" if (settings_names is not None and name in settings_names) else " "

            if self.compare_live:
                present = ""
                diff = ""
                if not is_placeholder and name in present_values:
                    present = present_values[name]
                    try:
                        changed = present != value
                    except Exception:
                        changed = True
                    if not changed:
                        diff = "=="
                    else:
                        try:
                            diff = f"{value - present:+g}"
                        except TypeError:
                            diff = "changed"
                row = [selector, name, present, diff, value, unit, typechar, description]
            else:
                row = [selector, name, value, unit, typechar, description]
            tab.append(row)

        if not tab:
            return ""
        if self.compare_live:
            headers = ["", "name", "present", "diff", "run value", "unit", "", "description"]
            maxcolwidths = [None, None, None, None, None, None, None, 50]
        else:
            headers = ["", "name", "value", "unit", "", "description"]
            maxcolwidths = [None, None, None, None, None, 50]
        if settings_names is None:
            # couldn't determine settings membership -- drop the column
            # rather than show it always-blank
            tab = [row[1:] for row in tab]
            headers = headers[1:]
            maxcolwidths = maxcolwidths[1:]
        return format_table(tab, headers=headers, tablefmt=tablefmt, maxcolwidths=maxcolwidths)

    def __getitem__(self, key):
        return self.dic[key]

    def __repr__(self):
        name = self.obj.alias.get_full_name()
        header = f"{name} run {self.run_number} status"
        if self.compare_live:
            header += "  [present vs run value, with difference"
        else:
            header += "  ["
        header += "; 'x' = a recallable setting, others are status only]"
        return header + "\n" + self._build_table()

    def _repr_html_(self):
        return self._build_table(tablefmt="html")


class MultiRunStatusView:
    """Several runs' historical status side by side, one value column per
    run (labelled by run number) -- the multi-run counterpart of
    `RunStatusView`, e.g. `obj.run_status([-2, -1])`. Optionally also a
    single "present" column when `compare_live=True` (the live value is
    the same regardless of which run you're comparing it against, so it
    only needs one column, with each run's own diff-from-present folded
    inline into that run's cell as "value (+diff)" rather than spending a
    whole extra column per run on it), and the same settings-vs-
    status-only selector column `RunStatusView` has. Not meant to be
    constructed directly; returned by `RunStatusAccessor.__call__`/
    `__getitem__` when given more than one run number. For raw,
    machine-readable multi-run data instead, use `.run_status.dict(
    run_number=[...], as_dataframe=True)`.
    """

    def __init__(
        self, obj, run_numbers, dics, depth=None, include_hidden=False, compare_live=False,
        pgroup="auto", status_type="status_run_start", force_reload=False,
    ):
        self.obj = obj
        self.run_numbers = list(run_numbers)
        self.dics = dics  # {run_number: {name: value}}
        self.depth = depth
        self.include_hidden = include_hidden
        self.compare_live = compare_live
        self.pgroup = pgroup
        self.status_type = status_type
        self.force_reload = force_reload

    # _rows/_settings_names/_live_present_values mirror RunStatusView's own
    # (not shared via a base class -- the two differ in exactly one place
    # each: no single self.dic to fall back on for _rows()'s "no
    # status_collection" branch, and only one of the several runs' settings
    # is needed since "is this a setting" doesn't vary per run for the same
    # live object).

    def _rows(self):
        obj = self.obj
        if not hasattr(obj, "status_collection"):
            names = set()
            for dic in self.dics.values():
                names.update(dic.keys())
            return [(name, None) for name in sorted(names)]
        if not self.include_hidden and self.depth is None:
            items = obj.status_collection.get_list(selection="display")
            return [(item.alias.get_full_name(base=obj), item) for item in items]
        return _walk_status_items(obj, obj, self.depth or 1, self.include_hidden, set())

    def _settings_names(self):
        try:
            settings_stat = self.obj._fetch_run_status_dict(
                run_number=[self.run_numbers[0]],
                par_type="settings",
                force_reload=self.force_reload,
                pgroup=self.pgroup,
                status_type=self.status_type,
            )
            return set(settings_stat[self.run_numbers[0]].keys())
        except Exception:
            return None

    def _live_present_values(self, names):
        memory = getattr(self.obj, "memory", None)
        if memory is not None:
            try:
                return memory._prefetch_present_values(names)
            except Exception:
                pass
        values = {}
        items_by_name = dict(self._rows())
        for name in names:
            item = items_by_name.get(name)
            if item is None:
                continue
            try:
                values[name] = item.get_current_value()
            except Exception:
                pass
        return values

    def to_dict(self):
        """Raw stored values across these runs, as {run_number: {name: value}}."""
        return {rn: dict(dic) for rn, dic in self.dics.items()}

    def _build_table(self, tablefmt="simple"):
        settings_names = self._settings_names()

        present_values = {}
        if self.compare_live:
            live_names = [
                name for name, item in self._rows()
                if item is not None
                and any(self.dics.get(rn, {}).get(name) is not None for rn in self.run_numbers)
            ]
            present_values = self._live_present_values(live_names)

        tab = []
        for name, item in self._rows():
            typechar = ""
            unit = ""
            description = ""
            if item is not None:
                if hasattr(item, "status_collection"):
                    typechar = "↳"
                try:
                    unit = item.unit.get_current_value()
                except Exception:
                    pass
                try:
                    description = item.description.get_current_value()
                except Exception:
                    pass

            selector = "x" if (settings_names is not None and name in settings_names) else " "
            row = [selector, name]
            if self.compare_live:
                row.append(present_values.get(name, ""))

            for rn in self.run_numbers:
                dic = self.dics.get(rn, {})
                value = dic.get(name)
                if value is None:
                    prefix = name + "."
                    if any(k.startswith(prefix) for k in dic):
                        value = "\x1b[3mnested\x1b[0m"
                    else:
                        value = ""
                elif self.compare_live and name in present_values:
                    present = present_values[name]
                    try:
                        changed = present != value
                    except Exception:
                        changed = True
                    if changed:
                        try:
                            diffstr = f"{value - present:+g}"
                        except TypeError:
                            diffstr = "changed"
                        value = f"{value} ({diffstr})"
                row.append(value)

            row.extend([unit, typechar, description])
            tab.append(row)

        if not tab:
            return ""
        headers = ["", "name"]
        if self.compare_live:
            headers.append("present")
        headers.extend([f"run {rn}" for rn in self.run_numbers])
        headers.extend(["unit", "", "description"])
        if settings_names is None:
            tab = [row[1:] for row in tab]
            headers = headers[1:]
        return format_table(tab, headers=headers, tablefmt=tablefmt, maxcolwidths=[None] * (len(headers) - 1) + [50])

    def __getitem__(self, run_number):
        return dict(self.dics[run_number])

    def __repr__(self):
        name = self.obj.alias.get_full_name()
        runs_str = ", ".join(str(rn) for rn in self.run_numbers)
        header = f"{name} runs {runs_str} status"
        if self.compare_live:
            header += "  [present vs each run's value, diff inline per run"
        else:
            header += "  ["
        header += "; 'x' = a recallable setting, others are status only]"
        return header + "\n" + self._build_table()

    def _repr_html_(self):
        return self._build_table(tablefmt="html")


class RunStatusAccessor:
    """`obj.run_status` - pick a run to see this object's historical status
    as a display view (like its live repr), with options to unfold nested
    (`depth=`) and normally-hidden (`include_hidden=`) components, and to
    compare against the live value (`compare_live=`). Tab-completable in
    IPython/Jupyter via `obj.run_status[<TAB>]`. Given more than one run
    number (e.g. `obj.run_status([-2, -1])`), returns one combined table
    with a value column per run instead (`MultiRunStatusView`). Use
    `.dict(...)` for the original, raw (optionally multi-run) dictionary/
    DataFrame output.
    """

    def __init__(self, obj):
        self._obj = obj

    def _pgroup_key(self, pgroup):
        if pgroup == "auto":
            return list(STATUS_DATA.keys())[0]
        return pgroup

    def available(self, pgroup="auto"):
        return STATUS_DATA[self._pgroup_key(pgroup)].get_available_run_numbers()

    def keys(self, pgroup="auto"):
        """Enables dict-key tab completion, e.g. obj.run_status[<TAB>]."""
        return self.available(pgroup=pgroup)

    def dict(
        self,
        run_number=None,
        par_type="status",
        force_reload=False,
        pgroup="auto",
        status_type="status_run_start",
        as_dataframe=False,
    ):
        """Raw values for one or several runs, as {run_number: {name: value}}
        (or a DataFrame if as_dataframe=True). This is the original
        run_status() return shape."""
        return self._obj._fetch_run_status_dict(
            run_number=run_number,
            par_type=par_type,
            force_reload=force_reload,
            pgroup=pgroup,
            status_type=status_type,
            as_dataframe=as_dataframe,
        )

    def __call__(
        self,
        run_number=None,
        depth=None,
        include_hidden=False,
        compare_live=False,
        par_type="status",
        force_reload=False,
        pgroup="auto",
        status_type="status_run_start",
    ):
        if run_number is None:
            return self.available(pgroup=pgroup)
        single = isinstance(run_number, Number)
        run_numbers = [run_number] if single else list(run_number)
        stat = self._obj._fetch_run_status_dict(
            run_number=run_numbers,
            par_type=par_type,
            force_reload=force_reload,
            pgroup=pgroup,
            status_type=status_type,
        )
        if single:
            runno, dic = next(iter(stat.items()))
            return RunStatusView(
                self._obj,
                runno,
                dic,
                depth=depth,
                include_hidden=include_hidden,
                compare_live=compare_live,
                pgroup=pgroup,
                status_type=status_type,
                force_reload=force_reload,
            )
        # more than one run number: one combined table, one value column
        # per run (labelled by run number), rather than a plain dict of
        # separate per-run views (which never displayed usefully anyway --
        # `.dict(run_number=[...], as_dataframe=True)` remains the way to
        # get raw, machine-readable multi-run data if that's what's wanted).
        return MultiRunStatusView(
            self._obj,
            list(stat.keys()),
            stat,
            depth=depth,
            include_hidden=include_hidden,
            compare_live=compare_live,
            pgroup=pgroup,
            status_type=status_type,
            force_reload=force_reload,
        )

    def __getitem__(self, run_number):
        return self(run_number)

    def table(self, runs=None, columns=None, settings_only=False, pgroup="auto"):
        """This object's status over many runs, as a DataFrame: one row per
        run (index "run"), a "time" column, then one column per name below
        this object, relative to it as in `.dict()`; the object's own value
        is the column "value".

        runs: None for all, a slice over the available runs (slice(-50,
        None): the last 50), run numbers (negative count from the newest).
        columns: names to include (relative, "value" for the object itself).
        settings_only: only the names this object marks as settings.
        Built from the pgroup's run store (eco.acquisition.run_store), i.e.
        the status at run start.
        """
        data = STATUS_DATA[self._pgroup_key(pgroup)]
        nam = self._obj.alias.get_full_name()
        if columns is None:
            tab = data.table(prefix=nam, runs=runs)
        else:
            full = [nam if c in ("value", "") else f"{nam}.{c}" for c in columns]
            tab = data.table(columns=full, runs=runs)
        rel = {c: c[len(nam) + 1:] for c in tab.columns if c.startswith(nam + ".")}
        own = "value" if "value" not in rel.values() else nam
        rel[nam] = own
        # a child called "time" keeps its full name next to the run's time
        rel = {k: (k if v == "time" and k != "time" else v) for k, v in rel.items()}
        tab = tab.rename(columns=rel)
        if settings_only and hasattr(self._obj, "status_collection"):
            keep = set(self._obj.status_collection.get_names(selection="settings"))
            tab = tab[["time"] + [c for c in tab.columns if c in keep]]
        return tab

    def plot(self, columns=None, runs=None, x="time", ax=None, pgroup="auto", **plot_kwargs):
        """Plot this object's status over runs, against time (x="time", run
        numbers on a second x axis) or run number (x="run", time on the
        second axis). columns: relative names as in `.table()`; default the
        object's own value."""
        from eco.acquisition.run_store import plot_table

        if columns is None:
            tab = self.table(runs=runs, columns=["value"], pgroup=pgroup)
            own = tab.drop(columns="time")
            if own.iloc[:, 0].isna().all():
                choices = [c for c in self.table(runs=slice(-1, None), pgroup=pgroup).columns if c != "time"]
                raise ValueError(
                    f"{self._obj.alias.get_full_name()} has no value of its own in the "
                    f"run status; pass columns=, e.g. {choices[:10]}"
                )
        else:
            tab = self.table(runs=runs, columns=columns, pgroup=pgroup)
        return plot_table(tab, x=x, ax=ax, title=self._obj.alias.get_full_name(), **plot_kwargs)

    def __repr__(self):
        try:
            runs = self.available()
            avail = f"{len(runs)} runs available ({min(runs)}-{max(runs)})" if runs else "no runs available"
        except Exception:
            avail = "run list unavailable"
        return (
            f"<run_status for {self._obj.alias.get_full_name()}: {avail}>\n"
            "Call e.g. .run_status(1234) or .run_status[1234] for a display view "
            "(depth=, include_hidden=, compare_live=); .run_status([1234, 1235]) for "
            "a combined multi-run table; .run_status.dict(...) for raw values; "
            ".run_status.table(runs=slice(-50, None)) / .run_status.plot() for "
            "values over many runs."
        )


def _block_values(obj, block, par_type, nam):
    """{full name: value} of `par_type` from one status block of a run's
    status.json (e.g. its "status_run_start").

    "settings" has had three layouts on disk:
    - before the collection rework (files up to 2025-11): a top-level
      "settings" dict next to "status";
    - get_status(selections=["settings"]) output: under
      "selections"["settings"];
    - since then, in practice: only "status". Which status items are
      settings is a property of the assembly (`is_setting=True` puts them
      in its status_collection's "settings" selection), so the live
      object picks them out of the run's "status" - the same membership
      `Memory` uses.
    """
    if par_type != "settings":
        return block[par_type]
    if block.get("settings"):
        return block["settings"]
    stored = (block.get("selections") or {}).get("settings")
    if stored:
        return stored
    status = block.get("status") or {}
    if not hasattr(obj, "status_collection"):
        return {}
    full = {}
    for name in obj.status_collection.get_names(selection="settings"):
        key = f"{nam}.{name}"
        if key in status:
            full[key] = status[key]
    return full


def run_status_convenience(Obj):
    # if not hasattr(Obj, "alias"):
    #     return Obj

    def _fetch_run_status_dict(
        self,
        run_number=None,
        par_type="status",
        force_reload=False,
        pgroup="auto",
        status_type="status_run_start",
        as_dataframe=False,
    ):
        if pgroup == "auto":
            pgroup = list(STATUS_DATA.keys())[0]

        if run_number is None:
            return STATUS_DATA[pgroup].get_available_run_numbers()
        if isinstance(run_number, Number):
            run_number = [run_number]

        nam = self.alias.get_full_name()
        stat = {}
        for runno in run_number:
            if runno < 0:
                runno = STATUS_DATA[pgroup].get_available_run_numbers()[runno]
            if force_reload:
                block = STATUS_DATA[pgroup].load_run_status(runno)[status_type]
            else:
                block = STATUS_DATA[pgroup].get_run_status(runno)[status_type]
            dic = _block_values(self, block, par_type, nam)
            # the object's own value (an AdjustableVirtual's) under "", its
            # children relative to it; "bernina.mono" must not pick up
            # "bernina.mono2.*", which used to land on "" and overwrite it
            prefix = nam + "."
            dic = {
                ("" if k == nam else k[len(prefix):]): v
                for k, v in dic.items()
                if k == nam or k.startswith(prefix)
            }
            stat[runno] = dic

        if as_dataframe:
            from pandas import DataFrame  # deferred: not needed to import eco

            return DataFrame.from_dict(stat)

        return stat

    Obj._fetch_run_status_dict = _fetch_run_status_dict
    Obj.run_status = property(lambda self: RunStatusAccessor(self))

    def apply_run_settings(
        self,
        run_number=None,
        par_type="settings",
        force_reload=False,
        pgroup="auto",
        status_type="status_run_start",
        as_dataframe=False,
        **kwargs,
    ):
        stat = self._fetch_run_status_dict(
            run_number=run_number,
            par_type=par_type,
            force_reload=force_reload,
            pgroup=pgroup,
            status_type=status_type,
            as_dataframe=as_dataframe,
        )
        run_number = list(stat.keys())
        if not len(run_number) == 1:
            raise Exception("Cannot apply mutiple settings")
        run_number = run_number[0]
        stat = stat[run_number]

        self.memory.recall(input_obj=dict(settings=stat), **kwargs)

    Obj.apply_run_settings = apply_run_settings

    return Obj
