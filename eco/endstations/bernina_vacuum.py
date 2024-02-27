

from eco import Assembly
from eco.epics.adjustable import AdjustablePvEnum


class BerninaVacuum(Assembly):
    def __init__(self,name=None):
        super().__init__(name=name)
        self._append(AdjustablePvEnum,"SAROP21-VVPG-0010:PLC_OPEN_F",name="all_valves_open")
