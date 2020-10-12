from ..aliases import Alias
from tabulate import tabulate
import colorama
from . import memory 

class Assembly:
    def __init__(self, name=None, parent=None, is_alias=True):
        self.name = name
        self.alias = Alias(name,parent=parent)
        self.settings = []
        self.status_indicators = []
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

    def get_status(self,base=None):
        if base is None:
            base = self
        settings = {}
        status_indicators = {}
        for ts in self.settings:
            if (not (ts is self)) and hasattr(ts,'get_status'):
                tstat = ts.get_status(base=self)
                settings.update(tstat['settings'])
                status_indicators.update(tstat['status_indicators'])
            else:
                settings[ts.alias.get_full_name(base=base)] = ts.get_current_value()
        for ts in self.status_indicators:
            if (not (ts is self)) and hasattr(ts,'get_status'):
                tstat = ts.get_status()
                settings.update(tstat['settings'])
                status_indicators.update(tstat['status_indicators'])
            else:
                status_indicators[ts.alias.get_full_name(base=base)] = ts.get_current_value()
        return {'settings':settings,'status_indicators':status_indicators}
        

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

    def __repr__(self):
        stat = self.get_status()
        s = tabulate([[name, value] for name, value in stat["settings"].items()])
        return s
