from diffcalc.hkl.calc import HklCalculation
from diffcalc.hkl.constraints import Constraints
from diffcalc.hkl.geometry import Position
from diffcalc.ub import calc as dccalc

# from diffcalc.ub import calc calc import UBCalculation, Crystal
from eco.elements import Assembly
from eco.elements.adjustable import AdjustableMemory


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

    def set_unit_cell(
        self, name_crystal, a=None, b=None, c=None, alpha=None, beta=None, gamma=None
    ):
        self.unit_cell(
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

    def recalculate(self):
        self.ubcalc = dccalc.UBCalculation("you")
        uc = self.unit_cell()
        self.ubcalc.set_lattice(uc.pop("name"), **uc)
        for ori in self.orientations():
            self.ubcalc.add_orientation(ori.pop("hkl"), ori.pop("xyz"), **ori)

    def add_reflection(
        hkl: Tuple[float, float, float],
        position: Position,
        energy: float,
        tag: Optional[str] = None,
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
        self.reflections(
            self.reflections()
            + {"hkl": hkl, "position": position, "energy": energy, "tag": tag}
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
        self.ubcalc.add_orientation(hkl, xyz, position=None, tag=None)
        self.orientations(
            self.orientations()
            + {"hkl": hkl, "xyz": xyz, "position": position, "tag": tag}
        )
        self.recalculate()

    def calc_ub(self, *args, **kwargs):
        self.ubcalc.calc_ub(*args, **kwargs)

    def fit_ub(self, *args, **kwargs):
        self.ubcalc.fit_ub(*args, **kwargs)

    pass
    # def __init__(sel):
