"""eco.web.viewmodels / backends: what the web client is told -- all of it
plain JSON, built from real Assemblies (the demo namespace)."""
import enum
import json

import numpy as np
import pytest

from eco.web import viewmodels
from eco.web.backends import NotFound, SessionBackend
from eco.web.demo import DemoNamespace


class Color(enum.Enum):
    red = 0
    green = 1


@pytest.mark.parametrize(
    "value, expected",
    [
        (1.5, {"text": "1.5", "num": 1.5}),
        (3, {"text": "3", "num": 3}),
        (np.float64(2.0), {"text": "2.0", "num": 2.0}),
        (np.int32(7), {"text": "7", "num": 7}),
        (float("nan"), {"text": "nan"}),
        (True, {"text": "True", "bool": True}),
        (np.bool_(False), {"text": "False", "bool": False}),
        ("open", {"text": "open", "cls": "green"}),
        ("Closed ", {"text": "Closed ", "cls": "red"}),
        (Color.green, {"text": "green", "enum": ["red", "green"]}),
    ],
)
def test_encode_value(value, expected):
    encoded = viewmodels.encode_value(value)
    assert encoded == expected
    json.dumps(encoded)


def test_encode_value_clips_long_text():
    encoded = viewmodels.encode_value(np.arange(1000))
    assert len(encoded["text"]) == viewmodels.MAX_TEXT
    json.dumps(encoded)


@pytest.mark.parametrize(
    "sent, last, expected",
    [("2.5", 1.0, 2.5), (3, 1.0, 3.0), ("4", 1, 4), ("on", False, True), ("green", Color.red, "green"), ("x", "y", "x")],
)
def test_coerce_target_follows_the_last_readback(sent, last, expected):
    result = viewmodels.coerce_target(sent, last)
    assert result == expected and type(result) is type(expected)


def test_coerce_target_rejects_a_fraction_for_an_int():
    with pytest.raises(ValueError):
        viewmodels.coerce_target("2.5", 1)


@pytest.fixture(scope="module")
def backend():
    return SessionBackend(DemoNamespace())


def test_names_report_states_and_failure_reasons(backend):
    names = backend.names()
    assert {"mono", "lxt"} <= set(names["initialized"])
    assert "vacuum" in names["lazy"] or "vacuum" in names["initialized"]
    assert "IOC not reachable" in names["failed"]["broken"]
    json.dumps(names)


def test_describe_an_assembly(backend):
    view, objects = viewmodels.describe(backend.resolve("mono"), "mono")
    json.dumps(view)
    kinds = {row["label"]: row["kind"] for row in view["rows"]}
    assert kinds == {"energy": "adjustable", "bragg": "adjustable", "pitch_rbv": "detector",
                     "harmonic": "adjustable", "home_all": "trigger"}
    energy = next(r for r in view["rows"] if r["label"] == "energy")
    assert energy["name"] == "mono.energy" and energy["unit"] == "keV"
    assert objects["mono.energy"].get_current_value() == 7.0
    assert not view["svg"]


def test_describe_lists_non_display_items_as_hidden(backend):
    view, _ = viewmodels.describe(backend.resolve("slits"), "slits")
    hidden = [r["label"] for r in view["rows"] if r.get("hidden")]
    assert hidden == ["blade_offset"]


def test_describe_a_single_item(backend):
    view, objects = viewmodels.describe(backend.resolve("lxt"), "lxt")
    assert view["kind"] == "adjustable" and [r["name"] for r in view["rows"]] == ["lxt"]
    assert "lxt" in objects


def test_resolve_initializes_a_lazy_component(backend):
    backend.resolve("vacuum")
    assert "vacuum" in backend.names()["initialized"]


def test_resolve_dotted_names_and_refuses_private_ones(backend):
    assert backend.resolve("mono.energy").get_current_value() == 7.0
    with pytest.raises(NotFound):
        backend.resolve("mono._append")
    with pytest.raises(NotFound):
        backend.resolve("mono.nothing")
    with pytest.raises(NotFound):
        backend.resolve("nothing")
    with pytest.raises(NotFound):
        backend.resolve("broken")


def test_svg_panel(backend):
    vacuum = backend.resolve("vacuum")
    assert viewmodels.has_svg_panel(vacuum)
    assert not viewmodels.has_svg_panel(backend.resolve("mono"))
    text, prefix = backend.svg(vacuum)
    assert text.startswith("<svg") and 'onclick="// eco: valve1"' in text
    assert prefix == "vacuum"


def test_nested_assemblies_follow_the_display_rules(backend):
    view, objects = viewmodels.describe(backend.resolve("sample"), "sample")
    rows = {r["name"]: r for r in view["rows"]}
    assert rows["sample.gonio"]["kind"] == "assembly" and not rows["sample.gonio"].get("hidden")  # its own view
    assert not rows["sample.cryo.temperature"].get("hidden")  # recursive: inline
    assert rows["sample.cryo"].get("hidden")
    inner, _ = viewmodels.describe(objects["sample.gonio"], "sample.gonio")
    assert [r["label"] for r in inner["rows"]] == ["omega", "chi", "phi"]


def test_a_motor_record_row_has_its_value_and_jog(backend):
    # an Assembly that is itself an Adjustable (MotorRecord): the grids show
    # its value and controls on the row, next to the link into it
    view, objects = viewmodels.describe(backend.resolve("stage"), "stage")
    x = next(r for r in view["rows"] if r["name"] == "stage.x")
    assert x["kind"] == "assembly" and x["value"] == "adjustable" and x["writable"]
    assert x["jog"] == {"speed": "stage.x.jog_speed"} and x["unit"] == "mm"
    assert objects["stage.x.jog_speed"] is objects["stage.x"].jog_speed
    diode = next(r for r in view["rows"] if r["name"] == "stage.diode")
    assert "jog" not in diode and "value" not in diode


def test_a_motor_record_view_starts_with_its_own_value(backend):
    view, objects = viewmodels.describe(backend.resolve("stage").x, "stage.x")
    own = view["rows"][0]
    assert own["name"] == "stage.x" and own["own"] and own["kind"] == "adjustable" and own["jog"]
    assert [r["name"] for r in view["rows"]].count("stage.x") == 1
    assert objects["stage.x"] is backend.resolve("stage").x


def test_items_without_a_hardware_jog_offer_none(backend):
    assert viewmodels.jog_speed_item(backend.resolve("mono").energy) is None
    assert viewmodels.jog_speed_item(backend.resolve("stage").x) is not None


def test_svg_panels_are_built_once_for_clients_close_together(backend):
    vacuum = backend.resolve("vacuum")
    calls = []
    original = type(vacuum)._widget_svg_panel

    def counting(self, path=None, live=True):
        calls.append(path)
        return original(self, path=path, live=live)

    type(vacuum)._widget_svg_panel = counting
    try:
        backend._svg_cache.clear()
        first = backend.svg(vacuum)
        assert backend.svg(vacuum) == first
        assert len(calls) == 1 and "eco_web" in calls[0]  # its own file, not the Qt panel's
    finally:
        type(vacuum)._widget_svg_panel = original
