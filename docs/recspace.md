# Reciprocal space: `recspace`

`gps.recspace`, `xrd.recspace`, `rob.recspace` and the namespace-level
`recspace` turn (h, k, l) into diffractometer angles and back. They are the
successor of the `diffcalc` objects next to them, built on
[hklforge](https://github.com/swissfel/hklforge) instead of diffcalc-core,
and for now both run in parallel: same interface, separate files, so trying
`recspace` never changes what `diffcalc` does.

Why a successor:

- **Two reflections at once.** A second reflection can be kept in Bragg
  condition while `hkl` moves, which diffcalc cannot do.
- **Every branch, exactly.** hklforge generates closed-form solvers from the
  geometry and returns all solutions. Where diffcalc returns a position that
  violates its own constraint (see the benchmark below), `recspace` returns
  the correct one.
- **A view of what is going on**: a 3D drawing of the diffractometer with the
  reciprocal lattice, clickable per solution.

## The smallest example (no hardware)

Without a diffractometer, `RecSpace` uses six dummy circles. Pass a fixed
energy and a scratch directory so nothing touches the beamline:

```python
import tempfile
from eco.utilities.recspace_hklforge import RecSpace

rs = RecSpace(name="recspace", config_dir=tempfile.mkdtemp(), energy_source=lambda: 9000)
si = rs.create_crystal("si", unit_cell=dict(a=5.431, b=5.431, c=5.431, alpha=90, beta=90, gamma=90))
si.add_orientation([0, 0, 1], [1, 0, 0])   # (001) vertical (You x)
si.add_orientation([1, 1, 0], [0, 1, 0])   # (110) along the beam (You y)
si.calc_ub()

rs.constraints.mu(0); rs.constraints.gamma(0); rs.constraints.chi(90)
si.calc_angles(1, 1, 1)                     # every solution, as a table
```

On the beamline it is the same, with the real diffractometer underneath:
`xrd.recspace.create_crystal()` asks for the lattice and orientation
interactively, as `diffcalc` does, and `xrd.recspace.si.hkl.mv([1, 1, 1])`
moves the motors to the one solution inside their soft limits.

## Same interface as `diffcalc`

| | |
|---|---|
| crystals | `create_crystal`, `activate_crystal`, `deactivate_crystal`, `delete_crystal` |
| UB | `add_orientation`, `add_reflection`, `del_*`, `calc_ub(idx1, idx2)`, `refine_ub`, `fit_ub`, `set_unit_cell` |
| moving | `h`, `k`, `l`, `hkl` adjustables; `constraints` (3 of: circles and virtual angles) |
| calculating | `calc_angles`, `calc_angles_unique`, `calc_hkl`, `calc_angles_plot` |
| view | `widget_geom()` |

The conventions are diffcalc's (You geometry, x vertical, y along the beam;
`gamma` is diffcalc's `nu`), so a crystal from `diffcalc` can be copied over
unchanged:

```python
xrd.recspace.import_from_diffcalc("si")       # one crystal, or None for all
xrd.recspace.import_constraints_from_diffcalc()
```

Differences worth knowing:

- `set_unit_cell` keeps U and recalculates UB with the new lattice; the
  diffcalc wrapper kept the old UB until the next `calc_ub`.
- A solution that is outside a motor's limits only by its representation
  (e.g. phi = -170 with limits 0..360) is driven as the equivalent angle
  (190) instead of being rejected.
- `set_reference(hkl=...)` / `set_surface(hkl=...)` set the vectors behind
  alpha/beta/psi/naz and betain/betaout; the diffcalc wrapper always used
  diffcalc's defaults (reference hkl (1, 0, 0), surface phi-frame z).
- `pseudo_angles()` gives all virtual angles at the current position,
  `summary()` the crystal at a glance.
- Where a mode leaves a continuum of positions (e.g. Q along a free
  circle's axis), `recspace` raises instead of picking one at random.

## Two reflections at once

Hold two of the sample circles' worth of freedom less, and add `h2`:

```python
rs.constraints.chi(None)
rs.constraints.h2([2, 2, 0])             # keep (220) in Bragg too
rs.constraints.h2_detector("h1")         # detector on (h,k,l); "between": on the bisector
si.hkl.mv([1, 1, 1])                     # moves to a position with (111) and (220) both in Bragg
```

Or, without touching the stored constraints:

```python
si.calc_angles_two((1, 1, 1), (2, 2, 0), detector="between",
                   constraints_update=dict(mu=0, eta=10, gamma=None))
```

The table then also has `separation` (the angle between the two outgoing
beams) and `tdet_max`, the largest sample-detector distance (mm, 77 mm
detector) that keeps both spots on the detector; `si.spots(...)` gives the
same for any set of reflections at any position.

## Seeing it

`si.widget_reflections(1, 1, 1)` is the solution browser modelled on
bernina-scripts' `apps/diffraction_reflections.ipynb`, working on the
crystal's own UB:

- in Jupyter: hkl, energy, constraints with "fix" boxes (including the
  second reflection), a table of all solutions with a motor-limit row, and
  the 3D view; clicking a solution moves the drawing. Constraints changed
  there apply to the browser only until **store constraints**.
- from a terminal it opens `widget_geom` in the web browser: the current
  position, every stored reflection and every solution, clickable, with
  their angles; solutions outside the limits are marked.

Nothing in either view moves a motor.

## Benchmark against diffcalc

`scripts/benchmark_recspace.py` runs both the way the eco wrappers run
them and compares, per crystal: UB from references and from a fit, the full
*set* of solutions for every reachable hkl under eight constraint modes
(with every position re-checked against hkl and the held constraints),
angles to hkl, the two-reflection mode, and time per solve. On a console,
against the crystals the `diffcalc` objects know (only read):

```bash
python scripts/benchmark_recspace.py \
    --crystal-dir /sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/crystals
```

Without `--crystal-dir` it uses five test crystals (cubic, tetragonal,
hexagonal, monoclinic, triclinic). Result on those (2026-10-30, 1600 cases):

- UB from two references: equal to 3e-16; fitted UB equal to 7e-16, fitted
  lattice to 5e-5 Å (diffcalc's fit uses hc = 12.3984 keV Å).
- Solutions: identical sets in 1470 cases, no solution on either side in 89.
  In 26 (all `qaz` + `mu` + `eta`) one of diffcalc's four positions violates
  the held `eta` by up to 23°, and the correct fourth branch, which
  `recspace` returns, is missing. In 15 cases `recspace` reports a continuum;
  diffcalc returned arbitrary positions for 3 of them and nothing for the rest.
  `recspace` returned no invalid position.
- Two reflections: 660 pairs, 298 solvable, 2384 branches, all with both
  reflections in Bragg to 2.4e-14 (relative).
- Time per solve: diffcalc 1.5 ms, `recspace` 3.1 ms (mostly numpy overhead
  on 3-vectors inside hklforge; fast enough for interactive use).
