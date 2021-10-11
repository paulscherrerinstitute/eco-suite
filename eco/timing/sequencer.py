from ..epics.adjustable import AdjustablePv,AdjustablePvEnum
from ..elements import Assembly

class CtaSequencer(Assembly):
    def __init__(self,pvname, sequence_number, event_codes=list(range(200,220)), name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self.sequence_number = sequence_number
        self.event_codes = event_codes
        self.sequence
        self._append(AdjustablePv,f'{self.pvname}:SerMaxLen-O',name='max_length',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-Length-I'),name='length',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-Cycles-I'),name='cycles',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-SCfgMode-I'),name='mode',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-SCfgModDivisor-I'),name='start_condition_pulse_id_divisor',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-SCfgModOffset-I'),name='start_condition_pulse_id_offset',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-Start-I'),name='start',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-Stop-I'),name='stop',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-IsRunning-O'),name='is_running',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-StartedAt-O'),name='start_pulse_id',is_setting=True)
        self.event_code_sequences = {}
        for i,eventcode in enumerate(self.event_codes):
            self._append(AdjustablePv, self._pvstr(f'Ser{i}-Data-I'), name=f'seq_code{eventcode}', is_setting=True)
            self.event_code_sequences[eventcode] = self.__dict__[f'seq_code{eventcode}']



    def get_sequence_array(self):
        arrays = {}
        lens = []
        for eventcode,tadj in self.event_code_sequences.items():
            arrays[eventcode] = tadj.get_current_value()
            lens = len(arrays[eventcode])
        if not all([self.length.get_current_value() == len(ta) for ta in arrays.values()]):
            raise Exception("sequencer event code arrays lengths don't fit to total length")

        return arrays



    def _pvstr(self,suffix=''):
        return f'{self.pvname}:seq{self.sequence_number:d}{suffix}'





