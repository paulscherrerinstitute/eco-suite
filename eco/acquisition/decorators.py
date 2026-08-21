import weakref
from eco.acquisition.counters import CounterValue
# from eco.acquisition import scan

# from lazy_object_proxy import Proxy as LazyProxy


def scannable(Obj):
    @property
    def scans(self):
        from eco.acquisition import scan  # moved import here to avoid circular import
        if hasattr(self, "_counter"):
            if not hasattr(self, "_old_counters"):
                self._old_counters = []
            self._old_counters.append(weakref.ref(self._counter))
            del self._counter
        self._counter = CounterValue(self, name=self.alias.get_full_name())
        if not hasattr(self, "_scans"):
            self._scans = scan.Scans(default_counters=[self._counter])
        else:
            # Scans reads self._default_counters (leading underscore)
            # everywhere -- this used to set a same-named-minus-underscore
            # attribute that nothing ever read, so a second+ access to
            # `.scans` silently kept scanning with the *first* CounterValue
            # ever built, even though a fresh one is constructed above on
            # every access.
            self._scans._default_counters = [self._counter]
            # keyword docstrings (see Scans._augment_docstrings) are
            # discovered from default_counters, which just changed above
            self._scans._augment_docstrings()
        return self._scans

    Obj.scans = scans
    return Obj
