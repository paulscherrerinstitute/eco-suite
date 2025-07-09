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
import time
from ..elements.adjustable import (
    AdjustableError,
    spec_convenience,
    update_changes,
    value_property,
    AdjustableFS,
    AdjustableGetSet,
)
from ..elements.detector import DetectorGet
from eco.devices_general.utilities import Changer
import pylab as plt


@spec_convenience
@value_property
class Att_usd(Assembly):
    """This is an adjusted smaract record compatible version of the original att_usd by roman."""

    def __init__(self, name=None, Id=None, alias_namespace=None, xp=None):
        super().__init__(name=name)
        self.Id = Id
        self.E = None
        self.E_min = 1500
        self._sleeptime = 1
        self._cb = None
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/{name}_limit_high.json",
            default_value=1,
            name="limit_high",
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            f"/sf/bernina/config/eco/reference_values/{name}_limit_low.json",
            default_value=0,
            name="limit_low",
            is_setting=True,
        )
        self._append(
            SmaractRecord,
            "SARES20-MCS1:MOT_10",
            name="transl_2",
            is_setting=True,
            is_display=True,
        )
        self._append(
            SmaractRecord,
            "SARES20-MCS1:MOT_3",
            name="transl_1",
            is_setting=True,
            is_display=True,
        )
        self._append(
            DetectorGet, self.get_current_value, name="readback", is_display=True
        )
        self.motor_configuration = {
            "transl_2": {
                "id": "SARES20-MCS110",
                "pv_descr": "att_usd transl 2",
                "type": 1,
                "sensor": 0,
                "speed": 500,
                "home_direction": "back",
                "hl": 50,
                "ll": -50,
            },
            "transl_1": {
                "id": "SARES20-MCS112",
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
        Si3N4 = materials.Amorphous(name="Si3N4", density=3440)
        polyimide = materials.Amorphous(name="C35H28N2O7", density=1440)
        self.targets_2 = {
            "mat": np.array(
                [
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    polyimide,
                    Al2O3,
                ]
            ),
            "d": np.array(
                [
                    2800,
                    2000,
                    1600,
                    1200,
                    800,
                    550,
                    420,
                    320,
                    240,
                    175,
                    125,
                    75,
                    30,
                    125,
                    0,
                ]
            ),
            "pos": np.array(
                [
                    38.3,
                    33.4,
                    27.7,
                    23.3,
                    18.8,
                    13.0,
                    8.0,
                    2.5,
                    -2.8,
                    -7.7,
                    -12.8,
                    -18.0,
                    -22.0,
                    -26.7,
                    -35.0,
                ]
            ),
        }
        self.targets_1 = {
            "mat": np.array(
                [
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    Al2O3,
                    polyimide,
                    polyimide,
                    polyimide,
                    Al2O3,
                ]
            ),
            "d": np.array(
                [2800, 1600, 800, 420, 240, 175, 125, 75, 30, 125, 50, 25, 0]
            ),
            "pos": np.array(
                [-37.7, -32.6, -27.3, -23, -18, -13, -7.8, -3, 1.7, 7.4, 12.6, 17.6, 25]
            ),
        }

    def _updateE(self, energy=None, check_times=2):
        n = 0
        while not energy:
            energy = PV("SAROP21-ARAMIS:ENERGY").value
            if np.isnan(energy):
                energy = PV("SARUN:FELPHOTENE").value * 1000
            if energy < self.E_min:
                n = n + 1
                if n > check_times:
                    raise ValueError(f"Machine photon energy is below {self.E_min} since {self._sleeptime*n}s")
                energy = None
                sleep(self._sleeptime)
                print(
                    f"Machine photon energy is below {self.E_min} - waiting for the machine to recover since {self._sleeptime*n}s"
                )
        self.E = energy
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
        t_comb = (
            (np.expand_dims(t1, axis=0)).T * (np.expand_dims(t2, axis=0))
        ).flatten()
        pos_comb = np.array(
            [[p1, p2] for p1 in self.targets_1["pos"] for p2 in self.targets_2["pos"]]
        )
        self.transmissions = {"t": t_comb, "pos": pos_comb}

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
        while (abs(p1 - self.transl_1.get_current_value()) > 0.05) or (
            (abs(p2 - self.transl_2.get_current_value()) > 0.05)
        ):
            sleep(0.1)
        print("transmission changed")
        self._xp.open()

    def get_current_value(self):
        self._updateE()
        self._calc_transmission()
        idx1, p1 = self._find_nearest(
            self.targets_1["pos"], self.transl_1.get_current_value()
        )
        t1 = self.targets_1["t"][idx1]
        idx2, p2 = self._find_nearest(
            self.targets_2["pos"], self.transl_2.get_current_value()
        )
        t2 = self.targets_2["t"][idx2]
        return t1 * t2

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

    def home_smaract_stages(self, motor_configuration=None):
        if motor_configuration == None:
            motor_configuration = self.motor_configuration
        stages = motor_configuration.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = motor_configuration[name]
            mot = self.__dict__[name]
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            sleep(1)
            if config["home_direction"] == "back":
                mot.home_reverse(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in forward direction".format(name)
                    )
                    mot.home_forward(1)
            elif config["home_direction"] == "forward":
                mot.home_forward(1)
                sleep(0.5)
                while not mot.flags.motion_complete():
                    sleep(1)
                if not mot.flags.is_homed():
                    print(
                        "Homing failed, try homing {} in backward direction".format(
                            name
                        )
                    )
                    mot.home_reverse(1)

    ######### Motion commands ########

    def get_limits(self):
        return (self.limit_low(), self.limit_high())

    def set_limits(self, limit_low, limit_high):
        self.limit_low(limit_low)
        self.limit_high(limit_high)

    def stop(self):
        """Adjustable convention"""
        self.transl_1.stop()
        self.transl_2.stop()
        print("STOPPING AT: \n" + get_adjustable_positions_str())
        pass

    def get_moveDone(self, p1, p2):
        if self._cb:
            self._cb()
        if (abs(p1 - self.transl_1.get_current_value()) < 0.05) & (
            abs(p2 - self.transl_2.get_current_value()) < 0.05
        ):
            return True
        else:
            return False

    def move(self, value, check=True, wait=True, update_value_time=0.1, timeout=120):
        if check:
            lim_low, lim_high = self.get_limits()
            if not ((lim_low <= value) and (value <= lim_high)):
                raise AdjustableError("Soft limits violated!")
        self._updateE()
        self._calc_transmission()
        idx, t = self._find_nearest(self.transmissions["t"], value)
        p1, p2 = self.transmissions["pos"][idx]
        self._xp.close()
        print(f"Set transmission to {t:0.2E} | Moving to pos {[p1, p2]}")
        self.transl_1.set_target_value(p1)
        self.transl_2.set_target_value(p2)
        if wait:
            t_start = time.time()
            time.sleep(update_value_time)
            while not self.get_moveDone(p1, p2):
                if (time.time() - t_start) > timeout:
                    raise AdjustableError(f"motion timeout reached in att_usd motion")
                time.sleep(update_value_time)
            self._xp.open()

    def set_target_value(self, value, hold=False, check=True):
        changer = lambda value: self.move(value, check=check, wait=True)
        return Changer(
            target=value,
            parent=self,
            changer=changer,
            hold=hold,
            stopper=self.stop,
        )

    ### helper functions ###
    def sim_target_values(self, values, energy=None, plot=True):
        try:
            l = len(values)
        except TypeError:
            values = [values]
        self._updateE(energy=energy)
        self._calc_transmission()
        act_values = np.array(
            [self._find_nearest(self.transmissions["t"], value) for value in values]
        )
        if plot:
            plt.close("att_usd target_positions")
            plt.figure("att_usd target_positions")
            plt.plot(values, act_values.T[1], "o-")
            plt.grid()
            plt.xlabel("set transmission")
            plt.ylabel("reachable transmission")
            plt.tight_layout()
        return act_values.T[1]

