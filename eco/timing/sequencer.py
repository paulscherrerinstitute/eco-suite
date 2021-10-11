from ..epics.adjustable import AdjustablePv,AdjustablePvEnum
from ..elements import Assembly

class CtaSequencer(Assembly):
    def __init__(self,pvname, sequence_number, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self.sequence_number = sequence_number
        self._append(AdjustablePv,self._pvstr('SerMaxLen-0'),name='max_length',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Crtl-Length-I'),name='length',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Crtl-Cycles-I'),name='cycles',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-SCfgMode-I'),name='mode',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-SCfgModDivisor-I'),name='start_condition_pulse_id_divisor',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-SCfgModOffset-I'),name='start_condition_pulse_id_offset',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-Start-I'),name='start',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-Stop-I'),name='stop',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-IsRunning-O'),name='is_running',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Ctrl-StartedAt-O'),name='start_pulse_id',is_setting=True)

    def _pvstr(self,suffix=''):
        return f'{self.pvname}:seq{self.sequence_number:d}{suffix}'





