from cam_server import CamClient, PipelineClient
from ..aliases import Alias, append_object_to_object
from .adjustable import PvRecord, PvEnum, AdjustableGetSet, AdjustableVirtual
from ..elements import Assembly
from .motors import MotorRecord

CAM_CLIENT = None
PIPELINE_CLIENT = None


def get_camclient():
    global CAM_CLIENT
    if not CAM_CLIENT:
        CAM_CLIENT = CamClient()
    return CAM_CLIENT


def get_pipelineclient():
    global PIPELINE_CLIENT
    if not PIPELINE_CLIENT:
        PIPELINE_CLIENT = PipelineClient()
    return PIPELINE_CLIENT


class CamserverConfig(Assembly):
    def __init__(self, cam_id, camserver_alias=None, name=None):
        super().__init__(name=name)
        self.cam_id = cam_id
        self.camserver_alias = camserver_alias

    @property
    def cc(self):
        return get_camclient()

    @property
    def pc(self):
        return get_pipelineclient()

    def get_current_value(self):
        return self.cc.get_camera_config(self.cam_id)

    def set_config_fields(self, fields):
        """fields is a dictionary containing the keys and values that should be updated, e.g. fields={'group': ['Laser', 'Bernina']}"""
        config = self.get_current_value()
        config.update(fields)
        self.cc.set_camera_config(self.cam_id, config)

    ### convenience functions ###
    def set_alias(self, alias=None):
        """creates an alias in the camera config on the server. If no alias is provided, it defaults to the camera name"""
        if not alias:
            alias = self.camserver_alias
        self.set_config_fields({"alias": [alias]})

    def stop(self):
        self.cc.stop_instance(self.cam_id)

    def set_cross(self, x, y, x_um_per_px=None, y_um_per_px=None):
        """set x and y position of the refetence marker on a camera  px/um calibration is conserved if no new value is given"""
        calib = self.get_current_value()["camera_calibration"]
        if calib:
            if not x_um_per_px:
                x_um_per_px = calib["reference_marker_width"] / abs(
                    calib["reference_marker"][2] - calib["reference_marker"][0]
                )
            if not y_um_per_px:
                y_um_per_px = calib["reference_marker_height"] / abs(
                    calib["reference_marker"][3] - calib["reference_marker"][1]
                )
        else:
            calib = {}
            x_um_per_px = 1
            y_um_per_px = 1

        calib["reference_marker"] = [x - 1, y - 1, x + 1, y + 1]
        calib["reference_marker_width"] = 2 * x_um_per_px
        calib["reference_marker_height"] = 2 * y_um_per_px
        self.set_config_fields(fields={"camera_calibration": calib})

    def __repr__(self):
        s = f"**Camera Server Config {self.cam_id} with Alias {self.name}**\n"
        for key, item in self.get_current_value().items():
            s += f"{key:20} : {item}\n"
        return s


class CameraBasler(Assembly):
    def __init__(self, pvname, camserver_alias=None, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        if not camserver_alias:
            camserver_alias = self.alias.get_full_name() + f" ({pvname})"
        self._append(
            CamserverConfig,
            self.pvname,
            camserver_alias=camserver_alias,
            name="config_cs",
        )
        self.config_cs.set_alias()
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


class CameraPCO(Assembly):
    def __init__(self, pvname, camserver_alias=None, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        if not camserver_alias:
            camserver_alias = self.alias.get_full_name() + f"({pvname})"
        self._append(
            CamserverConfig,
            self.pvname,
            camserver_alias=camserver_alias,
            name="config_cs",
        )
        self.config_cs.set_alias()
        self._append(PvEnum, self.pvname + ":INIT", name="initialize")
        self._append(PvEnum, self.pvname + ":CAMERA", name="running")
        self._append(PvRecord, self.pvname + ":BOARD", name="board_no")
        self._append(PvRecord, self.pvname + ":SERIALNR", name="serial_no")
        self._append(PvRecord, self.pvname + ":EXPOSURE", name="_exposure_time")
        self._append(PvEnum, self.pvname + ":ACQMODE", name="_acq_mode")
        self._append(PvEnum, self.pvname + ":RECMODE", name="_req_mode")
        self._append(PvEnum, self.pvname + ":STOREMODE", name="_store_mode")
        self._append(PvRecord, self.pvname + ":HSSPEED", name="_hs_speed")
        self._append(PvEnum, self.pvname + ":SCMOSREADOUT", name="_readout_mode")
        self._append(PvRecord, self.pvname + ":BINY", name="_binx")
        self._append(PvRecord, self.pvname + ":BINY", name="_biny")
        self._append(PvRecord, self.pvname + ":REGIONX_START", name="_roixmin")
        self._append(PvRecord, self.pvname + ":REGIONX_END", name="_roixmax")
        self._append(PvRecord, self.pvname + ":REGIONY_START", name="_roiymin")
        self._append(PvRecord, self.pvname + ":REGIONY_END", name="_roiymax")
        self._append(PvEnum, self.pvname + ":SET_PARAM", name="_set_parameters")
        self._append(PvEnum, self.pvname + ":TRIGGER", name="trigger_on")
        # append_object_to_object(self,PvEnum,self.pvname+':TRIGGEREDGE',name='trigger_edge')
        self._append(
            AdjustableGetSet,
            self._exposure_time.get_current_value,
            lambda value: self._set_params((self._exposure_time, value)),
            name="exposure_time",
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


False
False