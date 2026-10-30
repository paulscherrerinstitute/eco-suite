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
