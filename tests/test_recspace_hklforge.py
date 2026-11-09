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


def _lab(oris):
    """diffcalc (You frame) orientations as RecSpace (Bernina frame) ones."""
    return [{**o, "xyz": rc.from_you(o["xyz"]).tolist()} for o in oris]


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
    # diffcalc starts a primary-only U from U = 1 in its own frame
    U, _ = rc.calc_u(CELL, refls, _lab(ORIENTATIONS), idx1, idx2, U0=rc.YOU_TO_LAB)
    assert np.abs(U - rc.from_you(u.U)).max() < 1e-12


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
    assert np.abs(U - rc.from_you(newu)).max() < 1e-12
    # diffcalc uses hc = 12.3984 keV A here, so the lattice agrees to ~1e-5 only
    assert max(abs(lattice[k] - v) for k, v in zip(("a", "b", "c", "alpha", "beta", "gamma"), lat[1:])) < 1e-4

    pos = [0.2, 33, 1, 17, 42, 12]
    U0, UB0 = u.U.copy(), u.UB.copy()
    with contextlib.redirect_stdout(io.StringIO()):
        u.refine_ub([1, 1, 1], Position(*pos), rc.en2lam(ENERGY), False, True)
    _, M, _, _ = rc.refine_from_reflection(CELL, rc.from_you(U0), rc.from_you(UB0), [1, 1, 1],
                                           rc.position_to_angles(pos), ENERGY)
    assert np.abs(M @ rc.from_you(U0) - rc.from_you(u.U)).max() < 1e-12


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
    ub = rc.from_you(u.UB)
    rs = rc.solve(ub, ENERGY, (1, 1, 1), mode)
    wrap = lambda a: round(((a + 180) % 360) - 180, 5)
    dset = sorted(tuple(wrap(p.asdict["nu" if n == "gamma" else n]) for n in rc.POSITION_ORDER) for p, _ in dc)
    rset = sorted(tuple(wrap(s[n]) for n in rc.POSITION_ORDER) for s in rs)
    assert dset == rset and len(rset) > 0
    # virtual angles as diffcalc's
    for p, va in dc:
        a = {("gamma" if k == "nu" else k): v for k, v in p.asdict.items()}
        mine = rc.pseudo_angles(ub, a)
        for k in ("theta", "qaz", "alpha", "beta", "psi", "naz", "tau", "betain", "betaout"):
            assert abs(wrap(mine[k] - va[k])) < 1e-6, k


def test_wrap_into_limits():
    assert rc.wrap_into_limits(-170, (0, 360)) == 190
    assert rc.wrap_into_limits(10, (-180, 180)) == 10
    assert rc.wrap_into_limits(100, (-50, 50)) is None


def test_lab_directions_from_geometry():
    # Bernina: z+ beam, y+ up, x+ = y x z
    assert rc.lab_direction("beam") == [0, 0, 1]
    assert rc.lab_direction("up") == [0, 1, 0]
    assert rc.lab_direction("phi") == [-1, 0, 0]
    assert rc.lab_direction("-phi") == rc.lab_direction("x+") == [1, 0, 0]
    assert rc.lab_direction([0, 2, 0]) == [0, 1, 0]
    with pytest.raises(ValueError):
        rc.lab_direction("sideways")
    text = rc.frame_description()
    assert "z+   along the incoming X-ray beam" in text and "phi: axis along x-" in text
    assert np.allclose(np.cross(rc.UP, rc.BEAM), [1, 0, 0])


def test_orientation_by_name_equals_by_vector(tmp_path):
    ubs = []
    for i, (n, b) in enumerate((("x+", "beam"), ([1, 0, 0], [0, 0, 1]))):
        rs = RecSpace(name="recspace", config_dir=tmp_path / str(i), energy_source=lambda: ENERGY)
        c = rs.create_crystal("si", unit_cell=dict(a=5.431, b=5.431, c=5.431, alpha=90, beta=90, gamma=90))
        c.add_orientation([0, 0, 1], n)
        c.add_orientation([1, 1, 0], b)
        with contextlib.redirect_stdout(io.StringIO()):
            c.calc_ub()
        ubs.append(c._ub())
    assert np.allclose(*ubs)


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
    c.add_orientation([0, 0, 1], "up", tag="n")
    c.add_orientation([1, 1, 0], "beam")
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
    # matrices and directions turned into the Bernina frame, angles untouched
    assert np.allclose(rs.tri._ub(), rc.from_you(files["ub_matrix"]))
    assert np.allclose(rs.tri.orientations()[0]["xyz"], rc.from_you(ORIENTATIONS[0]["xyz"]))
    assert rs.tri.reflections() == REFLECTIONS
    for r in REFLECTIONS:
        assert np.allclose(_hkl_you(files["ub_matrix"], r["position"]),
                           rs.tri.calc_hkl(*r["position"], energy=ENERGY))
    # the source is only read
    assert sorted(p.name for p in src.iterdir()) == sorted(
        [f"tri_{a}.json" for a in files] + ["diffcalc_list.json"])


def _hkl_you(ub_you, position):
    """hkl at a position in diffcalc's own frame (hklforge's psic)."""
    from hklforge import Geometry

    a = dict(zip(("mu", "delta", "nu", "eta", "chi", "phi"), position))
    q = Geometry.builtin("psic").q_phi(a, rc.wavevector(ENERGY))
    return np.linalg.solve(np.asarray(ub_you, float), q)


def test_crystal_stored_in_you_frame_is_turned_once(tmp_path):
    """A crystal written before the frame was recorded (You frame) is turned
    into the Bernina frame on first load, and only once."""
    U, _ = rc.calc_u(CELL, [], ORIENTATIONS)                  # You-frame data
    ub_you = U @ rc.lattice_b(CELL)
    files = {"unit_cell": {"name": "x", **CELL}, "u_matrix": U.tolist(), "ub_matrix": ub_you.tolist(),
             "orientations": ORIENTATIONS, "reflections": REFLECTIONS,
             "vectors": {"reference": {"hkl": [1, 0, 0]}, "surface": {"phi": [0, 0, 1]}}}
    for a, v in files.items():
        (tmp_path / f"old_{a}.json").write_text(json.dumps({"value": v}))
    (tmp_path / "recspace_list.json").write_text(json.dumps({"value": {"old": ["now", "dummy"]}}))
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rs = RecSpace(name="recspace", config_dir=tmp_path, energy_source=lambda: ENERGY)
    assert "turned" in out.getvalue()
    pos = REFLECTIONS[1]["position"]
    assert np.allclose(rs.old.calc_hkl(*pos, energy=ENERGY), _hkl_you(ub_you, pos))
    assert rs.old.vectors()["surface"] == {"phi": rc.DEFAULT_SURFACE["phi"]}
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rs2 = RecSpace(name="recspace", config_dir=tmp_path, energy_source=lambda: ENERGY)
    assert "turned" not in out.getvalue()
    assert np.allclose(rs2.old._ub(), rs.old._ub())


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


def test_reflections_app_filters_presets_and_simulator(rs):
    pytest.importorskip("ipywidgets")
    from eco.widgets.recspace_widget import ReflectionsApp

    c = _si(rs)
    _set(rs, mu=0, gamma=0, chi=90)
    app = ReflectionsApp(c, hkl=(1, 1, 1))
    assert len(app.sols) == 4
    lo, hi = app.filters["eta"]
    lo.value, hi.value = "0", "180"                # min/max filter the solutions
    assert 0 < len(app.sols) < 4 and all(0 <= s["eta"] <= 180 for s in app.sols)
    app._store()                                   # circle min/max become the solution ranges
    assert rs.solution_ranges() == {"eta": [0.0, 180.0]}

    app.preset.value = "sd_horizontal"             # mu 0, eta 0, chi 90; gamma -150..10, delta -10..120
    assert app.preset.value == ""
    assert app.cons["eta"][1].value and not app.cons["gamma"][1].value
    assert app.filters["delta"][0].value == "-10" and app.filters["eta"][0].value == ""
    assert app.sols and all(-10 <= s["delta"] <= 120 for s in app.sols)

    app.select(0)
    app._sim_from_view()                           # simulator starts from the solution shown
    assert app.selected == app.SEL_SIM and "simulated" in app.payload_holder.value
    assert "1.000&nbsp;&nbsp;1.000&nbsp;&nbsp;1.000" in app.sim_out.value
    app.sim["delta"].value += 5                    # off Bragg: hkl follows
    assert "1.000&nbsp;&nbsp;1.000" not in app.sim_out.value and "off by" in app.sim_out.value


def test_parse_triple_as_people_write_it():
    for text in ("0 0 1", "0,0,1", "(0,0,1)", "[0, 0, 1]", "(001)"):
        assert rc.parse_triple(text) == [0, 0, 1], text
    assert rc.parse_triple("1-10") == [1, -1, 0]
    assert rc.parse_triple("phi") is None and rc.parse_triple("") is None
    assert rc.lab_direction("(0,1,0)") == rc.lab_direction("up")


def test_interactive_create_crystal(rs, monkeypatch):
    answers = iter(["", "3", "", "", "", "", "",
                    "(1,1,1)",                        # surface normal
                    "phi", "(0,0,1)", "phi",          # a lab word at the hkl question is explained, re-asked
                    "1,0,0", "up"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    with contextlib.redirect_stdout(io.StringIO()) as out:
        c = rs.create_crystal("cub")
    assert "where it points in the lab is asked next" in out.getvalue()
    assert "turning phi, then chi, then eta, then mu" in out.getvalue()
    o = c.orientations()
    assert o[0]["hkl"] == [0, 0, 1] and np.allclose(o[0]["xyz"], rc.lab_direction("phi"))
    assert o[1]["hkl"] == [1, 0, 0] and np.allclose(o[1]["xyz"], [0, 1, 0])
    # (001) along the phi axis at all angles 0: reads back as hkl there
    q = rc.GEOMETRY.sample_matrix({}) @ c._ub() @ [0, 0, 1]
    assert np.allclose(q / np.linalg.norm(q), rc.lab_direction("phi"))
    # the surface normal is its own: (111), not the first orientation
    assert c.vectors()["surface"] == {"hkl": [1.0, 1.0, 1.0]}


def test_interactive_crystal_without_surface(rs, monkeypatch):
    answers = iter(["", "3", "", "", "", "", "", "none", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    with contextlib.redirect_stdout(io.StringIO()):
        c = rs.create_crystal("bare")
    assert c._surface is None
    assert c.orientations()[0]["hkl"] == [0, 0, 1] and c.orientations()[1]["hkl"] == [1, 0, 0]


def test_surface_normal_independent_of_orientations(rs):
    c = _si(rs)                         # orientations (001) up, (110) beam
    assert c._surface is None           # nothing taken from the orientations
    assert "not defined" in c.summary()
    a = {"mu": 0, "delta": 30, "gamma": 0, "eta": 15, "chi": 90, "phi": 0}
    assert "betain" not in c.pseudo_angles(**a)
    with pytest.raises(ValueError, match="set_surface_normal"):
        c.calc_angles(1, 1, 1, constraints_update=dict(betain=2, mu=None, chi=None, eta=0, gamma=0))
    # a (110) surface on a crystal mounted with (001) up: grazing incidence on (110)
    with contextlib.redirect_stdout(io.StringIO()):
        c.set_surface_normal("(110)")
    assert "(1 1 0) in the crystal, along z+" in c.summary()
    free = {n: None for n in ("mu", "eta", "chi", "phi", "delta", "gamma", "qaz")}
    df = c.calc_angles(1, 1, 1, constraints_update={**free, "betain": 2, "mu": 0, "chi": 90})
    assert len(df)
    n = c._ub() @ [1, 1, 0]
    for _, row in df.iterrows():   # the beam meets the (110) planes at 2 degrees
        s = rc.GEOMETRY.sample_matrix({k: row[k] for k in rc.POSITION_ORDER}) @ n
        assert np.degrees(np.arcsin(-np.dot(rc.BEAM, s) / np.linalg.norm(s))) == pytest.approx(2, abs=1e-9)
    # given in the lab, fixed to the stage: kept when UB changes
    with contextlib.redirect_stdout(io.StringIO()):
        c.set_surface_normal(lab="up")
        c.calc_ub()
    assert c.vectors()["surface"] == {"phi": [0.0, 1.0, 0.0]} and "fixed to the stage, along y+" in c.summary()
    c.del_surface_normal()
    assert c._surface is None


def test_unreadable_angle_does_not_break_the_display(rs):
    c = _si(rs)
    c._diff_adjs.pop("eta")                       # as a FailedComponent eta, constraint eta unset
    c._diff_adjs_constrained["eta"] = rs.constraints.eta
    with pytest.raises(ValueError, match="eta"):
        c.calc_hkl()
    assert np.isnan(c.h.get_current_value())
    repr(c)
    assert len(c.calc_hkl(eta=0)) == 3


def test_set_constraints_from_preset(rs, monkeypatch):
    c = _si(rs)
    _set(rs, h2=[0, 0, 1], qaz=90)
    with contextlib.redirect_stdout(io.StringIO()):
        held = rs.set_constraints("thc")
    assert held == {"mu": 0.0, "chi": 90.0, "phi": 0.0}
    cons = rs._constraints()
    assert cons["h2"] is None and cons["qaz"] is None   # everything else released
    assert rs.solution_ranges() == {"delta": [45.0, 122.0]}
    # the ranges decide which solution a move picks; calc_angles shows all
    d = rs.diffractometer
    for n in ("mu", "delta", "gamma", "eta", "chi", "phi"):
        getattr(d, n).set_limits(-400, 400)
    with contextlib.redirect_stdout(io.StringIO()):
        rs.set_constraints("thc", ranges=dict(delta=(-10, 120), gamma=(-150, 10)))
        assert len(c.calc_angles(1, 1, 1)) == 4
        sol = c.calc_angles_unique(1, 1, 1)       # the only one with delta > -10 and gamma < 10
        assert -10 < sol["delta"] < 120 and sol["gamma"] < 10
        rs.set_constraints("thc", ranges=False)
        with pytest.raises(Exception, match="No unique solution"):
            c.calc_angles_unique(1, 1, 1)
    with contextlib.redirect_stdout(io.StringIO()):
        rs.set_constraints(mu=0, gamma=0, chi=90)
    assert rs.solution_ranges() == {}
    assert {k: v for k, v in rs._constraints().items() if v is not None and k != "h2_detector"} == \
        {"mu": 0, "gamma": 0, "chi": 90}
    # interactive: the list, then a choice by number
    names = list(rs.constraint_presets())
    monkeypatch.setattr("builtins.input", lambda prompt="": str(names.index("sd_horizontal")))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rs.set_constraints()
    assert "thc" in out.getvalue() and rs._constraints()["chi"] == 90.0 and rs._constraints()["eta"] == 0.0
    with pytest.raises(KeyError):
        rs.set_constraints("nonsense")


def test_store_constraint_preset(rs, tmp_path):
    with contextlib.redirect_stdout(io.StringIO()):
        rs.set_constraints(mu=0, eta=10, qaz=90, ranges=dict(delta=(0, 90)))
    rs.store_constraint_preset("mine", "test setup")
    assert json.loads((tmp_path / "constraint_presets.json").read_text())["value"]["mine"]["ranges"] == {"delta": [0.0, 90.0]}
    with contextlib.redirect_stdout(io.StringIO()):
        rs.set_constraints(mu=0, gamma=0, chi=90)
        rs.set_constraints("mine")
    assert rs._constraints()["qaz"] == 90 and rs.solution_ranges() == {"delta": [0.0, 90.0]}


def test_geom_page_without_ub_says_so(rs):
    c = rs.create_crystal("bare", unit_cell=dict(a=4.132, b=4.132, c=28.6, alpha=90, beta=90, gamma=120))
    c.add_orientation([0, 0, 1], "x+")
    c.add_orientation([1, 0, 0], "beam")
    with pytest.raises(ValueError, match=r"run bare.calc_ub\(\)"):
        c.widget_geom(open_browser=False)
    with contextlib.redirect_stdout(io.StringIO()):
        c.calc_ub()
    page = c.widget_geom(open_browser=False).read_text()
    assert "showCell: true" in page and '"ub"' in page


def test_append_recspace_never_breaks_the_diffractometer(monkeypatch, capsys):
    """recspace is optional: a broken import or a failing RecSpace only prints."""
    import sys
    append_recspace = pytest.importorskip("eco.endstations.bernina_diffractometers").append_recspace   # beamline deps

    class Obj:
        name = "xrd"
        appended = []

        def _append(self, *args, **kwargs):
            self.appended.append(kwargs["name"])

    obj = Obj()
    append_recspace(obj, {"diffcalc": True})
    assert obj.appended == ["recspace"]
    monkeypatch.setitem(sys.modules, "eco.utilities.recspace_hklforge", None)   # import fails
    append_recspace(obj, {"diffcalc": True})
    assert "recspace not added to xrd: ModuleNotFoundError" in capsys.readouterr().out
    monkeypatch.undo()

    class Failing(Obj):
        def _append(self, *args, **kwargs):
            raise RuntimeError("no motors")

    append_recspace(Failing(), {"diffcalc": True})
    assert "RuntimeError: no motors" in capsys.readouterr().out
