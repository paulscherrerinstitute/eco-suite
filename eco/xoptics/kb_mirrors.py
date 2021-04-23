from ..elements.assembly import Assembly
from ..devices_general.motors import MotorRecord
from ..devices_general.adjustable import PvRecord, PvEnum


class KbVer(Assembly):
    def __init__(self, pvname, name=None):
        self.pvname = pvname
        super().__init__(name=name)
        self._append(
            MotorRecord, pvname + ":W_X", name="x", is_setting=False, is_status=True
        )
        self._append(
            MotorRecord, pvname + ":W_Y", name="y", is_setting=False, is_status=True
        )
        self._append(
            MotorRecord,
            pvname + ":W_RX",
            name="pitch",
            is_setting=False,
            is_status=True,
        )
        self._append(
            MotorRecord, pvname + ":W_RZ", name="roll", is_setting=False, is_status=True
        )
        self._append(
            MotorRecord, pvname + ":W_RY", name="yaw", is_setting=False, is_status=True
        )
        self._append(MotorRecord, pvname + ":BU", name="bend1", is_setting=True)
        self._append(MotorRecord, pvname + ":BD", name="bend2", is_setting=True)
        self._append(
            PvRecord,
            pvname + ":CURV_SP",
            pvreadbackname=pvname + ":CURV",
            accuracy=0.002,
            name="curv",
        )
        self._append(
            PvRecord,
            pvname + ":ASYMMETRY_SP",
            pvreadbackname=pvname + ":CURV",
            accuracy=0.002,
            name="asym",
        )

        #### actual motors ###
        self._append(MotorRecord, pvname + ":TY1", name="_Y1", is_setting=True)
        self._append(MotorRecord, pvname + ":TY2", name="_Y2", is_setting=True)
        self._append(MotorRecord, pvname + ":TY3", name="_Y3", is_setting=True)
        self._append(MotorRecord, pvname + ":TX1", name="_X1", is_setting=True)
        self._append(MotorRecord, pvname + ":TX2", name="_X2", is_setting=True)


class KbHor(Assembly):
    def __init__(self, pvname, name=None):
        self.pvname = pvname
        super().__init__(name=name)
        self._append(
            MotorRecord, pvname + ":W_X", name="x", is_setting=False, is_status=True
        )
        self._append(
            MotorRecord, pvname + ":W_Y", name="y", is_setting=False, is_status=True
        )
        self._append(
            MotorRecord,
            pvname + ":W_RY",
            name="pitch",
            is_setting=False,
            is_status=True,
        )
        self._append(
            MotorRecord, pvname + ":W_RZ", name="roll", is_setting=False, is_status=True
        )
        self._append(
            MotorRecord, pvname + ":W_RX", name="yaw", is_setting=False, is_status=True
        )
        self._append(MotorRecord, pvname + ":BU", name="bend1", is_setting=True)
        self._append(MotorRecord, pvname + ":BD", name="bend2", is_setting=True)
        self._append(
            PvRecord,
            pvname + ":CURV_SP",
            pvreadbackname=pvname + ":CURV",
            accuracy=0.002,
            name="curv",
        )
        self._append(
            PvRecord,
            pvname + ":ASYMMETRY_SP",
            pvreadbackname=pvname + ":CURV",
            accuracy=0.002,
            name="asym",
        )

        #### actual motors ###
        self._append(MotorRecord, pvname + ":TY1", name="_Y1", is_setting=True)
        self._append(MotorRecord, pvname + ":TY2", name="_Y2", is_setting=True)
        self._append(MotorRecord, pvname + ":TY3", name="_Y3", is_setting=True)
        self._append(MotorRecord, pvname + ":TX1", name="_X1", is_setting=True)
        self._append(MotorRecord, pvname + ":TX2", name="_X2", is_setting=True)
