from ..elements.assembly import Assembly
from ..aliases import Alias
from eco import ecocnf
from epics.pv import PV
from bsread.bsavail import pollStream
from bsread import dispatcher, source
from ..epics import get_from_archive
from escape import stream
from time import time, sleep
from eco.acquisition.utilities import Acquisition


@get_from_archive
class DetectorBsStream:
    def __init__(self, bs_channel, cachannel="same", name=None):
        self.name = name
        self.bs_channel = bs_channel
        if cachannel == "same":
            self.pvname = bs_channel
        elif (not cachannel) or cachannel == "none":
            self.pvname = None
        else:
            self.pvname = cachannel
        if self.pvname:
            self._pv = PV(self.pvname)
        self.alias = Alias(name, channel=bs_channel, channeltype="BS")

        self.stream = stream.EscData(source=stream.EventSource(self.bs_channel, None))

    def bs_avail(self):
        return self.bs_channel in [
            tmp["name"] for tmp in dispatcher.get_current_channels()
        ]

    def get_current_value(self, force_bsstream=False):
        if not force_bsstream:
            if not hasattr(self, "_pv"):
                return None
            return self._pv.get()
        else:
            raise NotImplementedError(
                "setup of stream for bs channel not implemented yet"
            )

    def get_stream_state(self, timeout=1):
        return pollStream(self.bs_channel, timeout=1)

    def create_stream_callback(self, foo):
        with source(channels=[self.bs_channel]) as s:
            done = False
            while not done:
                done = foo(s.receive())

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

    @property
    def data(self):
        return self._data[
            self._accumulate["ix"]
            + 1 : self._accumulate["ix"]
            + 1
            + self._accumulate["n_buffer"]
        ]


@get_from_archive
class DetectorBsCam:
    def __init__(self, bschannel, name=None):
        self.name = name
        self.bschannel = bschannel
        self.alias = Alias(name, channel=bschannel, channeltype="BSCAM")
