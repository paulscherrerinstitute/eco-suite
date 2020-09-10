from ..devices_general.motors import MotorRecord
from ..devices_general.pv_adjustable import PvRecord
from epics import PV
from ..aliases import Alias, append_object_to_object

def addMotorRecordToSelf(self, Id=None, name=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find motor {name} (Id:{Id})")

def addPvRecordToSelf(self, 
        pvsetname, 
        pvreadbackname=None, 
        accuracy=None, 
        sleeptime=0, 
        name=None
        ):
    try:
        self.__dict__[name] = PvRecord(
            pvsetname,
            pvreadbackname=pvreadbackname,
            accuracy=accuracy,
            sleeptime=sleeptime,
            name=name,
            )
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find PV {name} (Id:{pvsetname} RB:{pvreadbackname})")

class KBver:
    def __init__(self, Id, name=None):
        self.name = name
        self.Id = Id
        self.alias = Alias(name)

        addMotorRecordToSelf(self, Id=Id + ":W_X", name='x')
        addMotorRecordToSelf(self, Id=Id + ":W_Y", name='y')
        addMotorRecordToSelf(self, Id=Id + ":W_RX", name='pitch')
        addMotorRecordToSelf(self, Id=Id + ":W_RZ", name='roll')
        addMotorRecordToSelf(self, Id=Id + ":W_RY", name='yaw')
        addMotorRecordToSelf(self, Id=Id + ":BU", name='bend1')
        addMotorRecordToSelf(self, Id=Id + ":BD", name='bend2')
        addPvRecordToSelf(self, pvsetname=Id + ":CURV_SP", pvreadbackname =Id + ":CURV", accuracy= 0.002, name='curv')
        addPvRecordToSelf(self, pvsetname=Id + ":ASYMMETRY_SP", pvreadbackname =Id + ":ASYMMETRY", accuracy= 0.002, name='asym')

        #self.mode = PV(Id[:11] + ":MODE").enum_strs[PV(Id[:11] + ":MODE").value]

        #### actual motors ###
        addMotorRecordToSelf(self, Id=Id + ":TY1", name='_Y1')
        addMotorRecordToSelf(self, Id=Id + ":TY2", name='_Y2')
        addMotorRecordToSelf(self, Id=Id + ":TY3", name='_Y3')
        addMotorRecordToSelf(self, Id=Id + ":TX1", name='_X1')
        addMotorRecordToSelf(self, Id=Id + ":TX2", name='_X2')

    def __str__(self):
        s = "**Vertical KB mirror**\n"
        motors = "bend1 bend2 pitch roll yaw x y".split()
        for motor in motors:
            s += " - %s = %.4f\n" % (motor, getattr(self, motor).wm())
        s += "\n**Stages**\n"
        stages = "_Y1 _Y2 _Y3 _X1 _X2".split()
        for stage in stages:
            s += " - %s = %.4f\n" % (stage, getattr(self, stage).wm())
        return s

    def __repr__(self):
        return self.__str__()
