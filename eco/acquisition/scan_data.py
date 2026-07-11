from numbers import Number
import os
from pathlib import Path
from escape.swissfel import load_dataset_from_scan

# from eco.elements.assembly import Assembly
import json

from pandas import DataFrame
from tabulate import tabulate


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
        self, obj, run_number, dic, depth=None, include_hidden=False, compare_live=False
    ):
        self.obj = obj
        self.run_number = run_number
        self.dic = dic
        self.depth = depth
        self.include_hidden = include_hidden
        self.compare_live = compare_live

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

    def _build_table(self, tablefmt="simple"):
        tab = []
        for name, item in self._rows():
            value = self.dic.get(name)
            if value is None:
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
                if self.compare_live:
                    try:
                        value = f"{value} (crr: {item.get_current_value()})"
                    except Exception:
                        pass
            tab.append([name, value, unit, typechar, description])
        if not tab:
            return ""
        return tabulate(tab, tablefmt=tablefmt, maxcolwidths=[None, 50, None, None, None])

    def __getitem__(self, key):
        return self.dic[key]

    def __repr__(self):
        name = self.obj.alias.get_full_name()
        header = f"{name} run {self.run_number} status"
        if self.compare_live:
            header += "  [value shown as: run value (crr: <live value>)]"
        return header + "\n" + self._build_table()

    def _repr_html_(self):
        return self._build_table(tablefmt="html")


class RunStatusAccessor:
    """`obj.run_status` - pick a run to see this object's historical status
    as a display view (like its live repr), with options to unfold nested
    (`depth=`) and normally-hidden (`include_hidden=`) components, and to
    compare against the live value (`compare_live=`). Tab-completable in
    IPython/Jupyter via `obj.run_status[<TAB>]`. Use `.dict(...)` for the
    original, raw (optionally multi-run) dictionary/DataFrame output.
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
        views = {
            runno: RunStatusView(
                self._obj,
                runno,
                dic,
                depth=depth,
                include_hidden=include_hidden,
                compare_live=compare_live,
            )
            for runno, dic in stat.items()
        }
        if single:
            return list(views.values())[0]
        return views

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
            "(depth=, include_hidden=, compare_live=); .run_status.dict(...) for raw values."
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
