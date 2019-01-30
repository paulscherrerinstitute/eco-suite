from epics import PV
from ..devices_general.utilities import Changer
from time import sleep
import numpy as np
from ..devices_general.motors import MotorRecord

from ..aliases import Alias


def addMotorRecordToSelf(self, name=None, Id=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print("Warning! Could not find motor {name} (Id:{Id})")

class Pulsepick:
    def __init__(self, Id=None, evrout=None, name=None):
        self.name = name
        self.alias = Alias(name)
        self.evrout = evrout

        self.Id = Id
        self._start = PV(Id + ":seq0Ctrl-Start-I")
        self._stop = PV(Id + ":seq0Ctrl-Stop-I")
        self._cycles = PV(Id + ":seq0Ctrl-Cycles-I")
        self._openclose = PV(self.evrout)
        addMotorRecordToSelf(self, Id=self.Id + ":MOTOR_X1", name="x")
        addMotorRecordToSelf(self, Id=self.Id + ":MOTOR_Y1", name="y")

    def movein(self):
        self.x.changeTo(4.45)
        self.y.changeTo(-0.9)

    def moveout(self):
        self.x.changeTo(-5)
        self.y.changeTo(-0.9)

    def open(self):
        self._openclose.put(1)
        print("Opened Pulse Picker")

    def close(self):
        self._openclose.put(0)
        print("Closed Pulse Picker")

    def stop(self):
        self._stop.put(0)
        print("Stopped Pulse Picker")

    def start(self):
        self._start.put(0)
        print("Started Pulse Picker")

    def cycles(self, n):
        self._cycles.put(n)

    def scan(self, adj, targetlist, sleeptime=0.1):
        self.stop()
        for target in targetlist:
            changer = adj.changeTo(target)
            changer.wait()
            print("Adjustable position {}".format(adj.get_current_value()))
            self.start()
            sleep(sleeptime)
            self.stop()
        print("done")

    def scan2(self, adj1, adj2, targetlist1, targetlist2, sleeptime=0.1):
        self.stop()
        for n in range(len(targetlist1)):
            changer1 = adj1.changeTo(targetlist1[n])
            changer2 = adj2.changeTo(targetlist2[n])
            changer2.wait()
            print(
                "Adjustable 1 position {}".format(adj1.get_current_value()),
                "Adjustable 2 position {}".format(adj2.get_current_value()),
            )
            self.start()
            sleep(sleeptime)
            self.stop()
        print("done")
