"""EvrOutput._resolve_pulser: an out-of-range pulser number is routine IOC
state (an unwired output), not a per-output failure.

`init_all()` on the real Bernina EVR0 hits this on a few dozen outputs at
once, all wired to the sentinel 65535 -- see the module docstring in
`eco.timing.event_timing_new_new`. Two things follow from that being routine:
the message should not be shown by default (WARNING -> DEBUG, matching the
"unconfigured slot" treatment in `MasterEventSystem._get_slot_codes`), and it
should not cost a fresh `DummyPulser` (eight `AdjustableMemory` children) per
occurrence -- one shared, lazily-built instance is enough.
"""

import logging

import pytest

evt = pytest.importorskip("eco.timing.event_timing_new_new")


@pytest.fixture(autouse=True)
def _reset_shared_dummy(monkeypatch):
    monkeypatch.setattr(evt, "_shared_dummy_pulser", None)


def _make_output(monkeypatch, number=65535):
    monkeypatch.setattr(
        evt, "read_pv_value", lambda *a, **k: number
    )
    output = evt.EvrOutput.__new__(evt.EvrOutput)
    output.name = "output_test"
    output.pv_base = "TEST:Output"
    output._pulsers = [object() for _ in range(24)]
    return output


def test_out_of_range_pulser_returns_the_shared_dummy(monkeypatch):
    output_a = _make_output(monkeypatch)
    output_b = _make_output(monkeypatch)

    pulser_a = output_a._resolve_pulser(None, "pulserA")
    pulser_b = output_b._resolve_pulser(None, "pulserB")

    assert isinstance(pulser_a, evt.DummyPulser)
    assert pulser_a is pulser_b, "one shared instance, not one per output"
    assert pulser_a is evt._get_shared_dummy_pulser()


def test_out_of_range_pulser_is_not_shown_by_default(monkeypatch, caplog):
    output = _make_output(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=evt.__name__):
        output._resolve_pulser(None, "pulserA")

    assert caplog.text == "", "should be invisible at the default log level"

    with caplog.at_level(logging.DEBUG, logger=evt.__name__):
        output._resolve_pulser(None, "pulserA")

    assert "does not address any of the" in caplog.text
