from epics import PV
from ..aliases import Alias


class PhotonShutter:
    def __init__(self, pvname, name=None):
        self.name = name
        self.pv = PV(pvname)
        if name:
            self.alias = Alias(name, channel=pvname, channeltype="CA")

    def open(self):
        self.pv.put(1)

    def close(self):
        self.pv.put(0)

    def get_state(self):
        return self.pv.get()


class SafetyShutter:
    def __init__(self, pvname, name=None):
        self.name = name
        self.pv = PV(pvname)
        if name:
            self.alias = Alias(name, channel=pvname, channeltype="CA")

    def open(self):
        self.pv.put(1)
