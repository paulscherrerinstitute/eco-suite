from ..devices_general.motors import MotorRecord, MotorRecord_new
from eco.elements.adjustable import AdjustableFS, AdjustableVirtual
from ..epics.adjustable import AdjustablePv, AdjustablePvEnum
from ..epics.detector import DetectorPvData
from epics import PV
from ..devices_general.utilities import Changer
from time import sleep
import numpy as np
from ..aliases import Alias, append_object_to_object
from ..elements.adjustable import (
    spec_convenience,
    update_changes,
    default_representation,
    tweak_option,
)
from ..devices_general.utilities import Changer
from ..elements.assembly import Assembly


@spec_convenience
@update_changes
@tweak_option
class DoubleCrystalMono(Assembly):
    def __init__(
        self,
        pvname=None,
        name=None,
        energy_sp="SAROP21-ARAMIS:ENERGY_SP",
        energy_rb="SAROP21-ARAMIS:ENERGY",
        fel=None,
        undulator_deadband_eV=None,
    ):
        super().__init__(name=name)
        self.fel = fel
        self.undulator_deadband_eV = undulator_deadband_eV
        self.pvname = pvname
        self._append(
            MotorRecord_new,
            pvname + ":RX12",
            name="theta",
            is_setting=True,
            view_toplevel_only=True,
        )
        self._append(
            MotorRecord_new,
            pvname + ":TX12",
            name="x",
            is_setting=True,
            view_toplevel_only=True,
        )
        self._append(
            MotorRecord_new,
            pvname + ":T2",
            name="gap",
            is_setting=True,
            view_toplevel_only=True,
        )
        self._append(
            MotorRecord_new,
            pvname + ":RZ1",
            name="roll1",
            is_setting=True,
            view_toplevel_only=True,
        )
        self._append(
            MotorRecord_new,
            pvname + ":RZ2",
            name="roll2",
            is_setting=True,
            view_toplevel_only=True,
        )
        self._append(
            MotorRecord_new,
            pvname + ":RX2",
            name="pitch2",
            is_setting=True,
            view_toplevel_only=True,
        )
        self._append(
            AdjustablePv,
            energy_sp,
            pvreadbackname=energy_rb,
            accuracy=0.5,
            name="energy",
        )
        self._append(
            DetectorPvData,
            energy_rb,
            name="readback",
            is_setting=False,
            is_display=False,
        )
        self.settings_collection.append(self)
        if self.fel is not None:
            self._append(
                AdjustableFS,
                "/photonics/home/gac-bernina/eco/configuration/mono_und_offset",
                name="mono_und_calib",
                default_value=[[6500, 0], [7100, 0]],
                is_setting=True,
            )

            def en_set(en):
                ofs = np.array(self.mono_und_calib()).T
                fel_ofs = ofs[1][np.argmin(abs(ofs[0] - en))]
                e_und_curr = (
                    self.fel.aramis_photon_energy_undulators.get_current_value()
                )

                if (
                    np.abs(en - (e_und_curr + fel_ofs) * 1000)
                    < self.undulator_deadband_eV
                ):
                    return en, None
                else:
                    return en, en / 1000 - fel_ofs

            def en_get(monoen, felen):
                return monoen

            self._append(
                AdjustableVirtual,
                [self.energy, self.fel.aramis_photon_energy_undulators],
                en_get,
                en_set,
                name="mono_und_energy",
            )

    def add_mono_und_calibration_point(self):
        mono_energy = self.energy.get_current_value()
        fel_offset = (
            self.energy.get_current_value() / 1000
            - self.fel.aramis_photon_energy_undulators.get_current_value()
        )
        self.mono_und_calib.mvr([[mono_energy, fel_offset]])

    def reset_mono_und_calibration(self):
        self.mono_und_calib.mv([])

    def set_target_value(self, *args, **kwargs):
        return self.energy.set_target_value(*args, **kwargs)

    def get_current_value(self, *args, **kwargs):
        return self.energy.get_current_value(*args, **kwargs)

    def __str__(self):
        return f"{self.name} @ {self.get_current_value()} eV"

    def add_value_callback(self, callback, index=None):
        return self.energy._pvreadback.add_callback(callback=callback, index=index)

    def clear_value_callback(self, index=None):
        if index:
            self.energy._pvreadback.remove_callback(index)
        else:
            self.energy._pvreadback.clear_callbacks()


@spec_convenience
@default_representation
class EcolEnergy(Assembly):
    def __init__(
        self,
        pv_val="SARCL02-MBND100:USER-ENE",
        pv_enable="SARCL02-MBND100:USER-ENA",
        pv_rb="SARCL02-MBND100:P-READ",
        pv_diff="SARCL02-MBND100:USER-ERROR",
        name=None,
    ):
        super().__init__(name=name)
        self._append(AdjustablePvEnum, pv_enable, name="enable_control")
        self._pv_val = PV(pv_val)
        self._pv_rb = PV(pv_rb)
        self._pv_diff = PV(pv_diff)

    def change_energy_to(self, value, tolerance=0.5):
        self.enable_control(0)
        sleep(0.1)
        self._pv_val.put(value)
        sleep(0.1)
        self.enable_control(1)
        done = False
        sleep(0.1)
        while not done:
            sleep(0.05)
            diffabs = np.abs(self._pv_rb.get() - value)
            # diff = self._pv_diff.get()
            if diffabs < tolerance:
                diff = self._pv_diff.get()
                if diff == 0:
                    done = True
        self.enable_control(0)

    def get_current_value(self):
        return self._pv_rb.get()

    def set_target_value(self, value, hold=False):
        """Adjustable convention"""

        changer = lambda value: self.change_energy_to(value)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )
