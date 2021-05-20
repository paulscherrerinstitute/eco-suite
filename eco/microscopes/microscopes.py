from ..elements.assembly import Assembly
from ..devices_general.cameras_swissfel import CameraBasler
from ..devices_general.motors import MotorRecord
from ..devices_general.adjustable import PvRecord, AdjustableVirtual, spec_convenience
from epics import PV
import numpy as np


class MicroscopeMotorRecord(Assembly):
    def __init__(
        self,
        pvname_camera=None,
        camserver_alias=None,
        # camserver_alias=None,
        pvname_zoom=None,
        pvname_focus=None,
        name=None,
    ):
        super().__init__(name=name)
        if pvname_camera:
            self._append(
                CameraBasler,
                pvname_camera,
                camserver_alias=camserver_alias,
                name="camera",
                is_setting=True,
                is_status="recursive",
            )
        if pvname_zoom:
            pv_base = pvname_zoom.split(":")[0]
            port = pvname_zoom.split(":MOT_")[-1]

            self._append(
                MotorRecord,
                pvname_zoom,
                name="zoom",
                is_setting=True,
                is_status=True,
                schneider_config=(pvname_zoom, pv_base + f":{port}"),
            )
        if pvname_focus:
            self._append(
                MotorRecord,
                pvname_focus,
                name="focus",
                is_setting=True,
                is_status=True,
                schneider_config=(pvname_zoom, pv_base + f":{port}"),
            )


class BerninaInlineMicroscope(Assembly):
    def __init__(
        self,
        pvname_camera="",
        camserver_alias="inline_microscope",
        # camserver_alias=None,
        name="inline_microscope",
    ):
        super().__init__(name=name)
        self._append(
            CameraBasler,
            pvname_camera,
            camserver_alias=camserver_alias,
            name="camera",
            is_setting=True,
            is_status="recursive",
        )
        # self._
        self._append(OptoSigmaZoom, name="zoom", is_setting=[True])

    # def zoom


@spec_convenience
class OptoSigmaZoom(Assembly):
    def __init__(
        self,
        pv_get_position="SARES20-OPSI:MOT_RB.VAL",
        pv_set_position="SARES20-OPSI:MOT_SP.VAL",
        pv_commands="SARES20-OPSI:debug.VAL",
        name=None,
    ):
        super().__init__(name=name)
        self.settings.append(self)
        self._append(
            PvRecord,
            pv_set_position,
            pv_get_position,
            accuracy=1,
            name="zoom_raw",
            is_setting=False,
        )
        self.command_pv = PV(pv_commands)
        self._append(
            AdjustableVirtual,
            [self.zoom_raw],
            lambda x: abs(round(x / 4260 * 100) - 100),
            lambda x: round(abs(x - 100) / 100 * 4260),
            name="zoom",
            is_setting=False,
        )

    def home(self):
        self.command_pv.put("H:1")

    def get_current_value(self):
        return self.zoom.get_current_value()

    def set_target_value(self, value, **kwargs):
        return self.zoom.set_target_value(value, **kwargs)
