from eco.acquisition.counters import CounterValue
from eco.acquisition import scan

# from lazy_object_proxy import Proxy as LazyProxy


def scannable(Obj):
    @property
    def scans(self):
        if not hasattr(self, "_counter"):
            self._counter = CounterValue(self, name=self.alias.get_full_name())
        if not hasattr(self, "_scans"):
            self._scans = scan.Scans(default_counters=[self._counter])
        return self._scans

    Obj.scans = scans
    return Obj
