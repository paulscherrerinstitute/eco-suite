from cam_server import CamClient, PipelineClient
from matplotlib.backend_bases import MouseButton
from eco.devices_general.utilities import Changer
from eco.epics.detector import DetectorPvData

from ..aliases import Alias, append_object_to_object
from ..elements.adjustable import AdjustableVirtual, AdjustableGetSet, value_property
from eco.elements.detector import DetectorGet
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from eco.elements.adj_obj import AdjustableObject, DetectorObject
from .pipelines_swissfel import Pipeline
from ..elements.assembly import Assembly
from .motors import MotorRecord
import sys
from pathlib import Path
import time
import matplotlib.pyplot as plt
import numpy as np
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
class CamserverConfig2(Assembly):
    def __init__(self, cam_id, camserver_alias=None, name=None, camserver_group=None):
        super().__init__(name=name)
        self.cam_id = cam_id
        self.camserver_alias = camserver_alias
        self.camserver_group = camserver_group
        self._cross = None
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

    @property
    def pc(self):
        return get_pipelineclient()

    @property
    def cc(self):
        return get_camclient()

    def _get_config(self):
        return  self.cc.get_camera_config(self.cam_id)

    def _set_config(self, value, hold=False):
        return Changer(
            target=value,
            changer=lambda v: self.cc.set_camera_config(self.cam_id, v),
            hold=hold,
        )

    def _get_info(self):
        fields = {
            "camera_geometry": self.cc.get_camera_geometry(self.cam_id),
            "pipelines": self._get_pipelines(),
        }
        return  fields

    ### convenience functions ###
    def get_camera_image(self):
        im = self.cc.get_camera_array(self.cam_id)
        return im

    def set_alias(self, alias=None):
        """creates an alias in the camera config on the server. If no alias is provided, it defaults to the camera name"""
        if not alias:
            alias = self.camserver_alias
        self.set_config_fields({"alias": [alias.upper()]})

    def set_group(self, group=None):
        """adds the camera to the given group"""
        if not group:
            group = self.camserver_group
        self.config.group(group)

    def _get_pipelines(self):
        return [p for p in self.pc.get_pipelines() if self.cam_id in p]

    def set_config_fields(self, fields):
        """fields is a dictionary containing the keys and values that should be updated, e.g. fields={'group': ['Laser', 'Bernina']}"""
        config = self.cc.get_camera_config(self.cam_id)
        config.update(fields)
        self.cc.set_camera_config(self.cam_id, config)

    def set_config_fields_multiple_cams(self, conditions, fields):
        """
        conditions is a dictionary holding the conditions to select a subset of cameras, e.g. {"group": Bernina}
        fields is a dictionary containing the keys and values that should be updated, e.g. fields={'alias': ['huhu', 'duda']}
        """
        cams = {
            cam: self.cc.get_camera_config(cam)
            for cam in self.cc.get_cameras()
            if not "jungfrau" in cam
        }
        cams_selected = {}
        for cam, cfg in cams.items():
            try:
                if all([value in cfg[key] for key, value in conditions.items()]):
                    cfg.update(fields)
                    self.cc.set_camera_config(cam, cfg)
                    cams_selected[cam] = cfg
            except Exception as e:
                print(f"{type(e)} {e} in cam {cam}")
        return cams_selected

    def clear_all_bernina_aliases(self, verbose=True):
        cams_selected = self.set_config_fields_multiple_cams(
            conditions={"group": "Bernina"}, fields={"alias": []}
        )
        if verbose:
            print(f"Reset alias of {len(cams_selected)} cameras")
            print(cams_selected.keys())

    def _run_cmd(self, line, silent=True):
        if silent:
            print(f"Starting following commandline silently:\n" + line)
            with open(os.devnull, "w") as FNULL:
                subprocess.Popen(
                    line, shell=True, stdout=FNULL, stderr=subprocess.STDOUT
                )
        else:
            subprocess.Popen(line, shell=True)

    def gui(self):
        self._run_cmd(f'csm')

@value_property
class CamserverConfig(Assembly):
    def __init__(self, cam_id, camserver_alias=None, name=None, camserver_group=None):
        super().__init__(name=name)
        self.cam_id = cam_id
        self.camserver_alias = camserver_alias
        self.camserver_group = camserver_group

    @property
    def cc(self):
        return get_camclient()

    @property
    def pc(self):
        return get_pipelineclient()

    def get_current_value(self):
        return self.cc.get_camera_config(self.cam_id)

    def set_target_value(self, value, hold=False):
        return Changer(
            target=value,
            changer=lambda v: self.cc.set_camera_config(self.cam_id, v),
            hold=hold,
        )

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
        self.set_config_fields({"alias": [alias.upper()]})

    def set_group(self, group=None):
        """creates an alias in the camera config on the server. If no alias is provided, it defaults to the camera name"""
        if not group:
            group = self.camserver_group
        self.set_config_fields({"group": group})

    def restart_pipeline(self):
        base_directory = "/sf/bernina/config/src/python/sf_databuffer/"
        label = self.cam_id

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

    def set_config_fields_multiple_cams(self, conditions, fields):
        """
        conditions is a dictionary holding the conditions to select a subset of cameras, e.g. {"group": Bernina}
        fields is a dictionary containing the keys and values that should be updated, e.g. fields={'alias': ['huhu', 'duda']}
        """
        cams = {
            cam: self.cc.get_camera_config(cam)
            for cam in self.cc.get_cameras()
            if not "jungfrau" in cam
        }
        cams_selected = {}
        for cam, cfg in cams.items():
            try:
                if all([value in cfg[key] for key, value in conditions.items()]):
                    cfg.update(fields)
                    self.cc.set_camera_config(cam, cfg)
                    cams_selected[cam] = cfg
            except Exception as e:
                print(f"{type(e)} {e} in cam {cam}")
        return cams_selected

    def clear_all_bernina_aliases(self, verbose=True):
        cams_selected = self.set_config_fields_multiple_cams(
            conditions={"group": "Bernina"}, fields={"alias": []}
        )
        if verbose:
            print(f"Reset alias of {len(cams_selected)} cameras")
            print(cams_selected.keys())

    def __repr__(self):
        s = f"**Camera Server Config {self.cam_id} with Alias {self.name}**\n"
        for key, item in self.get_current_value().items():
            s += f"{key:20} : {item}\n"
        return s


class CameraBasler(Assembly):
    def __init__(self, pvname, camserver_alias=None, name=None, camserver_group=None):
        super().__init__(name=name)
        self.pvname = pvname
        if not camserver_alias:
            camserver_alias = self.alias.get_full_name() + f" ({pvname})"
        else:
            camserver_alias = camserver_alias + f" ({pvname})"
        self._append(
            CamserverConfig2,
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
            DetectorPvData,
            self.pvname + ":DEVICEFREQUENCY",
            has_unit=True,
            name="frequency",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":SW_PULSID_SRC",
            name="bscheck",
            is_setting=True,
            is_display=True,
        )
        self._append(
            DetectorPvData,
            self.pvname + ":ERRORCOUNTER",
            name="pulse_id_error_sum",
            is_setting=False,
            is_display=True,
        )
        self._append(
            DetectorPvData,
            self.pvname + ":FEEDBACKTIME0",
            name="response_time_bs",
            is_setting=False,
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
            unit = 'ms',
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

    def get_camera_images(self, n):
        imgs=[]
        while(len(np.unique(imgs, axis=0))<n):
            imgs.append(self.config_cs.get_camera_image())
            return np.unique(imgs, axis=0)

    def set_cross(self, x=None, y=None, x_um_per_px=None, y_um_per_px=None, n_images=10):
        """set x and y position of the refetence marker on a camera  px/um calibration is conserved if no new value is given"""
        def prompt(x,y,x_um_per_px,y_um_per_px):
            x=int(x)
            y=int(y)
            answer = input(f"Set the new cross position [{x}, {y}] with calibration [{x_um_per_px:.3}, {y_um_per_px:.3}] ([y]/n)?") or "y"
            if answer == "y":
                calib.reference_marker([x - 1, y - 1, x + 1, y + 1])
                calib.reference_marker_width(2 * x_um_per_px)
                calib.reference_marker_height(2 * y_um_per_px)
                print("\nNew calibration:")
                print(calib)
            else:
                print("aborted")

        calib = self.config_cs.config.camera_calibration
        print("Current calibration:")
        print(calib)
        try:
            w = calib.reference_marker_width() 
            h = calib.reference_marker_height() 
            rm = calib.reference_marker()
            if not x_um_per_px:
                x_um_per_px = w / abs(rm[2] - rm[0])
            if not y_um_per_px:
                y_um_per_px = h / abs(rm[3] - rm[1])
        except:
            rm=[0,0,0,0]
            x_um_per_px = 1
            y_um_per_px = 1
        if x is None or y is None:
            x = (rm[2] + rm[0])/2
            y = (rm[3] + rm[1])/2
            img = np.mean(self.get_camera_images(n_images), axis=0)
            run = True
            def on_click(event):
                if event.button is MouseButton.LEFT:
                    x = event.xdata
                    y = event.ydata
                    cross_plot.set_data(x,y)
                    plt.draw()
                    print(f'cross at x: {x:.4} and y: {y:.4}')
                    self.config_cs._cross = [x,y]
                else:
                    plt.disconnect(bid)
                    plt.close(self.config_cs.cam_id)
                    
            fig = plt.figure(num=self.config_cs.cam_id)
            plt.title(f"Set cross: left mouse click, Finish: right click")
            plt.imshow(img)
            cross_plot = plt.plot(x,y, '+r', markersize=10)[0]
            bid = fig.canvas.mpl_connect('button_press_event', on_click)
            plt.show(block=True)
            x, y = self.config_cs._cross
            print(x,y)
        prompt(x,y,x_um_per_px,y_um_per_px)
        


    def gui(self):
        self._run_cmd(
            f'caqtdm -macro "NAME={self.pvname},CAMNAME={self.pvname}" /sf/controls/config/qt/Camera/CameraExpert.ui'
        )

# NB: please note this should be moved to microscopes which are using cameras plus zooms,
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
        else:
            camserver_alias = camserver_alias + f"({pvname})"
        self._append(
            CamserverConfig,
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


# NB: please note this should be moved to microscopes which are using cameras plus zooms,
class FeturaMicroscope(CameraPCO):
    def __init__(self, pvname_camera, pvname_base_zoom=None, name=None, camserver_alias=None):
        super().__init__(pvname_camera, name=name, camserver_alias=camserver_alias)
        if pvname_base_zoom:
            self._append(AdjustablePv, pvsetname=pvname_base_zoom+":POS_SP", pvreadbackname=pvname_base_zoom+":POS_RB", name="_zoom_motor", is_setting=True, is_display=False)
            def getv(v):
                return v/10.
            def setv(v):
                return v*10.
            self._append(AdjustableVirtual, [self._zoom_motor], getv, setv, name="zoom", unit="%")
        
        