import time
import requests
from pathlib import Path
from time import sleep

from eco.elements.protocols import Adjustable
from ..epics.detector import DetectorPvDataStream
from epics import PV
from ..acquisition.utilities import Acquisition
from ..elements.assembly import Assembly
from ..utilities.path_alias import PathAlias


class Daq(Assembly):
    def __init__(
        self,
        broker_address="http://sf-daq:10002",
        broker_address_aux="http://sf-daq:10003",
        timeout=10,
        pgroup=None,
        pulse_id_adj=None,
        event_master=None,
        detectors_event_code=None,
        instrument=None,
        channels_JF=None,
        channels_BS=None,
        channels_BSCAM=None,
        channels_CA=None,
        config_JFs=None,
        rate_multiplicator=None,
        name=None,
    ):
        super().__init__(name=name)
        self.channels = {}
        self.path_alias = PathAlias()
        if channels_JF:
            self.channels["channels_JF"] = channels_JF
        if channels_BS:
            self.channels["channels_BS"] = channels_BS
        if channels_BSCAM:
            self.channels["channels_BSCAM"] = channels_BSCAM
        if channels_CA:
            self.channels["channels_CA"] = channels_CA
        if config_JFs:
            self.config_JFs = config_JFs
        else:
            self.config_JFs = {}
        self.broker_address = broker_address
        self.broker_address_aux = broker_address_aux
        self.timeout = timeout
        self._pgroup = pgroup
        if type(pulse_id_adj) is str:
            self.pulse_id = DetectorPvDataStream(pulse_id_adj, name="pulse_id")
            self._pid_wo_automonitor = PV(
                "SGE-CPCW-85-EVR0:RX-PULSEID",
                connection_timeout=0.05,
                auto_monitor=False,
            )
        else:
            self.pulse_id = pulse_id_adj
        self.running = []
        self._event_master = event_master
        self._detectors_event_code = detectors_event_code
        self.name = name
        self._default_file_path = None
        if not rate_multiplicator=='auto':
            print('warning: rate multiplicator automatically determined from event_master!')
            
        
    @property
    def rate_multiplicator(self):
        freq = self._event_master.__dict__[f'code{self._detectors_event_code:03d}'].frequency.get_current_value()
        return int(100/freq)
    
    @property
    def pgroup(self):
        if isinstance(self._pgroup, Adjustable):
            return self._pgroup.get_current_value()
        else:
            return self._pgroup

    @pgroup.setter
    def pgroup(self, value):
        if isinstance(self._pgroup, Adjustable):
            self._pgroup.set_target_value(value).wait()
        else:
            self._pgroup = value

    def acquire(self, file_name=None, Npulses=100, acq_pars={}):
        print(acq_pars)
        print(file_name, Npulses)
        acquisition = Acquisition(
            acquire=None,
            acquisition_kwargs={"Npulses": Npulses},
        )

        def acquire():
            runno, file_names = self.acquire_pulses(
                Npulses,
                directory_relative=Path(file_name).parents[0],
                wait=True,
                channels_JF=self.channels["channels_JF"].get_current_value(),
                channels_BS=self.channels["channels_BS"].get_current_value(),
                channels_BSCAM=self.channels["channels_BSCAM"].get_current_value(),
                channels_CA=self.channels["channels_CA"].get_current_value(),
                **acq_pars,
            )
            acquisition.acquisition_kwargs.update({"file_names": file_names})
            for key, val in acquisition.acquisition_kwargs.items():
                acquisition.__dict__[key] = val

        acquisition.set_acquire_foo(acquire, hold=False)

        return acquisition

    def acquire_pulses(self, Npulses, label=None, wait=True, **kwargs):
        ix = self.start(label=label, **kwargs)
        return self.stop(
            stop_id=self.running[ix]["start_id"] + Npulses - 1, acq_ix=ix, wait=wait
        )

    def start(self, label=None, **kwargs):
        starttime_local = time.time()
        while self.pulse_id._pv.get_timevars()['timestamp'] < starttime_local:
            time.sleep(.02)
        start_id = self.pulse_id.get_current_value(use_monitor=False)
        acq_pars = {
            "label": label,
            "start_id": start_id,
        }
        acq_pars.update(kwargs)
        self.running.append(acq_pars)
        return self.running.index(acq_pars)

    def stop(
        self,
        stop_id=None,
        acq_ix=None,
        label=None,
        wait=True,
        wait_cycle_sleep=0.01,
    ):
        if not stop_id:
            stop_id = int(self.pulse_id.get_current_value())
        if not acq_ix:
            acq_ix = -1

        acq_pars = self.running.pop(acq_ix)
        acq_pars["stop_id"] = stop_id
        label = acq_pars.pop("label")
        if wait:
            while int(self.pulse_id.get_current_value()) < stop_id:
                sleep(wait_cycle_sleep)
        return self.retrieve(**acq_pars)

    def retrieve(
        self,
        *,
        start_id,
        stop_id,
        directory_relative=None,
        channels_CA=None,
        channels_JF=None,
        channels_BS=None,
        channels_BSCAM=None,
        pgroup=None,
        pgroup_base_path="/sf/bernina/data/{:s}/raw",
        filename_format="run_{:06d}",
        **kwargs,
    ):
        # print("This is the additional input:", kwargs)
        if not pgroup:
            pgroup = self.pgroup
        if not pgroup:
            raise Exception("a pgroup needs to be defined")
        if not directory_relative:
            directory_relative = ""
        directory_relative = Path(directory_relative)
        directory_base = Path(pgroup_base_path.format(pgroup)) / directory_relative
        files_extensions = []
        parameters = {"start_pulseid": start_id, "stop_pulseid": stop_id}
        parameters.update(kwargs)
        print(parameters)
        if channels_CA:
            parameters["pv_list"] = channels_CA
            files_extensions.append("PVCHANNELS")
        if channels_BS:
            parameters["channels_list"] = channels_BS
            files_extensions.append("BSDATA")
        if channels_JF:
            parameters["detectors"] = {
                tn: self.config_JFs().get(tn, {}) for tn in channels_JF
            }
            for ch in channels_JF:
                files_extensions.append(ch)
        if channels_BSCAM:
            parameters["camera_list"] = channels_BSCAM
            files_extensions.append("CAMERAS")
        if directory_relative:
            parameters["directory_name"] = directory_relative.as_posix()

        parameters["pgroup"] = pgroup
        parameters["rate_multiplicator"] = self.rate_multiplicator
        # print("----- debug info ----->\n", parameters, "\n<----- debug info -----")
        response = validate_response(
            requests.post(
                f"{self.broker_address}/retrieve_from_buffers",
                json=parameters,
                timeout=self.timeout,
            ).json()
        )

        runno = response["run_number"]

        filenames = response["files"]

        # filenames = [
        #     (directory_base / Path(filename_format.format(runno)))
        #     .with_suffix(f".{ext}.h5")
        #     .as_posix()
        #     for ext in files_extensions
        # ]

        return runno, filenames

    def get_next_run_number(self, pgroup=None):
        if pgroup is None:
            pgroup = self.pgroup
        res = requests.get(
            f"{self.broker_address}/get_next_run_number",
            json={"pgroup": pgroup},
            timeout=self.timeout,
        )
        assert res.ok, f"Getting last run number failed {res.raise_for_status()}"
        return int(res.json()["message"])

    def get_last_run_number(self, pgroup=None):
        if pgroup is None:
            pgroup = self.pgroup
        res = requests.get(
            f"{self.broker_address}/get_last_run_number",
            json={"pgroup": pgroup},
            timeout=self.timeout,
        )
        assert res.ok, f"Getting last run number failed {res.raise_for_status()}"
        return int(res.json()["message"])

    def get_detector_frequency(self):
        return self._event_master.event_codes[
            self._detectors_event_code
        ].frequency.get_current_value()

    def get_JFs_available(self):
        return requests.get(f"{self.broker_address}/get_allowed_detectors_list").json()[
            "detectors"
        ]

    def get_JFs_running(self):
        return requests.get(f"{self.broker_address}/get_running_detectors_list").json()[
            "detectors"
        ]

    def power_on_JF(self, JF_channel):
        par = {"detector_name": JF_channel}
        return requests.post(
            f"{self.broker_address}/power_on_detector", json=par
        ).json()

    def take_pedestal(self, JF_list=None, pgroup=None):
        if pgroup is None:
            pgroup = self.pgroup
        if not JF_list:
            JF_list = self.get_JFs_running()
        parameters = {
            "pgroup": pgroup,
            "rate_multiplicator": 1,
            "detectors": {tJF: {} for tJF in JF_list},
        }
        return requests.post(
            f"{self.broker_address}/take_pedestal", json=parameters
        ).json()

    def append_aux(self, *file_names, run_number=None, pgroup=None):
        if pgroup is None:
            pgroup = self.pgroup
        if run_number is None:
            run_number = self.get_last_run_number()

        return requests.post(
            self.broker_address_aux + "/copy_user_files",
            json={"pgroup": pgroup, "run_number": run_number, "files": file_names},
        )


#     def get_dap_settings(detector_name):
#     dap_parameters = {}
#     try:
#         r = requests.post(f'{broker_slow_address}/get_dap_settings', json={'detector_name': detector_name}, timeout=TIMEOUT_DAQ)
#         answer = r.json()
#         if "status" in answer and answer["status"] == "ok":
#             dap_parameters = answer.get("message", {})
#         else:
#             print(f"Got bad result from daq for dap parameters : {answer}")
#         return dap_parameters
#     except Exception as e:
#         print(f"Error to get dap configuration {e}")
#         return dap_parameters

# def set_dap_settings(detector_name, parameters):
#     try:
#         r = requests.post(f'{broker_slow_address}/set_dap_settings', json={'detector_name': detector_name, 'parameters': parameters}, timeout=TIMEOUT_DAQ)
#         answer = r.json()
#         print(f"answer from daq for changing dap parameters for detector {detector_name} : {answer}")
#     except Exception as e:
#         print(f"Error to set dap configuration {e}")


def validate_response(resp):
    if resp.get("status") == "ok":
        return resp
    message = resp.get("message", "Unknown error")
    msg = "An error happened on the server:\n{}".format(message)
    raise Exception(msg)


# parameters = {
#     "pgroup":"p16584",
#     "start_pulseid":12054777413-2000,
#     "stop_pulseid":12054777413-1000,
#     "channels_list":[
#         "SAR-CVME-TIFALL5:EvtSet"
#     ],
#     "run_number"
# }

# r = requests.post(f'{broker_address}/retrieve_from_buffers',json=parameters, timeout=TIMEOUT_DAQ).json()
