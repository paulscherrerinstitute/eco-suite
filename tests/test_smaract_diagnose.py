"""Parsing and verdict logic of eco.motion.smaract_diagnose (no EPICS)."""

import time

import pytest

from eco.motion import smaract_diagnose as sd


def state(n, msta=2306, updated=True, severity=0, prefix="SARES20-MCS1"):
    return {
        "pv": f"{prefix}:MOT_{n}",
        "connected": True,
        "description": f"ch{n}",
        "msta": msta,
        "msta_flags": sd.decode_msta(msta),
        "readback": 0.0,
        "last_update": time.time() if updated else 631152000.0,
        "record_alarm": "NO_ALARM",
        "record_severity": severity,
    }


def test_names():
    assert sd.split_pvname("SARES20-MCS1:MOT_12") == ("SARES20-MCS1", 12)
    assert sd.ioc_name("SARES20-MCS1") == "SARES20-CPCL-MCS001"
    assert sd.ioc_name("SARES20-MCS3") == "SARES20-CPCL-MCS003"


def test_decode_msta():
    assert sd.decode_msta(2306) == ["done", "encoder_present", "gain_support"]
    assert sd.decode_msta(2) == ["done"]
    assert sd.decode_msta(None) == []


def test_parse_and_describe_replies():
    assert sd._parse(":S0,3") == ("S", [0, 3])
    assert sd._parse(":N18") == ("N", [18])
    assert sd._describe(":S2,6") == "calibrating"
    assert sd._describe(":E0,129") == "error 129: no sensor present"
    assert sd._describe(":E0,999") == "error 999: unknown code"
    assert sd._describe(":ST4,1") == "1"
    assert sd._describe(":P4,-12345") == "-12345"


def test_query_refuses_anything_but_get_commands():
    for cmd in [":CS0", ":FRM0,2,60000,1", ":SST0,1", "GS0"]:
        with pytest.raises(ValueError):
            sd.query("SARES20-MCS1", cmd)


def test_healthy_channel_has_no_findings():
    assert sd.assess(state(4), ioc_start=time.time() - 3600) == []


def test_never_polled_channel():
    findings = sd.assess(state(1, msta=2, updated=False))
    assert len(findings) == 1
    assert "never updated" in findings[0]
    assert "IOC is restarted" in findings[0]


def test_stale_since_ioc_restart():
    s = state(1)
    s["last_update"] = time.time() - 7200
    findings = sd.assess(s, ioc_start=time.time() - 3600)
    assert any("never updated" in f for f in findings)


def test_problem_and_comm_bits():
    findings = sd.assess(state(4, msta=2306 | (1 << 9) | (1 << 12)))
    assert any("problem" in f for f in findings)
    assert any("communication" in f for f in findings)


def test_controller_answers_explain_the_channel():
    ctrl = {
        "status": "stopped",
        "sensor_type": "error 129: no sensor present",
        "position_nm": "error 129: no sensor present",
        "position_known": "error 129: no sensor present",
    }
    findings = sd.assess(state(1, msta=2, updated=False), ctrl=ctrl)
    assert any("controller sensor_type: error 129" in f for f in findings)


def test_controller_fine_but_ioc_not_polling():
    ctrl = {
        "status": "stopped",
        "sensor_type": "1",
        "position_nm": "100",
        "position_known": "1",
    }
    findings = sd.assess(state(1, msta=2, updated=False), ctrl=ctrl)
    assert any("restart the IOC" in f for f in findings)


def test_channel_beyond_controller_channel_count():
    findings = sd.assess(state(18), n_channels=15)
    assert any("only 15 channels" in f for f in findings)
    assert sd._n_channels({"channels": ":N15"}) == 15
    assert sd._n_channels({"channels": "query failed: x"}) is None


def test_module_summary_flags_whole_modules_only():
    results = []
    for n in range(1, 7):
        s = state(n, msta=2306 if n > 3 else 2, updated=n > 3)
        s["findings"] = sd.assess(s)
        results.append(s)
    lines = sd.module_summary(results)
    assert len(lines) == 1
    assert "module 1 (channels 1-3, addresses 0-2)" in lines[0]
    # one failing channel alone is not a module problem
    results[1]["findings"] = []
    assert sd.module_summary(results) == []


def test_expected_reply_prefixes():
    assert sd._expected_reply_prefixes(":GS3") == (":S3,", ":E3,")
    assert sd._expected_reply_prefixes(":GPPK11") == (":PPK11,", ":E11,")
    assert sd._expected_reply_prefixes(":GNC") is None
