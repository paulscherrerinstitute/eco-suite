import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord
from epics import PV
from ..aliases import Alias


def addMotorRecordToSelf(self, name=None, Id=None):
    self.__dict__[name] = MotorRecord(Id, name=name)
    self.alias.append(self.__dict__[name].alias)


class XRD:
    def __init__(self, name=None, Id=None, configuration=[]):
        """X-ray diffractometer platform in AiwssFEL Bernina.\
                <configuration> : list of elements mounted on 
                the plaform, options are kappa, nutable, hlgonio, polana"""
        self.Id = Id
        self.name = name
        self.alias = Alias(name)

        ### motors base platform ###
        addMotorRecordToSelf(self, Id=Id + ":MOT_TX", name="xbase")
        addMotorRecordToSelf(self, Id=Id + ":MOT_TY", name="ybase")
        addMotorRecordToSelf(self, Id=Id + ":MOT_RX", name="rxbase")
        addMotorRecordToSelf(self, Id=Id + ":MOT_MY_RYTH", name="omega")

        ### motors XRD detector arm ###
        addMotorRecordToSelf(self, Id=Id + ":MOT_NY_RY2TH", name="gamma")
        addMotorRecordToSelf(self, Id=Id + ":MOT_DT_RX2TH", name="delta")

        ### motors XRD area detector branch ###
        addMotorRecordToSelf(self, Id=Id + ":MOT_D_T", name="tdet")

        ### motors XRD polarisation analyzer branch ###
        addMotorRecordToSelf(self, Id=Id + ":MOT_P_T", name="tpol")
        # missing: slits of flight tube

        ### motors heavy load goniometer ###
        addMotorRecordToSelf(self, Id=Id + ":MOT_TBL_TX", name="xhl")
        addMotorRecordToSelf(self, Id=Id + ":MOT_TBL_TZ", name="zhl")
        addMotorRecordToSelf(self, Id=Id + ":MOT_TBL_TY", name="yhl")
        try:
            addMotorRecordToSelf(self, Id=Id + ":MOT_TBL_RX", name="rxhl")
        except:
            print("XRD.rxhl not found")
            pass
        try:
            addMotorRecordToSelf(self, Id=Id + ":MOT_TBL_RY", name="rzhl")
        except:
            print("XRD.rzhl not found")
            pass

        ### motors nu table ###
        addMotorRecordToSelf(self, Id=Id + ":MOT_HEX_TX", name="teta")
        addMotorRecordToSelf(self, Id=Id + ":MOT_HEX_RX", name="eta")

        ### motors PI hexapod ###
        self.hex_x = PV("SARES20-HEX_PI:POSI-X")
        self.hex_y = PV("SARES20-HEX_PI:POSI-Y")
        self.hex_z = PV("SARES20-HEX_PI:POSI-Z")
        self.hex_u = PV("SARES20-HEX_PI:POSI-U")
        self.hex_v = PV("SARES20-HEX_PI:POSI-V")
        self.hex_w = PV("SARES20-HEX_PI:POSI-W")

    def __repr__(self):
        s = "**Heavy Load**\n"
        motors = "xmu mu tth xbase ybase".split()
        for motor in motors:
            s += " - %s %.4f\n" % (motor, getattr(self, motor).wm())

        s += " - xhl %.4f\n" % (self.xhl.wm())
        s += " - yhl %.4f\n" % (self.yhl.wm())
        s += " - zhl %.4f\n" % (self.zhl.wm())
        s += " - th %.4f\n" % (self.th.wm())
        s += "\n"

        s += "**Gonio**\n"
        motors = "xmu mu tth delta det_z cam_z xbase ybase".split()
        for motor in motors:
            s += " - %s %.4f\n" % (motor, getattr(self, motor).wm())
        s += "\n"

        s += "**Hexapod**\n"
        motors = "x y z u v w".split()
        for motor in motors:
            s += " - hex_%s %.4f\n" % (motor, getattr(self, "hex_" + motor).get())
        return s
