import sys
from ..elements.assembly import Assembly

sys.path.append("..")
from ..devices_general.motors import SmaractRecord, SmaractStreamdevice, MotorRecord
from epics import PV
from ..aliases import Alias, append_object_to_object
from time import sleep
from xrayutilities import materials
import numpy as np

from time import sleep


class att_usd_targets(Assembly):
    def __init__(self, name=None, Id=None, alias_namespace=None, xp=None):
        super().__init__(name=name)
        self.Id = Id
        # self.name = name
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

        for name, config in self.motor_configuration.items():
            self._append(
                SmaractStreamdevice,
                pvname=Id + config["id"],
                name=name,
                is_setting=True,
                is_status=False,
            )

        Al = materials.Al
        self.targets = {
            "mat": np.array([Al, Al, Al, Al, Al, Al, Al, Al]),
            "d": np.array([0, 60, 160, 200, 300, 400, 500, 700]),
            "pos": np.array([-35, -25, -15, -5, 5, 15, 25, 35]),
        }

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

    def _calc_transmission(self):
        t = np.array(
            [
                np.exp(-d / mat.absorption_length(self.E))
                for d, mat in zip(self.targets["d"], self.targets["mat"])
            ]
        )
        self.targets["t"] = t

    def _find_nearest(self, a, a0):
        "Element in nd array `a` closest to the scalar value `a0`"
        idx = np.abs(a - a0).argmin()
        return idx, a[idx]

    def set_transmission(self, value):
        self._updateE()
        self._calc_transmission()
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
        self._updateE()
        self._calc_transmission()
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




class Att_usd(Assembly):
    """This is an adjusted smaract record compatible version of the original att_usd by roman."""
    def __init__(self, name=None, Id=None, alias_namespace=None, xp=None):
        super().__init__(name=name)
        self.Id = Id
        self.E = None
        self.E_min = 1500
        self._sleeptime = 1
        self._append(SmaractRecord,"SARES23:LIC10",name='transl_2',  is_setting=True)
        self._append(SmaractRecord,"SARES23:LIC3",name='transl_1',  is_setting=True)
        self.motor_configuration = {
            "transl_2": {
                "id": "SARES23-LIC10",
                "pv_descr": "att_usd transl 2",
                "type": 1,
                "sensor": 0,
                "speed": 500,
                "home_direction": "back",
                "hl": 50,
                "ll": -50,
            },
            "transl_1": {
                "id": "SARES23-LIC12",
                "pv_descr": "att_usd transl 1",
                "type": 1,
                "sensor": 0,
                "speed": 500,
                "home_direction": "back",
                "hl": 50,
                "ll": -50,
            },
        }
        self._xp = xp
        self.E = None


        Al2O3 = materials.Al2O3
        Si3N4 = materials.Amorphous(name='Si3N4', density=3440)
        polyimide = materials.Amorphous(name='C35H28N2O7', density=1440)
        self.targets_2 = {
            "mat": np.array([Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,polyimide, Al2O3]),
            "d": np.array([2800, 2000, 1600, 1200, 800, 550, 420, 320, 240, 175, 125, 75, 30, 125, 0]),
            "pos": np.array([38.3,  33.4,  27.7,  23.3,  18.8,  13. , 8. , 2.5,-2.8, -7.7, -12.8, -18. , -22. , -26.7, -35.]),
        }
        self.targets_1 = {
            "mat": np.array([Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,polyimide,polyimide,polyimide, Al2O3]),
            "d": np.array([2800, 1600, 800, 420, 240, 175, 125, 75, 30, 125, 50, 25, 0]),
            "pos": np.array([-37.7, -32.6, -27.3, -23, -18, -13, -7.8,  -3,  1.7, 7.4, 12.6,  17.6,  25]),
        }

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

    def _calc_transmission(self):
        t1 = np.array(
            [
                np.exp(-d / mat.absorption_length(self.E))
                for d, mat in zip(self.targets_1["d"], self.targets_1["mat"])
            ]
        )
        self.targets_1["t"] = t1
        t2 = np.array(
            [
                np.exp(-d / mat.absorption_length(self.E))
                for d, mat in zip(self.targets_2["d"], self.targets_2["mat"])
            ]
        )
        self.targets_2["t"] = t2
        t_comb = ((np.expand_dims(t1, axis=0)).T*(np.expand_dims(t2, axis=0))).flatten()
        pos_comb = np.array([[p1, p2] for p1 in self.targets_1['pos'] for p2 in self.targets_2['pos']])
        self.transmissions = {'t':t_comb, 'pos': pos_comb}


    def _find_nearest(self, a, a0):
        "Element in nd array `a` closest to the scalar value `a0`"
        idx = np.abs(a - a0).argmin()
        return idx, a[idx]

    def set_transmission(self, value):
        self._updateE()
        self._calc_transmission()
        idx, t = self._find_nearest(self.transmissions["t"], value)
        p1, p2 = self.transmissions["pos"][idx]
        self._xp.close()
        self.transl_1.set_target_value(p1)
        self.transl_2.set_target_value(p2)
        print(f"Set transmission to {t:0.2E} | Moving to pos {[p1, p2]}")
        while ((abs(p1 - self.transl_1.get_current_value()) > 0.05) or (abs(p2 - self.transl_2.get_current_value() > 0.05))):
            sleep(0.1)
        print("transmission changed")
        self._xp.open()

    def get_current_value(self):
        self._updateE()
        self._calc_transmission()
        idx1, p1 = self._find_nearest(
            self.targets_1["pos"], self.transl_1.get_current_value()
        )
        t1 =  self.targets_1["t"][idx1]
        idx2, p2 = self._find_nearest(
            self.targets_2["pos"], self.transl_2.get_current_value()
        )
        t2 =  self.targets_2["t"][idx2]
        return t1*t2

    def set_stage_config(self):
        for name, config in self.motor_configuration.items():
            mot = self.__dict__[name]
            mot.caqtdm_name(config["pv_descr"])
            mot.stage_type(config["type"])
            mot.sensor_type(config["sensor"])
            mot.speed(config["speed"])
            mot.limit_high(config["hl"])
            mot.limit_low(config["ll"])
            sleep(0.5)
            mot.calibrate_sensor(1)

    def home_smaract_stages(self, stages=None):
        if stages == None:
            stages = self.motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = self.motor_configuration[name]
            mot = self.__dict__[name]
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.home_backward(1)
                while mot.status_channel().value == 7:
                    sleep(1)
                if mot.is_homed() == 0:
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.home_forward(1)
            elif config["home_direction"] == "forward":
                mot.home_forward(1)
                while mot.status_channel().value == 7:
                    sleep(1)
                if mot.is_homed() == 0:
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.home_backward(1)

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



class att_usd(Assembly):
    def __init__(self, name=None, alias_namespace=None, xp=None):
        super().__init__(name=name)
        # self.name = name
        self.alias = Alias(name)
        self.E = None
        self.E_min = 1500
        self._sleeptime = 1
        self.motor_configuration = {
            "transl_2": {
                "id": "SARES23-LIC10",
                "pv_descr": "att_usd transl 2",
                "type": 1,
                "sensor": 0,
                "speed": 500,
                "home_direction": "back",
                "hl": 50,
                "ll": -50,
            },
            "transl_1": {
                "id": "SARES23-LIC12",
                "pv_descr": "att_usd transl 1",
                "type": 1,
                "sensor": 0,
                "speed": 500,
                "home_direction": "back",
                "hl": 50,
                "ll": -50,
            },
        }
        self._xp = xp
        self.E = None

        for name, config in self.motor_configuration.items():
            self._append(
                SmaractStreamdevice,
                pvname=config["id"],
                name=name,
                is_setting=True,
                is_status=False,
            )

        Al2O3 = materials.Al2O3
        Si3N4 = materials.Amorphous(name='Si3N4', density=3440)
        polyimide = materials.Amorphous(name='C35H28N2O7', density=1440)
        self.targets_2 = {
            "mat": np.array([Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,polyimide, Al2O3]),
            "d": np.array([2800, 2000, 1600, 1200, 800, 550, 420, 320, 240, 175, 125, 75, 30, 125, 0]),
            "pos": np.array([38.3,  33.4,  27.7,  23.3,  18.8,  13. , 8. , 2.5,-2.8, -7.7, -12.8, -18. , -22. , -26.7, -35.]),
        }
        self.targets_1 = {
            "mat": np.array([Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,Al2O3,polyimide,polyimide,polyimide, Al2O3]),
            "d": np.array([2800, 1600, 800, 420, 240, 175, 125, 75, 30, 125, 50, 25, 0]),
            "pos": np.array([-37.7, -32.6, -27.3, -23, -18, -13, -7.8,  -3,  1.7, 7.4, 12.6,  17.6,  25]),
        }

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

    def _calc_transmission(self):
        t1 = np.array(
            [
                np.exp(-d / mat.absorption_length(self.E))
                for d, mat in zip(self.targets_1["d"], self.targets_1["mat"])
            ]
        )
        self.targets_1["t"] = t1
        t2 = np.array(
            [
                np.exp(-d / mat.absorption_length(self.E))
                for d, mat in zip(self.targets_2["d"], self.targets_2["mat"])
            ]
        )
        self.targets_2["t"] = t2
        t_comb = ((np.expand_dims(t1, axis=0)).T*(np.expand_dims(t2, axis=0))).flatten()
        pos_comb = np.array([[p1, p2] for p1 in self.targets_1['pos'] for p2 in self.targets_2['pos']])
        self.transmissions = {'t':t_comb, 'pos': pos_comb}


    def _find_nearest(self, a, a0):
        "Element in nd array `a` closest to the scalar value `a0`"
        idx = np.abs(a - a0).argmin()
        return idx, a[idx]

    def set_transmission(self, value):
        self._updateE()
        self._calc_transmission()
        idx, t = self._find_nearest(self.transmissions["t"], value)
        p1, p2 = self.transmissions["pos"][idx]
        self._xp.close()
        self.transl_1.set_target_value(p1)
        self.transl_2.set_target_value(p2)
        print(f"Set transmission to {t:0.2E} | Moving to pos {[p1, p2]}")
        while ((abs(p1 - self.transl_1.get_current_value()) > 0.05) or (abs(p2 - self.transl_2.get_current_value() > 0.05))):
            sleep(0.1)
        print("transmission changed")
        self._xp.open()

    def get_current_value(self):
        self._updateE()
        self._calc_transmission()
        idx1, p1 = self._find_nearest(
            self.targets_1["pos"], self.transl_1.get_current_value()
        )
        t1 =  self.targets_1["t"][idx1]
        idx2, p2 = self._find_nearest(
            self.targets_2["pos"], self.transl_2.get_current_value()
        )
        t2 =  self.targets_2["t"][idx2]
        return t1*t2

    def set_stage_config(self):
        for name, config in self.motor_configuration.items():
            mot = self.__dict__[name]
            mot.caqtdm_name(config["pv_descr"])
            mot.stage_type(config["type"])
            mot.sensor_type(config["sensor"])
            mot.speed(config["speed"])
            mot.limit_high(config["hl"])
            mot.limit_low(config["ll"])
            sleep(0.5)
            mot.calibrate_sensor(1)

    def home_smaract_stages(self, stages=None):
        if stages == None:
            stages = self.motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = self.motor_configuration[name]
            mot = self.__dict__[name]
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.home_backward(1)
                while mot.status_channel().value == 7:
                    sleep(1)
                if mot.is_homed() == 0:
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.home_forward(1)
            elif config["home_direction"] == "forward":
                mot.home_forward(1)
                while mot.status_channel().value == 7:
                    sleep(1)
                if mot.is_homed() == 0:
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.home_backward(1)

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
