from cam_server import CamClient, PipelineClient

from eco.devices_general.utilities import Changer
from eco.elements.adj_obj import AdjustableObject, DetectorObject
from eco.elements.detector import DetectorGet
from ..aliases import Alias, append_object_to_object
from ..elements.adjustable import AdjustableVirtual, AdjustableGetSet, value_property
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from ..elements.assembly import Assembly
from .motors import MotorRecord
import sys
from pathlib import Path
import time

sys.path.append("/sf/bernina/config/src/python/sf_databuffer/")
import bufferutils

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


@value_property
class Pipeline(Assembly):
    def __init__(self, pipeline_name, name=None, camserver_group=None):
        super().__init__(name=name)
        self.pipeline_name = pipeline_name
        self.camserver_group = camserver_group
        self._append(AdjustableGetSet, 
                     self._get_config, 
                     self._set_config, 
                     cache_get_seconds =.05, 
                     precision=0, 
                     check_interval=None, 
                     name='_config', 
                     is_setting=False, 
                     is_display=False)
        
        self._append(AdjustableObject, self._config, name='config',is_setting=True, is_display='recursive')
        self._append(DetectorGet, self._get_info, cache_get_seconds =.05, name='_info', is_setting=False, is_display=False)
        self._append(DetectorObject, self._info, name='info', is_display='recursive', is_setting=False)
        

    # @property
    # def cc(self):
    #     return get_camclient()

    @property
    def pc(self):
        return get_pipelineclient()

    def _get_config(self):
        return self.pc.get_pipeline_config(self.pipeline_name)

    def _set_config(self, value, hold=False):
        return Changer(
            target=value,
            changer=lambda v: self.pc.set_pipeline_config(self.pipeline_name, v),
            hold=hold,
        )

    def _get_info(self, reject_kws = ['config']):
        info = self.pc.get_instance_info(self.pipeline_name)
        for rkw in reject_kws:
            info.pop(rkw)
        return info
    
    def _get_stream(self):
        return self.pc.get_instance_stream(self.pipeline_name)

    

    # ### convenience functions ###
    # def set_alias(self, alias=None):
    #     """creates an alias in the camera config on the server. If no alias is provided, it defaults to the camera name"""
    #     if not alias:
    #         alias = self.camserver_alias
    #     self.set_config_fields({"alias": [alias.upper()]})

    # def set_group(self, group=None):
    #     """creates an alias in the camera config on the server. If no alias is provided, it defaults to the camera name"""
    #     if not group:
    #         group = self.camserver_group
    #     self.set_config_fields({"group": group})

    def restart_pipeline(self):
        base_directory = "/sf/bernina/config/src/python/sf_databuffer/"
        label = self.pipeline_name

        policies = bufferutils.read_files(base_directory / Path("policies"), "policies")
        sources = bufferutils.read_files(base_directory / Path("sources"), "sources")
        sources_new = sources.copy()

        # Only for debugging purposes
        labeled_sources = bufferutils.get_labeled_sources(sources_new, label)
        for s in labeled_sources:
            bufferutils.logging.info(f"Restarting {s['stream']}")

        sources_new = bufferutils.remove_labeled_source(sources_new, label)

        # Stopping the removed source(s)
        bufferutils.update_sources_and_policies(sources_new, policies)

        # Starting the source(s) again
        bufferutils.update_sources_and_policies(sources, policies)

    def stop(self):
        self.pc.stop_instance(self.pipeline_name)

    # def set_cross(self, x, y, x_um_per_px=None, y_um_per_px=None):
    #     """set x and y position of the refetence marker on a camera  px/um calibration is conserved if no new value is given"""
    #     calib = self.get_current_value()["camera_calibration"]
    #     if calib:
    #         if not x_um_per_px:
    #             x_um_per_px = calib["reference_marker_width"] / abs(
    #                 calib["reference_marker"][2] - calib["reference_marker"][0]
    #             )
    #         if not y_um_per_px:
    #             y_um_per_px = calib["reference_marker_height"] / abs(
    #                 calib["reference_marker"][3] - calib["reference_marker"][1]
    #             )
    #     else:
    #         calib = {}
    #         x_um_per_px = 1
    #         y_um_per_px = 1

    #     calib["reference_marker"] = [x - 1, y - 1, x + 1, y + 1]
    #     calib["reference_marker_width"] = 2 * x_um_per_px
    #     calib["reference_marker_height"] = 2 * y_um_per_px
    #     self.set_config_fields(fields={"camera_calibration": calib})

    # def set_config_fields_multiple_cams(self, conditions, fields):
    #     """
    #     conditions is a dictionary holding the conditions to select a subset of cameras, e.g. {"group": Bernina}
    #     fields is a dictionary containing the keys and values that should be updated, e.g. fields={'alias': ['huhu', 'duda']}
    #     """
    #     cams = {
    #         cam: self.cc.get_camera_config(cam)
    #         for cam in self.cc.get_cameras()
    #         if not "jungfrau" in cam
    #     }
    #     cams_selected = {}
    #     for cam, cfg in cams.items():
    #         try:
    #             if all([value in cfg[key] for key, value in conditions.items()]):
    #                 cfg.update(fields)
    #                 self.cc.set_camera_config(cam, cfg)
    #                 cams_selected[cam] = cfg
    #         except Exception as e:
    #             print(f"{type(e)} {e} in cam {cam}")
    #     return cams_selected

    # def clear_all_bernina_aliases(self, verbose=True):
    #     cams_selected = self.set_config_fields_multiple_cams(
    #         conditions={"group": "Bernina"}, fields={"alias": []}
    #     )
    #     if verbose:
    #         print(f"Reset alias of {len(cams_selected)} cameras")
    #         print(cams_selected.keys())

    # def __repr__(self):
    #     s = f"**Camera Server Config {self.pipeline_name} with Alias {self.name}**\n"
    #     for key, item in self.get_current_value().items():
    #         s += f"{key:20} : {item}\n"
    #     return s


class CameraBasler(Assembly):
    def __init__(self, pvname, camserver_alias=None, name=None, camserver_group=None):
        super().__init__(name=name)
        self.pvname = pvname
        if not camserver_alias:
            camserver_alias = self.alias.get_full_name() + f" ({pvname})"
        self._append(
            Pipeline,
            self.pvname,
            camserver_alias=camserver_alias,
            camserver_group=camserver_group,
            name="config_cs",
            is_display=False,
        )
        self.config_cs.set_alias()
        if camserver_group is not None:
            self.config_cs.set_group()
        self._append(
            AdjustablePvEnum,
            self.pvname + ":INIT",
            name="initialize",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":CAMERASTATUS",
            name="running",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":BOARD",
            name="board_no",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":SERIALNR",
            name="serial_no",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":EXPOSURE",
            name="_exposure_time",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":ACQMODE",
            name="_acq_mode",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":RECMODE",
            name="_req_mode",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":STOREMODE",
            name="_store_mode",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":BINX",
            name="_binx",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":BINY",
            name="_biny",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":REGIONX_START",
            name="_roixmin",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":REGIONX_END",
            name="_roixmax",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":REGIONY_START",
            name="_roiymin",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":REGIONY_END",
            name="_roiymax",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":SET_PARAM",
            name="_set_parameters",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":TRIGGER",
            name="trigger_on",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":AMPGAIN",
            name="_gain",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":TRIGGERSOURCE",
            name="trigger_source",
            is_setting=True,
            is_display=False,
        )
        # append_object_to_object(self,PvEnum,self.pvname+':TRIGGEREDGE',name='trigger_edge')
        self._append(
            AdjustableGetSet,
            self._exposure_time.get_current_value,
            lambda value: self._set_params((self._exposure_time, value)),
            name="exposure_time",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustableGetSet,
            self._gain.get_current_value,
            lambda value: self._set_params((self._gain, value)),
            name="gain",
            is_setting=True,
            is_display=True,
        )

        def set_roi(roi):
            self._set_params(
                [self._roixmin, roi[0]],
                [self._roixmax, roi[1]],
                [self._roiymin, roi[2]],
                [self._roiymax, roi[3]],
            )
            return (roi[0], roi[1], roi[2], roi[3])

        self._append(
            AdjustableVirtual,
            [self._roixmin, self._roixmax, self._roiymin, self._roiymax],
            lambda x_from, x_to, y_from, y_to: [x_from, x_to, y_from, y_to],
            set_roi,
            name="roi",
            is_setting=True,
        )

    def _set_params(self, *args):
        self.running(1)
        for ob, val in args:
            ob(val)
        self._set_parameters(1)
        self.running(2)

    def gui(self):
        self._run_cmd(
            f'caqtdm -macro "NAME={self.pvname},CAMNAME={self.pvname}" /sf/controls/config/qt/Camera/CameraExpert.ui'
        )


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
            Pipeline,
            self.pvname,
            camserver_alias=camserver_alias,
            name="config_cs",
        )
        self.config_cs.set_alias()
        self._append(AdjustablePvEnum, self.pvname + ":INIT", name="initialize")
        self._append(
            AdjustablePvEnum,
            self.pvname + ":CAMERASTATUS",
            name="camera_status",
            is_display=True,
        )
        self._append(AdjustablePv, self.pvname + ":BOARD", name="board_no")
        self._append(AdjustablePv, self.pvname + ":SERIALNR", name="serial_no")
        self._append(AdjustablePv, self.pvname + ":EXPOSURE", name="_exposure_time")
        self._append(AdjustablePvEnum, self.pvname + ":ACQMODE", name="_acq_mode")
        self._append(AdjustablePvEnum, self.pvname + ":RECMODE", name="_req_mode")
        self._append(AdjustablePvEnum, self.pvname + ":STOREMODE", name="_store_mode")
        self._append(AdjustablePv, self.pvname + ":HSSPEED", name="_hs_speed")
        self._append(
            AdjustablePvEnum, self.pvname + ":SCMOSREADOUT", name="_readout_mode"
        )
        self._append(AdjustablePv, self.pvname + ":BINY", name="_binx")
        self._append(AdjustablePv, self.pvname + ":BINY", name="_biny")
        self._append(AdjustablePv, self.pvname + ":REGIONX_START", name="_roixmin")
        self._append(AdjustablePv, self.pvname + ":REGIONX_END", name="_roixmax")
        self._append(AdjustablePv, self.pvname + ":REGIONY_START", name="_roiymin")
        self._append(AdjustablePv, self.pvname + ":REGIONY_END", name="_roiymax")
        self._append(
            AdjustablePvEnum, self.pvname + ":SET_PARAM", name="_set_parameters"
        )
        self._append(AdjustablePvEnum, self.pvname + ":TRIGGER", name="trigger_on")
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
            [self.camera_status],
            lambda stat: stat == 2,
            lambda running: 2 if running else 1,
            name="running",
            is_setting=True,
        )

        def set_roi(roi):
            self._set_params(
                [self._roixmin, roi[0]],
                [self._roixmax, roi[1]],
                [self._roiymin, roi[2]],
                [self._roiymax, roi[3]],
            )
            return (roi[0], roi[1], roi[2], roi[3])

        self._append(
            AdjustableVirtual,
            [self._roixmin, self._roixmax, self._roiymin, self._roiymax],
            lambda x_from, x_to, y_from, y_to: [x_from, x_to, y_from, y_to],
            set_roi,
            name="roi",
            is_setting=True,
        )

    def _set_params(self, *args):
        self.running(False)
        for ob, val in args:
            ob(val)
        self._set_parameters(1)
        self.running(True)

    def gui(self):
        self._run_cmd(
            f'caqtdm -macro "NAME={self.pvname},CAMNAME={self.pvname}" /sf/controls/config/qt/Camera/CameraExpert.ui'
        )
