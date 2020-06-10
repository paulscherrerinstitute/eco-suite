import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord
from ..devices_general.adjustable import PvRecord

from epics import PV
from ..aliases import Alias, append_object_to_object
import datetime


def addMotorRecordToSelf(self, name=None, Id=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print("Warning! Could not find motor {name} (Id:{Id})")


class OffsetMirror:
    def __init__(self, name=None, Id=None, alias_namespace=None):
        self.Id = Id
        self.name = name
        self.alias = Alias(name)

        addMotorRecordToSelf(self, Id=Id + ":W_X", name="x")
        addMotorRecordToSelf(self, Id=Id + ":W_Y", name="y")
        addMotorRecordToSelf(self, Id=Id + ":W_RX", name="rx")
        addMotorRecordToSelf(self, Id=Id + ":W_RZ", name="rz")
        append_object_to_object(
            self,
            PvRecord,
            Id + ":CURV_SP",
            pvreadbackname=Id + ":CURV",
            accuracy=None,
            name="curvature",
            elog=None,
        )
        append_object_to_object(
            self,
            PvRecord,
            Id + ":ASYMMETRY_SP",
            pvreadbackname=Id + ":ASYMMETRY",
            accuracy=None,
            name="asymmetry",
            elog=None,
        )

    def out(self):
        pass

    def move_in(self):
        pass

    def get_status(self):
        s = f'Offset mirror {self.alias.get_full_name()} status ({datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")})\n'
        for var in ["x", "y", "rx", "rz", "curvature", "asymmetry"]:
            s += (
                " " * 4
                + var.ljust(16)
                + f"{self.__dict__[var].get_current_value():g}\n"
            )
        return s

    def __str__(self):
        return self.get_status()

    def __repr__(self):
        return self.__str__()
