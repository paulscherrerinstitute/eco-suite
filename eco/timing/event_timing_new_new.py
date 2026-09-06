import time

from epics import PV, caget_many
from ..elements.adjustable import AdjustableMemory, AdjustableVirtual
from ..elements.detector import DetectorVirtual
from ..epics_utils.adjustable import (
    AdjustablePv,
    AdjustablePvEnum,
    AdjustablePvString,
    read_pv_value,
)
from ..epics_utils.detector import DetectorPvData, DetectorPvDataStream
from ..detector.detectors_psi import DetectorBsStream
from eco.epics_utils.utilities_epics import EpicsString
import logging
from ..elements.assembly import Assembly
from ..utilities.tables import format_table

logging.getLogger("cta_lib").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class TimingSystem(Assembly):
    """This is a wrapper object for the global timing system at SwissFEL"""

    def __init__(self, pv_master=None, pv_pulse_id=None, pv_eventset=None, name=None):
        super().__init__(name=name)
        self._append(MasterEventSystem, pv_master, name="event_master", is_display=True)
        # self._append(DetectorPvDataStream, pv_pulse_id, name="pulse_id")
        self._append(
            DetectorBsStream, "pulse_id", cachannel=pv_pulse_id, name="pulse_id"
        )
        self._append(DetectorBsStream, "lab_time", cachannel=None, name="lab_time")

        if pv_eventset:
            self._append(DetectorBsStream, pv_eventset, cachannel=None, name="eventset")


# EVR output mapping
evr_mapping = {
    0: "Pulser 0",
    1: "Pulser 1",
    2: "Pulser 2",
    3: "Pulser 3",
    4: "Pulser 4",
    5: "Pulser 5",
    6: "Pulser 6",
    7: "Pulser 7",
    8: "Pulser 8",
    9: "Pulser 9",
    10: "Pulser 10",
    11: "Pulser 11",
    12: "Pulser 12",
    13: "Pulser 13",
    14: "Pulser 14",
    15: "Pulser 15",
    16: "Pulser 16",
    17: "Pulser 17",
    18: "Pulser 18",
    19: "Pulser 19",
    20: "Pulser 20",
    21: "Pulser 21",
    22: "Pulser 22",
    23: "Pulser 23",
    32: "Distributed bus bit 0",
    33: "Distributed bus bit 1",
    34: "Distributed bus bit 2",
    35: "Distributed bus bit 3",
    36: "Distributed bus bit 4",
    37: "Distributed bus bit 5",
    38: "Distributed bus bit 6",
    39: "Distributed bus bit 7",
    40: "Prescaler 0",
    41: "Prescaler 1",
    42: "Prescaler 2",
    62: "Logic High",
    63: "Logic low",
}


# temporary mapping of Ids to codes, be aware of changes!
eventcodes = [
    1,
    2,
    3,
    4,
    5,
    0,
    6,
    7,
    8,
    12,
    0,
    11,
    9,
    10,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    41,
    42,
    43,
    44,
    45,
    46,
    47,
    48,
    49,
    50,
]

event_code_delays_fix = {
    200: 100,
    201: 107,
    202: 114,
    203: 121,
    204: 128,
    205: 135,
    206: 142,
    207: 149,
    208: 156,
    209: 163,
    210: 170,
    211: 177,
    212: 184,
    213: 191,
    214: 198,
    215: 205,
    216: 212,
    217: 219,
    218: 226,
    219: 233,
}

tim_tick = 7e-9


class MasterEventCode(Assembly):
    def __init__(self, pvname, slot_number, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._slot_number = slot_number
        self._append(
            DetectorPvData,
            f"{self.pvname}:Evt-{slot_number}-Code-SP",
            name="code_number",
        )
        self._append(
            DetectorPvData, f"{self.pvname}:Evt-{slot_number}-Delay-RB", name="delay"
        )
        self._append(
            DetectorPvData, f"{self.pvname}:Evt-{slot_number}-Freq-I", name="frequency"
        )
        self._append(
            AdjustablePvString,
            f"{self.pvname}:Evt-{slot_number}.DESC",
            name="description",
        )


class MasterEventCodeFix(Assembly):
    def __init__(self, code_number, delay, description="fixed event code", name=None):
        super().__init__(name=name)
        self._append(AdjustableMemory, delay, name="code_number")
        self._append(AdjustableMemory, delay, name="delay")
        self._append(AdjustableMemory, None, name="frequency")
        self._append(AdjustableMemory, description, name="description")


class MasterEventSystem(Assembly):
    def __init__(self, pvname="SIN-TIMAST-TMA", name=None):
        super().__init__(name=name)
        self.pvname = pvname
        slots, codes = self._get_slot_codes()
        self.event_codes = {}
        for slot, code in zip(slots, codes):
            self._append(
                MasterEventCode,
                self.pvname,
                slot,
                name=f"code{code:03d}",
                is_display="recursive",
            )
            self.event_codes[code] = self.__dict__[f"code{code:03d}"]
        for code, delay in event_code_delays_fix.items():
            self._append(
                MasterEventCodeFix,
                code,
                delay,
                "fix delay CTA sequencer code",
                name=f"code{code:03d}",
                is_display="recursive",
            )
            self.event_codes[code] = self.__dict__[f"code{code:03d}"]

    def _get_slot_codes(self, slots=range(1, 257), attempts=3, timeout=3.0,
                        connect_timeout=1.0):
        """Read the master's slot->event-code table.

        `caget_many` reports a slot it could not read as `None`, and those
        used to be dropped silently. Whichever init worker builds this
        component does so while the rest of the pass saturates channel
        access, so under `init_all()` a handful of slots could time out and
        the resulting `event_codes` dict stayed permanently incomplete for
        the life of the session - after which every EVR pulser wired to a
        dropped code came up without its delay/frequency chain, blaming "code
        missing in Timing Master" for what was really a timed-out read.
        Retry the missing ones and say so if any are still missing, rather
        than quietly shipping a partial table.

        **A slot that is simply not configured is not a failure.** Measured
        on SIN-TIMAST-TMA (2026-09-06): exactly 74 of the 256 slots exist;
        the other 182 have no record on the IOC at all - their PVs do not
        connect even given 5 s on a completely idle network, and the count
        is identical inside and outside `init_all()`. Retrying those is
        pointless (three `caget_many` passes over 182 non-existent channels
        cost ~15 s of every namespace init) and warning about them is a false
        alarm that has been firing at every session start.

        So the two cases are separated by *connection*, which is the only
        thing that distinguishes them - `caget_many` reports both as `None`.
        A channel that does not connect is an unconfigured slot: skipped
        silently. A channel that connects but whose read came back empty is
        a genuine timed-out read, which is what the retry and the warning
        are for.
        """
        slots = list(slots)
        pvs = [f"{self.pvname}:Evt-{slot}-Code-SP" for slot in slots]
        codes = list(caget_many(pvs, timeout=timeout))

        missing = [i for i, c in enumerate(codes) if c is None]
        unconfigured = set()
        if missing:
            # Create the channels non-blockingly and let libca resolve them
            # in the background, then ask once - far cheaper than a
            # wait_for_connection() per channel.
            probes = {i: PV(pvs[i], connection_timeout=connect_timeout,
                            auto_monitor=False) for i in missing}
            deadline = time.time() + connect_timeout
            while time.time() < deadline and not all(
                p.connected for p in probes.values()
            ):
                time.sleep(0.05)
            unconfigured = {i for i, p in probes.items() if not p.connected}

        retryable = [i for i in missing if i not in unconfigured]
        for _ in range(max(int(attempts) - 1, 0)):
            if not retryable:
                break
            retried = caget_many([pvs[i] for i in retryable], timeout=timeout)
            for i, c in zip(retryable, retried):
                codes[i] = c
            retryable = [i for i in retryable if codes[i] is None]

        if unconfigured:
            logger.debug(
                "timing master %s: %d of %d event-code slots are not "
                "configured (no record on the IOC) and were skipped; %d in "
                "use.",
                self.pvname, len(unconfigured), len(slots),
                len(slots) - len(unconfigured),
            )
        still_missing = [slots[i] for i in retryable]
        if still_missing:
            logger.warning(
                "timing master %s: %d of %d event-code slots connected but "
                "could not be read after %d attempts (slots %s%s); event "
                "codes served by them will look missing to every EVR pulser "
                "using them.",
                self.pvname,
                len(still_missing),
                len(slots),
                attempts,
                ", ".join(str(x) for x in still_missing[:10]),
                ", ..." if len(still_missing) > 10 else "",
            )

        slots_out = []
        codes_out = []
        for s, c in zip(slots, codes):
            if not c == None:
                if c in codes_out:
                    # print(f"Code {c} exists multiple times!")
                    continue
                slots_out.append(s)
                codes_out.append(c)

        codes_out, slots_out = zip(*sorted(zip(codes_out, slots_out)))
        return slots_out, codes_out

    def status(self, code=None, printit=True):
        if code == None:
            code = self.event_codes.keys()
        else:
            try:
                iter(code)
            except TypeError:
                code = [code]

        o = []
        for cod in code:
            tc = self.__dict__[f"code{cod:03d}"]
            o.append([cod, tc.delay(), tc.frequency(), tc.description()])
        s = format_table(
            o, headers=["Code", "Delay / us", "Freq. / Hz", "Description"], tablefmt="simple"
        )
        if printit:
            print(s)
        else:
            return s

    def __repr__(self):
        return self.status(printit=False)

class EvrSequencer(Assembly):
    def __init__(self, pv_base, name=None):
        super().__init__(name=name)
        try:
            self._append(AdjustablePvEnum, pv_base + ':SEQ_SOURCE', name='source', is_display=True, is_setting=True)
            self._append(AdjustablePvEnum, pv_base + ':SEQ_SNUMPD', name='pulser_number', is_display=True, is_setting=True)
            self._append(DetectorPvData, pv_base + ':SEQ_RUNNING', name='is_running', is_display=True)
            self._append(AdjustablePvEnum, pv_base + ':Seq-Ena-Sel', name='enabled', is_display=True, is_setting=True)
            self._append(DetectorPvData, pv_base + ':SEQ_SELECT_FREQ', name='frequency', is_display=True)
        except:
            print(f'The evr sequencer of {pv_base} is likely old type')
            self._append(AdjustableMemory, None, name='frequency', is_display=True)
        
        self._append(AdjustablePvEnum, pv_base + ':Seq-RunMode-Sel', name='mode', is_display=True, is_setting=True)
        
        
        self._append(AdjustablePv, pv_base + ':SEQ_DELAY', name='delay', is_display=True, is_setting=True)
        self._append(AdjustablePv, pv_base + ':SEQ_REPS', name='repetitions', is_display=True, is_setting=True)
        self._append(AdjustablePv, pv_base + ':SEQ_MULTIPLIER', name='freq_multiplier', is_display=True, is_setting=True)


class EvrPulser(Assembly):
    def __init__(self, pv_base, event_master, parent_evr=None, name=None):
        super().__init__(name=name)
        self.pv_base = pv_base
        self._event_master = event_master
        self._parent_evr = parent_evr

        self._append(
            AdjustablePvString, pv_base + "-Name-I", name="description", is_display=True
        )
        self._append(
            AdjustablePvEnum,
            f"{self.pv_base}-Polarity-Sel",
            name="polarity",
            is_setting=True,
        )
        self._append(
            AdjustablePvEnum, f"{self.pv_base}-Ena-Sel", name="enable", is_setting=True
        )
        self._append(
            AdjustablePv,
            f"{self.pv_base}-Evt-Trig0-SP",
            name="eventcode",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            f"{self.pv_base}-Evt-Set0-SP",
            name="event_set",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            f"{self.pv_base}-Evt-Reset0-SP",
            name="event_reset",
            is_setting=False,
        )

        self._append(
            AdjustablePv,
            f"{self.pv_base}-Delay-SP",
            pvreadbackname=f"{self.pv_base}-Delay-RB",
            name="delay_pulser",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            f"{self.pv_base}-Width-SP",
            pvreadbackname=f"{self.pv_base}-Width-RB",
            name="width",
            is_setting=True,
        )
        self.description = EpicsString(pv_base + "-Name-I")

        # Resolve the event code ONCE and keep it. It used to be a property
        # re-issuing a CA get on every access, and __init__ touched it three
        # times (the `is not None` guard, then .frequency, then .delay): under
        # a concurrent init_all() the guard could pass and the next access
        # come back None (timed-out get -> None -> KeyError -> None), giving
        # `AttributeError: 'NoneType' object has no attribute 'frequency'` on
        # a pulser that initializes fine on its own. One read, one value, no
        # window between the check and the use.
        self._eventcode_resolved = self._resolve_eventcode()

        if self._eventcode_resolved is not None:
            self._append(
                DetectorVirtual,
                [self._eventcode_resolved.frequency],
                lambda x: x,
                name="frequency",
            )
            self._append(
                DetectorVirtual,
                [self._eventcode_resolved.delay],
                lambda x: x,
                name="delay_eventcode",
            )
            self._append(
                AdjustableVirtual,
                [self.delay_pulser],
                lambda tp: self.delay_eventcode.get_current_value() + tp,
                lambda x: x - self.delay_eventcode.get_current_value(),
                name="delay",
            )
        else:
            logger.warning(
                "pulser %s of EVR %s: event code %s is missing in the timing "
                "master; delay/frequency are not available on it.",
                self.name,
                self.pv_base,
                self._eventcode_number,
            )

    def _resolve_eventcode(self, timeout=3.0, attempts=3):
        """The timing-master event code object this pulser is wired to.

        The read is done through `read_pv_value` rather than
        `get_current_value()` so a channel that has not connected yet raises
        instead of yielding `None` - a `None` here used to be looked up in
        `event_codes`, miss, and silently leave the pulser without its
        delay/frequency chain, which then broke every EvrOutput pointing at
        it. Returns None only when the code is genuinely absent from the
        master.
        """
        try:
            self._eventcode_number = read_pv_value(
                self.eventcode,
                timeout=timeout,
                attempts=attempts,
                description=f"event code of pulser {self.name} ({self.pv_base})",
            )
        except TimeoutError as e:
            # Degrade the way this always did (no delay/frequency chain)
            # rather than failing the whole pulser - a PV that genuinely
            # isn't there must not turn into a wall of new failures. The
            # difference is that it now says so instead of silently looking
            # up event code `None` and reporting it as missing from the
            # master.
            logger.warning("%s", e)
            self._eventcode_number = None
            return None
        if self._eventcode_number == 27:
            return self._parent_evr.sequencer
        return self._event_master.event_codes.get(self._eventcode_number)

    def update_eventcode(self):
        """Re-read the event code from the IOC (it is otherwise resolved once,
        at construction). Use after re-wiring the pulser externally."""
        self._eventcode_resolved = self._resolve_eventcode()
        return self._eventcode_resolved

    @property
    def _eventcode(self):
        return self._eventcode_resolved


class DummyPulser(Assembly):
    def __init__(self, name="dummy"):
        super().__init__(name=name)
        self._append(AdjustableMemory, None, name="delay")
        self._append(AdjustableMemory, None, name="delay_pulser")
        self._append(AdjustableMemory, None, name="delay_eventcode")
        self._append(AdjustableMemory, None, name="eventcode")
        self._append(AdjustableMemory, None, name="frequency")
        self._append(AdjustableMemory, None, name="enable")
        self._append(AdjustableMemory, None, name="polarity")
        self._append(AdjustableMemory, None, name="width")


_shared_dummy_pulser = None


def _get_shared_dummy_pulser():
    """The one `DummyPulser` instance for the whole process.

    An out-of-range pulser number is a routine, expected IOC state (an unwired
    output) rather than a per-output failure, so there is nothing output- or
    EVR-specific to preserve by giving each affected output its own instance.
    A full `init_all()` can hit this on a few dozen outputs at once (as it does
    on the real Bernina EVR0, all wired to the sentinel 65535), and each fresh
    `DummyPulser()` builds and appends eight `AdjustableMemory` children for no
    behavioural difference from any other dummy -- one shared, lazily-built
    instance avoids that multiplied-by-outputs construction cost.
    """
    global _shared_dummy_pulser
    if _shared_dummy_pulser is None:
        _shared_dummy_pulser = DummyPulser()
    return _shared_dummy_pulser


class EvrOutput(Assembly):
    def __init__(self, pv_base, pulsers=None, name=None):
        super().__init__(name=name)
        self.pv_base = pv_base
        self._pulsers = pulsers
        # self._update_connected_pulsers()
        self._append(
            AdjustablePvString, pv_base + "-Name-I", name="description", is_display=True
        )
        self._append(
            AdjustablePvEnum, f"{self.pv_base}-Ena-SP", name="enable", is_setting=True
        )
        # self._append(
        # PvEnum,
        # f"{self.pv_base}_SOURCE",
        # name="sourceA",
        # is_setting=True,
        # )
        self._append(
            AdjustablePv,
            f"{self.pv_base}-Src-Pulse-SP",
            f"{self.pv_base}-Src-Pulse-RB",
            name="pulserA_number",
            is_setting=True,
        )
        # resolve once, before the eight virtuals below reach for it
        self._pulserA_resolved = self._resolve_pulser(self.pulserA_number, "pulserA")
        self._append(
            AdjustableVirtual,
            [self.pulserA.delay],
            lambda x: x,
            lambda x: x,
            name="pulserA_delay",
        )
        self._append(
            AdjustableVirtual,
            [self.pulserA.delay_pulser],
            lambda x: x,
            lambda x: x,
            name="pulserA_delay_pulser",
        )
        self._append(
            DetectorVirtual,
            [self.pulserA.delay_eventcode],
            lambda x: x,
            name="pulserA_delay_eventcode",
        )
        self._append(
            AdjustableVirtual,
            [self.pulserA.eventcode],
            lambda x: x,
            lambda x: x,
            name="pulserA_eventcode",
        )
        self._append(
            DetectorVirtual,
            [self.pulserA.frequency],
            lambda x: x,
            name="pulserA_frequency",
        )
        self._append(
            AdjustableVirtual,
            [self.pulserA.enable],
            lambda x: x,
            lambda x: x,
            name="pulserA_enable",
        )
        self._append(
            AdjustableVirtual,
            [self.pulserA.polarity],
            lambda x: x,
            lambda x: x,
            name="pulserA_polarity",
        )
        self._append(
            AdjustableVirtual,
            [self.pulserA.width],
            lambda x: x,
            lambda x: x,
            name="pulserA_width",
        )
        # self._append(
        # PvEnum,
        # f"{self.pv_base}_SOURCE2",
        # name="sourceB",
        # is_setting=True,
        # )

        self._append(
            AdjustablePv,
            f"{self.pv_base}-Src2-Pulse-SP",
            f"{self.pv_base}-Src2-Pulse-RB",
            name="pulserB_number",
            is_setting=True,
        )
        # NB: this used to read pulserA_number - every pulserB_* virtual on
        # every output was silently tracking pulser A.
        self._pulserB_resolved = self._resolve_pulser(self.pulserB_number, "pulserB")
        self._append(
            AdjustableVirtual,
            [self.pulserB.delay],
            lambda x: x,
            lambda x: x,
            name="pulserB_delay",
        )
        self._append(
            AdjustableVirtual,
            [self.pulserB.delay_pulser],
            lambda x: x,
            lambda x: x,
            name="pulserB_delay_pulser",
        )
        self._append(
            DetectorVirtual,
            [self.pulserB.delay_eventcode],
            lambda x: x,
            name="pulserB_delay_eventcode",
        )
        self._append(
            AdjustableVirtual,
            [self.pulserB.eventcode],
            lambda x: x,
            lambda x: x,
            name="pulserB_eventcode",
        )
        self._append(
            DetectorVirtual,
            [self.pulserB.frequency],
            lambda x: x,
            name="pulserB_frequency",
        )
        self._append(
            AdjustableVirtual,
            [self.pulserB.enable],
            lambda x: x,
            lambda x: x,
            name="pulserB_enable",
        )
        self._append(
            AdjustableVirtual,
            [self.pulserB.polarity],
            lambda x: x,
            lambda x: x,
            name="pulserB_polarity",
        )
        self._append(
            AdjustableVirtual,
            [self.pulserB.width],
            lambda x: x,
            lambda x: x,
            name="pulserB_width",
        )

    def _resolve_pulser(self, number_adj, which, timeout=3.0, attempts=3):
        """The pulser object this output is wired to, read once.

        Two bugs used to live here. The read went through
        `get_current_value()`, which returns None on a timed-out CA get, and
        `self._pulsers[None]` raised TypeError, which was caught and turned
        into a *fresh* `DummyPulser()`. And because this was a property,
        `__init__` re-read it once per virtual it builds - eight times for
        pulserA, eight for pulserB - so under a concurrent init_all() a
        single output could end up with its eight `pulserA_*` virtuals bound
        to several different objects, some of them throwaway dummies whose
        values are permanently None. That mis-wiring never failed and never
        printed anything.

        `read_pv_value` raises rather than yielding None, and the result is
        resolved once and cached; an out-of-range number still degrades to a
        DummyPulser, since that is a real (if odd) IOC state rather than a
        transport failure.
        """
        try:
            number = read_pv_value(
                number_adj,
                timeout=timeout,
                attempts=attempts,
                description=f"{which} number of output {self.name} ({self.pv_base})",
            )
        except TimeoutError as e:
            logger.warning("%s", e)
            number = None
        try:
            return self._pulsers[number]
        except (IndexError, TypeError):
            # An unwired output (number outside the EVR's pulser range, e.g.
            # the 65535 sentinel) is routine IOC state, not a failure worth
            # surfacing by default -- see the docstring above and the shared
            # dummy singleton this returns.
            logger.debug(
                "output %s (%s): %s number %r does not address any of the %d "
                "pulsers of this EVR; using a dummy pulser.",
                self.name,
                self.pv_base,
                which,
                number,
                len(self._pulsers or ()),
            )
            return _get_shared_dummy_pulser()

    def update_pulsers(self):
        """Re-read which pulsers this output is wired to (they are otherwise
        resolved once, at construction). Use after re-wiring the output."""
        self._pulserA_resolved = self._resolve_pulser(self.pulserA_number, "pulserA")
        self._pulserB_resolved = self._resolve_pulser(self.pulserB_number, "pulserB")

    @property
    def pulserA(self):
        return self._pulserA_resolved

    @property
    def pulserB(self):
        return self._pulserB_resolved


class EventReceiver(Assembly):
    def __init__(
        self,
        pvname,
        event_master,
        n_pulsers=24,
        n_output_front=8,
        n_output_rear=16,
        has_evr_sequencer=True,
        name=None,
    ):
        super().__init__(name=name)
        self.pvname = pvname

        if has_evr_sequencer:
            self._append(EvrSequencer,self.pvname,name='sequencer', is_display=True, is_setting=True)
        

        pulsers = []
        for n in range(n_pulsers):
            self._append(
                EvrPulser,
                f"{self.pvname}:Pul{n}",
                event_master,
                parent_evr=self,
                name=f"pulser{n}",
                is_setting=True,
                is_display=False,
            )
            pulsers.append(self.__dict__[f"pulser{n}"])
        self.pulsers = tuple(pulsers)
        outputs = []
        for n in range(n_output_front):
            self._append(
                EvrOutput,
                f"{self.pvname}:FrontUnivOut{n}",
                pulsers=pulsers,
                name=f"output_front{n}",
                is_setting=True,
                is_display="recursive",
            )
            outputs.append(self.__dict__[f"output_front{n}"])
        for n in range(n_output_rear):
            self._append(
                EvrOutput,
                f"{self.pvname}:RearUniv{n}",
                pulsers=pulsers,
                name=f"output_rear{n}",
                is_setting=True,
                is_display="recursive",
            )
            outputs.append(self.__dict__[f"output_rear{n}"])
        # for to in outputs:
        #     to._pulsers = self.pulsers
        self.outputs = outputs

        

        self._append(
            AdjustablePv,
            self.pvname + ":SYSRESET",
            is_status=False,
            is_setting=False,
            name="restart_ioc_pv",
        )

    def restart_ioc(self):
        self.restart_ioc_pv.set_target_value(1)

    def gui(self):
        dev = self.pvname.split("-")[-1]
        sys = self.pvname[: -(len(dev) + 1)]
        ioc = self.pvname
        self._run_cmd(
            f"caqtdm -noMsg  -macro IOC={ioc},SYS={sys},DEVICE={dev}  /sf/laser/config/qt/S_LAS-TMAIN.ui"
        )

    def status(self, printit=True):
        o = []
        for output in self.outputs:
            o.append(
                [
                    output.name,
                    output.description(),
                    output.enable(),
                    f"{output.pulserA_number()}/{output.pulserA_number()}",
                    f"{output.pulserA_frequency()}/{output.pulserA_frequency()}",
                    f"{output.pulserA_eventcode()}/{output.pulserA_eventcode()}",
                ]
            )
        s = format_table(
            o,
            headers=["Output name", "Description", "On", "Pulsers", "Freqs. / Hz", "EvtCds"],
            tablefmt="simple",
        )
        if printit:
            print(s)
        else:
            return s

    def __repr__(self):
        return self.status(printit=False)
