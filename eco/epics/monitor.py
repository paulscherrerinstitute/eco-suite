from epics import PV
import numpy as np
from scipy.interpolate import interp1d
import time
from concurrent.futures import ThreadPoolExecutor


class MultiMonitor:
    def __init__(self, *args, max_workers=100):
        self.monitors = {}

        exc_init = ThreadPoolExecutor(max_workers=max_workers)
        jobs = [exc_init.submit(self.append, targ) for targ in args]
        exc_init.shutdown(wait=True)
        # for targ in args:
        #     self.append(targ)

    def append(self, pvname):
        self.monitors[pvname] = Monitor(pvname)

    def clear(self):
        for tmon in self.monitors.values():
            tmon.clear()

    def merge_data(self, *args, merge_timestamps_local=True):
        if len(args) == 0:
            args = list(self.monitors.keys())
        ts = []
        for targ in args:
            ts += self.monitors[targ].data["timestamp_local"]
        ts = sorted(ts)
        out = {}
        for targ in args:
            if len(self.monitors[targ].data["timestamp_local"]) > 1:
                f = interp1d(
                    self.monitors[targ].data["timestamp_local"],
                    self.monitors[targ].data["value"],
                    bounds_error=False,
                    fill_value=(np.nan, 555555555555),
                )
                out[targ] = f(ts)
            else:
                out[targ] = [np.nan] * len(ts)
        # except ValueError:
        #     print("issue")
        #     out[targ] = []
        return ts, out


class Monitor:
    def __init__(self, pvname, start_immediately=True, active_get=True):
        self.data = {"value": [], "timestamp": [], "timestamp_local": []}
        self.print = False
        self.pv = PV(pvname)
        self.cb_index = None
        self.clear_before_next = False
        if start_immediately:
            self.start_callback()
        if active_get:
            self.pv.get()

    def start_callback(self):
        self.cb_index = self.pv.add_callback(self.append)

    def stop_callback(self):
        self.pv.remove_callback(self.cb_index)

    def clear(self):
        self.clear_before_next = True

    def append(self, pvname=None, value=None, timestamp=None, **kwargs):
        if self.clear_before_next:
            self.data = {
                "value": [self.data["value"][-1]],
                "timestamp": [self.data["timestamp"][-1]],
                "timestamp_local": [self.data["timestamp_local"][-1]],
            }
        ts_local = time.time()
        self.data["value"].append(value)
        self.data["timestamp"].append(timestamp)
        self.data["timestamp_local"].append(ts_local)
        if self.clear_before_next:
            self.clear_before_next = False

        if self.print:
            print(
                f"{pvname}:  {value};  time: {timestamp}; time_local: {ts_local}; diff: {ts_local-timestamp}"
            )
