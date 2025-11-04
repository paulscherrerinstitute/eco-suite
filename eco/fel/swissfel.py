import time
from ..elements.assembly import Assembly
from ..xoptics.dcm import EcolEnergy_new
from ..elements.adjustable import Changer, spec_convenience, default_representation
from ..epics.adjustable import AdjustablePvEnum, AdjustablePvString, AdjustablePv
from ..epics.detector import DetectorPvData, DetectorPvEnum
from ..aliases import Alias
from datetime import datetime
from time import sleep
import numpy as np
from ..detector.detectors_psi import DetectorBsStream


class SwissFel(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            DetectorPvData,
            "SARFE10-PBPG050:HAMP-INTENSITY-CAL",
            name="aramis_pulse_energy",
            is_display=True,
            has_unit=True,
        )
        self._append(
            UndulatorK, name="aramis_photon_energy_undulators", is_display=True
        )
        self._append(
            EcolEnergy_new, name="aramis_electron_energy_ecol", is_display=True
        )
        self._append(
            DetectorPvData,
            "SARUN03-UIND030:FELPHOTENE",
            name="aramis_photon_energy_und03",
            is_display=True,
        )
        # self._append(
        # DetectorPvData,
        # "SARUN:FELPHOTENE",
        # name="aramis_photon_energy",
        # is_display=True,
        # )
        self._append(
            DetectorPvData,
            "SWISSFEL-STATUS:Bunch-1-Appl-Freq-RB",
            name="aramis_rep_rate",
            is_display=True,
        )
        self._append(
            DetectorPvData,
            "SAR-EVPO-010:DEACTIVATE",
            name="mode_monitor_inactive",
            is_display=True,
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
            AdjustablePvEnum,
            "SAROP-ARAMIS:BEAMLINE",
            pvname_set="SAROP-ARAMIS:BEAMLINE_SP",
            name="aramis_beamline_switch",
            is_display=True,
            is_setting=True,
        )
        self._append(
            AdjustablePvEnum,
            "SAROP21-ARAMIS:MODE_SP",
            name="bernina_beamline_mode",
            is_display=True,
            is_setting=True,
        )
        self._append(
            AdjustablePvEnum,
            "SFB_PSICO_AR:ONOFF1",
            name="psico_running",
            is_display=True,
            is_setting=False,
        )
        self._append(
            DetectorPvEnum,
            "SFB_POINTING_AR_MON:SELECT",
            name="pointing_feedback_monitor",
            is_display=True,
            is_setting=False,
        )
        self._append(
            AdjustablePvEnum,
            "SFB_POINTING_AR:ONOFF1",
            name="pointing_feedback_running",
            is_display=True,
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            "SFB_POINTING_AR:SP1",
            name="pointing_feedback_setpoint_x",
            is_display=True,
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            "SFB_POINTING_AR:SP2",
            name="pointing_feedback_setpoint_y",
            is_display=True,
            is_setting=False,
        )
        self._append(
            MessageBoard, name="message", is_setting=True, is_display="recursive"
        )

        self._append(
            DetectorBsStream,
            "SINLH01-DBAM010:EOM1_T1",
            name="bam_injector",
            is_setting=False,
        )
        self._append(
            DetectorBsStream,
            "S10BC01-DBAM070:EOM1_T1",
            name="bam_linac_70m",
            is_setting=False,
        )
        self._append(
            DetectorBsStream,
            "SARCL01-DBAM110:EOM1_T1",
            name="bam_linac_110m",
            is_setting=False,
        )
        self._append(
            DetectorBsStream,
            "SARUN20-DBAM020:EOM1_T1",
            name="bam_aramisund",
            is_setting=False,
        )
        self._append(
            DetectorBsStream,
            "SARUN03-DBPM070:X-REF-FB",
            name="undulator_x_orbit",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorBsStream,
            "SARUN03-DBPM070:Y-REF-FB",
            name="undulator_y_orbit",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorBsStream,
            "SARUN03-MQUA080:X",
            name="undulator_quad_mover_x",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorBsStream,
            "SARUN03-MQUA080:Y",
            name="undulator_quad_mover_y",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorBsStream,
            "SARUN03-UIND030:GM-X-SET",
            name="undulator_girder_x",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorBsStream,
            "SARUN03-UIND030:GM-Y-SET",
            name="undulator_girder_y",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorBsStream,
            "SARUN03-UIND030:GM-YAW-SET",
            name="undulator_girder_yaw",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorBsStream,
            "SARUN03-UIND030:GM-PITCH-SET",
            name="undulator_girder_pitch",
            is_setting=False,
            is_display=False,
        )


# stuff to add


# Quadrupole mover positions (normally set by the BBA):
# SARUN03-MQUA080:X
# SARUN03-MQUA080:Y

# Undulators girder position (also set by the BBA):
# SARUN03-UIND030:GM-X-SET
# SARUN03-UIND030:GM-Y-SET
# SARUN03-UIND030:GM-YAW-SET
# SARUN03-UIND030:GM-PITCH-SET


class MessageBoard(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(Message, "SF-OP:CR-MSG", name="control_room", is_setting=True)
        self._append(
            AdjustablePvEnum,
            "SF-OP:ESA-MSG:STATUS",
            name="alvra_status",
            is_setting=True,
        )
        self._append(Message, "SF-OP:ESA-MSG", name="alvra_message", is_setting=True)
        self._append(
            AdjustablePvEnum,
            "SF-OP:ESB-MSG:STATUS",
            name="bernina_status",
            is_setting=True,
        )
        self._append(Message, "SF-OP:ESB-MSG", name="bernina_message", is_setting=True)
        self._append(
            AdjustablePvEnum,
            "SF-OP:ESC-MSG:STATUS",
            name="cristallina_status",
            is_setting=True,
        )
        self._append(
            Message, "SF-OP:ESC-MSG", name="cristallina_message", is_setting=True
        )
        self._append(
            AdjustablePvEnum,
            "SF-OP:ESE-MSG:STATUS",
            name="maloja_status",
            is_setting=True,
        )
        self._append(Message, "SF-OP:ESE-MSG", name="maloja_message", is_setting=True)
        self._append(
            AdjustablePvEnum,
            "SF-OP:ESE-MSG:STATUS",
            name="furka_status",
            is_setting=True,
        )
        self._append(Message, "SF-OP:ESF-MSG", name="furka_message", is_setting=True)


@spec_convenience
class Message:
    def __init__(self, pvstem, name=None):
        self.pvname = pvstem
        self.pvs_msg = [
            AdjustablePvString(self.pvname + f":OP-MSG{n + 1}") for n in range(5)
        ]
        self.pvs_date = [
            AdjustablePvString(self.pvname + f":OP-DATE{n + 1}") for n in range(5)
        ]
        self.alias = Alias(name, channel=self.pvname + ":OP-MSG1", channeltype="CA")
        self.pv_tmp = AdjustablePvString(self.pvname + ":OP-MSG-tmp")

    def set_new_message(self, message):
        # for i in range(3, -1, -1):
        #     self.pvs_msg[i + 1].set_target_value(
        #         self.pvs_msg[i].get_current_value()
        #     ).wait()
        #     self.pvs_date[i + 1].set_target_value(
        #         self.pvs_date[i].get_current_value()
        #     ).wait()
        self.pv_tmp.set_target_value(message).wait()
        # timestr = datetime.now().strftime("%a %d-%b-%Y %H:%M:%S")
        # self.pvs_msg[0].set_target_value(message).wait()
        # self.pvs_date[0].set_target_value(timestr).wait()

    def get_current_value(self):
        return self.pvs_msg[0].get_current_value()

    def set_target_value(self, value, hold=False):
        return Changer(
            target=value,
            parent=self,
            changer=self.set_new_message,
            hold=hold,
            stopper=None,
        )


@spec_convenience
class UndulatorK(Assembly):
    def __init__(self, maximum_energy_change_keV=1.0, name=None):
        super().__init__(name=name)
        self.maximum_energy_change_keV = maximum_energy_change_keV
        self._append(
            DetectorPvData,
            "SARUN:FELPHOTENE",
            name="aramis_undulator_photon_energy",
            is_display=True,
            has_unit=True,
        )
        self.ksets = []
        self.gaps = []
        for undno in [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]:
            self._append(
                AdjustablePv,
                f"SARUN{undno:02d}-UIND030:K_SET",
                name=f"und{undno:02d}_Kset",
                is_setting=False,
                is_display=False,
            )
            self.ksets.append(self.__dict__[f"und{undno:02d}_Kset"])
            self._append(
                AdjustablePv,
                f"SARUN{undno:02d}-UIND030:GAP_SP",
                pvreadbackname=f"SARUN{undno:02d}-UIND030:GAP-READ",
                accuracy=0.0002,
                name=f"und{undno:02d}_gap",
                is_setting=False,
                is_display=False,
            )
            self.gaps.append(self.__dict__[f"und{undno:02d}_gap"])
        self.status_collection.append(self, selection="settings", recursive=False)
        self.unit = self.aramis_undulator_photon_energy.unit

    def calc_new_Ksets(self, energy_target, energy_start=None):
        if not energy_start:
            energy_start = self.aramis_undulator_photon_energy.get_current_value()
        K_start = [tks.get_current_value() for tks in self.ksets]
        return [
            (energy_start / energy_target * (tK_start**2 + 2) - 2) ** 0.5
            for tK_start in K_start
        ]

    def get_current_value(self):
        return self.aramis_undulator_photon_energy.get_current_value()

    def change_energy(self, energy):
        if np.abs(energy - self.get_current_value()) > self.maximum_energy_change_keV:
            raise Exception("Likely too large undulator energy change requested!!!")

        vals = self.calc_new_Ksets(energy)
        start_time = time.time()
        for kset, val in zip(self.ksets, vals):
            kset.set_target_value(val)

        sleep(0.2)
        for gap in self.gaps:
            while gap.get_change_done() == 0:
                sleep(0.02)
                if (time.time() - start_time) > 10:
                    print(
                        "NB: did not see all Undulators start move and stop for 10s, calling move done anyways."
                    )
                    break
        sleep(1)

    def set_target_value(self, value, hold=False):
        return Changer(
            target=value,
            parent=self,
            changer=self.change_energy,
            hold=hold,
            stopper=None,
        )
