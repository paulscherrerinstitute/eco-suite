from eco.acquisition.counters import CounterValue
from eco.acquisition import scan
from lazy_object_proxy import Proxy as LazyProxy


def scannable(Obj):
    @property
    def scans(self):
        counter = CounterValue(self, name=self.alias.get_full_name())
        scans = scan.Scans(default_counters=[counter])
        return scans

    Obj.scans = scans
    return Obj
