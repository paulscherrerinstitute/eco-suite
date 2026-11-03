# Crystal setup reference (recspace appendix)

Facts needed to set up a crystal in `recspace` correctly, collected from real
mistakes. The how-to is [recspace.md](recspace.md); this page is the
crystallography and the pitfalls behind it. Every number below was checked
with recspace itself.

## Lattice parameters by crystal system

`create_crystal(name, unit_cell=dict(a=, b=, c=, alpha=, beta=, gamma=))`
takes the six conventional cell parameters, angstrom and degrees. recspace
does not know crystal systems: it uses exactly the six numbers given.

| system | lengths | angles |
|---|---|---|
| cubic | a = b = c | alpha = beta = gamma = 90 |
| tetragonal | a = b, c | 90, 90, 90 |
| orthorhombic | a, b, c | 90, 90, 90 |
| hexagonal | a = b, c | alpha = beta = 90, **gamma = 120** |
| trigonal, hexagonal setting | a = b, c | alpha = beta = 90, **gamma = 120** |
| trigonal, rhombohedral setting | a = b = c | alpha = beta = gamma (not 90) |
| monoclinic | a, b, c | alpha = gamma = 90, beta (unique axis b) |
| triclinic | a, b, c | alpha, beta, gamma |

A hexagonal or trigonal crystal given by only a and c (as papers and
databases do) is in the hexagonal setting: **gamma = 120, never 90**.
gamma = 90 with a = b describes a tetragonal cell and puts every reflection
with h or k nonzero at wrong angles. Rhombohedral crystals such as Bi2Se3,
Bi2Te3, Sb2Te3, LiNbO3, sapphire (Al2O3) and calcite (space group R-3m or
R-3c) are almost always given in the hexagonal setting.

## Miller-Bravais indices (h k i l)

Hexagonal and trigonal reflections are often written with four indices
(h k i l), with i = -(h + k). recspace takes three: drop i. (1 0 -1 5) is
`[1, 0, 5]`, (0 0 0 6) is `[0, 0, 6]`, (1 1 -2 0) is `[1, 1, 0]`.

## Crystal directions are reciprocal-lattice directions

The h k l given to `add_orientation`, `set_surface_normal`,
`add_reflection` and `calc_angles` is a reciprocal-lattice vector
h a* + k b* + l c*: the normal of the (hkl) lattice planes, the direction of
Q at that reflection. It is parallel to the real-space direction [h k l] only
for orthogonal axes (cubic, tetragonal, orthorhombic).

In a hexagonal cell (gamma = 120):

- (0 0 1) is along the real c axis (alpha = beta = 90), so "c axis along the
  surface normal" is `set_surface_normal([0, 0, 1])`.
- (1 0 0) is **not** along the real a axis: it is 30 degrees off,
  perpendicular to b. The real a axis is along (2 -1 0).
- So "the a axis points along the beam" is
  `add_orientation([2, -1, 0], "beam")`, not `[1, 0, 0]`.

## Surface normal and orientations are separate

`set_surface_normal(hkl)` says which lattice planes form the sample surface;
it is used only for the incidence and exit angles (betain, betaout,
bin_eq_bout) and the surface plate in the 3D view. `add_orientation` says how
the crystal is mounted and, with `calc_ub()`, makes UB. Tagging an
orientation "surface normal" does **not** set the surface normal. For a film
grown with its c axis out of plane the surface normal is (0 0 1), and the
first mounting direction is usually the same (0 0 1).

## Where the sample surface points with all angles 0

Orientations are always given with **all diffractometer angles at 0**, in
the Bernina lab frame: x+ left looking along the beam, y+ up, z+ along the
beam. At all angles 0 the phi axis lies along x (its rotation vector is x-),
and a sample mounted on the phi stage faces along that line. So the surface
normal of a mounted sample points along **x+** (the default the dialog
offers), whatever constraint setup is used later:

- not y+ ("up"), unless the sample is really mounted that way on the stage;
- not z- or z+: that would put the surface normal along the beam (the beam
  hitting the surface at normal incidence, or from behind).

`widget_geom()` shows the result: the sample plate faces along the surface
normal, the reciprocal cell a*, b*, c* is drawn from the sample.

## Allowed and forbidden reflections

recspace calculates geometry only; it does not know structure factors, so it
happily gives angles for a forbidden reflection. Lattice centring removes
reflections; pick allowed ones to align on:

| lattice | allowed (h k l) |
|---|---|
| face-centred cubic (Cu, Au, Al, rock salt) | h, k, l all even or all odd |
| diamond (Si, Ge, C) | as fcc, and for all-even h + k + l = 4n: (111), (220), (004), (113), not (200), (222), (002) |
| body-centred (Fe, W, Cr) | h + k + l even |
| rhombohedral, hexagonal setting (R-3m, R-3c; obverse) | -h + k + l = 3n: (0 0 3), (0 0 6), (0 1 5), (1 0 10), (1 1 0); not (0 0 1), (0 0 2), (1 0 0) |

R-3c (sapphire, calcite) additionally needs l even for (h -h 0 l)-type
reflections, so the sapphire specular reflections are (0 0 6), (0 0 12).

## Constraints for specular and off-specular reflections

Q along the surface normal (specular, e.g. (0 0 l) of a c-axis film) is along
the phi axis at all angles 0, so phi alone cannot bring it into Bragg
condition: under the `sd` setup (mu = eta = chi = 0, phi free) there is no
solution for (0 0 l). Release mu instead:

```python
xrd.recspace.set_constraints(eta=0, chi=0, phi=0)   # mu = theta, gamma = 2 theta
bs.calc_angles(0, 0, 6)
```

For reflections with an in-plane component the `sd` setup works:
`set_constraints("sd")`, then `calc_angles(0, 1, 5)`.

## Worked example: Bi2Se3 film on sapphire

Bi2Se3: R-3m, hexagonal setting a = 4.132 A, c = 28.600 A, film c axis
along the surface normal; in-plane azimuth not known yet.

```python
bs = recspace.create_crystal(
    "Bi2Se3_15nm",
    unit_cell=dict(a=4.132, b=4.132, c=28.600, alpha=90, beta=90, gamma=120))
bs.set_surface_normal([0, 0, 1])                  # the (001) planes form the surface
bs.add_orientation([0, 0, 1], "x+", tag="mount 1")  # c axis along the phi axis line
bs.add_orientation([2, -1, 0], "beam", tag="mount 2")  # a axis along the beam: a guess
bs.calc_ub()                                      # without it there is no UB
bs.summary()
bs.widget_geom()                                  # check the drawing
```

Then align:

1. specular (0 0 6) or (0 0 15) with `set_constraints(eta=0, chi=0, phi=0)`:
   corrects the tilt of the surface;
2. an off-specular reflection, e.g. (0 1 5) or (1 0 10) under
   `set_constraints("sd")`: scan phi to find it (the azimuth guess is
   probably wrong, the film is 3-fold in plane, often twinned 6-fold);
3. store both with `bs.add_reflection(h, k, l)` at the peak, then
   `bs.calc_ub(0, 1)`.

## Common mistakes

- gamma = 90 for a hexagonal or trigonal cell (must be 120).
- (1 0 0) taken for the real a axis in a hexagonal cell (it is (2 -1 0)).
- `add_orientation` without `calc_ub()` afterwards: the crystal has no UB,
  h, k, l read NaN and `widget_geom()` says "No UB matrix ... run calc_ub()".
- An orientation tagged "surface normal" instead of `set_surface_normal`.
- Lab directions guessed for a flat-lying sample (y+) or along the beam (z-):
  with all angles 0 the mounted surface faces x+.
- Aligning on a forbidden reflection ((0 0 1), (1 0 0) of an R-3m crystal).
- Calling methods that do not exist: there is no `calc_angles(...).mv()`;
  moving is `bs.hkl.mv([h, k, l])` (or `bs.h.mv(...)`), the only calls that
  drive motors. `calc_angles`, `calc_hkl`, `summary`, `widget_*` never move.
- `refine_ub` only prints what it would change unless `refine_umatrix=True`
  or `refine_lattice=True` is given.
