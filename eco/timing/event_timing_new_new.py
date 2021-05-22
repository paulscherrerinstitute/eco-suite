from epics import caget_many
from ..elements.adjustable import AdjustableMemory, AdjustableVirtual
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum, AdjustablePvString
from ..epics.detector import DetectorPvData, DetectorPvDataStream
from eco.epics.utilities_epics import EpicsString
import logging
from ..elements.assembly import Assembly
from tabulate import tabulate

logging.getLogger("cta_lib").setLevel(logging.WARNING)
from cta_lib import CtaLib


class TimingSystem(Assembly):
    """ This is a wrapper object for the global timing system at SwissFEL"""

    def __init__(self, pv_master=None, pv_pulse_id=None, name=None):
        super().__init__(name=name)
        self._append(
            MasterEventSystem, pv_master, name="event_master", is_status="recursive"
        )
        self._append(DetectorPvDataStream, pv_pulse_id, name="pulse_id")


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
            DetectorPvData, f"{self.pvname}:Evt-{slot_number}-Code-SP", name="code_number"
        )
        self._append(DetectorPvData, f"{self.pvname}:Evt-{slot_number}-Delay-RB", name="delay")
        self._append(
            DetectorPvData, f"{self.pvname}:Evt-{slot_number}-Freq-I", name="frequency"
        )
        self._append(
            AdjustablePvString, f"{self.pvname}:Evt-{slot_number}.DESC", name="description"
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
                is_status="recursive",
            )
            self.event_codes[code] = self.__dict__[f"code{code:03d}"]
        for code, delay in event_code_delays_fix.items():
            self._append(
                MasterEventCodeFix,
                code,
                delay,
                "fix delay CTA sequencer code",
                name=f"code{code:03d}",
                is_status="recursive",
            )
            self.event_codes[code] = self.__dict__[f"code{code:03d}"]

    def _get_slot_codes(self, slots=range(1, 257)):
        pvs = [f"{self.pvname}:Evt-{slot}-Code-SP" for slot in slots]
        codes = caget_many(pvs)

        slots_out = []
        codes_out = []
        for s, c in zip(slots, codes):
            if not c == None:
                if c in codes_out:
                    print(f"Code {c} exists multiple times!")
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
        s = tabulate(o, ["Code", "Delay / us", "Freq. / Hz", "Description"], "simple")
        if printit:
            print(s)
        else:
            return s

    def __repr__(self):
        return self.status(printit=False)


class EvrPulser(Assembly):
    def __init__(self, pv_base, event_master, name=None):
        super().__init__(name=name)
        self.pv_base = pv_base
        self._event_master = event_master

        self._append(AdjustablePvString, pv_base + "-Name-I", name="description", is_status=True)
        self._append(
            AdjustablePvEnum, f"{self.pv_base}-Polarity-Sel", name="polarity", is_setting=True
        )
        self._append(AdjustablePvEnum, f"{self.pv_base}-Ena-Sel", name="enable", is_setting=True)
        self._append(
            AdjustablePv, f"{self.pv_base}-Evt-Trig0-SP", name="eventcode", is_setting=True
        )
        self._append(
            AdjustablePv, f"{self.pv_base}-Evt-Set0-SP", name="event_set", is_setting=True
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
        self._append(
            AdjustableVirtual,
            [self._eventcode.frequency],
            lambda x: x,
            lambda x: x,
            name="frequency",
        )
        self._append(
            AdjustableVirtual,
            [self._eventcode.delay],
            lambda x: x,
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

    @property
    def _eventcode(self):
        return self._event_master.event_codes[self.eventcode.get_current_value()]


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


class EvrOutput(Assembly):
    def __init__(self, pv_base, pulsers=None, name=None):
        super().__init__(name=name)
        self.pv_base = pv_base
        self._pulsers = pulsers
        # self._update_connected_pulsers()
        self._append(AdjustablePvString, pv_base + "-Name-I", name="description", is_status=True)
        self._append(AdjustablePvEnum, f"{self.pv_base}-Ena-SP", name="enable", is_setting=True)
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
            AdjustableVirtual,
            [self.pulserA.delay_eventcode],
            lambda x: x,
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
            AdjustableVirtual,
            [self.pulserA.frequency],
            lambda x: x,
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
            AdjustableVirtual,
            [self.pulserB.delay_eventcode],
            lambda x: x,
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
            AdjustableVirtual,
            [self.pulserB.frequency],
            lambda x: x,
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

    @property
    def pulserA(self):
        try:
            return self._pulsers[self.pulserA_number.get_current_value()]
        except IndexError:
            return DummyPulser()

    @property
    def pulserB(self):
        try:
            return self._pulsers[self.pulserA_number.get_current_value()]
        except IndexError:
            return DummyPulser()


class EventReceiver(Assembly):
    def __init__(
        self,
        pvname,
        event_master,
        n_pulsers=24,
        n_output_front=8,
        n_output_rear=16,
        name=None,
    ):
        super().__init__(name=name)
        self.pvname = pvname
        pulsers = []
        for n in range(n_pulsers):
            self._append(
                EvrPulser,
                f"{self.pvname}:Pul{n}",
                event_master,
                name=f"pulser{n}",
                is_setting=True,
                is_status=False,
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
                is_status="recursive",
            )
            outputs.append(self.__dict__[f"output_front{n}"])
        for n in range(n_output_rear):
            self._append(
                EvrOutput,
                f"{self.pvname}:RearUniv{n}",
                pulsers=pulsers,
                name=f"output_rear{n}",
                is_setting=True,
                is_status="recursive",
            )
            outputs.append(self.__dict__[f"output_rear{n}"])
        # for to in outputs:
        #     to._pulsers = self.pulsers
        self.outputs = outputs

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
        s = tabulate(
            o,
            ["Output name", "Description", "On", "Pulsrs", "Freqs. / Hz", "EvtCds"],
            "simple",
        )
        if printit:
            print(s)
        else:
            return s

    def __repr__(self):
        return self.status(printit=False)


class CTA_sequencer:
    def __init__(self, Id, name=None, master_frequency=100):
        self._cta = CtaLib(Id)
        self.sequence_local = {}
        self.synced = False
        self._master_frequency = master_frequency

    def get_active_sequence(self):
        self.sequence_local = self._cta.download()
        self.length = self._cta.get_length()
        self.synced = True

    def upload_local_sequence(self):
        self._cta.upload(self.sequence_local)

    def get_start_config(self, set_params=True):
        cfg = self._cta.get_start_config()
        if set_params:
            self._start_immediately = cfg["mode"]
            self.start_divisor = cfg["divisor"]
            self.start_offset = cfg["offset"]
        else:
            return cfg

    def set_start_config(self, divisor, offset):
        if divisor == 1 and offset == 0:
            mode = 0
        else:
            mode = 1
        self._cta.set_start_config(
            config={
                "mode": self._cta.StartMode(mode),
                "modulo": divisor,
                "offset": offset,
            }
        )

    def reset_local_sequence(self):
        self.sequence_local = {}
        self.length = 0
        self.synced = False

    def append_singlecode(self, code, pulse_delay):
        if self.length == 0:
            self.length = 1
        if not code in self.sequence_local.keys():
            self.sequence_local[code] = self.length * [0]
        self.length += pulse_delay
        for tc in self.sequence_local.keys():
            self.sequence_local[tc].extend(pulse_delay * [0])
        self.sequence_local[code][self.length - 1] = 1
        self.synced = False

    def set_repetitions(self, n_rep):
        """Set the number of sequence repetitions, 0 is infinite repetitions"""
        ntim = int(n_rep > 0)
        self._cta.set_repetition_config(config={"mode": ntim, "n": n_rep})

    def get_repetitions(self):
        """Get the number of sequence repetitions, 0 is infinite repetitions"""
        repc = self._cta.get_repetition_config()
        if repc["mode"] == 0:
            return 0
        else:
            return repc["n"]

    def start(self):
        self._cta.start()

    def stop(self):
        self._cta.stop()
