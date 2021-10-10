from ..epics import AdjustablePv
from ..elements import Assembly

class CtaSequencer(Assembly):
    def __init__(self,pvname, sequence_number, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self.sequence_number = sequence_number
        self._append(AdjustablePv,self._pvstr('Crtl-Length-I'),name='length',is_setting=True)
        self._append(AdjustablePv,self._pvstr('Crtl-Cycles-I'),name='length',is_setting=True)

    def _pvstr(self,suffix=''):
        return f'{self.pvname}:seq{self.sequence_number:d}{suffix}'





