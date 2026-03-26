# # ad hoc incoupling device
from eco.devices_general.motors import MotorRecord, SmaractRecord, ThorlabsPiezoRecord
from eco.devices_general.wago import AnalogOutput
from eco.elements.adjustable import AdjustableInterpolate, AdjustableVirtual
from eco.elements.assembly import Assembly
from eco.epics.adjustable import AdjustablePv
from eco.epics.detector import DetectorPvData


class Incoupling(Assembly):
    def __init__(self, delaystage_pump=None, name=None):
        super().__init__(name=name)
        # self._append(
        #     SmaractRecord, "SARES20-MCS2:MOT_13", name="thz_par2_x", is_setting=True
        # )
        # self._append(
        #     SmaractRecord, "SARES20-MCS2:MOT_16", name="thz_par2_z", is_setting=True
        # )
        # self._append(
        #     SmaractRecord, "SARES20-MCS2:MOT_14", name="thz_par2_ry", is_setting=True
        # )
        # self._append(
        #     SmaractRecord, "SARES20-MCS2:MOT_15", name="thz_par2_rx", is_setting=True
        # )
        self._append(
            SmaractRecord, "SARES20-MCS2:MOT_11", name="thz_par1_z", is_setting=True
        )
        self._append(
            SmaractRecord, "SARES20-MCS2:MOT_17", name="thz_par1_ry", is_setting=True
        )

        try:
            self.motor_configuration_thorlabs = {
                "thz_filter": {
                    "pvname": "SLAAR21-LMOT-ELL4",
                },
                "thz_crystal": {
                    "pvname": "SLAAR21-LMOT-ELL3",
                },
                "thz_waveplate": {
                    "pvname": "SLAAR21-LMOT-ELL5",
                },
                "nd_filter": {
                    "pvname": "SLAAR21-LMOT-ELL2",
                },
                "polarizer": {
                    "pvname": "SLAAR21-LMOT-ELL1",
                },
            }

            ### thorlabs piezo motors ###
            for name, config in self.motor_configuration_thorlabs.items():
                self._append(
                    ThorlabsPiezoRecord,
                    pvname=config["pvname"],
                    name=name,
                    is_setting=True,
                    accuracy=0.5,
                )
        except Exception as e:
            print(e)

        self._append(
            AdjustableInterpolate,
            self.nd_filter,
            filename_calib="/sf/bernina/config/eco/reference_values/nd_filter_wheel_thlabs.json",
            deadband=None,
            interp_method="next",
            callbacks_before_change=[],
            callbacks_after_change=[],
            unit="OptDens",
            name="nd_filter_optical_density",
        )

        self._append(
            SmaractRecord, "SARES20-MCS2:MOT_18", name="opa_mirr2_ry", is_setting=True
        )
        self._append(
            SmaractRecord, "SARES20-MCS2:MOT_10", name="tt_nopa_target", is_setting=True
        )
        self._append(
            AnalogOutput,
            "SLAAR21-LDIO-LAS6991:DAC07_VOLTS",
            name="opa_mirr1_ry",
            is_setting=True,
        )
        self._append(
            AnalogOutput,
            "SLAAR21-LDIO-LAS6991:DAC08_VOLTS",
            name="opa_mirr1_rx",
            is_setting=True,
        )

        self._append(MotorRecord, "SARES20-XPS1:MOT_X", name="lens_z", is_setting=True)
        self._append(MotorRecord, "SARES20-XPS1:MOT_Y", name="lens_x", is_setting=True)
        self._append(MotorRecord, "SARES20-XPS1:MOT_Z", name="lens_y", is_setting=True)
        # self._append(
        #     MotorRecord, "SARES20-MF1:MOT_13", name="eos_mirr", is_setting=True
        # )

        self._append(
            AnalogOutput,
            "SLAAR21-LDIO-LAS6991:DAC06_VOLTS",
            name="eos_fb_rx",
            is_setting=True,
        )
        self._append(
            AnalogOutput,
            "SLAAR21-LDIO-LAS6991:DAC05_VOLTS",
            name="eos_fb_ry",
            is_setting=True,
        )

        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LCAM-C561:FIT2_REQUIRED.PROC",
            name="eos_fb_setpoint_rq",
            accuracy=1,
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LCAM-C561:FIT2_DEFAULT.PROC",
            name="eos_fb_setpoint_df",
            accuracy=1,
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LTIM01-EVR0:CALCW.A",
            name="eos_fd_enable",
            accuracy=1,
            is_setting=True,
        )

        self._append(
            AdjustableVirtual,
            [self.thz_crystal, self.thz_waveplate],
            lambda c, w: c,
            lambda angle: [angle, angle / 2],
            name="thz_polarization",
            is_setting=False,
        )

        self._append(
            DetectorPvData,
            "SLAAR21-L-BECKBS:PR1_CH0_VAL_GET",
            name="energymeter_intensity_raw",
        )

        self._append(
            DetectorPvData,
            "SLAAR-LADC-WL009:ADC1_VAL",
            name="pump_intensity",
        )

        self._append(
            DetectorPvData,
            "SARES20-LSCP9-FNS:CH6:VAL_GET",
            name="shg_intensity",
        )
        # IOXAS (SARES20_CH6)

        # self._append(
        #     AdjustableVirtual,
        #     [self.thz_par1_z, self.thz_par2_z],
        #     lambda z1, z2: z2,
        #     lambda z: [
        #         self.thz_par1_z.get_current_value()
        #         + (z - self.thz_par2_z.get_current_value()),
        #         z,
        #     ],
        #     name="thz_focus",
        #     is_setting=False,
        #     is_display=False,
        # )

        # self._append(
        #     delaystage_pump,
        #     name="delaystage_pump",
        #     is_setting=False,
        #     is_display=False,
        # )

        # self._append(
        #     AdjustableVirtual,
        #     [self.delaystage_pump, self.thz_par2_x],
        #     lambda d, x: x,
        #     lambda x: [
        #         self.delaystage_pump.get_current_value()
        #         + (x - self.thz_par2_x.get_current_value()) / 2,
        #         x,
        #     ],
        #     name="thz_par2_x_delaycomp",
        #     is_setting=False,
        #     is_display=False,
        # )

    # def thz_pol_set(self, val):
    #     return 1.0 * val, 1.0 / 2 * val

    # def thz_pol_get(self, val, val2):
    #     return 1.0 * val2
