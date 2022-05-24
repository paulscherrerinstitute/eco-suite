import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord, SmaractStreamdevice
from ..devices_general.smaract import SmarActRecord
from ..epics.adjustable import AdjustablePv
from ..devices_general.cameras_swissfel import CameraBasler, CameraPCO

from epics import PV
from ..aliases import Alias, append_object_to_object
from time import sleep

from cam_server import PipelineClient
from bsread import source
from matplotlib import pyplot as plt
import numpy as np


from bsread import source
from collections import deque
from matplotlib.animation import FuncAnimation
from threading import Thread
from time import sleep
from ..elements.assembly import Assembly


def addPvRecordToSelf(
    self, pvsetname, pvreadbackname=None, accuracy=None, sleeptime=0, name=None
):
    try:
        self.__dict__[name] = AdjustablePv(
            pvsetname,
            pvreadbackname=pvreadbackname,
            accuracy=accuracy,
            name=name,
        )
        self.alias.append(self.__dict__[name].alias)
    except:
        print(
            f"Warning! Could not find motor {name} (SET PV:{pvsetname}) (RB PV:{pvreadbackname})"
        )


def addMotorRecordToSelf(self, name=None, Id=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find motor {name} (Id:{Id})")


def addSmarActRecordToSelf(self, Id=None, name=None):
    self.__dict__[name] = SmarActRecord(Id, name=name)
    self.alias.append(self.__dict__[name].alias)


class Bsen(Assembly):
    def __init__(
        self,
        name=None,
        processing_pipeline="SARES20-CAMS142-M5_psen_db",
        processing_instance="SARES20-CAMS142-M5_psen_db1",
        spectrometer_camera_channel="SARES20-CAMS142-M5:FPICTURE",
    ):
        super().__init__(name=name)
        self.proc_client = PipelineClient()
        self.proc_pipeline = processing_pipeline
        self.proc_instance = processing_instance
        self.spectrometer_camera_channel = spectrometer_camera_channel
        self._append(
            SmaractStreamdevice,
            "SARES23-LIC5",
            name="x_mirror_microscope",
            is_setting=True,
        )
        self._append(MotorRecord, "SARES20-MF2:MOT_1", name="x_target", is_setting=True)
        self._append(MotorRecord, "SARES20-MF2:MOT_2", name="y_target", is_setting=True)
        self._append(MotorRecord, "SARES20-MF2:MOT_3", name="z_target", is_setting=True)
        self._append(
            MotorRecord, "SARES20-MF2:MOT_4", name="zoom_microscope", is_setting=True
        )
        self._append(
            CameraBasler,
            "SARES20-PROF141-M1",
            name="camera_microscope",
            is_setting=True,
            is_display=False,
        )
        self._append(
            CameraPCO,
            "SARES20-CAMS142-M5",
            name="camera_spectrometer",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LMNP-ESBIR11:DRIVE",
            pvreadbackname="SLAAR21-LMNP-ESBIR11:MOTRBV",
            name="las_in_ry",
            accuracy=10,
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LMNP-ESBIR12:DRIVE",
            pvreadbackname="SLAAR21-LMNP-ESBIR12:MOTRBV",
            name="las_in_rx",
            accuracy=10,
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LMNP-ESBIR13:DRIVE",
            pvreadbackname="SLAAR21-LMNP-ESBIR13:MOTRBV",
            name="las_out_rx",
            accuracy=10,
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LMNP-ESBIR14:DRIVE",
            pvreadbackname="SLAAR21-LMNP-ESBIR14:MOTRBV",
            name="las_out_ry",
            accuracy=10,
            is_setting=True,
        )

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


class TtProcessor:
    def __init__(self, Nbg=10):
        self.bg = deque([], Nbg)
        self.sig = deque([], 1)
        self.accumulator = Thread(target=self.run_continuously)
        self.accumulator.start()

    def run_continuously(self):
        with source(
            channels=[
                "SARES20-CAMS142-M5.roi_signal_x_profile",
                "SAR-CVME-TIFALL5:EvtSet",
            ]
        ) as s:
            while True:
                m = s.receive()
                ix = m.data.pulse_id

                prof = m.data.data["SARES20-CAMS142-M5.roi_signal_x_profile"].value
                if prof is None:
                    continue

                codes = m.data.data["SAR-CVME-TIFALL5:EvtSet"].value
                if codes is None:
                    continue
                laseron = codes[25] == 0
                try:
                    if (lastgoodix - ix) > 1:
                        print(f"missed  {lastgoodix-ix-1} events!")
                except:
                    pass
                lastgoodix = ix
                if not laseron:
                    self.bg.append(prof)
                else:
                    self.sig.append(prof / np.asarray(self.bg).mean(axis=0))

    def setup_plot(self):
        self.lh_sig = self.axs[1].plot(self.sig[-1])[0]
        self.lh_bg = self.axs[0].plot(np.asarray(self.bg).mean(axis=0))[0]
        self.lh_bg_last = self.axs[0].plot(self.bg[-1])[0]

    def update_plot(self, dum):
        self.lh_sig.set_ydata(self.sig[-1])
        self.lh_bg.set_ydata(np.asarray(self.bg).mean(axis=0))
        self.lh_bg_last.set_ydata(self.bg[-1])
        return self.lh_sig

    def plot_animation(self, name="TT online ana", animate=True):
        plt.ion()
        if len(self.sig) < 1:
            print("no signals yet")
            return
        self.fig, self.axs = plt.subplots(2, 1, sharex=True, num=name)
        # self.fig.clf()
        # self.ax = self.fig.add_subplot(111)
        if animate:
            self.ani = FuncAnimation(
                self.fig, self.update_plot, init_func=self.setup_plot
            )
            plt.show()
