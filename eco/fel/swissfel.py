from ..elements.assembly import Assembly
from ..xoptics.dcm import EcolEnergy_new
from ..devices_general.adjustable import PvString
from ..devices_general.detectors import PvData


class SwissFel(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            PvData,
            "SWISSFEL-STATUS:Bunch-1-Appl-Freq-RB",
            name="aramis_rep_rate",
            is_status=True,
        )
        self._append(EcolEnergy_new, name="ecol_energy", is_status=True)
        self._append(
            MessageBoard, name="message", is_setting=True, is_status="recursive"
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
