from ..devices_general.motors import MotorRecord
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from ..elements.assembly import Assembly
from ..elements.adjustable import AdjustableVirtual
import numpy as np


class OffsetMirror(Assembly):
    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(MotorRecord, self.pvname + ":W_X", name="x", is_setting=True)
        self._append(MotorRecord, self.pvname + ":W_Y", name="y", is_setting=True)
        self._append(MotorRecord, self.pvname + ":W_RX", name="rx", is_setting=True)
        self._append(MotorRecord, self.pvname + ":W_RZ", name="rz", is_setting=True)
        self._append(
            AdjustablePv,
            self.pvname + ":CURV_SP",
            pvreadbackname=self.pvname + ":CURV",
            accuracy=None,
            name="curvature",
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":ASYMMETRY_SP",
            pvreadbackname=self.pvname + ":ASYMMETRY",
            accuracy=None,
            name="asymmetry",
            is_setting=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname+":COATING",
            pvname_set=self.pvname+":COATING_SP",
            name='coating')


class OffsetMirrorsBernina(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            OffsetMirror,
            "SAROP21-OOMV092",
            name="mirr1",
            is_setting=True,
            is_display="recursive",
        )
        self._append(
            OffsetMirror,
            "SAROP21-OOMV096",
            name="mirr2",
            is_setting=True,
            is_display="recursive",
        )
        self._append(
            AdjustableVirtual,
            [self.mirr1.rz, self.mirr2.rz],
            lambda b1, b2: float(np.mean([b1, b2])),
            lambda mn: self._set_mean_2adj(mn, self.mirr1.rz, self.mirr2.rz),
            name="rz_mean",
            unit="mrad",
            is_setting=False,
            is_display=True,
        )

        self._append(
            AdjustableVirtual,
            [self.mirr1.rz, self.mirr2.rz],
            lambda b1, b2: float(np.diff([b1, b2])),
            lambda mn: self._set_diff_2adj(mn, self.mirr1.rz, self.mirr2.rz),
            name="rz_diff",
            unit="mrad",
            is_setting=False,
            is_display=True,
        )

    def _set_mean_2adj(self, val, adj0, adj1):
        mn = np.mean([adj0.get_current_value(), adj1.get_current_value()])
        df = val - mn
        return adj0.get_current_value() + df, adj1.get_current_value() + df

    def _set_diff_2adj(self, val, adj0, adj1):
        df = val - np.diff([adj0.get_current_value(), adj1.get_current_value()])
        return (
            adj0.get_current_value() - df / 2,
            adj1.get_current_value() + df / 2,
        )
