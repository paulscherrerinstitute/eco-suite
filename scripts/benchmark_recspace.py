"""Benchmark RecSpace (hklforge) against the diffcalc objects, crystal by crystal.

Both sides are run exactly as the eco wrappers run them:

- diffcalc: what ``eco.utilities.recspace.DiffGeometryYou`` does per call,
  i.e. rebuild a ``UBCalculation`` from the stored unit cell / orientations /
  reflections / U / UB (``to_diffcalc``), then ``HklCalculation``.
- RecSpace: ``eco.utilities.recspace_calc`` on the same stored data.

Crystals are read from a directory in the diffcalc objects' file format
(``<name>_unit_cell.json`` ...). On a beamline console::

    python scripts/benchmark_recspace.py \
        --crystal-dir /sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/crystals

Without ``--crystal-dir`` a set of test crystals (cubic to triclinic, with
orientations and simulated reflections) is written to a temporary directory
first. Nothing is ever written to ``--crystal-dir``.

Checked per crystal:
  1. UB: calc_ub(0, 1) (and fit_ub with >= 3 reflections) on both sides.
  2. hkl -> angles: every reachable hkl up to --hmax under several constraint
     modes; the solution *sets* are compared (angles wrapped to +-180).
  3. angles -> hkl at every solution.
  4. Two reflections at once (RecSpace only, diffcalc cannot): every branch
     re-checked through the forward model for both reflections.
  5. Time per call.
"""

import argparse
import contextlib
import io
import itertools
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from eco.utilities import recspace_calc as rc

MODES = [
    {"eta": 0, "chi": 0, "phi": 0},              # three sample circles held (detector free)
    {"mu": 0, "gamma": 0, "phi": 0},             # vertical scattering, phi held
    {"mu": 0, "gamma": 0, "chi": 90},            # vertical, chi 90
    {"mu": 0, "gamma": 0, "psi": 30},            # azimuth about Q (reference vector)
    {"mu": 0, "gamma": 0, "a_eq_b": True},       # symmetric
    {"chi": 90, "phi": 0, "betain": 2},          # grazing incidence
    {"delta": 0, "mu": 0.5, "eta": 10},          # horizontal detector, two sample circles held
    {"qaz": 90, "mu": 0, "eta": 10},             # qaz + two sample circles
]
TWO_MODES = [
    ({"mu": 0, "gamma": 0}, "h1"),
    ({"mu": 0, "eta": 0}, "between"),
]
ENERGY = 9000.0


def read_value(path):
    with open(path) as f:
        return json.load(f)["value"]


def load_crystal(d, name):
    return {a: read_value(d / f"{name}_{a}.json")
            for a in ("unit_cell", "u_matrix", "ub_matrix", "orientations", "reflections")}


# --- the diffcalc side, as DiffGeometryYou does it ---------------------------------


def diffcalc_ubcalc(cr):
    from diffcalc.hkl.geometry import Position
    from diffcalc.ub import calc as dccalc

    with contextlib.redirect_stdout(io.StringIO()):
        ub = dccalc.UBCalculation("you")
        ub.set_lattice(**cr["unit_cell"])
        for o in cr["orientations"]:
            ub.add_orientation(o["hkl"], o["xyz"], **{k: v for k, v in o.items() if k not in ("xyz", "hkl")})
        for r in cr["reflections"]:
            ub.add_reflection(r["hkl"], Position(*r["position"]), r["energy"] * 1e-3,
                              **{k: v for k, v in r.items() if k not in ("position", "hkl", "energy")})
        if len(cr["ub_matrix"]):
            ub.set_ub(cr["ub_matrix"])
            ub.set_u(cr["u_matrix"])
    return ub


def diffcalc_solve(cr, hkl, mode, ubcalc=None):
    from diffcalc.hkl.calc import HklCalculation
    from diffcalc.hkl.constraints import Constraints

    ubcalc = ubcalc or diffcalc_ubcalc(cr)
    cons = {"nu" if k == "gamma" else k: v for k, v in mode.items()}
    with contextlib.redirect_stdout(io.StringIO()):
        res = HklCalculation(ubcalc, Constraints(cons)).get_position(*hkl, rc.en2lam(ENERGY))
    return [{("gamma" if k == "nu" else k): v for k, v in p.asdict.items()} for p, _ in res]


def diffcalc_hkl(cr, angles, ubcalc=None):
    from diffcalc.hkl.calc import HklCalculation
    from diffcalc.hkl.constraints import Constraints
    from diffcalc.hkl.geometry import Position

    ubcalc = ubcalc or diffcalc_ubcalc(cr)
    calc = HklCalculation(ubcalc, Constraints({"mu": 0, "nu": 0, "phi": 0}))
    return np.array(calc.get_hkl(Position(*rc.angles_to_position(angles)), rc.en2lam(ENERGY)))


# --- test crystals ---------------------------------------------------------------


TEST_CELLS = {
    "Si": (5.431, 5.431, 5.431, 90, 90, 90),
    "SrTiO3_tet": (3.905, 3.905, 3.95, 90, 90, 90),
    "Bi2Se3_hex": (4.143, 4.143, 28.636, 90, 90, 120),
    "VO2_mono": (5.752, 4.538, 5.383, 90, 122.6, 90),
    "triclinic": (4.1, 5.2, 6.3, 87, 95, 101),
}


def make_test_crystals(d, seed=1):
    """Crystals in the diffcalc objects' file format: orientations from a
    surface normal and an in-plane direction, plus (for some) four reflections
    simulated at a slightly misoriented U with 0.01 deg angle noise."""
    rng = np.random.default_rng(seed)
    names = []
    for i, (name, cell) in enumerate(TEST_CELLS.items()):
        uc = dict(zip(("a", "b", "c", "alpha", "beta", "gamma"), cell), name=name)
        oris = [{"hkl": [0, 0, 1], "xyz": [1, 0, 0], "position": None, "tag": "surface normal"},
                {"hkl": [1, 0, 0], "xyz": [0, 1, 0], "position": None, "tag": "in-plane"}]
        U0, _ = rc.calc_u(uc, [], oris)
        miss = rc._rotation(rng.normal(size=3), np.radians(1.5))
        U = miss @ U0
        UB = U @ rc.lattice_b(uc)
        refls = []
        if i % 2 == 0:
            for hkl in ([0, 0, 2], [1, 1, 1], [2, 0, 1], [1, -1, 2], [0, 2, 2]):
                sols = rc.solve(UB, ENERGY, hkl, {"mu": 0, "gamma": 0, "chi": 90 - 5 * len(refls)})
                if not sols:
                    continue
                pos = rc.angles_to_position(sols[0])
                pos = [p + rng.normal(scale=0.01) for p in pos]
                refls.append({"hkl": hkl, "position": pos, "energy": ENERGY, "tag": None})
                if len(refls) == 4:
                    break
        data = {"unit_cell": uc, "u_matrix": U0.tolist(), "ub_matrix": (U0 @ rc.lattice_b(uc)).tolist(),
                "orientations": oris, "reflections": refls}
        for a, v in data.items():
            (d / f"{name}_{a}.json").write_text(json.dumps({"value": v}))
        names.append(name)
    (d / "diffcalc_list.json").write_text(json.dumps({"value": {n: ["test"] for n in names}}))
    return names


# --- comparison --------------------------------------------------------------------


def same_sets(a, b, tol=1e-4):
    if len(a) != len(b):
        return False
    used = set()
    for x in a:
        for j, y in enumerate(b):
            if j not in used and all(abs(((u - v + 180) % 360) - 180) < tol for u, v in zip(x, y)):
                used.add(j)
                break
        else:
            return False
    return True


def as_tuples(sols):
    return [tuple(s[n] for n in rc.POSITION_ORDER) for s in sols]


def violation(ub, hkl, mode, sol):
    """Largest error of a position: hkl (in r.l.u.) and every held
    circle / virtual angle (degrees) of the mode."""
    err = [residual(ub, hkl, sol)]
    va = rc.pseudo_angles(ub, sol)
    for k, v in mode.items():
        if k in rc.POSITION_ORDER:
            err.append(abs(((sol[k] - v + 180) % 360) - 180))
        elif k == "a_eq_b":
            err.append(abs(va["alpha"] - va["beta"]))
        elif k in va:
            err.append(abs(((va[k] - v + 180) % 360) - 180))
    return max(err)


def residual(ub, hkl, sol):
    return float(np.linalg.norm(rc.calc_hkl(ub, ENERGY, sol) - np.asarray(hkl, float)))


def hkl_grid(ub, hmax):
    k = 2 * np.pi / rc.en2lam(ENERGY)
    out = []
    for hkl in itertools.product(range(-hmax, hmax + 1), repeat=3):
        q = np.linalg.norm(np.asarray(ub) @ hkl)
        if 0 < q < 2 * k * 0.98:
            out.append(hkl)
    return out


def benchmark_crystal(name, cr, hmax, max_hkls, rng):
    rep = {"crystal": name}
    ub = np.asarray(cr["ub_matrix"], float)
    if not len(cr["ub_matrix"]):
        rep["skipped"] = "no UB"
        return rep

    # 1. UB from references
    dub = diffcalc_ubcalc(cr)
    nref = len(cr["reflections"]) + len(cr["orientations"])
    if nref >= 2:
        with contextlib.redirect_stdout(io.StringIO()):
            dub.calc_ub(1, 2)
        U, _ = rc.calc_u(cr["unit_cell"], cr["reflections"], cr["orientations"], 0, 1)
        rep["calc_ub_max_dU"] = float(np.abs(U - dub.U).max())
    if len(cr["reflections"]) >= 3:
        with contextlib.redirect_stdout(io.StringIO()):
            du, dlat = dub.fit_ub(list(range(1, len(cr["reflections"]) + 1)))
        U, lat, rms = rc.fit_ub(cr["reflections"])
        rep["fit_ub_max_dU"] = float(np.abs(U - du).max())
        rep["fit_ub_max_dlattice"] = float(max(abs(lat[k] - v) for k, v in
                                               zip(("a", "b", "c", "alpha", "beta", "gamma"), dlat[1:])))

    # 2./3. solutions
    hkls = hkl_grid(ub, hmax)
    if len(hkls) > max_hkls:
        hkls = [hkls[i] for i in sorted(rng.choice(len(hkls), max_hkls, replace=False))]
    counts = dict(cases=0, same=0, both_empty=0, dc_error=0, dc_subset=0, differ=0, dc_invalid=0, rs_invalid=0,
                  same_without_dc_invalid=0, rs_degenerate=0,
                  rs_degenerate_dc_answered=0)
    t_dc = t_dc_cached = t_rs = 0.0
    n_dc = n_rs = 0
    hkl_err = []
    dc_cache = diffcalc_ubcalc(cr)
    examples = []
    for mode in MODES:
        for hkl in hkls:
            counts["cases"] += 1
            t = time.perf_counter()
            try:
                rs = rc.solve(ub, ENERGY, hkl, mode)
            except rc.Degenerate:
                # a continuum of positions (e.g. Q along a free circle's axis)
                counts["rs_degenerate"] += 1
                try:
                    dc = diffcalc_solve(cr, hkl, mode, dc_cache)
                    if dc:
                        counts["rs_degenerate_dc_answered"] += 1
                        if len(examples) < 6:
                            examples.append(f"{mode} {hkl}: recspace degenerate (continuum), "
                                            f"diffcalc picked {len(dc)}")
                except Exception:
                    pass
                continue
            t_rs += time.perf_counter() - t
            n_rs += 1
            try:
                t = time.perf_counter()
                dc = diffcalc_solve(cr, hkl, mode)
                t_dc += time.perf_counter() - t
                t = time.perf_counter()
                diffcalc_solve(cr, hkl, mode, dc_cache)
                t_dc_cached += time.perf_counter() - t
                n_dc += 1
            except Exception as e:
                if not rs:
                    counts["both_empty"] += 1        # diffcalc raises where there is no solution
                    continue
                counts["dc_error"] += 1
                if len(examples) < 6:
                    examples.append(f"{mode} {hkl}: diffcalc {type(e).__name__}: "
                                    f"{(str(e).splitlines() or [''])[0][:90]}; recspace {len(rs)} solutions")
                continue
            dc_valid = [x for x in dc if violation(ub, hkl, mode, x) < 1e-5]
            if any(violation(ub, hkl, mode, x) >= 1e-5 for x in rs):
                counts["rs_invalid"] += 1
            if len(dc_valid) < len(dc):
                counts["dc_invalid"] += 1
            if not dc and not rs:
                counts["both_empty"] += 1
            elif same_sets(as_tuples(dc), as_tuples(rs)):
                counts["same"] += 1
            elif same_sets(as_tuples(dc_valid), as_tuples(rs)):
                counts["same_without_dc_invalid"] += 1
                if len(examples) < 6:
                    bad = [x for x in dc if x not in dc_valid][0]
                    examples.append(f"{mode} {hkl}: diffcalc also returned "
                                    f"{np.round(rc.angles_to_position(bad), 3).tolist()}, violating the "
                                    f"constraints by {violation(ub, hkl, mode, bad):.3g}")
            elif all(any(same_sets([x], [y]) for y in as_tuples(rs)) for x in as_tuples(dc_valid)):
                counts["dc_subset"] += 1
                if len(examples) < 6:
                    examples.append(f"{mode} {hkl}: diffcalc {len(dc)} ({len(dc_valid)} valid), recspace {len(rs)}")
            else:
                counts["differ"] += 1
                if len(examples) < 6:
                    examples.append(f"{mode} {hkl}: DIFFER diffcalc {len(dc)}, recspace {len(rs)}")
            for s in rs[:2]:
                hkl_err.append(float(np.abs(rc.calc_hkl(ub, ENERGY, s) - diffcalc_hkl(cr, s, dc_cache)).max()))
    rep.update(counts)
    rep["examples"] = examples
    rep["hkl_from_angles_max_diff"] = max(hkl_err) if hkl_err else None
    rep["ms_diffcalc_as_wrapper"] = 1e3 * t_dc / max(n_dc, 1)
    rep["ms_diffcalc_ubcalc_reused"] = 1e3 * t_dc_cached / max(n_dc, 1)
    rep["ms_recspace"] = 1e3 * t_rs / max(n_rs, 1)

    # 4. two reflections at once
    two = dict(cases=0, with_solutions=0, branches=0, degenerate=0, max_residual=0.0)
    pairs = [(a, b) for a, b in itertools.combinations(hkls[:12], 2)]
    for cons, det in TWO_MODES:
        for h1, h2 in pairs:
            two["cases"] += 1
            try:
                sols = rc.solve(ub, ENERGY, h1, {**cons, "h2": list(h2), "h2_detector": det})
            except rc.Degenerate:
                two["degenerate"] += 1
                continue
            if sols:
                two["with_solutions"] += 1
                two["branches"] += len(sols)
            for s in sols:
                k = rc.wavevector(ENERGY)
                R = rc.GEOMETRY.sample_matrix(s)
                b = np.asarray(rc.GEOMETRY.beam)
                for h in (h1, h2):
                    r = abs(np.linalg.norm(k * b + R @ ub @ np.asarray(h, float)) - k) / k
                    two["max_residual"] = max(two["max_residual"], float(r))
                if det == "h1":
                    two["max_residual"] = max(two["max_residual"], residual(ub, h1, s))
    rep["two_reflections"] = two
    return rep


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--crystal-dir", type=Path, help="diffcalc crystal directory (read only)")
    p.add_argument("--crystals", nargs="*", help="only these crystal names")
    p.add_argument("--hmax", type=int, default=3)
    p.add_argument("--max-hkls", type=int, default=40, help="random subset of hkls per crystal")
    p.add_argument("--json", type=Path, help="write the full report here")
    args = p.parse_args(argv)
    rng = np.random.default_rng(0)

    if args.crystal_dir:
        d = args.crystal_dir
        names = args.crystals or sorted(p.name[: -len("_unit_cell.json")] for p in d.glob("*_unit_cell.json"))
    else:
        d = Path(tempfile.mkdtemp(prefix="recspace_bench_"))
        names = make_test_crystals(d)
        if args.crystals:
            names = [n for n in names if n in args.crystals]

    reports = []
    for name in names:
        try:
            cr = load_crystal(d, name)
            rep = benchmark_crystal(name, cr, args.hmax, args.max_hkls, rng)
        except Exception as e:
            rep = {"crystal": name, "skipped": f"{type(e).__name__}: {e}"}
        reports.append(rep)
        print(format_report(rep), flush=True)
    print(format_total(reports))
    if args.json:
        args.json.write_text(json.dumps(reports, indent=2))


def format_report(r):
    if "skipped" in r:
        return f"\n## {r['crystal']}: skipped ({r['skipped']})"
    lines = [f"\n## {r['crystal']}"]
    for k in ("calc_ub_max_dU", "fit_ub_max_dU", "fit_ub_max_dlattice", "hkl_from_angles_max_diff"):
        if r.get(k) is not None:
            lines.append(f"  {k:28s} {r[k]:.2e}")
    lines.append(f"  hkl->angles cases {r['cases']}: same {r['same']}, no solution on both {r['both_empty']}, "
                 f"same after dropping diffcalc's invalid positions {r['same_without_dc_invalid']}, "
                 f"diffcalc subset {r['dc_subset']}, diffcalc error {r['dc_error']}, DIFFER {r['differ']}; "
                 f"recspace degenerate (continuum) {r['rs_degenerate']} (diffcalc returned positions for "
                 f"{r['rs_degenerate_dc_answered']}); invalid positions: diffcalc {r['dc_invalid']}, recspace {r['rs_invalid']}")
    lines += [f"    e.g. {e}" for e in r["examples"]]
    lines.append(f"  time per solve [ms]: diffcalc as wrapped {r['ms_diffcalc_as_wrapper']:.2f}, "
                 f"diffcalc reusing UBCalculation {r['ms_diffcalc_ubcalc_reused']:.2f}, recspace {r['ms_recspace']:.2f}")
    t = r["two_reflections"]
    lines.append(f"  two reflections (recspace only): {t['cases']} pairs, {t['with_solutions']} solvable, "
                 f"{t['branches']} branches, {t['degenerate']} degenerate, max Bragg residual {t['max_residual']:.1e}")
    return "\n".join(lines)


def format_total(reports):
    ok = [r for r in reports if "skipped" not in r]
    if not ok:
        return "\nnothing benchmarked"
    tot = {k: sum(r[k] for r in ok) for k in ("cases", "same", "both_empty", "same_without_dc_invalid", "dc_subset",
                                                 "dc_error", "differ", "dc_invalid", "rs_invalid",
                                                 "rs_degenerate", "rs_degenerate_dc_answered")}
    ms = lambda k: np.mean([r[k] for r in ok])
    return (f"\n# total over {len(ok)} crystals ({len(reports) - len(ok)} skipped)\n"
            f"  {tot['cases']} cases: same {tot['same']}, no solution on both {tot['both_empty']}, same after "
            f"dropping diffcalc's invalid positions {tot['same_without_dc_invalid']}, diffcalc subset "
            f"{tot['dc_subset']}, diffcalc error {tot['dc_error']}, DIFFER {tot['differ']}\n"
            f"  cases with invalid positions: diffcalc {tot['dc_invalid']}, recspace {tot['rs_invalid']}; "
            f"recspace degenerate {tot['rs_degenerate']} "
            f"(diffcalc answered {tot['rs_degenerate_dc_answered']} of them)\n"
            f"  mean ms per solve: diffcalc as wrapped {ms('ms_diffcalc_as_wrapper'):.2f}, "
            f"reusing UBCalculation {ms('ms_diffcalc_ubcalc_reused'):.2f}, recspace {ms('ms_recspace'):.2f}")


if __name__ == "__main__":
    sys.exit(main())
