"""Setpoint selection of eco.fel.beamline_config (no EPICS)."""

import re

import pytest

from eco.fel import beamline_config as bc

REC = bc.EMBEDDED_SETPOINT_RECORDS


def chosen(endstation, mode=None):
    return {(p.device, p.field): p.setpoint_record
            for p in bc.plan_axes(endstation, mode, records=REC)}


def original_selection(dev, records, bl, mode):
    """BeamLineConfig.py's moveConfig selection, transcribed (last match wins)."""
    sel = {}
    for pvn in records:
        ax, *t = pvn.split("_")
        if t[0] == "DB" or not re.fullmatch(r"[TR][XYZ]", ax):
            continue
        t = set(t)
        if "ES" + bl not in t and mode not in t:
            continue
        if dev.startswith("SAT") and "OUT" in t and sel.get(ax) is not None:
            continue
        sel[ax] = pvn
    return sel


@pytest.mark.parametrize("name,es", [("bernina", "B"), ("ESB", "B"), ("b", "B"),
                                     ("alvra", "A"), ("esf", "F"), ("furka", "F")])
def test_normalize_endstation(name, es):
    assert bc.normalize_endstation(name) == es


def test_normalize_mode():
    assert bc.normalize_mode("pink") == "PINK"
    assert bc.normalize_mode("Fix Offset") == "FIX"
    assert bc.normalize_mode("m") == "MONO"
    assert bc.normalize_mode(None) is None
    with pytest.raises(ValueError):
        bc.normalize_mode("white")
    with pytest.raises(ValueError):
        bc.normalize_endstation("esz")


def test_bernina_pink():
    c = chosen("bernina", "pink")
    assert c[("SAROP11-OOMH064", "W_X")] == "TX_ESB_ESC"
    assert c[("SAROP11-OOMH064", "W_RY")] is None  # only an ESA setpoint: stays
    assert c[("SAROP31-OOMH067", "W_X")] == "TX_ESB"
    assert c[("SAROP31-OOMH067", "W_RZ")] == "RZ_ESB"
    assert c[("SAROP21-OOMV092", "W_RX")] == "RX_PINK"
    assert c[("SAROP21-OOMV092", "W_Y")] == "TY_PINK"
    assert c[("SAROP21-OOMV096", "W_Y")] == "TY_PINK"


def test_bernina_fix_and_no_mode():
    c = chosen("bernina", "fix")
    assert c[("SAROP21-OOMV092", "W_RX")] == "RX_FIX"
    assert c[("SAROP21-OOMV096", "W_RX")] is None  # no FIX setpoints on 096
    c = chosen("bernina")
    assert c[("SAROP21-OOMV092", "W_RX")] is None
    assert c[("SAROP31-OOMH067", "W_RY")] == "RY_ESB"


def test_aperture_field_and_out_fallback():
    c = chosen("esd", "pink")
    assert c[("SATOP11-OAPU089", "MOTOR_Y")] is None
    assert c[("SATOP11-OOMV076", "W_Y")] == "TY_OUT"
    assert c[("SATOP11-OFMH091", "W_Y")] == "TY_ESD_PINK"


def test_esd_mono_picks_mono_setpoints():
    # BeamLineConfig.py took TY_ESD_PINK here (both match ESD, last wins)
    c = chosen("esd", "mono")
    assert c[("SATOP11-OFMH091", "W_RX")] == "RX_ESD_MONO"
    assert c[("SATOP11-OFMH091", "W_Y")] == "TY_ESD_MONO"
    orig = original_selection("SATOP11-OFMH091", REC["SATOP11-OFMH091"], "D", "MONO")
    assert orig["TY"] == "TY_ESD_PINK"


@pytest.mark.parametrize("es", "ABC")
@pytest.mark.parametrize("mode", [None, "PINK", "FIX", "VAR", "MONO"])
def test_aramis_same_as_original(es, mode):
    for dev in bc.BEAMLINE_DEVICES[es]:
        ours = {ax: sp for ax, (sp, _) in
                bc.select_setpoints(dev, REC[dev], es, mode).items() if sp}
        assert ours == original_selection(dev, REC[dev], es, mode), dev


def test_decide_moves():
    p = [bc.AxisPlan("D", "TX", "W_X", "TX_ESB", "TX_DB", setpoint=1.0, deadband=0.5, readback=1.2),
         bc.AxisPlan("D", "RY", "W_RY", "RY_ESB", "RY_DB", setpoint=1.0, deadband=0.5, readback=2.0),
         bc.AxisPlan("D", "RZ", "W_RZ", None, "RZ_DB", deadband=0.5, readback=3.0),
         bc.AxisPlan("D", "TY", "W_Y", "TY_ESB", None, setpoint=9.0, readback=0.0)]
    bc.decide_moves(p)
    assert [x.move for x in p] == [False, True, False, False]
    assert p[2].target == 3.0
    bc.decide_moves(p, force=True)
    assert [x.move for x in p] == [True, True, False, False]
    assert "MOVE" in bc.format_plan(p)


def test_yaml_file_matches_embedded(tmp_path):
    yaml = pytest.importorskip("yaml")
    f = tmp_path / "SetPoints.yaml"
    f.write_text(yaml.safe_dump({d: [[r, "desc", 0.0, "mm", 3] for r in recs]
                                 for d, recs in REC.items()}))
    assert bc.load_setpoint_records(str(f)) == REC
    assert bc.load_setpoint_records(str(tmp_path / "missing.yaml")) == REC


def test_available_modes():
    assert bc.available_modes("bernina", REC) == ("PINK", "FIX")
    assert bc.available_modes("alvra", REC) == ("PINK", "FIX", "VAR")
    assert bc.available_modes("cristallina", REC) == ()
    assert bc.available_modes("diavolezza", REC) == ("PINK", "MONO")
    assert bc.available_modes("furka", REC) == ("PINK",)
    assert bc.branch_of("esb") == "aramis" and bc.branch_of("maloja") == "athos"


def _filled(es, mode, offsets=None):
    plans = bc.plan_axes(es, mode, records=REC)
    for p in plans:
        p.setpoint = 1.0 if p.setpoint_record else None
        p.deadband = 0.1 if p.deadband_record else None
        p.readback = 1.0 + (offsets or {}).get(p.field, 0.0)
    return plans


def test_in_position():
    assert bc.in_position(_filled("bernina", "pink"))
    off = _filled("bernina", "pink", {"W_RX": 0.5})  # OOMV092/096 RX are pink-tagged
    assert not bc.in_position(off)
    assert not bc.in_position(off, mode_only=True)
    assert bc.in_position(_filled("bernina", None, {"W_RX": 0.5}))  # RX not checked w/o mode
    assert not bc.in_position(_filled("cristallina", None)[:0])


class _FakeBranch:
    branch = "aramis"

    def __init__(self):
        self.calls = []

    def _state(self):
        return {"endstation": "bernina", "mode": None}

    def switch(self, **kw):
        self.calls.append(kw)

    def stop(self):
        pass


def test_selector_enum_and_set():
    br = _FakeBranch()
    es = bc.BranchSelector(br, "endstation", name="endstation")
    mode = bc.BranchSelector(br, "mode", name="mode")
    assert es.enum_strs == ["alvra", "bernina", "cristallina"]
    assert mode.enum_strs == ["pink", "fix", "var"]
    assert es.get_current_value().name == "bernina"
    assert mode.get_current_value().name == "undefined"
    assert es.validate("ESB").name == "bernina"
    with pytest.raises(ValueError):
        es.validate("furka")
    with pytest.raises(ValueError):
        es.validate(0)
    with pytest.raises(ValueError):
        mode.validate("mono")
    es.set_target_value("alvra").wait()
    mode.set_target_value("Fix Offset").wait()
    assert br.calls == [{"endstation": "alvra"}, {"mode": "fix"}]
