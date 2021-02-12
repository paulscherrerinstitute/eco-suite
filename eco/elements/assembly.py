from ..aliases import Alias
from tabulate import tabulate
import colorama
from . import memory
from enum import Enum


class Collection:
    def __init__(self, name=None):
        if name is None:
            raise Exception("A name of collection is required")
        self.name = name
        self._list = []

    def get_list(self):
        return self._list

    def append(self, obj, recursive=True, force=False):
        if force:
            if not (obj in self._list):
                self._list.append(obj)

        elif hasattr(obj, self.name):
            if isinstance(obj.__dict__[self.name], type(self)):
                if not recursive:
                    # if (obj in obj.__dict__[self.name]()):
                    if not (obj in self._list):
                        self._list.append(obj)
                else:
                    for it in obj.__dict__[self.name].get_list():
                        if not (it in self._list):
                            self._list.append(it)
        else:
            if not (obj in self._list):
                self._list.append(obj)

    def __call__(self):
        return self.get_list()

    # def __repr__(self):
    #     return f'{{id(self)}'


class Assembly:
    def __init__(self, name=None, parent=None, is_alias=True):
        self.name = name
        self.alias = Alias(name, parent=parent)
        self.settings = []
        self.status_indicators = []
        self.settings_collection = Collection(name="settings_collection")
        self.status_indicators_collection = Collection(
            name="status_indicators_collection"
        )
        self.view_toplevel_only = []
        if memory.global_memory_dir:
            self.memory = memory.Memory(self)

    def _append(
        self,
        foo_obj_init,
        *args,
        name=None,
        is_setting=False,
        is_status=True,
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
            self.settings_collection.append(self.__dict__[name], recursive=True)

        if is_status:
            if is_status == "recursive":
                self.status_indicators_collection.append(
                    self.__dict__[name], recursive=True
                )
            else:
                self.status_indicators_collection.append(
                    self.__dict__[name], recursive=False
                )

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
                status_indicators[
                    ts.alias.get_full_name(base=base)
                ] = ts.get_current_value()
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

    def get_status_indicator_str(self):
        main_name = self.name
        stats = self.status_indicators_collection()
        # stats_dict = {}
        tab = []
        for to in stats:
            name = to.alias.get_full_name(base=self)
            value = to.get_current_value()
            if isinstance(value, Enum):
                value = f"{value.value} ({value.name})"
            try:
                unit = to.unit()
            except:
                unit = None
            tab.append([".".join([main_name, name]), value, unit])
        s = tabulate(tab)
        return s

    def __repr__(self):
        return self.get_status_indicator_str()


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
        is_status=True,
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
                status_indicators[
                    ts.alias.get_full_name(base=base)
                ] = ts.get_current_value()
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
