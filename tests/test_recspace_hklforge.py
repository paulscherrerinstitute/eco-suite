"""RecSpace (hklforge) against diffcalc, and the eco wrapper on a dummy diffractometer."""

import contextlib
import io
import json

import numpy as np
import pytest

pytest.importorskip("hklforge")

from eco.utilities import recspace_calc as rc  # noqa: E402
from eco.utilities.recspace_hklforge import RecSpace, _config_flag, _DummyYou  # noqa: E402

ENERGY = 9000.0
CELL = dict(a=4.1, b=5.2, c=6.3, alpha=87, beta=95, gamma=101)
ORIENTATIONS = [
    {"hkl": [0, 0, 1], "xyz": [1, 0, 0], "position": None, "tag": "n"},
    {"hkl": [1, 0, 0], "xyz": [0, 1, 0], "position": None, "tag": "ip"},
]
REFLECTIONS = [
    {"hkl": [1, 1, 1], "position": [1, 30, 2, 15, 40, 10], "energy": 9000, "tag": None},
    {"hkl": [0, 2, 1], "position": [0.5, 35, -3, 20, 60, -30], "energy": 9000, "tag": "b"},
    {"hkl": [2, 0, 1], "position": [0.2, 45, 4, 25, 20, 70], "energy": 9100, "tag": None},
    {"hkl": [1, -1, 2], "position": [-0.3, 50, 1, 22, 80, 120], "energy": 9100, "tag": None},
]


def _ubcalc(refls, oris):
    from diffcalc.hkl.geometry import Position
    from diffcalc.ub import calc as dccalc

    u = dccalc.UBCalculation("you")
    u.set_lattice("x", *(CELL[k] for k in ("a", "b", "c", "alpha", "beta", "gamma")))
    for o in oris:
        u.add_orientation(o["hkl"], o["xyz"])
    for r in refls:
        u.add_reflection(r["hkl"], Position(*r["position"]), r["energy"] * 1e-3)
    return u


@pytest.mark.parametrize("refls,idx1,idx2", [
    ([], 0, 1),                  # two orientations
    (REFLECTIONS, 0, 1),         # two reflections
    (REFLECTIONS[:1], 0, 1),     # reflection, then orientation 1 (diffcalc's index fallback)
    (REFLECTIONS, 0, None),      # primary reflection only
])
def test_calc_u_matches_diffcalc(refls, idx1, idx2):
    pytest.importorskip("diffcalc")
    u = _ubcalc(refls, ORIENTATIONS)
    with contextlib.redirect_stdout(io.StringIO()):
        u.calc_ub(idx1 + 1, None if idx2 is None else idx2 + 1)
    U, _ = rc.calc_u(CELL, refls, ORIENTATIONS, idx1, idx2)
    assert np.abs(U - u.U).max() < 1e-12


def test_calc_u_by_tag():
    U1, _ = rc.calc_u(CELL, REFLECTIONS, ORIENTATIONS, 0, 1)
    U2, _ = rc.calc_u(CELL, REFLECTIONS, ORIENTATIONS, 0, "b")
    assert np.abs(U1 - U2).max() < 1e-14


def test_fit_and_refine_match_diffcalc():
    pytest.importorskip("diffcalc")
    from diffcalc.hkl.geometry import Position

    u = _ubcalc(REFLECTIONS, ORIENTATIONS)
    with contextlib.redirect_stdout(io.StringIO()):
        u.calc_ub(1, 2)
        newu, lat = u.fit_ub([1, 2, 3, 4])
    U, lattice, _ = rc.fit_ub(REFLECTIONS)
    assert np.abs(U - newu).max() < 1e-12
    # diffcalc uses hc = 12.3984 keV A here, so the lattice agrees to ~1e-5 only
    assert max(abs(lattice[k] - v) for k, v in zip(("a", "b", "c", "alpha", "beta", "gamma"), lat[1:])) < 1e-4

    pos = [0.2, 33, 1, 17, 42, 12]
    U0, UB0 = u.U.copy(), u.UB.copy()
    with contextlib.redirect_stdout(io.StringIO()):
        u.refine_ub([1, 1, 1], Position(*pos), rc.en2lam(ENERGY), False, True)
    _, M, _, _ = rc.refine_from_reflection(CELL, U0, UB0, [1, 1, 1], rc.position_to_angles(pos), ENERGY)
    assert np.abs(M @ U0 - u.U).max() < 1e-12


@pytest.mark.parametrize("mode", [
    {"eta": 0, "chi": 0, "phi": 0},
    {"mu": 0, "gamma": 0, "psi": 10},
    {"chi": 10, "phi": 20, "betain": 5},
    {"mu": 0, "gamma": 0, "a_eq_b": True},
    {"delta": 30, "eta": 10, "phi": 3},
])
def test_solution_sets_match_diffcalc(mode):
    pytest.importorskip("diffcalc")
    from diffcalc.hkl.calc import HklCalculation
    from diffcalc.hkl.constraints import Constraints

    u = _ubcalc(REFLECTIONS, ORIENTATIONS)
    with contextlib.redirect_stdout(io.StringIO()):
        u.calc_ub(1, 2)
    cons = {("nu" if k == "gamma" else k): v for k, v in mode.items()}
    dc = HklCalculation(u, Constraints(cons)).get_position(1, 1, 1, rc.en2lam(ENERGY))
    rs = rc.solve(u.UB, ENERGY, (1, 1, 1), mode)
    wrap = lambda a: round(((a + 180) % 360) - 180, 5)
    dset = sorted(tuple(wrap(p.asdict["nu" if n == "gamma" else n]) for n in rc.POSITION_ORDER) for p, _ in dc)
    rset = sorted(tuple(wrap(s[n]) for n in rc.POSITION_ORDER) for s in rs)
    assert dset == rset and len(rset) > 0
    # virtual angles as diffcalc's
    for p, va in dc:
        a = {("gamma" if k == "nu" else k): v for k, v in p.asdict.items()}
        mine = rc.pseudo_angles(u.UB, a)
        for k in ("theta", "qaz", "alpha", "beta", "psi", "naz", "tau", "betain", "betaout"):
            assert abs(wrap(mine[k] - va[k])) < 1e-6, k


def test_wrap_into_limits():
    assert rc.wrap_into_limits(-170, (0, 360)) == 190
    assert rc.wrap_into_limits(10, (-180, 180)) == 10
    assert rc.wrap_into_limits(100, (-50, 50)) is None


def test_config_flag():
    assert _config_flag(["robot", "gps"], "robot")
    assert not _config_flag(["robot"], "kappa")
    assert _config_flag({"kappa": True}, "kappa")
    assert _config_flag(None, "x", default=True)


# --- the eco wrapper ---------------------------------------------------------


@pytest.fixture
def rs(tmp_path):
    diff = _DummyYou(name="dummy")
    for n in ("gamma", "mu", "delta", "eta", "chi", "phi"):
        getattr(diff, n).set_limits(-180, 180)
    return RecSpace(diffractometer_you=diff, name="recspace", config_dir=tmp_path, energy_source=lambda: ENERGY)


def _si(rs):
    c = rs.create_crystal("si", unit_cell=dict(a=5.431, b=5.431, c=5.431, alpha=90, beta=90, gamma=90))
    c.add_orientation([0, 0, 1], [1, 0, 0], tag="n")
    c.add_orientation([1, 1, 0], [0, 1, 0])
    with contextlib.redirect_stdout(io.StringIO()):
        c.calc_ub()
    return c


def _set(rs, **cons):
    for k, v in cons.items():
        getattr(rs.constraints, k).set_target_value(v).wait()


def test_move_hkl_and_read_back(rs):
    c = _si(rs)
    _set(rs, mu=0, gamma=0, chi=90)
    d = rs.diffractometer
    d.delta.set_limits(0, 180)
    d.eta.set_limits(0, 180)
    c.hkl.set_target_value([1, 1, 1]).wait()
    assert np.allclose(c.calc_hkl(), [1, 1, 1], atol=1e-9)
    assert 0 < d.eta() < 180 and d.delta() > 0
    assert len(c.calc_angles(1, 1, 1)) == 4


def test_limits_take_360_equivalent(rs):
    c = _si(rs)
    _set(rs, mu=0, gamma=0, chi=90)
    d = rs.diffractometer
    d.delta.set_limits(0, 180)
    d.eta.set_limits(0, 180)
    phi = c.calc_angles_unique(1, 1, 1)["phi"]
    # only phi + 360 fits now; diffcalc's wrapper would reject the branch
    d.phi.set_limits(phi + 359, phi + 361)
    assert c.calc_angles_unique(1, 1, 1)["phi"] == pytest.approx(phi + 360)
    c.hkl.set_target_value([1, 1, 1]).wait()
    assert d.phi() == pytest.approx(phi + 360)


def test_two_reflections_in_bragg(rs):
    c = _si(rs)
    _set(rs, mu=0, gamma=0, h2=[2, 2, 0])
    df = c.calc_angles(1, 1, 1)
    assert len(df) > 0
    ub, k = c._ub(), rc.wavevector(ENERGY)
    b = np.asarray(rc.GEOMETRY.beam)
    for _, row in df.iterrows():
        R = rc.GEOMETRY.sample_matrix(row.to_dict())
        for h in ((1, 1, 1), (2, 2, 0)):
            assert abs(np.linalg.norm(k * b + R @ ub @ np.asarray(h, float)) - k) < 1e-9
    df2 = c.calc_angles_two((1, 1, 1), (2, 2, 0), detector="between", constraints_update={"mu": 0, "eta": 10, "gamma": None, "h2": None})
    assert {"separation", "tdet_max"} <= set(df2.columns)


def test_reference_and_surface(rs):
    c = _si(rs)
    c.set_reference(hkl=[0, 0, 1])
    c.set_surface(phi=[1, 0, 0])
    a = {"mu": 0, "delta": 30, "gamma": 0, "eta": 15, "chi": 90, "phi": 0}
    va = c.pseudo_angles(**a)
    assert {"alpha", "beta", "psi", "betain", "betaout"} <= set(va)


def test_fit_ub_from_reflections(rs):
    c = _si(rs)
    _set(rs, mu=0, gamma=0, chi=90)
    rs.diffractometer.delta.set_limits(0, 180)
    rs.diffractometer.eta.set_limits(0, 180)
    ub = c._ub()
    for hkl in ([1, 1, 1], [0, 0, 4], [2, 0, 2]):
        sol = c.calc_angles(*hkl).iloc[0]
        c.add_reflection(*hkl, **{n: sol[n] for n in rc.POSITION_ORDER}, energy=ENERGY)
    with contextlib.redirect_stdout(io.StringIO()):
        c.fit_ub(refine_lattice=True)
    assert np.abs(c._ub() - ub).max() < 1e-9
    assert c.unit_cell()["a"] == pytest.approx(5.431, abs=1e-9)


def test_crystal_survives_reload(rs, tmp_path):
    _si(rs)
    again = RecSpace(diffractometer_you=rs.diffractometer, name="recspace", config_dir=tmp_path,
                     energy_source=lambda: ENERGY)
    assert again.crystals == ["si"]
    assert np.allclose(again.si._ub(), rs.si._ub())


def test_import_from_diffcalc(rs, tmp_path):
    src = tmp_path / "diffcalc_crystals"
    src.mkdir()
    U, _ = rc.calc_u(CELL, [], ORIENTATIONS)
    files = {"unit_cell": {"name": "x", **CELL}, "u_matrix": U.tolist(),
             "ub_matrix": (U @ rc.lattice_b(CELL)).tolist(), "orientations": ORIENTATIONS,
             "reflections": REFLECTIONS}
    for a, v in files.items():
        (src / f"tri_{a}.json").write_text(json.dumps({"value": v}))
    (src / "diffcalc_list.json").write_text(json.dumps({"value": {"tri": ["now", "xrd"]}}))
    with contextlib.redirect_stdout(io.StringIO()):
        assert rs.import_from_diffcalc(source_dir=src) == ["tri"]
    assert "tri" in rs.crystals
    assert np.allclose(rs.tri._ub(), files["ub_matrix"])
    assert rs.tri.reflections() == REFLECTIONS
    # the source is only read
    assert sorted(p.name for p in src.iterdir()) == sorted(
        [f"tri_{a}.json" for a in files] + ["diffcalc_list.json"])


def test_geom_page(rs):
    from eco.widgets.recspace_widget import geom_entries, geom_page

    c = _si(rs)
    _set(rs, mu=0, gamma=0, chi=90)
    entries = geom_entries(c, hkl=(1, 1, 1))
    groups = [e["group"] for e in entries]
    assert groups[0] == "position" and sum(g.startswith("solutions") for g in groups) == 4
    path = geom_page(c, hkl=(1, 1, 1), open_browser=False)
    text = path.read_text()
    assert "diff3d-payload" in text and "ENTRIES" in text


def test_reflections_app(rs):
    pytest.importorskip("ipywidgets")
    from eco.widgets.recspace_widget import ReflectionsApp

    c = _si(rs)
    _set(rs, mu=0, gamma=0, chi=90)
    app = ReflectionsApp(c, hkl=(1, 1, 1))
    assert len(app.sols) == 4
    app.select(1)
    assert "sol. 1" in app.payload_holder.value
    app.two.value = True                           # (0 0 1) as second reflection
    app.cons["chi"][1].value = False
    assert all("separation" in s for s in app.sols)
    app._store()
    assert rs.constraints.h2() == [0.0, 0.0, 1.0]
