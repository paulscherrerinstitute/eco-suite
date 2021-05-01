from ..elements.assembly import Assembly
from ..xoptics.dcm import EcolEnergy_new
from ..devices_general.adjustable import PvString, PvEnum, Changer
from ..devices_general.detectors import PvData
from ..aliases import Alias
from datetime import datetime


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
        self._append(
            PvData,
            "SAR-EVPO-010:DEACTIVATE",
            name="mode_monitor_inactive",
            is_status=True,
        )
        # PMM disable:
        # 1.
        # caput SAR-EVPO-010:SET_CODE 6500
        # 2.
        # caput SAR-EVPO-010:PLC_OC_PMM 1
        # Check PMM is disabled.
        # caget SAR-EVPO-010:DEACTIVATE
        # SAR-EVPO-010:DEACTIVATE "TRUE"
        # PMM activate:
        # 1.
        # caput SAR-EVPO-010:SET_CODE 0
        # 2.
        # caput SAR-EVPO-010:PLC_OC_PMM
        # Check PMM is activate.
        # caget SAR-EVPO-010:DEACTIVATE
        # SAR-EVPO-010:DEACTIVATE "FALSE"
        self._append(
            PvEnum,
            "SAROP-ARAMIS:BEAMLINE_SP",
            name="aramis_beamline_switch",
            is_status=True,
            is_setting=True,
        )
        self._append(
            PvEnum,
            "SAROP21-ARAMIS:MODE_SP",
            name="bernina_beamline_mode",
            is_status=True,
            is_setting=True,
        )
        self._append(
            PvEnum,
            "SFB_PSICO_AR:ONOFF1",
            name="psico_running",
            is_status=True,
            is_setting=False,
        )
        self._append(
            PvEnum,
            "SFB_POINTING_AR:ONOFF1",
            name="pointing_feedback_running",
            is_status=True,
            is_setting=False,
        )
        self._append(
            PvRecord,
            "SFB_POINTING_AR:SP1",
            name="pointing_feedback_setpoint_x",
            is_status=True,
            is_setting=False,
        )
        self._append(
            PvRecord,
            "SFB_POINTING_AR:SP2",
            name="pointing_feedback_setpoint_y",
            is_status=True,
            is_setting=False,
        )


class MessageBoard(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(Message, "SF-OP:CR-MSG", name="control_room", is_setting=True)
        self._append(Message, "SF-OP:ESA-MSG", name="alvra", is_setting=True)
        self._append(Message, "SF-OP:ESB-MSG", name="bernina", is_setting=True)
        self._append(Message, "SF-OP:ESM-MSG", name="maloja", is_setting=True)


class Message:
    def __init__(self, pvstem, name=None):
        self.pvname = pvstem
        self.pvs_msg = [PvString(self.pvname + f":OP-MSG{n+1}") for n in range(5)]
        self.pvs_date = [PvString(self.pvname + f":OP-DATE{n+1}") for n in range(5)]
        self.alias = Alias(name, channel=self.pvname + ":OP-MSG1", channeltype="CA")

    def set_new_message(self, message):
        for i in range(3, -1, -1):
            self.pvs_msg[i + 1].set_target_value(
                self.pvs_msg[i].get_current_value()
            ).wait()
            self.pvs_date[i + 1].set_target_value(
                self.pvs_date[i].get_current_value()
            ).wait()
        timestr = datetime.now().strftime("%a %d-%b-%Y %H:%M:%S")
        self.pvs_msg[0].set_target_value(message).wait()
        self.pvs_date[0].set_target_value(timestr).wait()

    def get_current_value(self):
        return self.pvs_msg[0].get_current_value()

    def set_target_value(self, value):
        return Changer(
            target=value,
            parent=self,
            changer=self.set_new_message,
            hold=True,
            stopper=None,
        )
