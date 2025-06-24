from importlib import import_module
from diffcalc.hkl.calc import HklCalculation
from diffcalc.hkl.constraints import Constraints
from diffcalc.hkl.geometry import Position
from diffcalc.ub import calc as dccalc
from matplotlib import pyplot as plt
import pandas as pd
import numpy as np
from datetime import datetime
import os
from PIL import Image
from scipy.spatial.transform import Rotation

# from diffcalc.ub import calc calc import UBCalculation, Crystal
from eco.elements.assembly import Assembly
from eco.elements.adjustable import (
    AdjustableMemory,
    AdjustableFS,
    AdjustableVirtual,
    DummyAdjustable,
)
from eco.elements.detector import DetectorVirtual

from eco.elements.adj_obj import AdjustableObject
from epics import PV


class Diffractometer_Dummy(Assembly):
    def __init__(self, *args, name=None, **kwargs):
        Assembly.__init__(self, name=name)
        self._config_dict = DummyAdjustable("_config_dict")
        self._config_dict(
            {
                "thc": False,
                "robot": False,
                "kappa": False,
            }
        )
        self._append(AdjustableObject, self._config_dict, name="configuration")
        adjs = ["gamma", "mu", "delta", "eta", "chi", "phi"]
        for adj in adjs:
            self._append(
                DummyAdjustable,
                name=adj,
                is_setting=True,
                is_display=True,
                limits=[-180, 180],
            )


diffractometer_dummy = Diffractometer_Dummy(name="dummy")


class CrystalNew(Assembly):
    def __init__(self, *args, name=None, **kwargs):
        Assembly.__init__(self, name=name)
        nam = "a1"
        self._ucpars = ["a", "b", "c", "alpha", "beta", "gamma"]
        for name in self._ucpars:
            self._append(AdjustableMemory, 0, name=f"{name}")

    def set_unit_cell(self, a=None, b=None, c=None, alpha=None, beta=None, gamma=None):
        self.a(a)
        self.b(b)
        self.c(c)
        self.alpha(alpha)
        self.beta(beta)
        self.gamma(gamma)

    def _calc_cryst(self):
        self.crystal = dccalc.Crystal(
            self.name,
            {par: self.__dict__[par].get_current_value() for par in self._ucpars},
        )


class Crystals(Assembly):
    def __init__(self, diffractometer_you=diffractometer_dummy, name=None):
        super().__init__(name=name)
        self.diffractometer = diffractometer_you
        self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/crystals/{name}_list",
            name="_crystal_list",
            default_value={},
            is_setting=True,
        )
        cons = {
            "mu": None,
            "eta": None,
            "chi": None,
            "phi": None,
            "delta": None,
            "gamma": None,
            "a_eq_b": None,
            "bin_eq_bout": None,
            "betain": None,
            "betaout": None,
            "qaz": None,
            "naz": None,
            "alpha": None,
            "beta": None,
            "bisect": None,
            "psi": None,
            "omega": None,
        }
        self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/crystals/{name}_constraints",
            name="_constraints",
            default_value=cons,
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustableObject,
            self._constraints,
            name="constraints",
            is_setting=False,
            is_display="recursive",
        )
        for key, meta in self._crystal_list().items():
            if self.diffractometer.name in meta:
                self._append(
                    DiffGeometryYou,
                    diffractometer_you=self.diffractometer,
                    constraints=self.constraints,
                    name=key,
                )

    def create_crystal(self, name=None):
        if name == None:
            specials = np.array(
                [
                    "'",
                    '"',
                    ".",
                    " ",
                    "/",
                    "(",
                    ")",
                    "[",
                    "]",
                    "{",
                    "}",
                    "|",
                    "-",
                    "*",
                    "+",
                ]
            )
            selecting = True
            while selecting:
                name = input(
                    "Please choose a name for your crystal (no spaces or other special characters):"
                )
                in_name = np.array(
                    [s in name for s in specials]
                    + [s == name for s in list(self._crystal_list().keys())]
                )
                if name == "":
                    print(f"Name cannot be empty.")
                if np.any(in_name[: len(specials)]):
                    print(
                        f"Special character(s) {specials[in_name[:len(specials)]]} in name not allowed."
                    )
                elif np.any(in_name[len(specials) :]):
                    print(
                        f"Name {np.array(list(self._crystal_list().keys()))[in_name[len(specials):]]}  already exists."
                    )
                else:
                    break

        self._append(
            DiffGeometryYou,
            diffractometer_you=self.diffractometer,
            name=name,
            constraints=self.constraints,
            is_setting=True,
            is_display=False,
        )
        crystals = self._crystal_list()
        crystals[name] = [str(datetime.now()), self.diffractometer.name]
        self._crystal_list.mv(crystals)
        self.__dict__[name].new_ub()

    def delete_crystal(self, name=None):
        """
        Delete crystal with a given name, deletes also the files.
        """
        crystal_names = list(self._crystal_list().keys())
        if name == None:
            input_message = "Select the crystal to delete:\nq) quit\n"
            for index, crystal in enumerate(crystal_names):
                input_message += f"{index:2}) {crystal:15}\n"
            idx = ""
            input_message += "Your choice: "
            while idx not in range(len(crystal_names)):
                idx = input(input_message)
                if idx == "q":
                    return
                else:
                    try:
                        idx = int(idx)
                    except:
                        continue
                print(f"Selected crystal: {crystal_names[idx]}")
            name = crystal_names[idx]
        elif not name in crystal_names:
            print(f"Crystal {name} has not been defined.")
            return
        sure = "n"
        sure = input(
            f"are you sure you want to permanently remove the crystal {name} and its UB matrix and memories (y/n)? "
        )
        if sure == "y":
            crystals = self._crystal_list()
            meta = crystals[name]
            if self.diffractometer.name in meta:
                self.deactivate_crystal(name=name)
            removed = crystals.pop(name)
            del removed
            self._crystal_list.mv(crystals)
            attrs = [
                "unit_cell",
                "u_matrix",
                "ub_matrix",
                "orientations",
                "reflections",
                "constraints",
            ]
            for a in attrs:
                if os.path.exists(
                    f"/photonics/home/gac-bernina/eco/configuration/crystals/{name}_{a}"
                ):
                    os.remove(
                        f"/photonics/home/gac-bernina/eco/configuration/crystals/{name}_{a}"
                    )
            print(f"Deleted crystal {name}.")
        else:
            print(f"Aborted deletion of crystal {name}.")

    def activate_crystal(self, name=None):
        crystals = self._crystal_list()
        inactive_crystals = [
            k for k in crystals.keys() if not self.diffractometer.name in crystals[k]
        ]
        active_crystals = [
            k for k in crystals.keys() if self.diffractometer.name in crystals[k]
        ]
        if name == None:
            input_message = "Select the crystal to activate:\nq) quit\n"
            for index, crystal in enumerate(inactive_crystals):
                input_message += f"{index:2}) {crystal:15}\n"
            idx = ""
            input_message += "Your choice: "
            while idx not in range(len(inactive_crystals)):
                idx = input(input_message)
                if idx == "q":
                    return
                else:
                    try:
                        idx = int(idx)
                    except:
                        continue
            name = inactive_crystals[idx]
        elif name in active_crystals:
            print(
                f"Crystal {name} is already active. Use diffcalc.{name} to start calculations."
            )
            return
        elif name not in inactive_crystals:
            print(
                f"Crystal {name} has not been defined yet. Use diffcalc.create_crystal() to create a new crystal."
            )
            return

        self._append(
            DiffGeometryYou,
            diffractometer_you=self.diffractometer,
            name=name,
            constraints=self.constraints,
            is_setting=True,
            is_display=False,
        )
        meta = crystals[name]
        if not self.diffractometer.name in meta:
            meta = meta + [self.diffractometer.name]
        crystals[name] = meta
        self._crystal_list.mv(crystals)
        print(f"Activated crystal: {name}")

    def deactivate_crystal(self, name=None):
        crystals = self._crystal_list()
        inactive_crystals = [
            k for k in crystals.keys() if not self.diffractometer.name in crystals[k]
        ]
        active_crystals = [
            k for k in crystals.keys() if self.diffractometer.name in crystals[k]
        ]
        if name == None:
            active_crystals = [
                k for k in crystals.keys() if self.diffractometer.name in crystals[k]
            ]
            input_message = "Select the crystal to activate:\nq) quit\n"
            for index, crystal in enumerate(active_crystals):
                input_message += f"{index:2}) {crystal:15}\n"
            idx = ""
            input_message += "Your choice: "
            while idx not in range(len(active_crystals)):
                idx = input(input_message)
                if idx == "q":
                    return
                else:
                    try:
                        idx = int(idx)
                    except:
                        continue
                print(f"Selected crystal: {active_crystals[idx]}")
            name = active_crystals[idx]
        elif name in inactive_crystals:
            print(f"Crystal {name} is already inactive.")
            return
        elif name not in active_crystals:
            print(f"Crystal {name} has not been defined, yet.")
            return
        meta = crystals[name]
        if self.diffractometer.name in meta:
            i = meta.index(self.diffractometer.name)
            meta.pop(i)
        crystals[name] = meta
        self._crystal_list.mv(crystals)
        removed = self.__dict__.pop(name)
        self.alias.pop_object(removed.alias)
        del removed
        print(f"Deactivated crystal: {name}")


class DiffGeometryYou(Assembly):
    def __init__(self, diffractometer_you=None, constraints=None, name=None):
        super().__init__(name=name)
        # self._append(diffractometer_you,call_obj=False, name='diffractometer')
        self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/crystals/{name}_unit_cell",
            name="unit_cell",
            default_value={
                "name": "",
                "a": 1,
                "b": 1,
                "c": 1,
                "alpha": 90,
                "beta": 90,
                "gamma": 90,
            },
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/crystals/{name}_u_matrix",
            name="u_matrix",
            default_value=[],
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/crystals/{name}_ub_matrix",
            name="ub_matrix",
            default_value=[],
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/crystals/{name}_orientations",
            name="orientations",
            default_value=[],
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/crystals/{name}_reflections",
            name="reflections",
            default_value=[],
            is_setting=True,
        )
        self.diffractometer = diffractometer_you
        self.constraints = constraints
        adjs = ["gamma", "mu", "delta", "eta", "chi", "phi"]
        cfg = []
        if hasattr(self.diffractometer, "configuration"):
            cfg = self.diffractometer.configuration

        adjustables_dict = {}
        adjustables_dict.update(self.diffractometer.__dict__)

        ### use robot motors if robot is in config
        if cfg.robot():
            self._append(
            AdjustableFS,
            f"/photonics/home/gac-bernina/eco/configuration/crystals/move_robot",
            name="move_robot",
            default_value=True,
            is_setting=False,
        )
            def rob_get(a): return a
            def rob_set(a):
                if self.move_robot(): 
                    return [a]
                else:
                    return None
            gam_rob = AdjustableVirtual([self.diffractometer.gamma_robot],rob_get, rob_set, name="gamma_robot", check_limits=True)
            del_rob = AdjustableVirtual([self.diffractometer.delta_robot],rob_get, rob_set, name="delta_robot", check_limits=True)
            get_lims = lambda a: a
            gam_rob.get_limits = get_lims(self.diffractometer.gamma_robot.get_limits)
            del_rob.get_limits = get_lims(self.diffractometer.delta_robot.get_limits)
            adjustables_dict.update(
                {
                    "gamma": gam_rob,
                    "delta": del_rob,
                }
            )
        ### add the phi constraint to thc as phi_wobble if thc is in config
        if cfg.thc():
            def phi_wobble_get(a): return a
            def phi_wobble_set(a): return [a]
            self.diffractometer.thc._append(AdjustableVirtual, [self.constraints.phi], phi_wobble_get, phi_wobble_set, name='phi_wobble')
        if cfg.kappa():
            adjs = ["gamma", "mu", "delta", "eta_kap", "kappa", "phi_kap"]

        self._diff_adjs = {}
        self._diff_adjs_constrained = {}
        for adj in adjs:
            if adj in adjustables_dict.keys():
                if adjustables_dict[adj].__class__ is not DetectorVirtual:
                    self._diff_adjs[adj] = adjustables_dict[adj]
                else:
                    self._diff_adjs_constrained[adj] = self.constraints.__dict__[adj]
            else:
                self._diff_adjs_constrained[adj] = self.constraints.__dict__[adj]
                self.diffractometer._append(
                    DetectorVirtual,
                    [self.constraints.__dict__[adj]],
                    lambda a: a,
                    name=adj,
                    is_setting=False,
                    is_display=True,
                )

        def get_h(*args, **kwargs):
            return self.calc_hkl()[0]

        def set_h(val):
            return self._calc_angles_unique_diffractometer([val, None, None])

        def get_k(*args, **kwargs):
            return self.calc_hkl()[1]

        def set_k(val):
            return self._calc_angles_unique_diffractometer([None, val, None])

        def get_l(*args, **kwargs):
            return self.calc_hkl()[2]

        def set_l(val):
            return self._calc_angles_unique_diffractometer([None, None, val])

        def get_hkl(*args, **kwargs):
            return self.calc_hkl()

        self._append(
            AdjustableVirtual,
            list(self._diff_adjs.values()),
            get_h,
            set_h,
            name="h",
            change_simultaneously=True,
        )
        self._append(
            AdjustableVirtual,
            list(self._diff_adjs.values()),
            get_k,
            set_k,
            name="k",
            change_simultaneously=True,
        )
        self._append(
            AdjustableVirtual,
            list(self._diff_adjs.values()),
            get_l,
            set_l,
            name="l",
            change_simultaneously=True,
        )
        self._append(
            AdjustableVirtual,
            list(self._diff_adjs.values()),
            get_hkl,
            self._calc_angles_unique_diffractometer,
            name="hkl",
        )
        self.to_diffcalc()

    def convert_from_you(self, **kwargs):
        cfg = self.diffractometer.configuration
        if cfg.kappa():
            eta_kap, kappa, phi_kap = self.diffractometer.calc_you2kappa(
                kwargs["eta"], kwargs["chi"], kwargs["phi"]
            )
            kwargs.update({"eta_kap": eta_kap, "kappa": kappa, "phi_kap": phi_kap})
        return {key: kwargs[key] for key in self._diff_adjs.keys()}

    def convert_to_you(
        self,
        gamma=None,
        mu=None,
        delta=None,
        eta=None,
        chi=None,
        phi=None,
        eta_kap=None,
        kappa=None,
        phi_kap=None,
    ):
        cfg = self.diffractometer.configuration
        if cfg.kappa():
            eta, chi, phi = self.diffractometer.calc_kappa2you(eta_kap, kappa, phi_kap)
        return gamma, mu, delta, eta, chi, phi

    def get_diffractometer_angles(self):
        ### assume that all angles exist in self._diff_adjs ###
        gamma, mu, delta, eta, chi, phi = self.convert_to_you(
            **{
                key: adj()
                for key, adj in {
                    **self._diff_adjs,
                    **self._diff_adjs_constrained,
                }.items()
            }
        )
        return mu, delta, gamma, eta, chi, phi

    def _calc_angles_unique_diffractometer(self, hkl):
        angles = self.calc_angles_unique(*hkl)
        angles_diff_dict = self.convert_from_you(**angles)
        return [angles_diff_dict[tk] for tk in self._diff_adjs.keys()]

    def check_target_value_within_limits(self, **kwargs):
        ### virtual adjustables got a new function check_target_value_within_limits(values)
        in_lims = []
        target_dict = self.convert_from_you(**kwargs)
        for axname, target_value in target_dict.items():
            adj = self._diff_adjs[axname]
            if hasattr(adj, "get_limits"):
                lim_low, lim_high = adj.get_limits()
                in_lims.append((lim_low < target_value) and (target_value < lim_high))
            else:
                raise Exception(f"Failed to get limits of adjustable {adj.name}")
        return all(in_lims)

    def new_ub(self):
        ### missing: clear ub ###
        ### missing: check ub ###
        crystal_name = input(f"Name of the crystal: ({self.name})" or str(self.name))
        a = float(input(f"Lattice constant a (1): ") or 1)
        b = float(input(f"Lattice constant b ({a}): ") or a)
        c = float(input(f"Lattice constant c ({a}): ") or a)
        alpha = float(input("Angle alpha (90): ") or 90)
        beta = float(input(f"Angle beta ({alpha}): ") or alpha)
        gamma = float(input(f"Angle gamma ({alpha}): ") or alpha)
        im = Image.open(
            "/photonics/home/gac-bernina/eco/configuration/crystals/you_diffractometer.png"
        )
        normal = []
        while not len(normal) == 3:
            try:
                normal = [
                    float(val)
                    for val in input(
                        "(h,k,l) surface normal (along YOU z-axis), e.g. 0,0,1: "
                    ).split(",")
                    or [0, 0, 1]
                ]
            except:
                continue
        inplane = []
        while not len(inplane) == 3:
            try:
                inplane = [
                    float(val)
                    for val in input(
                        "(h,k,l) in-plane orientation along beam (YOU y-axis), e.g. 1,0,0: "
                    ).split(",")
                    or [1, 0, 0]
                ]
            except:
                continue
        self.set_unit_cell(crystal_name, a, b, c, alpha, beta, gamma)
        self.add_orientation(normal, (0, 0, 1), tag="surface normal")
        print(self.orientations())
        self.add_orientation(
            inplane, (0, 1, 0), tag="in-plane along x-ray beam direction"
        )
        print(self.orientations())
        self.calc_ub()
        print(
            "UB was calculated - next please set the constraints (.constraints) and the limits of the diffractometer motors"
        )

    def set_unit_cell(
        self, name_crystal, a=None, b=None, c=None, alpha=None, beta=None, gamma=None
    ):
        self.unit_cell.set_target_value(
            {
                "name": name_crystal,
                "a": a,
                "b": b,
                "c": c,
                "alpha": alpha,
                "beta": beta,
                "gamma": gamma,
            }
        ).wait()
        self.to_diffcalc()

    def to_diffcalc(self):
        self.ubcalc = dccalc.UBCalculation("you")
        uc = self.unit_cell()
        self.ubcalc.set_lattice(**uc)
        for ori in self.orientations():
            self.ubcalc.add_orientation(
                ori["hkl"],
                ori["xyz"],
                **{k: v for k, v in ori.items() if k not in ["xyz", "hkl"]},
            )
        for refl in self.reflections():
            position = Position(*refl["position"])
            self.ubcalc.add_reflection(
                refl["hkl"],
                position,
                refl["energy"] * 1e-3,
                **{
                    k: v
                    for k, v in refl.items()
                    if k not in ["position", "hkl", "energy"]
                },
            )
        self._u_ub_to_dc()

    def add_reflection(
        self,
        h,
        k,
        l,
        mu=None,
        delta=None,
        gamma=None,
        eta=None,
        chi=None,
        phi=None,
        energy=None,
        tag=None,
    ):
        """
        Example: add_reflection(2,2,0)

        Add a reference reflection.

        Adds a reflection position in degrees and in the systems internal
        representation.

        Parameters
        ----------
        h, k, l : float
            hkl index of the reflection
        mu, delta, gamma, eta, chi, phi: float
            diffractometer angles in degrees, if not given, the current diffractometer angles are used
        energy : float
            energy of the x-ray beam, if not given, the mono or machine energy are used depending on the beamline mode
        tag : Optional[str], default = None
            identifying tag for the reflection
        """
        hkl = [h, k, l]
        if np.any([hasattr(i, "__len__") for i in [h, k, l]]):
            print("Please enter hkl as touple: add_reflection(h, k, l)")
            return
        setvals = [mu, delta, gamma, eta, chi, phi]
        curvals = self.get_diffractometer_angles()
        angs = [
            curval if setval == None else setval
            for setval, curval in zip(setvals, curvals)
        ]
        if energy is None:
            energy = self.get_energy()
        position = Position(*angs)
        self.ubcalc.add_reflection(hkl, position, energy, tag=tag)
        self.reflections.set_target_value(
            self.reflections()
            + [{"hkl": hkl, "position": angs, "energy": energy, "tag": tag}]
        ).wait()

    def del_reflection(self, idx):
        """Delete a reference reflection.

        Parameters
        ----------
        idx : int
            index of the deleted reflection
        """
        self.ubcalc.del_reflection(idx)
        refls = self.reflections()
        removed = refls.pop(idx)
        self.reflections.set_target_value(refls).wait()
        print(f"Removed reflection {removed}")

        self.to_diffcalc()

    def add_orientation(self, hkl, xyz, position=None, tag=None):
        """Add a reference orientation.

        Adds a reference orientation in the diffractometer
        coordinate system.

        Parameters
        ----------
        hkl : :obj:`tuple` of numbers
            hkl index of the reference orientation
        xyz : :obj:`tuple` of numbers
            xyz coordinate of the reference orientation
        position: :obj:`list` or :obj:`tuple` of numbers
            list of diffractometer angles in internal representation in degrees
        tag : str
            identifying tag for the reflection
        """
        if not hasattr(hkl, "__len__"):
            print("Please enter hkl as touple or list: add_orientation([h, k, l])")
            return
        if not hasattr(xyz, "__len__"):
            print("Please enter xyz as touple or list: add_orientation([x, y, z])")
            return
        orientations = self.orientations() + [
            {"hkl": hkl, "xyz": xyz, "position": position, "tag": tag}
        ]
        self.orientations.set_target_value(orientations).wait()
        self.to_diffcalc()

    def del_orientation(self, idx):
        """Delete a reference reflection.

        Parameters
        ----------
        idx : int
            index of the deleted reflection
        """
        refls = self.orientations()
        removed = refls.pop(idx)
        self.orientations.set_target_value(refls).wait()
        print(f"Removed reflection {removed}")
        self.to_diffcalc()

    def calc_ub(self, idx1=0, idx2=1):
        """Calculate UB matrix.

        Calculate UB matrix using two reference reflections and/or
        reference orientations.

        By default use the first two reference reflections when provided.
        If one or both reflections are not available use one or two reference
        orientations to complement mission reflection data.

        Parameters
        ----------
        idx1: int or str, optional
            The index or the tag of the first reflection or orientation.
        idx2: int or str, optional
            The index or the tag of the second reflection or orientation.
        """
        self.to_diffcalc()
        self.ubcalc.calc_ub(idx1 + 1, idx2 + 1)
        self._u_ub_from_dc()

    def show_you_geometry(self):
        im = Image.open(
            "/photonics/home/gac-bernina/eco/configuration/crystals/you_diffractometer.png"
        )
        im.show()

    def refine_ub(
        self,
        hkl,
        mu=None,
        delta=None,
        gamma=None,
        eta=None,
        chi=None,
        phi=None,
        energy=None,
        refine_lattice=False,
        refine_umatrix=False,
    ):
        """
        Refine UB matrix to using single reflection.

        Refine UB matrix to match diffractometer position for the specified
        reflection. Refined U matrix will be accurate up to an azimuthal rotation
        around the specified scattering vector.

        Parameters
        ----------
        hkl: Tuple[float, float, float] Miller indices of the reflection.
        pos: Position Diffractometer position object.
        wavelength: float Radiation wavelength.
        refine_lattice: Optional[bool], default = False

        Apply refined lattice parameters to the current UB calculation object.
        refine_umatrix: Optional[bool], default = False
        Apply refined U matrix to the current UB calculation object.

        Returns
        -------
        Tuple[np.ndarray, Tuple[str, float, float, float, float, float, float]]
        Refined U matrix as NumPy array and refined crystal lattice parameters.

        """
        if refine_lattice:
            print("fitting the lattice is not yet implemented")
            return
        setvals = [mu, delta, gamma, eta, chi, phi]
        curvals = self.get_diffractometer_angles()
        angs = [
            curval if setval == None else setval
            for setval, curval in zip(setvals, curvals)
        ]
        if energy is None:
            energy = self.get_energy()
        wl = self.en2lam(energy)
        position = Position(*angs)
        self.to_diffcalc()
        self.ubcalc.refine_ub(
            hkl,
            position=position,
            wavelength=wl,
            refine_lattice=refine_lattice,
            refine_umatrix=refine_umatrix,
        )
        self._u_ub_from_dc()

    def fit_ub(self, indices=None, refine_lattice=False, refine_umatrix=True):
        """Refine UB matrix using reference reflections.

        Parameters
        ----------
        indices: Sequence[Union[str, int]]
            List of reference reflection indices or tags.
        refine_lattice: Optional[bool], default = False
            Apply refined lattice parameters to the current UB calculation object.
        refine_umatrix: Optional[bool], default = False
            Apply refined U matrix to the current UB calculation object.

        Returns
        -------
        Tuple[np.ndarray, Tuple[str, float, float, float, float, float, float]]
            Refined U matrix as NumPy array and refined crystal lattice parameters.
        """
        self.to_diffcalc()
        if indices is None:
            indices = list(range(len(self.reflections())))
        ub, lat = self.ubcalc.fit_ub(
            indices, refine_lattice=refine_lattice, refine_umatrix=refine_umatrix
        )
        if refine_umatrix:
            print("\nFitted UB matrix applied")
        else:
            print(
                "\nFitted UB matrix not applied. To apply it, set refine_umatrix=True"
            )
        print(ub)
        if refine_lattice:
            print("\nFitted lattice applied")
        else:
            print("\nFitted lattice not applied. To apply it, set refine_lattice=True")
        for k, val in {
            "name": lat[0],
            "a": lat[1],
            "b": lat[2],
            "c": lat[3],
            "alpha": lat[4],
            "beta": lat[5],
            "gamma": lat[6],
        }.items():
            print(f"{k:8}: {val}")
        self._u_ub_from_dc()
        if refine_lattice:
            self._lat_from_dc()

    def calc_angles(self, h=None, k=None, l=None, energy=None, constraints_update={}):
        """calculate diffractometer angles for a given h,k,l and energy in eV.
        If any of the h, k, l are not given, their current value is used instead.
        energy: float energy of the x-ray beam, if not given, the mono or machine energy are used depending on the beamline mode
        Shows all solutions neglecting diffractometer limits"""
        setvals = [h, k, l]
        curvals = [self.h, self.k, self.l]
        h, k, l = [
            curval() if setval == None else setval
            for setval, curval in zip(setvals, curvals)
        ]
        self.to_diffcalc()
        constraints_dict = {
            "nu" if k == "gamma" else k: v
            for k, v in self.constraints._base_dict().items()
        }
        constraints_dict.update(constraints_update)
        cons = Constraints(constraints_dict)

        hklcalc = HklCalculation(self.ubcalc, cons)
        if energy is None:
            energy = self.get_energy()
        lam = self.en2lam(energy)
        result = hklcalc.get_position(h, k, l, lam)
        result = pd.concat(
            [
                pd.DataFrame.from_dict(
                    {
                        **{
                            "gamma" if k == "nu" else k: v
                            for k, v in tres[0].asdict.items()
                        },
                        **tres[1],
                    },
                    orient="index",
                    columns=[f"sol. {n}"],
                )
                for n, tres in enumerate(result)
            ],
            axis=1,
        )
        return result.T

    def calc_angles_plot(
        self,
        h=None,
        k=None,
        l=None,
        det_distance=500,
        energy=None,
        constraints_update={},
        solution_index=0,
        clear_fig=True,
        ax=None,
    ):
        sols = self.calc_angles(
            h=h, k=k, l=l, energy=energy, constraints_update=constraints_update
        )
        solution = sols.iloc[solution_index]
        b2y = (
            Rotation.from_rotvec(-np.pi / 2 * np.array([0, 0, 1])).as_matrix()
            @ Rotation.from_rotvec(-np.pi / 2 * np.array([0, 1, 0])).as_matrix()
        )
        y2b = np.linalg.inv(b2y)
        fig = plt.figure(f"Recspace {self.name} (h,k,l) = ({h},{k},{l})")
        if clear_fig:
            fig.clf()

        self.diffractometer.recspace_conv.plot_geom(
            mu=solution["mu"],
            eta=solution["eta"],
            chi=solution["chi"],
            phi=solution["phi"],
            gamma=solution["gamma"],
            delta=solution["delta"],
            energy=energy,
            detector_distance=det_distance,
            fig=fig,
            ax=ax,
            ub_matrix=y2b @ self.ub_matrix.get_current_value(),
        )
        return solution

    def calc_angles_unique(self, h=None, k=None, l=None, energy=None):
        """calculate unique solution of diffractometer angles for a given h,k,l and energy in eV.
        If any of the h, k, l are not given, their current value is used instead.
        If the energy is not given, the monochromator energy is used."""
        df = self.calc_angles(h, k, l, energy)
        in_lims = np.array(
            [
                self.check_target_value_within_limits(**df.loc[idx].to_dict())
                for idx in df.index
            ]
        )
        idx_in = df.index[in_lims]
        idx_out = df.index[~in_lims]

        if len(idx_in) > 1:
            s = f"There is not a unique angular configuration to reach ({h},{k},{l}), please change the diffractometer motor soft limits to allow only one of the solutions shown below. \nCurrent limits are:\n"
            for axname, adj in self._diff_adjs.items():
                if hasattr(adj, "get_limits"):
                    s += f"{axname}: {adj.get_limits()}\n"
            print(s)
            print("Solutions:")
            print(df.loc[idx_in])
            raise Exception("No unique solution")
        elif len(idx_in) == 0:
            s = "There is no angular configuration, which is allowed for the current diffractometer motor soft limits. please check the diffractometer limits to allow one of the solutions shown below. \nCurrent limits are:\n"
            for axname, adj in self._diff_adjs.items():
                if hasattr(adj, "get_limits"):
                    s += f"{axname}: {adj.get_limits()}\n"
            print(s)
            print("Solutions:")
            print(df)
            raise Exception("No unique solution")
        solution_unique = df.loc[idx_in[0]]

        return solution_unique.to_dict()

    def calc_hkl(
        self, mu=None, delta=None, gamma=None, eta=None, chi=None, phi=None, energy=None
    ):
        """calculate (h,k,l) for given diffractometer angles and energy in eV.
        If any of the diffractometer angles are not given, their current value is used instead.
        If the energy is not given, the monochromator energy is used"""
        setvals = [mu, delta, gamma, eta, chi, phi]
        curvals = self.get_diffractometer_angles()
        angs = [
            curval if setval == None else setval
            for setval, curval in zip(setvals, curvals)
        ]
        pos = Position(*angs)
        self.to_diffcalc()
        if energy is None:
            energy = self.get_energy()
        lam = self.en2lam(energy)
        constraints_dict = {
            "nu" if k == "gamma" else k: v
            for k, v in self.constraints._base_dict().items()
        }
        cons = Constraints(constraints_dict)
        hklcalc = HklCalculation(self.ubcalc, cons)
        try:
            hkl = hklcalc.get_hkl(pos=pos, wavelength=lam)
        except Exception as e:
            print(str(e))
            return
        return hkl

    def get_energy(self):
        energy = PV("SAROP21-ARAMIS:ENERGY").value
        if energy is None:
            raise ("Getting energy from monochromator / machine returned None")
        return energy

    def _u_ub_from_dc(self):
        self.ub_matrix(self.ubcalc.UB.tolist())
        self.u_matrix(self.ubcalc.U.tolist())

    def _u_ub_to_dc(self):
        if len(self.ub_matrix()) > 0:
            self.ubcalc.set_ub(self.ub_matrix())
            self.ubcalc.set_u(self.u_matrix())

    def _lat_from_dc(self):
        self.set_unit_cell(*self.ubcalc.crystal.get_lattice())

    def en2lam(self, en):
        """input: energy in eV, returns wavelength in A"""
        return 12398.419843320025 / en

    def lam2en(self, lam):
        """input: wavelength in A, returns energy in eV"""
        return 12398.419843320025 / lam

    pass
    # def __init__(sel):
