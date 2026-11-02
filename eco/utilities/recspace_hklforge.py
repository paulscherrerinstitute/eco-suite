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
# Bernina setups, from bernina-scripts' apps/bernina_parameters (the presets
# of the diffraction reflections app; "gam" there is "gamma" here). "ranges"
# narrow the solutions a move may choose from, on top of the motor limits;
# the app's ranges on fixed angles are left out (redundant, and HGeom's chi
# 0..0 contradicts its chi = 90). User presets: <config_dir>/constraint_presets.json.
CONSTRAINT_PRESETS = {
    "sd": {
        "description": "SD: surface diffractometer (You-B), phi axis horizontal (x-)",
        "constraints": {"mu": 0.0, "eta": 0.0, "chi": 0.0},
        "ranges": {"gamma": [-150.0, 10.0], "delta": [-10.0, 120.0]},
    },
    "sd_horizontal": {
        "description": "SD: surface diffractometer (You-B), chi = 90: phi axis vertical (y-)",
        "constraints": {"mu": 0.0, "eta": 0.0, "chi": 90.0},
        "ranges": {"gamma": [-150.0, 10.0], "delta": [-10.0, 120.0]},
    },
    "sd_vertical_elbow_down": {
        "description": "SD: surface diffractometer (You-B), eta = -90 (elbow down), phi axis horizontal (x-)",
        "constraints": {"mu": 0.0, "eta": -90.0, "chi": 0.0},
        "ranges": {"gamma": [-150.0, 10.0], "delta": [-10.0, 120.0]},
    },
    "sd_vertical_elbow_up": {
        "description": "SD: surface diffractometer (You-B), eta = +90 (elbow up), phi axis horizontal (x-)",
        "constraints": {"mu": 0.0, "eta": 90.0, "chi": 0.0},
        "ranges": {"gamma": [-150.0, 10.0], "delta": [-10.0, 120.0]},
    },
    "gic": {
        "description": "GIC chamber, phi axis horizontal (x-)",
        "constraints": {"mu": 0.0, "eta": 0.0, "chi": 0.0},
        "ranges": {"gamma": [-150.0, 10.0], "delta": [-10.0, 120.0]},
    },
    "thc": {
        "description": "THC chamber, chi = 90 and phi = 0, eta free",
        "constraints": {"mu": 0.0, "chi": 90.0, "phi": 0.0},
        "ranges": {"delta": [45.0, 122.0]},
    },
}

# per-crystal files; the diffcalc objects use the same names minus "vectors"
CRYSTAL_FILES = ("unit_cell", "u_matrix", "ub_matrix", "orientations", "reflections", "vectors", "frame")
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


# the lab frame a crystal's matrices and vectors are stored in
LAB_FRAME = {"beam": list(rc.BEAM), "up": list(rc.UP)}
# diffcalc's, and that of RecSpace crystals stored before the frame was recorded
YOU_FRAME = {"beam": list(rc.YOU_BEAM), "up": list(rc.YOU_UP)}


def _bring_into_lab_frame(config_dir, name):
    """Turn a stored crystal into LAB_FRAME if it was stored in another frame
    (no ``_frame`` file: written before the frame was recorded, i.e. in the
    You frame), and record the frame."""
    config_dir = Path(config_dir)
    frame_path = config_dir / f"{name}_frame.json"
    existing = [k for k in ("u_matrix", "ub_matrix", "orientations", "vectors")
                if (config_dir / f"{name}_{k}.json").exists()]
    stored = _read_fs_value(frame_path) if frame_path.exists() else (YOU_FRAME if existing else LAB_FRAME)
    if stored != LAB_FRAME:
        M = rc.frame_rotation(stored["beam"], stored["up"])
        values = rc.turn_crystal_data({k: _read_fs_value(config_dir / f"{name}_{k}.json") for k in existing}, M)
        for k, v in values.items():
            AdjustableFS(config_dir / f"{name}_{k}.json", default_value=v).set_target_value(v).wait()
        print(f"Crystal {name}: turned from the frame beam {stored['beam']}, up {stored['up']} "
              f"into beam {LAB_FRAME['beam']}, up {LAB_FRAME['up']}")
    if not frame_path.exists() or stored != LAB_FRAME:
        AdjustableFS(frame_path, default_value=LAB_FRAME).set_target_value(LAB_FRAME).wait()


class RecSpace(Assembly):
    """Crystals for one diffractometer: (h, k, l) <-> diffractometer angles.

    The hklforge-based successor of the ``diffcalc`` object next to it, with
    the same interface; see the module docstring for what is new. Each
    crystal is an attribute (``xrd.recspace.si``) with its own UB matrix and
    ``h``, ``k``, ``l``, ``hkl`` adjustables; the constraints are shared by
    all crystals of this diffractometer. Lattice in angstrom and degrees,
    energies in eV.

    A crystal's orientation is always given as it lies with all
    diffractometer angles at 0, in the SwissFEL/Bernina lab frame;
    ``xrd.recspace.frame()`` prints it::

        x+   left, looking along the beam
        y+   up
        z+   along the incoming X-ray beam
        last sample rotation phi: axis along x-

    A position is reached from all angles 0 by turning phi, then chi, eta
    and mu, each about its axis at all angles 0 (the innermost circle turns
    the crystal first).

    Two kinds of direction appear, and they are not interchangeable:

    - in the crystal: Miller indices h k l, the normal of the lattice planes
      (hkl), i.e. a reciprocal-lattice direction: [0, 0, 1] or "(001)";
    - in the lab: an axis ("x+", "y-"), "beam", "up", "down", a circle
      ("phi", "-phi": along its rotation axis) or 3 numbers.

    An orientation pairs one of each: "crystal (001) points along lab x+".

    Create a crystal -- one of three ways:

    1. Interactively::

           xrd.recspace.create_crystal()

       asks for a name, the lattice, the surface normal in the crystal (h k
       l) and where it points in the lab with all angles at 0 (e.g. "x+"),
       then a direction in the surface (h k l) and where it points (e.g.
       "beam"). The first is matched exactly, the second only fixes the
       rotation about the first. That gives a nominal UB: the crystal
       mounted as designed.

    2. The same as a script, here Si(001), surface normal along x+ and
       [110] along the beam::

           si = xrd.recspace.create_crystal(
               "si", unit_cell=dict(a=5.431, b=5.431, c=5.431, alpha=90, beta=90, gamma=90))
           si.add_orientation([0, 0, 1], "x+", tag="surface normal")
           si.add_orientation([1, 1, 0], "beam", tag="in-plane")
           si.set_surface(hkl=[0, 0, 1])     # for betain/betaout; option 1 does this too
           si.calc_ub()

    3. From the crystals the diffcalc object already knows::

           xrd.recspace.diffcalc_crystals()        # what there is
           xrd.recspace.import_from_diffcalc("si")  # None: all of them

    Then replace the nominal orientation by measured reflections: drive to a
    Bragg peak and ``si.add_reflection(h, k, l)`` stores it with the current
    angles and energy. With one reflection,
    ``si.calc_ub(0, "surface normal")`` combines it with the nominal surface
    normal; with two, ``si.calc_ub()``; with three or more, ``si.fit_ub()``.
    Two references must not be parallel (e.g. (004) with a (001) surface
    normal), three for a fit not in one plane. ``si.summary()`` shows the
    result, ``si.calc_hkl()`` should then read the peak's hkl.

    Constraints -- fix three. A stored Bernina setup (sd = surface diffractometer
    horizontal/vertical, GIC, THC; the list and a choice without argument)::

        xrd.recspace.set_constraints()
        xrd.recspace.set_constraints("thc")

    or exactly three angles, everything else released::

        xrd.recspace.set_constraints(mu=0, gamma=0, chi=90)

    or one at a time, release with None: ``xrd.recspace.constraints.chi(90)``.
    A setup may also restrict which solution a move picks
    (``solution_ranges``, e.g. delta 45..122 for the THC).

    A second reflection kept in Bragg condition takes the place of one of
    them: ``constraints.chi(None)``, ``constraints.h2([2, 2, 0])``.

    Use it::

        si.calc_angles(1, 1, 1)        # every solution, limits ignored
        si.calc_angles_unique(1, 1, 1) # the one inside the motor limits
        si.hkl.mv([1, 1, 1])           # move there
        si.widget_reflections(1, 1, 1) # browse the solutions in 3D

    Managing crystals: ``crystals`` (this diffractometer's),
    ``activate_crystal`` (one of another diffractometer, or deactivated),
    ``deactivate_crystal``, ``delete_crystal``. Constraints from diffcalc:
    ``import_constraints_from_diffcalc()``; own setups: ``store_constraint_preset``. More in docs/recspace.md.
    """

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
        self._append(
            AdjustableFS,
            self.config_dir / f"{name}_solution_ranges.json",
            name="solution_ranges",
            default_value={},
            is_setting=True,
            is_display=True,
        )
        for key, meta in self._crystal_list().items():
            if self.diffractometer.name in meta:
                self._append_crystal(key, is_setting=False, is_display=True)

    def frame(self):
        """Print the diffractometer frame that crystal directions refer to."""
        print(rc.frame_description())

    @property
    def crystals(self):
        """Names of the crystals active on this diffractometer."""
        return [k for k, meta in self._crystal_list().items() if self.diffractometer.name in meta]

    def _append_crystal(self, name, is_setting=True, is_display=False):
        self._append(
            RecSpaceCrystal,
            diffractometer_you=self.diffractometer,
            constraints=self.constraints,
            solution_ranges=self.solution_ranges,
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
        """Create a new crystal on this diffractometer and return it.

        Without ``unit_cell`` everything is asked interactively (``new_ub``):
        name, lattice, surface normal and in-plane direction, and a nominal
        UB is calculated. With ``unit_cell=dict(a=.., b=.., c=.., alpha=..,
        beta=.., gamma=..)`` (angstrom, degrees) only the lattice is set; add
        orientations or reflections and run ``calc_ub()`` next. The new
        crystal is then ``self.<name>``. See ``RecSpace`` for an example.
        """
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
        values = rc.turn_crystal_data(values, rc.YOU_TO_LAB)
        values["vectors"] = {"reference": dict(rc.DEFAULT_REFERENCE), "surface": dict(rc.DEFAULT_SURFACE)}
        values["frame"] = LAB_FRAME
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

    # --- constraint presets -----------------------------------------------------

    def _preset_file(self):
        return self.config_dir / "constraint_presets.json"

    def constraint_presets(self):
        """The stored setups for ``set_constraints``: the built-in Bernina ones
        (``CONSTRAINT_PRESETS``) and those stored with
        ``store_constraint_preset`` (shared by all diffractometers)."""
        user = _read_fs_value(self._preset_file()) if self._preset_file().exists() else {}
        return {**CONSTRAINT_PRESETS, **user}

    def _describe_preset(self, name, preset):
        cons = ", ".join(f"{k}={v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else f"{k}={v}"
                         for k, v in preset["constraints"].items())
        ranges = ", ".join(f"{k} {lo:g}..{hi:g}" for k, (lo, hi) in (preset.get("ranges") or {}).items())
        line = f"{name:28} {cons}"
        if ranges:
            line += f"; ranges {ranges}"
        return line + f"\n{'':28} {preset.get('description', '')}"

    def set_constraints(self, preset=None, ranges=True, **fixed):
        """Set all constraints at once: from a stored setup, or exactly the
        angles given. Everything not set is released, including ``h2``.

        Without arguments it lists the stored setups and asks::

            xrd.recspace.set_constraints()
            xrd.recspace.set_constraints("sd_horizontal")   # mu=0, eta=0, chi=90
            xrd.recspace.set_constraints(mu=0, gamma=0, chi=90)  # these three, the rest free

        Three are needed for a finite set of solutions: sample circles,
        detector circles or virtual angles (qaz, naz, alpha, beta, psi,
        betain, betaout, ...), see ``constraints``. ``constraint_presets()``
        lists the setups, ``store_constraint_preset`` adds one.

        ``ranges`` restricts which solution a move (``hkl.mv``,
        ``calc_angles_unique``) may choose, on top of the motor limits:
        True takes the setup's ranges (e.g. delta 45..122 for the THC), a
        dict sets them, e.g. ``ranges=dict(delta=(-10, 120))``, False or {}
        clears them. They are kept in ``solution_ranges``; ``calc_angles``
        still shows every solution.
        """
        if preset is None and not fixed:
            names = list(self.constraint_presets())
            print("Stored constraint setups:")
            for i, n in enumerate(names):
                print(f"{i:2}) " + self._describe_preset(n, self.constraint_presets()[n]).replace("\n", "\n    "))
            choice = input("Your choice (number or name, q to quit): ").strip()
            if choice in ("", "q"):
                return None
            preset = names[int(choice)] if choice.isdigit() and int(choice) < len(names) else choice
        if preset is not None:
            presets = self.constraint_presets()
            if preset not in presets:
                raise KeyError(f"No constraint setup {preset!r}; there are: {', '.join(presets)}")
            fixed = {**presets[preset]["constraints"], **fixed}
            if ranges is True:
                ranges = presets[preset].get("ranges") or {}
        unknown = [k for k in fixed if k not in DEFAULT_CONSTRAINTS]
        if unknown:
            raise KeyError(f"Unknown constraint(s) {unknown}; possible: {', '.join(DEFAULT_CONSTRAINTS)}")
        if not isinstance(ranges, dict):
            ranges = {}
        bad = [k for k in ranges if k not in rc.POSITION_ORDER]
        if bad:
            raise KeyError(f"Ranges only for the circles {', '.join(rc.POSITION_ORDER)}, not {bad}")
        cons = {k: None for k in DEFAULT_CONSTRAINTS}
        cons["h2_detector"] = self._constraints().get("h2_detector") or "h1"
        cons.update(fixed)
        self._constraints.set_target_value(cons).wait()
        self.solution_ranges.set_target_value({k: [float(lo), float(hi)] for k, (lo, hi) in ranges.items()}).wait()
        held = {k: v for k, v in cons.items() if v is not None and k != "h2_detector"}
        print(f"constraints: {', '.join(f'{k}={v}' for k, v in held.items())}"
              + (f" (setup {preset})" if preset else ""))
        if self.solution_ranges():
            print("solution ranges: " + ", ".join(f"{k} {lo:g}..{hi:g}" for k, (lo, hi) in self.solution_ranges().items()))
        if len(held) != 3:
            print(f"Note: {len(held)} constraints held; the solver needs 3 (a second reflection h2 counts as one).")
        return held

    def store_constraint_preset(self, name, description=""):
        """Store the current constraints and solution ranges as a setup for
        ``set_constraints(name)``, in the config directory (shared by all
        diffractometers; a built-in name is overridden)."""
        cons = {k: v for k, v in self._constraints().items() if v is not None and k != "h2_detector"}
        user = _read_fs_value(self._preset_file()) if self._preset_file().exists() else {}
        user[name] = {"description": description, "constraints": cons, "ranges": dict(self.solution_ranges())}
        AdjustableFS(self._preset_file(), default_value={}).set_target_value(user).wait()
        return user[name]


class RecSpaceCrystal(Assembly):
    """One crystal on one diffractometer: UB matrix, reference data and the
    h, k, l adjustables. Interface as ``eco.utilities.recspace.DiffGeometryYou``.

    Orientation (UB), with directions in the lab frame at all angles 0
    (``frame()``: z+ along the beam, y+ up, x+ = y x z, left looking along
    the beam; the phi axis is x-):

    - ``add_orientation(hkl, xyz)``: a nominal direction, e.g.
      ``add_orientation([0, 0, 1], "x+", tag="surface normal")``
    - ``add_reflection(h, k, l)``: a measured peak at the current angles and
      energy (or give ``mu=..., ..., energy=...``)
    - ``calc_ub()``: from reflections 0 and 1 (an index falls back to the
      orientation with that index; a string is a tag, so
      ``calc_ub(0, "surface normal")`` mixes both); ``fit_ub()``: from 3 or
      more reflections; ``refine_ub(hkl)``: lattice and/or U from one peak
    - ``set_unit_cell``, ``set_reference``, ``set_surface``, ``summary()``

    Calculating and moving: ``h``, ``k``, ``l``, ``hkl`` (adjustables),
    ``calc_angles``, ``calc_angles_two``, ``calc_angles_unique``,
    ``calc_hkl``, ``pseudo_angles``, ``spots``. Viewing: ``widget_reflections``,
    ``widget_geom``.
    """

    def __init__(self, diffractometer_you=None, constraints=None, name=None, config_dir=None, energy_source=None,
                 solution_ranges=None):
        super().__init__(name=name)
        self.config_dir = Path(config_dir or CONFIG_DIR)
        self._energy_source = energy_source
        self._solution_ranges = solution_ranges
        _bring_into_lab_frame(self.config_dir, name)
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
                logger.warning("diffractometer angle '%s' failed to initialize; recspace uses constraints.%s instead "
                               "(h, k, l read NaN while that is unset).", adj, adj)
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
            return lambda *args, **kwargs: self._current_hkl()[i]

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
            lambda *args, **kwargs: self._current_hkl(),
            self._calc_angles_unique_diffractometer,
            name="hkl",
        )

    def _current_hkl(self):
        """calc_hkl() for the readbacks; NaN where an angle cannot be read,
        so that one failed motor does not break the display."""
        try:
            return self.calc_hkl()
        except ValueError:
            return np.full(3, np.nan)

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
        angles = [c if v is None else v for v, c in zip(given, cur)]
        missing = [n for n, v in zip(rc.POSITION_ORDER, angles) if v is None]
        if missing:
            raise ValueError(f"no value for {', '.join(missing)}: not readable (failed to initialize?) and "
                             f"no constraint value; give it, e.g. calc_hkl({missing[0]}=0), or set "
                             f"constraints.{missing[0]}(...)")
        return rc.position_to_angles(angles)

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
        """Set lattice and two crystal directions interactively and calculate UB.

        The directions are given as they lie with all diffractometer angles
        at 0, in the diffractometer's own frame (``frame()``): e.g. the
        surface normal along "x+" and an in-plane direction along "beam".
        """
        crystal_name = input(f"Name of the crystal: ({self.name})") or str(self.name)
        a = float(input("Lattice constant a (1): ") or 1)
        b = float(input(f"Lattice constant b ({a}): ") or a)
        c = float(input(f"Lattice constant c ({a}): ") or a)
        alpha = float(input("Angle alpha (90): ") or 90)
        beta = float(input(f"Angle beta ({alpha}): ") or alpha)
        gamma = float(input(f"Angle gamma ({alpha}): ") or alpha)

        def ask_hkl(prompt, default):
            while True:
                text = input(prompt)
                if not text.strip():
                    return default
                v = rc.parse_triple(text)
                if v is not None and any(v):
                    return v
                print("  Miller indices h k l of the crystal, e.g. 0 0 1, (0,0,1) or (001)"
                      + (" -- where it points in the lab is asked next" if text.strip()[0].isalpha() else ""))

        def ask_dir(prompt, default):
            while True:
                text = input(prompt).strip() or default
                try:
                    return rc.lab_direction(text)
                except ValueError as e:
                    print(f"  {e}")

        phi_axis = rc._axis_name(rc.GEOMETRY.sample[-1].axis)
        normal_default = rc._axis_name(rc.DEFAULT_SURFACE["phi"])
        print()
        print(rc.frame_description())
        print(
            "\nThe crystal's orientation, as mounted with all angles at 0, from two directions.\n"
            "Each is given twice:\n"
            "  in the crystal: Miller indices h k l, the normal of the lattice planes (hkl)\n"
            "                  (a reciprocal-lattice direction), e.g. 0 0 1, (0,0,1) or (001)\n"
            "  in the lab:     where that direction points: an axis (x+, y-, ...), beam, up,\n"
            "                  down, phi or -phi (along the phi axis), or 3 numbers\n"
            "The first direction is matched exactly; the second only fixes the rotation about\n"
            "the first (its part perpendicular to the first is used). Defaults in [brackets]."
        )
        normal = ask_hkl("1st direction (surface normal) in the crystal, h k l [0 0 1]: ", [0, 0, 1])
        normal_dir = ask_dir(f"  ... points in the lab along [{normal_default}] (the phi axis is {phi_axis}): ",
                             normal_default)
        inplane = ask_hkl("2nd direction (in the surface) in the crystal, h k l [1 0 0]: ", [1, 0, 0])
        inplane_dir = ask_dir("  ... points in the lab along [beam]: ", "beam")
        self.set_unit_cell(crystal_name, a, b, c, alpha, beta, gamma)
        self.orientations.set_target_value([]).wait()
        self.add_orientation(normal, normal_dir, tag="surface normal")
        self.add_orientation(inplane, inplane_dir, tag="in-plane")
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
        ``set_reference``. Default: phi (1, 0, 0), diffcalc's phi (0, 0, 1)
        in this frame (the line of the phi axis at all angles 0)."""
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
        """Add a reference orientation: the crystal direction ``hkl`` points
        along the lab direction ``xyz`` with all angles at 0 (or at
        ``position``, a list in Position order).

        - ``hkl``, in the crystal: Miller indices, the normal of the lattice
          planes (hkl), i.e. a reciprocal-lattice direction (not a real-space
          [uvw]); [0, 0, 1] or a string like "(001)".
        - ``xyz``, in the lab (``frame()`` prints it): an axis ('x+', 'y-',
          ...), 'beam', 'up', 'down', a circle ('phi', '-phi': along its
          rotation axis) or 3 numbers.

        E.g. ``add_orientation([0, 0, 1], "x+", tag="surface normal")``.
        """
        if isinstance(hkl, str) and rc.parse_triple(hkl) is not None:
            hkl = rc.parse_triple(hkl)
        if not hasattr(hkl, "__len__") or isinstance(hkl, str):
            print("Please enter hkl as Miller indices: add_orientation([h, k, l], ...) or add_orientation('(001)', ...)")
            return
        xyz = rc.lab_direction(xyz)
        self.orientations.set_target_value(
            self.orientations() + [{"hkl": list(hkl), "xyz": xyz, "position": position, "tag": tag}]
        ).wait()

    def frame(self):
        """Print the diffractometer frame that directions refer to."""
        print(rc.frame_description())

    def del_orientation(self, idx):
        oris = self.orientations()
        removed = oris.pop(idx)
        self.orientations.set_target_value(oris).wait()
        print(f"Removed orientation {removed}")

    def calc_ub(self, idx1=0, idx2=1):
        """Calculate U (and UB) from two references. The first is matched
        exactly, the second only fixes the rotation about the first (its
        part perpendicular to the first is used), as in Busing & Levy.

        As diffcalc: an index refers to a reflection, or to the orientation
        with that index if there are not that many reflections; a string is a
        tag. ``idx2=None`` uses reflection ``idx1`` alone: the current U turned
        by the smallest rotation that puts that reflection where it was
        measured (the rotation about its Q is kept).
        """
        U, used = rc.calc_u(self.unit_cell(), self.reflections(), self.orientations(), idx1, idx2,
                            U0=self.u_matrix())
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
            lines.append(f"  orientation {i}: hkl {o['hkl']} along {rc._axis_name(o['xyz'])}  {o.get('tag') or ''}")
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

    def _ranges(self):
        if self._solution_ranges is None:
            return {}
        try:
            return dict(self._solution_ranges())
        except Exception:
            return {}

    def calc_angles_unique(self, h=None, k=None, l=None, energy=None):
        """The one solution for (h, k, l) inside the motor limits and the
        solution ranges of ``set_constraints``, as a dict. Raises if there
        are none or several (adjust the soft limits or the ranges)."""
        return self._unique_solution(h, k, l, energy)[0]

    def _unique_solution(self, h=None, k=None, l=None, energy=None):
        """(solution dict, motor targets inside the limits)."""
        df = self.calc_angles(h, k, l, energy)
        ranges = self._ranges()
        fitted = [self.fit_target_into_limits(**df.loc[idx].to_dict())
                  if rc.in_ranges(df.loc[idx].to_dict(), ranges) else None for idx in df.index]
        idx_in = [idx for idx, f in zip(df.index, fitted) if f is not None]
        if len(idx_in) != 1:
            if idx_in:
                print(f"There is not a unique angular configuration to reach ({h},{k},{l}), please change "
                      "the diffractometer motor soft limits to allow only one of the solutions shown below.")
            else:
                print("There is no angular configuration, which is allowed for the current diffractometer "
                      "motor soft limits. please check the diffractometer limits to allow one of the solutions shown below.")
            print("Current limits are:\n" + self._limits_str())
            if ranges:
                print("Solution ranges (set_constraints) are:\n"
                      + "".join(f"{n}: ({lo:g}, {hi:g})\n" for n, (lo, hi) in ranges.items()))
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
