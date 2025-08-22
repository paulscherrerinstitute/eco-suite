from scipy import constants
import numpy as np
from xrayutilities import materials
from ..elements.adjustable import AdjustableFS, AdjustableVirtual
from ..elements.assembly import Assembly
from eco import Adjustable
from eco.devices_general.motors import MotorRecord


def energy2tthe(energy, hkl=(1, 1, 1), material=materials.Si):
    """calculates 2 theta angle of certain energy, given material and bragg reflection"""
    if energy == 0:
        return np.nan
    return 2 * np.arcsin(
        constants.h
        * constants.c
        / constants.eV
        * 1e10
        / energy
        * np.linalg.norm(materials.Si.Q(*hkl))
        / 4
        / np.pi
    )


def calcDcmExtension(energy, offset=20e-3, hkl=(1, 1, 1), material=materials.Si):
    tthe = energy2tthe(energy, hkl, material=material)
    return offset * (1 / np.sin(tthe) - 1 / np.tan(tthe))
    # return offset / np.sin(tthe) * (1 - np.cos(tthe))


class MonoTimecompensation(Assembly):
    def __init__(
        self,
        laser_delay_seconds,
        mono_energy_eV,
        path_ref_energy,
        path_laser_delay_inverted,
        name=None,
    ):
        super().__init__(name=name)
        self._append(AdjustableFS, path_ref_energy, name="ref_energy")
        self._append(
            AdjustableFS,
            path_laser_delay_inverted,
            default_value=True,
            name="laser_delay_inverted",
        )
        if isinstance(laser_delay_seconds, Adjustable):
            self._laser_delay = laser_delay_seconds
        else:
            raise Exception("issue getting laser delay for mono compensation")

        self._mono_energy = mono_energy_eV
        self._append(
            AdjustableVirtual,
            [self._mono_energy, self._laser_delay],
            lambda energy, delay: self.calc_realdelay(energy, delay),
            lambda delay: (
                self._mono_energy.get_current_value(),
                self.calc_delay_correction(
                    self._mono_energy.get_current_value(), delay
                ),
            ),
            name="delay_monodelay_corr",
            unit="s",
        )
        self._append(
            AdjustableVirtual,
            [self._mono_energy, self._laser_delay],
            lambda energy, delay: energy,
            lambda energy: (
                energy,
                self.calc_delay_correction(
                    energy, self.delay_monodelay_corr.get_current_value()
                ),
            ),
            name="mono_delay_corr",
            unit="eV",
        )

    def calc_delay_correction(self, target_energy, target_delay):
        x_ref = calcDcmExtension(self.ref_energy.get_current_value())
        x_target = calcDcmExtension(target_energy)
        x_delay = x_target - x_ref
        delta_delay = x_delay / constants.c
        target_delay_adj = (
            target_delay
            + (self.laser_delay_inverted.get_current_value() * -2 + 1)
            * delta_delay  # NB: boolean to ± 1 conversion
        )
        # print("debug here")
        # print(x_delay, delta_delay)
        return target_delay_adj

    def calc_realdelay(self, current_energy, delay_adjusted):
        x_ref = calcDcmExtension(self.ref_energy.get_current_value())
        x_target = calcDcmExtension(current_energy)
        x_delay = x_target - x_ref
        delta_delay = x_delay / constants.c
        real_delay = (
            delay_adjusted
            - (self.laser_delay_inverted.get_current_value() * -2 + 1)
            * delta_delay  # NB: boolean to ± 1 conversion
        )
        # print("debug here")
        # print(x_delay, delta_delay)
        return real_delay
