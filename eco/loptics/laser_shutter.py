from epics import caput, caget

from ..elements.assembly import Assembly
from ..epics_utils.adjustable import AdjustablePvEnum


class laser_shutter:
    def __init__(self, Id):
        self.Id = Id

    def __repr__(self):
        return self.get_status()

    def get_status(self):
        Id = self.Id
        status = caget(Id + ":FrontUnivOut3_SOURCE")
        if status == 4:
            return "open"
        elif status == 3:
            return "close"
        else:
            return "unknown"

    def open(self):
        caput(self.Id + ":FrontUnivOut3_SOURCE", 4)

    def close(self):
        caput(self.Id + ":FrontUnivOut3_SOURCE", 3)


class LaserSafetyShutter(Assembly):
    """Laser safety shutter: EVR front-panel output source select plus the
    laser beam-transport interlock's open/close command PVs.

    Mirrors ``/sf/bernina/bin/laseron``/``laseroff``:
      open:  ``<pvname_evr>:FrontUnivOut3_SOURCE2`` -> LOW,
             ``<pvname_transport>:OPEN_TRANS`` -> ON
      close: ``<pvname_evr>:FrontUnivOut3_SOURCE2`` -> HIGH,
             ``<pvname_transport>:SHUT_TRANS`` -> ON
    """

    def __init__(self, pvname_evr, pvname_transport, name=None):
        super().__init__(name=name)
        self._append(
            AdjustablePvEnum,
            f"{pvname_evr}:FrontUnivOut3_SOURCE2",
            name="_source_select",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            f"{pvname_transport}:OPEN_TRANS",
            name="_open_cmd",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            f"{pvname_transport}:SHUT_TRANS",
            name="_close_cmd",
            is_setting=False,
            is_display=False,
        )

    def open(self):
        self._source_select.set_target_value("LOW")
        self._open_cmd.set_target_value("ON")

    def close(self):
        self._source_select.set_target_value("HIGH")
        self._close_cmd.set_target_value("ON")

    def get_current_value(self):
        return "open" if self._source_select.get_current_value().name == "LOW" else "closed"

    def __call__(self):
        return self.get_current_value()
