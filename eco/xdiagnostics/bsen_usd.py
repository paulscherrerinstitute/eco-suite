import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord
from ..devices_general.smaract import SmarActRecord
from ..devices_general.adjustable import PvRecord

from epics import PV
from ..aliases import Alias, append_object_to_object
from time import sleep

from cam_server import PipelineClient
from bsread import source
from matplotlib import pyplot as plt
import numpy as np


def addPvRecordToSelf(
    self, pvsetname, pvreadbackname=None, accuracy=None, sleeptime=0, name=None
):
    try:
        self.__dict__[name] = PvRecord(
            pvsetname,
            pvreadbackname=pvreadbackname,
            accuracy=accuracy,
            sleeptime=sleeptime,
            name=name,
        )
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find PV {name} (Id:{pvsetname} RB:{pvreadbackname})")


def addMotorRecordToSelf(self, name=None, Id=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find motor {name} (Id:{Id})")


def addSmarActRecordToSelf(self, Id=None, name=None):
    self.__dict__[name] = SmarActRecord(Id, name=name)
    self.alias.append(self.__dict__[name].alias)


class Bsen:
    def __init__(
        self,
        name=None,
        Id=None,
        processing_pipeline="SARES20-CAMS142-M5_psen_db",
        processing_instance="SARES20-CAMS142-M5_psen_db1",
        spectrometer_camera_channel="SARES20-CAMS142-M5:FPICTURE",
    ):
        self.Id = Id
        self.name = name
        self.alias = Alias(name)
        self.proc_client = PipelineClient()
        self.proc_pipeline = processing_pipeline
        self.proc_instance = processing_instance
        self.spectrometer_camera_channel = spectrometer_camera_channel

        self.motor_configuration = {
            "transl": {
                "id": "-LIC9",
                "pv_descr": " ",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
        }

        ### BSEN target position ###
        for name, config in self.motor_configuration.items():
            addSmarActRecordToSelf(self, Id=Id + config["id"], name=name)

    def get_proc_config(self):
        return self.proc_client.get_pipeline_config(self.proc_pipeline)

    def update_proc_config(self, cfg_dict):
        cfg = self.get_proc_config()
        cfg.update(cfg_dict)
        self.proc_client.set_instance_config(self.proc_instance, cfg)

    def acquire_and_plot_spectrometer_image(self, N_pulses=50):
        with source(channels=[self.spectrometer_camera_channel]) as s:
            im = []
            while True:
                m = s.receive()
                tim = m.data.data[self.spectrometer_camera_channel]
                if not tim:
                    continue
                if len(im) > N_pulses:
                    break
                im.append(tim.value)
        im = np.asarray(im).mean(axis=0)
        fig = plt.figure("bsen spectrometer pattern")
        fig.clf()
        ax = fig.add_subplot(111)
        ax.imshow(im)

    def set_stage_config(self):
        for name, config in self.motor_configuration.items():
            mot = self.__dict__[name]._device
            mot.put("NAME", config["pv_descr"])
            mot.put("STAGE_TYPE", config["type"])
            mot.put("SET_SENSOR_TYPE", config["sensor"])
            mot.put("CL_MAX_FREQ", config["speed"])
            sleep(0.5)
            mot.put("CALIBRATE.PROC", 1)

    def home_smaract_stages(self, stages=None):
        if stages == None:
            stages = self.motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = self.motor_configuration[name]
            mot = self.__dict__[name]._device
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.put("FRM_BACK.PROC", 1)
                while mot.get("STATUS") == 7:
                    sleep(1)
                if mot.get("GET_HOMED") == 0:
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.put("FRM_FORW.PROC", 1)
            elif config["home_direction"] == "forward":
                mot.put("FRM_FORW.PROC", 1)
                while mot.get("STATUS") == 7:
                    sleep(1)
                if mot.get("GET_HOMED") == 0:
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.put("FRM_BACK.PROC", 1)
        ## IR beam pointing mirrors
        try:
            addPvRecordToSelf(
                self,
                pvsetname="SLAAR21-LMNP-ESBIR11:DRIVE",
                pvreadbackname="SLAAR21-LMNP-ESBIR11:MOTRBV",
                accuracy=10,
                name="ry",
            )
            addPvRecordToSelf(
                self,
                pvsetname="SLAAR21-LMNP-ESBIR12:DRIVE",
                pvreadbackname="SLAAR21-LMNP-ESBIR12:MOTRBV",
                accuracy=10,
                name="rx",
            )
        except:
            print("Issue intializing picomotor white light beam pointing mirrors")
            pass

    def get_adjustable_positions_str(self):
        ostr = "*****BSEN target position******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def __repr__(self):
        return self.get_adjustable_positions_str()
