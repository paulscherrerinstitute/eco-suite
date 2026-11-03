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
si.set_surface_normal([0, 0, 1])                     # the (001) planes form the surface
si.add_orientation([0, 0, 1], "x+", tag="mount 1")  # mounted, with all angles at 0,
si.add_orientation([1, 1, 0], "beam", tag="mount 2")  # with (001) along x+, (110) along the beam
si.calc_ub()

rs.constraints.mu(0); rs.constraints.gamma(0); rs.constraints.chi(90)
si.calc_angles(1, 1, 1)                     # every solution, as a table
```

On the beamline it is the same, with the real diffractometer underneath,
and `xrd.recspace.si.hkl.mv([1, 1, 1])` moves the motors to the one
solution inside their soft limits. `xrd.recspace?` and
`xrd.recspace.si?` show this help in the session.

## Creating a crystal

A crystal is always described as it lies with **all diffractometer angles
at 0**, in the SwissFEL/Bernina lab frame: z+ along the beam, y+ up, x+
completing a right-handed frame (left, looking along the beam) -- the frame
the 3D view is labelled in. `xrd.recspace.frame()` prints it, generated
from the geometry:

```
Diffractometer frame at all angles 0:
  x+   left, looking along the beam
  y+   up
  z+   along the incoming X-ray beam
  last sample rotation phi: axis along x- (positive phi turns right-handed about it)
  sample circles, outermost first: mu y+, eta x-, chi z+, phi x-
  a position is reached from all angles 0 by turning phi, then chi, then eta, then mu,
  each about its axis as listed (the innermost circle turns the crystal first)
```

Two kinds of direction appear, and they are not interchangeable:

| | written as | example |
|---|---|---|
| **in the crystal** | Miller indices h k l: the normal of the lattice planes (hkl), a reciprocal-lattice direction (not a real-space [uvw]) | `0 0 1`, `(0,0,1)`, `(001)`, `[0, 0, 1]` |
| **in the lab** | where a direction points with all angles at 0: an axis, `beam`, `up`, `down`, `phi`/`-phi` (along the phi axis) or three numbers | `x+`, `y-`, `beam`, `-phi` |

An orientation pairs one of each: "crystal (001) points along lab x+".

The **surface normal** and the **orientations** are two separate things:

- the surface normal says which lattice planes form the sample surface. It
  is behind the incidence and exit angles `betain`, `betaout` and
  `bin_eq_bout`, and the surface plate in the 3D view. Nothing else uses
  it, and a sample without a defined surface does not need one.
- the orientations (and later the measured reflections) say how the crystal
  is mounted; only they determine UB.

A mounting direction is often the surface normal, but does not have to be:
a crystal can be mounted with another face up, and a miscut surface is not
along a low-index direction. diffcalc's tools took the first orientation as
the surface normal; here it is set on its own:

| | |
|---|---|
| `si.set_surface_normal([0, 0, 1])` or `("(001)")` | in the crystal, Miller indices (non-integer for a miscut); a new UB turns it along |
| `si.set_surface_normal(lab="up")` | where it points with all angles at 0 (e.g. measured with a laser); fixed to the sample stage, a new UB does not turn it |
| `si.del_surface_normal()` | no defined surface |

`si.summary()` shows it in both forms. A mode with `betain`, `betaout` or
`bin_eq_bout` on a crystal without a surface normal is refused with a
message naming `set_surface_normal`.
Lattice constants are in angstrom and degrees, energies in eV.

The diffractometer is still You's six-circle geometry, so every angle
(circles and virtual angles: qaz = 90 is vertical scattering, betain,
psi, ...) means what it means in diffcalc. Only vectors and matrices differ:
diffcalc writes them in You's frame (x up, y beam, z = x cross y), which is
the same frame with the axes named cyclically differently. UB and U from
diffcalc become `YOU_TO_LAB @ UB` here (`recspace_calc.from_you`); a
crystal imported from diffcalc is turned on import, and one stored by an
earlier `recspace` (before the frame was recorded with each crystal, in a
`<name>_frame.json`) is turned once when it is first loaded.

**Interactively**:

```python
xrd.recspace.create_crystal()
```

prints the frame and asks for a name (a Python name, it becomes
`xrd.recspace.<name>`), the six lattice parameters (Enter keeps the default
in brackets, so a cubic crystal needs only `a`), the surface normal (or
`none`), then two mounting directions, each first in the crystal and then
in the lab:

```
Surface normal in the crystal, h k l, or none [0 0 1]: (001)
1st direction in the crystal, h k l [0 0 1]: (001)
  ... points in the lab along [x+] (the phi axis is x-): x+
2nd direction in the crystal, h k l [1 0 0]: 1 1 0
  ... points in the lab along [beam]: beam
```

Enter takes the default in brackets; the first direction's default is the
surface normal just entered, since that is the usual mount (`x+` lies on
the phi axis line, opposite to its rotation vector). The first direction is matched exactly;
the second only fixes the rotation about the first (its part perpendicular
to the first is used), so it does not have to be exactly perpendicular.
From the two it calculates a *nominal* UB: the crystal as mounted by design.

**The same as a script** is `create_crystal(name, unit_cell=dict(...))`
followed by `set_surface_normal(hkl)`, `add_orientation(hkl, direction)`
for each nominal direction and `calc_ub()`, as in the example above.

**From diffcalc**: `xrd.recspace.diffcalc_crystals()` lists what the
diffcalc object knows, `xrd.recspace.import_from_diffcalc("si")` copies one
(`None`: all), including its reflections and UB.

**Then align on real peaks.** The nominal UB is only as good as the mount.
Drive to a Bragg peak (e.g. found with `calc_angles` and a scan) and store
it: `si.add_reflection(2, 2, 0)` takes the current angles and energy (or
pass `mu=..., ..., energy=...`). Then:

| measured | command |
|---|---|
| 1 reflection | `si.calc_ub(0, "mount 1")`: the peak plus the first nominal direction (a string is a tag) |
| 2 reflections | `si.calc_ub()`: reflections 0 and 1 |
| 3 or more | `si.fit_ub()`: least squares; `refine_lattice=True` also applies the fitted lattice |
| 1, lattice off | `si.refine_ub([h, k, l])`: prints the lattice/U it would change; apply with `refine_lattice=True` / `refine_umatrix=True` |

Two references must not be parallel ((004) together with a nominal (001) says nothing about the azimuth), and three for a fit not in one
plane; both are refused with a message. `si.summary()` shows the result,
and `si.calc_hkl()` at a stored peak should give back its hkl.

**Then the constraints**, shared by all crystals of the diffractometer:
fix three. `xrd.recspace.set_constraints()` lists the stored Bernina setups
and asks for one; with a name it sets that one directly, with angles exactly
those, releasing all others:

```python
xrd.recspace.set_constraints("sd_horizontal")   # mu=0, eta=0, chi=90
xrd.recspace.set_constraints(mu=0, gamma=0, chi=90)
```

| setup (sd = surface diffractometer) | held | solution ranges |
|---|---|---|
| `sd` | mu 0, eta 0, chi 0 (phi axis horizontal) | gamma -150..10, delta -10..120 |
| `sd_horizontal` | mu 0, eta 0, chi 90 (phi axis vertical) | same |
| `sd_vertical_elbow_down` | mu 0, eta -90, chi 0 | same |
| `sd_vertical_elbow_up` | mu 0, eta 90, chi 0 | same |
| `gic` | mu 0, eta 0, chi 0 | same |
| `thc` | mu 0, chi 90, phi 0 | delta 45..122 |

They are the presets of bernina-scripts' diffraction reflections app
(`apps/bernina_parameters`). Their solution ranges (the app's min/max
filters) are kept in `xrd.recspace.solution_ranges` and restrict, on top of
the motor soft limits, which solution `calc_angles_unique` and `hkl.mv`
pick; `calc_angles` still lists all. `ranges=False` leaves them out,
`ranges=dict(delta=(0, 90))` sets your own, and
`store_constraint_preset("name", "description")` stores the current
constraints and ranges as a setup of your own. Single constraints can still
be set one at a time, release with `None`: `xrd.recspace.constraints.chi(90)`.
And check the motor soft limits, since `calc_angles_unique` and `hkl.mv`
pick the one solution inside them.

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

The geometry and the angles are diffcalc's (`gamma` is diffcalc's `nu`),
so a crystal from `diffcalc` can be copied over; its matrices and
directions are turned into the Bernina frame on the way:

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
- The surface normal is the crystal's own (`set_surface_normal`, see
  above), not the first orientation; `set_surface` is the same under
  diffcalc's name. A crystal imported from diffcalc keeps the normal the
  diffcalc wrapper always used (phi-frame z in You's frame, fixed to the
  stage, along x+ here) until you set its own. `set_reference(hkl=...)`
  sets the vector behind alpha/beta/psi/naz (diffcalc's default hkl
  (1, 0, 0)).
- `calc_ub(idx)` with one reflection keeps the current U's rotation about
  that reflection's Q and changes it by the smallest rotation; diffcalc
  started from U = 1, which depends on the frame.
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

```{admonition} Did you know?
:class: tip

`xrd.recspace.set_constraints()` without arguments lists the stored
Bernina setups; one name sets all of a setup's constraints at once.
```
