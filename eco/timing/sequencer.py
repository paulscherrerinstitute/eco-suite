from epics.ca import element_count
from epics.pv import PV
import numpy as np
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from ..epics.detector import DetectorPvData
from ..elements.detector import DetectorGet
from ..elements.assembly import Assembly


class CtaSequencer(Assembly):
    def __init__(
        self,
        pvname,
        sequence_number,
        event_codes=list(range(200, 220)),
        name=None,
    ):
        super().__init__(name=name)
        self.pvname = pvname
        self.sequence_number = sequence_number
        self.event_codes = event_codes
        # self.sequence
        self._append(
            AdjustablePv,
            f"{self.pvname}:SerMaxLen-O",
            name="max_length",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv, self._pvstr("Ctrl-Length-I"), name="length", is_setting=True
        )
        self._append(
            AdjustablePv,
            self._pvstr("Ctrl-Cycles-I"),
            name="number_of_repetitions",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self._pvstr("Ctrl-SCfgMode-I"),
            name="start_condition_enabled",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self._pvstr("Ctrl-SCfgModDivisor-I"),
            name="start_condition_pulse_id_divisor",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self._pvstr("Ctrl-SCfgModOffset-I"),
            name="start_condition_pulse_id_offset",
            is_setting=True,
        )
        self._startpv = PV(self._pvstr("Ctrl-Start-I"))
        self._stoppv = PV(self._pvstr("Ctrl-Stop-I"))
        self._append(
            DetectorPvData,
            self._pvstr("Ctrl-IsRunning-O"),
            name="is_running",
        )
        self._append(
            DetectorPvData,
            self._pvstr("Ctrl-StartedAt-O"),
            name="last_start_pulse_id",
        )
        self.event_code_sequences = {}
        for i, eventcode in enumerate(self.event_codes):
            self._append(
                AdjustablePv,
                self._pvstr(f"Ser{i}-Data-I"),
                # element_count=self.max_length.get_current_value(),
                name=f"seq_code{eventcode}",
                is_setting=True,
                is_display=False,
            )
            self.event_code_sequences[eventcode] = self.__dict__[f"seq_code{eventcode}"]

        self._append(
            DetectorGet, self.get_reduced_sequence, name="sequence", is_setting=False
        )

    def start(self):
        self._startpv.put(1)

    def stop(self):
        self._stoppv.put(1)

    def get_sequence_array(self):
        arrays = {}
        totlen = self.length.get_current_value()
        lens = []
        for eventcode, tadj in self.event_code_sequences.items():
            arrays[eventcode] = tadj.get_current_value()[:totlen]

        return arrays

    def append_sequence_step(self, code, step_delay):
        if code not in self.event_code_sequences.keys():
            raise Exception(
                f"Eventcode {code} is not within the allowed or configured eventcodes for the sequencer"
            )
        oldlength = self.length.get_current_value()
        newlength = oldlength + step_delay
        changes = []
        for i, ec in self.event_code_sequences.items():
            if oldlength == 0:
                o = []
            else:
                o = list(ec.get_current_value())
            if i == code:
                n = o + [0] * (newlength - oldlength - 1) + [1]
            else:
                n = o + [0] * (newlength - oldlength)
            # print(o, n)
            changes.append(ec.set_target_value(n))
        for change in changes:
            change.wait()

        # self.event_code_sequences[code]._value[newlength - 1] = 1

        self.length.set_target_value(newlength).wait()

    def reset_sequence(self):
        chs = []
        for code, adj in self.event_code_sequences.items():
            chs.append(
                adj.set_target_value(
                    np.zeros(self.max_length.get_current_value(), dtype=np.int32)
                )
            )
        for ch in chs:
            ch.wait()
        self.length.set_target_value(0).wait()

    def get_reduced_sequence(self):
        seq = self.get_sequence_array()
        seq_red = {}
        for code, is_present_array in seq.items():
            tsteps = is_present_array.nonzero()[0]
            for step in tsteps:
                if not step in seq_red.keys():
                    seq_red[step] = []
                seq_red[step].append(code)

        return seq_red

    def _pvstr(self, suffix=""):
        return f"{self.pvname}:seq{self.sequence_number:d}{suffix}"


# temp for development


def get_cta():
    return CtaSequencer("SAR-CCTA-ESB", 0, name="c")
