import requests
from pathlib import Path
from time import sleep
from ..devices_general.detectors import PvDataStream
from ..acquisition.utilities import Acquisition


class Daq:
    def __init__(
        self,
        broker_address="http://sf-daq:10002",
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
        name=None,
    ):
        self.channels = {}
        if channels_JF:
            self.channels["channels_JF"] = channels_JF
        if channels_BS:
            self.channels["channels_BS"] = channels_BS
        if channels_BSCAM:
            self.channels["channels_BSCAM"] = channels_BSCAM
        self.broker_address = broker_address
        self.timeout = timeout
        self.pgroup = pgroup
        if type(pulse_id_adj) is str:
            self.pulse_id = PvDataStream(pulse_id_adj, name="pulse_id")
        else:
            self.pulse_id = pulse_id_adj
        self.running = []
        self._event_master = event_master
        self._detectors_event_code = detectors_event_code
        self.name = name
        self._default_file_path = None

    def acquire(self, file_name=None, Npulses=100, acq_pars={}):
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
        start_id = self.pulse_id.get_current_value()
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
        print("This is tha additional input:", kwargs)
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
            files_extensions.append("CADUMP")
        if channels_BS:
            parameters["channels_list"] = channels_BS
            files_extensions.append("BSDATA")
        if channels_JF:
            parameters["detectors"] = {tn: {} for tn in channels_JF}
            for ch in channels_JF:
                files_extensions.append(ch)
        if channels_BSCAM:
            parameters["camera_list"] = channels_BSCAM
            files_extensions.append("CAMERAS")
        if directory_relative:
            parameters["directory_name"] = directory_relative.as_posix()

        parameters["pgroup"] = pgroup

        runno = validate_response(
            requests.post(
                f"{self.broker_address}/retrieve_from_buffers",
                json=parameters,
                timeout=self.timeout,
            ).json()
        )

        filenames = [
            (directory_base / Path(filename_format.format(runno)))
            .with_suffix(f".{ext}.h5")
            .as_posix()
            for ext in files_extensions
        ]

        return runno, filenames

    def get_detector_frequency(self):
        return self._event_master.get_evtcode_frequency(self._detectors_event_code)


def validate_response(resp):
    if resp.get("status") == "ok":
        return int(resp.get("message"))

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
