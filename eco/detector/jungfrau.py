import shutil
import time
from tkinter import W

from eco.base.adjustable import Adjustable
from eco.devices_general.therm import ChillerThermotek
from eco.elements.adj_obj import AdjustableObject
from eco.elements.detector import DetectorGet
from ..elements.adjustable import AdjustableFS, AdjustableVirtual, AdjustableGetSet
from ..epics_utils.adjustable import AdjustablePv
from ..elements.assembly import Assembly
from ..aliases import Alias
from pathlib import Path
from ..elements import memory
from datetime import datetime
import requests


class JungfrauChannel(Assembly):
    def __init__(
        self,
        jf_id,
        name=None,
    ):
        super().__init__(name=name)
        self.alias = Alias(name, channel=jf_id, channeltype="JF")


class Jungfrau(Assembly):
    def __init__(
        self,
        jf_id,
        pv_trigger="SAR-CVME-TIFALL5-EVG0:SoftEvt-EvtCode-SP",
        trigger_on=254,
        trigger_off=255,
        broker_address="http://sf-daq:10002",
        broker_address_aux="http://sf-daq:10003",
        pgroup_adj=None,
        config_adj=None,
        chiller_thermotek="SARES20-CHIL",
        event_master=None,
        detectors_event_code=None,
        name=None,
    ):
        super().__init__(name=name)
        # self.alias = Alias(name, channel=jf_id, channeltype="JF")
        self._event_master = event_master
        self._detectors_event_code = detectors_event_code
        self.pgroup = pgroup_adj
        self.jf_id = jf_id
        self.broker_address = broker_address
        self.broker_address_aux = broker_address_aux
        self._append(
            DetectorGet, lambda: f"http://{self.get_vis_url()}", name="visulization_url"
        )
        self._append(JungfrauChannel, jf_id, name="data")
        self._append(JungfrauChannel, jf_id + "_rawdata", name="data_raw")
        self._append(
            JungfrauChannel, jf_id + "_dap_col4", name="data_online_processing"
        )
        for n in range(10):
            self._append(
                JungfrauChannel,
                jf_id + f"_dap_col{n+4}",
                name=f"data_online_processing_roi{n}",
                is_display=False,
            )
        self._append(
            JungfrauChannel, jf_id + "_dap_col3", name="ppref_online_processing"
        )
        self._append(
            AdjustablePv,
            pv_trigger,
            is_display=True,
            is_setting=False,
            name="trigger",
        )
        self._trigger_on = trigger_on
        self._trigger_off = trigger_off
        self._append(
            AdjustableVirtual,
            [self.trigger],
            lambda value: value == self._trigger_on,
            self._set_trigger_enable,
            name="trigger_enable",
            append_aliases=False,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self.get_present_pedestal_filename,
            lambda value: NotImplementedError(
                "Can not set the pedestal file manually yet."
            ),
            name="pedestal_file",
            is_display=True,
        )
        self._append(
            AdjustableGetSet,
            self.get_present_gain_filename,
            lambda value: NotImplementedError(
                "Can not set the pedestal file manually yet."
            ),
            name="gain_file",
            is_display=True,
        )
        self._append(
            AdjustableGetSet,
            self.get_present_pedestal_filename_in_run,
            lambda value: NotImplementedError(
                "Can not set the pedestal file manually yet."
            ),
            name="pedestal_file_in_run",
            is_display=True,
        )
        self._append(
            AdjustableGetSet,
            self.get_present_gain_filename_in_run,
            lambda value: NotImplementedError(
                "Can not set the pedestal file manually yet."
            ),
            name="gain_file_in_run",
            is_display=True,
        )
        self._last_dap_req_time = 0
        self._append(
            AdjustableFS,
            f"/sf/bernina/code/gac-bernina/eco_cnf_bernina/reference_values/dap_settings_{self.jf_id:s}.json",
            name="_dap_settings_storage",
            is_display=False,
            is_setting=False,
        )
        self._append(
            AdjustableGetSet,
            self.get_dap_settings,
            self.set_dap_settings,
            name="_dap_settings",
            is_display=False,
            is_setting=False,
        )
        self._append(
            AdjustableObject,
            self._dap_settings,
            is_setting_children=True,
            name="settings_dap",
        )

        # Hardware settings (delay/detector_mode/exptime/gain_mode) served by
        # sf_daq_broker's "slow" broker, at broker_address_aux -- same
        # request/caching pattern as _dap_settings above.
        self._last_detector_settings_req_time = 0
        self._append(
            AdjustableFS,
            f"/sf/bernina/code/gac-bernina/eco_cnf_bernina/reference_values/detector_settings_{self.jf_id:s}.json",
            name="_detector_settings_storage",
            is_display=False,
            is_setting=False,
        )
        self._append(
            AdjustableGetSet,
            self.get_detector_settings,
            self.set_detector_settings,
            name="_detector_settings",
            is_display=False,
            is_setting=False,
        )
        self._append(
            AdjustableObject,
            self._detector_settings,
            is_setting_children=True,
            name="settings_detector",
        )

        if config_adj:
            self._append(
                JungfrauDaqConfig,
                jf_id,
                config_adj,
                name="config_daq",
                is_setting=True,
                is_status=True,
                is_display="recursive",
            )
        if chiller_thermotek:
            self._append(
                ChillerThermotek,
                pvbase=chiller_thermotek,
                name="chiller",
                is_display="recursive",
            )

    def set_dap_rois(self, *rois):
        tmp = self.settings_dap._base_dict()
        tmp["roi_x1"] = [roi[0] for roi in rois if roi]
        tmp["roi_x2"] = [roi[1] for roi in rois if roi]
        tmp["roi_y1"] = [roi[2] for roi in rois if roi]
        tmp["roi_y2"] = [roi[3] for roi in rois if roi]
        self.settings_dap._base_dict(tmp)

    def _set_trigger_enable(self, value):
        if value:
            self.trigger.set_target_value(self._trigger_on).wait()
        else:
            self.trigger.set_target_value(self._trigger_off).wait()

    def get_present_gain_filename(self):
        filepath = Path(f"/sf/jungfrau/config/gainMaps/{self.jf_id}/gains.h5")

        if filepath.exists():
            return filepath.as_posix()
        else:
            raise Exception(f"File {filepath.as_posix()} seems not to exist!")

    def get_present_gain_filename_in_run(self, intempdir=False):
        f = Path(self.get_present_gain_filename())
        dest = Path(
            f"/sf/bernina/data/{self.pgroup()}/res/tmp/gainmaps_{self.jf_id}.h5"
        )

        try:
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o775)
                try:
                    dest.parent.chmod(0o775)
                except:
                    pass
                shutil.copyfile(f, dest)

        except PermissionError:
            return "No permissions to res directory!"

        if intempdir:
            return dest.as_posix()
        else:
            return f"aux/{dest.name}"

    def get_present_pedestal_filename(self):
        searchpath = Path(f"/sf/jungfrau/data/pedestal/{self.jf_id}")
        filelist = list(searchpath.glob("*.h5"))
        times = [datetime.strptime(f.stem, "%Y%m%d_%H%M%S") for f in filelist]
        if len(times) == 0:
            return ''
        else:
            return filelist[times.index(max(times))].as_posix()

    def get_present_pedestal_filename_in_run(self, intempdir=False):
        f = Path(self.get_present_pedestal_filename())
        dest = Path(
            f"/sf/bernina/data/{self.pgroup()}/res/tmp/pedestal_{self.jf_id}_{f.stem}.h5"
        )
        try:
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o775)
                try:
                    dest.parent.chmod(0o775)
                except:
                    pass
                shutil.copyfile(f, dest)
        except PermissionError:
            return "No poermissions to res directory!"

        if intempdir:
            return dest.as_posix()
        else:
            return f"aux/{dest.name}"

    def get_dap_settings(self, force=False):
        """
        Read this detector's DAP (online analysis pipeline) parameters.

        REST: GET {broker_address_aux}/get_dap_settings. broker_address_aux
        is sf_daq_broker's "slow" broker (DEFAULT_BROKER_SLOW_REST_PORT,
        served by broker_slow.py) despite the "aux" name used in eco --
        confirmed against sf_daq_broker/config.py, where both
        eco.acquisition.daq_client.Daq.broker_address_aux and this class's
        broker_address_aux default to port 10003, matching
        DEFAULT_BROKER_SLOW_REST_PORT exactly. It reads
        pipeline_parameters.{detector_name}.json off GPFS server-side.

        force=False (default) returns the last cached value from
        _dap_settings_storage without touching the network. force=True
        re-queries the broker, throttled to once per 5 s via
        _last_dap_req_time -- because the slow broker is single-threaded
        (bottle's default wsgiref server handles one HTTP request at a time,
        broker process-wide), so hammering this stalls every other client of
        broker_address_aux for as long as each call takes. get_detector_settings
        below follows the same pattern for the same reason.
        """
        if force:
            if 5 < (time.time() - self._last_dap_req_time):
                self._last_dap_message = requests.get(
                    f"{self.broker_address_aux}/get_dap_settings",
                    json={"detector_name": self.jf_id},
                ).json()
                self._last_dap_req_time = time.time()

            if self._last_dap_message["status"] == "ok":
                self._dap_settings_storage.set_target_value(
                    self._last_dap_message["parameters"]
                ).wait()
                return self._last_dap_message["parameters"]
        else:
            val = self._dap_settings_storage.get_current_value()
            if not val:
                val = self.get_dap_settings(force=True)
            return val

    def set_dap_settings(self, dap_setting_dict):
        """
        Change this detector's DAP parameters.

        REST: POST {broker_address_aux}/set_dap_settings, body
        {"detector_name": jf_id, "parameters": dap_setting_dict}. The
        broker diffs against the current file, keeps a timestamped backup,
        and rolls back automatically if the write fails.
        """
        m = requests.post(
            f"{self.broker_address_aux}/set_dap_settings",
            json={"detector_name": self.jf_id, "parameters": dap_setting_dict},
        ).json()
        if m["status"] == "ok":
            self._dap_settings_storage.set_target_value(dap_setting_dict).wait()
            return m

    def get_detector_settings(self, force=False):
        """
        Read this detector's live hardware settings (delay, detector_mode,
        exptime, gain_mode).

        REST: GET {broker_address_aux}/get_detector_settings -- same slow
        broker as get_dap_settings above, same caching/throttling pattern
        and the same reasoning: force=False (default) returns the cached
        value from _detector_settings_storage; force=True re-queries the
        broker, throttled to once per 5 s via _last_detector_settings_req_time.
        """
        if force:
            if 5 < (time.time() - self._last_detector_settings_req_time):
                self._last_detector_settings_message = requests.get(
                    f"{self.broker_address_aux}/get_detector_settings",
                    json={"detector_name": self.jf_id},
                ).json()
                self._last_detector_settings_req_time = time.time()

            if self._last_detector_settings_message["status"] == "ok":
                self._detector_settings_storage.set_target_value(
                    self._last_detector_settings_message["parameters"]
                ).wait()
                return self._last_detector_settings_message["parameters"]
        else:
            val = self._detector_settings_storage.get_current_value()
            if not val:
                val = self.get_detector_settings(force=True)
            return val

    def set_detector_settings(self, detector_setting_dict):
        """
        Change this detector's live hardware settings.

        REST: POST {broker_address_aux}/set_detector_settings, body
        {"detector_name": jf_id, "parameters": detector_setting_dict}. Only
        delay, detector_mode, exptime and gain_mode are recognised
        server-side; anything else in the dict is ignored. The broker stops
        the trigger, applies changes via setattr on its Detector object,
        then restarts the trigger -- i.e. this briefly interrupts triggering
        for this detector. Do not call it mid-acquisition.
        """
        m = requests.post(
            f"{self.broker_address_aux}/set_detector_settings",
            json={"detector_name": self.jf_id, "parameters": detector_setting_dict},
        ).json()
        if m["status"] == "ok":
            self._detector_settings_storage.set_target_value(
                detector_setting_dict
            ).wait()
            return m

    def get_status(self):
        """
        REST: GET {broker_address_aux}/get_detector_status -> the broker's
        Detector.get_status() dict. Single round trip, not cached (unlike
        get_detector_settings/get_dap_settings above).
        """
        return requests.get(
            f"{self.broker_address_aux}/get_detector_status",
            json={"detector_name": self.jf_id},
        ).json()["detector_status"]

    def get_pings(self):
        """
        Per-module network reachability for this detector.

        REST: GET {broker_address_aux}/get_detector_pings ->
        {"responding": [module_numbers], "unreachable": [module_numbers]}.
        Unlike the other diagnostics calls here, this one actually pings
        hardware over the network server-side and can take noticeably
        longer if a module is down -- and because the slow broker handles
        one request at a time (see get_dap_settings docstring), that stalls
        every other client of broker_address_aux for as long as this call
        takes. Don't call it from inside a scan loop.
        """
        return requests.get(
            f"{self.broker_address_aux}/get_detector_pings",
            json={"detector_name": self.jf_id},
        ).json()["pings"]

    def get_temperatures(self):
        """REST: GET {broker_address_aux}/get_detector_temperatures."""
        return requests.get(
            f"{self.broker_address_aux}/get_detector_temperatures",
            json={"detector_name": self.jf_id},
        ).json()["temperatures"]

    def get_stats(self):
        """
        Combined health check: is this detector actually alive and writing.

        REST: GET {broker_address_aux}/get_jfstats (note: the request key is
        "det", not "detector_name", unlike every other slow-broker
        endpoint here) -> {"parameters" (jfctrl monitor), "temperatures",
        "writing": bool}. "writing" reflects whether the detector's buffer
        file on the server was modified within the last 30 s -- of
        everything sf-daq exposes, this is the closest to a direct "is data
        actually flowing right now" signal, as opposed to get_isrunning()
        below, which only checks whether this jf_id is in the broker's
        *configured* running-detectors list, not whether it is actually
        producing data.
        """
        return requests.get(
            f"{self.broker_address_aux}/get_jfstats",
            json={"det": self.jf_id},
        ).json()

    def get_detector_frequency(self):
        return self._event_master.event_codes[
            self._detectors_event_code
        ].frequency.get_current_value()

    def get_availability(self):
        is_available = (
            self.jf_id
            in requests.get(f"{self.broker_address}/get_allowed_detectors").json()[
                "detectors"
            ]
        )
        return is_available

    def get_vis_url(self):
        tmp = requests.get(f"{self.broker_address}/get_allowed_detectors").json()
        ix = tmp["detectors"].index(self.jf_id)
        return tmp["visualisation_address"][ix]

    def get_isrunning(self):
        is_running = (
            self.jf_id
            in requests.get(f"{self.broker_address}/get_running_detectors").json()[
                "detectors"
            ]
        )
        return is_running

    def power_on(self):
        JF_channel = self.jf_id
        par = {"detector_name": JF_channel}
        return requests.post(
            f"{self.broker_address}/power_on_detector", json=par
        ).json()

    # def take_pedestal(self, JF_list=None, pgroup=None):
    #     if pgroup is None:
    #         pgroup = self.pgroup
    #     if not JF_list:
    #         JF_list = self.get_JFs_running()
    #     parameters = {
    #         "pgroup": pgroup,
    #         "rate_multiplicator": 1,adc_to_energy
    #         "detectors": {tJF: {} for tJF in JF_list},
    #     }
    #     return requests.post(
    #         f"{self.broker_address}/take_pedestal", json=parameters
    #     ).json()


class JungfrauDaqConfig(Assembly):
    def __init__(self, jf_id, jf_daq_cfg: Adjustable, name=None):
        super().__init__(name=name)
        self._jf_id = jf_id
        self._jf_daq_cfg = jf_daq_cfg
        cfg = self._jf_daq_cfg.get_current_value()
        if self._jf_id not in cfg.keys():
            cfg[self._jf_id] = {}
            self._jf_daq_cfg.set_target_value(cfg).wait()

        self._append(
            AdjustableGetSet,
            self._get_adc_to_energy,
            self._set_adc_to_energy,
            name="convert_adc_to_energy",
            is_display=True,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self._get_geometry_corr,
            self._set_geometry_corr,
            name="apply_tile_geometry",
            is_display=True,
            is_setting=True,
        )

        self._append(
            AdjustableGetSet,
            self._get_compressed_bitshuffle,
            self._set_compressed_bitshuffle,
            name="compress_bitshuffle",
            is_display=True,
            is_setting=True,
        )

        self._append(
            AdjustableGetSet,
            self._get_rounding_factor,
            self._set_rounding_factor,
            name="rounding_factor_keV",
            is_display=True,
            is_setting=True,
        )

        self._append(
            AdjustableGetSet,
            self._get_large_pixel_processing,
            self._set_large_pixel_processing,
            name="large_pixel_processing",
            is_display=True,
            is_setting=True,
        )

        self._append(
            AdjustableGetSet,
            self._get_disabled_modules,
            self._set_disabled_modules,
            name="disabled_tiles",
            is_display=True,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self._get_binning,
            self._set_binning,
            name="downsample",
            is_display=True,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self._get_keep_raw_data,
            self._set_keep_raw_data,
            name="keep_raw_data",
            is_display=True,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self._get_save_online_processing,
            self._set_save_online_processing,
            name="save_online_processing",
            is_display=True,
            is_setting=True,
        )

    def _get_adc_to_energy(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["adc_to_energy"]
        except KeyError:
            return False

    def _set_adc_to_energy(self, value):
        if value:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["adc_to_energy"] = True
            self._jf_daq_cfg.set_target_value(cfg).wait()
        else:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["adc_to_energy"] = False
            self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_geometry_corr(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["geometry"]
        except KeyError:
            return "not sure what happens"

    def _set_geometry_corr(self, value):
        if value:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["geometry"] = True
            self._jf_daq_cfg.set_target_value(cfg).wait()
        else:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["geometry"] = False
            self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_compressed_bitshuffle(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["compression"]
        except KeyError:
            return False

    def _set_compressed_bitshuffle(self, value):
        if value:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["compression"] = True
            self._jf_daq_cfg.set_target_value(cfg).wait()
        else:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["compression"] = False
            self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_save_online_processing(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["save_dap_results"]
        except KeyError:
            # raise Exception("unclear what the default for keeping raw files is!")
            return None

    def _set_save_online_processing(self, value):
        if value:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["save_dap_results"] = True
            self._jf_daq_cfg.set_target_value(cfg).wait()
        else:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["save_dap_results"] = False
            self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_keep_raw_data(self, *args):
        try:
            return not self._jf_daq_cfg.get_current_value()[self._jf_id][
                "remove_raw_files"
            ]
        except KeyError:
            # raise Exception("unclear what the default for keeping raw files is!")
            return None

    def _set_keep_raw_data(self, value):
        if value:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["remove_raw_files"] = False
            self._jf_daq_cfg.set_target_value(cfg).wait()
        else:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["remove_raw_files"] = True
            self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_large_pixel_processing(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id][
                "double_pixels_action"
            ]
        except KeyError:
            # raise Exception("unclear what the default for double pixels is!")
            return None

    def _set_large_pixel_processing(self, value):
        cfg = self._jf_daq_cfg.get_current_value()
        cfg[self._jf_id]["double_pixels_action"] = value
        self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_rounding_factor(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["factor"]
        except KeyError:
            # raise Exception("unclear what the default for double pixels is!")
            return None

    def _set_rounding_factor(self, value):
        cfg = self._jf_daq_cfg.get_current_value()
        cfg[self._jf_id]["factor"] = value
        self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_disabled_modules(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["disabled_modules"]
        except KeyError:
            return []

    def _set_disabled_modules(self, value):
        cfg = self._jf_daq_cfg.get_current_value()
        if value == []:
            cfg[self._jf_id].pop("disabled_modules")
        else:
            cfg[self._jf_id]["disabled_modules"] = value
        self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_binning(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["downsample"]
        except KeyError:
            return [1, 1]

    def _set_binning(self, value):
        cfg = self._jf_daq_cfg.get_current_value()
        if value == [1, 1]:
            cfg[self._jf_id].pop("downsample")
        else:
            cfg[self._jf_id]["downsample"] = value
        self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_keepraw(self, *args):
        try:
            remove_raw = self._jf_daq_cfg.get_current_value()[self._jf_id][
                "remove_raw_files"
            ]
            # if type(remove_raw) is bool:
            return remove_raw

        except KeyError:
            return "not sure what happens"

    def _set_keepraw(self, value):
        cfg = self._jf_daq_cfg.get_current_value()
        cfg[self._jf_id]["remove_raw_files"] = value
        self._jf_daq_cfg.set_target_value(cfg).wait()


#          {
#     "adc_to_energy": true,
#     "compression": true,
#     "double_pixels_actions": "interpolate",
#     "downsample": [
#         1,
#         1
#     ],
#     "factor": 0.25,x
#     "geometry": true,
#     "remove_raw_files": false
#   "disabled_modules": [],
# },
