from epics import PV
from copy import copy
from time import sleep, time


class EnumWrapper:
    def __init__(self, pvname, elog=None):
        self._elog = elog
        self._pv = PV(pvname)
        self.names = self._pv.enum_strs
        # print(self.names)
        # if self.names:
        self.setters = Positioner([(nam, lambda: self.set(nam)) for nam in self.names])

    def set(self, target):
        if type(target) is str:
            assert target in self.names, (
                "set value need to be one of \n %s" % self.names
            )
            self._pv.put(self.names.index(target))
        elif type(target) is int:
            assert target >= 0, "set integer needs to be positive"
            assert target < len(self.names)
            self._pv.put(target)

    def get(self):
        return self._pv.get()

    def get_name(self):
        return self.names[self.get()]

    def __repr__(self):
        return self.get_name()


class MonitorAccumulator:
    def __init__(self, pv, attr=None, keywords=["value", "timestamp"]):
        self.pv = pv
        self.attr = attr
        self.values = []
        self.keywords = keywords

    def _accumulate(self, **kwargs):
        self.values.append([kwargs[kw] for kw in self.keywords])

    def accumulate(self):
        self.pv.add_callback(self._accumulate, self.attr)

    def stop(self):
        self.pv.remove_callbacks(self.attr)

    def cycle(self):
        self.stop()
        d = self.values.copy()
        self.values = []
        self.accumulate()
        return d


class Monitor:
    def __init__(self, pvname, start_immediately=True):
        self.data = {}
        self.print = False
        self.pv = PV(pvname)
        self.cb_index = None
        if start_immediately:
            self.start_callback()

    def start_callback(self):
        self.cb_index = self.pv.add_callback(self.append)

    def stop_callback(self):
        self.pv.remove_callback(self.cb_index)

    def append(self, pvname=None, value=None, timestamp=None, **kwargs):
        if not (pvname in self.data):
            self.data[pvname] = []
        ts_local = time()
        self.data[pvname].append(
            {"value": value, "timestamp": timestamp, "timestamp_local": ts_local}
        )
        if self.print:
            print(
                f"{pvname}:  {value};  time: {timestamp}; time_local: {ts_local}; diff: {ts_local-timestamp}"
            )


class Positioner:
    def __init__(self, list_of_name_func_tuples):
        for name, func in list_of_name_func_tuples:
            tname = name.replace(" ", "_").replace(".", "p")
            if tname[0].isnumeric():
                tname = "v" + tname
            self.__dict__[tname] = func


class EpicsString:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.pvname = pvname
        self._pv = PV(pvname)
        self._elog = elog

    def get(self):
        return self._pv.get()

    def set(self, string):
        self._pv.put(bytes(string, "utf8"))

    def __repr__(self):
        return self.get()

    def __call__(self, string):
        self.set(string)


class WaitPvConditions:
    def __init__(self, pv, *condition_foos):
        self.pv = pv
        self.foos = list(copy(condition_foos))
        self.callback_index = self.pv.add_callback(self.func)

    def func(self, **kwargs):
        if len(self.foos) > 0:
            if self.foos[0](**kwargs):
                self.foos.pop(0)
            if len(self.foos) == 0:
                self.pv.remove_callback(self.callback_index)

    @property
    def steps_left(self):
        return len(self.foos)

    @property
    def is_done(self):
        return len(self.foos) == 0

    def wait_until_done(self, check_interval=0.05):
        while not self.is_done:
            sleep(check_interval)
