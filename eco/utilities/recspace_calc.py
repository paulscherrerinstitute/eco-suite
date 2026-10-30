"""Reciprocal-space calculations behind ``RecSpace``, on top of hklforge.

Pure numpy + hklforge, no eco, EPICS or file access, so everything here can
be tested and benchmarked on its own (``tests/test_recspace_hklforge.py``,
``scripts/benchmark_recspace.py``). ``eco.utilities.recspace_hklforge`` wraps
it into eco Assemblies with the same interface as the diffcalc-based
``eco.utilities.recspace``.

Conventions are diffcalc's, so crystals stored by the ``diffcalc`` objects
can be used unchanged:

- You (1999) geometry, lab frame x vertical, y along the beam, z transverse.
  The out-of-plane detector circle diffcalc calls ``nu`` is ``gamma`` here
  (the Bernina name), also inside the hklforge geometry, so no renaming is
  needed anywhere else.
- UB in the phi frame with the 2 pi convention, q = UB h, abs(q) = 2 pi / d.
- Stored reflection positions are lists in diffcalc's ``Position`` order
  (mu, delta, gamma, eta, chi, phi), energies in eV.
- Orientations are (hkl, xyz) with xyz a lab direction at the given position
  (all zeros if none), i.e. a phi-frame direction for ``position=None``.
- Reference vector default n_hkl = (1, 0, 0) (behind alpha, beta, psi, naz,
  tau), surface normal default n_phi = (0, 0, 1) (betain, betaout), as in
  diffcalc's ``UBCalculation``.

hklforge has no UB-from-reflections yet, so the Busing-Levy part (two
references, primary only, single-reflection refinement and the
least-squares fit) lives here, written to give diffcalc's numbers.
"""

from __future__ import annotations

import numpy as np
from hklforge import Degenerate, Geometry, ModeError
from hklforge import plan as _hkl_plan
from hklforge.geometry import HC_EV_ANGSTROM, Circle, wavevector
from hklforge.lattice import b_matrix
from hklforge.virtual import virtual_angles

# diffcalc's Position order, used for stored reflection positions
POSITION_ORDER = ("mu", "delta", "gamma", "eta", "chi", "phi")
SAMPLE_CIRCLES = ("mu", "eta", "chi", "phi")
DETECTOR_CIRCLES = ("gamma", "delta")
# rows of a solution table: the six circles, then the virtual angles
VIRTUAL_ORDER = ("theta", "ttheta", "qaz", "alpha", "naz", "tau", "psi", "beta",
                 "betain", "betaout", "omega")

# constraint keys taking a boolean (in the diffcalc constraint dict they are
# None or True)
BOOL_CONSTRAINTS = ("a_eq_b", "bin_eq_bout", "bisect")
DETECTOR_POLICIES = ("h1", "between")

DEFAULT_REFERENCE = {"hkl": [1, 0, 0]}
DEFAULT_SURFACE = {"phi": [0, 0, 1]}

SMALL = 1e-4  # diffcalc's (Vlieg's) threshold for parallel references


def _you_geometry() -> Geometry:
    psic = Geometry.builtin("psic")
    return Geometry(
        name="bernina_you",
        sample=list(psic.sample),
        detector=[Circle("gamma" if c.name == "nu" else c.name, c.axis) for c in psic.detector],
        beam=psic.beam,
        description="psic (You 1999) with the out-of-plane detector circle named gamma, as at Bernina",
        labels=dict(psic.labels),
        azimuth_zero=psic.azimuth_zero,
        bisect_circle=psic.bisect_circle,
    )


GEOMETRY = _you_geometry()


# --- units and frames --------------------------------------------------------


def en2lam(energy_eV):
    """Energy in eV -> wavelength in angstrom."""
    return HC_EV_ANGSTROM / energy_eV


def lam2en(lam):
    """Wavelength in angstrom -> energy in eV."""
    return HC_EV_ANGSTROM / lam


def position_to_angles(position) -> dict:
    """[mu, delta, gamma, eta, chi, phi] (diffcalc order) -> {name: degrees}."""
    return {n: float(v) for n, v in zip(POSITION_ORDER, position)}


def angles_to_position(angles: dict) -> list:
    return [float(angles[n]) for n in POSITION_ORDER]


def q_phi_unit(angles: dict) -> np.ndarray:
    """Scattering vector in the phi frame in units of abs(k), diffcalc's ``get_q_phi``."""
    return GEOMETRY.q_phi(angles, 1.0)


def vector_phi(spec, ub) -> np.ndarray | None:
    """Unit phi-frame vector of a reference/surface spec.

    ``spec`` is ``{"hkl": [h, k, l]}`` (a reciprocal-lattice direction, turned
    with UB, so it follows the crystal) or ``{"phi": [x, y, z]}`` (fixed in
    the phi frame, i.e. to the sample stage).
    """
    if not spec:
        return None
    if spec.get("hkl") is not None:
        v = np.asarray(ub, float) @ np.asarray(spec["hkl"], float)
    else:
        v = np.asarray(spec["phi"], float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else None


def vector_hkl(spec, ub) -> np.ndarray | None:
    """The same spec expressed as (unnormalised) Miller indices."""
    if not spec:
        return None
    if spec.get("hkl") is not None:
        return np.asarray(spec["hkl"], float)
    return np.linalg.solve(np.asarray(ub, float), np.asarray(spec["phi"], float))


def _rotation(axis, angle) -> np.ndarray:
    """Right-handed rotation about ``axis`` by ``angle`` (radians)."""
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * K @ K


# --- UB from references --------------------------------------------------------


def lattice_b(unit_cell: dict) -> np.ndarray:
    return b_matrix(*(float(unit_cell[k]) for k in ("a", "b", "c", "alpha", "beta", "gamma")))


def reflection_vector(reflection: dict) -> np.ndarray:
    """Phi-frame direction of a stored reflection's measured Q."""
    return q_phi_unit(position_to_angles(reflection["position"]))


def orientation_vector(orientation: dict) -> np.ndarray:
    """Phi-frame direction of a stored orientation's xyz."""
    pos = orientation.get("position")
    xyz = np.asarray(orientation["xyz"], float)
    if pos is None:
        return xyz
    return GEOMETRY.sample_matrix(position_to_angles(pos)).T @ xyz


def u_from_two(B, h1, v1, h2, v2) -> np.ndarray:
    """U from two (hkl, phi-frame direction) pairs, Busing & Levy.

    The first pair is matched exactly, the second only in the plane it
    spans with the first, as in diffcalc's ``_calc_ub_from_two_references``.
    """
    h1c, h2c = B @ np.asarray(h1, float), B @ np.asarray(h2, float)
    v1, v2 = np.asarray(v1, float), np.asarray(v2, float)

    def triple(a, b):
        t3 = np.cross(a, b)
        t2 = np.cross(t3, a)
        out = []
        for t in (a, t2, t3):
            n = np.linalg.norm(t)
            if n < SMALL:
                raise ValueError("Invalid UB reference data: the two reflections/orientations "
                                 "are parallel.")
            out.append(t / n)
        return np.column_stack(out)

    return triple(v1, v2) @ np.linalg.inv(triple(h1c, h2c))


def u_from_one(B, h, v) -> np.ndarray:
    """The smallest rotation taking B h onto the direction v (primary reflection only)."""
    hc = B @ np.asarray(h, float)
    hc = hc / np.linalg.norm(hc)
    v = np.asarray(v, float) / np.linalg.norm(v)
    axis = np.cross(hc, v)
    s = np.linalg.norm(axis)
    if s < 1e-12:
        return np.eye(3) if hc @ v > 0 else _rotation(_any_perpendicular(hc), np.pi)
    return _rotation(axis, np.arctan2(s, hc @ v))


def _any_perpendicular(v):
    w = np.cross(v, [1.0, 0, 0] if abs(v[0]) < 0.9 else [0, 1.0, 0])
    return w / np.linalg.norm(w)


def find_reference(reflections, orientations, idx):
    """diffcalc's lookup: an int is a reflection index, falling back to the
    orientation with that index if there are not enough reflections; a str
    is a tag, searched in the reflections first. Returns (hkl, phi direction,
    description)."""
    if isinstance(idx, str):
        for kind, items, vec in (("reflection", reflections, reflection_vector),
                                 ("orientation", orientations, orientation_vector)):
            for i, item in enumerate(items):
                if item.get("tag") == idx:
                    return item["hkl"], vec(item), f"{kind} {i} ({idx})"
        raise ValueError(f"no reflection or orientation tagged {idx!r}")
    if idx < len(reflections):
        return reflections[idx]["hkl"], reflection_vector(reflections[idx]), f"reflection {idx}"
    if idx < len(orientations):
        return orientations[idx]["hkl"], orientation_vector(orientations[idx]), f"orientation {idx}"
    raise ValueError(f"no reflection or orientation with index {idx} "
                     f"({len(reflections)} reflections, {len(orientations)} orientations)")


def calc_u(unit_cell, reflections, orientations, idx1=0, idx2=1):
    """U from two references (``idx2=None``: from reflection ``idx1`` alone).

    Returns (U, description of what was used).
    """
    B = lattice_b(unit_cell)
    h1, v1, d1 = find_reference(reflections, orientations, idx1)
    if idx2 is None:
        return u_from_one(B, h1, v1), f"{d1} only (azimuth about it undetermined)"
    h2, v2, d2 = find_reference(reflections, orientations, idx2)
    return u_from_two(B, h1, v1, h2, v2), f"{d1} and {d2}"


def fit_ub(reflections, indices=None):
    """Least-squares UB from 3 or more reflections, lattice unconstrained.

    Same algorithm as diffcalc's ``_fit_ub_uncon`` (which is what the eco
    diffcalc wrapper always gets, since it passes all six lattice parameters
    and diffcalc then treats the crystal as triclinic). Returns (U, lattice
    dict with a, b, c, alpha, beta, gamma, rms residual of q in 1/angstrom).
    """
    if indices is None:
        indices = range(len(reflections))
    refls = [reflections[i] for i in indices]
    if len(refls) < 3:
        raise ValueError("Need at least 3 reference reflections to fit UB matrix.")
    X = np.array([r["hkl"] for r in refls], float)
    if np.linalg.matrix_rank(X) < 3:
        raise ValueError("The reflections' hkl are coplanar, which leaves UB undetermined; "
                         "add a reflection out of their plane.")
    Y =np.array([reflection_vector(r) * wavevector(r["energy"]) for r in refls])
    Bt, *_ = np.linalg.lstsq(X, Y, rcond=None)      # rows: a*, b*, c* in the phi frame
    b1, b2, b3 = Bt
    e1 = b1 / np.linalg.norm(b1)
    e2 = b2 - e1 * (b2 @ e1)
    e2 /= np.linalg.norm(e2)
    e3 = b3 - e1 * (b3 @ e1) - e2 * (b3 @ e2)
    e3 /= np.linalg.norm(e3)
    U = np.column_stack([e1, e2, e3])
    V = b1 @ np.cross(b2, b3)
    a1, a2, a3 = (2 * np.pi * np.cross(x, y) / V for x, y in ((b2, b3), (b3, b1), (b1, b2)))
    la, lb, lc = (float(np.linalg.norm(x)) for x in (a1, a2, a3))
    ang = lambda x, y, nx, ny: float(np.degrees(np.arccos(np.clip(x @ y / (nx * ny), -1, 1))))
    lattice = {"a": la, "b": lb, "c": lc,
               "alpha": ang(a2, a3, lb, lc), "beta": ang(a1, a3, la, lc), "gamma": ang(a1, a2, la, lb)}
    rms = float(np.sqrt(np.mean(np.sum((X @ Bt - Y) ** 2, axis=1))))
    return U, lattice, rms


def refine_from_reflection(unit_cell, U, ub, hkl, angles, energy):
    """What one measured reflection says about lattice and orientation.

    Returns (scaled unit cell or None, miscut rotation matrix or None,
    scale, miscut angle in degrees), following diffcalc's ``refine_ub``:
    the lattice constants along non-zero indices are scaled by
    d_measured / d_calculated, and U is rotated by the smallest rotation
    taking UB h onto the measured Q (accurate up to a rotation about Q).
    """
    q = q_phi_unit(angles)
    d_meas = en2lam(energy) / np.linalg.norm(q)
    d_calc = 2 * np.pi / np.linalg.norm(lattice_b(unit_cell) @ np.asarray(hkl, float))
    scale = float(d_meas / d_calc)
    new_cell = None
    if abs(scale - 1) >= SMALL:
        new_cell = dict(unit_cell)
        for key, idx in zip(("a", "b", "c"), hkl):
            if abs(idx) > SMALL:
                new_cell[key] = unit_cell[key] * scale
    hphi = np.asarray(ub, float) @ np.asarray(hkl, float)
    axis = np.cross(hphi, q)
    if np.linalg.norm(axis) < SMALL:
        return new_cell, None, scale, 0.0
    angle = np.arccos(np.clip(hphi @ q / (np.linalg.norm(hphi) * np.linalg.norm(q)), -1, 1))
    return new_cell, _rotation(axis, angle), scale, float(np.degrees(angle))


# --- solving -----------------------------------------------------------------

_PLANS: dict = {}


def make_mode(constraints: dict) -> tuple[dict, str]:
    """diffcalc-style constraint dict (None = free) -> (hklforge mode, detector policy).

    Extra keys over diffcalc: ``h2`` (a second reflection kept in Bragg
    condition, list of 3 or None) and ``h2_detector`` ("h1": detector on the
    first reflection, "between": on the bisector of both outgoing beams).
    """
    mode, detector = {}, "h1"
    for key, val in constraints.items():
        if key == "h2_detector":
            detector = val or "h1"
            continue
        if val is None or val == "":
            continue
        key = "gamma" if key == "nu" else key
        if key in BOOL_CONSTRAINTS:
            if val:
                mode[key] = True
        elif key == "h2":
            if len(val):
                mode["h2"] = tuple(float(x) for x in val)
        else:
            mode[key] = float(val)
    if "h2" not in mode:
        detector = "h1"
    return mode, detector


def get_plan(mode: dict, detector: str = "h1"):
    key = (tuple(sorted((n, repr(v)) for n, v in mode.items())), detector)
    if key not in _PLANS:
        _PLANS[key] = _hkl_plan(GEOMETRY, mode, detector)
    return _PLANS[key]


def solve(ub, energy, hkl, constraints: dict, reference=DEFAULT_REFERENCE,
          surface=DEFAULT_SURFACE) -> list[dict]:
    """Every position putting ``hkl`` in Bragg condition under ``constraints``.

    Each solution is a dict of the six circles plus all virtual angles, in
    degrees, circles wrapped to (-180, 180]. Raises ``ModeError`` for a mode
    that does not fix a finite set of positions.
    """
    mode, detector = make_mode(constraints)
    pl = get_plan(mode, detector)
    n_ref, n_surf = vector_phi(reference, ub), vector_phi(surface, ub)
    k = wavevector(energy)
    out = []
    for a in pl.run(ub, k, hkl, n_surf, n_ref):
        row = {n: a[n] for n in POSITION_ORDER}
        row.update(virtual_angles(GEOMETRY, a, n_surf, n_ref))
        out.append(row)
    return out


def calc_hkl(ub, energy, angles: dict) -> np.ndarray:
    return np.linalg.solve(np.asarray(ub, float), GEOMETRY.q_phi(angles, wavevector(energy)))


def pseudo_angles(ub, angles: dict, reference=DEFAULT_REFERENCE, surface=DEFAULT_SURFACE) -> dict:
    return virtual_angles(GEOMETRY, angles, vector_phi(surface, ub), vector_phi(reference, ub))


def spots(ub, energy, angles: dict, hkls, width=77.0, height=77.0, margin=0.9) -> dict:
    """Outgoing beams of several reflections at one position, their largest
    separation and the largest detector distance keeping them all on a
    ``width`` x ``height`` face (``tdet_max``, in the units of width)."""
    from hklforge import Diffractometer

    d = Diffractometer(GEOMETRY, ub, energy=energy)
    return d.spots(angles, hkls, width=width, height=height, margin=margin)


def wrap_into_limits(value, limits, strict=True):
    """``value`` or an equivalent value +-360 n inside ``limits``, else None.

    hklforge wraps free circles to (-180, 180]; diffcalc may return e.g.
    190 for the same position. A motor with limits (0, 360) can drive both,
    so a solution is not rejected just for its representation.
    """
    lo, hi = limits
    for n in (0, -1, 1, -2, 2):
        v = value + 360.0 * n
        if (lo < v < hi) if strict else (lo <= v <= hi):
            return v
    return None


__all__ = [
    "GEOMETRY", "POSITION_ORDER", "VIRTUAL_ORDER", "ModeError", "Degenerate",
    "en2lam", "lam2en", "position_to_angles", "angles_to_position", "q_phi_unit",
    "vector_phi", "vector_hkl", "lattice_b", "calc_u", "fit_ub", "refine_from_reflection",
    "make_mode", "solve", "calc_hkl", "pseudo_angles", "spots", "wrap_into_limits",
]
