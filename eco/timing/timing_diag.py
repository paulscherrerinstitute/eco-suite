from eco.detector.detectors_psi import DetectorBsStream
from eco.devices_general.pipelines_swissfel import Pipeline
from eco.devices_general.spectrometers import SpectrometerAndor
from eco.microscopes.microscopes import FeturaPlusZoom
from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice, MotorRecord, SmaractRecord
from ..elements.adjustable import AdjustableMemory, AdjustableVirtual
from ..epics_utils.adjustable import AdjustablePv
from ..devices_general.cameras_swissfel import CameraBasler, CameraPCO
from cam_server import PipelineClient
import colorama
import datetime
from pint import UnitRegistry
import time
from time import sleep
from ..xdiagnostics.profile_monitors import Target_xyz
from eco.xdiagnostics.intensity_monitors import CalibrationRecord
from .timetool_online_helper import TtProcessor
import numpy as np
import pylab as plt
from epics import PV
from bsread import source
from pathlib import Path
import datahub as dh
from pandas import DataFrame
from scipy.optimize import curve_fit
import pickle

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
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LFEEDBACK1:ENABLE",
            name="feedback_enabled",
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

    def filter_outliers(self, data, sig=1.5):
        data = np.array(data)
        score = (data - np.nanmedian(data)) / np.nanstd(data)
        data_f = data[(-sig < score) & (score < sig)]
        return data_f

    def dataframe_to_escape_dataset(
        self, x, df, pids_start, pids_stop, filepath="", calibration_fit=None
    ):
        from escape.storage import DataSet, Array
        import json

        alias_mapping = {
            "SAROP21-ATT01:edge_pos": "tt_kb.edge_pos",
            "SAR-CVME-TIFALL5:EvtSet": "eventset",
            "SARES20-CAMS142-M5.roi_signal_x_profile": "tt_kb.profile",
        }

        dfs = [df.query(f"{a}<index<{b}") for a, b in zip(pids_start, pids_stop)]

        ds = DataSet.create_with_new_result_file(
            results_filepath=filepath, force_overwrite=True
        )
        # try:
        for key in df.keys():
            if key in alias_mapping.keys():
                name = alias_mapping[key]
            else:
                name = key
            pulseids = np.concat(
                [np.array(df[key].dropna().index.to_list()) for df in dfs]
            )
            data = np.concat([np.array(df[key].dropna().to_list()) for df in dfs])
            step_lengths = [len(df[key].dropna().index.to_list()) for df in dfs]

            ds.append(
                Array(
                    data=data,
                    index=pulseids,
                    step_lengths=step_lengths,
                    parameter={"tt_kb.delay": {"values": x}},
                ),
                name=name,
            )

        status = json._default_decoder.decode(
            json._default_encoder.encode(self.get_status(raise_on_incomplete=False))
        )
        ds.append(status, name="tt_kb_status")

        # small (one-value-per-step / few-values-total) calibration summary,
        # kept alongside the per-shot data instead of only in the small pickle
        if calibration_fit is not None:
            ds.append(
                {k: np.asarray(v) for k, v in calibration_fit.items()},
                name="tt_kb_calibration_fit",
            )

        ds.store_datasets_max_element_size(100000)
        ds.results_file.close()

    def scan_calibration(
        self,
        seconds=5,
        scan_range=0.8e-12,
        scan_steps=20,
        scan_array=None,
        reverse_direction=False,
    ):
        t0 = self.delay()
        if scan_array is None:
            x = np.linspace(t0 - scan_range / 2, t0 + scan_range / 2, scan_steps)
        else:
            x = scan_array
        if reverse_direction:
            x = x[::-1]
        stop_time = None
        try:
            pids_start = []
            pids_stop = []
            pid = PV("SARES20-CVME-01-EVR0:RX-PULSEID")
            for pos in x:
                print(f"Moving to {pos*1e15} fs")
                self.delay.set_target_value(pos).wait()
                pids_start.append(pid.value)
                sleep(seconds)
                pids_stop.append(pid.value)
            # wall-clock time by which the last needed pulse was produced --
            # lets retrieve_calibration_data wait for sf-databuffer's
            # ingestion to catch up to this instant instead of blindly
            # polling for the data itself
            stop_time = time.time()

        except Exception as e:
            print(e)
            print(f"Moving back to inital value of {t0}")
            self.delay.set_target_value(t0)

        print(f"Moving back to inital value of {t0}")
        self.delay.set_target_value(t0)
        return x, pids_start, pids_stop, stop_time

    def retrieve_calibration_data(
        self,
        pids_start,
        pids_stop,
        additional_channels=["SARES20-CAMS142-M5.bsen_signal_x_profile"],
        stop_time=None,
        max_retries=6,
    ):
        # Wait for sf-databuffer's ingestion to catch up to the scan's end
        # time using a cheap probe channel, instead of blindly polling the
        # (often much larger) calibration channels themselves up to 60 times.
        # This does most of the waiting; the retry loop below is now just a
        # safety net for per-channel ingestion variance around that estimate.
        if stop_time is not None:
            from eco.dbase.archiver import wait_for_databuffer

            try:
                lag = wait_for_databuffer(stop_time, timeout=60)
                print(
                    f"sf-databuffer ingestion caught up (measured lag {lag:.1f}s)"
                )
            except Exception as e:
                print(
                    f"Could not confirm sf-databuffer ingestion ({e}), "
                    "falling back to polling the calibration data directly"
                )

        retrieving = True
        i = 1
        source = dh.DataBuffer()
        table = dh.Table()
        source.add_listener(table)
        while retrieving:
            if i == max_retries:
                raise TimeoutError(f"Retrieval failed after {max_retries} attempts")
            print(f"Waiting for data to arrive in the Data Buffer: try {i}")
            i = i + 1
            sleep(1)
            source.req(
                ["SAROP21-ATT01:edge_pos", "SAR-CVME-TIFALL5:EvtSet"]
                + additional_channels,
                int(pids_start[0]),
                int(pids_stop[-1]),
            )
            df = table.as_dataframe(index="pulse_id")
            y = [
                np.array(
                    df.query(f"{a}<index<{b}")["SAROP21-ATT01:edge_pos"]
                    .dropna()
                    .to_list()
                )
                for a, b in zip(pids_start, pids_stop)
            ]
            lens = [sum(~np.isnan(a)) for a in y]
            print(f"Shots per Step: {lens}")
            retrieving = np.any([l < 0.5 * np.max(lens) for l in lens])
        return y, df

    def fit_calibration_data(self, x, y, filter_outliers=True):
        if filter_outliers:
            yf = [self.filter_outliers(a) for a in y]
            y = yf
        x = np.asarray(x)
        ymed = np.array([np.nanmedian(a) for a in y])
        yerr = np.array([np.nanstd(a) for a in y])

        # mask out steps that ended up with no usable data (nan median/std,
        # e.g. an empty step after outlier filtering) or a zero error, which
        # would otherwise blow up the 1/yerr weight or feed a nan straight
        # into polyfit and crash the whole calibration
        mask = np.isfinite(x) & np.isfinite(ymed) & np.isfinite(yerr) & (yerr > 0)
        n_bad = int((~mask).sum())
        if n_bad:
            print(
                f"Excluding {n_bad} of {len(mask)} calibration point(s) "
                "with nan/inf/zero-error data from the fit"
            )
        if mask.sum() < 3:
            raise RuntimeError(
                f"Not enough valid calibration points to fit a 2nd order "
                f"polynomial (only {int(mask.sum())} of {len(mask)} usable)"
            )

        p = np.polyfit(ymed[mask], x[mask], 2, w=1 / yerr[mask])
        print(f"Fit results c0*px^2 + c1*px + c2:\n{p}")
        return p, x, y, ymed, yerr

    def gauss(self, x, fwhm, x0, a):
        return a * np.exp(-0.5 * (x - x0) ** 2 / (fwhm / 2.3482) ** 2)

    def plot_calibration(
        self,
        p,
        x,
        y,
        ymed,
        yerr,
        p_last_calib=None,
        filepath_last_calib="",
        to_elog=True,
        path_figure="",
        filepath_data="",
        bidirectional=True,
    ):
        xu = np.unique(x)
        yu = [
            np.hstack([y_step for x_step, y_step in zip(x, y) if x_step == xu_step])
            for xu_step in xu
        ]
        binmin = np.min([np.min(step) for step in yu])
        binmax = np.max([np.max(step) for step in yu])
        bins = np.arange(binmin, binmax, 1)
        bins_center = bins[:-1] + 0.5
        hists = np.array([np.histogram(step, bins=bins)[0] for step in yu]).T
        plt.close("tt_calib")
        fig = plt.figure("tt_calib", figsize=(13, 6))
        gs = plt.GridSpec(1, 6, figure=fig)
        ax0 = fig.add_subplot(gs[0, 0])
        ax1 = fig.add_subplot(gs[0, 1:4], sharey=ax0)
        ax2 = fig.add_subplot(gs[0, 4:])
        ax1.pcolor(1e15 * xu, bins_center, hists)
        line = ax1.errorbar(
            1e15 * np.asarray(x), ymed, yerr, color="red", marker=".", linestyle=""
        )
        fit = ax1.plot(
            1e15 * np.polyval(p, ymed), ymed, label="poly fit", color="yellow"
        )
        if p_last_calib is not None:
            fitl = ax1.plot(
                1e15 * np.polyval(p_last_calib, ymed),
                ymed,
                label=f"poly fit last calibration\n{filepath_last_calib.stem}",
                color="orange",
            )
        ax0.axvline(0, linestyle="--", color="k")
        residual = 1e15 * (np.polyval(p, ymed) - x)
        n_steps = len(x)
        # bidirectional scans run the forward sweep then immediately retrace
        # it backward (see calibrate()'s x_f/x_f[::-1] hstack) -- split the
        # residual at the midpoint so a systematic forth/back offset (e.g.
        # from stage backlash) is visible as a color difference rather than
        # hidden inside one averaged-looking scribble.
        if bidirectional and n_steps > 1:
            half = n_steps // 2
            ax0.plot(
                residual[:half], ymed[:half], color="royalblue", label="→ forth"
            )
            ax0.plot(
                residual[half:], ymed[half:], color="darkorange", label="← back"
            )
            ax0.legend(loc="best", fontsize="small")
        else:
            ax0.plot(residual, ymed, color="royalblue")
        ax1.set_xlabel("tt_kb.delay (fs)")
        ax1.set_ylabel("edge position (px)")
        at = (
            np.hstack(
                [np.polyval(p, ys) - np.polyval(p, ymeds) for ys, ymeds in zip(y, ymed)]
            )
            * 1e15
        )
        d = ax2.hist(
            at, bins="auto", label=f"std {np.std(at):.3} fs", color="royalblue"
        )
        ax2.hist(at, bins="auto", edgecolor="black", histtype="step")
        try:
            fx = d[1][:-1] + d[1][1] - d[1][0]
            parsopt, parss = curve_fit(
                self.gauss,
                fx,
                d[0],
                [30, 0, 50],
                bounds=[[5, -1000, 5], [500, 1000, 50000]],
            )
            plx = np.arange(np.min(d[1]), np.max(d[1]), 0.1)
            ax2.plot(plx, self.gauss(plx, *parsopt), color="k")
        except Exception as e:
            print("Fitting of arrival time histogram failed with:")
            print(e)
            parsopt = [0.0, 0.0, 0.0]
            pass
        ax0.set_title("Residual")
        ax1.set_title("Scan")
        ax2.set_title(f"Jitter {parsopt[0]:.3} fs fwhm")
        ax1.legend(loc="upper right")
        ax2.set_xlabel("arrival time (fs)")
        ax0.set_xlabel("$\Delta$t (fs)")
        ax0.set_ylabel("edge position (px)")
        ax2.set_yticks([])
        fig.tight_layout()
        # plt.show() alone only *schedules* the draw -- with ion() active
        # (set at eco startup) it returns immediately without pumping the GUI
        # event loop, so nothing actually paints until control returns to the
        # IPython prompt. That's invisible for a single interactive call, but
        # in a loop (or anywhere else calibrate() is called back-to-back) the
        # window stays blank the whole time. plt.pause() forces an immediate
        # draw + event-loop flush, which is the general-purpose fix for
        # "update this plot now" from inside a loop/script, regardless of
        # backend.
        fig.canvas.draw()
        plt.pause(0.001)

        if to_elog:
            fpath = path_figure + ".jpg"
            fig.savefig(fpath, dpi=200)
            fpath = Path(fpath)
            dpath = Path(filepath_data)
        if to_elog:
            try:
                msg = f"<h3>Timetool calibration results:</h3>\n"
                msg += f"Polynomial fit c0*edge_pos(px)^2 + c1*edge_pos(px) + c2:\n {p} \n\n"
                elog = self._get_elog()
                elog.post(msg.replace("\n", "<br>"), fpath, dpath)
            except Exception as e:
                print(f"Elog posting failed with:\n {e}")
        return fig

    def set_calibration_values(self, p, pipeline=True, to_elog=True):
        if pipeline:
            old_calib = self.pipeline_edgefinding.config.calibration()
            # self.pipeline_edgefinding.config.calibration.set_target_value(p).wait() #This does not work because some issues with caching!
            self.update_proc_config({"calibration": list(p)}, self.pipeline_edgefinding)
            msg = f"Updated timetool processing pipeline calibration:\n"
            msg += f"old values: {old_calib} \nnew values: {p}"
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

    def load_last_calib(self, datapath):
        datapath = Path(datapath)
        files = sorted(
            list(datapath.glob("*_calib.pkl")) + list(datapath.glob("*_calib.esc.h5"))
        )
        if not files:
            raise FileNotFoundError(f"No previous calibration file found in {datapath}")
        filename = files[-1]
        if filename.name.endswith(".esc.h5"):
            from escape.storage import DataSet

            ds = DataSet.load_from_result_file(filename)
            p = np.asarray(ds.datasets["tt_kb_calibration_fit"]["p"])
            ds.results_file.close()
        else:
            with open(filename, "rb") as file:
                lc = pickle.load(file)
            p = np.asarray(lc.attrs["p"])
        return p, filename

    def save_calibration(self, p, x, y, ymed, yerr, dpath_calib, format="df"):
        """Save a calibration fit result.

        format: "df" (default) pickles p, x, ymed, yerr (this is small data,
        a few values per scan step) as a pandas DataFrame. "esc" instead
        stores them, plus the raw (outlier-filtered) per-step edge positions
        y, as escape arrays in an escape DataSet (an .esc.h5 file).
        """
        dpath_calib = Path(dpath_calib)
        if format == "esc":
            self._save_calibration_esc(p, x, y, ymed, yerr, dpath_calib)
        elif format == "df":
            self._save_calibration_df(p, x, y, ymed, yerr, dpath_calib)
        else:
            raise ValueError(
                f"Unknown calibration save format {format!r}, expected 'esc' or 'df'"
            )

    def _save_calibration_df(self, p, x, y, ymed, yerr, dpath_calib):
        # p (3 fit coefficients) has a different length than x/y/ymed/yerr
        # (one entry per scan step), so it cannot be a column of the same
        # DataFrame -- stash it in .attrs instead, which pickling preserves.
        df = DataFrame(
            {
                "tt_kb.delay": np.asarray(x),
                "tt_kb.edge_position_px": [np.asarray(a) for a in y],
                "ymed": np.asarray(ymed),
                "yerr": np.asarray(yerr),
            }
        )
        df.attrs["p"] = np.asarray(p)
        df.to_pickle(dpath_calib)

    def _save_calibration_esc(self, p, x, y, ymed, yerr, dpath_calib):
        from escape.storage import DataSet, Array

        x = np.asarray(x)
        y = [np.atleast_1d(np.asarray(a)) for a in y]
        step_lengths = [len(a) for a in y]
        data = np.concatenate(y) if sum(step_lengths) else np.array([])

        ds = DataSet.create_with_new_result_file(
            results_filepath=dpath_calib, force_overwrite=True
        )
        # single-shot data is what actually benefits from being an escape
        # Array/Scan (real, possibly unequal step_lengths per delay position)
        ds.append(
            Array(
                data=data,
                index=np.arange(len(data)),
                step_lengths=step_lengths,
                parameter={"tt_kb.delay": {"values": x}},
            ),
            name="tt_kb.edge_position_px",
        )
        # the fit result and its one-value-per-step inputs are plain arrays,
        # no per-shot structure to represent -- just stash them as a dict
        ds.append(
            {
                "p": np.asarray(p),
                "tt_kb.delay": x,
                "ymed": np.asarray(ymed),
                "yerr": np.asarray(yerr),
            },
            name="tt_kb_calibration_fit",
        )
        ds.store_datasets_max_element_size(100000)
        ds.results_file.close()

    def calibrate(
        self,
        seconds=5,
        scan_range=1e-12,
        scan_steps=20,
        plot=True,
        to_elog=True,
        filter_outliers=True,
        reverse_direction=False,
        bidirectional=True,
        update_pipeline_config=False,
        ask_apply=True,
        save=True,
        calibration_format="df",
        output_dir=None,
        additional_channels=["SARES20-CAMS142-M5.roi_signal_x_profile"],
    ):
        """Run a timetool calibration scan.

        To just measure a calibration without touching the pipeline config
        and without any interactive question, pass
        ``update_pipeline_config=False, ask_apply=False`` (the calibration is
        still scanned, fit, saved and plotted as usual).

        ``update_pipeline_config=True`` applies the new calibration
        immediately without asking; ``ask_apply`` (default True) controls
        whether the "apply this calibration?" question is asked when
        ``update_pipeline_config`` is False -- set it to False to skip the
        question and simply not apply.

        ``output_dir`` overrides where results (raw data, calibration file,
        figure) are written; it defaults to the shared
        ``/sf/bernina/config/src/beamline_devices/tt_kb/`` tree.
        """
        from datetime import datetime
        from eco.bernina import config_bernina
        from eco.utilities.datafiles import ensure_dir

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        pgroup = config_bernina.pgroup()
        basepath = (
            str(output_dir)
            if output_dir is not None
            else "/sf/bernina/config/src/beamline_devices/tt_kb/"
        )
        if not basepath.endswith("/"):
            basepath += "/"
        path_data = f"{basepath}data/{timestamp}_{pgroup}"
        path_figure = f"{basepath}figures/{timestamp}_{pgroup}"
        calib_ext = "esc.h5" if calibration_format == "esc" else "pkl"
        path_calib = Path(f"{path_data}_calib.{calib_ext}")
        if save:
            ensure_dir(Path(path_data).parent)
        if plot:
            ensure_dir(Path(path_figure).parent)

        feedback = self.feedback_enabled()
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
        if feedback:
            self.feedback_enabled(0)
            print("Turned feedback off")

        # feedback is guaranteed to be restored below even if the scan,
        # retrieval, fit or saving raises partway through
        try:
            #######  bidirectional  ########
            if bidirectional:
                x_f = np.linspace(t0 - scan_range / 2, t0 + scan_range / 2, scan_steps)
                x = np.hstack([x_f, x_f[::-1]])
            else:
                x = None

            ##########    scan    ##########
            x, pids_start, pids_stop, stop_time = self.scan_calibration(
                seconds=seconds,
                scan_range=scan_range,
                scan_steps=scan_steps,
                scan_array=x,
                reverse_direction=reverse_direction,
            )

            ########## retrieve data ##########
            y, df = self.retrieve_calibration_data(
                pids_start=pids_start,
                pids_stop=pids_stop,
                additional_channels=additional_channels,
                stop_time=stop_time,
            )

            ##########  fit data ##########
            p, x, y, ymed, yerr = self.fit_calibration_data(
                x, y, filter_outliers=filter_outliers
            )

            ########## save data (raw per-shot data, plus the small fit summary) ##########
            if save:
                self.dataframe_to_escape_dataset(
                    x,
                    df,
                    pids_start,
                    pids_stop,
                    filepath=path_data + ".esc.h5",
                    calibration_fit={"p": p, "ymed": ymed, "yerr": yerr},
                )

            ####### Load previous calib (before this run's file becomes "latest") ######
            try:
                p_last_calib, filepath_last_calib = self.load_last_calib(
                    basepath + "data/"
                )
            except Exception as e:
                print("Failed to load last calibration")
                print(e)
                p_last_calib = None
                filepath_last_calib = ""

            ####### save calibration ######
            if save:
                self.save_calibration(
                    p, x, y, ymed, yerr, path_calib, format=calibration_format
                )

            edge_last_calib = self._edge_position_from_poly(p_last_calib)
            edge_new_calib = self._edge_position_from_poly(p)

            ########## plot data ##########
            if plot:
                fig = self.plot_calibration(
                    p,
                    x,
                    y,
                    ymed,
                    yerr,
                    p_last_calib=p_last_calib,
                    filepath_last_calib=filepath_last_calib,
                    to_elog=to_elog,
                    path_figure=path_figure,
                    filepath_data=path_calib,
                    bidirectional=bidirectional,
                )

            ########## print comparison ##########
            self._print_calibration_comparison(
                p_last_calib,
                edge_last_calib,
                filepath_last_calib,
                p,
                edge_new_calib,
                path_calib,
            )

            ####### User question: apply this calibration at all ######
            if update_pipeline_config:
                apply_calib = True
            elif not ask_apply:
                apply_calib = False
            else:
                ans = ""
                while not any([a in ans for a in ["y", "n"]]):
                    try:
                        ans = input(
                            "Apply this new calibration to the pipeline config (y/n)? "
                        )
                    except:
                        continue
                apply_calib = ans == "y"

            if apply_calib:
                # User question: keep pixel of previous calib
                if edge_last_calib is not None:
                    ans = ""
                    while not any([a in ans for a in ["y", "n"]]):
                        try:
                            ans = input(
                                f"Do you wish to shift the calibration to keep the edge at the same pixel ({edge_last_calib:.5}) as in the previous calibration (y/n)?"
                            )
                        except:
                            continue
                    if ans == "y":
                        p[-1] = -(p[0] * edge_last_calib**2 + p[1] * edge_last_calib)
                        print(f"Shifted calibration curve to preserve edge position: {p}")
                self.set_calibration_values(p, pipeline=True, to_elog=to_elog)
            else:
                print("Calibration not applied.")
        finally:
            if feedback:
                self.feedback_enabled(1)
                print("Turned feedback on")

    def _edge_position_from_poly(self, p):
        if p is None:
            return None
        for root in np.roots(p):
            if np.isreal(root) and 0 < root.real < 2000:
                return float(root.real)
        return None

    def _print_calibration_comparison(
        self,
        p_last_calib,
        edge_last_calib,
        filepath_last_calib,
        p_new,
        edge_new_calib,
        filepath_new_calib,
    ):
        def fmt_p(p):
            if p is None:
                return "n/a"
            return "  ".join(f"{v: .6e}" for v in p)

        def fmt_edge(edge):
            return f"{edge:.5g} px" if edge is not None else "n/a"

        width = 78
        print()
        print("=" * width)
        print("Timetool calibration comparison (c0*px^2 + c1*px + c2)")
        print("-" * width)
        print(f"previous : {filepath_last_calib or 'none found'}")
        print(f"  p          = {fmt_p(p_last_calib)}")
        print(f"  edge pos.  = {fmt_edge(edge_last_calib)}")
        print(f"new      : {filepath_new_calib}")
        print(f"  p          = {fmt_p(p_new)}")
        print(f"  edge pos.  = {fmt_edge(edge_new_calib)}")
        print("=" * width)
        print()

    ##############  OLD functions  #####################

    def get_calibration_values_old(
        self,
        seconds=5,
        scan_range=0.8e-12,
        plot=False,
        pipeline=True,
        use_bsread=False,
        to_elog=False,
        filter_outliers=False,
        reverse_direction=False,
    ):
        t0 = self.delay()
        x = np.linspace(t0 - scan_range / 2, t0 + scan_range / 2, 20)
        if reverse_direction:
            x = x[::-1]
        y = []
        ymed = []
        yerr = []

        try:
            if use_bsread:

                pids_start = []
                pids_stop = []
                pid = PV("SARES20-CVME-01-EVR0:RX-PULSEID")
                for pos in x:
                    print(f"Moving to {pos*1e15} fs")
                    self.delay.set_target_value(pos).wait()
                    pids_start.append(int(pid.value))
                    sleep(seconds)
                    pids_stop.append(int(pid.value))
                retrieving = True
                i = 1
                source = dh.Daqbuf()
                table = dh.Table()
                source.add_listener(table)
                while retrieving:
                    if i == 60:
                        raise TimeoutError("Retrieval failed after 60 attempts")
                    print(f"Waiting for data to arrive in the Data Buffer: try {i}")
                    i = i + 1
                    sleep(1)
                    source.req(["SAROP21-ATT01:edge_pos"], pids_start[0], pids_stop[-1])
                    df = table.as_dataframe(index="pulse_id")
                    y = [
                        df.query(f"{a}<index<{b}")["SAROP21-ATT01:edge_pos"].array
                        for a, b in zip(pids_start, pids_stop)
                    ]
                    lens = [len(a) for a in y]
                    print(f"Shots per Step: {lens}")
                    retrieving = np.any([l < 0.5 * len(df) / len(x) for l in lens])

                if filter_outliers:
                    yf = [self.filter_outliers(a) for a in y]
                    y = yf
                ymed = [np.nanmedian(a) for a in y]
                yerr = [np.nanstd(a) for a in y]
            else:
                for pos in x:
                    print(f"Moving to {pos*1e15} fs")
                    self.delay.set_target_value(pos).wait()
                    if pipeline:
                        # needed due to delay of data arrival
                        sleep(5)
                    ys = self.edge_position_px.acquire(seconds=seconds).wait()
                    if filter_outliers:
                        ys = self.filter_outliers(ys)
                    y.append(ys)
                    ymed.append(np.nanmedian(ys))
                    yerr.append(np.nanstd(ys) / np.sqrt(len(ys)))
        except Exception as e:
            print(e)
            print(f"Moving back to inital value of {t0}")
            self.delay.set_target_value(t0)

        p = np.polyfit(ymed, x, 2, w=1 / np.array(yerr))
        fpath = ""

        def gauss(x, fwhm, x0, a):
            return a * np.exp(-0.5 * (x - x0) ** 2 / (fwhm / 2.3482) ** 2)

        if plot:
            binmin = np.min([np.min(step) for step in y])
            binmax = np.max([np.max(step) for step in y])
            bins = np.arange(binmin, binmax, 1)
            bins_center = bins[:-1] + 0.5
            hists = np.array([np.histogram(step, bins=bins)[0] for step in y]).T
            plt.close("tt_calib")
            fig = plt.figure("tt_calib", figsize=(13, 6))
            gs = plt.GridSpec(1, 6, figure=fig)
            ax0 = fig.add_subplot(gs[0, 0])
            ax1 = fig.add_subplot(gs[0, 1:4], sharey=ax0)
            ax2 = fig.add_subplot(gs[0, 4:])
            ax1.pcolor(1e15 * x, bins_center, hists)
            line = ax1.errorbar(
                1e15 * x, ymed, yerr, color="red", marker=".", linestyle=""
            )
            fit = ax1.plot(
                1e15 * np.polyval(p, ymed), ymed, label="poly fit", color="yellow"
            )
            ax0.axvline(0, linestyle="--", color="k")
            ax0.plot(1e15 * (np.polyval(p, ymed) - x), ymed, color="royalblue")
            ax1.set_xlabel("tt_kb.delay (fs)")
            ax1.set_ylabel("edge position (px)")
            at = (
                np.hstack(
                    [
                        np.polyval(p, ys) - np.polyval(p, ymeds)
                        for ys, ymeds in zip(y, ymed)
                    ]
                )
                * 1e15
            )
            d = ax2.hist(
                at, bins="auto", label=f"std {np.std(at):.3} fs", color="royalblue"
            )
            ax2.hist(at, bins="auto", edgecolor="black", histtype="step")
            try:
                fx = d[1][:-1] + d[1][1] - d[1][0]
                parsopt, parss = curve_fit(gauss, fx, d[0], [30, 0, 50])
                plx = np.arange(np.min(d[1]), np.max(d[1]), 0.1)
                ax2.plot(plx, gauss(plx, *parsopt), color="k")
            except:
                print("Fitting of arrival time histogram failed")
                parsopt = [0, 0, 0]
                pass
            ax0.set_title("Residual")
            ax1.set_title("Scan")
            ax2.set_title(f"Jitter {parsopt[0]:.3} fs fwhm")
            ax1.legend()
            ax2.set_xlabel("arrival time (fs)")
            ax0.set_xlabel("$\Delta$t (fs)")
            ax0.set_ylabel("edge position (px)")
            ax2.set_yticks([])
            fig.tight_layout()
            plt.show()
            if to_elog:
                fpath = f"{Path.home()}/temp/tt_calib.jpg"
                fig.savefig(fpath, dpi=200)
                fpath = Path(fpath)
                dpath = Path(f"{Path.home()}/temp/tt_calib.pkl")
                df = DataFrame({"tt_kb.delay": x, "tt_kb.edge_position_px": y})
                df.to_pickle(dpath)
        if to_elog:
            try:
                msg = f"<h1>Timetool calibration results:</h1>\n"
                msg += f"Polynomial fit c0*edge_pos(px)^2 + c1*edge_pos(px) + c2:\n {p} \n\n"
                msg += self.target_stages.__repr__()
                elog = self._get_elog()
                elog.post(msg.replace("\n", "<br>"), fpath, dpath)
            except Exception as e:
                print(f"Elog posting failed with:\n {e}")
        print(f"Fit results c0*px^2 + c1*px + c2:\n{p}")
        print(f"Moving back to inital value of {t0}")
        self.delay.set_target_value(t0)
        return p, x, y

    def set_calibration_values_old(self, p, pipeline=True, to_elog=True):
        if pipeline:
            old_calib = self.pipeline_edgefinding.config.calibration()
            # self.pipeline_edgefinding.config.calibration.set_target_value(p).wait() #This does not work because some issues with caching!
            self.update_proc_config({"calibration": list(p)}, self.pipeline_edgefinding)
            msg = f"Updated timetool processing pipeline calibration:\n"
            msg += f"old values: {old_calib} \nnew values: {p}"
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

    def calibrate_old(
        self,
        seconds=5,
        scan_range=1e-12,
        plot=True,
        pipeline=True,
        to_elog=True,
        filter_outliers=True,
        use_bsread=True,
        reverse_direction=False,
    ):
        feedback = self.feedback_enabled()
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
        if feedback:
            self.feedback_enabled(0)
            print("Turned feedback off")
        p, x, y = self.get_calibration_values(
            seconds=seconds,
            scan_range=scan_range,
            plot=plot,
            to_elog=to_elog,
            pipeline=pipeline,
            use_bsread=use_bsread,
            filter_outliers=filter_outliers,
            reverse_direction=reverse_direction,
        )
        self.set_calibration_values(p, pipeline=pipeline, to_elog=to_elog)
        if feedback:
            self.feedback_enabled(1)
            print("Turned feedback on")

    def get_online_data(self):
        self.online_monitor = TtProcessor()

    def start_online_monitor(self):
        print(f"Starting online data acquisition ...")
        self.get_online_data()
        print(f"... done, waiting for data coming in ...")
        sleep(5)
        print(f"... done, starting online plot.")
        self.online_monitor.plot_animation()

    def start_camera_restarter(self):
        print(f"Starting camera restarter ...")
        from time import sleep

        while True:
            sleep(1)
            try:
                tx = float(self.pipeline_projection.info.statistics.tx().split("Hz")[0])
            except Exception as e:
                print(f"Could not read projection pipeline tx")
                print(e)
            if tx < 10:
                try:
                    self.camera_spectrometer.config_cs.stop()
                except Exception as e:
                    print(e)
                try:
                    self._get_elog().post(
                        f"### tx was {tx}: Automatically restarted timetool camera"
                    )
                    print(f"tx was {tx}Hz: Automatically restarted timetool camera")
                except Exception as e:
                    print(e)
                sleep(120)

    def update_proc_config(self, cfg_dict, pipeline):
        cfg = pipeline._get_config()
        cfg.update(cfg_dict)
        pipeline.pc.set_instance_config(pipeline.pipeline_name, cfg)

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
        with source(
            channels=[
                self.edge_position_fs.bs_channel,
                self.edge_position_px.bs_channel,
                self.edge_amplitude.bs_channel,
            ]
        ) as s:
            while True:
                d = s.receive()
                fs, px, mx = [
                    [d.data.data[self.edge_position_fs.bs_channel].value],
                    d.data.data[self.edge_position_px.bs_channel].value,
                    d.data.data[self.edge_amplitude.bs_channel].value,
                ]
                if not fs:
                    continue
                fs_pv.put(fs)
                px_pv.put(px)
                mx_pv.put(mx)


class TimetoolBerninaDSD(Assembly):
    def __init__(
        self,
        name=None,
        edge_finding_pipeline="SAROP21-ATT02_proc",
        microscope_pvname="SLAAR21-LCAM-CS841",
        delaystage_PV="SLAAR21-LMOT-M521:MOTOR_1",
    ):
        super().__init__(name=name)
        self._append(
            MotorRecord, delaystage_PV, name="delaystage_tt_dsd", is_setting=True
        )
        self._append(DelayTime, self.delaystage_tt_dsd, name="delay", is_setting=True)

        self.proc_client = PipelineClient()
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
        from eco.devices_general.cameras_swissfel import FeturaMicroscope

        self._append(
            FeturaMicroscope,
            microscope_pvname,
            pvname_base_zoom="SARES20-FETURA",
            name="camera",
        )

        self._append(
            DetectorBsStream,
            "SAROP21-ATT02:edge_pos",
            cachannel="",
            name="edge_position_px",
            is_setting=False,
            is_display=True,
        )
        self._append(
            DetectorBsStream,
            "SAROP21-ATT02:xcorr_ampl",
            cachannel="",
            name="edge_amplitude",
            is_setting=False,
            is_display=True,
        )
        self._append(
            DetectorBsStream,
            "SAROP21-ATT02:arrival_time",
            cachannel="",
            name="edge_position_fs",
            is_setting=False,
            is_display=True,
        )

    def filter_outliers(self, data, sig=1.5):
        data = np.array(data)
        score = (data - np.nanmedian(data)) / np.nanstd(data)
        data_f = data[(-sig < score) & (score < sig)]
        return data_f

    def get_calibration_data(
        self,
        seconds=5,
        scan_range=0.8e-12,
        scan_steps=20,
        reverse_direction=False,
        additional_channels=[],
    ):
        t0 = self.delay()
        x = np.linspace(t0 - scan_range / 2, t0 + scan_range / 2, scan_steps)
        if reverse_direction:
            x = x[::-1]
        try:
            pids_start = []
            pids_stop = []
            pid = PV("SARES20-CVME-01-EVR0:RX-PULSEID")
            for pos in x:
                print(f"Moving to {pos*1e15} fs")
                self.delay.set_target_value(pos).wait()
                pids_start.append(pid.value)
                sleep(seconds)
                pids_stop.append(pid.value)
            retrieving = True
            i = 1
            source = dh.Daqbuf()
            table = dh.Table()
            source.add_listener(table)
            while retrieving:
                if i == 60:
                    raise TimeoutError("Retrieval failed after 60 attempts")
                print(f"Waiting for data to arrive in the Data Buffer: try {i}")
                i = i + 1
                sleep(1)
                source.req(
                    ["SAROP21-ATT02:edge_pos"] + additional_channels,
                    pids_start[0],
                    pids_stop[-1],
                )
                df = table.as_dataframe(index="pulse_id")
                y = [
                    df.query(f"{a}<index<{b}")["SAROP21-ATT02:edge_pos"].array
                    for a, b in zip(pids_start, pids_stop)
                ]
                lens = [len(a) for a in y]
                print(f"Shots per Step: {lens}")
                retrieving = np.any([l < 0.5 * len(df) / len(x) for l in lens])

        except Exception as e:
            print(e)
            print(f"Moving back to inital value of {t0}")
            self.delay.set_target_value(t0)

        print(f"Moving back to inital value of {t0}")
        self.delay.set_target_value(t0)
        return x, y, df

    def fit_calibration_data(self, x, y, filter_outliers=True):
        if filter_outliers:
            yf = [self.filter_outliers(a) for a in y]
            y = yf
        ymed = [np.nanmedian(a) for a in y]
        yerr = [np.nanstd(a) for a in y]
        p = np.polyfit(ymed, x, 2, w=1 / np.array(yerr))
        print(f"Fit results c0*px^2 + c1*px + c2:\n{p}")
        return p, x, y

    def gauss(self, x, fwhm, x0, a):
        return a * np.exp(-0.5 * (x - x0) ** 2 / (fwhm / 2.3482) ** 2)

    def plot_calibration(self, p, x, y, to_elog=True):
        fpath = ""

        binmin = np.min([np.min(step) for step in y])
        binmax = np.max([np.max(step) for step in y])
        bins = np.arange(binmin, binmax, 1)
        bins_center = bins[:-1] + 0.5
        hists = np.array([np.histogram(step, bins=bins)[0] for step in y]).T
        plt.close("tt_calib")
        fig = plt.figure("tt_calib", figsize=(13, 6))
        gs = plt.GridSpec(1, 6, figure=fig)
        ax0 = fig.add_subplot(gs[0, 0])
        ax1 = fig.add_subplot(gs[0, 1:4], sharey=ax0)
        ax2 = fig.add_subplot(gs[0, 4:])
        ax1.pcolor(1e15 * x, bins_center, hists)
        line = ax1.errorbar(1e15 * x, ymed, yerr, color="red", marker=".", linestyle="")
        fit = ax1.plot(
            1e15 * np.polyval(p, ymed), ymed, label="poly fit", color="yellow"
        )
        ax0.axvline(0, linestyle="--", color="k")
        ax0.plot(1e15 * (np.polyval(p, ymed) - x), ymed, color="royalblue")
        ax1.set_xlabel("tt_kb.delay (fs)")
        ax1.set_ylabel("edge position (px)")
        at = (
            np.hstack(
                [np.polyval(p, ys) - np.polyval(p, ymeds) for ys, ymeds in zip(y, ymed)]
            )
            * 1e15
        )
        d = ax2.hist(
            at, bins="auto", label=f"std {np.std(at):.3} fs", color="royalblue"
        )
        ax2.hist(at, bins="auto", edgecolor="black", histtype="step")
        try:
            fx = d[1][:-1] + d[1][1] - d[1][0]
            parsopt, parss = curve_fit(self.gauss, fx, d[0], [30, 0, 50])
            plx = np.arange(np.min(d[1]), np.max(d[1]), 0.1)
            ax2.plot(plx, self.gauss(plx, *parsopt), color="k")
        except:
            print("Fitting of arrival time histogram failed")
            parsopt = [0, 0, 0]
            pass
        ax0.set_title("Residual")
        ax1.set_title("Scan")
        ax2.set_title(f"Jitter {parsopt[0]:.3} fs fwhm")
        ax1.legend()
        ax2.set_xlabel("arrival time (fs)")
        ax0.set_xlabel("$\Delta$t (fs)")
        ax0.set_ylabel("edge position (px)")
        ax2.set_yticks([])
        fig.tight_layout()
        plt.show()

        if to_elog:
            fpath = f"{Path.home()}/temp/tt_calib.jpg"
            fig.savefig(fpath, dpi=200)
            fpath = Path(fpath)
            dpath = Path(f"{Path.home()}/temp/tt_calib.pkl")
            df = DataFrame({"tt_kb.delay": x, "tt_kb.edge_position_px": y})
            df.to_pickle(dpath)
        if to_elog:
            try:
                msg = f"<h3>Timetool calibration results:</h3>\n"
                msg += f"Polynomial fit c0*edge_pos(px)^2 + c1*edge_pos(px) + c2:\n {p} \n\n"
                elog = self._get_elog()
                elog.post(msg.replace("\n", "<br>"), fpath, dpath)
            except Exception as e:
                print(f"Elog posting failed with:\n {e}")

    def set_calibration_values(self, p, pipeline=True, to_elog=True):
        if pipeline:
            old_calib = self.pipeline_edgefinding.config.calibration()
            # self.pipeline_edgefinding.config.calibration.set_target_value(p).wait() #This does not work because some issues with caching!
            self.update_proc_config({"calibration": list(p)}, self.pipeline_edgefinding)
            msg = f"Updated timetool processing pipeline calibration:\n"
            msg += f"old values: {old_calib} \nnew values: {p}"
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

    def calibrate(
        self,
        seconds=5,
        scan_range=1e-12,
        plot=True,
        pipeline=True,
        to_elog=True,
        filter_outliers=True,
        reverse_direction=False,
        bidirectional=False,
        update_pipeline_config=False,
    ):
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
        if bidirectional:
            print("Starting calibration in forward direction")

            print("Starting calibration in backward direction")

        x, y, df = self.get_calibration_data(
            seconds=seconds,
            scan_range=scan_range,
            reverse_direction=reverse_direction,
        )

        p, x, y = self.fit_calibration_data(x, y, filter_outliers=filter_outliers)

        if plot:
            self.plot_calibration(p, x, y, to_elog=to_elog)

        if update_pipeline_config:
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

    def start_camera_restarter(self):
        print(f"Starting camera restarter ...")
        from time import sleep

        while True:
            sleep(1)
            try:
                tx = float(self.pipeline_projection.info.statistics.tx().split("Hz")[0])
            except Exception as e:
                print(f"Could not read projection pipeline tx")
                print(e)
            if tx < 10:
                try:
                    self.camera_spectrometer.config_cs.stop()
                except Exception as e:
                    print(e)
                try:
                    self._get_elog().post(
                        f"### tx was {tx}: Automatically restarted timetool camera"
                    )
                    print(f"tx was {tx}Hz: Automatically restarted timetool camera")
                except Exception as e:
                    print(e)
                sleep(120)

    def update_proc_config(self, cfg_dict, pipeline):
        cfg = pipeline._get_config()
        cfg.update(cfg_dict)
        pipeline.pc.set_instance_config(pipeline.pipeline_name, cfg)

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
        with source(
            channels=[
                self.edge_position_fs.bs_channel,
                self.edge_position_px.bs_channel,
                self.edge_amplitude.bs_channel,
            ]
        ) as s:
            while True:
                d = s.receive()
                fs, px, mx = [
                    [d.data.data[self.edge_position_fs.bs_channel].value],
                    d.data.data[self.edge_position_px.bs_channel].value,
                    d.data.data[self.edge_amplitude.bs_channel].value,
                ]
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
