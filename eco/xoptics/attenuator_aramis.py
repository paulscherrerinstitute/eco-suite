from ..devices_general.motors import MotorRecord
from epics import PV
from time import sleep


class AttenuatorAramis:
    def __init__(self, Id, E_min=1500, sleeptime=1):
        self.Id = Id
        self.E_min = E_min
        self._pv_status_str = PV(self.Id + ":MOT2TRANS.VALD")
        self._pv_status_int = PV(self.Id + ":IDX_RB")
        self._sleeptime = sleeptime

    def __str__(self):
        pass

    def __status__(self):
        pass

    def updateE(self, energy=None):
        while not energy:
            energy = PV("SARUN03-UIND030:FELPHOTENE").value
            energy = energy * 1000
            if energy < self.E_min:
                energy = None
                print(f'Machine photon energy is below {self.E_min} - waiting for the machine to recover')
                sleep(self._sleeptime)
        PV(self.Id + ":ENERGY").put(energy)
        print("Set energy to %s eV" % energy)
        return

    def set_transmission(self, value, energy=None):
        self.updateE(energy)
        PV(self.Id + ":3RD_HARM_SP").put(0)
        PV(self.Id + ":TRANS_SP").put(value)
        pass

    def set_transmission_third_harmonic(self, value, energy=None):
        self.updateE(energy)
        PV(self.Id + ":3RD_HARM_SP").put(1)
        PV(self.Id + ":TRANS_SP").put(value)
        pass

    def setE(self):
        pass

    def get_transmission(self):
        tFun = PV(self.Id + ":TRANS_RB").value
        tTHG = PV(self.Id + ":TRANS3EDHARM_RB").value
        print("Transmission Fundamental: %s THG: %s" % (tFun, tTHG))
        return tFun, tTHG

    def get_status(self):
        s_str = self._pv_status_str.get(as_string=True)
        s_int = self._pv_status_int.get()
        return s_str, s_int

    def __repr__(self):
        t = self.get_transmission()
        s = "1st harm. transmission = %g\n" % t[0]
        s += "3rd harm. transmission = %g\n" % t[1]
        s += "Targets in beam:\n"
        s += "%s" % self.get_status()[0]
        return s

    def __call__(self, *args, **kwargs):
        self.set_transmission(*args, **kwargs)
