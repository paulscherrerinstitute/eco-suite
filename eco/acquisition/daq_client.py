import json
import pickle
import shutil
from datetime import datetime
from itertools import count
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
from eco.elements.protocols import Adjustable, is_adjustable, resolve_lazy
from eco.utilities.utilities import foo_get_kwargs
from eco.utilities.datafiles import ensure_dir, open_group_writable
from ..epics_utils.detector import DetectorPvDataStream
from ..epics_utils.utilities_epics import Monitor
from ..epics_utils import ca_tuning
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

    Run status: local namespace, or a status server
    ----------------------------------------------
    By default the three status-related scan callbacks (``init_namespace``,
    ``append_start_status_to_scan``, ``append_status_to_scan_and_store``)
    initialize this session's namespace and read every status channel from
    it -- minutes of ``init_all()`` plus a full CA fan-out, per session.
    Passing ``status_server="http://<host>:<port>"`` instead hands the whole
    job -- snapshot, write ``status.json``, upload it to the run -- to a
    long-running ``eco.status_server`` process that already holds an
    initialized namespace, and returns without waiting for any of it
    (``status_server_async``, on by default). Measured on bernina: 0.03 s per
    callback against ~22 s when this class waited. The file, its location and
    its contents are unchanged either way, so the two paths are
    interchangeable per run.

    Whether the server is used is decided once per scan, from ``/health``:
    it has to be reachable, ``ready``, and its namespace has to have been
    built less than ``status_server_max_age`` (12 h) ago -- a long-lived
    server drifts, since ``AdjustableMemory``/``AdjustableFS`` values a
    scientist changes in their own session never reach it. Too old, and
    ``status_server_stale_action`` decides: ``"ask"`` (default) prompts with
    a 20 s timeout and restarts the server unless told to use the local
    namespace instead. Anything unusable -- no server, unreachable, still
    initializing -- prints why and falls back to initializing and reading
    this session's namespace, exactly as before, unless
    ``status_server_strict=True``. See ``eco/status_server/README.md``.
    """

    def __init__(
        self,
        broker_address="http://sf-daq:10002",
        broker_address_aux="http://sf-daq:10003",
        timeout=2,
        # timeout=10,
        pulse_id_timeout=5.0,
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
        status_server=None,
        status_server_timeout=10.0,
        status_server_snapshot_timeout=180.0,
        status_server_strict=False,
        status_server_wait_ready=1800,
        status_server_max_age=12 * 3600,
        status_server_stale_action="ask",
        status_server_stale_timeout=20.0,
        status_server_async=True,
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
        # Separate from `timeout` (the broker HTTP request budget) on purpose:
        # get_pulse_id() used to default to `self.timeout`, so a 2 s HTTP
        # timeout doubled as the pulse_id wait budget with no way to tune one
        # without the other. A real scan (p19734 run 1146, 2026-09-06) hit
        # this exact 2 s budget on a transient CA gap of ~3.4 s and aborted;
        # raising just this knob gives real hiccups more room without
        # changing broker request semantics.
        self.pulse_id_timeout = pulse_id_timeout
        # Never actually stored before despite being an __init__ parameter
        # since day one - every f"/sf/{self.instrument or 'bernina'}/..."
        # path built in _write_status_locally()/write_status() therefore hit
        # AttributeError on any real Daq (masked so far because the only
        # caller reachable in tests treats "any exception" as "fell back
        # correctly" - see test_start_status_falls_back_to_local_namespace_
        # on_server_error).
        self.instrument = instrument
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
        # keyed by a monotonic id (not position) so that concurrent scans
        # starting/stopping acquisitions on this shared Daq instance can't
        # invalidate each other's in-flight index into this structure
        self.running = {}
        self._running_ids = count()
        self._event_master = event_master
        self._detectors_event_code = detectors_event_code
        # Dedicated CA-monitor cache for the detectors' event-code
        # frequency, read by `rate_multiplicator` on *every* scan step (via
        # `retrieve()`, once per `stop()`). Reading it with a fresh
        # get_current_value() each time is the exact `pulse_id` bug above,
        # on a different PV: pyepics' PV.get() returns None on a transient
        # CA hiccup instead of raising, and `int(100 / freq)` on that None
        # killed a real scan mid-run (SIN-TIMAST-TMA:Evt-52-Freq-I, hundreds
        # of channels transiently disconnected under CA congestion - see
        # the ca_tuning "silent None" warning this raised). Monitor once
        # here, same pattern as the pulse_id cache above, and read the cache
        # in `rate_multiplicator` instead of gets on the hot path.
        self._frequency_detector = None
        self._frequency_monitor = None
        self._frequency_latest = {"value": None}
        if event_master is not None and detectors_event_code is not None:
            try:
                self._frequency_detector = event_master.__dict__[
                    f"code{detectors_event_code:03d}"
                ].frequency
                mon = self._frequency_detector.set_current_value_callback(
                    func="latest"
                )
                mon.start()  # one get to seed the cache, then monitor-only
                self._frequency_monitor = mon
                self._frequency_latest = mon.data
            except Exception:
                # A code with no live frequency (MasterEventCodeFix, used for
                # the CTA sequencer's fixed-delay codes) has no PV to
                # monitor at all - get_detector_code_frequency()'s own
                # fallback below handles that by raising a clear error
                # instead of a bare TypeError on `100 / None`.
                pass
        self.name = name
        self.namespace = namespace
        self.checker = checker
        self.run_table = run_table
        self.pulse_picker = pulse_picker
        self._default_file_path = None
        # Opt-in alternative to doing namespace.init_all() + a full
        # namespace.get_status() CA fan-out in *this* session: point at a
        # long-running eco.status_server namespace-mode server (see
        # eco/status_server/), which already holds an initialized namespace.
        # A str is a base URL; a StatusServerClient instance is used as-is;
        # None (default) keeps the existing, unchanged local behaviour.
        self._status_server = status_server
        self._status_server_client = None
        # Short timeout for /health and the admin routes (so an unreachable
        # server fails fast), long one for a snapshot - a full get_status()
        # fan-out over ~14k bernina channels takes 10-20 s.
        self.status_server_timeout = status_server_timeout
        self.status_server_snapshot_timeout = status_server_snapshot_timeout
        # strict=False: a status server that is down, unreachable or still
        # initializing must never abort a run - fall back to the local
        # mechanism and say so. strict=True turns those into hard errors,
        # for testing the server path itself.
        self.status_server_strict = status_server_strict
        self.status_server_wait_ready = status_server_wait_ready
        # How old the server's namespace may be before this session stops
        # trusting it and reads its own instead. The server holds one
        # initialized namespace for as long as it runs, and some of what
        # get_status() reports is not re-read from hardware on every
        # snapshot - AdjustableMemory/DetectorMemory values are
        # process-local, and a component that lost its IOC stays as it was.
        # None disables the check (always use the server when it answers).
        self.status_server_max_age = status_server_max_age
        # What to do when it is too old: "ask" (prompt, with a timeout),
        # "restart" (refresh it and wait), "local" (use this session's
        # namespace instead), "use" (trust it anyway).
        self.status_server_stale_action = status_server_stale_action
        self.status_server_stale_timeout = status_server_stale_timeout
        # Have the server take the snapshot, write status.json AND upload it
        # to the run in the background, instead of making the scan wait for
        # all three. See Daq.write_status / POST /status/capture.
        self.status_server_async = status_server_async
        if not rate_multiplicator == "auto":
            print(
                "warning: rate multiplicator automatically determined from event_master!"
            )
        self.callbacks_start_scan = [
            self.check_counters_for_scan,
            self.init_namespace,
            self.count_run_number_up_and_attach_to_scan,
            self.append_start_status_to_scan,
            # namespace-wide recording on the status server - no-op without
            # one (see start_scan_monitoring's docstring); after
            # count_run_number_up_and_attach_to_scan, which is where
            # scan.daq_run_number comes from.
            self.start_scan_monitoring,
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
            # before copy_scan_info_to_raw: registers "monitors" on
            # scan.scan_parameters, which that call then writes into
            # scan_info_rel.json (see end_scan_monitoring's docstring).
            self.end_scan_monitoring,
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
        return int(100 / self.get_detector_code_frequency())

    def get_detector_code_frequency(self):
        """Frequency (Hz) of the detectors' event code -- never `None`.

        Reads the CA-monitor cache set up in `__init__`, not a fresh get:
        this is called once per scan step (`rate_multiplicator`, from
        `retrieve()`), which is exactly the traffic pattern that made the
        `pulse_id` bug (see `get_pulse_id`'s docstring) real rather than
        theoretical. Falls back to one direct read only if the monitor was
        never able to attach (e.g. the code was resolved before the
        underlying component finished initializing), and raises rather than
        ever returning `None` for the caller to divide by.
        """
        value = self._frequency_latest.get("value")
        if value is not None:
            return value
        if self._frequency_detector is not None:
            try:
                value = self._frequency_detector.get_current_value()
            except Exception:
                value = None
            if value is not None:
                return value
        pvname = getattr(self._frequency_detector, "pvname", "<unknown PV>")
        raise TimeoutError(
            f"could not determine the frequency of detectors_event_code="
            f"{self._detectors_event_code} ({pvname}): no monitored value "
            "yet and a direct read also failed or returned None. If this "
            "code has no live frequency (e.g. a fixed-delay CTA sequencer "
            "code), detectors_event_code is pointed at the wrong one."
        )

    @property
    def pgroup(self):
        """The pgroup *value* (a string), never the adjustable holding it.

        Resolved before the protocol check: `_pgroup` normally
        arrives as a still-lazy namespace proxy (bernina.py passes
        `NamespaceComponent(namespace, "config_bernina.pgroup")`, which
        `replace_NamespaceComponents` turns into a `Proxy`), and a bare
        protocol check on an unresolved proxy is False -- which used to leak
        the proxy itself into every `json={"pgroup": ...}` REST call as
        "Object of type LazyComponent is not JSON serializable". See
        `eco.elements.protocols.resolve_lazy`.
        """
        pgroup = resolve_lazy(self._pgroup)
        if isinstance(pgroup, Adjustable):
            return pgroup.get_current_value()
        else:
            return pgroup

    @pgroup.setter
    def pgroup(self, value):
        if is_adjustable(self._pgroup):
            return resolve_lazy(self._pgroup).set_target_value(value).wait()
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

    def get_pulse_id(self, newer_than=None, timeout=None):
        """The current pulse_id as an `int` -- never `None`.

        Every caller used to do ``int(self.pulse_id.get_current_value())``,
        which is a fresh channel-access get on an ``auto_monitor=False`` PV.
        pyepics' ``PV.get()`` **returns None on timeout** rather than raising,
        so under CA congestion -- exactly what a scan step produces, with every
        detector/adjustable in the namespace issuing its own gets at once --
        that ``int(...)`` blew up mid-scan with::

            TypeError: int() argument must be a string, a bytes-like object
            or a real number, not 'NoneType'

        and killed the run. `start()` was hardened against this before, by
        adding the dedicated ``auto_monitor=True`` PV set up in `__init__` and
        reading its cache instead of issuing gets; `stop()` was not, and kept
        the raw get in two places -- including a *polling loop* that fired one
        CA get every ``wait_cycle_sleep`` (10 ms) for the whole acquisition,
        which did not just suffer from the congestion but measurably added to
        it. This method is that same monitor-cache read, factored out so every
        pulse_id caller shares one hardened implementation.

        Reading the monitor cache costs no CA traffic at all: the IOC pushes
        updates and the callback in `__init__` stores them. `newer_than`
        (a `time.time()` stamp) additionally waits for an update at least that
        recent, for callers that need *now*'s pulse and not merely the last one
        seen -- `start()` needs that, since a stale start_id would silently
        widen the acquisition window.

        Raises `TimeoutError` (never returns None) if no acceptable value
        arrives within `timeout`, defaulting to ``self.pulse_id_timeout``
        (separate from ``self.timeout``, the broker HTTP request budget, so
        one can be tuned without the other).
        """
        if timeout is None:
            timeout = self.pulse_id_timeout
        deadline = time.time() + timeout

        if hasattr(self, "_pulse_id_updated"):
            while True:
                with self._pulse_id_latest_lock:
                    ts = self._pulse_id_latest["timestamp"]
                    val = self._pulse_id_latest["value"]
                if val is not None and (
                    newer_than is None or (ts is not None and ts >= newer_than)
                ):
                    return int(val)
                remaining = deadline - time.time()
                if remaining <= 0:
                    state = ca_tuning.describe_channel(
                        getattr(self, "_pulse_id_monitor_pv", None),
                        self.pulse_id.pvname,
                    )
                    raise TimeoutError(
                        f"Timeout {timeout} s hit while waiting for a valid"
                        f"{', up-to-date' if newer_than is not None else ''} "
                        f"pulse_id from the {self.pulse_id.pvname} monitor. "
                        f"last value: {val}; last timestamp: {ts}; "
                        f"required newer than: {newer_than}; {state}"
                    )
                self._pulse_id_updated.wait(timeout=min(remaining, 0.25))
                self._pulse_id_updated.clear()

        # Fallback for a pulse_id_adj configured as a pre-built object rather
        # than a PV name string: no dedicated monitor is set up in that case,
        # so poll explicitly -- with a per-call timeout and a None check, which
        # is the part the old inline code in stop() was missing.
        pv = self.pulse_id._pv
        tvars = None
        value = None
        poll_interval = 0.02
        max_poll_interval = 0.25
        per_call_timeout = 1.0  # headroom for a saturated CA processing thread
        while True:
            if newer_than is not None:
                tvars = pv.get_timevars(timeout=per_call_timeout)
                fresh = tvars is not None and tvars["timestamp"] >= newer_than
            else:
                fresh = True
            if fresh:
                value = pv.get(use_monitor=False, timeout=per_call_timeout)
                if value is not None:
                    return int(value)
            if time.time() > deadline:
                state = ca_tuning.describe_channel(pv, getattr(pv, "pvname", None))
                raise TimeoutError(
                    f"Timeout {timeout} s hit while waiting for a valid"
                    f"{', up-to-date' if newer_than is not None else ''} "
                    f"pulse_id. timevars: {tvars}; value: {value}; "
                    f"required newer than: {newer_than}; {state}"
                )
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, max_poll_interval)

    def wait_for_pulse_id(self, pulse_id, poll_interval=0.01, timeout=None):
        """Block until the machine's pulse_id counter has reached `pulse_id`.

        Event-driven off the same monitor as `get_pulse_id`, so waiting out an
        acquisition costs zero CA gets where the old ``while
        int(self.pulse_id.get_current_value()) < stop_id: sleep(0.01)`` issued
        one per 10 ms.

        `timeout=None` (the default) waits indefinitely, deliberately: the old
        loop did too, and a scan step that is waiting for beam to come back
        must not be turned into an exception just because the wait got long.
        """
        deadline = None if timeout is None else time.time() + timeout
        while True:
            # A get that cannot see a fresh value is not a reason to give up
            # here -- we only need to know whether we have passed pulse_id yet,
            # and the next monitor update will tell us.
            try:
                current = self.get_pulse_id(timeout=poll_interval)
            except TimeoutError:
                current = None
            if current is not None and current >= pulse_id:
                return current
            if deadline is not None and time.time() > deadline:
                raise TimeoutError(
                    f"Timeout {timeout} s hit waiting for pulse_id {pulse_id} "
                    f"(last seen: {current})"
                )
            if hasattr(self, "_pulse_id_updated"):
                self._pulse_id_updated.wait(timeout=poll_interval)
                self._pulse_id_updated.clear()
            else:
                sleep(poll_interval)

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

        # newer_than: the start of the acquisition window must be a pulse seen
        # *after* this step began, never a stale cached one.
        starttime_local = time.time()
        start_id = self.get_pulse_id(newer_than=starttime_local)

        acq_pars = {
            "label": label,
            "start_id": start_id,
        }
        acq_pars.update(kwargs)
        acq_id = next(self._running_ids)
        self.running[acq_id] = acq_pars
        if scan:
            scan.counter_scratch(self.name)["acquisition_index"] = acq_id
        return acq_id

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
            # No `newer_than` here: the last monitored pulse is at most one
            # machine period old, and requiring a *newer* one would turn a
            # stopped beam (where the counter simply stops advancing) into a
            # TimeoutError, which stopping an acquisition must not do.
            stop_id = self.get_pulse_id()

        if scan:
            acq_ix = scan.counter_scratch(self.name).get("acquisition_index")
        if acq_ix is None:
            # no scan/explicit id given: fall back to the most recently
            # started acquisition (dicts preserve insertion order)
            acq_ix = next(reversed(self.running))

        acq_pars = self.running.pop(acq_ix)
        acq_pars["stop_id"] = stop_id

        label = acq_pars.pop("label")

        # if scan:
        #     tmp = scan.info()
        #     tmp['daq_pars'] = acq_pars
        #     scan.info()
        if wait:
            self.wait_for_pulse_id(stop_id, poll_interval=wait_cycle_sleep)

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

    # -- status server (optional alternative to the local namespace) --------
    #
    # With `status_server=` set, the three status-related scan callbacks
    # below take their values from a long-running server process that
    # already holds an initialized namespace, instead of initializing and
    # reading one in this session. Everything else - where the file lands,
    # its contents, the append_aux upload attaching it to the run's aux
    # folder - is unchanged, so the two paths are interchangeable per run.

    @property
    def status_client(self):
        """The configured StatusServerClient, or None if not using one."""
        if self._status_server is None:
            return None
        if self._status_server_client is None:
            from eco.status_server.client import StatusServerClient

            if isinstance(self._status_server, str):
                self._status_server_client = StatusServerClient(
                    self._status_server,
                    timeout=self.status_server_timeout,
                    snapshot_timeout=self.status_server_snapshot_timeout,
                )
            else:
                self._status_server_client = self._status_server
        return self._status_server_client

    def _status_server_failed(self, what, exc):
        """Common handling for a status-server call that did not work:
        re-raise in strict mode, otherwise warn and let the caller fall back
        to the local mechanism."""
        msg = f"status server: {what} failed ({type(exc).__name__}: {exc})"
        if self.status_server_strict:
            raise RuntimeError(msg) from exc
        print(colorama.Fore.RED + "WARNING: " + msg + colorama.Fore.RESET)
        print("         falling back to the local namespace mechanism.")
        return None

    def use_status_server(self, verbose=False):
        """Whether this session should take status from the server right now.

        Three conditions, all checked live rather than assumed, because a
        long-running server is exactly the thing that can be up but no longer
        worth believing:

        1. one is configured at all;
        2. it answers /health and reports ``ready``;
        3. its namespace was (re)built less than ``status_server_max_age``
           ago (12 h by default).

        The age check is the substantive one. The server holds a single
        initialized namespace for its whole lifetime; a snapshot re-reads
        every EPICS channel, but `AdjustableMemory`/`DetectorMemory` values
        are process-local to the server and an `AdjustableFS` setting a
        scientist changed in their own session is not visible to it. That
        drift grows with uptime, so past some age this session is better off
        paying for its own `get_status()` than recording a plausible-looking
        stale one. Set ``status_server_max_age=None`` to switch the check
        off.

        Never raises: an unreachable server is a "no", which is the whole
        point of the fallback.
        """
        client = self.status_client
        if client is None:
            return False
        try:
            health = client.health()
        except Exception as exc:
            if verbose:
                print(f"status server: not reachable ({type(exc).__name__}), "
                      "using the local namespace.")
            return False
        if not health.get("ready"):
            if verbose:
                print(f"status server: state '{health.get('state')}', "
                      "using the local namespace.")
            return False
        max_age = self.status_server_max_age
        if max_age is None:
            return True
        built_at = health.get("last_init_finished")
        if built_at is None:
            # ready but no build timestamp: nothing to judge staleness by,
            # so treat it as unusable rather than silently trusting it.
            if verbose:
                print("status server: ready but reports no build time, "
                      "using the local namespace.")
            return False
        age = time.time() - built_at
        if age <= max_age:
            return True
        return self._handle_stale_status_server(age, max_age, verbose=verbose)

    def _handle_stale_status_server(self, age, max_age, verbose=True):
        """The server is up but its namespace is older than we trust.

        Both ways out cost minutes - restarting the server re-runs
        init_all() there, falling back re-runs it here - so this is a real
        question rather than something to decide silently. Asked with a
        timeout, because a scan started from a script has nobody to answer
        it; the timeout takes the restart, since that is the option that
        leaves the next run (and every other session's) fast rather than
        making each one pay locally.
        """
        action = self.status_server_stale_action
        msg = (
            f"status server: its namespace was built {age/3600:.1f} h ago, "
            f"older than the {max_age/3600:.1f} h limit."
        )
        if action == "use":
            if verbose:
                print(msg + " Using it anyway (status_server_stale_action='use').")
            return True
        if action == "local":
            if verbose:
                print(msg + " Using this session's namespace instead.")
            return False
        if action == "ask":
            timeout = self.status_server_stale_timeout
            print(colorama.Fore.YELLOW + msg + colorama.Fore.RESET)
            try:
                answer = inputimeout.inputimeout(
                    prompt=(
                        f"Restart it now and wait (a few minutes), or use this "
                        f"session's namespace? [R/l] "
                        f"({timeout:.0f} s -> restart): "
                    ),
                    timeout=timeout,
                )
            except inputimeout.TimeoutOccurred:
                answer = ""
            except Exception:
                # no tty (a script, a service, a captured pipe): treat it as
                # nobody there to answer rather than failing the scan.
                answer = ""
            if answer.strip().lower().startswith(("l", "n")):
                print("  -> using this session's namespace.")
                return False
        elif action != "restart":
            print(msg + f" Unknown status_server_stale_action "
                        f"{action!r}; using this session's namespace.")
            return False

        print("  -> restarting the status server and waiting for it ...")
        try:
            health = self.status_client.reinit(
                wait=True, progress=True, timeout=self.status_server_wait_ready
            )
        except Exception as exc:
            self._status_server_failed("restarting a stale server", exc)
            return False
        print(
            f"status server refreshed: {health.get('n_initialized')}/"
            f"{health.get('n_target_names')} components, "
            f"{health.get('n_failed')} failed."
        )
        return True

    def _write_status_locally(self, payload, runno, pgroup):
        """Write `payload` (the whole `{block: get_status()}` mapping) to the
        run's aux directory and return the path. Shared by the two scan
        callbacks and by `write_status`, which all wrote their own copy of
        this before."""
        tmpdir = Path(
            f"/sf/{self.instrument or 'bernina'}/data/{pgroup}"
            f"/res/run_data/daq/run{runno:04d}/aux"
        )
        ensure_dir(tmpdir)
        statusfile = tmpdir / "status.json"
        with open_group_writable(statusfile, "w") as f:
            json.dump(payload, f, sort_keys=True, cls=NumpyEncoder, indent=4)
        return statusfile

    def write_status(
        self,
        pgroup=None,
        run_number=None,
        key="status_run_start",
        use_server=None,
        upload=True,
    ):
        """Capture the namespace status and write it into one run's aux
        directory, outside of a scan.

        The standalone form of what the scan callbacks do: use it to attach
        status to a run that was taken without one, to re-capture it after
        fixing a component, or to record the state of the instrument against
        an arbitrary run number.

        pgroup / run_number default to this Daq's pgroup and the broker's
        current run number.

        use_server: None (default) asks :meth:`use_status_server` - the
        server is used when it is up and its namespace is fresh enough.
        True forces it (and, with ``status_server_strict``, makes a failure
        an error rather than a fallback); False always reads the local
        namespace.

        upload: also hand the file to the broker with :meth:`append_aux`, so
        it lands in the run's raw aux folder. That is what makes it part of
        the run rather than just a file in res/.

        Returns the path written.
        """
        if pgroup is None:
            pgroup = self.pgroup
        if run_number is None:
            run_number = self.get_last_run_number(pgroup=pgroup)
        run_number = int(run_number)

        if use_server is None:
            use_server = self.use_status_server(verbose=True)

        statuspath = None
        if use_server:
            if self.status_server_async:
                job = self._capture_on_server(key, run_number, pgroup)
                if job is not None:
                    # a standalone call should land before it returns, unlike
                    # a scan callback which deliberately does not wait
                    try:
                        done = self.status_client.wait_write_job(
                            job["job_id"],
                            timeout=self.status_server_snapshot_timeout,
                        )
                        return Path(done.get("path") or job["path"])
                    except Exception as exc:
                        self._status_server_failed("waiting for the capture", exc)
            else:
                result = self._status_from_server(key, run_number, pgroup)
                if result is not None:
                    _, statuspath = result

        if statuspath is None:
            # either no server, or it failed and strict mode let us continue
            namespace_status = self.namespace.get_status(
                base=None, raise_on_incomplete=False
            )
            payload = {key: namespace_status}
            existing = Path(
                f"/sf/{self.instrument or 'bernina'}/data/{pgroup}"
                f"/res/run_data/daq/run{run_number:04d}/aux/status.json"
            )
            if existing.exists():
                # match the server's merge behaviour: adding status_run_end
                # must not drop the status_run_start already on disk.
                try:
                    prior = json.loads(existing.read_text())
                    if isinstance(prior, dict):
                        prior.update(payload)
                        payload = prior
                except (ValueError, OSError):
                    pass
            statuspath = str(self._write_status_locally(payload, run_number, pgroup))

        if upload and statuspath:
            self.append_aux(statuspath, pgroup=pgroup, run_number=run_number)
        return Path(statuspath)

    def _status_server_ok_for_this_scan(self, scan):
        """Decide once per scan whether the server is used, and remember it.

        The decision involves a /health round trip and possibly a prompt, so
        it must not be re-taken between the start and end callbacks of the
        same scan: a run whose start status came from the server and whose
        end status came from the local namespace would be quietly
        inconsistent.
        """
        if self.status_client is None:
            return False
        cache = getattr(scan, "_eco_status_server_ok", None)
        if cache is None:
            cache = self.use_status_server(verbose=True)
            if not cache:
                print(
                    "Recording run status from this session's namespace "
                    "instead (the local, slower path)."
                )
            try:
                scan._eco_status_server_ok = cache
            except Exception:
                pass  # a scan object that will not take an attribute
        return cache

    def _server_status_for_scan(self, scan, key, runno, pgroup):
        """Ask the server for this run's status block. True if it took care
        of it, False to fall back to the local path.

        With `status_server_async` (the default) the server also writes and
        uploads the file, and this returns without waiting for any of it -
        which is the point: on bernina it turns ~22 s of scan-boundary dead
        time into one round trip.
        """
        cs = scan.counter_scratch(self.name)
        if self.status_server_async:
            # keep_status only for the start block: it is the one the run
            # table fills itself from, and holding the values for a block
            # nobody collects would just be memory sitting on the server.
            job = self._capture_on_server(
                key, runno, pgroup, keep_status=(key == "status_run_start")
            )
            if job is None:
                return False
            cs.setdefault("namespace_status", {})[key] = {
                "status": {}, "status_channels": {},
                "written_by_status_server": job.get("path"),
                "job_id": job.get("job_id"),
            }
            cs.setdefault("status_jobs", {})[key] = job
            print(f"status: {key} delegated to {self.status_client.base_url} "
                  f"-> {job.get('path')}")
            return True

        result = self._status_from_server(key, runno, pgroup)
        if result is None:
            return False
        namespace_status, statuspath = result
        cs.setdefault("namespace_status", {})[key] = namespace_status
        if statuspath:
            self.append_aux(statuspath, pgroup=pgroup, run_number=runno)
        return True

    def _capture_on_server(self, key, runno, pgroup, keep_status=False):
        """Fire-and-forget: the server snapshots, writes status.json and
        uploads it to the run. Returns the job description, or None on
        failure (so the caller can fall back)."""
        try:
            return self.status_client.capture(
                pgroup=pgroup, run_number=runno, key=key, upload=True,
                keep_status=keep_status,
            )
        except Exception as exc:
            return self._status_server_failed(f"capture for {key}", exc)

    def _status_from_server(self, key, runno, pgroup, write_async=False):
        """Ask the server for a status snapshot AND to write it into the
        run's aux directory. Returns (status_dict, path) or None on failure.

        The server writes the file itself (same NFS path, same JSON shape as
        the local path produces); this side only uploads it with append_aux,
        exactly as before.
        """
        client = self.status_client
        try:
            resp = client.snapshot(
                pgroup=pgroup,
                run_number=runno,
                save=True,
                key=key,
                write_async=write_async,
            )
        except Exception as exc:
            return self._status_server_failed(f"snapshot for {key}", exc)
        job_id = resp.get("write_job_id")
        if job_id:
            try:
                # append_aux tells the broker to copy a file, so it has to
                # exist by then - an async write still has to be joined here.
                client.wait_write_job(
                    job_id, timeout=self.status_server_snapshot_timeout
                )
            except Exception as exc:
                return self._status_server_failed(f"status write for {key}", exc)
        status = {
            k: resp[k]
            for k in ("status", "status_channels", "status_times", "selections")
            if k in resp
        }
        return status, resp.get("saved_to")

    def init_namespace(
        self,
        scan=None,
        init_required_namespace_components_only=True,
        append_status_info=True,
        **kwargs,
    ):
        if not append_status_info:
            return
        if self.status_client is not None:
            try:
                # The server initializes on its own; this only makes sure it
                # has got there before the status callbacks start asking it
                # for values. Normally instant - it is a long-running
                # process - but after a server restart it is the same wait
                # the local init_all() would have been.
                health = self.status_client.wait_ready(
                    timeout=self.status_server_wait_ready, progress=True
                )
                print(
                    f"Using status server {self.status_client.base_url} "
                    f"({health.get('n_initialized')}/{health.get('n_target_names')} "
                    f"components initialized, {health.get('n_failed')} failed) "
                    "instead of initializing the namespace locally."
                )
                return
            except Exception as exc:
                self._status_server_failed("waiting for readiness", exc)
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

        if self._status_server_ok_for_this_scan(scan):
            if hasattr(scan, "daq_run_number"):
                runno = scan.daq_run_number.get_current_value()
            else:
                runno = self.get_last_run_number()
            if pgroup is None:
                pgroup = self.pgroup
            if self._server_status_for_scan(scan, "status_run_start", runno, pgroup):
                return
            # fell through -> non-strict fallback, continue below.

        # raise_on_incomplete=False: this is a best-effort snapshot of the
        # whole namespace at run start -- an unrelated, incomplete component
        # elsewhere must never abort a run just to collect status metadata.
        namespace_status = self.namespace.get_status(
            base=None, raise_on_incomplete=False
        )
        stat = {"status_run_start": namespace_status}
        scan.counter_scratch(self.name)["namespace_status"] = stat

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
            ensure_dir(tmpdir)

            statusfile = tmpdir / Path("status.json")
            if not statusfile.exists():
                with open_group_writable(statusfile, "w") as f:
                    json.dump(
                        stat,
                        f,
                        sort_keys=True,
                        cls=NumpyEncoder,
                        indent=4,
                    )
            else:
                with open_group_writable(statusfile, "r+") as f:
                    f.seek(0)
                    json.dump(
                        stat,
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

    def _push_acquiring_scan_status(self, scan, metadata, runno):
        """Hand the status server the scans.acquiring_scan.* values it can
        never poll on its own.

        Nothing under `scans` has a CA channel -- it is built from
        DetectorMemory (eco.elements.detector), whose Alias is constructed
        with channel=None -- and the server's own snapshot walk is driven
        entirely by Alias.get_all(), which only ever returns an alias that
        has one (eco.aliases.aliases.Alias.get_all). So the server is
        structurally blind to these values no matter how long it runs or
        how it is configured; this session already resolved the real scan
        object a moment ago to build `metadata`, so it hands the same
        values over instead, keyed to match the run table's "Custom table"
        header convention (see eco.utilities.runtable_stripped).

        Best-effort: a failed push must not break the run table append,
        which still gets everything under metadata.* regardless.
        """
        try:
            values = {
                # A raw datetime isn't JSON-serializable (requests' own
                # json.dumps has no encoder hook for it - confirmed live,
                # this crashed the push outright); a unix timestamp matches
                # every other timestamp already flowing through this file
                # (status_times, recording started_at/stopped_at, ...).
                "scans.acquiring_scan.start_time": datetime.now().timestamp(),
                "scans.acquiring_scan.description": metadata.get("name"),
                "scans.acquiring_scan.scan_command": metadata.get("scan_command"),
                "scans.acquiring_scan.adjustables_names": [
                    adj.name for adj in scan.adjustables if hasattr(adj, "name")
                ],
                "scans.acquiring_scan.initial_values": (
                    scan.initial_values.get_current_value()
                ),
                "scans.acquiring_scan.number_of_steps": metadata.get("steps"),
            }
            self.status_client.push_status(
                self.pgroup, runno, values, key="status_run_start"
            )
        except Exception:
            print(
                f"WARNING: could not push scans.acquiring_scan status for run {runno}"
            )
            traceback.print_exc()

    def _create_runtable_metadata_append_status_to_runtable(
        self, scan, append_status_info=True, **kwargs
    ):
        # Lost when this was split out of the old combined elog+run_table
        # callback (see eco.acquisition.counters_tmp, which still has the
        # guard in the equivalent spot) - without it, append_status_info=False
        # still built scan metadata and called run_table.append_run(), whose
        # very first use in a session authenticates to Google Sheets
        # (Run_Table2.__init__ -> Gsheet_API), a genuinely slow, easily
        # mistaken-for-init_all pause that append_status_info=False is
        # supposed to buy out of.
        if not append_status_info:
            return

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
        cs = scan.counter_scratch(self.name)
        block = (cs.get("namespace_status") or {}).get("status_run_start") or {}
        # ["status"], not the whole block: the run table looks names up as
        # "bernina.<name>" in a flat mapping, so handing it the get_status()
        # result meant nothing ever matched and it silently read every
        # adjustable over CA instead. (It tolerates both shapes now, but the
        # caller should still pass the right one.)
        values = block.get("status") or {}
        job = (cs.get("status_jobs") or {}).get("status_run_start")

        if job is not None and self.status_client is not None:
            self._push_acquiring_scan_status(scan, metadata, runno)

        if values or job is None:
            self._append_run_to_runtable(runno, metadata, values)
            return

        # The status is being taken on the status server right now. Wait for
        # it in the background rather than either blocking the scan or
        # letting the run table fall back to its own CA fan-out - which is
        # the very cost the server exists to remove.
        def _later():
            d = {}
            try:
                done = self.status_client.wait_write_job(
                    job["job_id"],
                    timeout=self.status_server_snapshot_timeout,
                    include_status=True,
                )
                if done.get("state") == "done":
                    d = done.get("status") or {}
                    block["status"] = d
                else:
                    print(
                        "WARNING: status server job for run "
                        f"{runno} ended '{done.get('state')}' "
                        f"({done.get('error')}); the run table row is filled "
                        "by reading the adjustables directly."
                    )
            except Exception as exc:
                print(
                    f"WARNING: could not collect run {runno} status from "
                    f"{self.status_client.base_url} ({type(exc).__name__}: "
                    f"{exc}); the run table row is filled by reading the "
                    "adjustables directly."
                )
            # append either way: a run without a run-table row is worse than
            # one whose row was filled the slow way.
            self._append_run_to_runtable(runno, metadata, d)

        Thread(
            target=_later, name=f"runtable-run{runno}", daemon=True
        ).start()
        print(
            f"run_table: run {runno} will be appended once the status server "
            "has the values (in the background)."
        )

    def _append_run_to_runtable(self, runno, metadata, d):
        t_start_rt = time.time()
        try:
            self.run_table.append_run(runno, metadata=metadata, d=d)
        except Exception:
            print("WARNING: issue adding data to run table")
            traceback.print_exc()
        print(
            f"Runtable appending took: {time.time()-t_start_rt:.3f} s "
            f"({len(d)} values from status)"
        )

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
        ensure_dir(tmpdir)
        scaninfofile = tmpdir / Path("scan_info_rel.json")
        if not Path(scaninfofile).exists():
            with open_group_writable(scaninfofile, "w") as f:
                json.dump(si, f, sort_keys=True, cls=NumpyEncoder, indent=4)
        else:
            with open_group_writable(scaninfofile, "r+") as f:
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

        if self._status_server_ok_for_this_scan(scan):
            if hasattr(scan, "daq_run_number"):
                runno = scan.daq_run_number.get_current_value()
            else:
                runno = self.get_last_run_number()
            if pgroup is None:
                pgroup = self.pgroup
            if self._server_status_for_scan(scan, "status_run_end", runno, pgroup):
                scan.set_scan_parameter("status", "aux/status.json")
                return

        # raise_on_incomplete=False: see append_start_status_to_scan above --
        # a best-effort snapshot must not abort the run over unrelated status.
        namespace_status = self.namespace.get_status(
            base=None, raise_on_incomplete=False
        )
        cs = scan.counter_scratch(self.name)
        cs["namespace_status"]["status_run_end"] = namespace_status
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number.get_current_value()
        else:
            runno = self.get_last_run_number()

        if pgroup is None:
            pgroup = self.pgroup
        tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/run_data/daq/run{runno:04d}/aux")
        ensure_dir(tmpdir)

        statusfile = tmpdir / Path("status.json")
        if not statusfile.exists():
            with open_group_writable(statusfile, "w") as f:
                json.dump(
                    cs["namespace_status"], f, sort_keys=True, cls=NumpyEncoder, indent=4
                )
        else:
            with open_group_writable(statusfile, "r+") as f:
                f.seek(0)
                json.dump(
                    cs["namespace_status"], f, sort_keys=True, cls=NumpyEncoder, indent=4
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
        scan.set_scan_parameter("status", "aux/status.json")

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

    def _capture_aliases_on_server(self, runno, pgroup, channeltypes=None):
        """Fire-and-forget: the server computes the alias list from its own,
        already-initialized namespace and writes/uploads aux/aliases.json.
        Returns the job description, or None on failure so the caller can
        fall back to the local namespace."""
        try:
            return self.status_client.capture_aliases(
                pgroup=pgroup, run_number=runno, upload=True,
                channeltypes=channeltypes,
            )
        except Exception as exc:
            return self._status_server_failed("aliases capture", exc)

    def copy_aliases_to_scan(self, scan, send_aliases_now=False, pgroup=None, **kwargs):
        """Write this run's alias list (short name -> PV/channel) into its
        aux directory, so downstream tools can map recorded status/data back
        to human-readable names without re-deriving eco's own namespace.

        With a status server configured and healthy for this scan (the same
        decision ``append_start_status_to_scan`` already made and cached on
        ``scan._eco_status_server_ok``), the server computes and writes the
        file from its own already-initialized namespace instead of this
        one - avoiding forcing the local, deliberately-still-lazy namespace
        just to read ``.alias``, which is exactly the cost status-server
        mode exists to avoid for ``get_status()``. Before this branch
        existed, ``self.namespace.alias.get_all()`` ran unconditionally here
        even in status-server mode, against a namespace nothing else had
        initialized - so ``aux/aliases.json`` was silently incomplete
        whenever a status server was in use. Falls back to the local path
        below (unchanged) on any server failure.
        """
        if not (send_aliases_now or (len(scan.values_done()) == 1)):
            return
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number.get_current_value()
        else:
            runno = self.daq.get_last_run_number()
        if pgroup is None:
            pgroup = self.pgroup

        if self._status_server_ok_for_this_scan(scan):
            job = self._capture_aliases_on_server(runno, pgroup)
            if job is not None:
                scan.counter_scratch(self.name).setdefault(
                    "status_jobs", {}
                )["aliases"] = job
                print(f"aliases: delegated to {self.status_client.base_url} "
                      f"-> {job.get('path')}")
                scan.set_scan_parameter("aliases", "aux/aliases.json")
                return
            # server failed - _status_server_failed already printed why and
            # that this is falling back; fall through to the local path.

        namespace_aliases = self.namespace.alias.get_all()
        tmpdir = Path(
            f"/sf/{self.instrument or 'bernina'}/data/{pgroup}"
            f"/res/run_data/daq/run{runno:04d}/aux"
        )
        ensure_dir(tmpdir)
        aliasfile = tmpdir / Path("aliases.json")
        if not Path(aliasfile).exists():
            with open_group_writable(aliasfile, "w") as f:
                json.dump(
                    namespace_aliases, f, sort_keys=True, cls=NumpyEncoder, indent=4
                )
        else:
            with open_group_writable(aliasfile, "r+") as f:
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
        scan.remaining_tasks[-1].start()
        scan.set_scan_parameter("aliases", "aux/aliases.json")

    # -- namespace-wide monitoring, server-only (callbacks_start_scan/
    # callbacks_end_scan below) --
    #
    # Unlike status/aliases, there is deliberately no local fallback here: a
    # local recording would mean this session's own namespace holding a live
    # CA monitor per channel for the scan's whole duration, which is exactly
    # the per-session cost the status server exists to avoid. If there is no
    # server (or it is not in use for this scan), these are no-ops - so a
    # scan run without a status server simply gets no
    # aux/namespace_monitor.h5, same as it already gets no server-backed
    # aliases.json/status.json.

    def start_scan_monitoring(self, scan, pgroup=None, mode="throttle",
                              min_interval=0.1, names=None, **kwargs):
        """Start a namespace-wide recording on the status server for this
        run: every monitorable channel's update history during the scan,
        not just the two snapshots status.json already captures.

        mode="throttle", min_interval=0.1 (10 Hz per channel) is the
        README's recommended default for a full-namespace recording -
        measured at ~22 MB / ~900 points/s against ~239 MB / ~5500
        points/s for mode="all" over the same window (DESIGN.md SS15).
        `names` restricts to a subset instead of every monitorable channel.

        Wired into `callbacks_start_scan`, right after
        `count_run_number_up_and_attach_to_scan` (needs `scan.daq_run_number`)
        and `append_start_status_to_scan` (shares its per-scan
        server-in-use decision, cached on the scan).
        """
        if self.status_client is None:
            return None
        if not self._status_server_ok_for_this_scan(scan):
            return None
        if pgroup is None:
            pgroup = self.pgroup
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number.get_current_value()
        else:
            runno = self.daq.get_last_run_number()
        recording_id = f"{pgroup}_run{runno:04d}"
        try:
            result = self.status_client.start_recording(
                recording_id=recording_id, names=names, mode=mode,
                min_interval=min_interval, pgroup=pgroup, run_number=runno,
            )
        except Exception as exc:
            return self._status_server_failed("start monitoring", exc)
        scan.counter_scratch(self.name)["monitoring_recording_id"] = recording_id
        print(
            f"monitoring: recording '{recording_id}' started on "
            f"{self.status_client.base_url} "
            f"({result.get('n_channels_attached')}/"
            f"{result.get('n_channels_requested')} channels attached)"
        )
        return result

    def end_scan_monitoring(self, scan, pgroup=None, upload=True, **kwargs):
        """Stop this scan's recording (see start_scan_monitoring) and have
        the server write it and upload it to the run - in the background
        (POST /recording/capture), the same fire-and-forget shape as
        append_status_to_scan_and_store/copy_aliases_to_scan: the write
        (one ArrayTimestamps per channel) and the broker upload both scale
        with how much was recorded, and neither should be on the scan's
        clock.

        A no-op if start_scan_monitoring was never called for this scan
        (no server, server not in use, or the start itself failed) - there
        is nothing to stop.

        Registers the file at `scan.set_scan_parameter("monitors",
        "aux/<filename>")` right after a successful dispatch - the same
        "register the path now, the job finishes later" pattern
        copy_aliases_to_scan/append_status_to_scan_and_store already use -
        so copy_scan_info_to_raw's scan_info_rel.json carries it the same
        way it already carries "aliases"/"status", regardless of whether
        the write+upload job has actually finished writing the file yet.

        Wired into `callbacks_end_scan`, right after
        `append_status_to_scan_and_store` and before `copy_scan_info_to_raw`
        (which needs the "monitors" scan parameter already set to write it
        into scan_info_rel.json).
        """
        if self.status_client is None:
            return None
        recording_id = scan.counter_scratch(self.name).get(
            "monitoring_recording_id"
        )
        if recording_id is None:
            return None
        if pgroup is None:
            pgroup = self.pgroup
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number.get_current_value()
        else:
            runno = self.daq.get_last_run_number()
        try:
            job = self.status_client.capture_recording(
                recording_id, pgroup, runno, upload=upload,
                filename="namespace_monitor.h5",
            )
        except Exception as exc:
            return self._status_server_failed("end monitoring", exc)
        scan.counter_scratch(self.name).setdefault("status_jobs", {})[
            "recording"
        ] = job
        scan.set_scan_parameter("monitors", "aux/namespace_monitor.h5")
        print(
            f"monitoring: recording '{recording_id}' stopped, capture job "
            f"{job.get('job_id')} -> {job.get('path')}"
        )
        return job

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
            scan.counter_scratch(self.name)["elog_id"] = elog_ids[1]

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
        monitors = scan.counter_scratch(self.name)["monitors"] = {}
        for adj in scan.adjustables:
            try:
                tname = adj.alias.get_full_name()
            except Exception:
                tname = adj.name
                traceback.print_exc()
            try:
                monitors[tname] = Monitor(adj.pvname)
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
                    monitors[rname] = Monitor(adj.readback.pvname)
            except Exception:
                print(f"Could not add CA readback monitor for {tname}")
                traceback.print_exc()

        for tname, tobj in custom_monitors.items():
            try:
                if type(tobj) is str:
                    tmonpv = tobj
                monitors[tname] = Monitor(tmonpv)
                print(f"Added custom monitor for {tname}")
            except Exception:
                print(f"Could not add custom monitor for {tname}")
                traceback.print_exc()
        try:
            tname = self.pulse_id.alias.get_full_name()
            monitors[tname] = Monitor(self.pulse_id.pvname)
        except Exception:
            print(f"Could not add daq.pulse_id monitor")
            traceback.print_exc()

    def end_scan_monitors(self, scan, pgroup=None, **kwargs):
        monitors = scan.counter_scratch(self.name)["monitors"]
        for tmon in monitors:
            monitors[tmon].stop_callback()

        monitor_result = {tmon: monitors[tmon].data for tmon in monitors}

        # save temprary file and send then to raw
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number.get_current_value()
        else:
            runno = self.get_last_run_number()

        if pgroup is None:
            pgroup = self.pgroup

        tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/run_data/daq/run{runno}/aux")
        ensure_dir(tmpdir)
        scanmonitorfile = tmpdir / Path("scan_monitor.pkl")
        if not Path(scanmonitorfile).exists():
            with open_group_writable(scanmonitorfile, "wb") as f:
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
