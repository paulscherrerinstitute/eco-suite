from ..devices_general.motors import MotorRecord
from epics import PV
from ..aliases import Alias, append_object_to_object
from ..elements import Assembly


class RefLaser_Aramis(Assembly):
    def __init__(self, Id, elog=None, name=None, inpos=-18.818, outpos=-5):
        super().__init__(name=name)
        self.Id = Id
        self.elog = elog
        # append_object_to_object(self,

        self._inpos = inpos
        self._outpos = outpos
        self._append(MotorRecord, self.Id + ":MOTOR_1", name="mirror", is_setting=True)
        self._append(MotorRecord, self.Id + ":MOTOR_X1", name="x1", is_setting=True)
        self._append(MotorRecord, self.Id + ":MOTOR_Z1", name="z1", is_setting=True)
        self._append(
            MotorRecord, self.Id + ":MOTOR_ROT_X1", name="rx1", is_setting=True
        )
        self._append(
            MotorRecord, self.Id + ":MOTOR_ROT_Z1", name="rz1", is_setting=True
        )
        pv_lir0 = "SAROP21-OLIR136"  # TODO hardcoded
        self._append(MotorRecord, pv_lir0 + ":MOTOR_MX", name="x_ap1", is_setting=True)
        self._append(MotorRecord, pv_lir0 + ":MOTOR_MY", name="y_ap1", is_setting=True)
        pv_lir1 = "SAROP21-OLIR138"  # TODO hardcoded
        self._append(MotorRecord, pv_lir1 + ":MOTOR_MX", name="x_ap2", is_setting=True)
        self._append(MotorRecord, pv_lir1 + ":MOTOR_MY", name="y_ap2", is_setting=True)
        self.mirror.set_limits(-20, 0)

    def __call__(self, *args, **kwargs):
        self.set(*args, **kwargs)

    def __str__(self):
        status = self.get_in_status()
        if status:
            return "Reflaser is In."
        elif status == False:
            return "Reflaser is Out."
        elif status == None:
            return "Reflaser status not defined."

    def get_in_status(self):
        v = self.mirror.get_current_value()
        if abs(v - self._inpos) < 0.2:
            isin = True
        elif abs(v - self._outpos) < 0.2:
            isin = False
        else:
            isin = None
        return isin

    def set(self, value):
        if type(value) is str:
            if value.lower() == "in":
                value = True
            elif value.lower() == "out":
                value = False
            else:
                print("String %s not recognized!" % value)
        if value:
            self.mirror.set_target_value(self._inpos)
        else:
            self.mirror.set_target_value(self._outpos)

    def movein(self):
        self.set("in")

    def moveout(self):
        self.set("out")

    def __repr__(self):
        return self.__str__() + "\n" + super().__repr__()
