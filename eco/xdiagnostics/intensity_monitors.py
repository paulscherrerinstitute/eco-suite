from ..devices_general.motors import MotorRecord
from ..devices_general.detectors import FeDigitizer
from ..epics.detector import DetectorPvDataStream
from ..detector.detectors_psi import DetectorBsStream
from ..elements.adjustable import AdjustableVirtual
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from ..aliases import Alias, append_object_to_object
from ..elements.assembly import Assembly
from epics import PV
import numpy as np


class GasDetector(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            DetectorPvDataStream,
            "SARFE10-PBPG050:HAMP-INTENSITY-CAL",
            name="fast_calibrated",
            is_display=True,
        )


class FeDigitiza(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            AdjustablePvEnum,
            pvname + "-WD-gain",
            name="gain",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            pvname + "-HV_SET",
            name="bias",
            is_setting=True,
            is_display=True,
        )

        # self.channels = [
        # Id + "-BG-DATA",
        # Id + "-BG-DRS_TC",
        # Id + "-BG-PULSEID-valid",
        # Id + "-DATA",
        # Id + "-DRS_TC",
        # Id + "-PULSEID-valid",
        # ]


class SolidTargetDetectorPBPS(Assembly):
    def __init__(
        self,
        pvname,
        # VME_crate=None,
        # pipeline=None,
        # link=None,
        # channels={},
        # ch_up=12,
        # ch_down=13,
        # ch_left=15,
        # ch_right=14,
        # elog=None,
        name=None,
        # calc=None,
        # calc_calib={},
    ):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            MotorRecord, pvname + ":MOTOR_X1", name="x_diodes", is_setting=True
        )
        self._append(
            MotorRecord, pvname + ":MOTOR_Y1", name="y_diodes", is_setting=True
        )
        self._append(
            MotorRecord, pvname + ":MOTOR_PROBE", name="target_y", is_setting=True
        )
        self._append(
            AdjustablePvEnum, pvname + ":PROBE_SP", name="target", is_setting=True
        )

        self._append(
            DetectorPvDataStream,
            pvname + ":INTENSITY",
            name="intensity",
            is_setting=False,
        )
        self._append(
            DetectorPvDataStream, pvname + ":XPOS", name="xpos", is_setting=False
        )
        self._append(
            DetectorPvDataStream, pvname + ":YPOS", name="ypos", is_setting=False
        )

        # Calibration calculation record

        # Intensity
        self._append(
            AdjustablePv,
            pvname + ":INTENSITY.INPA",
            name="calc_intensity_input_A",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pvname + ":INTENSITY.INPB",
            name="calc_intensity_input_B",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pvname + ":INTENSITY.INPC",
            name="calc_intensity_input_C",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pvname + ":INTENSITY.INPD",
            name="calc_intensity_input_D",
            is_setting=True,
            is_display=False,
        )

        self._append(
            AdjustablePv,
            pvname + ":INTENSITY.E",
            name="calc_intensity_const_E",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pvname + ":INTENSITY.F",
            name="calc_intensity_const_F",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pvname + ":INTENSITY.G",
            name="calc_intensity_const_G",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pvname + ":INTENSITY.H",
            name="calc_intensity_const_H",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pvname + ":INTENSITY.CALC",
            name="calc_intensity_function",
            is_setting=True,
            is_display=False,
        )
        # X position
        self._append(
            AdjustablePv,
            pvname + ":XPOS.INPA",
            name="calc_xpos_input_A",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pvname + ":XPOS.INPB",
            name="calc_xpos_input_B",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pvname + ":XPOS.CALC",
            name="calc_xpos_function",
            is_setting=True,
            is_display=False,
        )

        # self._append(
        #     AdjustablePv, pvname + ":XPOS.INPD", name="calc_input_D", is_setting=True, is_display=False
        # )

        # self._append(
        #     AdjustablePv, pvname + ":XPOS.IE", name="calc_input_E", is_setting=True, is_display=False

        # self._append(
        #     AdjustablePv, pvname + ":XPOS.IF", name="calc_input_F", is_setting=True, is_display=False
        # )
        # self._append(
        #     AdjustablePv, pvname + ":XPOS.INPG", name="calc_input_G", is_setting=True, is_display=False
        # )
        # self._append(
        #     AdjustablePv, pvname + ":XPOS.INPH", name="calc_input_H", is_setting=True, is_display=False
        # )
        # self._append(
        #     AdjustablePv, pvname + ":XPOS.CALC", name="calc_function", is_setting=True, is_display=False
        # )

        # # Intensity
        # # Set channels
        # # Input data
        # ep.PV(Devive_prefix+'INTENSITY.INPA').put(bytes(channels[0], "utf8"))
        # ep.PV(Devive_prefix+'INTENSITY.INPB').put(bytes(channels[1], "utf8"))
        # ep.PV(Devive_prefix+'INTENSITY.INPC').put(bytes(channels[2], "utf8"))
        # ep.PV(Devive_prefix+'INTENSITY.INPD').put(bytes(channels[3], "utf8"))
        # # Calibration values
        # ep.PV(Devive_prefix+'INTENSITY.E').put(bytes(str(norm_diodes[0,0]), "utf8"))
        # ep.PV(Devive_prefix+'INTENSITY.F').put(bytes(str(norm_diodes[0,1]), "utf8"))
        # ep.PV(Devive_prefix+'INTENSITY.G').put(bytes(str(norm_diodes[0,2]), "utf8"))
        # ep.PV(Devive_prefix+'INTENSITY.H').put(bytes(str(norm_diodes[0,3]), "utf8"))
        # # Calculation
        # ep.PV(Devive_prefix+'INTENSITY.CALC').put(bytes("A*E+B*F+C*G+D*H", "utf8"))

        # # XPOS
        # # Set channels
        # ep.PV(Devive_prefix+'XPOS.INPA').put(bytes(channels[2], "utf8"))
        # ep.PV(Devive_prefix+'XPOS.INPB').put(bytes(channels[3], "utf8"))
        # # Threshold value
        # ep.PV(Devive_prefix+'XPOS.D').put(bytes(str(0.2), "utf8"))
        # # Diode calibration value
        # ep.PV(Devive_prefix+'XPOS.E').put(bytes(str(norm_diodes[0,2]), "utf8"))
        # ep.PV(Devive_prefix+'XPOS.F').put(bytes(str(norm_diodes[0,3]), "utf8"))
        # # Null value
        # ep.PV(Devive_prefix+'XPOS.G').put(bytes(str(0), "utf8"))
        # # Position calibration value
        # ep.PV(Devive_prefix+'XPOS.I').put(bytes(str((Scan_x_range[1]-Scan_x_range[0])/ np.diff(scan_x_norm).mean()), "utf8"))
        # # Intensity threshold value
        # ep.PV(Devive_prefix+'XPOS.INPJ').put(bytes(Devive_prefix+'INTENSITY', "utf8"))
        # # Calculation
        # ep.PV(Devive_prefix+'XPOS.CALC').put(bytes("J<D?G:I*(A*E-B*F)/(A*E+B*F)", "utf8"))

        # # YPOS
        # # Set channels
        # ep.PV(Devive_prefix+'YPOS.INPA').put(bytes(channels[0], "utf8"))
        # ep.PV(Devive_prefix+'YPOS.INPB').put(bytes(channels[1], "utf8"))
        # # Threshold value
        # ep.PV(Devive_prefix+'YPOS.D').put(bytes(str(0.2), "utf8"))
        # # Diode calibration value
        # ep.PV(Devive_prefix+'YPOS.E').put(bytes(str(norm_diodes[0,0]), "utf8"))
        # ep.PV(Devive_prefix+'YPOS.F').put(bytes(str(norm_diodes[0,1]), "utf8"))
        # # Null value
        # ep.PV(Devive_prefix+'YPOS.G').put(bytes(str(0), "utf8"))
        # # Position calibration value
        # ep.PV(Devive_prefix+'YPOS.I').put(bytes(str((Scan_y_range[1]-Scan_y_range[0])/ np.diff(scan_y_norm).mean()), "utf8"))
        # # Intensity threshold value
        # ep.PV(Devive_prefix+'YPOS.INPJ').put(bytes(Devive_prefix+'INTENSITY', "utf8"))
        # # Calculation
        # ep.PV(Devive_prefix+'YPOS.CALC').put(bytes("J<D?G:I*(A*E-B*F)/(A*E+B*F)", "utf8"))

        # if VME_crate:
        #     self.diode_up = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_up))
        #     self.diode_down = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_down))
        #     self.diode_left = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_left))
        #     self.diode_right = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_right))

        # if channels:
        #     self._append(
        #         DetectorPvDataStream, channels["up"], name="signal_up", is_setting=False
        #     )
        #     self._append(
        #         DetectorPvDataStream,
        #         channels["down"],
        #         name="signal_down",
        #         is_setting=False,
        #     )
        #     self._append(
        #         DetectorPvDataStream,
        #         channels["left"],
        #         name="signal_left",
        #         is_setting=False,
        #     )
        #     self._append(
        #         DetectorPvDataStream,
        #         channels["right"],
        #         name="signal_right",
        #         is_setting=False,
        #     )

        # if calc:
        #     self._append(
        #         DetectorPvDataStream, calc["itot"], name="intensity", is_setting=False
        #     )
        #     self._append(
        #         DetectorPvDataStream, calc["xpos"], name="xpos", is_setting=False
        #     )
        #     self._append(
        #         DetectorPvDataStream, calc["ypos"], name="ypos", is_setting=False
        #     )

    def get_calibration_values(self, seconds=5):
        self.x_diodes.set_target_value(0).wait()
        self.y_diodes.set_target_value(0).wait()
        ds = [self.signal_up, self.signal_down, self.signal_left, self.signal_right]
        aqs = [d.acquire(seconds=seconds) for d in ds]
        data = [aq.wait() for aq in aqs]
        mean = [np.mean(td) for td in data]
        std = [np.std(td) for td in data]
        norm_diodes = [1 / tm / 4 for tm in mean]
        return norm_diodes

    def set_calibration_values(self, norm_diodes):
        # this is now only for bernina when using the ioxos from sla
        channels = [
            "SLAAR21-LTIM01-EVR0:CALCI.INPG",
            "SLAAR21-LTIM01-EVR0:CALCI.INPH",
            "SLAAR21-LTIM01-EVR0:CALCI.INPE",
            "SLAAR21-LTIM01-EVR0:CALCI.INPF",
        ]
        for tc, tv in zip(channels, norm_diodes):
            PV(tc).put(bytes(str(tv), "utf8"))
        channels = ["SLAAR21-LTIM01-EVR0:CALCX.INPE", "SLAAR21-LTIM01-EVR0:CALCX.INPF"]
        for tc, tv in zip(channels, norm_diodes[2:4]):
            PV(tc).put(bytes(str(tv), "utf8"))
        channels = ["SLAAR21-LTIM01-EVR0:CALCY.INPE", "SLAAR21-LTIM01-EVR0:CALCY.INPF"]
        for tc, tv in zip(channels, norm_diodes[0:2]):
            PV(tc).put(bytes(str(tv), "utf8"))

    def get_calibration_values_position(
        self, calib_intensities, seconds=5, motion_range=0.2
    ):
        self.x_diodes.set_limits(-motion_range / 2 - 0.1, +motion_range / 2 + 0.1)
        self.y_diodes.set_limits(-motion_range / 2 - 0.1, +motion_range / 2 + 0.1)
        self.x_diodes.set_target_value(0).wait()
        self.y_diodes.set_target_value(0).wait()
        raw = []
        for pos in [motion_range / 2, -motion_range / 2]:
            print(pos)
            self.x_diodes.set_target_value(pos).wait()
            aqs = [
                ts.acquire(seconds=seconds)
                for ts in [self.signal_left, self.signal_right]
            ]
            vals = [
                np.mean(aq.wait()) * calib
                for aq, calib in zip(aqs, calib_intensities[0:2])
            ]
            raw.append((vals[0] - vals[1]) / (vals[0] + vals[1]))
        grad = motion_range / np.diff(raw)[0]
        # xcalib = [np.diff(calib_intensities[0:2])[0]/np.sum(calib_intensities[0:2]), grad]
        xcalib = [0, grad]
        self.x_diodes.set_target_value(0).wait()
        raw = []
        for pos in [motion_range / 2, -motion_range / 2]:
            self.y_diodes.set_target_value(pos).wait()
            aqs = [
                ts.acquire(seconds=seconds) for ts in [self.signal_up, self.signal_down]
            ]
            vals = [
                np.mean(aq.wait()) * calib
                for aq, calib in zip(aqs, calib_intensities[2:4])
            ]
            raw.append((vals[0] - vals[1]) / (vals[0] + vals[1]))
        grad = motion_range / np.diff(raw)[0]
        # ycalib = [np.diff(calib_intensities[2:4])[0]/np.sum(calib_intensities[2:4]), grad]
        ycalib = [0, grad]
        self.y_diodes.set_target_value(0).wait()
        return xcalib, ycalib

    def set_calibration_values_position(self, xcalib, ycalib):
        channels = ["SLAAR21-LTIM01-EVR0:CALCX.INPJ", "SLAAR21-LTIM01-EVR0:CALCX.INPI"]
        # txcalib = [-1*xcalib[0],-1*xcalib[1]]
        for tc, tv in zip(channels, xcalib):
            PV(tc).put(bytes(str(tv), "utf8"))
        channels = ["SLAAR21-LTIM01-EVR0:CALCY.INPJ", "SLAAR21-LTIM01-EVR0:CALCY.INPI"]
        for tc, tv in zip(channels, ycalib):
            PV(tc).put(bytes(str(tv), "utf8"))

    def calibrate(self, seconds=5):
        c = self.get_calibration_values(seconds=seconds)
        self.set_calibration_values(c)
        xc, yc = self.get_calibration_values_position(c, seconds=seconds)
        self.set_calibration_values_position(xc, yc)

    def set_gains(self, value):
        try:
            self.diode_up.gain.set(value)
            self.diode_down.gain.set(value)
            self.diode_left.gain.set(value)
            self.diode_right.gain.set(value)
        except:
            print("No diodes configured, can not change any gain!")

    def get_available_gains(self):
        try:
            nu = self.diode_up.gain.names
            nd = self.diode_down.gain.names
            nl = self.diode_left.gain.names
            nr = self.diode_right.gain.names
            assert (
                nu == nd == nl == nr
            ), "NB: the gain options of the four diodes are not equal!!!"
            return nu
        except:
            print("No diodes configured, can not change any gain!")

    def get_gains(self):
        try:
            gains = dict()
            gains["up"] = (self.diode_up.gain.get_name(), self.diode_up.gain.get())
            gains["down"] = (
                self.diode_down.gain.get_name(),
                self.diode_down.gain.get(),
            )
            gains["left"] = (
                self.diode_left.gain.get_name(),
                self.diode_left.gain.get(),
            )
            gains["right"] = (
                self.diode_right.gain.get_name(),
                self.diode_right.gain.get(),
            )
            return gains
        except:
            print("No diodes configured, can not change any gain!")


class SolidTargetDetectorPBPS_assi(Assembly):
    def __init__(
        self,
        pvname,
        pvname_fedigitizerchannels=dict(
            up="SAROP21-CVME-PBPS1:Lnk9Ch0",
            down="SAROP21-CVME-PBPS1:Lnk9Ch12",
            left="SAROP21-CVME-PBPS1:Lnk9Ch15",
            right="SAROP21-CVME-PBPS1:Lnk9Ch13",
        ),
        channels_int=None,
        name=None,
    ):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            AdjustablePvEnum,
            pvname + ":PROBE_SP",
            name="target",
            is_setting=True,
            is_display=True,
        )
        self._append(
            MotorRecord,
            pvname + ":MOTOR_X1",
            name="x_diodes",
            is_setting=True,
            is_display=True,
        )
        self._append(
            MotorRecord,
            pvname + ":MOTOR_Y1",
            name="y_diodes",
            is_setting=True,
            is_display=True,
        )
        self._append(
            MotorRecord,
            pvname + ":MOTOR_PROBE",
            name="y_targets",
            is_setting=True,
            is_display=False,
        )
        for chdigi, tp in pvname_fedigitizerchannels.items():
            self._append(
                FeDigitiza,
                tp,
                name="diode_" + chdigi,
                is_setting=True,
                is_display=False,
            )
        self._append(
            AdjustableVirtual,
            [
                self.__dict__["diode_" + chdigi].bias
                for chdigi in pvname_fedigitizerchannels.keys()
            ],
            lambda a, b, c, d: all([x == a for x in [b, c, d]]) * a,
            lambda x: (x, x, x, x),
            name="bias",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustableVirtual,
            [
                self.__dict__["diode_" + chdigi].gain
                for chdigi in pvname_fedigitizerchannels.keys()
            ],
            lambda a, b, c, d: all([x == a for x in [b, c, d]]) * a,
            lambda x: (x, x, x, x),
            name="gain",
            is_setting=False,
            is_display=True,
        )


class SolidTargetDetectorPBPS_new:
    def __init__(
        self,
        pvname,
        VME_crate=None,
        link=None,
        channels={},
        ch_up=12,
        ch_down=13,
        ch_left=15,
        ch_right=14,
        elog=None,
        name=None,
        calc=None,
        calc_calib={},
    ):
        self.name = name
        self.pvname = pvname
        self.alias = Alias(name)
        append_object_to_object(
            self, MotorRecord, pvname + ":MOTOR_X1", name="x_diodes"
        )
        append_object_to_object(
            self, MotorRecord, pvname + ":MOTOR_Y1", name="y_diodes"
        )
        append_object_to_object(
            self, MotorRecord, pvname + ":MOTOR_PROBE", name="target_y"
        )
        append_object_to_object(
            self, AdjustablePvEnum, pvname + ":PROBE_SP", name="target"
        )
        if VME_crate:
            self.diode_up = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_up))
            self.diode_down = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_down))
            self.diode_left = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_left))
            self.diode_right = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_right))

        if channels:
            append_object_to_object(
                self, DetectorPvDataStream, channels["up"], name="signal_up"
            )
            append_object_to_object(
                self, DetectorPvDataStream, channels["down"], name="signal_down"
            )
            append_object_to_object(
                self, DetectorPvDataStream, channels["left"], name="signal_left"
            )
            append_object_to_object(
                self, DetectorPvDataStream, channels["right"], name="signal_right"
            )

        if calc:
            append_object_to_object(
                self, DetectorPvDataStream, calc["itot"], name="intensity"
            )
            append_object_to_object(
                self, DetectorPvDataStream, calc["xpos"], name="xpos"
            )
            append_object_to_object(
                self, DetectorPvDataStream, calc["ypos"], name="ypos"
            )

    def get_calibration_values(self, seconds=5):
        self.x_diodes.set_target_value(0).wait()
        self.y_diodes.set_target_value(0).wait()
        ds = [self.signal_up, self.signal_down, self.signal_left, self.signal_right]
        aqs = [d.acquire(seconds=seconds) for d in ds]
        data = [aq.wait() for aq in aqs]
        mean = [np.mean(td) for td in data]
        std = [np.std(td) for td in data]
        norm_diodes = [1 / tm / 4 for tm in mean]
        return norm_diodes

    def set_calibration_values(self, norm_diodes):
        # this is now only for bernina when using the ioxos from sla
        channels = [
            "SLAAR21-LTIM01-EVR0:CALCI.INPG",
            "SLAAR21-LTIM01-EVR0:CALCI.INPH",
            "SLAAR21-LTIM01-EVR0:CALCI.INPE",
            "SLAAR21-LTIM01-EVR0:CALCI.INPF",
        ]
        for tc, tv in zip(channels, norm_diodes):
            PV(tc).put(bytes(str(tv), "utf8"), wait=True, timeout=8)
        channels = ["SLAAR21-LTIM01-EVR0:CALCX.INPE", "SLAAR21-LTIM01-EVR0:CALCX.INPF"]
        for tc, tv in zip(channels, norm_diodes[2:4]):
            PV(tc).put(bytes(str(tv), "utf8"), wait=True, timeout=8)
        channels = ["SLAAR21-LTIM01-EVR0:CALCY.INPE", "SLAAR21-LTIM01-EVR0:CALCY.INPF"]
        for tc, tv in zip(channels, norm_diodes[0:2]):
            PV(tc).put(bytes(str(tv), "utf8"), wait=True, timeout=8)

    def get_calibration_values_position(
        self, calib_intensities, seconds=5, motion_range=0.2
    ):
        self.x_diodes.set_limits(-motion_range / 2 - 0.1, +motion_range / 2 + 0.1)
        self.y_diodes.set_limits(-motion_range / 2 - 0.1, +motion_range / 2 + 0.1)
        self.x_diodes.set_target_value(0).wait()
        self.y_diodes.set_target_value(0).wait()
        raw = []
        for pos in [motion_range / 2, -motion_range / 2]:
            print(pos)
            self.x_diodes.set_target_value(pos).wait()
            aqs = [
                ts.acquire(seconds=seconds)
                for ts in [self.signal_left, self.signal_right]
            ]
            vals = [
                np.mean(aq.wait()) * calib
                for aq, calib in zip(aqs, calib_intensities[0:2])
            ]
            raw.append((vals[0] - vals[1]) / (vals[0] + vals[1]))
        grad = motion_range / np.diff(raw)[0]
        # xcalib = [np.diff(calib_intensities[0:2])[0]/np.sum(calib_intensities[0:2]), grad]
        xcalib = [0, grad]
        self.x_diodes.set_target_value(0).wait()
        raw = []
        for pos in [motion_range / 2, -motion_range / 2]:
            self.y_diodes.set_target_value(pos).wait()
            aqs = [
                ts.acquire(seconds=seconds) for ts in [self.signal_up, self.signal_down]
            ]
            vals = [
                np.mean(aq.wait()) * calib
                for aq, calib in zip(aqs, calib_intensities[2:4])
            ]
            raw.append((vals[0] - vals[1]) / (vals[0] + vals[1]))
        grad = motion_range / np.diff(raw)[0]
        # ycalib = [np.diff(calib_intensities[2:4])[0]/np.sum(calib_intensities[2:4]), grad]
        ycalib = [0, grad]
        self.y_diodes.set_target_value(0).wait()
        return xcalib, ycalib

    def set_calibration_values_position(self, xcalib, ycalib):
        channels = ["SLAAR21-LTIM01-EVR0:CALCX.INPJ", "SLAAR21-LTIM01-EVR0:CALCX.INPI"]
        # txcalib = [-1*xcalib[0],-1*xcalib[1]]
        for tc, tv in zip(channels, xcalib):
            PV(tc).put(bytes(str(tv), "utf8"), wait=True)
        channels = ["SLAAR21-LTIM01-EVR0:CALCY.INPJ", "SLAAR21-LTIM01-EVR0:CALCY.INPI"]
        for tc, tv in zip(channels, ycalib):
            PV(tc).put(bytes(str(tv), "utf8"), wait=True)

    def calibrate(self, seconds=5):
        c = self.get_calibration_values(seconds=seconds)
        self.set_calibration_values(c)
        xc, yc = self.get_calibration_values_position(c, seconds=seconds)
        self.set_calibration_values_position(xc, yc)

    def __repr__(self):
        s = f"**Intensity  monitor {self.name}**\n\n"

        s += f"Target in: {self.target.get_current_value().name}\n\n"
        try:
            sd = "**Biasd voltage**\n"
            sd += " - Diode up: %.4f\n" % (sdelf.diode_up.get_biasd())
            sd += " - Diode down: %.4f\n" % (sdelf.diode_down.get_biasd())
            sd += " - Diode left: %.4f\n" % (sdelf.diode_left.get_biasd())
            sd += " - Diode right: %.4f\n" % (sdelf.diode_right.get_biasd())
            sd += "\n"

            sd += "**Gain**\n"
            sd += " - Diode up: %i\n" % (sdelf.diode_up.gain.get())
            sd += " - Diode down: %i\n" % (sdelf.diode_down.gain.get())
            sd += " - Diode left: %i\n" % (sdelf.diode_left.gain.get())
            sd += " - Diode right: %i\n" % (sdelf.diode_right.gain.get())
            s += sd
        except:
            pass
        return s

    def set_gains(self, value):
        try:
            self.diode_up.gain.set(value)
            self.diode_down.gain.set(value)
            self.diode_left.gain.set(value)
            self.diode_right.gain.set(value)
        except:
            print("No diodes configured, can not change any gain!")

    def get_available_gains(self):
        try:
            nu = self.diode_up.gain.names
            nd = self.diode_down.gain.names
            nl = self.diode_left.gain.names
            nr = self.diode_right.gain.names
            assert (
                nu == nd == nl == nr
            ), "NB: the gain options of the four diodes are not equal!!!"
            return nu
        except:
            print("No diodes configured, can not change any gain!")

    def get_gains(self):
        try:
            gains = dict()
            gains["up"] = (self.diode_up.gain.get_name(), self.diode_up.gain.get())
            gains["down"] = (
                self.diode_down.gain.get_name(),
                self.diode_down.gain.get(),
            )
            gains["left"] = (
                self.diode_left.gain.get_name(),
                self.diode_left.gain.get(),
            )
            gains["right"] = (
                self.diode_right.gain.get_name(),
                self.diode_right.gain.get(),
            )
            return gains
        except:
            print("No diodes configured, can not change any gain!")

        # SAROP21-CVME-PBPS:Lnk10Ch15-WD-gain


class SolidTargetDetectorPBPS_new_assembly(Assembly):
    def __init__(
        self,
        pvname,
        VME_crate=None,
        link=None,
        channels={},
        ch_up=12,
        ch_down=13,
        ch_left=15,
        ch_right=14,
        elog=None,
        name=None,
        calc=None,
        calc_calib={},
    ):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            MotorRecord, "SARES20-DSD:MOTOR_DSDX", name="xbase", is_setting=True
        )
        self._append(
            MotorRecord, pvname + ":MOTOR_X1", name="x_diodes", is_setting=True
        )
        self._append(
            MotorRecord, pvname + ":MOTOR_Y1", name="y_diodes", is_setting=True
        )
        self._append(
            MotorRecord, pvname + ":MOTOR_PROBE", name="target_y", is_setting=True
        )
        self._append(
            AdjustablePvEnum, pvname + ":PROBE_SP", name="target", is_setting=True
        )
        if VME_crate:
            self.diode_up = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_up))
            self.diode_down = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_down))
            self.diode_left = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_left))
            self.diode_right = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_right))

        if channels:
            self._append(
                DetectorPvDataStream, channels["up"], name="signal_up", is_setting=False
            )
            self._append(
                DetectorPvDataStream,
                channels["down"],
                name="signal_down",
                is_setting=False,
            )
            self._append(
                DetectorPvDataStream,
                channels["left"],
                name="signal_left",
                is_setting=False,
            )
            self._append(
                DetectorPvDataStream,
                channels["right"],
                name="signal_right",
                is_setting=False,
            )

        if calc:
            self._append(
                DetectorPvDataStream, calc["itot"], name="intensity", is_setting=False
            )
            self._append(
                DetectorPvDataStream, calc["xpos"], name="xpos", is_setting=False
            )
            self._append(
                DetectorPvDataStream, calc["ypos"], name="ypos", is_setting=False
            )

    def get_calibration_values(self, seconds=5):
        self.x_diodes.set_target_value(0).wait()
        self.y_diodes.set_target_value(0).wait()
        ds = [self.signal_up, self.signal_down, self.signal_left, self.signal_right]
        aqs = [d.acquire(seconds=seconds) for d in ds]
        data = [aq.wait() for aq in aqs]
        mean = [np.mean(td) for td in data]
        std = [np.std(td) for td in data]
        norm_diodes = [1 / tm / 4 for tm in mean]
        return norm_diodes

    def set_calibration_values(self, norm_diodes):
        # this is now only for bernina when using the ioxos from sla
        channels = [
            "SLAAR21-LTIM01-EVR0:CALCI.INPG",
            "SLAAR21-LTIM01-EVR0:CALCI.INPH",
            "SLAAR21-LTIM01-EVR0:CALCI.INPE",
            "SLAAR21-LTIM01-EVR0:CALCI.INPF",
        ]
        for tc, tv in zip(channels, norm_diodes):
            PV(tc).put(bytes(str(tv), "utf8"))
        channels = ["SLAAR21-LTIM01-EVR0:CALCX.INPE", "SLAAR21-LTIM01-EVR0:CALCX.INPF"]
        for tc, tv in zip(channels, norm_diodes[2:4]):
            PV(tc).put(bytes(str(tv), "utf8"))
        channels = ["SLAAR21-LTIM01-EVR0:CALCY.INPE", "SLAAR21-LTIM01-EVR0:CALCY.INPF"]
        for tc, tv in zip(channels, norm_diodes[0:2]):
            PV(tc).put(bytes(str(tv), "utf8"))

    def get_calibration_values_position(
        self, calib_intensities, seconds=5, motion_range=0.2
    ):
        self.x_diodes.set_limits(-motion_range / 2 - 0.1, +motion_range / 2 + 0.1)
        self.y_diodes.set_limits(-motion_range / 2 - 0.1, +motion_range / 2 + 0.1)
        self.x_diodes.set_target_value(0).wait()
        self.y_diodes.set_target_value(0).wait()
        raw = []
        for pos in [motion_range / 2, -motion_range / 2]:
            print(pos)
            self.x_diodes.set_target_value(pos).wait()
            aqs = [
                ts.acquire(seconds=seconds)
                for ts in [self.signal_left, self.signal_right]
            ]
            vals = [
                np.mean(aq.wait()) * calib
                for aq, calib in zip(aqs, calib_intensities[0:2])
            ]
            raw.append((vals[0] - vals[1]) / (vals[0] + vals[1]))
        grad = motion_range / np.diff(raw)[0]
        # xcalib = [np.diff(calib_intensities[0:2])[0]/np.sum(calib_intensities[0:2]), grad]
        xcalib = [0, grad]
        self.x_diodes.set_target_value(0).wait()
        raw = []
        for pos in [motion_range / 2, -motion_range / 2]:
            self.y_diodes.set_target_value(pos).wait()
            aqs = [
                ts.acquire(seconds=seconds) for ts in [self.signal_up, self.signal_down]
            ]
            vals = [
                np.mean(aq.wait()) * calib
                for aq, calib in zip(aqs, calib_intensities[2:4])
            ]
            raw.append((vals[0] - vals[1]) / (vals[0] + vals[1]))
        grad = motion_range / np.diff(raw)[0]
        # ycalib = [np.diff(calib_intensities[2:4])[0]/np.sum(calib_intensities[2:4]), grad]
        ycalib = [0, grad]
        self.y_diodes.set_target_value(0).wait()
        return xcalib, ycalib

    def set_calibration_values_position(self, xcalib, ycalib):
        channels = ["SLAAR21-LTIM01-EVR0:CALCX.INPJ", "SLAAR21-LTIM01-EVR0:CALCX.INPI"]
        # txcalib = [-1*xcalib[0],-1*xcalib[1]]
        for tc, tv in zip(channels, xcalib):
            PV(tc).put(bytes(str(tv), "utf8"))
        channels = ["SLAAR21-LTIM01-EVR0:CALCY.INPJ", "SLAAR21-LTIM01-EVR0:CALCY.INPI"]
        for tc, tv in zip(channels, ycalib):
            PV(tc).put(bytes(str(tv), "utf8"))

    def calibrate(self, seconds=5):
        c = self.get_calibration_values(seconds=seconds)
        self.set_calibration_values(c)
        xc, yc = self.get_calibration_values_position(c, seconds=seconds)
        self.set_calibration_values_position(xc, yc)

    def set_gains(self, value):
        try:
            self.diode_up.gain.set(value)
            self.diode_down.gain.set(value)
            self.diode_left.gain.set(value)
            self.diode_right.gain.set(value)
        except:
            print("No diodes configured, can not change any gain!")

    def get_available_gains(self):
        try:
            nu = self.diode_up.gain.names
            nd = self.diode_down.gain.names
            nl = self.diode_left.gain.names
            nr = self.diode_right.gain.names
            assert (
                nu == nd == nl == nr
            ), "NB: the gain options of the four diodes are not equal!!!"
            return nu
        except:
            print("No diodes configured, can not change any gain!")

    def get_gains(self):
        try:
            gains = dict()
            gains["up"] = (self.diode_up.gain.get_name(), self.diode_up.gain.get())
            gains["down"] = (
                self.diode_down.gain.get_name(),
                self.diode_down.gain.get(),
            )
            gains["left"] = (
                self.diode_left.gain.get_name(),
                self.diode_left.gain.get(),
            )
            gains["right"] = (
                self.diode_right.gain.get_name(),
                self.diode_right.gain.get(),
            )
            return gains
        except:
            print("No diodes configured, can not change any gain!")


class SolidTargetDetectorPBPS_assembly(Assembly):
    def __init__(
        self,
        pvname,
        VME_crate=None,
        pipeline=None,
        link=None,
        channels={},
        ch_up=12,
        ch_down=13,
        ch_left=15,
        ch_right=14,
        elog=None,
        name=None,
        calc=None,
        calc_calib={},
    ):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            MotorRecord, pvname + ":MOTOR_X1", name="x_diodes", is_setting=True
        )
        self._append(
            MotorRecord, pvname + ":MOTOR_Y1", name="y_diodes", is_setting=True
        )
        self._append(
            MotorRecord, pvname + ":MOTOR_PROBE", name="target_y", is_setting=True
        )
        self._append(
            AdjustablePvEnum, pvname + ":PROBE_SP", name="target", is_setting=True
        )
        self
        if VME_crate:
            self.diode_up = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_up))
            self.diode_down = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_down))
            self.diode_left = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_left))
            self.diode_right = FeDigitizer("%s:Lnk%dCh%d" % (VME_crate, link, ch_right))

        if channels:
            self._append(
                DetectorPvDataStream, channels["up"], name="signal_up", is_setting=False
            )
            self._append(
                DetectorPvDataStream,
                channels["down"],
                name="signal_down",
                is_setting=False,
            )
            self._append(
                DetectorPvDataStream,
                channels["left"],
                name="signal_left",
                is_setting=False,
            )
            self._append(
                DetectorPvDataStream,
                channels["right"],
                name="signal_right",
                is_setting=False,
            )

        if calc:
            self._append(
                DetectorPvDataStream, calc["itot"], name="intensity", is_setting=False
            )
            self._append(
                DetectorPvDataStream, calc["xpos"], name="xpos", is_setting=False
            )
            self._append(
                DetectorPvDataStream, calc["ypos"], name="ypos", is_setting=False
            )

    def get_calibration_values(self, seconds=5):
        self.x_diodes.set_target_value(0).wait()
        self.y_diodes.set_target_value(0).wait()
        ds = [self.signal_up, self.signal_down, self.signal_left, self.signal_right]
        aqs = [d.acquire(seconds=seconds) for d in ds]
        data = [aq.wait() for aq in aqs]
        mean = [np.mean(td) for td in data]
        std = [np.std(td) for td in data]
        norm_diodes = [1 / tm / 4 for tm in mean]
        return norm_diodes

    def set_calibration_values(self, norm_diodes):
        # this is now only for bernina when using the ioxos from sla
        channels = [
            "SLAAR21-LTIM01-EVR0:CALCI.INPG",
            "SLAAR21-LTIM01-EVR0:CALCI.INPH",
            "SLAAR21-LTIM01-EVR0:CALCI.INPE",
            "SLAAR21-LTIM01-EVR0:CALCI.INPF",
        ]
        for tc, tv in zip(channels, norm_diodes):
            PV(tc).put(bytes(str(tv), "utf8"))
        channels = ["SLAAR21-LTIM01-EVR0:CALCX.INPE", "SLAAR21-LTIM01-EVR0:CALCX.INPF"]
        for tc, tv in zip(channels, norm_diodes[2:4]):
            PV(tc).put(bytes(str(tv), "utf8"))
        channels = ["SLAAR21-LTIM01-EVR0:CALCY.INPE", "SLAAR21-LTIM01-EVR0:CALCY.INPF"]
        for tc, tv in zip(channels, norm_diodes[0:2]):
            PV(tc).put(bytes(str(tv), "utf8"))

    def get_calibration_values_position(
        self, calib_intensities, seconds=5, motion_range=0.2
    ):
        self.x_diodes.set_limits(-motion_range / 2 - 0.1, +motion_range / 2 + 0.1)
        self.y_diodes.set_limits(-motion_range / 2 - 0.1, +motion_range / 2 + 0.1)
        self.x_diodes.set_target_value(0).wait()
        self.y_diodes.set_target_value(0).wait()
        raw = []
        for pos in [motion_range / 2, -motion_range / 2]:
            print(pos)
            self.x_diodes.set_target_value(pos).wait()
            aqs = [
                ts.acquire(seconds=seconds)
                for ts in [self.signal_left, self.signal_right]
            ]
            vals = [
                np.mean(aq.wait()) * calib
                for aq, calib in zip(aqs, calib_intensities[0:2])
            ]
            raw.append((vals[0] - vals[1]) / (vals[0] + vals[1]))
        grad = motion_range / np.diff(raw)[0]
        # xcalib = [np.diff(calib_intensities[0:2])[0]/np.sum(calib_intensities[0:2]), grad]
        xcalib = [0, grad]
        self.x_diodes.set_target_value(0).wait()
        raw = []
        for pos in [motion_range / 2, -motion_range / 2]:
            self.y_diodes.set_target_value(pos).wait()
            aqs = [
                ts.acquire(seconds=seconds) for ts in [self.signal_up, self.signal_down]
            ]
            vals = [
                np.mean(aq.wait()) * calib
                for aq, calib in zip(aqs, calib_intensities[2:4])
            ]
            raw.append((vals[0] - vals[1]) / (vals[0] + vals[1]))
        grad = motion_range / np.diff(raw)[0]
        # ycalib = [np.diff(calib_intensities[2:4])[0]/np.sum(calib_intensities[2:4]), grad]
        ycalib = [0, grad]
        self.y_diodes.set_target_value(0).wait()
        return xcalib, ycalib

    def set_calibration_values_position(self, xcalib, ycalib):
        channels = ["SLAAR21-LTIM01-EVR0:CALCX.INPJ", "SLAAR21-LTIM01-EVR0:CALCX.INPI"]
        # txcalib = [-1*xcalib[0],-1*xcalib[1]]
        for tc, tv in zip(channels, xcalib):
            PV(tc).put(bytes(str(tv), "utf8"))
        channels = ["SLAAR21-LTIM01-EVR0:CALCY.INPJ", "SLAAR21-LTIM01-EVR0:CALCY.INPI"]
        for tc, tv in zip(channels, ycalib):
            PV(tc).put(bytes(str(tv), "utf8"))

    def calibrate(self, seconds=5):
        c = self.get_calibration_values(seconds=seconds)
        self.set_calibration_values(c)
        xc, yc = self.get_calibration_values_position(c, seconds=seconds)
        self.set_calibration_values_position(xc, yc)

    def set_gains(self, value):
        try:
            self.diode_up.gain.set(value)
            self.diode_down.gain.set(value)
            self.diode_left.gain.set(value)
            self.diode_right.gain.set(value)
        except:
            print("No diodes configured, can not change any gain!")

    def get_available_gains(self):
        try:
            nu = self.diode_up.gain.names
            nd = self.diode_down.gain.names
            nl = self.diode_left.gain.names
            nr = self.diode_right.gain.names
            assert (
                nu == nd == nl == nr
            ), "NB: the gain options of the four diodes are not equal!!!"
            return nu
        except:
            print("No diodes configured, can not change any gain!")

    def get_gains(self):
        try:
            gains = dict()
            gains["up"] = (self.diode_up.gain.get_name(), self.diode_up.gain.get())
            gains["down"] = (
                self.diode_down.gain.get_name(),
                self.diode_down.gain.get(),
            )
            gains["left"] = (
                self.diode_left.gain.get_name(),
                self.diode_left.gain.get(),
            )
            gains["right"] = (
                self.diode_right.gain.get_name(),
                self.diode_right.gain.get(),
            )
            return gains
        except:
            print("No diodes configured, can not change any gain!")
