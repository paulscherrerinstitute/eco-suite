from ..aliases import Alias
from tabulate import tabulate
import colorama
from . import memory


class Assembly:
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
        **kwargs
    ):
        self.__dict__[name] = foo_obj_init(*args, **kwargs, name=name)
        self.alias.append(self.__dict__[name].alias)
        # except:
        #     print(f'object {name} / {foo_obj_init} not initialized with name/parent')
        #     self.__dict__[name] = foo_obj_init(*args, **kwargs)
        if is_setting:
            self.settings.append(self.__dict__[name])
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
                settings.update(tstat["settings"])
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
