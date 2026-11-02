"""eco.motion.deltatau_config.module_ioc: nothing reaches the PowerBrick
without the safety password, never while a module motor moves, and the
script gets the arguments ConfigMotorIOC.py expects."""

import pytest

from eco.motion.deltatau_config import module_ioc as mi


class Motor:
    def __init__(self, pvname):
        self.pvname = pvname


class Diffractometer:
    pvname = "SARES21-XRD"

    def __init__(self):
        self.eta_kap = Motor("SARES21-XRD:MOT_KAP_KRX")
        self.kappa = Motor("SARES21-XRD:MOT_KAP_KAP")
        self.xbase = Motor("SARES21-XRD:MOT_TX")  # base PowerBrick, not module


@pytest.fixture
def setup(monkeypatch, tmp_path):
    script = tmp_path / "ConfigMotorIOC.py"
    script.write_text(
        "m_lut={\n  'SARES21-XRD:MOT_KAP_KRX':('...',),\n"
        "  'SARES21-XRD:MOT_KAP_KAP':('...',),\n}\n"
    )
    monkeypatch.setattr(mi, "SCRIPT", str(script))
    state = {"connected": set(), "dmov": {}, "runs": [], "answer": "deltatau"}

    def ca_get(name, timeout):
        pv, field = name.rsplit(".", 1)
        if field == "DMOV":
            return state["dmov"].get(pv, 1)
        return pv if pv in state["connected"] else None

    def run_script(self, args):
        state["runs"].append(args)
        if "--cfg" in args:  # the IOC comes up with the new records
            cfg = args[args.index("--cfg") + 1]
            state["connected"] = {
                f"{self.pvname}:{m}" for m in mi.CONFIG_MOTORS[cfg]
            }

    monkeypatch.setattr(mi, "_ca_get", ca_get)
    monkeypatch.setattr(mi.ModuleIoc, "_run_script", run_script)
    monkeypatch.setattr("builtins.input", lambda prompt="": state["answer"])
    monkeypatch.setattr(mi.time, "sleep", lambda s: None)
    return mi.ModuleIoc(Diffractometer()), state


def test_loaded_from_channel_access(setup):
    ioc, state = setup
    assert ioc.loaded() is None
    state["connected"] = {"SARES21-XRD:MOT_TBL_TX"}
    assert ioc.loaded() == "stage"
    assert "SARES21-CPPM-XRD1" in repr(ioc)


def test_load_runs_script_and_waits_for_records(setup):
    ioc, state = setup
    ioc.load("kappa")
    assert state["runs"] == [["--host", "XRD", "--cfg", "kappa", "-m", "0x2"]]
    assert ioc.loaded() == "kappa"


def test_load_needs_the_safety_password(setup):
    ioc, state = setup
    state["answer"] = "yes"
    with pytest.raises(RuntimeError, match="not confirmed"):
        ioc.load("kappa")
    assert state["runs"] == []


def test_load_refuses_while_a_module_motor_moves(setup):
    ioc, state = setup
    state["dmov"]["SARES21-XRD:MOT_KAP_KAP"] = 0
    with pytest.raises(RuntimeError, match="moving"):
        ioc.load("kappa", force=True)
    assert state["runs"] == []
    # a moving motor on the base PowerBrick is not affected by the restart
    state["dmov"] = {"SARES21-XRD:MOT_TX": 0}
    ioc.load("kappa", force=True)


def test_unknown_config_is_rejected(setup):
    ioc, state = setup
    with pytest.raises(ValueError):
        ioc.load("kappaSwOl", force=True)


def test_open_loop(setup):
    ioc, state = setup
    d = ioc._diffractometer
    with pytest.raises(RuntimeError, match="kappa is not loaded"):
        ioc.open_loop(d.eta_kap, force=True)
    state["connected"] = {f"SARES21-XRD:{m}" for m in mi.CONFIG_MOTORS["kappa"]}
    with pytest.raises(ValueError, match="no open-loop setting"):
        ioc.open_loop("SARES21-XRD:MOT_KAP_DRX", force=True)
    ioc.open_loop(d.eta_kap, "SARES21-XRD:MOT_KAP_KAP", force=True)
    assert state["runs"] == [
        [
            "--host", "XRD", "--noenc",
            "SARES21-XRD:MOT_KAP_KRX,SARES21-XRD:MOT_KAP_KAP", "-m", "0x0",
        ]
    ]
