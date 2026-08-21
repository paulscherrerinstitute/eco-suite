from numbers import Number
import os
from pathlib import Path
from escape.swissfel import load_dataset_from_scan

# from eco.elements.assembly import Assembly
import json

from pandas import DataFrame

from eco.utilities.tables import format_table


class RunData:
    def __init__(
        self,
        pgroup_adj,
        path_search="/sf/bernina/data/{pgroup:s}/raw",
        load_kwargs={},
        name="",
    ):
        # super().__init__(name=name)
        # self._append(pgroup_adj, name="pgroup")
        self.pgroup = pgroup_adj
        self.path_search = path_search
        self.load_kwargs = load_kwargs
        self.loaded_runs = {}

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

        self.loaded_runs[run_number] = {"dataset": trun}
        self.__setattr__(f"run{run_number:04d}", trun)
        return trun

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


STATUS_DATA = {}


class StatusData:
    def __init__(
        self,
        pgroup_adj,
        path_search="/sf/bernina/data/{pgroup:s}/raw",
        status_search="aux/status.json",
        load_kwargs={},
        name="",
    ):
        # super().__init__(name=name)
        # self._append(pgroup_adj, name="pgroup")
        self.pgroup = pgroup_adj
        self.path_search = path_search
        self.status_search = status_search
        self.load_kwargs = load_kwargs
        self.loaded_statii = {}
        STATUS_DATA[self.pgroup.get_current_value()] = self

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
                    if (tp / Path(self.status_search)).exists():
                        runs.append(int(numstring))
        runs.sort()
        return runs

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
        tkwargs = self.load_kwargs
        tkwargs.update(kwargs)

        tks = {}
        for tk, tv in tkwargs.items():
            if type(tv) is str:
                tv = tv.format(pgroup=self.pgroup.get_current_value())
            tks[tk] = tv
        pgroup = self.pgroup.get_current_value()
        with open(
            Path(self.path_search.format(pgroup=pgroup))
            / Path(f"run{run_number:04d}/aux/status.json"),
            "r",
        ) as fh:
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
            "a combined multi-run table; .run_status.dict(...) for raw values."
        )


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
                dic = STATUS_DATA[pgroup].load_run_status(runno)[status_type][par_type]
            else:
                dic = STATUS_DATA[pgroup].get_run_status(runno)[status_type][par_type]
            dic = {
                "".join(k.split(nam + ".")[1:]): v
                for k, v in dic.items()
                if k.startswith(nam)
            }
            stat[runno] = dic

        if as_dataframe:
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
