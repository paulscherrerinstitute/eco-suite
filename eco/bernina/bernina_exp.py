import numpy as np
from eco import Assembly
from eco.devices_general.motors import MotorRecord
from eco.devices_general.cameras_swissfel import CameraBasler
from eco.epics.adjustable import AdjustablePv
from eco.microscopes import MicroscopeMotorRecord
from eco.devices_general.powersockets import MpodModule, MpodChannel
from eco.detector import Jungfrau
from eco.devices_general.wago import AnalogOutput
from eco.elements.adjustable import AdjustableFS, AdjustableVirtual
from eco.elements.detector import DetectorGet
from eco.devices_general.pipelines_swissfel import Pipeline
from eco.devices_general.pv_adjustable import PvRecord
from eco.devices_general.motors import ThorlabsPiezoRecord


class LiquidJetSpectroscopy(Assembly):
    def __init__(
        self, pgroup_adj=None, config_JF_adj=None, name=None, v_g=None, e2v=None
    ):
        super().__init__(name=name)
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            channel_number=4,
            name="illumination_inline",
        )
        # self._append(
        #     MpodChannel,
        #     pvbase="SARES21-PS7071",
        #     channel_number=2,
        #     name="illumination_side",
        # )

        self._append(
            CameraBasler,
            # pvname_camera="SARES20-CAMS142-M3", #THC
            "SARES20-CAMS142-C2",  # GIC
            name="jetcam_inline",
        )
        self._append(Pipeline, "SARES20-CAMS142-C1_fb", name="pipeline_fb")
        # this is the large camera

        self._append(
            MicroscopeMotorRecord,
            pvname_camera="SARES20-CAMS142-C1",  # GIC
            pvname_zoom="SARES20-MF1:MOT_14",
            name="jetcam_top",
        )
        # self.jetcam_top._append(Pipeline, "SARES20-CAMS142-C1_fb", name="pipeline_fb")

        self._append(
            CameraBasler,
            # pvname_camera="SARES20-CAMS142-M3", #THC
            "SARES20-CAMS142-M1",  # GIC
            name="jetcam_back",
        )
        # jetcam_top._append(Pipeline,'SARES20-CAMS142-C1_fb',name="pipeline_fb")

        self._v_g = v_g
        self._e2v = e2v
        self._append(
            MotorRecord,
            "SARES20-MF1:MOT_5",
            name="x",
            backlash_definition=True,
            is_setting=True,
        )
        self._append(
            MotorRecord,
            "SARES20-MF1:MOT_6",
            name="y",
            backlash_definition=True,
            is_setting=True,
        )
        self._append(
            MotorRecord,
            "SARES20-MF1:MOT_7",
            name="z",
            backlash_definition=True,
            is_setting=True,
        )
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            channel_number=4,
            name="light",
        )
        self._append(
            MotorRecord,
            "SARES20-MF1:MOT_2",
            name="dist_vHamos",
            backlash_definition=True,
            is_setting=True,
        )
        # self._append(
        #     MotorRecord,y=True,
        #     "SARES20-MF1:MOT_3",
        #     name="x_analyzer",
        #     backlash_definition=True,
        #     is_setting=True,
        # )
        # self._append(
        #     MotorRecord,
        #     "SARES21-XRD:MOT_P_T",
        #     name="y_vhdet",
        #     is_setting=True,
        #
        self._append(
            Jungfrau,
            "JF03T01V02",
            name="det_totem",
            pgroup_adj=pgroup_adj,
            config_adj=config_JF_adj,
        )
        self._append(
            Jungfrau,
            "JF14T01V01",
            name="det_vhamos",
            pgroup_adj=pgroup_adj,
            config_adj=config_JF_adj,
        )
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            module_string="HV_EHS_3",
            channel_number=1,
            name="apd",
        )
        self._append(
            AdjustableFS,
            "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/apd_voltage_calibration.json",
            name="apd_voltage_calibration",
            is_display=False,
            is_setting=True,
        )

        # Convert energy - voltage using calibration
        def ene2volt(energy):
            try:
                E, V = np.asarray(self.apd_voltage_calibration()).T
                return np.interp(energy, E, V)
            except:
                return np.nan

        # Read the APD voltage and return it as the virtual value
        def get_voltage(apd_voltage):
            return self.apd.voltage.get_current_value()

        # compute voltage from energy and set it
        def set_voltage(target_energy):
            voltage = ene2volt(target_energy)
            self.apd.voltage.set_target_value(voltage)
            return voltage

        # Create virtual adjustable:
        self._append(
            AdjustableVirtual,
            [self.apd.voltage],
            get_voltage,
            set_voltage,
            reset_current_value_to=False,
            name="ene2volt",
            is_display=True,
            is_setting=True,
        )

        # Feedback adjustables
        self._append(
            AdjustablePv,
            pvsetname="SARES20-FEEDBACK-SAMPLE:TARGET",
            name="feedback_setpoint",
        )
        self._append(
            AdjustablePv,
            pvsetname="SARES20-FEEDBACK-SAMPLE:ENABLE",
            name="feedback_enabled",
        )


class SaxsSpectrometer(Assembly):
    def __init__(
        self,
        pv_xgc="SARES20-MF1:MOT_4",
        pv_rana="SARES20-MF1:MOT_3",
        jf_id="JF03T01V02",
        config_jf_adj=None,
        pgroup_adj=None,
        name="xspec_gc",
    ):
        super().__init__(name=name),
        self._append(
            MotorRecord,
            pv_xgc,
            name="x_gc",
        )
        self._append(
            MotorRecord,
            pv_rana,
            name="r_ana",
        )
        self._append(
            Jungfrau,
            jf_id,
            pgroup_adj=pgroup_adj,
            config_adj=config_jf_adj,
            name="detector",
        )


class LinearFresnelZonePlate(Assembly):
    def __init__(
        self,
        name=None,
    ):
        super().__init__(name=name)
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            channel_number=4,
            name="light",
        )
        self.motor_configuration_thorlabs = {
            "hwp_mon": {
                "pvname": "SLAAR21-LMOT-ELL1",
            },
            "hwp_pump": {
                "pvname": "SLAAR21-LMOT-ELL5",
            },
        }

        ### thorlabs piezo motors ###
        for name, config in self.motor_configuration_thorlabs.items():
            self._append(
                ThorlabsPiezoRecord,
                pvname=config["pvname"],
                name=name,
                is_setting=True,
            )

        # self._append(
        #     CameraBasler,
        #     # pvname_camera="SARES20-CAMS142-M3", #THC
        #     "SARES20-CAMS142-C2",  # GIC
        #     name="cam_inline",
        # )

        # self._append(
        #     MicroscopeMotorRecord,
        #     pvname_camera="SARES20-CAMS142-C1",  # GIC
        #     pvname_zoom="SARES20-MF1:MOT_14",
        #     name="cam_top",
        # )

        self._append(
            MotorRecord,
            "SARES20-MCS1:MOT_8",
            name="rot",
            is_setting=True,
            is_display=True,
        )

        self._append(
            MotorRecord,
            "SARES20-MCS1:MOT_3",
            name="tilt",
            is_setting=True,
            is_display=True,
        )

        self._append(
            MotorRecord,
            "SARES20-MCS3:MOT_6",
            name="x",
            is_setting=True,
            is_display=True,
        )
        self._append(
            MotorRecord,
            "SARES20-XPS1:MOT_2",
            name="y",
            is_setting=True,
            is_display=True,
        )
        self._append(
            MotorRecord,
            "SARES20-MCS3:MOT_4",
            name="z",
            is_setting=True,
            is_display=True,
        )
        self._append(
            MotorRecord,
            "SARES20-XPS1:MOT_1",
            name="foc",
            is_setting=True,
            is_display=True,
        )

        self._append(
            MotorRecord,
            "SARES20-XPS1:MOT_3",
            name="beam_stop_y",
            is_setting=True,
            is_display=True,
        )
        self._append(
            MotorRecord,
            "SARES20-MCS1:MOT_1",
            name="i0_pos",
            is_setting=True,
            is_display=True,
        )
