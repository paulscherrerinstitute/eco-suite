from ..elements.assembly import Assembly
from ..aliases import Alias
from eco import ecocnf
from epics.pv import PV
from bsread.bsavail import pollStream
from bsread import dispatcher, source
from ..epics import get_from_archive
from escape import stream


@get_from_archive
class DetectorBsStream:
    def __init__(self, bs_channel, cachannel="same", name=None):
        self.name = name
        self.bs_channel = bs_channel
        if cachannel == "same":
            self.pvname = bs_channel
        else:
            self.pvname = cachannel
        if self.pvname:
            self._pv = PV(self.pvname)
        self.alias = Alias(name, channel=bs_channel, channeltype="BS")

        self.stream = stream.EscData(source=stream.EventSource(self.bs_channel, None))

    def get_current_value(self, force_bsstream=False):
        if not force_bsstream:
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


@get_from_archive
class DetectorBsCam:
    def __init__(self, bschannel, name=None):
        self.name = name
        self.bschannel = bschannel
        self.alias = Alias(name, channel=bschannel, channeltype="BSCAM")
