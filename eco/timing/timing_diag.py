from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice, MotorRecord
from ..elements.adjustable import AdjustableMemory, AdjustableVirtual
from ..epics.adjustable import AdjustablePv
from ..devices_general.cameras_swissfel import CameraBasler, CameraPCO
from cam_server import PipelineClient
import colorama
import datetime
from pint import UnitRegistry
from time import sleep
from ..xdiagnostics.profile_monitors import Target_xyz

from .timetool_online_helper import TtProcessor

# from time import sleep

ureg = UnitRegistry()


class TimetoolBerninaUSD(Assembly):
    def __init__(
        self,
        name=None,
        processing_pipeline="SARES20-CAMS142-M5_psen_db",
        processing_instance="SARES20-CAMS142-M5_psen_db1",
        spectrometer_camera_channel="SARES20-CAMS142-M5:FPICTURE",
        spectrometer_pvname="SARES20-CAMS142-M5",
        microscope_pvname="SARES20-PROF141-M1",
        delaystage_PV="SLAAR21-LMOT-M524:MOTOR_1",
        pvname_mirror="SARES23-LIC9",
        pvname_zoom="SARES20-MF1:MOT_8",
        mirror_in=15,
        mirror_out=-5,
    ):
        super().__init__(name=name)
        self.mirror_in_position = mirror_in
        self.mirror_out_position = mirror_out
        # Table 1, Benrina hutch
        self._append(
            MotorRecord, delaystage_PV, name="delaystage_tt_usd", is_setting=True
        )
        self._append(DelayTime, self.delaystage_tt_usd, name="delay", is_setting=True)

        self.proc_client = PipelineClient()
        self.proc_pipeline = processing_pipeline
        self.proc_instance = processing_instance
        self.spectrometer_camera_channel = spectrometer_camera_channel
        self._append(
            Target_xyz,
            pvname_x="SARES20-MF2:MOT_1",
            pvname_y="SARES20-MF2:MOT_2",
            pvname_z="SARES20-MF2:MOT_3",
            name="target_stages",
            is_status="recursive",
        )
        self.target = self.target_stages.presets
        # self._append(MotorRecord, "SARES20-MF2:MOT_1", name="x_target", is_setting=True)
        # self._append(MotorRecord, "SARES20-MF2:MOT_2", name="y_target", is_setting=True)
        # self._append(MotorRecord, "SARES20-MF2:MOT_3", name="z_target", is_setting=True)
        self._append(
            MotorRecord, "SARES20-MF2:MOT_4", name="zoom_microscope", is_setting=True
        )
        self._append(
            SmaractStreamdevice,
            pvname_mirror,
            name="x_mirror_microscope",
            is_setting=True,
            is_status=False,
        )
        self._append(
            AdjustableVirtual,
            [self.x_mirror_microscope],
            lambda v: abs(v - self.mirror_in_position) < 0.003,
            lambda v: self.mirror_in_position if v else self.mirror_out_position,
            name="mirror_in",
            is_setting=True,
            is_status=True,
        )
        self._append(
            CameraBasler,
            pvname=microscope_pvname,
            name="camera_microscope",
            camserver_alias = f"{name} ({microscope_pvname})",
            is_setting=True,
            is_status=False,
        )
        self._append(
            MotorRecord, pvname_zoom, name="zoom", is_setting=True, is_status=True
        )
        self._append(
            CameraPCO,
            pvname=spectrometer_pvname,
            name="camera_spectrometer",
            camserver_alias = f"{name} ({spectrometer_pvname})",
            is_setting=True,
            is_status=False,
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

    def get_online_data(self):
        self.online_monitor = TtProcessor()

    def start_online_monitor(self):
        print(f"Starting online data acquisition ...")
        self.get_online_data()
        print(f"... done, waiting for data coming in ...")
        sleep(5)
        print(f"... done, starting online plot.")
        self.online_monitor.plot_animation()

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


class DelayTime(AdjustableVirtual):
    def __init__(
        self, stage, direction=1, passes=2, reset_current_value_to=True, name=None
    ):
        self._direction = direction
        self._group_velo = 299798458  # m/s
        self._passes = passes
        # self.Id = stage.Id + "_delay"
        self._stage = stage
        AdjustableVirtual.__init__(
            self,
            [stage],
            self._mm_to_s,
            self._s_to_mm,
            reset_current_value_to=reset_current_value_to,
            name=name,
            unit="s",
        )

    def _mm_to_s(self, mm):
        return mm * 1e-3 * self._passes / self._group_velo * self._direction

    def _s_to_mm(self, s):
        return s * self._group_velo * 1e3 / self._passes * self._direction

    def __repr__(self):
        s = ""
        s += f"{colorama.Style.DIM}"
        s += datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S") + ": "
        s += f"{colorama.Style.RESET_ALL}"
        s += f"{colorama.Style.BRIGHT}{self._get_name()}{colorama.Style.RESET_ALL} at "
        s += f"{self.get_current_value():g} s"
        s += f" ({(self.get_current_value()*ureg.second).to_compact():P~6.3f})"
        s += f"{colorama.Style.RESET_ALL}"
        return s

    def get_limits(self):
        return [self._mm_to_s(tl) for tl in self._stage.get_limits()]

    def set_limits(self, low_limit, high_limit):
        lims_stage = [self._s_to_mm(tl) for tl in [low_limit, high_limit]]
        lims_stage.sort()
        self._stage.set_limits(*lims_stage)

        return [self._mm_to_s(tl) for tl in self._stage.get_limits()]


class DelayCompensation(AdjustableVirtual):
    """Simple virtual adjustable for compensating delay adjustables. It assumes the first adjustable is the master for
    getting the current value."""

    def __init__(self, adjustables, directions, set_current_value=True, name=None):
        self._directions = directions
        self.Id = name
        AdjustableVirtual.__init__(
            self,
            adjustables,
            self._from_values,
            self._calc_values,
            set_current_value=set_current_value,
            name=name,
        )

    def _calc_values(self, value):
        return tuple(tdir * value for tdir in self._directions)

    def _from_values(self, *args):
        positions = [ta * tdir for ta, tdir in zip(args, self._directions)]
        return positions[0]

        tuple(tdir * value for tdir in self._directions)

    def __repr__(self):
        s = ""
        s += f"{colorama.Style.DIM}"
        s += datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S") + ": "
        s += f"{colorama.Style.RESET_ALL}"
        s += f"{colorama.Style.BRIGHT}{self._get_name()}{colorama.Style.RESET_ALL} at "
        s += f"{(self.get_current_value()*ureg.second).to_compact():P~6.3f}"
        s += f"{colorama.Style.RESET_ALL}"
        return s
