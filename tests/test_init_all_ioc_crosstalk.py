"""Namespace.init_all()'s use of eco.epics_utils.ioc_topology:

* `_run_init_pass` must serialize two components against each other when
  they are *known* (from the cache) to share a physical IOC, and must leave
  everything else exactly as concurrent as before.
* `init_all(check_object_crosstalk=True)` is the on-demand deep check that
  populates that cache for newly-built, never-checked components, and must
  never do so unless asked.
"""

import threading
import time

import pytest

from eco.epics_utils import ioc_topology
from eco.utilities.config import Namespace


@pytest.fixture(autouse=True)
def _isolated_ioc_topology_cache(monkeypatch, tmp_path):
    """No test here may read or write the real per-user ioc_topology.json."""
    monkeypatch.setattr(ioc_topology, "IOC_TOPOLOGY_STATE_FILE", tmp_path / "ioc_topology.json")
    monkeypatch.setattr(ioc_topology, "_cache", {})
    monkeypatch.setattr(ioc_topology, "_loaded", False)
    monkeypatch.setattr(ioc_topology, "_dirty", False)
    yield


class DependencyThing:
    def __init__(self, name=None):
        self.name = name


def _spy_on_init_name(ns):
    """Track how many of ns.init_name's calls are in flight at once."""
    active = {"n": 0, "max": 0}
    guard = threading.Lock()
    original = ns.init_name

    def spy(name, **kwargs):
        with guard:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.05)
        try:
            return original(name, **kwargs)
        finally:
            with guard:
                active["n"] -= 1

    ns.init_name = spy
    return active


def test_names_sharing_a_cached_ioc_never_build_concurrently(monkeypatch):
    monkeypatch.setattr(
        ioc_topology, "known_iocs_for",
        lambda names: {
            "tt_kb": frozenset({"SARES20-CSSU-MF2"}),
            "clic": frozenset({"SARES20-CSSU-MF2"}),
        },
    )

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="tt_kb")
    ns.append_obj(DependencyThing, lazy=True, name="clic")
    active = _spy_on_init_name(ns)

    ns.init_all(required_only=False, background=False, silent=True, max_workers=4)

    assert active["max"] == 1
    assert ns.initialized_names == {"tt_kb", "clic"}


def test_names_sharing_no_known_ioc_still_build_concurrently(monkeypatch):
    monkeypatch.setattr(ioc_topology, "known_iocs_for", lambda names: {})

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="a")
    ns.append_obj(DependencyThing, lazy=True, name="b")
    active = _spy_on_init_name(ns)

    ns.init_all(required_only=False, background=False, silent=True, max_workers=4)

    assert active["max"] == 2


def test_a_third_unrelated_name_is_not_held_up_by_a_shared_ioc_pair(monkeypatch):
    """Only the two names that actually share an IOC serialize - a third,
    unrelated one must still run concurrently with them."""
    monkeypatch.setattr(
        ioc_topology, "known_iocs_for",
        lambda names: {
            "tt_kb": frozenset({"SARES20-CSSU-MF2"}),
            "clic": frozenset({"SARES20-CSSU-MF2"}),
        },
    )

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="tt_kb")
    ns.append_obj(DependencyThing, lazy=True, name="clic")
    ns.append_obj(DependencyThing, lazy=True, name="unrelated")
    active = _spy_on_init_name(ns)

    ns.init_all(required_only=False, background=False, silent=True, max_workers=4)

    # tt_kb/clic serialize (max 1 of that pair at a time), but "unrelated"
    # still overlaps with whichever of them is running - so overall more
    # than one name is in flight at once.
    assert active["max"] == 2


def test_no_cached_entries_behaves_exactly_like_before_this_existed(monkeypatch):
    """An empty/never-populated cache (the state of every namespace before
    check_object_crosstalk has ever run) must not change behaviour at all."""
    monkeypatch.setattr(ioc_topology, "known_iocs_for", lambda names: {})

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="a")

    ns.init_all(required_only=False, background=False, silent=True)

    assert ns.initialized_names == {"a"}


def test_a_broken_ioc_topology_lookup_does_not_break_init_all(monkeypatch):
    """Best-effort: any failure reading the cache must not stop the pass."""
    def boom(names):
        raise RuntimeError("cache unreadable")

    monkeypatch.setattr(ioc_topology, "known_iocs_for", boom)

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="a")

    ns.init_all(required_only=False, background=False, silent=True)

    assert ns.initialized_names == {"a"}


def test_check_object_crosstalk_is_off_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ioc_topology, "discover_iocs",
        lambda *a, **k: calls.append(a) or frozenset(),
    )

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="tt_kb")
    ns.init_all(required_only=False, background=False, silent=True)

    assert calls == []


def test_check_object_crosstalk_only_checks_newly_built_uncached_names(monkeypatch):
    monkeypatch.setattr(ioc_topology, "has_been_checked", lambda name: name == "clic")
    monkeypatch.setattr(ioc_topology, "component_pv_names", lambda obj: ("FAKE:PV",))
    checked = []
    monkeypatch.setattr(
        ioc_topology, "discover_iocs",
        lambda name, pvs, **k: checked.append(name) or frozenset(),
    )

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="tt_kb")
    ns.append_obj(DependencyThing, lazy=True, name="clic")

    ns.init_all(
        required_only=False, background=False, silent=True,
        check_object_crosstalk=True,
    )

    assert checked == ["tt_kb"]


def test_check_object_crosstalk_skips_a_component_with_no_pvs(monkeypatch):
    monkeypatch.setattr(ioc_topology, "has_been_checked", lambda name: False)
    monkeypatch.setattr(ioc_topology, "component_pv_names", lambda obj: ())
    checked = []
    monkeypatch.setattr(
        ioc_topology, "discover_iocs",
        lambda name, pvs, **k: checked.append(name) or frozenset(),
    )

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="tt_kb")

    ns.init_all(
        required_only=False, background=False, silent=True,
        check_object_crosstalk=True,
    )

    assert checked == []


def test_check_object_crosstalk_never_raises_out_of_init_all(monkeypatch):
    monkeypatch.setattr(ioc_topology, "has_been_checked", lambda name: False)

    def boom(obj):
        raise RuntimeError("no alias here")

    monkeypatch.setattr(ioc_topology, "component_pv_names", boom)

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="tt_kb")

    ns.init_all(
        required_only=False, background=False, silent=True,
        check_object_crosstalk=True,
    )

    assert ns.initialized_names == {"tt_kb"}
