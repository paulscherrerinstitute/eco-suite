from eco.elements.assembly import Assembly
from eco.elements.detector import DetectorGet
from eco.epics.adjustable import AdjustablePv, AdjustablePvEnum
from eco.epics.detector import DetectorPvData


class AttenuatorSafetyBernina(Assembly):
    def __init__(self,name=None, xp=None):
        super().__init__(name=name)
        self._append(AdjustablePv,'SARES20-SAFETY:TRANS_TH',name='att_transm_threshold')
        self._append(AdjustablePvEnum,'SARES20-SAFETY:BBLK_SP',name='beamstop_position')
        self._append(DetectorPvData,'SARES20-SAFETY:BBLK_DIS',name='beamstop_translation_disabled')
        self._append(DetectorPvData,'SARES20-SAFETY:CLOSE_SHUTTER',name='not_force_close')
        self._append(DetectorPvData,'SARES20-SAFETY:BBLK_IN_POS',name='in_position')
        if xp is None:
            self.xp = None
        else:
            self._append(xp,name='xp',is_display=False)
        self._append(DetectorGet,self.xp.get_current_value,name='pulse_picker_open')    

    

