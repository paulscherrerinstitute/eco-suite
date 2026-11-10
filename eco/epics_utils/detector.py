import datetime
import threading
from enum import IntEnum
from time import time, sleep

import eco
import numpy as np
from epics import PV
from eco.epics_utils.adjustable import _read_pv
from eco.epics_utils import ca_tuning
from eco.epics_utils.ca_tuning import (
    CA_CONNECTION_TIMEOUT,
    CA_INIT_CONNECTION_TIMEOUT,
    has_ever_succeeded,
    note_successful_read,
    report_none_read,
)

from eco.acquisition.utilities import Acquisition
from eco.acquisition.decorators import scannable


from eco.aliases import Alias
from eco.elements.adjustable import AdjustableMemory
from eco.elements.assembly import Assembly
from eco.elements.detector import call_convenience, value_property
from eco.elements.protocols import enum_repr
from eco.epics_utils.adjustable import AdjustablePvString, AdjustablePv, wait_for_enum_strs
from eco.epics_utils import adjustable as _adjustable_module
from eco.epics_utils import get_from_archive
from eco.epics_utils.utilities_epics import CallbackEpics

from eco.acquisition.decorators import scannable


# @call_convenience
# @value_property
@get_from_archive
@scannable
class DetectorPvData(Assembly):
    def __init__(self, pvname, name=None, unit=None, has_unit=False, has_unit_pv=False):
        super().__init__(name=name)

        self.pvname = pvname
        singular = (unit is None) and (not has_unit)

        # if name == "aramis_undulator_photon_energy":
        #     print(f"singular is {singular}", unit, has_unit)
        if unit:
            self._append(AdjustableMemory, unit, name="unit")
            has_unit = False
        if not singular:
            self._append(AdjustablePv, pvname, name="readback", is_setting=False)
            # self.status_collection.append(self)
        else:
            self._pv = ca_tuning.make_pv(pvname)
            self.alias = Alias(self.name, channel=self.pvname, channeltype="CA")
            self.status_collection.append(self)
            self.status_collection.append(self, selection="settings", recursive=False)
            self.status_collection.append(self, selection="display", recursive=False)

        self.name = name
        if has_unit:
            self._append(
                AdjustablePvString, self.pvname + ".EGU", name="unit", is_setting=False
            )
        if has_unit_pv:
            self._append(AdjustablePv, has_unit_pv, name="unit", is_setting=False)

    def get_current_value(self):
        if hasattr(self, "_pv"):
            # diagnostics only, see eco.epics_utils.ca_tuning
            return _read_pv(self._pv, name=self.name)
        else:
            return self.readback.get_current_value()

    def get_severity(self):
        """EPICS alarm severity of the underlying PV: 0=NO_ALARM, 1=MINOR,
        2=MAJOR, 3=INVALID -- or None if it couldn't be read."""
        try:
            if hasattr(self, "_pv"):
                self._pv.get()
                return self._pv.severity
            return self.readback.get_severity()
        except Exception:
            return None

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        if hasattr(self, "_pv"):
            return CallbackEpics(
                self._pv,
                func=func,
                run_once=run_once,
                print_output=print_output,
                **kwargs,
            )
        # else:
        #     raise Exception('the object does not have a _pv')

    def __call__(self):
        return self.get_current_value()


# @call_convenience
# @value_property
@enum_repr
@get_from_archive
class DetectorPvEnum(Assembly):
    """Enum-valued PV Detector. Connecting and resolving the enum choice list
    is deferred to first use / an explicit `_wait_for_initialisation()` call
    -- see `AdjustablePvEnum`'s class docstring (`eco.epics_utils.adjustable`) for
    why: it's what lets many sibling enum fields constructed back-to-back
    (e.g. a Valve's several readbacks, an EVR's many pulsers) connect
    concurrently in pyepics's own CA background thread instead of each fully
    blocking before the next is even created."""

    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._pv = ca_tuning.make_pv(pvname, connection_timeout=CA_CONNECTION_TIMEOUT)
        self.name = name
        self.alias = Alias(name, channel=self.pvname, channeltype="CA")
        self._resolve_lock = threading.Lock()
        self._resolved = False
        self._enum_strs = None
        self._pv_enum = None
        if not _adjustable_module.LAZY_ENUM_RESOLUTION:
            # default: resolve now, like before this speedup existed -- see
            # eco.epics_utils.adjustable.LAZY_ENUM_RESOLUTION's docstring.
            self._resolve()

    def _resolve(self):
        # never raises for an unreachable/non-enum PV -- see AdjustablePvEnum
        # ._resolve()'s docstring for why (enumerate(None) used to crash this
        # after construction, somewhere Assembly._append's safety net no
        # longer applies).
        if self._resolved:
            return
        with self._resolve_lock:
            if self._resolved:
                return
            self._pv.wait_for_connection()
            self._enum_strs = wait_for_enum_strs(self._pv) or ()
            self._pv_enum = IntEnum(
                self.name, {tstr: n for n, tstr in enumerate(self._enum_strs)}
            )
            self._resolved = True

    def _wait_for_initialisation(self):
        # best-effort -- see AdjustablePvEnum._wait_for_initialisation
        try:
            self._resolve()
        except Exception:
            pass

    @property
    def enum_strs(self):
        self._resolve()
        return self._enum_strs

    @property
    def PvEnum(self):
        self._resolve()
        return self._pv_enum

    def validate(self, value):
        self._resolve()
        if type(value) is str:
            return self._pv_enum.__members__[value]
        else:
            return self._pv_enum(value)

    def get_current_value(self):
        return self.validate(_read_pv(self._pv, name=self.name))

    def __call__(self):
        return self.get_current_value()

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        return CallbackEpics(
            self._pv,
            func=func,
            run_once=run_once,
            print_output=print_output,
            **kwargs,
        )


# @call_convenience
@value_property
class DetectorPvString:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.pvname = pvname
        self._pv = ca_tuning.make_pv(pvname, connection_timeout=CA_CONNECTION_TIMEOUT)
        self._elog = elog
        self.alias = Alias(name, channel=self.pvname, channeltype="CA")

    def get_current_value(self):
        return _read_pv(self._pv, name=getattr(self, "name", None))

    def __repr__(self):
        return self.get_current_value()

    def __call__(self, string=None):
        if not string is None:
            self.set_target_value(string)
        else:
            return self.get_current_value()

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        return CallbackEpics(
            self._pv,
            func=func,
            run_once=run_once,
            print_output=print_output,
            **kwargs,
        )


@get_from_archive
class DetectorArchiverOnly(Assembly):
    """A Detector for a channel that exists only in a historical archive, with
    no live EPICS process variable behind it at all -- e.g. PSI's
    building-automation ("OSFA") room-climate sensors. They show up in
    datahub's "sf-archiver" backend under their own channel-id convention
    (e.g. "IKLUM-8701-EB01904:M01_A_D-OSFA"), not as an EPICS PV name, and a
    `caget` of the equivalent-looking PV name times out with "not found": the
    accelerator control network and PSI's building-management network are
    separate, and only the archiver side crosses over.

    `get_current_value()` therefore reads the latest archived sample within
    `lookback` seconds, rather than a live channel -- the usual
    `get_from_archive` historical API (`.get_archiver_time_range()`) works
    exactly as for a live PV-backed Detector, since all it needs is this
    object's `channel` alias. `.strip_plot()` (also inherited) has no live
    source to stream from here and will not show anything -- only
    `get_archiver_time_range()` is meaningful for a channel like this.
    """

    def __init__(self, channel, name=None, lookback=1800):
        super().__init__(name=name)
        self.Id = channel
        self.pvname = channel  # keeps get_archiver_channels' pvname fallback working
        self.channel = channel
        self.lookback = lookback
        self.alias = Alias(self.name, channel=self.channel, channeltype="CA")

    def get_current_value(self):
        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - datetime.timedelta(seconds=self.lookback)
        data = eco.defaults.ARCHIVER.get_data_time_range(
            channels=[self.channel],
            start=start,
            end=end,
            plot=False,
            verbose=False,
            force_type="CA",
        )
        if data is None or data.empty or self.channel not in data:
            return float("nan")
        values = data[self.channel].dropna()
        return float(values.iloc[-1]) if len(values) else float("nan")

    def __call__(self):
        return self.get_current_value()


@get_from_archive
class DetectorGls(Assembly):
    """A Detector combining a live EPICS PV with a GLS/building-automation
    channel (see `eco.epics_utils.gls`) that duplicates or stands in for it.
    `get_current_value()` reads the EPICS PV live whenever it exists and is
    connected; it falls back to the archived GLS value -- the latest sample
    within `gls_lookback` seconds -- when there is no PV at all
    (`pvname=None`), it has never connected, or it is not currently
    connected. There is no automatic judgment of "EPICS is significantly
    worse than GLS" beyond connectivity: if that distinction matters for a
    specific channel (e.g. an EPICS PV that connects but updates far slower
    than the GLS channel does), decide and wire it for that channel
    specifically rather than assuming a general rule here.

    Both sources are kept as sub-Detectors -- `.epics` (only if `pvname` was
    given) and `.gls` (always) -- so the inherited `.get_archiver_time_range()`
    (see `get_from_archive`) plots *both* traces together: directly useful
    for judging, on a given channel, whether GLS's coarser sampling (see the
    `eco.epics_utils.gls` module docstring) actually matters there. The
    inherited `.strip_plot()` only ever has live data to show for `.epics` --
    GLS has none.
    """

    def __init__(self, gls_channel, pvname=None, name=None, gls_lookback=1800):
        super().__init__(name=name)
        self.pvname = pvname
        self.gls_channel = gls_channel
        if pvname:
            self._append(DetectorPvDataStream, pvname, name="epics", is_setting=False)
        self._append(
            DetectorArchiverOnly,
            gls_channel,
            name="gls",
            is_setting=False,
            lookback=gls_lookback,
        )

    def _epics_connected(self):
        pv = getattr(getattr(self, "epics", None), "_pv", None)
        if pv is None:
            return False
        try:
            return bool(pv.connected)
        except Exception:
            return False

    @property
    def source(self):
        """Which sub-Detector `get_current_value()` is currently reading
        from: "epics" or "gls"."""
        return "epics" if self._epics_connected() else "gls"

    def get_current_value(self):
        if self._epics_connected():
            return self.epics.get_current_value()
        return self.gls.get_current_value()

    def __call__(self):
        return self.get_current_value()


# @call_convenience
# @value_property
@get_from_archive
@scannable
class DetectorPvDataStream(Assembly):
    def __init__(self, pvname, name=None, has_fields=False):
        super().__init__(name=name)
        self.Id = pvname
        self.pvname = pvname
        self._pv = ca_tuning.make_pv(pvname)
        self.alias = Alias(self.name, channel=self.pvname, channeltype="CA")
        if has_fields:
            self._append(
                AdjustablePvString, self.pvname + ".EGU", name="unit", is_setting=False
            )
        # self._append(
        #     PvString, self.pvname + ".DESC", name="description", is_setting=False
        # )

    def collect(self, seconds=None, samples=None):
        if (not seconds) and (not samples):
            raise Exception(
                "Either a time interval or number of samples need to be defined."
            )
        try:
            self._pv.callbacks.pop(self._collection["ix_cb"])
        except:
            pass
        self._collection = {"done": False}
        self.data_collected = []
        if seconds:
            self._collection["start_time"] = time()
            self._collection["seconds"] = seconds
            stopcond = (
                lambda: (time() - self._collection["start_time"])
                > self._collection["seconds"]
            )

            def addData(**kw):
                if not stopcond():
                    self.data_collected.append(kw["value"])
                else:
                    self._pv.callbacks.pop(self._collection["ix_cb"])
                    self._collection["done"] = True

        elif samples:
            self._collection["samples"] = samples
            stopcond = lambda: len(self.data_collected) >= self._collection["samples"]

            def addData(**kw):
                self.data_collected.append(kw["value"])
                if stopcond():
                    self._pv.callbacks.pop(self._collection["ix_cb"])
                    self._collection["done"] = True

        self._collection["ix_cb"] = self._pv.add_callback(addData)
        time_wait_start = time()
        while not self._collection["done"]:
            sleep(0.005)
            if seconds:
                if (time() - time_wait_start) > seconds:
                    if len(self.data_collected) == 0:
                        print(
                            f"No {self.name}({self.Id}) data update in time interval, reporting last value"
                        )
                        self._pv.callbacks.pop(self._collection["ix_cb"])
                        self.data_collected.append(self.get_current_value())
                        break

        return self.data_collected

    def acquire(self, hold=False, seconds=None, samples=None, **kwargs):
        return Acquisition(
            acquire=lambda: self.collect(seconds=seconds, samples=samples, **kwargs),
            hold=hold,
            stopper=None,
            get_result=lambda: self.data_collected,
        )

    def accumulate_ring_buffer(self, n_buffer):
        if not hasattr(self, "_accumulate"):
            self._accumulate = {"n_buffer": n_buffer, "ix": 0, "n_cb": -1}
        else:
            self._accumulate["n_buffer"] = n_buffer
            self._accumulate["ix"] = 0
        self._pv.callbacks.pop(self._accumulate["n_cb"], None)
        self._data = np.squeeze(np.zeros([n_buffer * 2, self._pv.count])) * np.nan

        def addData(**kw):
            self._accumulate["ix"] = (self._accumulate["ix"] + 1) % self._accumulate[
                "n_buffer"
            ]
            self._data[self._accumulate["ix"] :: self._accumulate["n_buffer"]] = kw[
                "value"
            ]

        self._accumulate["n_cb"] = self._pv.add_callback(addData)

    def accumulate_start(self):
        if not hasattr(self, "_accumulate_inf"):
            self._accumulate_inf = {"n_cb": -1}
        self._pv.callbacks.pop(self._accumulate_inf["n_cb"], None)
        self._data_inf = []

        def addData(**kw):
            self._data_inf.append(kw["value"])

        self._accumulate_inf["n_cb"] = self._pv.add_callback(addData)

    def accumulate_stop(self):
        self._pv.callbacks.pop(self._accumulate_inf["n_cb"], None)
        return self._data_inf

    def get_data(self):
        return self._data[
            self._accumulate["ix"]
            + 1 : self._accumulate["ix"]
            + 1
            + self._accumulate["n_buffer"]
        ]

    data = property(get_data)

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        if hasattr(self, "_pv"):
            return CallbackEpics(
                self._pv,
                func=func,
                run_once=run_once,
                print_output=print_output,
                **kwargs,
            )
        # else:
        #     raise Exception('the object does not have a _pv')

    def get_current_value(self, **kwargs):
        return self._pv.get(**kwargs)
