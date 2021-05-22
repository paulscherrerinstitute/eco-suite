from epics import PV
from ..aliases import Alias
from ..elements.assembly import Assembly
from ..epics.adjustable import AdjustablePvEnum


class PhotonShutter(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self._append(AdjustablePvEnum, pvname, name="request")

    def open(self):
        self.request(1)

    def close(self):
        self.request(0)

    def __call__(self, *args):
        if args:
            self.request.set_target_value(args[0])
        else:
            return self.request.get_current_value()


class SafetyShutter:
    def __init__(self, pvname, name=None):
        self.name = name
        self.pv = PV(pvname)
        if name:
            self.alias = Alias(name, channel=pvname, channeltype="CA")

    def open(self):
        self.pv.put(1)
