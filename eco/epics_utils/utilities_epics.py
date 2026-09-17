import threading
import weakref
from epics import PV
from copy import copy
from time import sleep, time


def _wait_for_enum_strs(pv, retries=10, delay=0.05):
    """Same fix/rationale as `eco.epics_utils.adjustable.wait_for_enum_strs` --
    duplicated locally (not imported) to avoid a circular import, since
    `eco.epics_utils.adjustable` itself imports from this module."""
    for _ in range(retries):
        if pv.enum_strs:
            return pv.enum_strs
        sleep(delay)
    return pv.enum_strs


class EnumWrapper:
    def __init__(self, pvname, elog=None):
        self._elog = elog
        self._pv = PV(pvname)
        self._pv.wait_for_connection()
        self.names = _wait_for_enum_strs(self._pv)
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


# Per-PV auto_monitor reference count, shared by every CallbackEpics instance
# regardless of who created it (a status-server baseline monitor, one
# RecordingSession, several overlapping RecordingSessions, ...). Keyed by the
# pyepics PV object itself (identity - PV doesn't override __hash__/__eq__),
# WeakKeyDictionary so an entry disappears with the PV rather than leaking.
#
# Why this exists: start()/stop() used to snapshot self.pv.auto_monitor at
# start() and blindly write it back at stop() - correct for exactly one
# concurrent CallbackEpics per PV, wrong for two. With two overlapping
# RecordingSessions (or one RecordingSession over the status server's own
# permanent baseline monitor - see NamespaceMonitorStore._build_index/
# self._monitors, which is already a second, independent CallbackEpics on
# top of any RecordingSession's) the one that stops *first* would restore
# auto_monitor to whatever *it* saw at its own start - potentially clobbering
# whatever the still-running one needs, silently, mid-flight. Only turning
# auto_monitor on for the first attacher and restoring it for the last
# detacher (a plain reference count) makes stop() order-independent.
_auto_monitor_lock = threading.Lock()
_auto_monitor_refs = weakref.WeakKeyDictionary()


class CallbackEpics:
    """set_current_value_callback() implementation for a single PV, shared
    by every PV-backed Detector/Adjustable class (eco.epics_utils.detector,
    eco.epics_utils.adjustable, eco.detector.detectors_psi). Moved here from
    eco.epics_utils.detector (still importable from there) so eco.epics_utils.adjustable
    can use it too without a circular import (eco.epics_utils.detector already
    imports from eco.epics_utils.adjustable)."""

    def __init__(
        self,
        pv,
        func="accumulate",
        collector=None,
        run_once=True,
        print_output=False,
    ):
        self.pv = pv
        # self.data = collector
        if func == "accumulate":
            func = self.accumulate_values
            if collector is None:
                collector = {"timestamps": [], "values": [], "timestamps_ioc": []}
            self.data = (
                collector  # {"timestamps": [], "values": [], "timestamps_ioc": []}
            )
        elif func == "latest":
            # Unlike "accumulate", keeps only the most recent value instead
            # of an ever-growing list - for monitoring that's meant to run
            # indefinitely (e.g. a long-lived status cache) rather than for
            # the duration of one scan, where "accumulate" would otherwise
            # grow without bound.
            func = self.set_latest_value
            if collector is None:
                collector = {"value": None, "timestamp": None, "timestamp_local": None}
            self.data = collector
        self.foo = func
        self.run_once = run_once
        self.print = print_output

    def start(self, add_current_value=True, with_ctrlvars=True, auto_monitor=True):
        """Attach the callback and switch the PV to monitoring.

        add_current_value : bool
            Seed the collector with one live ``pv.get()`` first, so a
            consumer has a value before the first monitor update arrives.
            That is a blocking CA round trip - set it False when starting
            thousands of these at once (a status server monitoring a whole
            namespace), where the seeding get-storm is exactly what the
            monitoring is meant to avoid.
        with_ctrlvars : bool
            Passed to ``pv.add_callback``. pyepics defaults it to True,
            which issues a blocking ``get_ctrlvars()`` (units, limits,
            precision) per already-connected PV. Harmless for one PV,
            another full get-storm for thousands - pass False there.
        auto_monitor : bool | int
            What to set ``pv.auto_monitor`` to. True is pyepics's default
            subscription mask (DBE_VALUE|DBE_ALARM). An ``epics.dbr.DBE_*``
            mask can be passed instead - notably ``DBE_LOG``, which
            subscribes to the IOC's *archive* deadband stream and so is the
            one way to make a fast channel actually send fewer updates
            without touching the IOC's record fields.
        """
        if add_current_value:
            self.foo(pvname=self.pv.pvname, value=self.pv.get(), timestamp=self.pv.timestamp)
        self.cb_index = self.pv.add_callback(
            self.foo,
            run_once=True,
            with_ctrlvars=with_ctrlvars,
        )
        # Reference-counted, not a plain snapshot/restore - see
        # _auto_monitor_refs' own docstring for why: this PV may already
        # have another CallbackEpics on it (a permanent baseline monitor, a
        # different overlapping recording, ...), and only the *first*
        # attacher should change auto_monitor, only the *last* detacher
        # should put it back.
        with _auto_monitor_lock:
            entry = _auto_monitor_refs.get(self.pv)
            if entry is None:
                entry = {"count": 0, "baseline": self.pv.auto_monitor}
                _auto_monitor_refs[self.pv] = entry
            entry["count"] += 1
            self._auto_monitor_entry = entry
            self.pv.auto_monitor = auto_monitor

    def is_running(self):
        return hasattr(self, "cb_index") and self.cb_index in self.pv.callbacks.keys()

    def stop(self):
        if self.is_running():
            self.pv.remove_callback(self.cb_index)
            entry = getattr(self, "_auto_monitor_entry", None)
            if entry is not None:
                with _auto_monitor_lock:
                    entry["count"] -= 1
                    if entry["count"] <= 0:
                        self.pv.auto_monitor = entry["baseline"]
                        _auto_monitor_refs.pop(self.pv, None)
                self._auto_monitor_entry = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def accumulate_values(self, pvname=None, value=None, timestamp=None, **kwargs):
        # if not self.data:
        #     self.data = []
        ts_local = time()
        assert (
            len(self.data["timestamps"])
            == len(self.data["values"])
            == len(self.data["timestamps_ioc"])
        )
        self.data["timestamps"].append(ts_local)
        self.data["values"].append(value)
        self.data["timestamps_ioc"].append(timestamp)

        if self.print:
            print(
                f"{pvname}:  {value};  time_ioc: {timestamp}; time_local: {ts_local}; diff: {ts_local-timestamp}"
            )

    def set_latest_value(self, pvname=None, value=None, timestamp=None, **kwargs):
        ts_local = time()
        self.data["value"] = value
        self.data["timestamp"] = timestamp
        self.data["timestamp_local"] = ts_local

        if self.print:
            print(
                f"{pvname}:  {value};  time_ioc: {timestamp}; time_local: {ts_local}"
            )


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
