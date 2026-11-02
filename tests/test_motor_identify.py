"""eco.devices_general.motor_identify: controller family from IOC boot files,
PowerBrick capture decoding, Schneider part numbers, the `_get` passthrough
(echo = no answer, "IS = ..." prefix), and the homing verdicts -- fakes built
from what the real drives answered on 2026-11-02."""

import pytest

from eco.devices_general import motor_identify as mi


class FakeGetPV:
    """A `_get` stringin: writing a mnemonic makes the drive's reply appear,
    or nothing changes (the record keeps the mnemonic) when it does not
    answer."""

    def __init__(self, replies, pvname="SARES20-DSD:DSDX_get"):
        self.pvname = pvname
        self.replies = replies
        self.value = ""
        self.puts = []

    def put(self, value, wait=True, timeout=None):
        self.puts.append(value)
        self.value = self.replies.get(value, value)

    def get(self, as_string=True, timeout=None):
        return self.value


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(mi.time, "sleep", lambda s: None)


def test_query_returns_reply():
    pv = FakeGetPV({"PN": "MDI3CRL34B7-EE-N-K"})
    assert mi.mcode_query(pv, "PN") == "MDI3CRL34B7-EE-N-K"


def test_query_echo_means_no_answer_and_is_retried():
    pv = FakeGetPV({})
    assert mi.mcode_query(pv, "S1") is None
    assert pv.puts == ["S1", "S1"]


def test_query_strips_echoed_name():
    # Lexium Motion Modules answer `PR IS` with "IS = 1, 2, 1"
    pv = FakeGetPV({"IS": "IS = 1, 2, 1"})
    assert mi.mcode_query(pv, "IS") == "1, 2, 1"


@pytest.mark.parametrize(
    "pn, line",
    [
        ("MDI3CRL34B7-EE-N-K", "MDrive Plus"),
        ("MFI3CRD17N4-EE", "MForce"),
        ("LMM-15-M", "Lexium Motion Module"),
        ("LMDCE421", "Lexium MDrive"),
        ("XYZ", None),
    ],
)
def test_schneider_model(pn, line):
    model = mi.schneider_model(pn)
    assert (model is None) if line is None else model.startswith(line)


@pytest.mark.parametrize(
    "ctrl, sel, text",
    [
        (10, 0, "home switch (active low)"),  # SARES21-CPPM-XRD3
        (2, 1, "+limit"),  # XRD2 detector slits
        (1, 0, "encoder index"),  # XRD4 axes 6, 7
        (3, 0, "encoder index and home switch"),
        (0, 0, "immediate (no reference)"),
    ],
)
def test_powerbrick_capture(ctrl, sel, text):
    assert mi.powerbrick_capture(ctrl, sel) == text


def _homing(inputs, encoder, inputs_known=True):
    ident = mi.MotorIdentity("X:MOT_1", online=True)
    mi._schneider_homing(ident, inputs, encoder, "1", inputs_known=inputs_known)
    return ident


def test_home_switch_makes_homf_work():
    # DSD X: S1 +limit, S2 -limit, S3 home (active low), S4 moving output
    ident = _homing({1: (2, 1), 2: (3, 1), 3: (1, 0), 4: (17, 0)}, encoder=True)
    assert ident.homing.record.startswith("HM")
    assert any(a.startswith("HI") for a in ident.homing.alternatives)
    assert ident.limits["switches"] == "+-"
    assert "S3 home switch (active low)" in ident.details["inputs"]


def test_without_home_input_homf_fails():
    # PDIM probe: only limits; its homing script borrows the -limit input
    ident = _homing({1: (2, 1), 2: (3, 1), 3: (0, 0), 4: (17, 0)}, encoder=True)
    assert ident.homing.record.startswith("fails")
    assert len(ident.homing.alternatives) == 2


def test_lmm_inputs_unknown_says_nothing_about_homf():
    ident = _homing({}, encoder=False, inputs_known=False)
    assert ident.homing.record == "unknown"
    assert ident.homing.alternatives == []
    assert "switches" not in ident.limits


BOOT = """
require motorIms
#drvAsynIPPortConfigure ("asynPort9","OLD:1002",0,0,0)
drvAsynIPPortConfigure ("asynPort2","SARES20-CMOV-DSD2:1002",0,0,0)
drvAsynSerialPortConfigure("asynPort3" ,"/dev/ttyM2",0,0,0)
ImsMDrivePlusCreateController("motPort2", "asynPort2", "1", 200, 5000)
ImsMDrivePlusCreateController("motPort3" , "asynPort3" , "1", 200, 5000)
"""


@pytest.fixture
def boot_root(tmp_path, monkeypatch):
    (tmp_path / "SARES20-CPCL-DSD").mkdir()
    (tmp_path / "SARES20-CPCL-DSD" / "startup.script_SARES20-CPCL-DSD").write_text(BOOT)
    monkeypatch.setattr(mi, "IOC_BOOT_ROOT", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize(
    "port, link",
    [("motPort2", "TCP SARES20-CMOV-DSD2:1002"), ("motPort3", "serial /dev/ttyM2")],
)
def test_boot_files_give_family_and_link(boot_root, port, link):
    ident = mi.MotorIdentity("SARES20-DSD:MOTOR_DSDX", ioc="SARES20-CPCL-DSD", asyn_port=port)
    mi._from_boot_files(ident)
    assert ident.family == "schneider"
    assert ident.driver == "ImsMDrivePlusCreateController"
    assert ident.link == link


def test_boot_files_missing_leaves_family_alone(boot_root):
    ident = mi.MotorIdentity("A:B", ioc="NOT-THERE", asyn_port="motPort2")
    mi._from_boot_files(ident)
    assert ident.family == "unknown"


def test_offline_report():
    assert str(mi.MotorIdentity("A:B")).endswith("offline")


def test_report_skips_empty_rows():
    ident = mi.MotorIdentity("A:B", online=True, family="smaract_mcs", encoder=True)
    text = str(ident)
    assert "controller  smaract_mcs" in text
    assert "firmware" not in text
