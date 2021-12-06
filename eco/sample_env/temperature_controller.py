from eco.epics.adjustable import AdjustablePV, AdjustablePvEnum
from eco.elements.assembly import Assembly

class LakeshoreController(Assembly):
    def __init__(self,pvname,name=None):\
        super().__init__(name=name)
        self.pvname = pvname
        self._append(AdjustablePv,self.pvname+':TEMP.VAL', self.pvname+':TEMP_RBV')


