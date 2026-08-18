import json
import pickle
import shutil
from threading import Thread, Lock, Event, Timer
import time
import traceback
import colorama
import numpy as np
import requests
from pathlib import Path
from time import sleep

from eco.elements.detector import DetectorMemory
from eco.utilities import NumpyEncoder
from eco.elements.protocols import Adjustable
from eco.utilities.utilities import foo_get_kwargs
from ..epics.detector import DetectorPvDataStream
from ..epics.utilities_epics import Monitor
from epics import PV
from ..acquisition.utilities import Acquisition
from ..elements.assembly import Assembly
from ..utilities.path_alias import PathAlias

# from ..acquisition.decorators import scannable
import inputimeout
from IPython import get_ipython
from os.path import relpath


class Daq(Assembly):
    """
    Client for the sf-daq broker REST API
    (https://gitea.psi.ch/sf-daq/sf_daq_broker).

    Two broker addresses are used here:
      - ``broker_address`` (default port 10002): sf_daq_broker's "fast"
        broker (``DEFAULT_BROKER_REST_PORT``). Endpoints used from here:
        retrieve_from_buffers, take_pedestal, get_allowed_detectors,
        get_running_detectors, power_on_detector, advance_run_number,
        get_current_run_number, get_pvlist, set_pvlist. (close_pgroup_writing
        also lives here but is not wrapped by this class.)
      - ``broker_address_aux`` (default port 10003): despite the "aux" name
        used in eco, this is what sf_daq_broker itself calls the *slow*
        broker (``DEFAULT_BROKER_SLOW_REST_PORT``, served by
        ``broker_slow.py``). It serves copy_user_files as well as
        detector/DAP settings and hardware-diagnostics endpoints
        (get/set_detector_settings, get/set_dap_settings, get_detector_status,
        get_detector_pings, get_detector_temperatures, get_jfctrl_monitor,
        get_jfstats). The get/set_dap_settings and get/set_detector_settings
        wrappers live on :class:`eco.detector.jungfrau.Jungfrau` instead of
        here, since they are per-detector, not per-Daq.

    Request pacing (why the DAQ "feels" like it can only take requests at
    low frequency): both broker processes are started with
    ``bottle.run(app=app, host=hostname, port=rest_port)`` -- no ``server=``
    argument, i.e. bottle's default *wsgiref* server. wsgiref is
    single-threaded and fully synchronous: only one HTTP request is handled
    at a time *per broker process*, regardless of which endpoint it hits.
    retrieve_from_buffers and take_pedestal are themselves cheap and
    asynchronous on the server side (they publish a message to RabbitMQ and
    return immediately; the actual, potentially long-running, write/convert
    job happens afterwards in a separate writer process) -- but a slow
    synchronous call to the *same* broker process (e.g. get_detector_pings,
    which pings detector module hosts over the network and can take seconds
    per unreachable module) blocks every other client's request to that
    broker for its entire duration. This -- not individual jobs being slow
    -- is almost certainly the real limitation: the REST front-end has no
    concurrency. Concretely:
      - do not poll diagnostics/settings endpoints (get_dap_settings,
        get_detector_settings, get_detector_status/pings/temperatures,
        get_jfctrl_monitor, get_jfstats) in a tight loop or once per scan
        step; ``Jungfrau.get_dap_settings`` already self-throttles to 5 s
        between real network calls for exactly this reason, and the new
        ``Jungfrau.get_detector_settings`` added alongside it does the same.
      - :meth:`append_aux` (``copy_user_files``) fires once per scan step
        (see ``copy_scan_info_to_raw``) against ``broker_address_aux`` --
        the same process that also serves settings/diagnostics -- so heavy
        use of those during an active scan adds latency to that per-step
        upload too.
      - the default ``timeout`` on this class applies per HTTP call; under
        broker congestion a call can simply be queued behind another
        request rather than the server being down, so a timeout is not on
        its own reliable evidence of an actual outage (see
        :meth:`check_alive`).
    """

    def __init__(
        self,
        broker_address="http://sf-daq:10002",
        broker_address_aux="http://sf-daq:10003",
        timeout=2,
        # timeout=10,
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
        elog=None,
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
            # Dedicated, permanently-monitored PV used only to wait for a fresh
            # pulse_id in start() without issuing competing CA get requests.
            # Kept separate from self.pulse_id._pv (auto_monitor=False) so that
            # get_current_value() elsewhere is unaffected; both share the same
            # underlying CA channel, so this costs nothing extra on the wire.
            self._pulse_id_latest = {"value": None, "timestamp": None}
            self._pulse_id_latest_lock = Lock()
            self._pulse_id_updated = Event()

            def _on_pulse_id_update(value=None, timestamp=None, **kwargs):
                with self._pulse_id_latest_lock:
                    self._pulse_id_latest["value"] = value
                    self._pulse_id_latest["timestamp"] = timestamp
                self._pulse_id_updated.set()

            self._pulse_id_monitor_pv = PV(pulse_id_adj, auto_monitor=True)
            self._pulse_id_monitor_pv.add_callback(_on_pulse_id_update)
        else:
            self.pulse_id = pulse_id_adj
        self.running = []
        self._event_master = event_master
        self._detectors_event_code = detectors_event_code
        self.name = name
        self.namespace = namespace
        self.checker = checker
        self.run_table = run_table
        self.pulse_picker = pulse_picker
        self._default_file_path = None
        if not rate_multiplicator == "auto":
            print(
                "warning: rate multiplicator automatically determined from event_master!"
            )
        self.callbacks_start_scan = [
            self.check_counters_for_scan,
            self.init_namespace,
            self.count_run_number_up_and_attach_to_scan,
            self.append_start_status_to_scan,
            self.scan_message_to_elog,
            self._create_runtable_metadata_append_status_to_runtable,
            self.append_scan_monitors,
        ]
        self.callbacks_start_step = [
            self.copy_aliases_to_scan,
            self.check_checker_before_step,
            self.pulse_picker_action_start_step,
        ]
        self.callbacks_step_counting = []
        self.callbacks_end_step = [
            self.pulse_picker_action_end_step,
            self.copy_scan_info_to_raw,
            self.check_checker_after_step,
        ]
        self.callbacks_end_scan = [
            self.append_status_to_scan_and_store,
            self.copy_scan_info_to_raw,
            self.end_scan_monitors,
        ]
        self.elog = elog

        # Trailing-edge debounce state for append_aux calls that would
        # otherwise fire once per scan step (see copy_scan_info_to_raw and
        # _debounced_append_aux): keyed pending threading.Timer per debounce
        # key, plus a lock guarding read-cancel-replace of that dict.
        self._debounce_timers = {}
        self._debounce_lock = Lock()
        self._scan_info_debounce_wait = 2.0

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

    def acquire(
        self,
        scan=None,
        run_number=None,
        Npulses=100,
        acq_pars={},
        pgroup=None,
        **kwargs,
    ):
        if pgroup is None:
            pgroup = self.pgroup

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
                "run_number": scan.daq_run_number.get_current_value(),
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
                pgroup=pgroup,
                **acq_pars,
            )
            acquisition.acquisition_kwargs.update({"file_names": response["files"]})
            if scan and not scan.daq_run_number.get_current_value() == int(
                response["run_number"]
            ):
                raise Exception(
                    f"Run number mismatch: scan {scan.daq_run_number.get_current_value()} != response {int(response['·run_number'])}"
                )

            for key, val in acquisition.acquisition_kwargs.items():
                acquisition.__dict__[key] = val

        acquisition.set_acquire_foo(acquire, hold=False)

        return acquisition

    def acquire_pulses(self, Npulses, label=None, wait=True, pgroup=None, **kwargs):
        if pgroup is None:
            pgroup = self.pgroup
        ix = self.start(label=label, **kwargs)
        return self.stop(
            stop_id=self.running[ix]["start_id"] + Npulses - 1,
            acq_ix=ix,
            wait=wait,
            pgroup=pgroup,
        )

    def start(self, label=None, scan=None, **kwargs):
        """
        Mark the current pulse_id as the start of an acquisition; the
        matching :meth:`stop` later sends the actual retrieve_from_buffers
        request. Any extra keyword, e.g. ``selected_pulse_ids=[...]``
        (see :meth:`retrieve`), is stored verbatim and forwarded through
        :meth:`stop` to :meth:`retrieve` unchanged.
        """
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
                "run_number": scan.daq_run_number.get_current_value(),
                "user_tag": "usertag",
            }
            kwargs.update(acq_pars)
        kwargs["channels_JF"] = kwargs.get(
            "channels_JF", self.channels["channels_JF"].get_current_value()
        )
        kwargs["channels_BS"] = kwargs.get(
            "channels_BS", self.channels["channels_BS"].get_current_value()
        )
        kwargs["channels_BSCAM"] = kwargs.get(
            "channels_BSCAM", self.channels["channels_BSCAM"].get_current_value()
        )
        kwargs["channels_CA"] = kwargs.get(
            "channels_CA", self.channels["channels_CA"].get_current_value()
        )

        starttime_local = time.time()
        start_id = None
        if hasattr(self, "_pulse_id_updated"):
            # Wait on the dedicated pulse_id monitor's cache instead of issuing
            # explicit CA get requests, to avoid adding CA traffic that competes
            # with itself/other concurrent CA activity right at scan start.
            while True:
                with self._pulse_id_latest_lock:
                    ts = self._pulse_id_latest["timestamp"]
                    val = self._pulse_id_latest["value"]
                if ts is not None and val is not None and ts >= starttime_local:
                    start_id = int(val)
                    break
                remaining = self.timeout - (time.time() - starttime_local)
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timeout {self.timeout} s hit while waiting for a valid, "
                        f"up-to-date pulse_id. last timestamp: {ts}; "
                        f"starttime of scan step: {starttime_local}"
                    )
                self._pulse_id_updated.wait(timeout=min(remaining, 0.25))
                self._pulse_id_updated.clear()
        else:
            # Fallback for a pulse_id_adj configured as a pre-built object rather
            # than a PV name string: no dedicated monitor is set up in that case,
            # so fall back to an explicit polling wait.
            pv = self.pulse_id._pv
            tvars = None
            poll_interval = 0.02
            max_poll_interval = 0.25
            per_call_timeout = 1.0  # headroom for a saturated CA processing thread
            while True:
                tvars = pv.get_timevars(timeout=per_call_timeout)
                if tvars is not None and tvars["timestamp"] >= starttime_local:
                    start_id = pv.get(use_monitor=False, timeout=per_call_timeout)
                    if start_id is not None:
                        break
                if time.time() - starttime_local > self.timeout:
                    raise TimeoutError(
                        f"Timeout {self.timeout} s hit while waiting for a valid, up-to-date "
                        f"pulse_id. timevars: {tvars}; start_id: {start_id}; "
                        f"starttime of scan step: {starttime_local}"
                    )
                time.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.5, max_poll_interval)

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
        pgroup=None,
    ):
        if pgroup is None:
            pgroup = self.pgroup
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

        acq_pars["pgroup"] = pgroup
        response = self.retrieve(**acq_pars)
        # print(response)

        if scan and not scan.daq_run_number.get_current_value() == int(
            response["run_number"]
        ):
            raise Exception(
                f"Run number mismatch: scan {scan.daq_run_number.get_current_value()} != response {int(response['run_number'])}"
            )

        # correct file names to relative paths
        if scan:
            run_directory = list(
                Path(f"/sf/bernina/data/{pgroup}/raw").glob(
                    f"run{scan.daq_run_number.get_current_value():04d}*"
                )
            )[0].as_posix()

            response["files"] = [
                relpath(file, run_directory) for file in response["files"]
            ]

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
        selected_pulse_ids=None,
        **kwargs,
    ):
        """
        POST {broker_address}/retrieve_from_buffers -- request that data for
        pulse ids [start_id, stop_id] be written to a run for pgroup.

        Async on the server: this only publishes the write request to
        RabbitMQ and returns predicted file paths/run_number immediately, it
        does *not* wait for the writer process to actually finish producing
        the files (that happens afterwards, out of band). This client only
        waits for the *source* pulse_id counter to reach stop_id
        (see :meth:`stop`), not for the write job itself to complete.

        Hard server-side limit: sf_daq_broker's validate.py enforces
        ``stop_id - start_id <= MAX_PULSEID_DELTA`` with
        ``MAX_PULSEID_DELTA = 60001``; a larger span is rejected outright
        rather than throttled.

        Parameters
        ----------
        selected_pulse_ids : list[int], optional
            Restrict channels_JF (detector) output to only these pulse ids,
            instead of every pulse in [start_id, stop_id]. Maps to the
            broker's top-level "selected_pulse_ids" request field
            (sf_daq_broker/broker_manager.py), which the broker copies
            verbatim into the per-detector write request; the writer then
            outputs "only pulse IDs present in the list within the
            specified by start_/stop_pulseid region" (broker_rest_api.md).
            Limitations, straight from the broker source:
              - only channels_JF is filtered this way. channels_BS,
                channels_CA and channels_BSCAM are still written for the
                *entire* contiguous [start_id, stop_id] range regardless of
                this argument -- there is no non-consecutive selection for
                those.
              - every id must still lie within [start_id, stop_id], and the
                MAX_PULSEID_DELTA cap above still applies to that bracketing
                range -- this filters pulses out of a range, it is not a way
                to request a sparse set of pulses spanning an unbounded
                span.
        """
        if selected_pulse_ids is not None:
            selected_pulse_ids = sorted(int(p) for p in selected_pulse_ids)
            if selected_pulse_ids and (
                selected_pulse_ids[0] < start_id or selected_pulse_ids[-1] > stop_id
            ):
                raise ValueError(
                    f"selected_pulse_ids must all lie within "
                    f"[{start_id}, {stop_id}]; got range "
                    f"[{selected_pulse_ids[0]}, {selected_pulse_ids[-1]}]"
                )
            kwargs["selected_pulse_ids"] = selected_pulse_ids
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

        response = validate_response(self._last_server_resp.json())

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
        """
        POST {broker_address}/advance_run_number -- increment and return the
        next run number for pgroup.

        GET/POST discrepancy: broker_rest_api.md documents this endpoint as
        GET, but sf_daq_broker/broker.py registers "advance_run_number" in
        its POST endpoint list (ENDPOINTS_POST), so POST is what the server
        actually routes -- the docs, not this client, look wrong. Left as
        POST deliberately; if this call ever starts failing with a routing
        error, check whether the server-side registration changed before
        "fixing" this to GET. (get_current_run_number below is GET both in
        the docs and in ENDPOINTS_GET, and is implemented as GET here --
        consistent.)
        """
        if pgroup is None:
            pgroup = self.pgroup
        res = requests.post(
            f"{self.broker_address}/advance_run_number",
            json={"pgroup": pgroup},
            timeout=self.timeout,
        )
        assert (
            res.ok
        ), f"Advancing and getting next run number failed {res.raise_for_status()}"
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

    def get_pvlist(self):
        """
        List the EPICS (CA) channels the sf-daq epics buffer is currently
        recording for this beamline.

        REST: GET {broker_address}/get_pvlist, no parameters. Reads the
        server-side config file
        ``/home/svcusr-sfdaq/service_configs/sf.{beamline}.epics_buffer.json``
        directly -- cheap, but still shares the single-threaded broker
        process with retrieve_from_buffers etc. (see class docstring), so
        avoid polling it in a tight loop.

        Returns
        -------
        list[str]
            PV names currently recorded to the EPICS buffer.
        """
        res = requests.get(f"{self.broker_address}/get_pvlist", timeout=self.timeout)
        assert res.ok, f"Getting PV list failed {res.raise_for_status()}"
        return res.json()["pv_list"]

    def set_pvlist(self, pv_list):
        """
        Replace the list of EPICS (CA) channels the sf-daq epics buffer
        records for this beamline.

        REST: POST {broker_address}/set_pvlist, body {"pv_list": [...]}.
        This is a **global, beamline-wide** change, not scoped to a single
        scan/run -- it rewrites ``sf.{beamline}.epics_buffer.json`` on the
        server (keeping a timestamped backup alongside it) and thereby
        changes what *every* future acquisition on the beamline records,
        until changed again. The broker deduplicates the list server-side
        (``list(dict.fromkeys(pv_list))``, order-preserving) but does not
        otherwise validate the PV names. It is not documented, and not
        verified here, whether the running epics buffer writer picks the
        new file up live or needs a restart -- treat a change as
        best-effort/eventual, not immediate.

        Length/size limit: there is **no hard, documented limit** on how
        many PVs can be set in one call. sf_daq_broker's validate.py has no
        explicit check on pv_list length or total size (unlike e.g. the
        pulse-id range, which is capped at MAX_PULSEID_DELTA=60001 in the
        same file). The broker is served by bottle's default wsgiref server
        with no ``MEMFILE_MAX`` override, so an oversized JSON body is not
        rejected either -- bottle just buffers request bodies above its
        100 kB default from memory to a temp file rather than refusing them.
        In practice the limiting factor is not this endpoint but the live
        CA client inside the epics buffer writer having to keep up with
        every PV in the list -- keep the list to what's actually needed
        rather than relying on the absence of a server-side cap.

        Parameters
        ----------
        pv_list : list[str]
            EPICS PV names to record.
        """
        res = requests.post(
            f"{self.broker_address}/set_pvlist",
            json={"pv_list": list(pv_list)},
            timeout=self.timeout,
        )
        assert res.ok, f"Setting PV list failed {res.raise_for_status()}"
        return res.json()["pv_list"]

    def check_alive(self, timeout=None):
        """
        Best-effort health/reachability check for the fast broker
        (broker_address).

        sf_daq_broker's REST API has no dedicated health/ping endpoint
        (checked broker_rest_api.md and the endpoint lists registered in
        sf_daq_broker/broker.py and broker_slow.py -- neither defines one).
        This uses GET /get_allowed_detectors as a cheap stand-in: it only
        reads static beamline config server-side, so an exception here is a
        reasonable proxy for "the fast broker process is not answering",
        while a slow-but-successful response is a proxy for "it's alive but
        the single-threaded request queue (see class docstring) is backed
        up" rather than down.

        Per-detector hardware diagnostics live on the *slow* broker and are
        wrapped on :class:`eco.detector.jungfrau.Jungfrau` instead (they
        take a detector_name, so they don't fit this class): get_status()
        (delay/exptime/gain_mode/detector_mode), get_pings() (per-module
        network reachability), get_temperatures(), and get_stats()
        (get_jfstats: bundles a "was the detector buffer file written to in
        the last 30 s" flag with temperatures and jfctrl status -- the
        closest thing sf-daq exposes to a single "is this detector actually
        alive and recording right now" check).

        Parameters
        ----------
        timeout : float, optional
            Overrides self.timeout for this call only. Consider passing
            something more generous than self.timeout here: per the class
            docstring, a slow response under broker congestion is not the
            same as the broker being down, and self.timeout is tuned tight
            for scan-critical calls, not diagnostics.

        Returns
        -------
        dict
            {"alive": bool, "latency_s": float, "error": str or None}
        """
        t0 = time.time()
        try:
            res = requests.get(
                f"{self.broker_address}/get_allowed_detectors",
                timeout=timeout or self.timeout,
            )
            res.raise_for_status()
            return {"alive": True, "latency_s": time.time() - t0, "error": None}
        except Exception as e:
            return {"alive": False, "latency_s": time.time() - t0, "error": str(e)}

    def power_on_JF(self, JF_channel):
        par = {"detector_name": JF_channel}
        return requests.post(
            f"{self.broker_address}/power_on_detector", json=par
        ).json()

    def take_pedestal(
        self, JF_list=None, pedestalmode=False, pgroup=None, verbose=False
    ):
        """
        POST {broker_address}/take_pedestal for JF_list (default: currently
        running detectors).

        Async on the server, like retrieve_from_buffers: this call publishes
        a request to RabbitMQ and returns immediately, it does not wait for
        the dark run to actually be taken. The broker's own response message
        states how long to wait: PEDESTAL_FRAMES / 100 * rate_multiplicator
        + 10 seconds, with PEDESTAL_FRAMES hardcoded to 3000 in
        sf_daq_broker/broker_manager.py -- i.e. ~40 s for the
        rate_multiplicator=1 used here. Do not call this (or anything else
        against broker_address) again before that estimated duration has
        passed: per the class docstring the broker handles one request at a
        time process-wide, so calling again while a pedestal job is in
        flight only adds queuing delay for both calls, it does not make the
        pedestal finish sooner.
        """
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

    def _debounced_append_aux(self, key, wait, *file_names, **aux_kwargs):
        """
        Trailing-edge debounce around :meth:`append_aux`.

        Calls sharing the same ``key`` that arrive within ``wait`` seconds
        of each other are coalesced: each new call cancels the previously
        scheduled upload for that key and reschedules it ``wait`` seconds
        out, so only the *last* call in a burst actually reaches the
        network -- fired once the calls for that key go quiet for ``wait``
        seconds, using whatever ``file_names``/``aux_kwargs`` that last call
        supplied. If calls keep arriving faster than ``wait``, the upload
        keeps getting pushed out and only ever fires after the burst ends
        (there is no periodic/every-Nth-call fallback) -- fine here because
        the thing being uploaded (e.g. scan_info_rel.json) is overwritten
        in place on local disk synchronously before this is scheduled, so
        by the time the deferred call fires it always ships the current
        on-disk content regardless of which call triggered it.

        Exists because :meth:`append_aux` posts to broker_address_aux,
        sf_daq_broker's single-threaded slow broker (see class docstring),
        and copy_scan_info_to_raw calls append_aux once per scan step --
        on a fast scan that alone is enough traffic to noticeably back up
        the broker's one-request-at-a-time queue.

        The scheduling Timer is a daemon thread and is not joined anywhere
        (matching the pre-existing plain Thread this replaces, which was
        never joined via scan.remaining_tasks either -- see that list's use
        in copy_scan_info_to_raw). On the very last call of a scan this
        still fires and uploads correctly, just up to ``wait`` seconds
        after the scan itself has finished.
        """
        with self._debounce_lock:
            pending = self._debounce_timers.pop(key, None)
            if pending is not None:
                pending.cancel()

            def fire():
                with self._debounce_lock:
                    self._debounce_timers.pop(key, None)
                self.append_aux(*file_names, **aux_kwargs)

            timer = Timer(wait, fire)
            timer.daemon = True
            self._debounce_timers[key] = timer
            timer.start()
        return timer

    def append_aux(self, *file_names, run_number=None, pgroup=None, check_group=True):
        """
        POST {broker_address_aux}/copy_user_files -- copy file_names into
        the aux directory of run_number on the server.

        broker_address_aux is sf_daq_broker's *slow* broker (see class
        docstring), the same single-threaded process that also serves
        detector/DAP settings and the hardware diagnostics endpoints. Called
        directly once per scan (start/end status, aliases, monitors), always
        from a background Thread so it doesn't block the scan itself --
        copy_scan_info_to_raw instead goes through
        :meth:`_debounced_append_aux` since it would otherwise fire once per
        scan step. Even so, it still queues behind whatever else is talking
        to broker_address_aux at the time. Avoid running settings/diagnostics
        polling on broker_address_aux during an active scan; it will show
        up as extra latency here.
        """
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
        do_pulse_picker_action=False,
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
        do_pulse_picker_action=False,
        **kwargs,
    ):
        if not self.pulse_picker:
            return
        if not do_pulse_picker_action:
            return

        self.pulse_picker.close(verbose=False)

    def check_counters_for_scan(
        self,
        scan,
        channels_to_check=["channels_BSCAM", "channels_JF"],
        channels_check_timeout=3,
        **kwargs,
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

    def count_run_number_up_and_attach_to_scan(self, scan, pgroup=None, **kwargs):
        """
        Increments the run number by one.
        """
        if pgroup is None:
            pgroup = self.pgroup
        runno = self.get_next_run_number(pgroup)
        print(f"Run number incremented to {runno}")
        # scan.daq_run_number = runno
        scan._append(DetectorMemory, runno, name="daq_run_number")

    # get/set_dap_settings and get/set_detector_settings are NOT dead here by
    # accident -- they're implemented and used, just per-detector rather than
    # per-Daq: see eco.detector.jungfrau.Jungfrau.get_dap_settings/
    # set_dap_settings/get_detector_settings/set_detector_settings, which
    # talk to broker_address_aux (the "slow" broker in sf_daq_broker's own
    # naming) exactly as these old stubs intended.

    def init_namespace(
        self,
        scan=None,
        init_required_namespace_components_only=True,
        append_status_info=True,
        **kwargs,
    ):
        if append_status_info:
            # background=False: this must block until init actually
            # finishes - the status info appended right after depends on
            # the namespace being initialized by then (background=True,
            # now init_all()'s default, would return before that).
            self.namespace.init_all(
                background=False,
                silent=False,
                required_only=init_required_namespace_components_only,
            )

    def append_start_status_to_scan(
        self, scan=None, pgroup=None, append_status_info=True, **kwargs
    ):
        if not append_status_info:
            return
        # raise_on_incomplete=False: this is a best-effort snapshot of the
        # whole namespace at run start -- an unrelated, incomplete component
        # elsewhere must never abort a run just to collect status metadata.
        namespace_status = self.namespace.get_status(
            base=None, raise_on_incomplete=False
        )
        stat = {"status_run_start": namespace_status}
        scan.namespace_status = stat

        if True:
            if hasattr(scan, "daq_run_number"):
                runno = scan.daq_run_number.get_current_value()
            else:
                runno = self.get_last_run_number()

            if pgroup is None:
                pgroup = self.pgroup
            tmpdir = Path(
                f"/sf/bernina/data/{pgroup}/res/run_data/daq/run{runno:04d}/aux"
            )
            tmpdir.mkdir(exist_ok=True, parents=True)
            try:
                tmpdir.chmod(0o775)
            except:
                pass

            statusfile = tmpdir / Path("status.json")
            if not statusfile.exists():
                with open(statusfile, "w") as f:
                    json.dump(
                        scan.namespace_status,
                        f,
                        sort_keys=True,
                        cls=NumpyEncoder,
                        indent=4,
                    )
            else:
                with open(statusfile, "r+") as f:
                    f.seek(0)
                    json.dump(
                        scan.namespace_status,
                        f,
                        sort_keys=True,
                        cls=NumpyEncoder,
                        indent=4,
                    )
                    f.truncate()
                    print("Wrote status with seek truncate!")
            if not statusfile.group() == statusfile.parent.group():
                shutil.chown(statusfile, group=statusfile.parent.group())

            response = self.append_aux(
                statusfile.resolve().as_posix(),
                pgroup=pgroup,
                run_number=runno,
            )

    def _create_runtable_metadata_append_status_to_runtable(
        self, scan, append_status_info=True, **kwargs
    ):

        print("run_table appending run")
        runno = scan.daq_run_number.get_current_value()
        metadata = {
            "type": "scan",
            "name": scan.description.get_current_value(),
            "scan_info_file": "",
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
            self.run_table.append_run(
                runno,
                metadata=metadata,
                d=scan.namespace_status["status_run_start"],
            )
            # self.run_table.update()
        except:
            print("WARNING: issue adding data to run table")
        print(f"Runtable appending took: {time.time()-t_start_rt:.3f} s")

    def copy_scan_info_to_raw(
        self, scan, pgroup=None, debounce_wait=None, **kwargs
    ):
        """
        Write scan_info_rel.json locally and upload it to the run's aux
        directory on the server.

        This runs once per scan step (callbacks_end_step) *and* once more
        at scan end (callbacks_end_scan) with the final scan_info. The local
        write is synchronous and cheap; the upload
        (:meth:`_debounced_append_aux`) is debounced with a ``debounce_wait``
        second trailing debounce (default: self._scan_info_debounce_wait,
        2.0 s) keyed on (pgroup, runno), so a burst of fast steps only
        produces one upload of the latest file, fired ``debounce_wait``
        seconds after the last step in the burst -- see class docstring for
        why this matters (broker_address_aux is single-threaded server-side).
        Pass debounce_wait=0 to restore the previous fire-immediately
        behaviour.
        """
        if debounce_wait is None:
            debounce_wait = self._scan_info_debounce_wait
        t_start = time.time()

        if pgroup is None:
            pgroup = self.pgroup

        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number.get_current_value()
        else:
            runno = self.get_last_run_number()

        # get data that should come later from api or similar.
        # run_directory = list(
        #     Path(f"/sf/bernina/data/{self.pgroup}/raw").glob(f"run{runno:04d}*")
        # )[0].as_posix()

        # Get scan info from scan
        si = scan.scan_info
        # save temprary file and send then to raw
        if pgroup is None:
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
            self._debounced_append_aux(
                ("scan_info", pgroup, runno),
                debounce_wait,
                scaninfofile.as_posix(),
                pgroup=pgroup,
                run_number=runno,
            )
        )
        # DEBUG
        # print(
        #     f"Sending scan_info_rel.json in {Path(scaninfofile).parent.stem} to run number {runno}."
        # )
        # response = daq.append_aux(scaninfofile.as_posix(), pgroup=pgroup, run_number=runno)
        # print(f"Status: {response.json()['status']} Message: {response.json()['message']}")
        # print(
        #     f"--> creating and copying file took{time.time()-t_start} s, presently adding to deadtime."
        # )

    def append_status_to_scan_and_store(
        self, scan, pgroup=None, append_status_info=True, **kwargs
    ):
        if not append_status_info:
            return

        if not len(scan.values_done()) > 0:
            return

        # raise_on_incomplete=False: see append_start_status_to_scan above --
        # a best-effort snapshot must not abort the run over unrelated status.
        namespace_status = self.namespace.get_status(
            base=None, raise_on_incomplete=False
        )
        scan.namespace_status["status_run_end"] = namespace_status
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number.get_current_value()
        else:
            runno = self.get_last_run_number()

        if pgroup is None:
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
                json.dump(
                    scan.namespace_status, f, sort_keys=True, cls=NumpyEncoder, indent=4
                )
        else:
            with open(statusfile, "r+") as f:
                f.seek(0)
                json.dump(
                    scan.namespace_status, f, sort_keys=True, cls=NumpyEncoder, indent=4
                )
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

    def check_checker_before_step(self, scan, **kwargs):
        # self.
        if self.checker:
            first_check = time.time()
            checker_unhappy = False
            print("")
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

    def check_checker_after_step(self, scan, **kwargs):
        if self.checker:
            if not self.checker.stop_and_analyze():
                scan._current_step_ok = False

    def copy_aliases_to_scan(self, scan, send_aliases_now=False, pgroup=None, **kwargs):
        if send_aliases_now or (len(scan.values_done()) == 1):
            namespace_aliases = self.namespace.alias.get_all()
            if hasattr(scan, "daq_run_number"):
                runno = scan.daq_run_number.get_current_value()
            else:
                runno = self.daq.get_last_run_number()
            if pgroup is None:
                pgroup = self.pgroup
            tmpdir = Path(
                f"/sf/bernina/data/{pgroup}/res/run_data/daq/run{runno:04d}/aux"
            )
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
        runno = scan.daq_run_number.get_current_value()
        message_string = f"#### DAQ run {runno}"
        if scan.description():
            message_string += f": {scan.description()}\n"
        else:
            message_string += f"\n"
        try:
            elog_ids = scan.status_to_elog(text=message_string, auto_title=False)
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
                if hasattr(adj, "readback"):
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

    def end_scan_monitors(self, scan, pgroup=None, **kwargs):
        for tmon in scan.daq_monitors:
            scan.daq_monitors[tmon].stop_callback()

        monitor_result = {
            tmon: scan.daq_monitors[tmon].data for tmon in scan.daq_monitors
        }

        # save temprary file and send then to raw
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number.get_current_value()
        else:
            runno = self.get_last_run_number()

        if pgroup is None:
            pgroup = self.pgroup

        tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/run_data/daq/run{runno}/aux")
        tmpdir.mkdir(exist_ok=True, parents=True)
        try:
            tmpdir.chmod(0o775)
        except:
            pass
        scanmonitorfile = tmpdir / Path("scan_monitor.pkl")
        if not Path(scanmonitorfile).exists():
            with open(scanmonitorfile, "wb") as f:
                pickle.dump(monitor_result, f)

        print(f"Copying monitor file to run {runno} to the raw directory of {pgroup}.")
        response = self.append_aux(
            scanmonitorfile.as_posix(), pgroup=pgroup, run_number=runno
        )
        print(
            f"Status: {response.json()['status']} Message: {response.json()['message']}"
        )

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
