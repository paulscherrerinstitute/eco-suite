from ..elements.assembly import Assembly
from ..xoptics.dcm import EcolEnergy_new


class SwissFel(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(EcolEnergy_new, name="ecol_energy", is_status=True)
