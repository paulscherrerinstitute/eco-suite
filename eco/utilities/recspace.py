from importlib import import_module
import pkgutil
from diffcalc.hkl.calc import HklCalculation
from diffcalc.hkl.constraints import Constraints
from diffcalc.hkl.geometry import Position
from diffcalc.ub import calc as dccalc
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
from typing import Tuple, Optional
from eco.elements.adj_obj import AdjustableObject
from epics import PV


class Diffractometer_Dummy(Assembly):
    def __init__(self, *args, name=None, **kwargs):
        Assembly.__init__(self, name=name)
        self.configuration = ["base", "arm"]
        adjs = ["nu", "mu", "delta", "eta", "chi", "phi"]
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
            name="crystal_list",
            default_value={},
            is_setting=True,
        )
        for key, meta in self.crystal_list().items():
            if self.diffractometer.name in meta:
                self._append(
                    DiffGeometryYou, diffractometer_you=self.diffractometer, name=key
                )

    def create_crystal(self, name=None):
        if name == None:
            name = input(
                "Please choose a name for your crystal (no spaces or other special characters):"
            )
        specials = np.array([".", " ", "/", "(", ")", "[", "]"])
        in_name = np.array(
            [s in name for s in specials]
            + [s == name for s in list(self.crystal_list().keys())]
        )
        if np.any(in_name):
            raise Exception(
                f"Special character(s) {specials[in_name]} in name not allowed or name already exists"
            )
        self._append(
            DiffGeometryYou,
            diffractometer_you=self.diffractometer,
            name=name,
            is_setting=True,
            is_display=False,
        )
        crystals = self.crystal_list()
        crystals[name] = [str(datetime.now()), self.diffractometer.name]
        self.crystal_list.mv(crystals)
        self.__dict__[name].new_ub()

    def delete_crystal(self, name=None):
        """
        Delete crystal with a given name, deletes also the files.
        """
        if name == None:
            crystal_names = list(self.crystal_list().keys())
            input_message = "Select the crystal to delete:\nq) quit\n"
            for index, crystal in enumerate(crystal_names):
                input_message += f"{index:2}) {crystal:15}\n"
            input_message += "Your choice: "
            idx = ""
            while idx not in range(len(crystal_names)):
                if idx == "q":
                    break
                idx = int(input(input_message))
                print(f"Selected crystal: {crystal_names[idx]}")
            name = crystal_names[idx]
        sure = "n"
        sure = input(
            f"are you sure you want to permanently remove the crystal {name} and its UB matrix and memories (y/n)? "
        )
        if sure == "y":
            crystals = self.crystal_list()
            meta = crystals[name]
            if self.diffractometer.name in meta:
                self.deactivate_crystal(name=name)
            removed = crystals.pop(name)
            del removed
            self.crystal_list.mv(crystals)
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

    def activate_crystal(self, name=None):
        crystals = self.crystal_list()
        if name == None:
            inactive_crystals = [
                k
                for k in crystals.keys()
                if not self.diffractometer.name in crystals[k]
            ]
            idx = ""
            input_message = "Select the crystal to activate:\nq) quit\n"
            for index, crystal in enumerate(inactive_crystals):
                input_message += f"{index:2}) {crystal:15}\n"
            input_message += "Your choice: "
            while idx not in range(len(inactive_crystals)):
                if idx == "q":
                    break
                idx = int(input(input_message))
                print(f"Activated crystal: {inactive_crystals[idx]}")
            name = inactive_crystals[idx]
        self._append(
            DiffGeometryYou,
            diffractometer_you=self.diffractometer,
            name=name,
            is_setting=True,
            is_display=False,
        )
        meta = crystals[name]
        if not self.diffractometer.name in meta:
            meta = meta + [self.diffractometer.name]
        crystals[name] = meta
        self.crystal_list.mv(crystals)

    def deactivate_crystal(self, name=None):
        crystals = self.crystal_list()
        if name == None:
            active_crystals = [
                k for k in crystals.keys() if self.diffractometer.name in crystals[k]
            ]
            idx = ""
            input_message = "Select the crystal to activate:\nq) quit\n"
            for index, crystal in enumerate(active_crystals):
                input_message += f"{index:2}) {crystal:15}\n"
            input_message += "Your choice: "
            while idx not in range(len(active_crystals)):
                if idx == "q":
                    break
                idx = int(input(input_message))
                print(f"Selected crystal: {active_crystals[idx]}")
            name = active_crystals[idx]
        meta = crystals[name]
        if self.diffractometer.name in meta:
            i = meta.index(self.diffractometer.name)
            meta.pop(i)
        crystals[name] = meta
        self.crystal_list.mv(crystals)
        removed = self.__dict__.pop(name)
        self.alias.pop_object(removed.alias)
        del removed


class DiffGeometryYou(Assembly):
    def __init__(self, diffractometer_you=None, name=None):
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
        cons = {
            "mu": None,
            "eta": None,
            "chi": None,
            "phi": None,
            "delta": None,
            "nu": None,
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

        adjs = ["nu", "mu", "delta", "eta", "chi", "phi"]
        cfg = []
        if hasattr(self.diffractometer, "configuration"):
            cfg = self.diffractometer.configuration

        ### merge config if it is a robot
        if "robot" in cfg:
            basename = cfg[1]
            import eco.bernina as b

            base = b.__dict__[basename]
            adjustables_dict = {}
            adjustables_dict.update(base.__dict__)
            adjustables_dict.update(
                {
                    "nu": self.diffractometer.spherical.gamma,
                    "delta": self.diffractometer.spherical.delta,
                }
            )
            self.diffractometer.configuration += base.configuration
            cfg = self.diffractometer.configuration
        else:
            adjustables_dict = self.diffractometer.__dict__
        if "kappa" in cfg:
            adjs = ["nu", "mu", "delta", "eta_kap", "kappa", "phi_kap"]
        self._diff_adjs = {
            adj: (
                adjustables_dict[adj]
                if adj in adjustables_dict.keys()
                else DummyAdjustable(name=adj + "dummy")
            )
            for adj in adjs
        }

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

        def set_hkl(val):
            return self._calc_angles_unique_diffractometer(val)

        self._append(
            AdjustableVirtual, list(self._diff_adjs.values()), get_h, set_h, name="h"
        )
        self._append(
            AdjustableVirtual, list(self._diff_adjs.values()), get_k, set_k, name="k"
        )
        self._append(
            AdjustableVirtual, list(self._diff_adjs.values()), get_l, set_l, name="l"
        )
        self._append(
            AdjustableVirtual,
            list(self._diff_adjs.values()),
            get_hkl,
            self._calc_angles_unique_diffractometer,
            name="hkl",
        )
        self.recalculate()

    def convert_from_you(self, **kwargs):
        cfg = self.diffractometer.configuration
        if "kappa" in cfg:
            eta_kap, kappa, phi_kap = self.diffractometer.calc_you2kappa(
                kwargs["eta"], kwargs["chi"], kwargs["phi"]
            )
            kwargs.update({"eta_kap": eta_kap, "kappa": kappa, "phi_kap": phi_kap})
        return {key: kwargs[key] for key in self._diff_adjs.keys()}

    def convert_to_you(
        self,
        nu=None,
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
        if "kappa" in cfg:
            eta, chi, phi = self.diffractometer.calc_kappa2you(eta_kap, kappa, phi_kap)
        return nu, mu, delta, eta, chi, phi

    def get_diffractometer_angles(self):
        ### assume that all angles exist in diffractometer at least as manual adjustable ###
        nu, mu, delta, eta, chi, phi = self.convert_to_you(
            **{key: adj() for key, adj in self._diff_adjs.items()}
        )
        return mu, delta, nu, eta, chi, phi

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

            # for val, adj in zip(target_dict, self._diff_adjs.values()):
            if hasattr(adj, "get_limits"):
                lim_low, lim_high = adj.get_limits()
                in_lims.append((lim_low < target_value) and (target_value < lim_high))
                # print(axname,lim_low,target_value,lim_high)
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
        normal = [
            float(val)
            for val in input(
                "(h,k,l) surface normal (along YOU z-axis), e.g. 0,0,1: "
            ).split(",")
            or [0, 0, 1]
        ]
        inplane = [
            float(val)
            for val in input(
                "(h,k,l) in-plane orientation along beam (YOU y-axis), e.g. 1,0,0: "
            ).split(",")
            or [1, 0, 0]
        ]
        self.set_unit_cell(crystal_name, a, b, c, alpha, beta, gamma)
        self.add_orientation(normal, (0, 0, 1), tag="surface normal")
        self.add_orientation(
            inplane, (0, 1, 0), tag="in-plane along x-ray beam direction"
        )
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
        )
        self.recalculate()

    def recalculate(self):
        self.ubcalc = dccalc.UBCalculation("you")
        # self.ubcalc.n_phi = [0,0,1]
        uc = self.unit_cell()
        self.ubcalc.set_lattice(uc.pop("name"), **uc)
        for ori in self.orientations():
            self.ubcalc.add_orientation(ori.pop("hkl"), ori.pop("xyz"), **ori)
        for refl in self.reflections():
            position = Position(*refl.pop("position"))
            self.ubcalc.add_reflection(
                refl.pop("hkl"), position, refl.pop("energy") * 1e-3, **refl
            )
        self._u_ub_to_dc()

    def add_reflection(
        self,
        hkl,
        mu=None,
        delta=None,
        nu=None,
        eta=None,
        chi=None,
        phi=None,
        energy=None,
        tag=None,
    ):
        """Add a reference reflection.

        Adds a reflection position in degrees and in the systems internal
        representation.

        Parameters
        ----------
        hkl : Tuple[float, float, float]
            hkl index of the reflection
        mu, delta, nu, eta, chi, phi: float
            diffractometer angles in degrees, if not given, the current diffractometer angles are used
        energy : float
            energy of the x-ray beam, if not given, the mono or machine energy are used depending on the beamline mode
        tag : Optional[str], default = None
            identifying tag for the reflection
        """
        setvals = [mu, delta, nu, eta, chi, phi]
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
        )

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
        self.reflections.set_target_value(refls)
        print(f"Removed reflection {removed}")
        self.recalculate()

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
        self.orientations.set_target_value(
            self.orientations()
            + [{"hkl": hkl, "xyz": xyz, "position": position, "tag": tag}]
        )
        self.recalculate()

    def del_orientation(self, idx):
        """Delete a reference reflection.

        Parameters
        ----------
        idx : int
            index of the deleted reflection
        """
        refls = self.orientations()
        removed = refls.pop(idx)
        self.orientations.set_target_value(refls)
        print(f"Removed reflection {removed}")
        self.recalculate()

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
        self.recalculate()
        self.ubcalc.calc_ub(idx1, idx2)
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
        nu=None,
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
        setvals = [mu, delta, nu, eta, chi, phi]
        curvals = self.get_diffractometer_angles()
        angs = [
            curval if setval == None else setval
            for setval, curval in zip(setvals, curvals)
        ]
        if energy is None:
            energy = self.get_energy()
        wl = self.en2lam(energy)
        position = Position(*angs)
        self.recalculate()
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
        self.recalculate()
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
        self.recalculate()
        constraints_dict = self._constraints()
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
                    {**tres[0].asdict, **tres[1]}, orient="index", columns=[f"sol. {n}"]
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
        self.diffractometer.recspace_conv.plot_geom(
            mu=solution["mu"],
            eta=solution["eta"],
            phi=solution["phi"],
            gamma=solution["nu"],
            delta=solution["delta"],
            energy=energy,
            detector_distance=det_distance,
            ub_matrix=y2b @ self.ub_matrix.get_current_value(),
        )

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
            print(
                f"There is not a unique angular configuration to reach ({h},{k},{l}), please change the diffractometer motor soft limits to allow only one of the solutions shown below:"
            )
            print(df.loc[idx_in])
            raise Exception("No unique solution")
        elif len(idx_in) == 0:
            print(
                "There is no angular configuration, which is allowed for the current diffractometer motor soft limits. please check the diffractometer limits."
            )
            print("Solutions")
            print(df)
            raise Exception("No unique solution")
        solution_unique = df.loc[idx_in[0]]

        return solution_unique.to_dict()

    def calc_hkl(
        self, mu=None, delta=None, nu=None, eta=None, chi=None, phi=None, energy=None
    ):
        """calculate (h,k,l) for given diffractometer angles and energy in eV.
        If any of the diffractometer angles are not given, their current value is used instead.
        If the energy is not given, the monochromator energy is used"""
        setvals = [mu, delta, nu, eta, chi, phi]
        curvals = self.get_diffractometer_angles()
        angs = [
            curval if setval == None else setval
            for setval, curval in zip(setvals, curvals)
        ]
        pos = Position(*angs)
        self.recalculate()
        if energy is None:
            energy = self.get_energy()
        lam = self.en2lam(energy)
        cons = Constraints(self._constraints())
        hklcalc = HklCalculation(self.ubcalc, cons)
        try:
            hkl = hklcalc.get_hkl(pos=pos, wavelength=lam)
        except Exception as e:
            print(str(e))
            return
        return hkl

    def get_energy(self):
        energy = PV("SAROP21-ARAMIS:ENERGY").value
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
