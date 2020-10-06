from ..aliases import Alias
from tabulate import tabulate
import colorama

class Assembly:
    def __init__(self,name=None,is_alias=True):
        self.name = name
        if is_alias:
            self.alias = Alias(name)
        self.settings = []
        self.status_indicators = []


    def _append(self,foo_obj_init, *args, name=None, is_setting=False, is_status=True, is_alias=True, **kwargs):
        self.__dict__[name] = foo_obj_init(*args, **kwargs, name=name)
        if is_alias:
            self.alias.append(self.__dict__[name].alias)
        if is_setting:
            self.settings.append(self.__dict__[name])
        if (not is_setting) and is_status :
            self.status_indicators.append(self.__dict__[name])

    def get_status(self):
        return {
                'settings':{ts.alias.get_full_name(base=self):ts.get_current_value() for ts in self.settings},
                'status_indicators':{ts.alias.get_full_name(base=self):ts.get_current_value() for ts in self.status_indicators},
                }

    def status(self):
        stat = self.get_status()
        s = tabulate(
            [[colorama.Style.BRIGHT+name+colorama.Style.RESET_ALL, value] for name,value in stat['settings'].items()]
            +[[name, value] for name,value in stat['status_indicators'].items()]
        )
        return s

    def __repr__(self):
        stat = self.get_status()
        s = tabulate([[name, value] for name,value in stat['status_indicators'].items()])
        return s
        




       
