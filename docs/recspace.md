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
si.add_orientation([0, 0, 1], [0, 0, 1], tag="surface normal")  # along phi at all angles 0 (You z)
si.add_orientation([1, 1, 0], [0, 1, 0], tag="along beam")      # (You y)
si.calc_ub()

rs.constraints.mu(0); rs.constraints.gamma(0); rs.constraints.chi(90)
si.calc_angles(1, 1, 1)                     # every solution, as a table
```

On the beamline it is the same, with the real diffractometer underneath,
and `xrd.recspace.si.hkl.mv([1, 1, 1])` moves the motors to the one
solution inside their soft limits. `xrd.recspace?` and
`xrd.recspace.si?` show this help in the session.

## Creating a crystal

The frame is You's: x vertical, y along the beam, z horizontal across it;
at all angles 0 the phi axis points along z. Lattice constants are in
angstrom and degrees, energies in eV.

**Interactively**, as with diffcalc:

```python
xrd.recspace.create_crystal()
```

asks for a name (a Python name, it becomes `xrd.recspace.<name>`), the six
lattice parameters (Enter keeps the default in brackets, so a cubic crystal
needs only `a`), the (h,k,l) of the surface normal and the (h,k,l) along the
beam. From those two it calculates a *nominal* UB: the crystal as mounted by
design, at all angles 0, with the surface normal along the phi axis.

**The same as a script** is `create_crystal(name, unit_cell=dict(...))`
followed by `add_orientation` for each nominal direction, `set_surface`
and `calc_ub()`, as in the example above.

**From diffcalc**: `xrd.recspace.diffcalc_crystals()` lists what the
diffcalc object knows, `xrd.recspace.import_from_diffcalc("si")` copies one
(`None`: all), including its reflections and UB.

**Then align on real peaks.** The nominal UB is only as good as the mount.
Drive to a Bragg peak (e.g. found with `calc_angles` and a scan) and store
it: `si.add_reflection(2, 2, 0)` takes the current angles and energy (or
pass `mu=..., ..., energy=...`). Then:

| measured | command |
|---|---|
| 1 reflection | `si.calc_ub(0, "surface normal")`: the peak plus the nominal surface normal (a string is a tag) |
| 2 reflections | `si.calc_ub()`: reflections 0 and 1 |
| 3 or more | `si.fit_ub()`: least squares; `refine_lattice=True` also applies the fitted lattice |
| 1, lattice off | `si.refine_ub([h, k, l])`: prints the lattice/U it would change; apply with `refine_lattice=True` / `refine_umatrix=True` |

Two references must not be parallel ((004) together with a (001) surface
normal says nothing about the azimuth), and three for a fit not in one
plane; both are refused with a message. `si.summary()` shows the result,
and `si.calc_hkl()` at a stored peak should give back its hkl.

**Then the constraints**, shared by all crystals of the diffractometer:
fix three, release with `None`, e.g. `xrd.recspace.constraints.mu(0)`,
`.gamma(0)`, `.chi(90)`. And check the motor soft limits, since
`calc_angles_unique` and `hkl.mv` pick the one solution inside them.

Other crystal management: `xrd.recspace.crystals` (this diffractometer's),
`activate_crystal` (one created on another diffractometer, or a
deactivated one), `deactivate_crystal`, `delete_crystal` (also deletes the
files).

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
