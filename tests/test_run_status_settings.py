"""Run settings after the collection rework (2025-11).

The rework replaced the separate settings_collection by a "settings"
selection of the status_collection, and get_status() stopped returning a
top-level "settings" dict. Nothing on the reading side followed:

- get_status(base=None, selections=[...]) named the selection relative to
  the assembly but the status keys relative to `base`, so every selection
  came back empty for the run status writers' base=None;
- run_status(par_type="settings") / apply_run_settings still looked up the
  old top-level "settings" key, which no run written since has;
- Assembly.settings() raised KeyError.

Since then a run's status.json carries only "status"; which of those items
are settings is known to the live assembly, which picks them out.
"""
import json

import pytest

from eco.acquisition import scan_data
from eco.elements import memory
from eco.elements.adjustable import DummyAdjustable
from eco.elements.assembly import Assembly


@pytest.fixture(autouse=True)
def _no_memory(monkeypatch):
    # never create memory directories, least of all in a shared tree
    monkeypatch.setattr(memory, "global_memory_dir", None)


class Dev(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(DummyAdjustable, name="pos", is_setting=True)
        self._append(DummyAdjustable, name="readback", is_setting=False)


class Top(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(Dev, name="mono", is_setting=True, is_display="recursive")
        self._append(Dev, name="mono2", is_setting=True, is_display="recursive")


def test_settings_selection_is_named_like_the_status_for_base_none():
    top = Top(name="bernina")

    st = top.get_status(base=None, selections=["settings"])

    assert st["selections"]["settings"] == {
        "bernina.mono.pos": 0,
        "bernina.mono2.pos": 0,
    }


def test_settings_selection_relative_to_self_is_unchanged():
    # what Memory relies on (it always passes base=<the object itself>)
    top = Top(name="bernina")

    st = top.get_status(base=top, selections=["settings"])

    assert st["selections"]["settings"] == {"mono.pos": 0, "mono2.pos": 0}


def test_assembly_settings_lists_only_settings():
    top = Top(name="bernina")

    s = top.mono.settings(get_string=True)

    assert "pos" in s
    assert "readback" not in s


class _Pgroup:
    def get_current_value(self):
        return "p00000"


def _write_run(tmp_path, run, block):
    aux = tmp_path / "p00000" / "raw" / f"run{run:04d}" / "aux"
    aux.mkdir(parents=True)
    (aux / "status.json").write_text(json.dumps({"status_run_start": block}))


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_data, "STATUS_DATA", {})
    status = {
        "bernina.mono.pos": 1.5,
        "bernina.mono.readback": 1.49,
        "bernina.mono2.pos": 7.0,
        "bernina.mono2.readback": 6.9,
    }
    # 1: before the rework, a separate top-level "settings" dict
    _write_run(tmp_path, 1, {"status": status, "settings": {"bernina.mono.pos": 1.0}})
    # 2: since the rework, as actually written: status only
    _write_run(tmp_path, 2, {"status": status, "selections": {}})
    # 3: get_status(selections=["settings"]) output
    _write_run(
        tmp_path,
        3,
        {"status": status, "selections": {"settings": {"bernina.mono.pos": 3.0}}},
    )
    scan_data.StatusData(_Pgroup(), path_search=str(tmp_path / "{pgroup:s}" / "raw"))
    return status


def test_old_files_keep_their_recorded_settings(runs):
    top = Top(name="bernina")

    assert top.mono.run_status.dict(1, par_type="settings") == {1: {"pos": 1.0}}


def test_status_only_files_take_settings_membership_from_the_live_object(runs):
    top = Top(name="bernina")

    assert top.mono.run_status.dict(2, par_type="settings") == {2: {"pos": 1.5}}


def test_stored_settings_selection_is_used(runs):
    top = Top(name="bernina")

    assert top.mono.run_status.dict(3, par_type="settings") == {3: {"pos": 3.0}}


def test_a_name_prefix_does_not_pull_in_a_sibling(runs):
    # "bernina.mono" must not match "bernina.mono2.*"
    top = Top(name="bernina")

    assert top.mono.run_status.dict(2) == {2: {"pos": 1.5, "readback": 1.49}}


def test_run_status_view_marks_settings(runs):
    top = Top(name="bernina")

    view = top.mono.run_status(2, include_hidden=True)

    assert view._settings_names() == {"pos"}


def test_apply_run_settings_recalls_only_settings(runs, monkeypatch):
    top = Top(name="bernina")
    recalled = {}

    class _Mem:
        def recall(self, input_obj=None, **kwargs):
            recalled.update(input_obj)

    monkeypatch.setattr(top.mono, "memory", _Mem(), raising=False)

    top.mono.apply_run_settings(2)

    assert recalled == {"settings": {"pos": 1.5}}
