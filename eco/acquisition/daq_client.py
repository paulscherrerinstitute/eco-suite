import json
import pickle
import shutil
from threading import Thread
import time
import traceback
import colorama
import numpy as np
import requests
from pathlib import Path
from time import sleep

from eco.utilities import NumpyEncoder
from eco.elements.protocols import Adjustable
from eco.utilities.utilities import foo_get_kwargs
from ..epics.detector import DetectorPvDataStream
from ..epics.utilities_epics import Monitor
from epics import PV
from ..acquisition.utilities import Acquisition
from ..elements.assembly import Assembly
from ..utilities.path_alias import PathAlias
import inputimeout
from IPython import get_ipython
from os.path import relpath

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
        namespace=None,
        checker=None,
        run_table=None,
        pulse_picker=None,
        elog = None,
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
        self.namespace = namespace
        self.checker = checker
        self.run_table=run_table
        self.pulse_picker = pulse_picker
        self._default_file_path = None
        if not rate_multiplicator == "auto":
            print(
                "warning: rate multiplicator automatically determined from event_master!"
            )
        self.callbacks_start_scan = [
            self.check_counters_for_scan, 
            self.init_namespace,
            self.append_start_status_to_scan, 
            self.count_run_number_up_and_attach_to_scan, 
            self.scan_message_to_elog,
            self._create_runtable_metadata_append_status_to_runtable, 
            self.append_scan_monitors]
        self.callbacks_start_step = [
            self.copy_aliases_to_scan,
            self.check_checker_before_step,
            self.pulse_picker_action_start_step,
            ]
        self.callbacks_step_counting = []
        self.callbacks_end_step = [
            self.pulse_picker_action_end_step,
            self.copy_scan_info_to_raw, 
            self.check_checker_after_step
            ]
        self.callbacks_end_scan = [
            self.append_status_to_scan_and_store, 
            self.copy_scan_info_to_raw, 
            self.end_scan_monitors,
            ]
        self.elog = elog

    @property
    def rate_multiplicator(self):
        freq = self._event_master.__dict__[
            f"code{self._detectors_event_code:03d}"
        ].frequency.get_current_value()
        return int(100 / freq)

    @property
    def pgroup(self):
        if isinstance(self._pgroup, Adjustable):
            return self._pgroup.get_current_value()
        else:
            return self._pgroup

    @pgroup.setter
    def pgroup(self, value):
        if isinstance(self._pgroup, Adjustable):
            return self._pgroup.set_target_value().wait()
        self._pgroup = value

    

    def acquire(self, scan=None, file_name=None, run_number=None, Npulses=100, acq_pars={}):
        acq_pars = {}
        if scan:
            acq_pars = {
                "scan_info": {
                    "scan_name": scan.description(),
                    "scan_values": scan.values_current_step,
                    "scan_readbacks": scan.readbacks_current_step,
                    "scan_step_info": {
                        "step_number": scan.next_step + 1,
                    },
                    "name": [adj.name for adj in scan.adjustables],
                    "expected_total_number_of_steps": scan.number_of_steps(),
                },
                "run_number": scan.daq_run_number,
                "user_tag": "usertag",
            }
        if run_number is not None:
            acq_pars["run_number"] = run_number

        
            
        acquisition = Acquisition(
            acquire=None,
            acquisition_kwargs={"Npulses": Npulses},
        )

        def acquire():
            
            response = self.acquire_pulses(
                Npulses,
                # directory_relative=Path(file_name).parents[0],
                wait=True,
                channels_JF=self.channels["channels_JF"].get_current_value(),
                channels_BS=self.channels["channels_BS"].get_current_value(),
                channels_BSCAM=self.channels["channels_BSCAM"].get_current_value(),
                channels_CA=self.channels["channels_CA"].get_current_value(),
                **acq_pars,
            )
            acquisition.acquisition_kwargs.update({"file_names": response["files"]})
            if scan and not scan.daq_run_number==int(response["run_number"]):
                raise Exception(
                    f"Run number mismatch: scan {scan.daq_run_number} != response {int(response['run_number'])}"
                )            

            for key, val in acquisition.acquisition_kwargs.items():
                acquisition.__dict__[key] = val

        acquisition.set_acquire_foo(acquire, hold=False)

        return acquisition

    def acquire_pulses(self, Npulses, label=None, wait=True, **kwargs):
        ix = self.start(label=label, **kwargs)
        return self.stop(
            stop_id=self.running[ix]["start_id"] + Npulses - 1, acq_ix=ix, wait=wait
        )

    def start(self, label=None, scan=None, **kwargs):
        if scan:
            acq_pars = {
                "scan_info": {
                    "scan_name": scan.description(),
                    "scan_values": scan.values_current_step,
                    "scan_readbacks": scan.readbacks_current_step,
                    "scan_step_info": {
                        "step_number": scan.next_step + 1,
                    },
                    "name": [adj.name for adj in scan.adjustables],
                    "expected_total_number_of_steps": scan.number_of_steps(),
                },
                "run_number": scan.daq_run_number,
                "user_tag": "usertag",
            }
            kwargs.update(acq_pars)
        kwargs['channels_JF'] = kwargs.get('channels_JF',self.channels["channels_JF"].get_current_value())
        kwargs['channels_BS'] = kwargs.get('channels_BS',self.channels["channels_BS"].get_current_value())
        kwargs['channels_BSCAM'] = kwargs.get('channels_BSCAM',self.channels["channels_BSCAM"].get_current_value())
        kwargs['channels_CA'] = kwargs.get('channels_CA',self.channels["channels_CA"].get_current_value())
        
        starttime_local = time.time()
        while self.pulse_id._pv.get_timevars() is None:
            time.sleep(0.02)
        while self.pulse_id._pv.get_timevars()["timestamp"] < starttime_local:
            time.sleep(0.02)
        start_id = self.pulse_id.get_current_value(use_monitor=False)
        acq_pars = {
            "label": label,
            "start_id": start_id,
        }
        acq_pars.update(kwargs)
        self.running.append(acq_pars)
        if scan:
            scan.daq_current_acquisition_index = self.running.index(acq_pars)
        return self.running.index(acq_pars)

    def stop(
        self,
        stop_id=None,
        acq_ix=None,
        label=None,
        wait=True,
        wait_cycle_sleep=0.01,
        scan=None, 
    ):
        if not stop_id:
            stop_id = int(self.pulse_id.get_current_value())
        
        if scan:
            acq_ix = scan.daq_current_acquisition_index
        if not acq_ix:
            acq_ix = -1

        acq_pars = self.running.pop(acq_ix)
        acq_pars["stop_id"] = stop_id
        label = acq_pars.pop("label")

        # if scan:
        #     tmp = scan.info()
        #     tmp['daq_pars'] = acq_pars
        #     scan.info()
        if wait:
            while int(self.pulse_id.get_current_value()) < stop_id:
                sleep(wait_cycle_sleep)

        response = self.retrieve(**acq_pars)
        # print(response)

        if scan and not scan.daq_run_number==int(response["run_number"]):
                raise Exception(
                    f"Run number mismatch: scan {scan.daq_run_number} != response {int(response['run_number'])}"
                )
        
        # correct file names to relative paths
        if scan:
            run_directory = list(
                Path(f"/sf/bernina/data/{self.pgroup}/raw").glob(f"run{scan.daq_run_number:04d}*")
            )[0].as_posix()
                
            response['files'] = [relpath(file, run_directory) for file in response['files']]
        
        return response
    
        # if scan:
        #     response = self.acquire_pulses(
        #         Npulses,
        #         # directory_relative=Path(file_name).parents[0],
        #         wait=True,
        #         channels_JF=self.channels["channels_JF"].get_current_value(),
        #         channels_BS=self.channels["channels_BS"].get_current_value(),
        #         channels_BSCAM=self.channels["channels_BSCAM"].get_current_value(),
        #         channels_CA=self.channels["channels_CA"].get_current_value(),
        #         **acq_pars,
        #     )
        #     acquisition.acquisition_kwargs.update({"file_names": response["files"]})
        #     if scan and not scan.daq_run_number==int(response["run_number"]):
        #         raise Exception(
        #             f"Run number mismatch: scan {scan.daq_run_number} != response {int(response['run_number'])}"
        #         )            

        #     for key, val in acquisition.acquisition_kwargs.items():
        #         acquisition.__dict__[key] = val




    def retrieve(
        self,
        *,
        start_id,
        stop_id,
        # directory_relative=None,
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
        # Here the receiver code: https://github.com/paulscherrerinstitute/sf_daq_broker/blob/master/sf_daq_broker/broker_manager.py
        if not pgroup:
            pgroup = self.pgroup
        if not pgroup:
            raise Exception("a pgroup needs to be defined")
        # if not directory_relative:
        #     directory_relative = ""
        # directory_relative = Path(directory_relative)
        # directory_base = Path(pgroup_base_path.format(pgroup)) / directory_relative
        files_extensions = []
        parameters = {"start_pulseid": start_id, "stop_pulseid": stop_id}
        parameters.update(kwargs)
        # print(parameters)
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
        # if directory_relative:
        #     parameters["directory_name"] = directory_relative.as_posix()

        parameters["pgroup"] = pgroup
        parameters["rate_multiplicator"] = self.rate_multiplicator
        # print("----- debug info ----->\n", parameters, "\n<----- debug info -----")
        self._last_server_post = f"{self.broker_address}/retrieve_from_buffers"
        self._last_server_post_parameters = parameters
        self._last_server_resp = requests.post(
                                f"{self.broker_address}/retrieve_from_buffers",
                                json=parameters,
                                timeout=self.timeout,
                            )

        response = validate_response(self._last_server_resp.json() )

        runno = response["run_number"]
        message = response["message"]       
        acquisition_number = response["acquisition_number"]
        unique_acquisition_number = response["unique_acquisition_number"]
        filenames = response["files"]
        # filenames = [
        #     (directory_base / Path(filename_format.format(runno)))
        #     .with_suffix(f".{ext}.h5")
        #     .as_posix()
        #     for ext in files_extensions
        # ]

        return response

    def get_next_run_number(self, pgroup=None):
        if pgroup is None:
            pgroup = self.pgroup
        res = requests.post(
            f"{self.broker_address}/advance_run_number",
            json={"pgroup": pgroup},
            timeout=self.timeout,
        )
        assert res.ok, f"Advancing and getting next run number failed {res.raise_for_status()}"
        return int(res.json()["run_number"])

    def get_last_run_number(self, pgroup=None):
        if pgroup is None:
            pgroup = self.pgroup
        res = requests.get(
            f"{self.broker_address}/get_current_run_number",
            json={"pgroup": pgroup},
            timeout=self.timeout,
        )
        assert res.ok, f"Getting last run number failed {res.raise_for_status()}"
        return int(res.json()["run_number"])

    def get_detector_frequency(self):
        return self._event_master.event_codes[
            self._detectors_event_code
        ].frequency.get_current_value()

    def get_JFs_available(self):
        return requests.get(f"{self.broker_address}/get_allowed_detectors").json()[
            "detectors"
        ]

    def get_JFs_running(self, return_full_response=False):
        res = requests.get(f"{self.broker_address}/get_running_detectors").json()
        if return_full_response:
            return res
        else:
            return res["running_detectors"]

    def power_on_JF(self, JF_channel):
        par = {"detector_name": JF_channel}
        return requests.post(
            f"{self.broker_address}/power_on_detector", json=par
        ).json()

    def take_pedestal(
        self, JF_list=None, pedestalmode=False, pgroup=None, verbose=False
    ):
        if pgroup is None:
            pgroup = self.pgroup
        if not JF_list:
            JF_list = self.get_JFs_running()
        parameters = {
            "pgroup": pgroup,
            "rate_multiplicator": 1,
            "detectors": {tJF: {} for tJF in JF_list},
            "pedestalmode": pedestalmode,
        }
        if verbose:
            print(self.broker_address)
            print(parameters)

        return requests.post(
            f"{self.broker_address}/take_pedestal", json=parameters
        ).json()

    def append_aux(self, *file_names, run_number=None, pgroup=None, check_group=True):
        if pgroup is None:
            pgroup = self.pgroup
        if run_number is None:
            run_number = self.get_last_run_number()
        if check_group:
            for file_name in file_names:
                if not Path(file_name).group() == pgroup:
                    shutil.chown(file_name, group=pgroup)

        return requests.post(
            self.broker_address_aux + "/copy_user_files",
            json={"pgroup": pgroup, "run_number": run_number, "files": file_names},
        )

    
    def pulse_picker_action_start_step(
            self,
            scan,
            do_pulse_picker_action=True,
            **kwargs,
            ):
                                       
        if not self.pulse_picker:
            return
        if not do_pulse_picker_action:
            return
        
        self.pulse_picker.open(verbose=False)
    
    def pulse_picker_action_end_step(
            self,
            scan,
            do_pulse_picker_action=True,
            **kwargs,
            ):
        if not self.pulse_picker:
            return
        if not do_pulse_picker_action:
            return
        
        self.pulse_picker.close(verbose=False)
    
    
    def check_counters_for_scan(
        self, scan, channels_to_check=["channels_BSCAM", "channels_JF"], channels_check_timeout=3, **kwargs
    ):
        if not set(self.channels.keys()).intersection(set(channels_to_check)):
            return
        print("FYI, selected channels are")
        for nam, chs in self.channels.items():
            if nam in channels_to_check:
                print(f"{nam}  :  {chs.get_current_value()}")
        try:
            o = inputimeout.inputimeout(
                prompt=f"Press Ctrl-c to abort, Return to continue, or wait {channels_check_timeout} seconds",
                timeout=channels_check_timeout,
            )
        except inputimeout.TimeoutOccurred:
            print("... timed out, continuing with selection.")
        except KeyboardInterrupt:
            raise Exception("User-requested cancelling!")
        else:
            if o == "c":
                raise Exception("User-requested cancelling!")
            
    def count_run_number_up_and_attach_to_scan(self,scan,**kwargs):
        """
        Increments the run number by one.
        """
        runno = self.get_next_run_number(self.pgroup)
        print(f"Run number incremented to {runno}")
        scan.daq_run_number = runno


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

    def init_namespace(self,scan=None, init_required_namespace_components_only=True, append_status_info=True):
        if append_status_info:
            self.namespace.init_all(silent=False, required_only=init_required_namespace_components_only)

    def append_start_status_to_scan(self,scan=None, append_status_info=True):
        if not append_status_info:
            return
        namespace_status = self.namespace.get_status(base=None)
        stat = {"status_run_start": namespace_status}
        scan.namespace_status = stat

    def _create_runtable_metadata_append_status_to_runtable(
            self,
            scan,
            append_status_info=True):

        print("run_table appending run")
        runno = scan.daq_run_number
        metadata = {
            "type": "scan",
            "name": scan.description.get_current_value(),
            "scan_info_file": '',
        }
        for n, adj in enumerate(scan.adjustables):
            nname = None
            adj_pvname = None
            if hasattr(adj, "Id"):
                adj_pvname = adj.Id
            if hasattr(adj, "name"):
                nname = adj.name

            metadata.update(
                {
                    f"scan_dim_{n}": nname,
                    f"from_dim_{n}": scan.values_todo.get_current_value()[0][n],
                    f"to_dim_{n}": scan.values_todo.get_current_value()[-1][n],
                    f"pvname_dim_{n}": adj_pvname,
                }
            )
        if np.mean(np.diff(scan.pulses_per_step)) < 1:
            pulses_per_step = scan.pulses_per_step[0]
        else:
            pulses_per_step = scan.pulses_per_step
        metadata.update(
            {
                "steps": len(scan.values_todo.get_current_value()),
                "pulses_per_step": pulses_per_step,
                "counters": scan.counters_names.get_current_value(),
                "scan_command": scan.scan_command.get_current_value(),
            }
        )
        t_start_rt = time.time()
        try:
            self.run_table.append_run(runno, metadata=metadata, d=scan.namespace_status["status_run_start"]["status"])
        except:
            print("WARNING: issue adding data to run table")
        print(f"Runtable appending took: {time.time()-t_start_rt:.3f} s")


    def copy_scan_info_to_raw(self,scan, **kwargs):
        t_start = time.time()

        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number
        else:
            runno = self.get_last_run_number()

        # get data that should come later from api or similar.
        # run_directory = list(
        #     Path(f"/sf/bernina/data/{self.pgroup}/raw").glob(f"run{runno:04d}*")
        # )[0].as_posix()
                
        # Get scan info from scan
        si = scan.scan_info
        # save temprary file and send then to raw
        
        pgroup = self.pgroup
        tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/run_data/daq/run{runno:04d}/aux")
        tmpdir.mkdir(exist_ok=True, parents=True)
        try:
            tmpdir.chmod(0o775)
        except:
            pass
        scaninfofile = tmpdir / Path("scan_info_rel.json")
        if not Path(scaninfofile).exists():
            with open(scaninfofile, "w") as f:
                json.dump(si, f, sort_keys=True, cls=NumpyEncoder, indent=4)
        else:
            with open(scaninfofile, "r+") as f:
                f.seek(0)
                json.dump(si, f, sort_keys=True, cls=NumpyEncoder, indent=4)
                f.truncate()
        if not scaninfofile.group() == scaninfofile.parent.group():
            shutil.chown(scaninfofile, group=scaninfofile.parent.group())
        # print(f"Copying info file to run {runno} to the raw directory of {pgroup}.")

        scan.remaining_tasks.append(
            Thread(
                target=self.append_aux,
                args=[scaninfofile.as_posix()],
                kwargs=dict(pgroup=pgroup, run_number=runno),
            )
        )
        # DEBUG
        # print(
        #     f"Sending scan_info_rel.json in {Path(scaninfofile).parent.stem} to run number {runno}."
        # )
        scan.remaining_tasks[-1].start()
        # response = daq.append_aux(scaninfofile.as_posix(), pgroup=pgroup, run_number=runno)
        # print(f"Status: {response.json()['status']} Message: {response.json()['message']}")
        # print(
        #     f"--> creating and copying file took{time.time()-t_start} s, presently adding to deadtime."
        # )


    def append_status_to_scan_and_store(self,
        scan, append_status_info=True, **kwargs
    ):
        if not append_status_info:
            return
        
        if not len(scan.values_done())>0:
            return
        
        namespace_status = self.namespace.get_status(base=None)
        scan.namespace_status["status_run_end"] = namespace_status
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number
        else:
            runno = self.get_last_run_number()
        
        pgroup = self.pgroup
        tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/run_data/daq/run{runno:04d}/aux")
        tmpdir.mkdir(exist_ok=True, parents=True)
        try:
            tmpdir.chmod(0o775)
        except:
            pass

        statusfile = tmpdir / Path("status.json")
        if not statusfile.exists():
            with open(statusfile, "w") as f:
                json.dump(scan.namespace_status, f, sort_keys=True, cls=NumpyEncoder, indent=4)
        else:
            with open(statusfile, "r+") as f:
                f.seek(0)
                json.dump(scan.namespace_status, f, sort_keys=True, cls=NumpyEncoder, indent=4)
                f.truncate()
                print("Wrote status with seek truncate!")
        if not statusfile.group() == statusfile.parent.group():
            shutil.chown(statusfile, group=statusfile.parent.group())

        response = self.append_aux(
            statusfile.resolve().as_posix(),
            pgroup=pgroup,
            run_number=runno,
        )
        # print("####### transfer status #######")
        # print(response.json())
        # print("###############################")
        scan.scan_info["scan_parameters"]["status"] = "aux/status.json"

   
    def check_checker_before_step(self,scan, **kwargs):
        # self.
        if self.checker:
            first_check = time.time()
            checker_unhappy = False
            print('')
            while not self.checker.check_now():
                print(
                    colorama.Fore.RED
                    + f"Condition checker is not happy, waiting for OK conditions since {time.time()-first_check:5.1f} seconds."
                    + colorama.Fore.RESET,
                    # end="\r",
                )
                sleep(1)
                
                checker_unhappy = True
            if checker_unhappy:
                print(
                    colorama.Fore.RED
                    + f"Condition checker was not happy and waiting for {time.time()-first_check:5.1f} seconds."
                    + colorama.Fore.RESET
                )
            self.checker.clear_and_start_counting()
            
    def check_checker_after_step(self,scan, **kwargs):
        if self.checker:
            if not self.checker.stop_and_analyze():
                scan._current_step_ok = False

    
    def copy_aliases_to_scan(self, scan, send_aliases_now=False, **kwargs):
        if send_aliases_now or (len(scan.values_done()) == 1):
            namespace_aliases = self.namespace.alias.get_all()
            if hasattr(scan, "daq_run_number"):
                runno = scan.daq_run_number
            else:
                runno = self.daq.get_last_run_number()
            pgroup = self.pgroup
            tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/run_data/daq/run{runno:04d}/aux")
            tmpdir.mkdir(exist_ok=True, parents=True)
            try:
                tmpdir.chmod(0o775)
            except:
                pass
            aliasfile = tmpdir / Path("aliases.json")
            if not Path(aliasfile).exists():
                with open(aliasfile, "w") as f:
                    json.dump(
                        namespace_aliases, f, sort_keys=True, cls=NumpyEncoder, indent=4
                    )
            else:
                with open(aliasfile, "r+") as f:
                    f.seek(0)
                    json.dump(
                        namespace_aliases, f, sort_keys=True, cls=NumpyEncoder, indent=4
                    )
                    f.truncate()
            if not aliasfile.group() == aliasfile.parent.group():
                shutil.chown(aliasfile, group=aliasfile.parent.group())

            scan.remaining_tasks.append(
                Thread(
                    target=self.append_aux,
                    args=[aliasfile.resolve().as_posix()],
                    kwargs=dict(pgroup=pgroup, run_number=runno),
                )
            )
            # DEBUG
            # print(
            #     f"Sending scan_info_rel.json in {Path(aliasfile).parent.stem} to run number {runno}."
            # )
            scan.remaining_tasks[-1].start()
            # response = daq.append_aux(
            #     aliasfile.resolve().as_posix(),
            #     pgroup=pgroup,
            #     run_number=runno,
            # )
            # print("####### transfer aliases started #######")
            # print(response.json())
            # print("################################")
            scan.scan_info["scan_parameters"]["aliases"] = "aux/aliases.json"
    
    def scan_message_to_elog(self, scan=None, **kwargs):
        # def _create_metadata_structure_start_scan(
    # scan, run_table=run_table, elog=elog, append_status_info=True, **kwargs
        # ):
        runno = scan.daq_run_number
        message_string = f"#### DAQ run {runno}"
        if scan.description():
            message_string += f': {scan.description()}\n'
        else:
            message_string += f'\n'
        try:
            elog_ids = scan.status_to_elog(message_string,auto_title=False)
            scan._elog_id = elog_ids[1]
        
        # message_string += "`" + metadata["scan_info_file"] + "`\n"
        # try:
        #     elog_ids = self.elog.post(
        #         message_string,
        #         Title=f'Run {runno}: {scan.description()}',
        #         text_encoding="markdown",
        #     )
            
    #     metadata.update({"elog_message_id": scan._elog_id})
    #     metadata.update(
    #         {"elog_post_link": scan._elog.elogs[1]._log._url + str(scan._elog_id)}
    #     )
        except:
            print("Elog posting failed with:")
            traceback.print_exc()

    def append_scan_monitors(
            self,
            scan,
            custom_monitors={},
            **kwargs,
        ):
        scan.daq_monitors = {}
        for adj in scan.adjustables:
            try:
                tname = adj.alias.get_full_name()
            except Exception:
                tname = adj.name
                traceback.print_exc()
            try:
                scan.daq_monitors[tname] = Monitor(adj.pvname)
            except Exception:
                print(f"Could not add CA monitor for {tname}")
                # traceback.print_exc()
            try:
                rname = adj.readback.alias.get_full_name()
            except Exception:
                print("no readback configured")
                # traceback.print_exc()
            try:
                scan.daq_monitors[rname] = Monitor(adj.readback.pvname)
            except Exception:
                print(f"Could not add CA readback monitor for {tname}")
                traceback.print_exc()

        for tname, tobj in custom_monitors.items():
            try:
                if type(tobj) is str:
                    tmonpv = tobj
                scan.daq_monitors[tname] = Monitor(tmonpv)
                print(f"Added custom monitor for {tname}")
            except Exception:
                print(f"Could not add custom monitor for {tname}")
                traceback.print_exc()
        try:
            tname = self.pulse_id.alias.get_full_name()
            scan.daq_monitors[tname] = Monitor(self.pulse_id.pvname)
        except Exception:
            print(f"Could not add daq.pulse_id monitor")
            traceback.print_exc()


    def end_scan_monitors(self, scan, **kwargs):
        for tmon in scan.daq_monitors:
            scan.daq_monitors[tmon].stop_callback()

        monitor_result = {tmon: scan.daq_monitors[tmon].data for tmon in scan.daq_monitors}

        # save temprary file and send then to raw
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number
        else:
            runno = self.get_last_run_number()
        
        tmpdir = Path(f"/sf/bernina/data/{self.pgroup}/res/run_data/daq/run{runno}/aux")
        tmpdir.mkdir(exist_ok=True, parents=True)
        try:
            tmpdir.chmod(0o775)
        except:
            pass
        scanmonitorfile = tmpdir / Path("scan_monitor.pkl")
        if not Path(scanmonitorfile).exists():
            with open(scanmonitorfile, "wb") as f:
                pickle.dump(monitor_result, f)

        print(f"Copying monitor file to run {runno} to the raw directory of {self.pgroup}.")
        response = self.append_aux(
            scanmonitorfile.as_posix(), pgroup=self.pgroup, run_number=runno
        )
        print(f"Status: {response.json()['status']} Message: {response.json()['message']}")


    def get_callback_keywords(self):
        kws_all = set([])
        for cb in self.callbacks_start_scan:
            kws = foo_get_kwargs(cb)
            
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_start_step:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_step_counting:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_end_step:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        for cb in self.callbacks_end_scan:
            kws = foo_get_kwargs(cb)
            if kws:
                kws_all.update(set(kws))
                print(cb.__name__, "has keywords:", kws)
        return kws_all

    # scan.monitors = None

    # def run_table_stuff(self):        
    #     #for run table
    #     metadata = {
    #         "type": "scan",
    #         "name": scan.description,
    #         "scan_info_file": scan.scan_info_filename,
    #     }

    #     for n, adj in enumerate(scan.adjustables):
    #         nname = None
    #         nId = None
    #         if hasattr(adj, "Id"):
    #             nId = adj.Id
    #         if hasattr(adj, "name"):
    #             nname = adj.name

    #         metadata.update(
    #             {
    #                 f"scan_motor_{n}": nname,
    #                 f"from_motor_{n}": scan.values_todo[0][n],
    #                 f"to_motor_{n}": scan.values_todo[-1][n],
    #                 f"id_motor_{n}": nId,
    #             }
    #         )
    #     if np.mean(np.diff(scan.pulses_per_step)) < 1:
    #         pulses_per_step = scan.pulses_per_step[0]
    #     else:
    #         pulses_per_step = scan.pulses_per_step
    #     metadata.update(
    #         {
    #             "steps": len(scan.values_todo),
    #             "pulses_per_step": pulses_per_step,
    #             "counters": [daq.name for daq in scan.counterCallers],
    #         }
    #     )

    # try:
    #     try:
    #         metadata.update({"scan_command": get_ipython().user_ns["In"][-1]})
    #     except:
    #         print("Count not retrieve ipython scan command!")

    #     message_string = f"#### Run {runno}"
    #     if metadata["name"]:
    #         message_string += f': {metadata["name"]}\n'
    #     else:
    #         message_string += "\n"

    #     if "scan_command" in metadata.keys():
    #         message_string += "`" + metadata["scan_command"] + "`\n"
    #     message_string += "`" + metadata["scan_info_file"] + "`\n"
    #     elog_ids = elog.post(
    #         message_string,
    #         Title=f'Run {runno}: {metadata["name"]}',
    #         text_encoding="markdown",
    #     )
    #     scan._elog_id = elog_ids[1]
    #     metadata.update({"elog_message_id": scan._elog_id})
    #     metadata.update(
    #         {"elog_post_link": scan._elog.elogs[1]._log._url + str(scan._elog_id)}
    #     )
    # except:
    #     print("Elog posting failed with:")
    #     traceback.print_exc()
    # if not append_status_info:
    #     return
    # d = {}
    # ## use values from status for run_table
    # try:
    #     d = scan.status["status_run_start"]["status"]
    # except:
    #     print("Tranferring values from status to run_table did not work")
    # t_start_rt = time.time()
    # try:
    #     run_table.append_run(runno, metadata=metadata, d=d)
    # except:
    #     print("WARNING: issue adding data to run table")
    # print(f"RT appending: {time.time()-t_start_rt:.3f} s")







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




#### >>> TODO implment >>>



#### <<< TODO  implment <<<
