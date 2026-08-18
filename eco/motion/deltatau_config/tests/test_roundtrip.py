"""Offline tests (no hardware): .cfg round-trip, bundle round-trip, registries.

Run with::

    python -m pytest eco/motion/deltatau_config/tests/test_roundtrip.py
    # or without pytest:
    python eco/motion/deltatau_config/tests/test_roundtrip.py
"""

import glob
import os

from eco.motion.deltatau_config.bundle import (
    CONFIGS,
    HOSTS,
    ConfigBundle,
    resolve_config,
    resolve_host,
)
from eco.motion.deltatau_config.cfgfile import parse_cfg, parse_cfg_file

CONFIG_ROOT = "/sf/bernina/config/src/python/ConfigMotorIOC"


def _sample_cfgs():
    return sorted(glob.glob(os.path.join(CONFIG_ROOT, "*", "*.cfg")))


def test_cfg_roundtrip_is_byte_stable():
    """Parsing then serialising a .cfg must reproduce the original exactly."""
    for path in _sample_cfgs():
        with open(path) as fh:
            original = fh.read()
        assert parse_cfg(original).to_text() == original, path


def test_cfg_dict_view_groups_axes():
    """The stageXYZ .cfg exposes per-axis motor/encoder argument dicts."""
    path = os.path.join(CONFIG_ROOT, "stageXYZ", "stageXYZ.cfg")
    d = parse_cfg_file(path).to_dict()
    assert 1 in d["axes"]
    assert d["axes"][1]["motor"]["dirCur"] == 1000
    assert d["axes"][1]["encoder"]["enc"] == 1
    # holding_current addresses many motors -> lives in globals, not axes
    assert any(g["template"] == "holding_current" for g in d["globals"])


def test_cfg_set_axis_regenerates_line():
    path = os.path.join(CONFIG_ROOT, "stageXYZ", "stageXYZ.cfg")
    cfg = parse_cfg_file(path)
    cfg.set_axis(1, "motor", dirCur=1234)
    assert cfg.to_dict()["axes"][1]["motor"]["dirCur"] == 1234
    assert "dirCur=1234" in cfg.to_text()


def test_bundle_dict_roundtrip():
    b = ConfigBundle(base="/x", add_device="a.cmd", files=["*"])
    assert ConfigBundle.from_dict(b.to_dict()) == b
    assert ConfigBundle.from_tuple(b.to_tuple()) == b


def test_registries_match_original():
    assert resolve_host("GPS")[0] == "SARES22-CPPM-GPS1"
    assert resolve_host("XRD")[0] == "SARES21-CPPM-XRD1"
    assert resolve_host("EXP1")[0] == "SARES20-CPPM-EXP1"
    assert resolve_config("stageXYZ").add_device == "add_deviceStageXYZ.cmd"
    # full hostname passes through untouched
    assert resolve_host("SARES20-CPPM-EXP1")[0] == "SARES20-CPPM-EXP1"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all offline tests passed")
