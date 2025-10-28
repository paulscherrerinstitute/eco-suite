import copy
from datetime import datetime
from inspect import isclass
import json
from pathlib import Path
from tkinter import W
from markdown import markdown

from numpy import isin
import numpy as np


from eco.acquisition.scan_data import run_status_convenience
from eco.elements.protocols import Detector, InitialisationWaitable
from eco.epics import get_from_archive

from ..aliases import Alias
from tabulate import tabulate
import colorama
from . import memory
from enum import Enum
import os
import subprocess
from rich.progress import track
from eco import Adjustable, Detector

import eco


_initializing_assemblies = []


class Collection:
    def __init__(self, name=None):
        if name is None:
            raise Exception("A name of collection is required")
        self.name = name
        self._list = []
        self._recurse = []

    # def get_list(self):
    #     return self._list

    # esired new way, in order to old containers and allow them to be replaced "on top" of a structure.
    # causes other issues  from recoursion, bigger issue...
    def get_list(self):
        ls = []
        for item in self._list:

            # if item in ls:
            #     print(f"Item {item.alias.get_full_name()} is already in list!")
            #     continue
            if (
                hasattr(item, f"{self.name}")
                and isinstance(item.__dict__[self.name], Collection)
                # and not (item.__dict__[self.name]==self)
            ):
                # if item.__dict__[self.name]==self:
                # print(f"Item {item.alias.get_full_name()} contains itself in collection!")

                if item in self._recurse:
                    # print(f"Item {item.alias.get_full_name()} is recursing ↳ ↳ ↳ ")
                    for titem in item.__dict__[self.name].get_list():
                        # print(titem.name)
                        # if titem.__dict__[self.name]==self:
                        #     print(titem.name)
                        if (
                            titem
                            not in ls
                            # and not
                        ):
                            ls.append(titem)
                else:
                    ls.append(item)
            else:
                ls.append(item)
        return ls

    def append(self, obj, recursive=True, force=False):
        if obj in self._list:
            return
        if force:
            self._list.append(obj)

        elif hasattr(obj, self.name):
            if isinstance(obj.__dict__[self.name], type(self)):
                if recursive:
                    self._recurse.append(obj)
                self._list.append(obj)
        else:
            self._list.append(obj)

    def pop(self, index):
        return self._list.pop(index)

    def index(self, item):
        return self._list.index(item)

    def pop_item(self, item):
        return self.pop(self.index(item))

    def pop_obj_children(self, obj):
        o = []
        for it in obj.__dict__[self.name].get_list():
            if it in self._list:
                o.append(self.pop_item(it))
        return o

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
    def __init__(self, name=None, parent=None, is_alias=True, elog=None):
        self.name = name
        self.alias = Alias(name, parent=parent)
        # self.settings = []
        # self.status_indicators = []
        self.settings_collection = Collection(name="settings_collection")
        self.status_collection = Collection(name="status_collection")
        self.display_collection = Collection(name="display_collection")
        self.view_toplevel_only = []
        if memory.global_memory_dir:
            self.memory = memory.Memory(self)
        if elog:
            self.__elog = elog
        else:
            self.__class__.__elog = property(lambda dum: ELOG)

    # TODO: Lazy an threaded append! (for PVs, should be quite a speedup).
    def _append(
        self,
        foo_obj_init,
        *args,
        name=None,
        is_setting=False,
        is_display=True,
        is_status=True,
        is_alias=True,
        view_toplevel_only=True,
        call_obj=True,
        # append_property_with_name=False,
        overwrite=False,
        delete_old=False,
        **kwargs,
    ):
        if overwrite:

            if name in self.__dict__:
                old = self.__dict__[name]
                for collection in [
                    self.settings_collection,
                    self.status_collection,
                ]:
                    if isinstance(old, Assembly):
                        collection.pop_obj_children(old)
                    else:
                        if old in collection.get_list():
                            collection.pop_item(old)
                self.display_collection.pop_item(old)
                self.alias.pop_object(old.alias)
            if delete_old:
                del old

        if isinstance(foo_obj_init, Adjustable) and not isclass(foo_obj_init):
            # adj_copy = copy.copy(foo_obj_init)
            adj_copy = foo_obj_init
            # adj_copy.alias = Alias(name,parent=self)
            self.__dict__[name] = adj_copy
        elif isinstance(foo_obj_init, Detector) and not isclass(foo_obj_init):
            self.__dict__[name] = foo_obj_init
        elif isinstance(foo_obj_init, Assembly) and not isclass(foo_obj_init):
            self.__dict__[name] = foo_obj_init
        elif call_obj and callable(foo_obj_init):
            self.__dict__[name] = foo_obj_init(*args, **kwargs, name=name)
        else:
            self.__dict__[name] = foo_obj_init
        self.alias.append(self.__dict__[name].alias)
        # except:
        #     print(f'object {name} / {foo_obj_init} not initialized with name/parent')
        #     self.__dict__[name] = foo_obj_init(*args, **kwargs)
        # if append_property_with_name:
        #     if isinstance(self.__dict__[name], Adjustable):
        #         self.__class__.__dict__[append_property_with_name] = property(
        #             self.__dict__[name].get_current_value,
        #             lambda val: self.__dict__[name].set_target_value(val).wait(),
        #         )
        #     elif isinstance(self.__dict__[name], Detector):
        #         self.__class__.__dict__[append_property_with_name] = property(
        #             self.__dict__[name].get_current_value,
        #         )

        # if is_setting == "auto":
        #     is_setting = isinstance(self.__dict__[name], Adjustable)
        if is_setting:
            self.settings_collection.append(self.__dict__[name], recursive=True)
        # if is_status == "auto":
        #     is_status = isinstance(self.__dict__[name], Detector)
        if is_status:
            self.status_collection.append(self.__dict__[name], recursive=True)
        if is_display:
            if is_display == "recursive":
                self.display_collection.append(self.__dict__[name], recursive=True)
            else:
                self.display_collection.append(self.__dict__[name], recursive=False)

        if view_toplevel_only:
            self.view_toplevel_only.append(self.__dict__[name])

    def get_status(
        self,
        base="self",
        verbose=True,
        print_times=False,
        channeltypes=None,
        print_name=False,
    ):
        if base == "self":
            base = self
        settings = {}
        settings_channels = {}
        settings_times = {}
        status = {}
        status_channels = {}
        status_times = {}
        nodet = []
        geterror = []

        #  with ThreadPoolExecutor(max_workers=max_workers) as exc:
        #         list(
        #             progress.track(
        #                 exc.map(
        #                     lambda name: self.init_name(
        #                         name, verbose=verbose, raise_errors=raise_errors
        #                     ),
        #                     self.all_names
        #                     - self.initialized_names
        #                     - set(exclude_names),
        #                 ),
        #                 description="Initializing ...",
        #                 total=len(
        #                     self.all_names - self.initialized_names - set(exclude_names)
        #                 ),
        #                 transient=True,
        #             )
        #         )

        def get_stat_one_assembly(ts):
            if hasattr(ts, "get_current_value"):
                try:
                    if (not channeltypes) or (ts.alias.channeltype in channeltypes):
                        status[ts.alias.get_full_name(base=base)] = (
                            ts.get_current_value()
                        )
                        try:
                            status_channels[ts.alias.get_full_name(base=base)] = (
                                ts.alias.channel
                            )
                        except:
                            pass
                except:
                    geterror.append(ts.alias.get_full_name(base=base))
            else:
                nodet.append(ts.alias.get_full_name(base=base))

        #  with ThreadPoolExecutor(max_workers=max_workers) as exc:
        #         list(
        #             progress.track(
        #                 exc.map(
        #                     get_stat_one_assembly,
        #                     self.status_collection.get_list(),
        #                 ),
        #             description="Getting status...",
        #             total=len(self.status_collection.get_list()),
        #             transient=True,
        #             )
        #         )

        for ts in track(
            self.status_collection.get_list(),
            transient=True,
            description="Reading status indicators ...",
        ):
            # if (not (ts is self)) and hasattr(ts, "get_status"):
            #     tstat = ts.get_status(base=base)
            #     status_indicators.update(tstat["settings"])
            #     status_indicators.update(tstat["status_indicators"])
            # else:
            if hasattr(ts, "get_current_value"):
                tstart = time.time()
                try:
                    if (not channeltypes) or (ts.alias.channeltype in channeltypes):
                        status[ts.alias.get_full_name(base=base)] = (
                            ts.get_current_value()
                        )
                        try:
                            status_channels[ts.alias.get_full_name(base=base)] = (
                                ts.alias.channel
                            )
                        except:
                            pass
                except:
                    geterror.append(ts.alias.get_full_name(base=base))
                status_times[ts.alias.get_full_name(base=base)] = time.time() - tstart
            else:
                nodet.append(ts.alias.get_full_name(base=base))

        for ts in track(
            self.settings_collection.get_list(),
            transient=True,
            description="Reading settings ...",
        ):

            if print_name:
                print(ts.name)
            # if (not (ts is self)) and hasattr(ts, "get_status"):
            #     tstat = ts.get_status(base=base)
            #     settings.update(tstat["settings"])
            #     status_indicators.update(tstat["status_indicators"])
            # else:
            if hasattr(ts, "get_current_value"):
                tstart = time.time()
                try:
                    if (not channeltypes) or (ts.alias.channeltype in channeltypes):
                        settings[ts.alias.get_full_name(base=base)] = (
                            ts.get_current_value()
                        )
                        try:
                            settings_channels[ts.alias.get_full_name(base=base)] = (
                                ts.alias.channel
                            )
                        except:
                            pass
                except:
                    geterror.append(ts.alias.get_full_name(base=base))
                settings_times[ts.alias.get_full_name(base=base)] = time.time() - tstart
            else:
                nodet.append(ts.alias.get_full_name(base=base))

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

        return {
            "settings": settings,
            "status": status,
            "settings_channels": settings_channels,
            "status_channels": status_channels,
            "settings_times": settings_times,
            "status_times": status_times,
        }

    def status(self, get_string=False):
        stat = self.get_status()
        s = tabulate([[name, value] for name, value in stat["status"].items()])
        if get_string:
            return s
        else:
            print(s)

    def settings(self, get_string=False):
        stat = self.get_status()
        s = tabulate(
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
        s = tabulate([[name, value] for name, value in stat_filt[stat_field].items()])
        return s

    def get_display_str(
        self,
        tablefmt="simple",
        with_base_name=False,
        maxcolwidths=[None, 50, None, None, None],
    ):
        main_name = self.name
        stats = self.display_collection()
        # stats_dict = {}
        tab = []
        for to in stats:
            name = to.alias.get_full_name(base=self)

            is_adjustable = isinstance(to, Adjustable)
            is_detector = isinstance(to, Detector)
            typechar = ""
            if is_adjustable:
                typechar += "✏️"
            elif is_detector:
                typechar += "👁️"
            if hasattr(to, "settings_collection"):
                typechar += " ↳"

            try:
                value = to.get_current_value()
            except AttributeError:
                if hasattr(to, "settings_collection"):
                    value = "\x1b[3mhas lower level items\x1b[0m"

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
                    [".".join([main_name, name]), value, unit, typechar, description]
                )
            else:
                tab.append([name, value, unit, typechar, description])
        if tab:
            s = tabulate(tab, tablefmt=tablefmt, maxcolwidths=maxcolwidths)
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
            message += self.get_display_str(tablefmt="html")
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

    def __repr__(self):
        label = self.alias.get_full_name() + " display\n"
        return label + self.get_display_str()

    # def _wait_for_initialisation(self, timeout=2):
    #     for ton, to in self.__dict__.items():
    #         try:
    #             iswaitable = isinstance(to, InitialisationWaitable)
    #             if iswaitable:
    #                 to._wait_for_initialisation()
    #         except:
    #             pass

    def _wait_for_initialisation(self):
        for item in self.status_collection.get_list():
            if isinstance(item, Assembly) and (item in _initializing_assemblies):
                continue
            if isinstance(item, InitialisationWaitable):
                if isinstance(item, Assembly):
                    _initializing_assemblies.append(item)
                item._wait_for_initialisation()

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


class Assembly_old:
    def __init__(self, name=None, parent=None, is_alias=True):
        self.name = name
        self.alias = Alias(name, parent=parent)
        self.settings = []
        self.status_indicators = []
        self.view_toplevel_only = []
        if memory.global_memory_dir:
            self.memory = memory.Memory(self)

    def _append(
        self,
        foo_obj_init,
        *args,
        name=None,
        is_setting=False,
        is_display=True,
        is_alias=True,
        view_toplevel_only=True,
        **kwargs,
    ):
        self.__dict__[name] = foo_obj_init(*args, **kwargs, name=name)
        self.alias.append(self.__dict__[name].alias)
        # except:
        #     print(f'object {name} / {foo_obj_init} not initialized with name/parent')
        #     self.__dict__[name] = foo_obj_init(*args, **kwargs)
        if is_setting:
            self.settings.append(self.__dict__[name])
            try:
                subsettings_names = self.__dict__[name].get_status()["settings"]
                subsettings = []
            except AttributeError:
                pass
        if (not is_setting) and is_status:
            self.status_indicators.append(self.__dict__[name])
        if view_toplevel_only:
            self.view_toplevel_only.append(self.__dict__[name])

    def get_status(self, base=None):
        if base is None:
            base = self
        settings = {}
        status_indicators = {}
        for ts in self.settings:
            if (not (ts is self)) and hasattr(ts, "get_status"):
                tstat = ts.get_status(base=base)
                settings.update(tstat["settings"])
                status_indicators.update(tstat["status_indicators"])
            else:
                settings[ts.alias.get_full_name(base=base)] = ts.get_current_value()
        for ts in self.status_indicators:
            if (not (ts is self)) and hasattr(ts, "get_status"):
                tstat = ts.get_status(base=base)
                status_indicators.update(tstat["settings"])
                status_indicators.update(tstat["status_indicators"])
            else:
                status_indicators[ts.alias.get_full_name(base=base)] = (
                    ts.get_current_value()
                )
        return {"settings": settings, "status_indicators": status_indicators}

    def status(self, get_string=False):
        stat = self.get_status()
        s = tabulate(
            [
                [colorama.Style.BRIGHT + name + colorama.Style.RESET_ALL, value]
                for name, value in stat["settings"].items()
            ]
            + [[name, value] for name, value in stat["status_indicators"].items()]
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
        s = tabulate([[name, value] for name, value in stat_filt[stat_field].items()])
        return s

    def __repr__(self):
        return self.get_status_str(base=self)


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
