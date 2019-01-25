from ..devices_general.motors import MotorRecord
from ..devices_general.detectors import CameraCA, CameraBS
from ..devices_general.pv_adjustable import PvRecord
from ..aliases import Alias

# from ..devices_general.epics_wrappers import EnumSelector
from epics import PV
from ..eco_epics.utilities_epics import EnumWrapper


def addMotorRecordToSelf(self, Id=None, name=None):
    self.__dict__[name] = MotorRecord(Id, name=name)
    self.alias.append(self.__dict__[name].alias)

def addPvRecordToSelf(self, name=None, pvsetname=None, pvreadbackname = None, accuracy = None):
    self.__dict__[name] = PvRecord(name=name, pvsetname=pvsetname, pvreadbackname = pvreadbackname , accuracy = accuracy)
    self.alias.append(self.__dict__[name].alias)



class Sigma:
    def __init__(self, Id, bshost=None, bsport=None, name=None):
        self.alias = Alias(name)

        self.Id = Id
        
        addPvRecordToSelf(self, name="zoom", pvsetname="SARES20-OPSI:MOT_SP", pvreadbackname = "SARES20-OPSI:MOT_RB")

        #except:
        #    print("Sigma zoom motor not found")
        #    pass
        try:
            self.cam = CameraCA(Id)
        except:
            print("Sigma Cam not found")
            pass

        if bshost:
            self.camBS = CameraBS(host=bshost, port=bsport)

    def get_adjustable_positions_str(self):
        ostr = "*****Qioptic motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def __repr__(self):
        return self.get_adjustable_positions_str()



class Qioptic:
    def __init__(self, Id, bshost=None, bsport=None, name=None):
        self.alias = Alias(name)

        self.Id = Id
        try:
            addMotorRecordToSelf(self, Id="SARES20-EXP:MOT_QIOPT_Z", name="zoom")

        except:
            print("Qioptic zoom motor not found")
            pass
        try:
            addMotorRecordToSelf(self, Id="SARES20-EXP:MOT_QIOPT_F", name="focus")

        except:
            print("Qioptic focus motor not found")
            pass
        try:
            self.cam = CameraCA(Id)
        except:
            print("Qioptic Cam not found")
            pass

        if bshost:
            self.camBS = CameraBS(host=bshost, port=bsport)

    def get_adjustable_positions_str(self):
        ostr = "*****Qioptic motor positions******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        return ostr

    def __repr__(self):
        return self.get_adjustable_positions_str()
