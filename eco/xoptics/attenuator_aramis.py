from ..devices_general.motors import MotorRecord
from epics import PV
from time import sleep
from ..devices_general.utilities import Changer
from ..aliases import Alias
from ..elements.adjustable import AdjustableFS
from ..epics.adjustable import AdjustablePvEnum
from ..elements.assembly import Assembly


class AttenuatorAramis:
    def __init__(
        self, Id, E_min=1500, sleeptime=10, name=None, set_limits=[-52, 2], shutter=None
    ):
        self.Id = Id
        self.E_min = E_min
        self._pv_status_str = PV(self.Id + ":MOT2TRANS.VALD")
        self._pv_status_int = PV(self.Id + ":IDX_RB")
        self._sleeptime = sleeptime
        self.name = name
        self.alias = Alias(name)
        self.shutter = shutter
        self.motors = [
            MotorRecord(f"{self.Id}:MOTOR_{n+1}", name=f"motor{n+1}") for n in range(6)
        ]
        for n, mot in enumerate(self.motors):
            self.__dict__[f"motor_{n+1}"] = mot
            self.alias.append(mot.alias)
            if set_limits:
                mot.set_limits(*set_limits)

        alias_fields = {
            "transmission": "TRANS_RB",
            "transmission_3rd": "TRANS3EDHARM_RB",
        }
        for an, af in alias_fields.items():
            ach = ":".join([self.Id, af])
            self.alias.append(Alias(an, channel=ach, channeltype="CA"))

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
                print(
                    f"Machine photon energy is below {self.E_min} - waiting for the machine to recover"
                )
                sleep(self._sleeptime)
        PV(self.Id + ":ENERGY").put(energy)
        print("Set energy to %s eV" % energy)
        return

    def set_transmission(self, value, energy=None):
        self.updateE(energy)
        PV(self.Id + ":3RD_HARM_SP").put(0)
        PV(self.Id + ":TRANS_SP").put(value)

    def set_transmission_third_harmonic(self, value, energy=None):
        self.updateE(energy)
        PV(self.Id + ":3RD_HARM_SP").put(1)
        PV(self.Id + ":TRANS_SP").put(value)

    def setE(self):
        pass

    def get_transmission(self, verbose=True):
        tFun = PV(self.Id + ":TRANS_RB").value
        tTHG = PV(self.Id + ":TRANS3EDHARM_RB").value
        if verbose:
            print("Transmission Fundamental: %s THG: %s" % (tFun, tTHG))
        return tFun, tTHG

    def get_current_value(self, *args, **kwargs):
        return self.get_transmission(*args, verbose=False, **kwargs)[0]

    def set_target_value(self, value, sleeptime=10, hold=False):
        def changer(value):
            self.set_transmission(value)
            sleep(sleeptime)
            if self.shutter:
                self.shutter.open()

        return Changer(target=value, parent=self, changer=changer, hold=hold)

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


class AttenuatorAramisStandalone(Assembly):
    def __init__(self, pvname, path_cfg="~/eco/att135_cfg", shutter=None, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self.E_min = E_min
        self.shutter = shutter
        self.cfg = AdjustableFS(path_cfg, name="cfg")
        for n in range(6):
            self._append(
                MotorRecord,
                f"{self.pvname}:MOTOR_{n+1}",
                name=f"motor{n+1}",
                is_setting=True,
                is_status=False,
            )
