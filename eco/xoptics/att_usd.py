import sys
from ..elements.assembly import Assembly
sys.path.append("..")
from ..devices_general.motors import SmaractStreamdevice, MotorRecord
from epics import PV
from ..aliases import Alias, append_object_to_object
from time import sleep
from xrayutilities import materials
import numpy as np

from time import sleep

class att_usd_targets(Assembly):
    def __init__(
        self, 
        name=None, 
        Id=None, 
        alias_namespace=None, 
        xp=None
    ):
        super().__init__(name=name)
        self.Id = Id
        #self.name = name
        self.alias = Alias(name)
        self.E = None
        self.E_min = 1500
        self._sleeptime = 1
        self.motor_configuration = {
            "transl": {
                "id": "-LIC10",
                "pv_descr": " ",
                "type": 2,
                "sensor": 1,
                "speed": 250,
                "home_direction": "back",
            },
        }
        self._xp = xp
        self.E = None
        self._updateE()

        ### BSEN target position ###
        for name, config in self.motor_configuration.items():
            self._append(SmaractStreamdevice, pvname=Id + config["id"], name=name, is_setting=True, is_status=False)

        Al = materials.Al
        self.targets = {
            "mat": np.array([Al, Al, Al, Al, Al, Al, Al, Al]),
            "d": np.array([0, 60, 160, 200, 300, 400, 500, 700]),
            "pos": np.array([-35, -25, -15, -5, 5, 15, 25, 35]),
        }
        self._get_transmission()

    def _updateE(self, energy=None, check_once=False):
        while not energy:
            energy = PV("SARUN03-UIND030:FELPHOTENE").value
            energy = energy * 1000
            if energy < self.E_min:
                energy = None
                print(
                    f"Machine photon energy is below {self.E_min} - waiting for the machine to recover"
                )
                sleep(self._sleeptime)
        self.E = energy
        print("Set energy to %s eV" % energy)
        return

    def _get_transmission(self):
        t = np.array([np.exp(-d / mat.absorption_length(self.E)) for d, mat in zip(self.targets["d"], self.targets["mat"])])     
        self.targets["t"] = t

    def _find_nearest(self, a, a0):
        "Element in nd array `a` closest to the scalar value `a0`"
        idx = np.abs(a - a0).argmin()
        return idx, a[idx]

    def set_transmission(self, value):
        self._updateE()
        self._get_transmission()
        idx, t = self._find_nearest(self.targets["t"], value)
        pos = self.targets["pos"][idx]
        self._xp.close()
        self.transl.mv(pos)
        print(f"Set transmission to {t:0.2E} | Moving to target {idx} at pos {pos}")
        while abs(pos - self.transl.get_current_value()) > 0.1:
            sleep(0.1)
        print("transmission changed")
        self._xp.open()

    def get_current_value(self):
        idx, pos = self._find_nearest(
            self.targets["pos"], self.transl.get_current_value()
        )
        t = self.targets["t"][idx]
        return t


    def set_stage_config(self):
        for name, config in self.motor_configuration.items():
            mot = self.__dict__[name]._device
            mot.put("NAME", config["pv_descr"])
            mot.put("STAGE_TYPE", config["type"])
            mot.put("SET_SENSOR_TYPE", config["sensor"])
            mot.put("CL_MAX_FREQ", config["speed"])
            sleep(0.5)
            mot.put("CALIBRATE.PROC", 1)

    def home_smaract_stages(self, stages=None):
        if stages == None:
            stages = self.motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = self.motor_configuration[name]
            mot = self.__dict__[name]._device
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.put("FRM_BACK.PROC", 1)
                while mot.get("STATUS") == 7:
                    sleep(1)
                if mot.get("GET_HOMED") == 0:
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.put("FRM_FORW.PROC", 1)
            elif config["home_direction"] == "forward":
                mot.put("FRM_FORW.PROC", 1)
                while mot.get("STATUS") == 7:
                    sleep(1)
                if mot.get("GET_HOMED") == 0:
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.put("FRM_BACK.PROC", 1)

    def get_adjustable_positions_str(self):
        ostr = "*****att_usd target position******\n"

        for tkey, item in self.__dict__.items():
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
        pos = self.get_current_value()
        ostr += "  " + "Transmission".ljust(17) + " : % 14.02E\n" % pos
        return ostr

    def __call__(self, *args, **kwargs):
        self.set_transmission(*args, **kwargs)

    def __repr__(self):
        return self.get_adjustable_positions_str()
