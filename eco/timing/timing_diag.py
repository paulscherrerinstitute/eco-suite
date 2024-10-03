from eco.detector.detectors_psi import DetectorBsStream
from eco.epics.detector import DetectorPvDataStream
from eco.devices_general.pipelines_swissfel import Pipeline
from eco.devices_general.spectrometers import SpectrometerAndor
from eco.microscopes.microscopes import FeturaPlusZoom
from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice, MotorRecord, SmaractRecord
from ..elements.adjustable import AdjustableMemory, AdjustableVirtual
from ..epics.adjustable import AdjustablePv
from ..devices_general.cameras_swissfel import CameraBasler, CameraPCO
from cam_server import PipelineClient
import colorama
import datetime
from pint import UnitRegistry
from time import sleep
from ..xdiagnostics.profile_monitors import Target_xyz
from eco.xdiagnostics.intensity_monitors import CalibrationRecord
from .timetool_online_helper import TtProcessor
import numpy as np
import pylab as plt
from epics import PV
from bsread import source
from pathlib import Path

# from time import sleep

ureg = UnitRegistry()


class TimetoolBerninaUSD(Assembly):
    def __init__(
        self,
        name=None,
        processing_pipeline="SARES20-CAMS142-M5_psen_db",
        edge_finding_pipeline="SAROP21-ATT01_proc",
        pv_writing_pipeline="Bernina_tt_kb_populate_pvs",
        processing_instance="SARES20-CAMS142-M5_psen_db",
        spectrometer_camera_channel="SARES20-CAMS142-M5:FPICTURE",
        spectrometer_pvname="SARES20-CAMS142-M5",
        microscope_pvname="SARES20-PROF141-M1",
        delaystage_PV="SLAAR21-LMOT-M524:MOTOR_1",
        pvname_mirror="SARES23-LIC:MOT_9",
        pvname_zoom="SARES20-MF1:MOT_8",
        mirror_in=15,
        mirror_out=-5,
        andor_spectrometer=None,
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
        try:
            self.proc_pipeline = processing_pipeline
            self._append(
                Pipeline,
                self.proc_pipeline,
                name="pipeline_projection",
                is_setting=True,
            )
            self.proc_instance = processing_instance
        except Exception as e:
            print(f"Timetool projection pipeline initialization failed with: \n{e}")
        try:
            self.proc_pipeline_edge = edge_finding_pipeline
            self._append(
                Pipeline,
                self.proc_pipeline_edge,
                name="pipeline_edgefinding",
                is_setting=True,
            )
        except Exception as e:
            print(f"Timetool edge finding pipeline initialization failed with: \n{e}")
        try:
            self.proc_pipeline_pv_writing = pv_writing_pipeline
            self._append(
                Pipeline,
                self.proc_pipeline_pv_writing,
                name="pipeline_pv_writing",
                is_setting=True,
            )
        except Exception as e:
            print(f"Timetool pv writing pipeline initialization failed with: \n{e}")
        self.spectrometer_camera_channel = spectrometer_camera_channel
        self._append(
            Target_xyz,
            pvname_x="SARES20-MF2:MOT_1",
            pvname_y="SARES20-MF2:MOT_2",
            pvname_z="SARES20-MF2:MOT_3",
            name="target_stages",
            is_display="recursive",
        )
        self.target = self.target_stages.presets
        # self._append(MotorRecord, "SARES20-MF2:MOT_1", name="x_target", is_setting=True)
        # self._append(MotorRecord, "SARES20-MF2:MOT_2", name="y_target", is_setting=True)
        # self._append(MotorRecord, "SARES20-MF2:MOT_3", name="z_target", is_setting=True)
        self._append(
            MotorRecord, "SARES20-MF2:MOT_4", name="zoom_microscope", is_setting=True
        )
        self._append(
            SmaractRecord,
            pvname_mirror,
            name="x_mirror_microscope",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustableVirtual,
            [self.x_mirror_microscope],
            lambda v: abs(v - self.mirror_in_position) < 0.003,
            lambda v: self.mirror_in_position if v else self.mirror_out_position,
            name="mirror_in",
            is_setting=True,
            is_display=True,
        )
        self._append(
            CameraBasler,
            pvname=microscope_pvname,
            name="camera_microscope",
            camserver_alias="PROF_KB (SARES20-PROF141-M1)",
            is_setting=True,
            is_display=False,
        )
        self._append(
            MotorRecord, pvname_zoom, name="zoom", is_setting=True, is_display=True
        )
        self._append(
            CameraPCO,
            pvname=spectrometer_pvname,
            name="camera_spectrometer",
            camserver_alias=f"{name} ({spectrometer_pvname})",
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

        # SARES20-CAMS142-M5.bsen_signal_x_profile
        # SARES20-CAMS142-M5.processing_parameters
        # SARES20-CAMS142-M5.psen_signal_x_profile
        #
        #
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LFEEDBACK1:TARGET1",
            name="feedback_setpoint",
            accuracy=10,
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LFEEDBACK1:ENABLE",
            name="feedback_enabled",
            accuracy=10,
            is_setting=True,
        )
        self._append(
            DetectorBsStream,
            "SAROP21-ATT01:edge_pos",
            cachannel="SLAAR21-SPECTT:PX",
            name="edge_position_px",
            is_setting=False,
            is_display=True,
        )
        self._append(
            DetectorBsStream,
            "SAROP21-ATT01:xcorr_ampl",
            cachannel="SLAAR21-SPECTT:MX",
            name="edge_amplitude",
            is_setting=False,
            is_display=True,
        )
        self._append(
            DetectorBsStream,
            "SAROP21-ATT01:arrival_time",
            cachannel="SLAAR21-SPECTT:AT",
            name="edge_position_fs",
            is_setting=False,
            is_display=True,
        )
        self._append(
            CalibrationRecord,
            pvbase="SLAAR21-LTIM01-EVR0:CALCI",
            name="calibration_CA",
            is_setting=True,
            is_display=False,
        )
        self._append(
            DetectorBsStream,
            "SARES20-CAMS142-M5.roi_signal_x_profile",
            cachannel=None,
            name="spectrum_signal",
            is_setting=False,
            is_display=True,
        )
        self._append(
            DetectorBsStream,
            "SARES20-CAMS142-M5.roi_background_x_prof",
            cachannel=None,
            name="spectrum_background",
            is_setting=False,
            is_display=True,
        )
        self._append(
            DetectorBsStream,
            "SARES20-CAMS142-M5.bsen_signal_x_profile",
            cachannel=None,
            name="spectrum_bsen",
            is_setting=False,
            is_display=True,
        )

        if andor_spectrometer:
            try:
                self._append(
                    SpectrometerAndor,
                    andor_spectrometer,
                    name="spectrometer",
                    is_setting=True,
                    is_display="recursive",
                )
            except Exception as e:
                print(f"Andor spectrometer initialization failed with: \n{e}")

    def get_calibration_values(
        self, seconds=5, scan_range=.8e-12, plot=False, pipeline=True, to_elog=False
    ):
        t0 = self.delay()
        x = np.linspace(t0 - scan_range / 2, t0 + scan_range / 2, 20)
        y = []
        ymean = []
        yerr = []
        try:
            for pos in x:
                print(f"Moving to {pos*1e15} fs")
                self.delay.set_target_value(pos).wait()
                if pipeline:
                    #needed due to delay of data arrival
                    sleep(2)
                ys = self.edge_position_px.acquire(seconds=seconds).wait()
                y.append(ys)
                ymean.append(np.mean(ys))
                yerr.append(np.std(ys)/np.sqrt(len(ys)))
        except Exception as e:
            print(e)
            print(f"Moving back to inital value of {t0}")
            self.delay.set_target_value(t0)

        p = np.polyfit(ymean, x, 2, w=1/np.array(yerr))
        fpath = ""
        if plot:
            binmin = np.min([np.min(step) for step in y])
            binmax = np.max([np.max(step) for step in y])
            bins = np.arange(binmin, binmax, 1)
            bins_center = bins[:-1]+0.5
            hists = np.array([np.histogram(step, bins=bins)[0]for step in y]).T
            plt.close("tt_calib")
            fig = plt.figure("tt_calib")
            plt.pcolor(x, bins_center, hists)
            line = plt.errorbar(x, ymean, yerr, color="red", marker=".", linestyle="")
            fit = plt.plot(np.polyval(p, ymean), ymean, label=p, color="yellow")
            plt.xlabel("tt_kb.delay (s)")
            plt.ylabel("edge position (px)")
            plt.legend()
            plt.show()
            if to_elog:
                fpath = "/photonics/home/gac-bernina/tt_calib.jpg"
                fig.savefig(fpath, dpi=200)
                fpath = Path(fpath)
        if to_elog:
            try:
                msg = f"<h1>Timetool calibration results:</h1>\n"
                msg+= "Polynomial fit c0*edge_pos(px)^2 + c1*edge_pos(px) + c2:\n {p} \n\n"
                msg+= self.target_stages.__repr__()
                elog = self._get_elog()
                elog.post(msg.replace("\n", "<br>"), fpath)
            except Exception as e:
                print(f"Elog posting failed with:\n {e}")
        print(f"Fit results c0*px^2 + c1*px + c2:\n{p}")
        print(f"Moving back to inital value of {t0}")
        self.delay.set_target_value(t0)
        return p, y

    def set_calibration_values(self, p, pipeline=True, to_elog=True):
        if pipeline:
            old_calib = self.pipeline_edgefinding.config.calibration()
            self.pipeline_edgefinding.config.calibration.set_target_value(p).wait()
            msg = f"Updated timetool processing pipeline calibration:\n"
            msg+= "old values: {old_calib} \nnew values: {p}"
        else:
            self.calibration.const_E.set_target_value(p[0]).wait()
            self.calibration.const_F.set_target_value(p[1]).wait()
            self.calibration.const_G.set_target_value(p[2]).wait()
            msg = f"Updated timetool processing epics calibration:\nnew values: {p}"
        if to_elog:
            try:
                elog = self._get_elog()
                elog.post(msg)
            except Exception as e:
                print(f"Elog posting failed with:\n {e}")

    def calibrate(self, seconds=5, scan_range=1e-12, plot=True, pipeline=True, to_elog=True):
        t0 = self.delay()
        if abs(t0) > 50e-15:
            ans = ""
            while not any([a in ans for a in ["y", "n"]]):
                try:
                    ans = input(
                        f"Timetool delay stage is at {t0*1e15} fs. Continue the calibration (y/n)?"
                    )
                except:
                    continue
            if ans == "n":
                return
        p, ys = self.get_calibration_values(
            seconds=seconds, scan_range=scan_range, plot=plot, to_elog=to_elog, pipeline=pipeline
        )
        self.set_calibration_values(p, pipeline=pipeline, to_elog=to_elog)

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

    def bs_read_to_pv(self):
        fs_pv = PV(self.edge_position_fs.pvname)
        px_pv = PV(self.edge_position_px.pvname)
        mx_pv = PV(self.edge_amplitude.pvname)
        with source(channels=[self.edge_position_fs.bs_channel, self.edge_position_px.bs_channel, self.edge_amplitude.bs_channel]) as s:
            while True:
                d = s.receive()
                fs, px, mx = [[d.data.data[self.edge_position_fs.bs_channel].value], d.data.data[self.edge_position_px.bs_channel].value, d.data.data[self.edge_amplitude.bs_channel].value]
                if not fs:
                    continue
                fs_pv.put(fs)
                px_pv.put(px)
                mx_pv.put(mx)

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


class TimetoolSpatial(Assembly):
    def __init__(
        self,
        name=None,
        processing_pipeline="SARES20-CAMS142-M4_psen_db",
        # edge_finding_pipeline="SAROP21-ATT01_proc",
        processing_instance="SARES20-CAMS142-M4_psen_db",
        microscope_pvname="SARES20-CAMS142-M4",
        delaystage_PV="SARES23-USR:MOT_2",
        pvname_target_stage="SARES20-MF1:MOT_8",
    ):
        super().__init__(name=name)

        self._append(
            MotorRecord, pvname_target_stage, name="transl_target", is_setting=True
        )

        self._append(SmaractRecord, delaystage_PV, name="delaystage", is_setting=True)
        self._append(DelayTime, self.delaystage, name="delay", is_setting=True)

        self.proc_client = PipelineClient()
        self.proc_pipeline = processing_pipeline
        self._append(
            Pipeline, self.proc_pipeline, name="pipeline_projection", is_setting=True
        )
        self.proc_instance = processing_instance
        # self.proc_pipeline_edge = edge_finding_pipeline
        # self._append(Pipeline,self.proc_pipeline_edge, name='pipeline_edgefinding', is_setting=True)

        # self._append(
        #     MotorRecord, pvname_zoom, name="zoom", is_setting=True, is_display=True
        # )

        self._append(
            CameraPCO,
            pvname=microscope_pvname,
            name="camera_microscope",
            camserver_alias=f"{name} ({microscope_pvname})",
            is_setting=True,
            is_display=False,
        )

        self._append(FeturaPlusZoom, name="zoom")

        # self._append(
        #     AdjustablePv,
        #     pvsetname="SLAAR21-LFEEDBACK1:TARGET1",
        #     name="feedback_setpoint",
        #     accuracy=10,
        #     is_setting=True,
        # )
        # self._append(
        #     AdjustablePv,
        #     pvsetname="SLAAR21-LFEEDBACK1:ENABLE",
        #     name="feedback_enabled",
        #     accuracy=10,
        #     is_setting=True,
        # )

        self._append(
            DetectorBsStream,
            "SARES20-CAMS142-M4.roi_signal_x_profile",
            cachannel=None,
            name="proj_signal",
            is_setting=False,
            is_display=True,
        )
        self._append(
            DetectorBsStream,
            "SARES20-CAMS142-M4.roi_background_x_prof",
            cachannel=None,
            name="proj_background",
            is_setting=False,
            is_display=True,
        )
        self._append(
            DetectorBsStream,
            "SARES20-CAMS142-M5.bsen_signal_x_profile",
            cachannel=None,
            name="spectrum_bsen",
            is_setting=False,
            is_display=True,
        )
        # self._append(
        #     DetectorBsStream,
        #     "SAROP21-ATT01:arrival_time",
        #     cachannel=None,
        #     name="edge_position",
        #     is_setting=False,
        #     is_display=True,
        # )

    def get_online_data(self):
        self.online_monitor = TtProcessor(
            channel_proj="SARES20-CAMS142-M4.roi_signal_x_profile"
        )

    def start_online_monitor(self):
        print(f"Starting online data acquisition ...")
        self.get_online_data()
        print(f"... done, waiting for data coming in ...")
        sleep(5)
        print(f"... done, starting online plot.")
        self.online_monitor.plot_animation()

    # def get_proc_config(self):
    #     return self.proc_client.get_pipeline_config(self.proc_pipeline)

    # def update_proc_config(self, cfg_dict):
    #     cfg = self.get_proc_config()
    #     cfg.update(cfg_dict)
    #     self.proc_client.set_instance_config(self.proc_instance, cfg)

    # def acquire_and_plot_spectrometer_image(self, N_pulses=50):
    #     with source(channels=[self.spectrometer_camera_channel]) as s:
    #         im = []
    #         while True:
    #             m = s.receive()
    #             tim = m.data.data[self.spectrometer_camera_channel]
    #             if not tim:
    #                 continue
    #             if len(im) > N_pulses:
    #                 break
    #             im.append(tim.value)
    #     im = np.asarray(im).mean(axis=0)
    #     fig = plt.figure("bsen spectrometer pattern")
    #     fig.clf()
    #     ax = fig.add_subplot(111)
    #     ax.imshow(im)
