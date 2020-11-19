from epics import PV
from ..aliases import Alias, append_object_to_object
from ..utilities.lazy_proxy import Proxy
from ..devices_general.adjustable import PvEnum, PvRecord
from ..devices_general.detectors import PvDataStream
from ..eco_epics.utilities_epics import EpicsString
import logging
from ..elements.assembly import Assembly

logging.getLogger("cta_lib").setLevel(logging.WARNING)
from cta_lib import CtaLib
from numbers import Number


class TimingSystem:
    """ This is a wrapper object for the global timing system at SwissFEL"""

    def __init__(self, pv_master=None, pv_pulse_id=None):
        self.event_master = MasterEventSystem(pv_master, name="event_master")
        self.pulse_id = PvDataStream(pv_pulse_id, name="pulse_id")


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


class MasterEventSystem(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname

        self._pvs = {}

    def _get_pv(self, pvname):
        if not pvname in self._pvs:
            self._pvs[pvname] = PV(pvname)
        return self._pvs[pvname]

    def _get_Id_code(self, intId):
        return self._get_pv(f"{self.pvname}:Evt-{intId}-Code-SP").get()

    def _get_Id_freq(self, intId):
        return self._get_pv(f"{self.pvname}:Evt-{intId}-Freq-I").get()

    def _get_Id_period(self, intId):
        return self._get_pv(f"{self.pvname}:Evt-{intId}-Period-I").get()

    def _get_Id_delay(self, intId, inticks=False):
        """in seconds if not ticks"""
        if inticks:
            return self._get_pv(f"{self.pvname}:Evt-{intId}-Delay-RB.A").get()
        else:
            return self._get_pv(f"{self.pvname}:Evt-{intId}-Delay-RB").get() / 1_000_000

    def _get_Id_description(self, intId):
        return self._get_pv(f"{self.pvname}:Evt-{intId}.DESC").get()

    def _get_evtcode_Id(self, evtcode):
        if not evtcode in eventcodes:
            raise Exception(f"Eventcode mapping not defined for {evtcode}")
        Id = eventcodes.index(evtcode) + 1
        if not self._get_Id_code(Id) == evtcode:
            raise Exception(f"Eventcode mapping has apparently changed!")
        return Id

    def get_evtcode_delay(self, evtcode, **kwargs):
        if evtcode in event_code_delays_fix.keys():
            return event_code_delays_fix[evtcode] * tim_tick
        Id = self._get_evtcode_Id(evtcode)
        return self._get_Id_delay(Id, **kwargs)

    def get_evtcode_description(self, evtcode):
        Id = self._get_evtcode_Id(evtcode)
        return self._get_Id_description(Id)

    def get_evtcode_frequency(self, evtcode):
        """ in Hz"""
        Id = self._get_evtcode_Id(evtcode)
        return self._get_Id_freq(Id)

    def get_evtcode_period(self, evtcode):
        """ in s"""
        Id = self._get_evtcode_Id(evtcode)
        return self._get_Id_period(Id)

    def get_evt_code_status(self, codes=None):
        if not codes:
            codes = sorted(eventcodes)
        if isinstance(codes, Number):
            codes = [codes]
        s = []
        for c in codes:
            s.append(
                f"{c:3d}: delay = {self.get_evtcode_delay(c)*1e6:9.3f} us; frequency: {self.get_evtcode_frequency(c):5.1f} Hz; Desc.: {self.get_evtcode_description(c)}"
            )
        return s

    def status(self, codes=None):
        print("\n".join(self.get_evt_code_status(codes))) / 1000


class EvrPulser(Assembly):
    def __init__(self, pv_base, name=None):
        super().__init__(name=name)
        self.pv_base = pv_base
        self._append(
            PvEnum, f"{self.pv_base}-Polarity-Sel", name="polarity", is_setting=True
        )
        self._append(
            PvEnum, f"{self.pv_base}-Ena-Sel", name="enable", is_setting=True
        )
        self._append(
            PvRecord, f"{self.pv_base}-Evt-Trig0-SP", name="eventcode", is_setting=True
        )
        self._append(
            PvRecord, f"{self.pv_base}-Evt-Set0-SP", name="event_set", is_setting=True
        )
        self._append(
            PvRecord,
            f"{self.pv_base}-Evt-Reset0-SP",
            name="event_reset",
            is_setting=False,
        )

        self._append(
            PvRecord,
            f"{self.pv_base}-Delay-SP",
            pvreadbackname=f"{self.pv_base}-Delay-RB",
            name="delay",
            is_setting=True,
        )
        self._append(
            PvRecord,
            f"{self.pv_base}-Width-SP",
            pvreadbackname=f"{self.pv_base}-Width-RB",
            name="width",
            is_setting=True,
        )
        self.description = EpicsString(pv_base + "-Name-I")


class EvrOutput(Assembly):
    def __init__(self, pv_base, pulsers=None, name=None):
        super().__init__(name=name)
        self.pv_base = pv_base
        self._pulsers = pulsers
        # self._update_connected_pulsers()
        self._append(PvEnum, f"{self.pv_base}-Ena-SP", name="enable",is_setting=True)
        self._append(PvEnum, f"{self.pv_base}-Src-Pulse-SP", name="pulser_number_A", is_setting=True)
        self._append(PvEnum, f"{self.pv_base}-Src2-Pulse-SP", name="pulser_number_B", is_setting=True)
        self.description = EpicsString(pv_base + "-Name-I")

    def _get_pulserA(self):
        return self._pulsers[self.pulser_number_A.get_current_value()]

    pulserA = property(_get_pulserA)

    def _get_pulserB(self):
        return self._pulsers[self.pulser_number_B.get_current_value()]

    pulserB = property(_get_pulserB)

    def _get_pv(self, pvname):
        if not pvname in self._pvs:
            self._pvs[pvname] = PV(pvname)
        return self._pvs[pvname]

    # def _update_connected_pulsers(self):
    # self._get_pv()

    # self.pulsers = ()



class EventReceiver(Assembly):
    def __init__(
        self, pvname, n_pulsers=24, n_output_front=8, n_output_rear=16, name=None
    ):
        super().__init__(name=name)
        self.pvname = pvname
        pulsers = []
        for n in range(n_pulsers):
            self._append(EvrPulser, f"{self.pvname}:Pul{n}", name=f"pulser{n}", is_setting=True)
            pulsers.append(self.__dict__[f"pulser{n}"])
        self.pulsers = tuple(pulsers)
        outputs = []
        for n in range(n_output_front):
            self._append(EvrOutput,
                f"{self.pvname}:FrontUnivOut{n}", pulsers=pulsers,
                name=f"output_front{n}", is_setting=True)
            outputs.append(self.__dict__[f"output_front{n}"])
        for n in range(n_output_rear):
            self._append(EvrOutput, f"{self.pvname}:RearUniv{n}", pulsers=pulsers, name=f"output_rear{n}", is_setting=True)
            outputs.append(self.__dict__[f"output_rear{n}"])
        # for to in outputs:
        #     to._pulsers = self.pulsers
        self.outputs = outputs


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
