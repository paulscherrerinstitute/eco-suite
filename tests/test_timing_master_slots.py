"""MasterEventSystem._get_slot_codes: an unconfigured slot is not a failure.

Measured on SIN-TIMAST-TMA (2026-09-06): exactly 74 of 256 event slots
exist. The other 182 have no record on the IOC - their PVs do not connect
even given 5 s on an idle network, and the count is identical inside and
outside `init_all()`. The old code could not tell that apart from a
timed-out read, so it retried all 182 three times (~15 s of every namespace
init) and then warned alarmingly about a completely normal configuration.
"""

import logging
import types

import pytest

evt = pytest.importorskip("eco.timing.event_timing_new_new")


class ProbePV:
    """A PV that connects only if its slot is in `configured`."""

    configured = set()

    def __init__(self, pvname, connection_timeout=None, auto_monitor=None):
        self.pvname = pvname
        slot = int(pvname.split("Evt-")[1].split("-")[0])
        self.connected = slot in ProbePV.configured


@pytest.fixture
def master(monkeypatch):
    m = evt.MasterEventSystem.__new__(evt.MasterEventSystem)
    m.pvname = "TEST-TMA"
    monkeypatch.setattr(evt, "PV", ProbePV)
    monkeypatch.setattr(evt.time, "sleep", lambda s: None)
    return m


def _install_caget(monkeypatch, table, flaky=()):
    """table: slot -> code (absent slot => None). `flaky` slots return None
    on the first read and their code afterwards."""
    seen = {"calls": 0}
    state = {s: 0 for s in flaky}

    def caget_many(pvs, timeout=None):
        seen["calls"] += 1
        out = []
        for pv in pvs:
            slot = int(pv.split("Evt-")[1].split("-")[0])
            if slot in state:
                state[slot] += 1
                out.append(table.get(slot) if state[slot] > 1 else None)
            else:
                out.append(table.get(slot))
        return out

    monkeypatch.setattr(evt, "caget_many", caget_many)
    return seen


def test_unconfigured_slots_are_skipped_without_retry_or_warning(
    master, monkeypatch, caplog
):
    table = {1: 10, 2: 20, 3: 30}                     # only 3 of 6 exist
    ProbePV.configured = set(table)
    seen = _install_caget(monkeypatch, table)

    with caplog.at_level(logging.DEBUG, logger=evt.__name__):
        slots, codes = master._get_slot_codes(slots=range(1, 7))

    assert sorted(codes) == [10, 20, 30]
    assert seen["calls"] == 1, "non-existent slots must not be retried"
    text = caplog.text
    assert "not configured" in text and "3 in use" in text
    assert "could not be read" not in text, "that would be the false alarm"


def test_a_connected_but_unread_slot_is_retried_and_warned_about(
    master, monkeypatch, caplog
):
    """The case the retry and the warning actually exist for."""
    table = {1: 10, 2: 20}
    ProbePV.configured = {1, 2, 3}          # slot 3 exists but never reads
    seen = _install_caget(monkeypatch, table)

    with caplog.at_level(logging.INFO):
        slots, codes = master._get_slot_codes(slots=range(1, 4), attempts=3)

    assert sorted(codes) == [10, 20]
    assert seen["calls"] == 3, "a connected slot should be retried"
    assert "connected but" in caplog.text and "could not be read" in caplog.text


def test_a_transient_read_recovers_on_retry(master, monkeypatch, caplog):
    table = {1: 10, 2: 20, 3: 30}
    ProbePV.configured = set(table)
    seen = _install_caget(monkeypatch, table, flaky=[3])

    with caplog.at_level(logging.INFO):
        slots, codes = master._get_slot_codes(slots=range(1, 4), attempts=3)

    assert sorted(codes) == [10, 20, 30], "the retry should have recovered slot 3"
    assert seen["calls"] == 2
    assert "could not be read" not in caplog.text


def test_everything_present_needs_no_probing_at_all(master, monkeypatch):
    table = {1: 10, 2: 20, 3: 30}
    ProbePV.configured = set(table)
    seen = _install_caget(monkeypatch, table)
    slots, codes = master._get_slot_codes(slots=range(1, 4))
    assert seen["calls"] == 1
    assert sorted(codes) == [10, 20, 30]
