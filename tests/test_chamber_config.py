"""The THC/GIC chambers take cameras and motors from config_bernina
(thc_config / gic_config); a part missing there comes from the fallback dict
in bernina_sample_environments.py, with a warning. NamespaceComponent hands
such a config entry over: its dict when it is one, `default` when the key
does not exist yet."""

import logging

import pytest

from eco.elements.adj_obj import AdjustableObject
from eco.elements.adjustable import AdjustableFS
from eco.utilities.config import Namespace, NamespaceComponent

bse = pytest.importorskip("eco.endstations.bernina_sample_environments")


def test_an_old_option_list_takes_every_part_from_the_fallback(caplog):
    with caplog.at_level(logging.WARNING):
        parts = bse.chamber_config(
            ["cube"], "thc_config", bse.THC_CONFIG_FALLBACK, "THC_CONFIG_FALLBACK"
        )
    assert parts == bse.THC_CONFIG_FALLBACK
    assert parts["motors"] is not bse.THC_CONFIG_FALLBACK["motors"]  # a copy
    (message,) = caplog.messages
    assert message.startswith(
        "Attention, no thc_config cameras, motors, motors_cube, motors_ocb defined"
    )
    assert "THC_CONFIG_FALLBACK" in message and "bernina_sample_environments.py" in message
    assert bse.chamber_options(["cube"]) == ["cube"]


def test_parts_in_the_config_win_and_only_the_rest_is_warned_about(caplog):
    cameras = {"inline": {"pvname": "X:CAM", "serial_number": 1}}
    with caplog.at_level(logging.WARNING):
        parts = bse.chamber_config(
            {"cameras": cameras, "motors_openloop": {}}, "gic_config",
            bse.GIC_CONFIG_FALLBACK, "GIC_CONFIG_FALLBACK",
        )
    assert parts["cameras"] == cameras and parts["motors_openloop"] == {}
    assert parts["motors"] == bse.GIC_CONFIG_FALLBACK["motors"]
    assert "no gic_config motors defined" in caplog.text


def test_a_complete_config_is_silent(caplog):
    config = {"options": ["cube", "ottifant"], **bse.THC_CONFIG_FALLBACK}
    with caplog.at_level(logging.WARNING):
        bse.chamber_config(config, "thc_config", bse.THC_CONFIG_FALLBACK, "X")
    assert caplog.text == ""
    assert bse.chamber_options(config) == ["cube", "ottifant"]
    assert bse.chamber_options(None) == []


@pytest.fixture
def config_namespace(tmp_path):
    fs = AdjustableFS(str(tmp_path / "config.json"), name="_config_dict", default_value={
        "pgroup": "p00000",
        "thc_config": {"options": ["cube"], "cameras": {"inline": {"pvname": "X", "serial_number": 1}}},
        "old_style": ["cube"],
    })
    ns = Namespace(name="test")
    ns.append_obj(AdjustableObject, fs, name="config_bernina")
    return ns


def test_namespace_component_gives_a_dict_entry_as_dict(config_namespace):
    value = NamespaceComponent(
        config_namespace, "config_bernina.thc_config", get_current_value=True
    ).get()
    assert value["options"] == ["cube"] and value["cameras"]["inline"]["serial_number"] == 1
    old = NamespaceComponent(
        config_namespace, "config_bernina.old_style", get_current_value=True
    ).get()
    assert old == ["cube"]


def test_namespace_component_default_for_a_missing_entry(config_namespace):
    missing = NamespaceComponent(
        config_namespace, "config_bernina.gic_config", get_current_value=True, default=None
    )
    assert missing.get() is None
    with pytest.raises(AttributeError):
        NamespaceComponent(config_namespace, "config_bernina.gic_config", get_current_value=True).get()
