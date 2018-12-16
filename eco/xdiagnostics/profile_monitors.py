from ..devices_general.motors import MotorRecord
from ..devices_general.detectors import CameraCA, CameraBS
from ..aliases import Alias

# from ..devices_general.epics_wrappers import EnumSelector
from epics import PV
from ..eco_epics.utilities_epics import EnumWrapper


def addMotorRecordToSelf(self, Id=None, name=None):
    self.__dict__[name] = MotorRecord(Id, name=name)
    self.alias.append(self.__dict__[name].alias)


class Pprm:
    def __init__(self, Id):
        self.Id = Id
        self.targetY = MotorRecord(Id + ":MOTOR_PROBE")
        self.cam = CameraCA(Id)
        self._led = PV(self.Id + ":LED")
        self.target = EnumWrapper(self.Id + ":PROBE_SP")

    def movein(self, target=1):
        self.target.set(target)

    def moveout(self, target=0):
        self.target.set(target)

    def illuminate(self, value=None):
        if value:
            self._led.put(value)
        else:
            self._led.put(not self.get_illumination_state())

    def get_illumination_state(self):
        return bool(self._led.get())

    def __repr__(self):
        s = "**Profile Monitor**\n"
        s += "Target: %s" % (self.target.get_name())
        return s


class Bernina_XEYE:
    def __init__(self, Id, bshost=None, bsport=None, name=None):
        self.alias = Alias(name)

        self.Id = Id
        try:
            addMotorRecordToSelf(self, Id="SARES20-EXP:MOT_NAV_Z", name="zoom")

        except:
            print("X-Ray eye zoom motor not found")
            pass
        try:
            self.cam = CameraCA(Id)
        except:
            print("X-Ray eye Cam not found")
            pass

        if bshost:
            self.camBS = CameraBS(host=bshost, port=bsport)

    def get_adjustable_positions_str(self):
        ostr = "*****Xeye motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def __repr__(self):
        return self.get_adjustable_positions_str()


#        self._led = PV(self.Id+':LED')


#    def illuminate(self,value=None):
#        if value:
#            self._led.put(value)
#        else:
#            self._led.put(
#                    not self.get_illumination_state())
#
#    def get_illumination_state(self):
#        return bool(self._led.get())
#
