import sys

sys.path.append("..")
from ..devices_general.motors import MotorRecord, MotorRecord_new
from ..devices_general.adjustable import PvRecord, AdjustableVirtual, AdjustableMemory

from epics import PV
from ..aliases import Alias, append_object_to_object
from ..endstations.hexapod import HexapodPI
from pathlib import Path
import subprocess
from ..elements.assembly import Assembly
from ..detector.jungfrau import Jungfrau
from .kappa_conversion import kappa2you, you2kappa
import numpy as np
from scipy.linalg import norm
import xrayutilities as xu


class Analyzer(Assembly):
    def __init__(
        self,
        hxrd,
        material,
        hkl,
        name=None,
        pos=None,
        config=None,
        pvname=None,
        det=None,
    ):
        super().__init__(name=name)
        self.name = name
        self.hxrd = hxrd
        self.material = material
        self.hkl = hkl
        self.pvname = pvname
        self.config = config
        self._det = det
        if pos:
            self._motor_cfg = {
                f"MOT_{int(pos)}_1": "om",
                f"MOT_{int(pos)}_2": "chi",
                f"MOT_{int(pos)}_3": "t_ver",
                f"MOT_{int(pos)}_4": "t_hor",
            }
            for pvmot, name in self._motor_cfg.items():
                # try:
                if name == "om":
                    self._append(
                        MotorRecord,
                        pvname + f":{pvmot}",
                        name=name,
                        is_setting=True,
                        is_status=True,
                        backlash_definition=True,
                        schneider_config=(pvname + f":{pvmot}", pvname + f":{pvmot}"),
                    )
                else:
                    self._append(
                        MotorRecord,
                        pvname + f":{pvmot}",
                        name=name,
                        is_setting=True,
                        is_status=True,
                        backlash_definition=False,
                        schneider_config=(pvname + f":{pvmot}", pvname + f":{pvmot}"),
                    )
            # except:
            # self._append(
            # AdjustableMemory,
            # name=name,
            # is_setting=True,
            # is_status=True,
            # )
            # print(f"Initialization of epics motor {name}: {pvname}:{pvmot} failed, replaced by dummy!")

        self._append(
            AdjustableVirtual,
            [self.om, self.t_hor, self._det.t_hor, self._det.t_ver, self._det.rot],
            self.energy_from_motor_pos,
            self.motor_pos_from_energy,
            is_setting=False,
            is_status=True,
            name="energy",
            unit="eV",
        )

    def angs_from_hkl(self, h, k, l, energy=11215):
        self.hxrd.energy = energy
        om, chi, phi, tth = self.hxrd.Q2Ang(self.material.Q((h, k, l)))
        return om, chi, phi, tth

    def motor_pos_from_energy(self, energy=11215):
        """
        This function returns the following stage positions for a given energy in eV:
            ana.om:    analyzer pitch angle
            ana.t_hor: analyzer crystal translation (distance between crystal and sample)
            det.t_hor: detector horizontal translation (mostly horizontal)
            det.t_ver: detector vertical translation (mostly vertical)
            det.rot:   detector rotation (to align surface to te beam reflected from the crystals)
        """
        om, chi, phi, tth = self.angs_from_hkl(*self.hkl, energy=energy)
        cfg = self.config["rowland"]
        r, a0 = cfg["r"], np.deg2rad(cfg["alpha_lin"])
        # NOTE: the following commented line rotates the Rowland circle with the crystal surface normal. To rotate with the normal of the hkl scattering plane instead, use tth/2 instead of om
        [x_s, y_s], [x_d, y_d] = self.calc_crystal_detector_positions(tth / 2, tth)
        det_t_hor = np.sin(a0) * y_d + np.cos(a0) * x_d
        det_t_ver = np.cos(a0) * y_d - np.sin(a0) * x_d
        det_rot = -(180 - tth) / 2
        t_hor = abs(x_s)
        return om, t_hor, det_t_hor, det_t_ver, det_rot

    def energy_from_motor_pos(self, om, t_hor, det_t_hor, det_t_ver, *args):
        tth = self.tth_from_motor_pos(
            t_hor=t_hor, det_t_hor=det_t_hor, det_t_ver=det_t_ver
        )
        energy = xu.lam2en(
            self.material.planeDistance(*self.hkl) * 2 * np.sin(np.deg2rad(tth / 2))
        )
        return energy

    def rowland_intersection(self, beta, tbeta):
        """
        This helper function calculates the x,y position of the intersection between the rowland circle for an angle of beta at a given tbeta angle:
            beta: angle between x-axis and line from the center of the crystal analyzer to the center of the rowland circle (i.e. 90-th)
            tbeta: angle between x-axis and line from the center of the crystal analyzer to a point on the rowland circle (i.e. 180-tth)
        """
        cfg = self.config["rowland"]
        r = cfg["r"]
        b = np.deg2rad(beta)
        tb = np.deg2rad(tbeta)
        x0 = r / 2 * np.cos(b)
        y0 = r / 2 * np.sin(b)
        p = np.cos(tb) * x0 + np.sin(tb) * y0
        d = p + np.sqrt(p ** 2 + (r / 2) ** 2 - x0 ** 2 - y0 ** 2)
        x, y = np.array([d * np.cos(tb), d * np.sin(tb)])
        return x, y

    def calc_crystal_detector_positions(self, om, tth):
        """
        This function returns the crystal and the detector positions in (x,y) assuming that the sample is at (0,0) for a given tth angle
        """
        tbeta = 180 - tth
        beta = 90 - om
        x_d, y_d = self.rowland_intersection(beta, tbeta)
        x_c, y_c = self.rowland_intersection(beta, 0)

        return np.array([-x_c, y_c]), np.array([x_d - x_c, y_d])

    def tth_from_motor_pos(self, t_hor, det_t_hor, det_t_ver):
        cfg = self.config["rowland"]
        a0 = np.deg2rad(cfg["alpha_lin"])
        y_d = np.sin(a0) * det_t_hor + np.cos(a0) * det_t_ver
        x_d = np.cos(a0) * det_t_hor - np.sin(a0) * det_t_ver + t_hor
        d_cryst_det = norm(np.array([x_d, y_d]))
        tth = np.rad2deg(np.arcsin(y_d / d_cryst_det))
        return 180 - tth


class Detector(Assembly):
    def __init__(
        self,
        name=None,
        pvname=None,
    ):
        super().__init__(name=name)
        self.name = name

        self._motor_cfg = {
            "MOT_1": "t_ver",
            "MOT_2": "t_hor",
            "MOT_3": "rot",
        }

        for pvmot, name in self._motor_cfg.items():
            try:
                self._append(
                    MotorRecord,
                    pvname + f":{pvmot}",
                    name=name,
                    is_setting=True,
                    is_status=True,
                    backlash_definition=False,
                )
            except:
                self._append(
                    AdjustableMemory,
                    name=name,
                    is_setting=True,
                    is_status=True,
                )
                print(
                    f"Initialization of epics motor {name}: {pvname}:{pvmot} failed, replaced by dummy!"
                )


class RIXS(Assembly):
    def __init__(
        self,
        name=None,
        pvname="SARES22-RIXS",
        alias_namespace=None,
    ):
        super().__init__(name=name)
        self.pvname = pvname
        self.config = {
            "crystals": {
                "Si533": {
                    "xu": xu.HXRD(
                        xu.materials.Si.Q(0, -1, 1), xu.materials.Si.Q(5, 3, 3)
                    ),
                    "material": xu.materials.Si,
                },
                "Si844": {
                    "xu": xu.HXRD(
                        xu.materials.Si.Q(0, -1, 1), xu.materials.Si.Q(8, 4, 4)
                    ),
                    "material": xu.materials.Si,
                },
            },
            "rowland": {
                "r": 1000,
                "alpha_lin": 90 - 69,
                "tth_0": 0,
                "x0": 999.85,
            },
        }

        # append the detector
        self._append(
            Detector,
            name="det",
            pvname=pvname,
            is_setting=False,
            is_status="recursive",
        )
        # append an analyzer
        self.append_analyzer(
            pos=2,
            analyzer="Si533",
            hkl=(8, 4, 4),
            name="ana_2",
            det=self.__dict__["det"],
            pvname=pvname,
        )

        self.append_analyzer(
            pos=2,
            analyzer="Si844",
            hkl=(8, 4, 4),
            name="ana_2_laser",
            det=self.__dict__["det"],
            pvname=pvname,
        )

    def append_analyzer(
        self,
        pos=None,
        analyzer="Si533",
        hkl=(8, 4, 4),
        name=None,
        det=None,
        pvname=None,
    ):
        if not name:
            if not pos:
                print("must either provide name or pos")
                return
            else:
                name = f"ana_{int(pos)}"
        hxrd = self.config["crystals"][analyzer]["xu"]
        material = self.config["crystals"][analyzer]["material"]
        self._append(
            Analyzer,
            hxrd=hxrd,
            material=material,
            hkl=hkl,
            pos=pos,
            name=name,
            config=self.config,
            det=det,
            pvname=pvname,
            is_setting=False,
            is_status="recursive",
        )

    def gui(self, guiType="xdm"):
        """ Adjustable convention"""
        cmd = ["caqtdm", "-macro"]
        cmd += ["P=SARES22-RIXS", "ESB_RIXS_motors.ui"]
        return self._run_cmd(" ".join(cmd))

    # def get_adjustable_positions_str(self):
    #     ostr = "*****GPS motor positions******\n"

    #     for tkey, item in self.__dict__.items():
    #         if hasattr(item, "get_current_value"):
    #             pos = item.get_current_value()
    #             ostr += "  " + tkey.ljust(17) + " : % 14g\n" % pos
    #     return ostr

    # def __repr__(self):
    #     return self.get_adjustable_positions_str()
