"""RecSpace: the hklforge-based successor of the diffcalc objects.

``gps.recspace``, ``xrd.recspace``, ``rob.recspace`` and the namespace-level
``recspace`` run in parallel to the existing ``diffcalc`` objects
(``eco.utilities.recspace.Crystals``) and have the same interface:
``create_crystal``/``activate_crystal``/..., and per crystal ``h``, ``k``,
``l``, ``hkl`` adjustables, ``add_reflection``, ``add_orientation``,
``calc_ub``, ``refine_ub``, ``fit_ub``, ``calc_angles``,
``calc_angles_unique``, ``calc_hkl``, ``widget_geom`` ...

What is new over diffcalc:

- **Two reflections as constraint**: ``constraints.h2 = [h, k, l]`` keeps a
  second reflection in Bragg condition while moving ``hkl``;
  ``constraints.h2_detector`` puts the detector on the first reflection
  ("h1") or between both ("between"). ``calc_angles_two(hkl1, hkl2)`` does
  the same without touching the stored constraints, and ``spots()`` tells
  how far apart the two spots are and the largest detector distance that
  keeps both on the detector.
- Every branch, exactly (hklforge), also for modes diffcalc lacks
  (naz with two sample circles) or gets wrong (bisect/omega).
- Reference vector and surface normal can be set (``set_reference``,
  ``set_surface``); diffcalc's wrapper always used its defaults.
- Solutions whose angles are only out of the motor limits by a 360 degree
  representation are mapped into them instead of being rejected.
- ``widget_reflections()``: an interactive solution browser with the 3D
  view, inline in Jupyter and as a page in the browser from a terminal.

Crystals live in their own files (``CONFIG_DIR``), so RecSpace never
changes what ``diffcalc`` uses; ``import_from_diffcalc()`` copies diffcalc's
crystals over. The calculations themselves are in
``eco.utilities.recspace_calc``.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from eco.elements.adj_obj import AdjustableObject
from eco.elements.adjustable import AdjustableFS, AdjustableVirtual, DummyAdjustable
from eco.elements.assembly import Assembly, FailedComponent
from eco.elements.detector import DetectorVirtual
from eco.utilities import recspace_calc as rc

logger = logging.getLogger(__name__)

CONFIG_DIR = "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/recspace"
DIFFCALC_CRYSTAL_DIR = "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/crystals"
YOU_GEOMETRY_PNG = f"{DIFFCALC_CRYSTAL_DIR}/you_diffractometer.png"
ENERGY_PV = "SAROP21-ARAMIS:ENERGY"

# diffcalc's constraint keys (same order as eco's diffcalc objects), plus the
# two-reflection mode
DEFAULT_CONSTRAINTS = {
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
    "h2": None,
    "h2_detector": "h1",
}
# per-crystal files; the diffcalc objects use the same names minus "vectors"
CRYSTAL_FILES = ("unit_cell", "u_matrix", "ub_matrix", "orientations", "reflections", "vectors")
DIFFCALC_FILES = ("unit_cell", "u_matrix", "ub_matrix", "orientations", "reflections")

_NAME_SPECIALS = "'\". /()[]{}|-*+"


def _config_flag(cfg, key, default=False):
    """A diffractometer configuration flag, whether the configuration is an
    AdjustableObject (``cfg.kappa()``), a dict or a plain list of names (the
    robot sets ``["robot", <goniometer>]``)."""
    if cfg is None:
        return default
    if isinstance(cfg, (list, tuple, set)):
        return key in cfg
    if isinstance(cfg, dict):
        return bool(cfg.get(key, default))
    flag = getattr(cfg, key, None)
    if flag is None:
        return default
    try:
        return bool(flag() if callable(flag) else flag)
    except Exception:
        return default


class _DummyYou(Assembly):
    """Six dummy circles, for a RecSpace without a real diffractometer."""

    def __init__(self, name=None):
        super().__init__(name=name)
        self.configuration = {"thc": False, "robot": False, "kappa": False}
        for adj in ["gamma", "mu", "delta", "eta", "chi", "phi"]:
            self._append(
                DummyAdjustable,
                name=adj,
                is_setting=True,
                is_display=True,
                limits=[-180, 180],
            )


_dummy = None


def diffractometer_dummy():
    global _dummy
    if _dummy is None:
        _dummy = _DummyYou(name="dummy")
    return _dummy


def _read_fs_value(path):
    import json

    with open(path) as f:
        return json.load(f)["value"]


class RecSpace(Assembly):
    """Crystals for one diffractometer, solved with hklforge. Same interface
    as the ``diffcalc`` objects; see the module docstring for what is new."""

    def __init__(self, diffractometer_you=None, name=None, config_dir=None, energy_source=None):
        super().__init__(name=name)
        self.diffractometer = diffractometer_you or diffractometer_dummy()
        self.config_dir = Path(config_dir or CONFIG_DIR)
        self._energy_source = energy_source
        self._append(
            AdjustableFS,
            self.config_dir / f"{name}_list.json",
            name="_crystal_list",
            default_value={},
            is_setting=True,
        )
        self._append(
            AdjustableFS,
            self.config_dir / f"{name}_constraints.json",
            name="_constraints",
            default_value=DEFAULT_CONSTRAINTS,
            is_setting=True,
            is_display=False,
        )
        cons = self._constraints()
        missing = {k: v for k, v in DEFAULT_CONSTRAINTS.items() if k not in cons}
        if missing:
            self._constraints.set_target_value({**cons, **missing}).wait()
        self._append(
            AdjustableObject,
            self._constraints,
            name="constraints",
            is_setting=False,
            is_display="recursive",
        )
        for key, meta in self._crystal_list().items():
            if self.diffractometer.name in meta:
                self._append_crystal(key, is_setting=False, is_display=True)

    @property
    def crystals(self):
        """Names of the crystals active on this diffractometer."""
        return [k for k, meta in self._crystal_list().items() if self.diffractometer.name in meta]

    def _append_crystal(self, name, is_setting=True, is_display=False):
        self._append(
            RecSpaceCrystal,
            diffractometer_you=self.diffractometer,
            constraints=self.constraints,
            config_dir=self.config_dir,
            energy_source=self._energy_source,
            name=name,
            is_setting=is_setting,
            is_display=is_display,
        )
        return self.__dict__[name]

    def _register(self, name):
        crystals = self._crystal_list()
        meta = crystals.get(name, [str(datetime.now())])
        if self.diffractometer.name not in meta:
            meta = meta + [self.diffractometer.name]
        crystals[name] = meta
        self._crystal_list.set_target_value(crystals).wait()

    def _ask_name(self):
        while True:
            name = input("Please choose a name for your crystal (no spaces or other special characters):")
            bad = [s for s in _NAME_SPECIALS if s in name]
            if name == "":
                print("Name cannot be empty.")
            elif bad:
                print(f"Special character(s) {bad} in name not allowed.")
            elif name in self._crystal_list():
                print(f"Name {name} already exists.")
            elif not name.isidentifier():
                print(f"{name} is not a valid python name.")
            else:
                return name

    def _choose(self, names, what):
        if not names:
            print(f"No crystal to {what}.")
            return None
        msg = f"Select the crystal to {what}:\nq) quit\n"
        msg += "".join(f"{i:2}) {n:15}\n" for i, n in enumerate(names)) + "Your choice: "
        while True:
            idx = input(msg)
            if idx == "q":
                return None
            try:
                return names[int(idx)]
            except (ValueError, IndexError):
                continue

    def create_crystal(self, name=None, unit_cell=None):
        """Create a new crystal. Without ``unit_cell`` the UB matrix is set up
        interactively (``new_ub``), as with diffcalc; with
        ``unit_cell=dict(a=.., b=.., c=.., alpha=.., beta=.., gamma=..)``
        only the lattice is set and orientations/reflections are added later."""
        if name is None:
            name = self._ask_name()
        elif name in self._crystal_list():
            raise ValueError(f"Crystal {name} already exists, use activate_crystal('{name}').")
        crystal = self._append_crystal(name)
        self._register(name)
        if unit_cell is None:
            crystal.new_ub()
        else:
            crystal.set_unit_cell(unit_cell.get("name", name), **{k: v for k, v in unit_cell.items() if k != "name"})
        return crystal

    def delete_crystal(self, name=None):
        """Delete crystal with a given name, deletes also the files."""
        names = list(self._crystal_list().keys())
        if name is None:
            name = self._choose(names, "delete")
            if name is None:
                return
        elif name not in names:
            print(f"Crystal {name} has not been defined.")
            return
        sure = input(f"are you sure you want to permanently remove the crystal {name} and its UB matrix and memories (y/n)? ")
        if sure != "y":
            print(f"Aborted deletion of crystal {name}.")
            return
        if name in self.crystals:
            self.deactivate_crystal(name=name)
        crystals = self._crystal_list()
        crystals.pop(name)
        self._crystal_list.set_target_value(crystals).wait()
        for a in CRYSTAL_FILES:
            path = self.config_dir / f"{name}_{a}.json"
            if path.exists():
                os.remove(path)
        print(f"Deleted crystal {name}.")

    def activate_crystal(self, name=None):
        crystals = self._crystal_list()
        inactive = [k for k in crystals if k not in self.crystals]
        if name is None:
            name = self._choose(inactive, "activate")
            if name is None:
                return
        elif name in self.crystals:
            print(f"Crystal {name} is already active. Use {self.name}.{name} to start calculations.")
            return
        elif name not in inactive:
            print(f"Crystal {name} has not been defined yet. Use {self.name}.create_crystal() "
                  f"or {self.name}.import_from_diffcalc() to create one.")
            return
        self._append_crystal(name)
        self._register(name)
        print(f"Activated crystal: {name}")

    def deactivate_crystal(self, name=None):
        if name is None:
            name = self._choose(self.crystals, "deactivate")
            if name is None:
                return
        elif name not in self.crystals:
            print(f"Crystal {name} is not active.")
            return
        crystals = self._crystal_list()
        crystals[name] = [m for m in crystals[name] if m != self.diffractometer.name]
        self._crystal_list.set_target_value(crystals).wait()
        removed = self.__dict__.pop(name)
        self.alias.pop_object(removed.alias)
        print(f"Deactivated crystal: {name}")

    # --- taking over diffcalc's crystals ---------------------------------------

    def diffcalc_crystals(self, source_dir=None, list_name="diffcalc"):
        """{name: meta} of the crystals the diffcalc objects know."""
        path = Path(source_dir or DIFFCALC_CRYSTAL_DIR) / f"{list_name}_list.json"
        return _read_fs_value(path) if path.exists() else {}

    def import_from_diffcalc(self, name=None, new_name=None, source_dir=None, activate=True, overwrite=False):
        """Copy a crystal of the diffcalc objects (unit cell, U, UB,
        orientations, reflections) into RecSpace. ``name=None`` imports all of
        them. The diffcalc files are only read. Returns the imported names."""
        src = Path(source_dir or DIFFCALC_CRYSTAL_DIR)
        if name is None:
            names = list(self.diffcalc_crystals(src)) or sorted(
                p.name[: -len("_unit_cell.json")] for p in src.glob("*_unit_cell.json"))
            done = []
            for n in names:
                try:
                    done += self.import_from_diffcalc(n, source_dir=src, activate=activate, overwrite=overwrite)
                except Exception as e:
                    print(f"{n}: not imported ({e})")
            return done
        new_name = new_name or name
        if new_name in self._crystal_list() and not overwrite:
            raise ValueError(f"RecSpace crystal {new_name} exists already (overwrite=True replaces it).")
        values = {}
        for a in DIFFCALC_FILES:
            path = src / f"{name}_{a}.json"
            if not path.exists():
                raise FileNotFoundError(path)
            values[a] = _read_fs_value(path)
        values["vectors"] = {"reference": dict(rc.DEFAULT_REFERENCE), "surface": dict(rc.DEFAULT_SURFACE)}
        for a, v in values.items():
            AdjustableFS(self.config_dir / f"{new_name}_{a}.json", default_value=v).set_target_value(v).wait()
        crystals = self._crystal_list()
        crystals[new_name] = [str(datetime.now())]
        self._crystal_list.set_target_value(crystals).wait()
        if new_name in self.__dict__:
            removed = self.__dict__.pop(new_name)
            self.alias.pop_object(removed.alias)
        if activate:
            self._append_crystal(new_name)
            self._register(new_name)
        print(f"Imported diffcalc crystal {name} as {self.name}.{new_name}")
        return [new_name]

    def import_constraints_from_diffcalc(self, list_name="diffcalc", source_dir=None):
        """Take over the diffcalc object's current constraints."""
        path = Path(source_dir or DIFFCALC_CRYSTAL_DIR) / f"{list_name}_constraints.json"
        cons = {**DEFAULT_CONSTRAINTS, **_read_fs_value(path)}
        self._constraints.set_target_value(cons).wait()
        return cons


class RecSpaceCrystal(Assembly):
    """One crystal on one diffractometer: UB matrix, reference data and the
    h, k, l adjustables. Interface as ``eco.utilities.recspace.DiffGeometryYou``."""

    def __init__(self, diffractometer_you=None, constraints=None, name=None, config_dir=None, energy_source=None):
        super().__init__(name=name)
        self.config_dir = Path(config_dir or CONFIG_DIR)
        self._energy_source = energy_source
        files = {
            "unit_cell": ({"name": "", "a": 1, "b": 1, "c": 1, "alpha": 90, "beta": 90, "gamma": 90}, True),
            "u_matrix": ([], False),
            "ub_matrix": ([], False),
            "orientations": ([], True),
            "reflections": ([], True),
            "vectors": ({"reference": dict(rc.DEFAULT_REFERENCE), "surface": dict(rc.DEFAULT_SURFACE)}, False),
        }
        for fname, (default, display) in files.items():
            self._append(
                AdjustableFS,
                self.config_dir / f"{name}_{fname}.json",
                name=fname,
                default_value=default,
                is_setting=True,
                is_display=display,
            )
        self.diffractometer = diffractometer_you
        self.constraints = constraints
        cfg = getattr(self.diffractometer, "configuration", None)
        self._kappa = _config_flag(cfg, "kappa")
        adjs = ["gamma", "mu", "delta", "eta_kap", "kappa", "phi_kap"] if self._kappa else [
            "gamma", "mu", "delta", "eta", "chi", "phi"]

        adjustables_dict = dict(self.diffractometer.__dict__)

        ### use robot motors if robot is in config (and the robot is wired up)
        if _config_flag(cfg, "robot") and hasattr(self.diffractometer, "gamma_robot"):
            self._append(
                AdjustableFS,
                self.config_dir / "move_robot.json",
                name="move_robot",
                default_value=True,
                is_setting=False,
            )

            def rob_set(a):
                return [a] if self.move_robot() else None

            robot = {}
            for ang in ("gamma", "delta"):
                real = getattr(self.diffractometer, f"{ang}_robot")
                v = AdjustableVirtual([real], lambda a: a, rob_set, name=f"{ang}_robot", check_limits=True)
                v.get_limits = real.get_limits
                robot[ang] = v
            adjustables_dict.update(robot)

        ### phi constraint as thc.phi_wobble, unless diffcalc made one already
        if _config_flag(cfg, "thc") and hasattr(self.diffractometer, "thc"):
            if not hasattr(self.diffractometer.thc, "phi_wobble"):
                self.diffractometer.thc._append(
                    AdjustableVirtual, [self.constraints.phi], lambda a: a, lambda a: [a], name="phi_wobble"
                )

        self._diff_adjs = {}
        self._diff_adjs_constrained = {}
        for adj in adjs:
            real = adjustables_dict.get(adj)
            is_failed = isinstance(real, FailedComponent)
            if real is not None and not is_failed and real.__class__ is not DetectorVirtual:
                self._diff_adjs[adj] = real
                continue
            # absent (virtual in this geometry), failed, or a virtual
            # placeholder another calculator made: the constraint value is
            # used as the angle
            self._diff_adjs_constrained[adj] = self.constraints.__dict__[adj]
            if is_failed:
                logger.warning("diffractometer angle '%s' failed to initialize; recspace uses its constraint.", adj)
            elif real is None:
                self.diffractometer._append(
                    DetectorVirtual,
                    [self.constraints.__dict__[adj]],
                    lambda a: a,
                    name=adj,
                    is_setting=False,
                    is_display=True,
                )

        def make_get(i):
            return lambda *args, **kwargs: self.calc_hkl()[i]

        def make_set(i):
            def set_(val):
                hkl = [None, None, None]
                hkl[i] = val
                return self._calc_angles_unique_diffractometer(hkl)

            return set_

        targets = list(self._diff_adjs.values())
        for i, n in enumerate("hkl"):
            self._append(AdjustableVirtual, targets, make_get(i), make_set(i), name=n, change_simultaneously=True)
        self._append(
            AdjustableVirtual,
            targets,
            lambda *args, **kwargs: self.calc_hkl(),
            self._calc_angles_unique_diffractometer,
            name="hkl",
        )

    # --- angle bookkeeping ------------------------------------------------------

    def convert_from_you(self, **kwargs):
        if self._kappa:
            eta_kap, kappa, phi_kap = self.diffractometer.calc_you2kappa(kwargs["eta"], kwargs["chi"], kwargs["phi"])
            kwargs.update({"eta_kap": eta_kap, "kappa": kappa, "phi_kap": phi_kap})
        return {key: kwargs[key] for key in self._diff_adjs.keys()}

    def convert_to_you(self, gamma=None, mu=None, delta=None, eta=None, chi=None, phi=None,
                       eta_kap=None, kappa=None, phi_kap=None):
        if self._kappa:
            eta, chi, phi = self.diffractometer.calc_kappa2you(eta_kap, kappa, phi_kap)
        return gamma, mu, delta, eta, chi, phi

    def get_diffractometer_angles(self):
        """(mu, delta, gamma, eta, chi, phi) now, diffcalc's Position order."""
        gamma, mu, delta, eta, chi, phi = self.convert_to_you(
            **{key: adj() for key, adj in {**self._diff_adjs, **self._diff_adjs_constrained}.items()}
        )
        return mu, delta, gamma, eta, chi, phi

    def _angles(self, mu=None, delta=None, gamma=None, eta=None, chi=None, phi=None):
        """{circle: degrees}, current values for the ones not given."""
        given = [mu, delta, gamma, eta, chi, phi]
        if all(v is not None for v in given):
            return rc.position_to_angles(given)
        cur = self.get_diffractometer_angles()
        return rc.position_to_angles([c if v is None else v for v, c in zip(given, cur)])

    def _calc_angles_unique_diffractometer(self, hkl):
        _, targets = self._unique_solution(*hkl)
        return [targets[tk] for tk in self._diff_adjs.keys()]

    def fit_target_into_limits(self, **kwargs):
        """The motor targets of a You-angle solution, each shifted by +-360
        if that brings it inside its limits; None if any stays outside."""
        out = {}
        for axname, value in self.convert_from_you(**kwargs).items():
            adj = self._diff_adjs[axname]
            if not hasattr(adj, "get_limits"):
                raise Exception(f"Failed to get limits of adjustable {adj.name}")
            v = rc.wrap_into_limits(value, adj.get_limits())
            if v is None:
                return None
            out[axname] = v
        return out

    def check_target_value_within_limits(self, **kwargs):
        return self.fit_target_into_limits(**kwargs) is not None

    def _limits_str(self):
        return "".join(f"{n}: {a.get_limits()}\n" for n, a in self._diff_adjs.items() if hasattr(a, "get_limits"))

    # --- crystal and UB ---------------------------------------------------------

    def _ub(self):
        ub = self.ub_matrix()
        if not len(ub):
            raise ValueError(f"No UB matrix for {self.name} yet: add orientations/reflections and run calc_ub().")
        return np.asarray(ub, float)

    def _set_u(self, U, unit_cell=None):
        U = np.asarray(U, float)
        uc = unit_cell or self.unit_cell()
        self.u_matrix.set_target_value(U.tolist()).wait()
        self.ub_matrix.set_target_value((U @ rc.lattice_b(uc)).tolist()).wait()

    def new_ub(self):
        """Set lattice, surface normal and in-plane direction interactively and calculate UB."""
        crystal_name = input(f"Name of the crystal: ({self.name})") or str(self.name)
        a = float(input("Lattice constant a (1): ") or 1)
        b = float(input(f"Lattice constant b ({a}): ") or a)
        c = float(input(f"Lattice constant c ({a}): ") or a)
        alpha = float(input("Angle alpha (90): ") or 90)
        beta = float(input(f"Angle beta ({alpha}): ") or alpha)
        gamma = float(input(f"Angle gamma ({alpha}): ") or alpha)

        def ask_vec(prompt, default):
            while True:
                text = input(prompt)
                if not text.strip():
                    return default
                try:
                    v = [float(x) for x in text.split(",")]
                except ValueError:
                    continue
                if len(v) == 3:
                    return v

        normal = ask_vec("(h,k,l) surface normal (along YOU x-axis, vertical), e.g. 0,0,1: ", [0, 0, 1])
        inplane = ask_vec("(h,k,l) in-plane orientation along beam (YOU y-axis), e.g. 1,0,0: ", [1, 0, 0])
        self.set_unit_cell(crystal_name, a, b, c, alpha, beta, gamma)
        self.orientations.set_target_value([]).wait()
        self.add_orientation(normal, (0, 0, 1), tag="surface normal")
        self.add_orientation(inplane, (0, 1, 0), tag="in-plane along x-ray beam direction")
        self.set_surface(hkl=normal)
        self.calc_ub()
        print(self.summary())
        print("UB was calculated - next please set the constraints (.constraints) and the limits of the diffractometer motors")

    def set_unit_cell(self, name_crystal, a=None, b=None, c=None, alpha=None, beta=None, gamma=None):
        """Set the lattice. An existing U is kept and UB recalculated with the
        new lattice (diffcalc kept the old UB until the next calc_ub)."""
        uc = {"name": name_crystal, "a": a, "b": b, "c": c, "alpha": alpha, "beta": beta, "gamma": gamma}
        self.unit_cell.set_target_value(uc).wait()
        if len(self.u_matrix()):
            self._set_u(self.u_matrix(), uc)

    def set_reference(self, hkl=None, phi=None):
        """Reference vector behind alpha, beta, psi, naz and tau: Miller
        indices (``hkl``, follows the crystal) or a phi-frame direction
        (``phi``). diffcalc's default is hkl (1, 0, 0)."""
        self._set_vector("reference", hkl, phi)

    def set_surface(self, hkl=None, phi=None):
        """Surface normal behind betain, betaout and bin_eq_bout, as
        ``set_reference``. diffcalc's default is phi (0, 0, 1)."""
        self._set_vector("surface", hkl, phi)

    def _set_vector(self, which, hkl, phi):
        if (hkl is None) == (phi is None):
            raise ValueError("give hkl or phi")
        v = self.vectors()
        v[which] = {"hkl": list(map(float, hkl))} if hkl is not None else {"phi": list(map(float, phi))}
        self.vectors.set_target_value(v).wait()

    @property
    def _reference(self):
        return self.vectors().get("reference", rc.DEFAULT_REFERENCE)

    @property
    def _surface(self):
        return self.vectors().get("surface", rc.DEFAULT_SURFACE)

    def add_reflection(self, h, k, l, mu=None, delta=None, gamma=None, eta=None, chi=None, phi=None,
                       energy=None, tag=None):
        """Add a reference reflection, add_reflection(2, 2, 0).

        Angles not given are the current diffractometer angles, the energy
        defaults to the mono/machine energy (eV).
        """
        if any(hasattr(i, "__len__") for i in (h, k, l)):
            print("Please enter hkl as touple: add_reflection(h, k, l)")
            return
        angs = rc.angles_to_position(self._angles(mu, delta, gamma, eta, chi, phi))
        if energy is None:
            energy = self.get_energy()
        self.reflections.set_target_value(
            self.reflections() + [{"hkl": [h, k, l], "position": angs, "energy": energy, "tag": tag}]
        ).wait()

    def del_reflection(self, idx):
        refls = self.reflections()
        removed = refls.pop(idx)
        self.reflections.set_target_value(refls).wait()
        print(f"Removed reflection {removed}")

    def add_orientation(self, hkl, xyz, position=None, tag=None):
        """Add a reference orientation: Miller indices ``hkl`` along lab
        direction ``xyz`` (You frame: x vertical, y beam, z transverse) at
        ``position`` (list in Position order; None means all angles 0)."""
        if not hasattr(hkl, "__len__"):
            print("Please enter hkl as touple or list: add_orientation([h, k, l])")
            return
        if not hasattr(xyz, "__len__"):
            print("Please enter xyz as touple or list: add_orientation([x, y, z])")
            return
        self.orientations.set_target_value(
            self.orientations() + [{"hkl": list(hkl), "xyz": list(xyz), "position": position, "tag": tag}]
        ).wait()

    def del_orientation(self, idx):
        oris = self.orientations()
        removed = oris.pop(idx)
        self.orientations.set_target_value(oris).wait()
        print(f"Removed orientation {removed}")

    def calc_ub(self, idx1=0, idx2=1):
        """Calculate U (and UB) from two references.

        As diffcalc: an index refers to a reflection, or to the orientation
        with that index if there are not that many reflections; a string is a
        tag. ``idx2=None`` uses reflection ``idx1`` alone (U then undetermined
        about its Q).
        """
        U, used = rc.calc_u(self.unit_cell(), self.reflections(), self.orientations(), idx1, idx2)
        self._set_u(U)
        print(f"UB calculated from {used}.")

    def refine_ub(self, hkl, mu=None, delta=None, gamma=None, eta=None, chi=None, phi=None, energy=None,
                  refine_lattice=False, refine_umatrix=False):
        """Refine with one reflection: the lattice constants along non-zero
        indices are scaled to the measured d (``refine_lattice``), U is turned
        to put hkl onto the measured Q (``refine_umatrix``, accurate up to a
        rotation about Q). Angles default to the current ones. Nothing is
        applied unless asked for; what would change is printed either way."""
        angles = self._angles(mu, delta, gamma, eta, chi, phi)
        if energy is None:
            energy = self.get_energy()
        uc = self.unit_cell()
        U = np.asarray(self.u_matrix(), float)
        new_cell, M, scale, miscut = rc.refine_from_reflection(uc, U, self._ub(), hkl, angles, energy)
        print(f"d measured / d calculated = {scale:.6f}; misorientation {miscut:.4f} deg")
        if new_cell is not None and refine_lattice:
            self.set_unit_cell(**{"name_crystal": uc["name"], **{k: new_cell[k] for k in ("a", "b", "c", "alpha", "beta", "gamma")}})
            print("Lattice refined:", {k: round(new_cell[k], 6) for k in ("a", "b", "c")})
        if M is not None and refine_umatrix:
            self._set_u(M @ U)
            print("U matrix refined.")
        if not (refine_lattice or refine_umatrix):
            print("Nothing applied; use refine_lattice=True and/or refine_umatrix=True.")

    def fit_ub(self, indices=None, refine_lattice=False, refine_umatrix=True):
        """Least-squares UB from 3 or more reflections (all by default)."""
        U, lat, rms = rc.fit_ub(self.reflections(), indices)
        uc = {**self.unit_cell(), **lat} if refine_lattice else self.unit_cell()
        print(U)
        for k in ("a", "b", "c", "alpha", "beta", "gamma"):
            print(f"{k:8}: {lat[k]}")
        print(f"rms residual of Q: {rms:.3g} 1/A")
        if refine_lattice:
            self.unit_cell.set_target_value(uc).wait()
            print("\nFitted lattice applied")
        else:
            print("\nFitted lattice not applied. To apply it, set refine_lattice=True")
        if refine_umatrix:
            self._set_u(U, uc)
            print("Fitted U matrix applied")
        else:
            print("Fitted U matrix not applied. To apply it, set refine_umatrix=True")
            if refine_lattice:
                self._set_u(self.u_matrix(), uc)

    def summary(self):
        """Lattice, U/UB, reference vectors and the stored reference data."""
        uc = self.unit_cell()
        lines = [f"crystal {self.name} ({uc.get('name', '')})",
                 "  lattice: " + ", ".join(f"{k}={uc[k]:g}" for k in ("a", "b", "c", "alpha", "beta", "gamma"))]
        if len(self.ub_matrix()):
            ub = self._ub()
            lines.append("  UB:\n" + "\n".join("    " + " ".join(f"{x: 10.5f}" for x in r) for r in ub))
            for which, spec in (("reference", self._reference), ("surface", self._surface)):
                lines.append(f"  {which}: {spec}  -> phi {np.round(rc.vector_phi(spec, ub), 5).tolist()}")
        else:
            lines.append("  UB: not calculated")
        for i, o in enumerate(self.orientations()):
            lines.append(f"  orientation {i}: hkl {o['hkl']} along xyz {o['xyz']}  {o.get('tag') or ''}")
        for i, r in enumerate(self.reflections()):
            pos = ", ".join(f"{n}={v:.3f}" for n, v in zip(rc.POSITION_ORDER, r["position"]))
            lines.append(f"  reflection {i}: hkl {r['hkl']} at {pos}, {r['energy']:.1f} eV  {r.get('tag') or ''}")
        return "\n".join(lines)

    # --- angles <-> hkl ---------------------------------------------------------

    def _constraint_dict(self, constraints_update=None, h2=None, detector=None):
        cons = dict(self.constraints._base_dict())
        cons.update(constraints_update or {})
        if h2 is not None:
            cons["h2"] = list(h2)
        if detector is not None:
            cons["h2_detector"] = detector
        return cons

    def calc_angles(self, h=None, k=None, l=None, energy=None, constraints_update={}, h2=None, detector=None):
        """All diffractometer solutions for (h, k, l) at ``energy`` (eV),
        ignoring motor limits, as a DataFrame (one row per solution).

        h, k, l not given are the current values, energy the mono/machine
        energy. ``constraints_update`` overrides stored constraints for this
        call only; ``h2``/``detector`` likewise set the two-reflection mode.
        """
        if None in (h, k, l):
            cur = self.calc_hkl()
            h, k, l = [c if v is None else v for v, c in zip((h, k, l), cur)]
        if energy is None:
            energy = self.get_energy()
        cons = self._constraint_dict(constraints_update, h2, detector)
        sols = rc.solve(self._ub(), energy, (h, k, l), cons, self._reference, self._surface)
        cols = list(rc.POSITION_ORDER) + [v for v in rc.VIRTUAL_ORDER if any(v in s for s in sols)]
        return pd.DataFrame([[s.get(c, np.nan) for c in cols] for s in sols], columns=cols,
                            index=[f"sol. {n}" for n in range(len(sols))])

    def calc_angles_two(self, hkl1, hkl2, detector="h1", energy=None, constraints_update={}):
        """Solutions with both reflections in Bragg condition.

        Needs two sample circles left free by the constraints (plus the
        detector circles), e.g. constraints_update=dict(mu=0, eta=0) or the
        stored ones. ``detector``: "h1" puts the detector on ``hkl1``,
        "between" on the bisector of both outgoing beams. Columns
        ``separation`` (angle between the two spots) and ``tdet_max`` (mm,
        largest distance for a 77 mm detector) are added.
        """
        if energy is None:
            energy = self.get_energy()
        df = self.calc_angles(*hkl1, energy=energy, constraints_update=constraints_update, h2=hkl2, detector=detector)
        sp = [self.spots([hkl1, hkl2], energy=energy, **{n: row[n] for n in rc.POSITION_ORDER}) for _, row in df.iterrows()]
        df["separation"] = [s["separation"] for s in sp]
        df["tdet_max"] = [s["tdet_max"] for s in sp]
        return df

    def calc_angles_unique(self, h=None, k=None, l=None, energy=None):
        """The one solution for (h, k, l) inside the motor limits, as a dict.
        Raises if there are none or several (adjust the soft limits)."""
        return self._unique_solution(h, k, l, energy)[0]

    def _unique_solution(self, h=None, k=None, l=None, energy=None):
        """(solution dict, motor targets inside the limits)."""
        df = self.calc_angles(h, k, l, energy)
        fitted = [self.fit_target_into_limits(**df.loc[idx].to_dict()) for idx in df.index]
        idx_in = [idx for idx, f in zip(df.index, fitted) if f is not None]
        if len(idx_in) != 1:
            if idx_in:
                print(f"There is not a unique angular configuration to reach ({h},{k},{l}), please change "
                      "the diffractometer motor soft limits to allow only one of the solutions shown below.")
            else:
                print("There is no angular configuration, which is allowed for the current diffractometer "
                      "motor soft limits. please check the diffractometer limits to allow one of the solutions shown below.")
            print("Current limits are:\n" + self._limits_str())
            print("Solutions:")
            print(df.loc[idx_in] if idx_in else df)
            raise Exception("No unique solution")
        sol = df.loc[idx_in[0]].to_dict()
        fit = next(f for f in fitted if f is not None)
        if not self._kappa:
            sol.update(fit)
        return sol, fit

    def calc_hkl(self, mu=None, delta=None, gamma=None, eta=None, chi=None, phi=None, energy=None):
        """(h, k, l) at the given (default: current) angles and energy (eV)."""
        angles = self._angles(mu, delta, gamma, eta, chi, phi)
        if energy is None:
            energy = self.get_energy()
        return rc.calc_hkl(self._ub(), energy, angles)

    def pseudo_angles(self, mu=None, delta=None, gamma=None, eta=None, chi=None, phi=None):
        """All virtual angles (theta, qaz, alpha, beta, psi, naz, tau, betain,
        betaout, omega) at the given (default: current) angles."""
        return rc.pseudo_angles(self._ub(), self._angles(mu, delta, gamma, eta, chi, phi),
                                self._reference, self._surface)

    def spots(self, hkls, mu=None, delta=None, gamma=None, eta=None, chi=None, phi=None, energy=None,
              width=77.0, height=77.0):
        """Outgoing beams of ``hkls`` at the given (default: current) angles:
        their angles from the detector centre (``off_axis``), the largest
        separation, and ``tdet_max``, the largest distance (units of
        width/height, mm) keeping all spots on the detector face."""
        if energy is None:
            energy = self.get_energy()
        return rc.spots(self._ub(), energy, self._angles(mu, delta, gamma, eta, chi, phi), hkls, width, height)

    def calc_angles_plot(self, h=None, k=None, l=None, det_distance=500, energy=None, constraints_update={},
                         solution_index=0, clear_fig=True, ax=None):
        from matplotlib import pyplot as plt
        from scipy.spatial.transform import Rotation

        if energy is None:
            energy = self.get_energy()
        solution = self.calc_angles(h=h, k=k, l=l, energy=energy, constraints_update=constraints_update).iloc[solution_index]
        b2y = (Rotation.from_rotvec(-np.pi / 2 * np.array([0, 0, 1])).as_matrix()
               @ Rotation.from_rotvec(-np.pi / 2 * np.array([0, 1, 0])).as_matrix())
        fig = plt.figure(f"Recspace {self.name} (h,k,l) = ({h},{k},{l})")
        if clear_fig:
            fig.clf()
        self.diffractometer.recspace_conv.plot_geom(
            mu=solution["mu"], eta=solution["eta"], chi=solution["chi"], phi=solution["phi"],
            gamma=solution["gamma"], delta=solution["delta"], energy=energy,
            detector_distance=det_distance, fig=fig, ax=ax,
            ub_matrix=np.linalg.inv(b2y) @ self._ub(),
        )
        return solution

    # --- visualisation ------------------------------------------------------------

    def widget_geom(self, h=None, k=None, l=None, energy=None, h2=None, detector=None, open_browser=True):
        """Browser page with the 3D reciprocal-space view: the current
        position, every stored reflection and, with an hkl given, every
        solution for it, clickable in a sidebar with their angles."""
        from eco.widgets.recspace_widget import geom_page

        return geom_page(self, hkl=None if h is None else (h, k, l), energy=energy, h2=h2,
                         detector=detector, open_browser=open_browser)

    def widget_reflections(self, h=None, k=None, l=None, h2=None):
        """Interactive solution browser: hkl, energy, constraints (with the
        two-reflection mode), the solution table with motor-limit check and
        filters, and the 3D view. Inline ipywidgets in Jupyter; from a
        terminal it opens ``widget_geom`` in the browser instead."""
        from eco.utilities.utilities import is_notebook

        if not is_notebook():
            return self.widget_geom(h, k, l, h2=h2)
        from eco.widgets.recspace_widget import ReflectionsApp

        return ReflectionsApp(self, hkl=None if h is None else (h, k, l), h2=h2).widget

    def show_you_geometry(self):
        from PIL import Image

        Image.open(YOU_GEOMETRY_PNG).show()

    # --- energy -------------------------------------------------------------------

    def get_energy(self):
        if self._energy_source is not None:
            return float(self._energy_source())
        from epics import PV

        energy = PV(ENERGY_PV).value
        if energy is None:
            raise ValueError("Getting energy from monochromator / machine returned None")
        return energy

    def en2lam(self, en):
        """input: energy in eV, returns wavelength in A"""
        return rc.en2lam(en)

    def lam2en(self, lam):
        """input: wavelength in A, returns energy in eV"""
        return rc.lam2en(lam)
