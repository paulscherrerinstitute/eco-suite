from ..elements.assembly import Assembly
from ..xoptics.dcm import EcolEnergy_new
from ..devices_general.adjustable import PvString


class SwissFel(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(EcolEnergy_new, name="ecol_energy", is_status=True)
        self._append(
            MessageBoard, name="message", is_setting=True, view_toplevel_only=False
        )


class MessageBoard(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            PvString, "SF-OP:CR-MSG:OP-MSG1", name="control_room", is_setting=True
        )
        self._append(PvString, "SF-OP:ESA-MSG:OP-MSG1", name="alvra", is_setting=True)
        self._append(PvString, "SF-OP:ESB-MSG:OP-MSG1", name="bernina", is_setting=True)
        self._append(PvString, "SF-OP:ESM-MSG:OP-MSG1", name="maloja", is_setting=True)
