from ..elements.assembly import Assembly
from ..devices_general.motors import MotorRecord
from ..elements.adjustable import AdjustableVirtual
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
import numpy as np
from epics import PV


class KbVer(Assembly):
    def __init__(self, pvname, name=None):
        self.pvname = pvname
        super().__init__(name=name)
        self._append(
            MotorRecord, pvname + ":W_X", name="x", is_setting=False, is_display=True
        )
        self._append(
            MotorRecord, pvname + ":W_Y", name="y", is_setting=False, is_display=True
        )
        self._append(
            MotorRecord,
            pvname + ":W_RX",
            name="pitch",
            is_setting=False,
            is_display=True,
        )
        self._append(
            MotorRecord,
            pvname + ":W_RZ",
            name="roll",
            is_setting=False,
            is_display=True,
        )
        self._append(
            MotorRecord, pvname + ":W_RY", name="yaw", is_setting=False, is_display=True
        )
        self._append(MotorRecord, pvname + ":BU", name="bend1", is_setting=True)
        self._append(MotorRecord, pvname + ":BD", name="bend2", is_setting=True)
        self._append(
            AdjustableVirtual,
            [self.bend1, self.bend2],
            lambda b1, b2: float(np.mean([b1, b2])),
            lambda mn: self._get_benders_set_mean(mn),
            name="bender_mean",
            unit="mm",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustableVirtual,
            [self.bend1, self.bend2],
            lambda b1, b2: float(np.diff([b1, b2])),
            lambda mn: self._get_benders_set_diff(mn),
            name="bender_diff",
            unit="mm",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            pvname + ":CURV_SP",
            pvreadbackname=pvname + ":CURV",
            accuracy=0.002,
            name="curv",
        )
        self._append(
            AdjustablePv,
            pvname + ":ASYMMETRY_SP",
            pvreadbackname=pvname + ":ASYMMETRY",
            accuracy=0.002,
            name="asym",
        )

        #### actual motors ###
        self._append(
            MotorRecord, pvname + ":TY1", name="_Y1", is_setting=True, is_display=False
        )
        self._append(
            MotorRecord, pvname + ":TY2", name="_Y2", is_setting=True, is_display=False
        )
        self._append(
            MotorRecord, pvname + ":TY3", name="_Y3", is_setting=True, is_display=False
        )
        self._append(
            MotorRecord, pvname + ":TX1", name="_X1", is_setting=True, is_display=False
        )
        self._append(
            MotorRecord, pvname + ":TX2", name="_X2", is_setting=True, is_display=False
        )
        self._pv_sync_world = PV(pvname + ":SYNC_AXES")
        self._pv_amp_reset = PV(pvname + ":RESET_AMP.PROC")
        self._pv_parkall = PV(pvname + "::KILL_ALL.PROC")
        self._pv_enable_all = PV(pvname + ":ENABLE_ALL.PROC")
        self._pv_sync_all_axes = PV(pvname + ":SYNC.PROC")
        self._pv_safety_on = PV(pvname + ":SAFETY_ON.PROC")
        self._pv_safety_off = PV(pvname + ":SAFETY_OFF.PROC")

    def sync_world(self):
        self._pv_sync_world.put(1)

    def sync_phys_axes(self):
        self._pv_sync_all_axes.put(1)

    def park_all(self):
        self._pv_parkall.put(1)

    def _get_bend_mean(self):
        return float(
            np.mean([self.bend1.get_current_value(), self.bend2.get_current_value()])
        )

    def _get_benders_set_mean(self, val):
        mn = self._get_bend_mean()
        df = val - mn
        return self.bend1.get_current_value() + df, self.bend2.get_current_value() + df

    def _get_bend_diff(self):
        return float(
            np.diff([self.bend1.get_current_value(), self.bend2.get_current_value()])
        )

    def _get_benders_set_diff(self, val):
        df = val - self._get_bend_diff()
        return (
            self.bend1.get_current_value() - df / 2,
            self.bend2.get_current_value() + df / 2,
        )


class KbHor(Assembly):
    def __init__(self, pvname, name=None):
        self.pvname = pvname
        super().__init__(name=name)
        self._append(
            MotorRecord, pvname + ":W_X", name="x", is_setting=False, is_display=True
        )
        self._append(
            MotorRecord, pvname + ":W_Y", name="y", is_setting=False, is_display=True
        )
        self._append(
            MotorRecord,
            pvname + ":W_RY",
            name="pitch",
            is_setting=False,
            is_display=True,
        )
        self._append(
            MotorRecord,
            pvname + ":W_RZ",
            name="roll",
            is_setting=False,
            is_display=True,
        )
        self._append(
            MotorRecord, pvname + ":W_RX", name="yaw", is_setting=False, is_display=True
        )
        self._append(MotorRecord, pvname + ":BU", name="bend1", is_setting=True)
        self._append(MotorRecord, pvname + ":BD", name="bend2", is_setting=True)
        self._append(
            AdjustableVirtual,
            [self.bend1, self.bend2],
            lambda b1, b2: float(np.mean([b1, b2])),
            lambda mn: self._get_benders_set_mean(mn),
            name="bender_mean",
            unit="mm",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustableVirtual,
            [self.bend1, self.bend2],
            lambda b1, b2: float(np.diff([b1, b2])),
            lambda mn: self._get_benders_set_diff(mn),
            name="bender_diff",
            unit="mm",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            pvname + ":CURV_SP",
            pvreadbackname=pvname + ":CURV",
            accuracy=0.002,
            name="curv",
        )
        self._append(
            AdjustablePv,
            pvname + ":ASYMMETRY_SP",
            pvreadbackname=pvname + ":ASYMMETRY",
            accuracy=0.002,
            name="asym",
        )

        #### actual motors ###
        self._append(
            MotorRecord, pvname + ":TY1", name="_Y1", is_setting=True, is_display=False
        )
        self._append(
            MotorRecord, pvname + ":TY2", name="_Y2", is_setting=True, is_display=False
        )
        self._append(
            MotorRecord, pvname + ":TY3", name="_Y3", is_setting=True, is_display=False
        )
        self._append(
            MotorRecord, pvname + ":TX1", name="_X1", is_setting=True, is_display=False
        )
        self._append(
            MotorRecord, pvname + ":TX2", name="_X2", is_setting=True, is_display=False
        )
        self._pv_sync_world = PV(pvname + ":SYNC_AXES")
        self._pv_amp_reset = PV(pvname + ":RESET_AMP.PROC")
        self._pv_parkall = PV(pvname + "::KILL_ALL.PROC")
        self._pv_enable_all = PV(pvname + ":ENABLE_ALL.PROC")
        self._pv_sync_all_axes = PV(pvname + ":SYNC.PROC")
        self._pv_safety_on = PV(pvname + ":SAFETY_ON.PROC")
        self._pv_safety_off = PV(pvname + ":SAFETY_OFF.PROC")

    def sync_world(self):
        self._pv_sync_world.put(1)

    def sync_phys_axes(self):
        self._pv_sync_all_axes.put(1)

    def park_all(self):
        self._pv_parkall.put(1)

    def _get_bend_mean(self):
        return float(
            np.mean([self.bend1.get_current_value(), self.bend2.get_current_value()])
        )

    def _get_benders_set_mean(self, val):
        mn = self._get_bend_mean()
        df = val - mn
        return self.bend1.get_current_value() + df, self.bend2.get_current_value() + df

    def _get_bend_diff(self):
        return float(
            np.diff([self.bend1.get_current_value(), self.bend2.get_current_value()])
        )

    def _get_benders_set_diff(self, val):
        df = val - self._get_bend_diff()
        return (
            self.bend1.get_current_value() - df / 2,
            self.bend2.get_current_value() + df / 2,
        )
