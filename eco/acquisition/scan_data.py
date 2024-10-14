from numbers import Number
import os
from pathlib import Path
from escape.swissfel import load_dataset_from_scan

# from eco.elements.assembly import Assembly
import json

from pandas import DataFrame


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
        self.adjust_group()

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

    def adjust_group(self,subdir_type='scratch/.escape_parse_result'):
        os.system("chgrp -R "+self.pgroup.get_current_value()[1:]+f" /sf/bernina/data/{self.pgroup.get_current_value()}/{subdir_type}")
    
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


def run_status_convenience(Obj):
    # if not hasattr(Obj, "alias"):
    #     return Obj

    def run_status(
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

    Obj.run_status = run_status

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
        stat = self.run_status(
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
