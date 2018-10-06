from epics import PV
from ..devices_general.utilities import Changer
from time import sleep
import numpy as np


class Pulse_Picker:
    def __init__(self, Id):
        self.Id = Id

        self._start = PV(Id + ":seq0Ctrl-Start-I")
        self._stop = PV(Id + ":seq0Ctrl-Stop-I")
        self._cycles = PV(Id + ":seq0Ctrl-Cycles-I")

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
