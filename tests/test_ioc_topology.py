"""eco.epics_utils.ioc_topology: the persisted "which IOC serves this
component" cache used by Namespace.init_all()'s per-IOC locks (see
tests/test_init_all_ioc_crosstalk.py) and its on-demand deep check
(check_object_crosstalk=True)."""

import pytest

from eco.epics_utils import ioc_topology


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch, tmp_path):
    """Every test gets its own empty, unloaded cache and its own state
    file - module-level globals would otherwise leak state between tests
    (and onto the real ~/.eco/ioc_topology.json)."""
    monkeypatch.setattr(ioc_topology, "IOC_TOPOLOGY_STATE_FILE", tmp_path / "ioc_topology.json")
    monkeypatch.setattr(ioc_topology, "_cache", {})
    monkeypatch.setattr(ioc_topology, "_loaded", False)
    monkeypatch.setattr(ioc_topology, "_dirty", False)
    yield


def test_unknown_component_has_no_cached_iocs():
    assert ioc_topology.known_iocs("nope") is None
    assert ioc_topology.has_been_checked("nope") is False


def test_record_and_read_back_iocs():
    ioc_topology.record_iocs("tt_kb", ["SARES20-CSSU-MF2"], n_pvs=12)

    assert ioc_topology.known_iocs("tt_kb") == frozenset({"SARES20-CSSU-MF2"})
    assert ioc_topology.has_been_checked("tt_kb")


def test_recording_an_empty_ioc_set_is_still_a_checked_result():
    """A component genuinely sharing nothing with anything is a real,
    useful answer - distinct from "never checked"."""
    ioc_topology.record_iocs("standalone_thing", [])

    assert ioc_topology.has_been_checked("standalone_thing")
    assert ioc_topology.known_iocs("standalone_thing") == frozenset()


def test_known_iocs_for_only_returns_checked_and_nonempty_entries():
    ioc_topology.record_iocs("tt_kb", ["SARES20-CSSU-MF2"])
    ioc_topology.record_iocs("clic", ["SARES20-CSSU-MF2"])
    ioc_topology.record_iocs("standalone_thing", [])

    result = ioc_topology.known_iocs_for(["tt_kb", "clic", "standalone_thing", "never_checked"])

    assert result == {
        "tt_kb": frozenset({"SARES20-CSSU-MF2"}),
        "clic": frozenset({"SARES20-CSSU-MF2"}),
    }


def test_persists_across_a_fresh_load(tmp_path, monkeypatch):
    ioc_topology.record_iocs("tt_kb", ["SARES20-CSSU-MF2"])

    # simulate a brand-new process: drop the in-memory cache, force a reload
    monkeypatch.setattr(ioc_topology, "_cache", {})
    monkeypatch.setattr(ioc_topology, "_loaded", False)

    assert ioc_topology.known_iocs("tt_kb") == frozenset({"SARES20-CSSU-MF2"})


def test_forget_removes_one_entry_and_clear_removes_everything():
    ioc_topology.record_iocs("tt_kb", ["SARES20-CSSU-MF2"])
    ioc_topology.record_iocs("clic", ["SARES20-CSSU-MF2"])

    assert ioc_topology.forget("tt_kb") is True
    assert ioc_topology.forget("tt_kb") is False  # already gone
    assert ioc_topology.has_been_checked("tt_kb") is False
    assert ioc_topology.has_been_checked("clic") is True

    ioc_topology.clear()
    assert ioc_topology.has_been_checked("clic") is False


class _FakeAlias:
    def __init__(self, channels):
        self._channels = channels

    def get_all(self):
        return [{"alias": f"a{i}", "channel": c} for i, c in enumerate(self._channels)]


class _FakeComponent:
    def __init__(self, channels):
        self.alias = _FakeAlias(channels)


def test_component_pv_names_walks_the_alias_tree():
    obj = _FakeComponent(["SARES20-MF2:MOT_1.RBV", "SARES20-MF2:MOT_2.RBV"])

    assert ioc_topology.component_pv_names(obj) == (
        "SARES20-MF2:MOT_1.RBV", "SARES20-MF2:MOT_2.RBV",
    )


def test_component_pv_names_of_an_object_without_an_alias_is_empty():
    assert ioc_topology.component_pv_names(object()) == ()


class _FakeIocMatch:
    def __init__(self, ioc):
        self.ioc = ioc


def test_discover_iocs_dedupes_by_prefix_and_records_the_result(monkeypatch):
    calls = []

    def fake_find_ioc(pattern, timeout=10.0):
        calls.append(pattern)
        return [_FakeIocMatch("SARES20-CSSU-MF2")]

    from eco.epics_utils import iocinfo
    monkeypatch.setattr(iocinfo, "find_ioc", fake_find_ioc)

    result = ioc_topology.discover_iocs(
        "tt_kb",
        ["SARES20-MF2:MOT_1.RBV", "SARES20-MF2:MOT_1.VAL", "SARES20-MF2:MOT_2.RBV"],
    )

    assert result == frozenset({"SARES20-CSSU-MF2"})
    assert calls == ["SARES20-MF2"]  # one call, not one per PV
    assert ioc_topology.known_iocs("tt_kb") == frozenset({"SARES20-CSSU-MF2"})


def test_discover_iocs_survives_one_prefix_failing(monkeypatch):
    def fake_find_ioc(pattern, timeout=10.0):
        if pattern == "BROKEN":
            raise RuntimeError("iocinfo unreachable")
        return [_FakeIocMatch("SOME-IOC")]

    from eco.epics_utils import iocinfo
    monkeypatch.setattr(iocinfo, "find_ioc", fake_find_ioc)

    result = ioc_topology.discover_iocs(
        "mixed", ["BROKEN:PV1", "OK:PV2"],
    )

    assert result == frozenset({"SOME-IOC"})


def test_discover_iocs_returns_none_and_records_nothing_if_everything_fails(monkeypatch):
    def fake_find_ioc(pattern, timeout=10.0):
        raise RuntimeError("iocinfo unreachable")

    from eco.epics_utils import iocinfo
    monkeypatch.setattr(iocinfo, "find_ioc", fake_find_ioc)

    result = ioc_topology.discover_iocs("tt_kb", ["SARES20-MF2:MOT_1.RBV"])

    assert result is None
    assert ioc_topology.has_been_checked("tt_kb") is False


def test_report_groups_components_by_shared_ioc():
    ioc_topology.record_iocs("tt_kb", ["SARES20-CSSU-MF2"])
    ioc_topology.record_iocs("clic", ["SARES20-CSSU-MF2"])
    ioc_topology.record_iocs("standalone_thing", ["SOME-OTHER-IOC"])

    rep = ioc_topology.report()

    assert rep["n_checked"] == 3
    assert rep["shared_iocs"] == {"SARES20-CSSU-MF2": ["clic", "tt_kb"]}
