from ..elements.assembly import Assembly
from ..aliases import Alias
from eco import ecocnf
from epics.pv import PV

# try:
#     from bsread.bsavail import pollStream
# except:
#     from bsread.unused.bsavail import pollStream
import requests
from bsread import dispatcher, source, DEFAULT_DISPATCHER_URL

# base url of the dispatcher REST api used to query the data policy / retention
# (ttl) of a bs channel via GET <base>/data/policy/<channel>, e.g.
#   https://dispatcher-api.psi.ch/sf-databuffer/data/policy/SINBC01-DBPM030:Q1
DISPATCHER_API_URL = DEFAULT_DISPATCHER_URL
from ..epics_utils import get_from_archive
from escape import stream
from time import time, sleep
from eco.acquisition.utilities import Acquisition
from eco.acquisition.decorators import scannable
from eco.epics_utils.detector import CallbackEpics


_bs_event_worker = None


def _ensure_bs_event_worker():
    """Lazily create the one shared `escape.stream.EventWorker` every
    `DetectorBsStream.stream` needs, and register it as escape.stream's
    module-global fallback (`EventWorker(make_default=True)`).

    Must run *before* `stream.EventSource(channel, None)` is constructed
    below: `EventSource.__init__` only checks for that global fallback once,
    at construction time - it is not a live/deferred lookup. Previously
    nothing in eco ever called this ahead of device construction - the
    `bs_worker` lazy namespace entry in eco/bernina/bernina.py only got
    touched, if at all, from inside `timetool_data_monitor()`, long after
    the `DetectorBsStream` devices it was meant to serve were already built
    - so every `DetectorBsStream.stream.eventWorker` was permanently `None`
    and `.stream.accumulate()` failed with `AttributeError: 'NoneType'
    object has no attribute 'registerSource'`. Idempotent: only the first
    call actually builds a worker, so every `DetectorBsStream` in a session
    shares the same one.

    NOTE: `bs_worker` (eco/bernina/bernina.py) is now redundant - by the
    time anything could touch it, some `DetectorBsStream` has essentially
    always already called this and registered the shared worker - and is
    also a live footgun: touching it calls `EventWorker(make_default=True)`
    a *second* time, which unconditionally overwrites escape.stream's global
    fallback with a fresh, empty instance, silently orphaning every
    `DetectorBsStream.stream` already built against the first one (they keep
    their own reference and keep working, but nothing new picks up their
    worker - e.g. a fresh device built afterwards would get the new, empty
    one instead). Left untouched here deliberately (a separate, more central
    file) - flagged for a follow-up rather than changed unilaterally.
    """
    global _bs_event_worker
    if _bs_event_worker is None:
        _bs_event_worker = stream.EventWorker(make_default=True)
    return _bs_event_worker


@get_from_archive
@scannable
class DetectorBsStream:
    def __init__(self, bs_channel, cachannel="same", name=None):
        self.name = name
        self.bs_channel = bs_channel
        if cachannel == "same":
            self.pvname = bs_channel
        elif (not cachannel) or cachannel == "none":
            self.pvname = None
        else:
            self.pvname = cachannel
        if self.pvname:
            self._pv = PV(self.pvname, auto_monitor=False)
        self.alias = Alias(name, channel=bs_channel, channeltype="BS")

        _ensure_bs_event_worker()
        self.stream = stream.EscData(source=stream.EventSource(self.bs_channel, None))

    def bs_avail(self):
        return self.bs_channel in [
            tmp["name"] for tmp in dispatcher.get_current_channels()
        ]

    def get_live_info(self):
        """Return the dispatcher metadata for this channel if it is currently
        being streamed live (source, type, shape, modulo, ...), else None."""
        for ch in dispatcher.get_current_channels():
            if ch["name"] == self.bs_channel:
                return ch
        return None

    def get_databuffer_policy(self, base_url=DISPATCHER_API_URL):
        """Query the dispatcher data policy for this channel.

        Corresponds to the REST call
            GET <base_url>/data/policy/<channel>
        e.g. https://dispatcher-api.psi.ch/sf/data/policy/SINBC01-DBPM030:Q1

        Returns the parsed policy dict. The interesting fields are:
          - ``pattern``: the channel-name pattern this policy matched. A specific
            pattern means a dedicated policy is defined; ``"."`` is the catch-all
            default policy.
          - ``data_reduction``: retention/reduction stages, each with ``ttl``
            (seconds the data is kept) and ``modulo`` (1 = every pulse stored,
            n = every n-th pulse kept for that stage).
          - ``data_layout``: the storage backends (e.g. ``sf-databuffer``) and the
            data types written to each.
        """
        response = requests.get(f"{base_url}/data/policy/{self.bs_channel}")
        if not response.ok:
            raise Exception(
                f"Unable to retrieve data policy for {self.bs_channel} - {response.text}"
            )
        return response.json()

    def get_retention(self, base_url=DISPATCHER_API_URL):
        """Return the retention (ttl) stages of the databuffer policy as a list of
        dicts with ``ttl_s`` (seconds), ``ttl_days`` and ``modulo`` (data
        reduction factor), sorted from full-rate to most-reduced."""
        policy = self.get_databuffer_policy(base_url=base_url)
        stages = []
        for reduction in policy.get("data_reduction", {}).values():
            for stage in reduction:
                ttl = stage.get("ttl")
                stages.append(
                    {
                        "ttl_s": ttl,
                        "ttl_days": None if ttl is None else round(ttl / 86400, 2),
                        "modulo": stage.get("modulo"),
                    }
                )
        stages.sort(key=lambda s: (s["modulo"] is None, s["modulo"]))
        return stages

    def get_stream_properties(self, printit=True, base_url=DISPATCHER_API_URL):
        """Collect the properties of this bs channel: whether it is currently
        streamed live by the dispatcher, whether it is stored in a data buffer
        and with which retention policy (ttl).

        Returns a dict with the collected information. If ``printit`` a short
        human-readable summary is printed as well."""
        live_info = self.get_live_info()
        policy = self.get_databuffer_policy(base_url=base_url)
        retention = self.get_retention(base_url=base_url)
        backends = sorted(
            {layout.get("backend") for layout in policy.get("data_layout", [])}
        )
        # a specific (non catch-all) pattern means a dedicated policy is defined
        pattern = policy.get("pattern")
        dedicated_policy = bool(pattern) and pattern != "."

        info = {
            "channel": self.bs_channel,
            "live": live_info is not None,
            "live_info": live_info,
            "stored": bool(backends),
            "backends": backends,
            "policy_pattern": pattern,
            "dedicated_policy": dedicated_policy,
            "retention": retention,
            "policy": policy,
        }

        if printit:
            print(f"Bs channel: {self.bs_channel}")
            if live_info is not None:
                print(
                    "  live streaming : yes "
                    f"(type={live_info.get('type')}, shape={live_info.get('shape')}, "
                    f"source={live_info.get('source')})"
                )
            else:
                print("  live streaming : no (not in dispatcher live channels)")
            if backends:
                print(f"  stored in      : {', '.join(backends)}")
            else:
                print("  stored in      : no storage backend in policy")
            print(
                f"  policy pattern : {pattern}"
                f"{'' if dedicated_policy else '  (catch-all default policy)'}"
            )
            print("  retention (ttl):")
            for stage in retention:
                print(
                    f"    - {stage['ttl_days']:>7} days "
                    f"({stage['ttl_s']} s), keeping every {stage['modulo']} pulse(s)"
                )
        return info

    def get_current_value(self, force_bsstream=False):
        if not force_bsstream:
            if not hasattr(self, "_pv"):
                return None
            return self._pv.get()
        else:
            raise NotImplementedError(
                "setup of stream for bs channel not implemented yet"
            )

    # def get_stream_state(self, timeout=1):
    #     return pollStream(self.bs_channel, timeout=1)

    def create_stream_callback(self, foo):
        with source(channels=[self.bs_channel]) as s:
            done = False
            while not done:
                done = foo(s.receive())

    def collect(self, seconds=None, samples=None):
        if (not seconds) and (not samples):
            raise Exception(
                "Either a time interval or number of samples need to be defined."
            )
        try:
            self._pv.callbacks.pop(self._collection["ix_cb"])
        except:
            pass
        self._collection = {"done": False}
        self.data_collected = []
        if seconds:
            self._collection["start_time"] = time()
            self._collection["seconds"] = seconds
            stopcond = (
                lambda: (time() - self._collection["start_time"])
                > self._collection["seconds"]
            )

            def addData(**kw):
                if not stopcond():
                    self.data_collected.append(kw["value"])
                else:
                    try:
                        self._pv.callbacks.pop(self._collection["ix_cb"])
                    except:
                        pass
                    self._collection["done"] = True

        elif samples:
            self._collection["samples"] = samples
            stopcond = lambda: len(self.data_collected) >= self._collection["samples"]

            def addData(**kw):
                self.data_collected.append(kw["value"])
                if stopcond():
                    try:
                        self._pv.callbacks.pop(self._collection["ix_cb"])
                    except:
                        pass
                    self._collection["done"] = True

        self._collection["ix_cb"] = self._pv.add_callback(addData)
        time_wait_start = time()
        while not self._collection["done"]:
            sleep(0.005)
            if seconds:
                if (time() - time_wait_start) > seconds:
                    if len(self.data_collected) == 0:
                        print(
                            f"No {self.name}({self.pvname}) data update in time interval, reporting last value"
                        )
                        self._pv.callbacks.pop(self._collection["ix_cb"])
                        self.data_collected.append(self.get_current_value())
                        break

        return self.data_collected

    def acquire(self, hold=False, seconds=None, samples=None, **kwargs):
        return Acquisition(
            acquire=lambda: self.collect(seconds=seconds, samples=samples, **kwargs),
            hold=hold,
            stopper=None,
            get_result=lambda: self.data_collected,
        )

    def accumulate_ring_buffer(self, n_buffer):
        if not hasattr(self, "_accumulate"):
            self._accumulate = {"n_buffer": n_buffer, "ix": 0, "n_cb": -1}
        else:
            self._accumulate["n_buffer"] = n_buffer
            self._accumulate["ix"] = 0
        self._pv.callbacks.pop(self._accumulate["n_cb"], None)
        self._data = np.squeeze(np.zeros([n_buffer * 2, self._pv.count])) * np.nan

        def addData(**kw):
            self._accumulate["ix"] = (self._accumulate["ix"] + 1) % self._accumulate[
                "n_buffer"
            ]
            self._data[self._accumulate["ix"] :: self._accumulate["n_buffer"]] = kw[
                "value"
            ]

        self._accumulate["n_cb"] = self._pv.add_callback(addData)

    def accumulate_start(self):
        if not hasattr(self, "_accumulate_inf"):
            self._accumulate_inf = {"n_cb": -1}
        self._pv.callbacks.pop(self._accumulate_inf["n_cb"], None)
        self._data_inf = []

        def addData(**kw):
            self._data_inf.append(kw["value"])

        self._accumulate_inf["n_cb"] = self._pv.add_callback(addData)
        self._pv.auto_monitor = True

    def accumulate_stop(self):
        self._pv.callbacks.pop(self._accumulate_inf["n_cb"], None)
        self._pv.auto_monitor = False
        return self._data_inf

    @property
    def data(self):
        return self._data[
            self._accumulate["ix"]
            + 1 : self._accumulate["ix"]
            + 1
            + self._accumulate["n_buffer"]
        ]

    def set_current_value_callback(
        self, func="accumulate", run_once=True, print_output=False, **kwargs
    ):
        if hasattr(self, "_pv"):
            return CallbackEpics(
                self._pv,
                func=func,
                run_once=run_once,
                print_output=print_output,
                **kwargs,
            )


@get_from_archive
class DetectorBsCam:
    def __init__(self, bschannel, name=None):
        self.name = name
        self.bschannel = bschannel
        self.alias = Alias(name, channel=bschannel, channeltype="BSCAM")
