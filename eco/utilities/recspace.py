from diffcalc.hkl.calc import HklCalculation
from diffcalc.hkl.constraints import Constraints
from diffcalc.hkl.geometry import Position
from diffcalc.ub import calc as dccalc
import pandas as pd

# from diffcalc.ub import calc calc import UBCalculation, Crystal
from eco.elements.assembly import Assembly
from eco.elements.adjustable import AdjustableMemory, AdjustableFS, AdjustableVirtual
from typing import Tuple, Optional


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


class DiffGeometryYou(Assembly):
    def __init__(self, diffractometer_you=None, name=None):
        super().__init__(name=name)
        # self._append(diffractometer_you,call_obj=False, name='diffractometer')
        self._append(AdjustableMemory, {}, name="contraints")
        self._append(AdjustableMemory, {}, name="unit_cell")
        self._append(AdjustableMemory, {}, name="U_matrix")
        self._append(AdjustableMemory, {}, name="UB_matrix")
        self._append(AdjustableMemory, [], name="orientations")
        self.diffractometer = diffractometer_you

    def get_position_angles(self):
        nu = self.diffractometer.nu.get_current_value()
        mu = self.diffractometer.mu.get_current_value()
        delta = self.diffractometer.delta.get_current_value()
        eta = self.diffractometer.eta.get_current_value()
        chi = self.diffractometer.chi.get_current_value()
        phi = self.diffractometer.phi.get_current_value()
        return mu, delta, nu, eta, chi, phi

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
        uc = self.unit_cell()
        self.ubcalc.set_lattice(uc.pop("name"), **uc)
        for ori in self.orientations():
            self.ubcalc.add_orientation(ori.pop("hkl"), ori.pop("xyz"), **ori)

    def add_reflection(
        self,
        hkl,
        position,
        energy,
        tag=None,
    ):
        """Add a reference reflection.

        Adds a reflection position in degrees and in the systems internal
        representation.

        Parameters
        ----------
        hkl : Tuple[float, float, float]
            hkl index of the reflection
        position: Position
            list of diffractometer angles in internal representation in degrees
        energy : float
            energy of the x-ray beam
        tag : Optional[str], default = None
            identifying tag for the reflection
        """

        self.ubcalc.add_reflection(hkl, position, energy, tag=tag)
        self.reflections.set_target_value(
            self.reflections()
            + [{"hkl": hkl, "position": position, "energy": energy, "tag": tag}]
        )

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
        self.recalculate()
        self.ubcalc.add_orientation(hkl, xyz, position=None, tag=None)
        self.orientations.set_target_value(
            self.orientations()
            + [{"hkl": hkl, "xyz": xyz, "position": position, "tag": tag}]
        )
        self.recalculate()

    def calc_ub(self, *args, **kwargs):
        self.ubcalc.calc_ub(*args, **kwargs)

    def fit_ub(self, *args, **kwargs):
        self.ubcalc.fit_ub(*args, **kwargs)

    pass
    # def __init__(sel):

class DiffGeometryYou2(Assembly):
    def __init__(self, diffractometer_you=None, name=None):
        super().__init__(name=name)
        # self._append(diffractometer_you,call_obj=False, name='diffractometer')
        #self._append(AdjustableMemory, {}, name="contraints")
        #self._append(AdjustableMemory, {}, name="unit_cell")
        #self._append(AdjustableMemory, [], name="U_matrix")
        #self._append(AdjustableMemory, [], name="UB_matrix")
        #self._append(AdjustableMemory, [], name="orientations")
        self._append(AdjustableFS, f'/photonics/home/gac-bernina/temp/diffc/{name}_constraints', name="constraints", default_value={})
        self._append(AdjustableFS, f'/photonics/home/gac-bernina/temp/diffc/{name}_unit_cell', name="unit_cell", default_value={})
        self._append(AdjustableFS, f'/photonics/home/gac-bernina/temp/diffc/{name}_u_matrix', name="u_matrix", default_value=[])
        self._append(AdjustableFS, f'/photonics/home/gac-bernina/temp/diffc/{name}_ub_matrix', name="ub_matrix", default_value=[])
        self._append(AdjustableFS, f'/photonics/home/gac-bernina/temp/diffc/{name}_orientations', name="orientations", default_value=[])
        self._append(AdjustableFS, f'/photonics/home/gac-bernina/temp/diffc/{name}_reflections', name="reflections", default_value=[])
        self.diffractometer = diffractometer_you
        adjs = [
            self.diffractometer.nu(),
            self.diffractometer.mu(),
            self.diffractometer.delta(),
            self.diffractometer.eta(),
            self.diffractometer.chi,
            self.diffractometer.phi(),
        ]
        def get_h(val):
            return self.get_current_value()[0]
        def set_h(val):
            return self.calc_angles_unique(h=val)
        def get_k(val):
            return self.get_current_value()[1]
        def set_k(val):
            return self.calc_angles_unique(k=val)
        def get_l(val):
            return self.get_current_value()[2]
        def set_l(val):
            return self.calc_angles_unique(l=val)

        self._append(AdjustableVirtual, adjs, get_h, set_h, name="h")
        self._append(AdjustableVirtual, adjs, get_k, set_k, name="k")
        self._append(AdjustableVirtual, adjs, get_l, set_l, name="l")
        self._append(AdjustableVirtual, adjs, self.calc_hkl, self.calc_angles_unique, name="hkl")
        self.recalculate()

    def new_ub(self):
        ### missing: clear ub ###
        crystal_name = input("Name of the crystal: ")
        a = float(input("Lattice constant: "))
        b = float(input(f"Lattice constant b {(a)}: ") or a)
        c = float(input(f"Lattice constant c {(a)}: ") or a)
        alpha = float(input("Angle alpha (90): ") or 90)
        beta = float(input(f"Angle beta {(alpha)}: ") or alpha)
        gamma = float(input(f"Angle gamma {(alpha)}: ") or alpha)
        normal = [float(val) for val in input("(h,k,l) surface normal (along YOU z-axis) without brackets and ',' separated: ").split(",")]
        inplane = [float(val) for val in input("(h,k,l) in-plane orientation along beam (YOU y-axis) without brackets and ',' separated: ")]
        self.set_unit_cell(crystal_name, a, b, c, alpha, beta, gamma)
        self.add_orientation(normal, (1,0,0), tag='surface normal')
        self.add_orientation(inplane, (0,1,0), tag='in-plane along YOU y axis')
        self.calc_ub()

    def get_diffractometer_angle_limits(self):
        diff_angle_adjs = {
            "nu": self.diffractometer.nu,
            "mu": self.diffractometer.mu,
            "delta": self.diffractometer.delta,
            "eta": self.diffractometer.eta,
            "chi": self.diffractometer.chi,
            "phi": self.diffractometer.phi
        }
        limits = {key: val.get_limits() for key, val in diff_angle_adjs.items()}
        return limits

    def get_diffractometer_angles(self):
        nu = self.diffractometer.nu.get_current_value()
        mu = self.diffractometer.mu.get_current_value()
        delta = self.diffractometer.delta.get_current_value()
        eta = self.diffractometer.eta.get_current_value()
        chi = self.diffractometer.chi.get_current_value()
        phi = self.diffractometer.phi.get_current_value()
        return mu, delta, nu, eta, chi, phi

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
        uc = self.unit_cell()
        self.ubcalc.set_lattice(uc.pop("name"), **uc)
        for ori in self.orientations():
            self.ubcalc.add_orientation(ori.pop("hkl"), ori.pop("xyz"), **ori)
        for refl in self.reflections():
            self.ubcalc.add_reflection(refl.pop("hkl"), refl.pop("xyz"), **refl)
        self._u_ub_to_dc()

    def add_reflection(
        self,
        hkl,
        position,
        energy,
        tag=None,
    ):
        """Add a reference reflection.

        Adds a reflection position in degrees and in the systems internal
        representation.

        Parameters
        ----------
        hkl : Tuple[float, float, float]
            hkl index of the reflection
        position: Position
            list of diffractometer angles in internal representation in degrees
        energy : float
            energy of the x-ray beam
        tag : Optional[str], default = None
            identifying tag for the reflection
        """

        self.ubcalc.add_reflection(hkl, position, energy, tag=tag)
        self.reflections.set_target_value(
            self.reflections()
            + [{"hkl": hkl, "position": position, "energy": energy, "tag": tag}]
        )

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

    def calc_ub(self, idx1=None, idx2=None):
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

    def fit_ub(self, refine_lattice=False, refine_umatrix=False):
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
        self.ubcalc.fit_ub( refine_lattice, refine_umatrix)
        self._u_ub_from_dc()
        if refine_lattice:
            self._lat_from_dc()

    def calc_angles(self, h=None,k=None,l=None, energy=None):
        """calculate diffractometer angles for a given h,k,l and energy in eV. 
        If any of the h, k, l are not given, their current value is used instead. 
        If the energy is not given, the monochromator energy is used
        Shows all solutions neglecting diffractometer limits"""
        setvals = [h,k,l]
        curvals =  [self.h, self.k, self.l]
        h,k,l = [curval() if setval == None else setval for setval, curval in zip(setvals, curvals)]
        self.recalculate()
        cons = Constraints(self.constraints())
        hklcalc = HklCalculation(self.ubcalc, cons)
        if energy is None:
            energy = 8000
        lam = self.en2lam(energy)
        result = hklcalc.get_position(h,k,l,lam)
        result = pd.concat([pd.DataFrame.from_dict({**tres[0].asdict,**tres[1]},orient='index', columns=[f'sol. {n}']) for n,tres in enumerate(result)],axis=1)
        return result.T

    def calc_angles_unique(self, h=None,k=None,l=None, energy=None):
        """calculate unique solution of diffractometer angles for a given h,k,l and energy in eV. 
        If any of the h, k, l are not given, their current value is used instead. 
        If the energy is not given, the monochromator energy is used."""
        result = self.calc_angles(h,k,l,energy)
        limits = self.get_diffractometer_angle_limits()
        s=''
        for ang, limit in limits.items():
            if len(s)>0:
                s=s+' and '
            s = s+f'{limit[0]} < {ang} < {limit[1]}'
        result_f = result.query(s)
        if result_f.shape[0] > 1:
            print(f"There is not a unique angular configuration to reach ({h},{k},{l}), please change the diffractometer motor soft limits to allow only one of the solutions shown below:")
            print(result_f)
            return None
        elif result_f.shape[0] ==0:
            print("There is no angular configuration, which is allowed for the current diffractometer motor soft limits. please check the diffractometer limits.")
            print(limits)
            print("Solutions")
            print(result)
            return None
        return result_f.array


    def calc_hkl(self, mu=None, delta=None, nu=None, eta=None, chi=None, phi=None, energy=None):
        """calculate (h,k,l) for given diffractometer angles and energy in eV. 
        If any of the diffractometer angles are not given, their current value is used instead. 
        If the energy is not given, the monochromator energy is used"""
        setvals = [mu, delta, nu, eta, chi, phi]
        curvals = self.get_diffractometer_angles()
        angs = [curval if setval == None else setval for setval, curval in zip(setvals, curvals)]
        pos = Position(*angs)
        self.recalculate()
        if energy is None:
            energy = 8000
        lam = self.en2lam(energy)
        cons = Constraints(self.constraints())
        hklcalc = HklCalculation(self.ubcalc, cons)
        try:
            hkl = hklcalc.get_hkl(pos=pos, wavelength=lam)
        except Exception as e:
            print(str(e))
            return
        return hkl

    def _u_ub_from_dc(self):
        self.ub_matrix(self.ubcalc.UB.tolist())
        self.u_matrix(self.ubcalc.U.tolist())

    def _u_ub_to_dc(self):
        if len(self.ub_matrix())>0:
            self.ubcalc.set_ub(self.ub_matrix())
            self.ubcalc.set_u(self.u_matrix())

    def _lat_from_dc(self):
        self.set_unit_cell(*self.ubcalc.crystal.get_lattice()[1:])

    def en2lam(self, en):
        """input: energy in eV, returns wavelength in A"""
        return 12398.419843320025/en

    def lam2en(self, lam):
        """input: wavelength in A, returns energy in eV"""
        return 12398.419843320025/lam

    pass
    # def __init__(sel):
