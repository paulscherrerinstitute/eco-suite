from ..aliases import Alias, append_object_to_object
from .adjustable import PvRecord, PvEnum, AdjustableGetSet, AdjustableVirtual
from ..elements import Assembly
from .motors import MotorRecord


class CameraBasler(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(PvEnum, self.pvname + ":INIT", name="initialize")
        self._append(PvEnum, self.pvname + ":CAMERA", name="running")
        self._append(PvRecord, self.pvname + ":BOARD", name="board_no")
        self._append(PvRecord, self.pvname + ":SERIALNR", name="serial_no")
        self._append(PvRecord, self.pvname + ":EXPOSURE", name="_exposure_time")
        self._append(PvEnum, self.pvname + ":ACQMODE", name="_acq_mode")
        self._append(PvEnum, self.pvname + ":RECMODE", name="_req_mode")
        self._append(PvEnum, self.pvname + ":STOREMODE", name="_store_mode")
        self._append(PvRecord, self.pvname + ":BINY", name="_binx")
        self._append(PvRecord, self.pvname + ":BINY", name="_biny")
        self._append(PvRecord, self.pvname + ":REGIONX_START", name="_roixmin")
        self._append(PvRecord, self.pvname + ":REGIONX_END", name="_roixmax")
        self._append(PvRecord, self.pvname + ":REGIONY_START", name="_roiymin")
        self._append(PvRecord, self.pvname + ":REGIONY_END", name="_roiymax")
        self._append(PvEnum, self.pvname + ":SET_PARAM", name="_set_parameters")
        self._append(PvEnum, self.pvname + ":TRIGGER", name="trigger_on")
        self._append(PvRecord, self.pvname + ":AMPGAIN", name="_gain")
        self._append(PvEnum, self.pvname + ":TRIGGERSOURCE", name="trigger_source")
        # append_object_to_object(self,PvEnum,self.pvname+':TRIGGEREDGE',name='trigger_edge')
        self._append(
            AdjustableGetSet,
            self._exposure_time.get_current_value,
            lambda value: self._set_params((self._exposure_time, value)),
            name="exposure_time",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self._gain.get_current_value,
            lambda value: self._set_params((self._gain, value)),
            name="gain",
            is_setting=True,
        )
        self._append(
            AdjustableVirtual,
            [self._roixmin, self._roixmax, self._roiymin, self._roiymax],
            lambda x_from, x_to, y_from, y_to: [x_from, x_to, y_from, y_to],
            lambda roi: (roi[0], roi[1], roi[2], roi[3]),
            name="roi",
            is_setting=True,
        )

    def _set_params(self, *args):
        self.running(0)
        for ob, val in args:
            ob(val)
        self._set_parameters(1)
        self.running(1)


class QioptiqMicroscope(CameraBasler):
    def __init__(self, pvname_camera, pvname_zoom=None, pvname_focus=None, name=None):
        super().__init__(pvname_camera, name=name)
        if pvname_zoom:
            self._append(MotorRecord, pvname_zoom, name="zoom", is_setting=True)
        if pvname_focus:
            self._append(MotorRecord, pvname_focus, name="focus", is_setting=True)
