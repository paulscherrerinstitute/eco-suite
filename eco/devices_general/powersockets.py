from ..epics.adjustable import AdjustablePvEnum, AdjustablePvString
from ..elements.assembly import Assembly
from ..epics.detector import DetectorPvEnum, DetectorPvData


class PowerSocket(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            AdjustablePvString,
            pvname + ":POWERONOFF-DESC",
            name="description",
            is_setting=True,
        )
        self._append(
            DetectorPvEnum, pvname + ":POWERONOFF-RB", name="stat", is_display=True
        )
        self._append(
            AdjustablePvEnum,
            pvname + ":POWERONOFF",
            name="on_switch",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvString,
            pvname + ":POWERCYCLE",
            name="powercycle_for_10s",
            is_setting=False,
            is_display=False,
        )

    def toggle(self):
        self.on_switch(int(not (self.stat() == 1)))

    def on(self):
        self.on_switch(1)

    def off(self):
        self.on_switch(0)

    def __call__(self, *args):
        if not args:
            self.toggle()
        else:
            self.on_switch(args[0])


class GudeStrip(Assembly):
    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        for n in range(1, 5):
            self._append(
                PowerSocket,
                pvbase + f"-CH{n}",
                is_display="recursive",
                is_setting=True,
                name=f"ch{n}",
            )
        self._append(
            DetectorPvData, pvbase + ":CURRENT", is_display=True, name="current"
        )
        self._append(
            DetectorPvData, pvbase + ":VOLTAGE", is_display=True, name="voltage"
        )
