"""All runs' status as one table (eco.acquisition.run_store), and its use
from StatusData and obj.run_status.table()/.plot()."""
import json
import os
import sys
from datetime import datetime

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pytest

from eco.acquisition import run_store, scan_data
from eco.acquisition.run_store import RunStore, select_runs
from eco.elements import memory
from eco.elements.adjustable import AdjustableVirtual, DummyAdjustable
from eco.elements.assembly import Assembly


@pytest.fixture(autouse=True)
def _no_memory(monkeypatch):
    monkeypatch.setattr(memory, "global_memory_dir", None)


def _write(root, where, run, status, settings=None, mtime=None, scan_info=None, raw_text=None):
    aux = root / where / f"run{run:04d}" / "aux"
    aux.mkdir(parents=True, exist_ok=True)
    p = aux / "status.json"
    if raw_text is not None:
        p.write_text(raw_text)
    else:
        block = {"status": status, "status_channels": {}, "status_times": {}}
        if settings is not None:
            block["settings"] = settings
        else:
            block["selections"] = {}
        p.write_text(json.dumps({"status_run_start": block}))
    if scan_info is not None:
        (aux / "scan_info_rel.json").write_text(json.dumps(scan_info))
    if mtime is not None:
        for f in aux.iterdir():
            os.utime(f, (mtime, mtime))
    return p


T0 = 1_780_000_000  # 2026-05-28


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "p00000"
    # res has runs 1-3, raw has 2 (an older copy) and 4 (only there)
    _write(root, "res/run_data/daq", 1, {"bernina.mono.x": 1.0, "bernina.mono.mode": "IN"}, mtime=T0)
    _write(
        root, "res/run_data/daq", 2,
        {"bernina.mono.x": 2.0, "bernina.mono.mode": "OUT",
         "bernina.scans.acquiring_scan.start_time": T0 + 100.0},
        mtime=T0 + 500,
        scan_info={"scan_description": "edge", "scan_parameters": {"name": ["bernina.mono.x"]},
                   "scan_values_all": [[1], [2], [3]]},
    )
    _write(root, "res/run_data/daq", 3, {"bernina.mono.x": None, "bernina.mono.mode": "IN"}, mtime=T0 + 1000)
    _write(root, "raw", 2, {"bernina.mono.x": -99.0}, mtime=T0 + 400)
    # older layout: separate settings dict, status wins where both have a name
    _write(root, "raw", 4, {"bernina.mono.x": 4.0},
           settings={"bernina.mono.x": 4.5, "bernina.mono.gap": 0.25}, mtime=T0 + 2000)
    return root


def _store(root, cache=True):
    dirs = [root / "res/run_data/daq", root / "raw"]
    return RunStore(dirs, root / "res/run_data/run_table/p00000_runs.parquet" if cache else None)


def test_table_reads_res_first_and_raw_only_runs(tree):
    tab = _store(tree).table(prefix="bernina.mono")

    assert list(tab.index) == [1, 2, 3, 4]
    assert tab.loc[2, "bernina.mono.x"] == 2.0  # res copy, not raw's -99
    assert np.isnan(tab.loc[3, "bernina.mono.x"])
    assert tab.loc[4, "bernina.mono.x"] == 4.0
    assert tab.loc[4, "bernina.mono.gap"] == 0.25
    assert list(tab["bernina.mono.mode"][:3]) == ["IN", "OUT", "IN"]


def test_time_from_acquiring_scan_else_file_mtime(tree):
    tab = _store(tree).table(columns=["bernina.mono.x"])

    # local wall-clock time
    assert tab.loc[2, "time"] == datetime.fromtimestamp(T0 + 100)
    assert tab.loc[1, "time"] == datetime.fromtimestamp(T0)


def test_start_time_string_in_both_formats():
    start = datetime(2026, 10, 4, 8, 1, 20)
    mtime = datetime(2026, 10, 4, 22, 54, 38).timestamp()  # file rewritten later
    # "%Y-%M-%d" (2025-07 to 2026-10): minutes where the month belongs
    assert run_store._start_from_string("2026-01-04 08:01:20", mtime) == start.timestamp()
    assert run_store._start_from_string("2026-10-04 08:01:20", mtime) == start.timestamp()
    # not this run's: after the status was written, or days before it
    assert run_store._start_from_string("2026-10-05 08:01:20", mtime) is None
    assert run_store._start_from_string("2026-10-01 08:01:20", mtime) is None
    assert run_store._start_from_string(None, mtime) is None


def test_run_time_prefers_scan_start_over_file_time():
    mtime = datetime(2026, 10, 4, 22, 54, 38).timestamp()
    row = {"scans.start_time": "2026-01-04 08:01:20"}
    assert run_store._run_time(row, mtime) == datetime(2026, 10, 4, 8, 1, 20).timestamp()
    row["scans.acquiring_scan.start_time"] = mtime - 5.0
    assert run_store._run_time(row, mtime) == mtime - 5.0
    assert run_store._run_time({}, mtime) == mtime


def test_scan_info_columns(tree):
    tab = _store(tree).table(columns=list(run_store.SCAN_INFO_COLUMNS))

    assert tab.loc[2, "scan_info.description"] == "edge"
    assert tab.loc[2, "scan_info.adjustables"] == ["bernina.mono.x"]
    assert tab.loc[2, "scan_info.steps"] == 3


def test_cache_is_written_and_reused_without_reparsing(tree, monkeypatch):
    _store(tree).refresh(verbose=False)
    assert (tree / "res/run_data/run_table/p00000_runs.parquet").exists()

    def _no(*a, **k):
        raise AssertionError("re-read a cached run")

    monkeypatch.setattr(RunStore, "_read_run", _no)
    tab = _store(tree).table(prefix="bernina.mono")
    assert list(tab["bernina.mono.mode"]) == ["IN", "OUT", "IN", None]


def test_a_question_reads_only_its_columns_from_the_cache(tree):
    _write(tree, "res/run_data/daq", 8, {"bernina.mono": 8.0, "bernina.mono.x": 8.5}, mtime=T0 + 7000)
    _store(tree).refresh(verbose=False)

    store = _store(tree)
    tab = store.table(columns=["bernina.mono"])

    assert store._frame is None  # the full table was not loaded
    # a dotted name must not drag in the names below it
    assert list(tab.columns) == ["time", "bernina.mono"]
    assert tab.loc[8, "bernina.mono"] == 8.0


def test_only_new_or_changed_runs_are_read(tree, monkeypatch):
    _store(tree).refresh(verbose=False)
    _write(tree, "res/run_data/daq", 5, {"bernina.mono.x": 5.0}, mtime=T0 + 3000)
    p1 = tree / "res/run_data/daq/run0001/aux/status.json"
    p1.write_text(json.dumps({"status_run_start": {"status": {"bernina.mono.x": 1.5}}}))
    os.utime(p1, (T0 + 9, T0 + 9))

    read = []
    original = RunStore._read_run

    def _spy(self, status_path, *a):
        read.append(status_path)
        return original(self, status_path, *a)

    monkeypatch.setattr(RunStore, "_read_run", _spy)
    tab = _store(tree).table(columns=["bernina.mono.x"])

    assert sorted(read) == sorted([p1, tree / "res/run_data/daq/run0005/aux/status.json"])
    assert tab.loc[1, "bernina.mono.x"] == 1.5
    assert tab.loc[5, "bernina.mono.x"] == 5.0


def test_a_column_turning_non_numeric_keeps_earlier_values(tree):
    store = _store(tree)
    store.refresh(verbose=False)
    _write(tree, "res/run_data/daq", 6, {"bernina.mono.x": "broken"}, mtime=T0 + 4000)

    tab = _store(tree).table(columns=["bernina.mono.x"])

    assert list(tab["bernina.mono.x"]) == [1.0, 2.0, None, 4.0, "broken"]


def test_unreadable_file_is_reported_and_retried(tree, capsys):
    p = _write(tree, "res/run_data/daq", 7, None, raw_text='{"status_run_st', mtime=T0 + 5000)
    store = _store(tree)

    store.refresh()
    assert 7 in store.unreadable and 7 not in store.runs
    assert "could not be read" in capsys.readouterr().out

    p.write_text(json.dumps({"status_run_start": {"status": {"bernina.mono.x": 7.0}}}))
    os.utime(p, (T0 + 6000, T0 + 6000))
    tab = store.table(columns=["bernina.mono.x"])
    assert tab.loc[7, "bernina.mono.x"] == 7.0 and not store.unreadable


def test_unwritable_cache_still_answers(tree, capsys):
    if os.geteuid() == 0:
        pytest.skip("root ignores permissions")
    ro = tree / "res/run_data/run_table"
    ro.mkdir(parents=True)
    ro.chmod(0o555)
    try:
        tab = _store(tree).table(columns=["bernina.mono.x"])
    finally:
        ro.chmod(0o755)
    assert list(tab.index) == [1, 2, 3, 4]
    assert "keeping the table in this session only" in capsys.readouterr().out


def test_select_runs():
    avail = [3, 1, 5, 9]
    assert select_runs(avail, None) == [1, 3, 5, 9]
    assert select_runs(avail, slice(-2, None)) == [5, 9]
    assert select_runs(avail, -1) == [9]
    assert select_runs(avail, [1, -2]) == [1, 5]


# ------------------------------------------------------------ via StatusData


class _Pgroup:
    def get_current_value(self):
        return "p00000"


class Mono(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        # an AdjustableVirtual, since only those and Assemblies carry run_status
        self._append(DummyAdjustable, name="x_raw", is_setting=False)
        self._append(
            AdjustableVirtual, [self.x_raw], lambda v: v, lambda v: v,
            name="x", is_setting=True,
        )
        self._append(DummyAdjustable, name="mode", is_setting=False)
        self._append(DummyAdjustable, name="gap", is_setting=True)


class Top(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(Mono, name="mono", is_setting=True, is_display="recursive")


@pytest.fixture
def status_data(tree, monkeypatch):
    monkeypatch.setattr(scan_data, "STATUS_DATA", {})
    base = str(tree.parent / "{pgroup:s}")
    sd = scan_data.StatusData(
        _Pgroup(),
        path_search=base + "/raw",
        res_search=base + "/res/run_data/daq",
        cache_path=base + "/res/run_data/run_table/{pgroup:s}_runs.parquet",
    )
    return sd


def test_status_data_lists_and_loads_from_res_and_raw(status_data):
    assert status_data.get_available_run_numbers() == [1, 2, 3, 4]
    assert status_data.load_run_status(2)["status_run_start"]["status"]["bernina.mono.x"] == 2.0
    assert status_data.load_run_status(4)["status_run_start"]["status"]["bernina.mono.x"] == 4.0


def test_load_run_status_does_not_change_load_kwargs(status_data):
    status_data.load_kwargs = {}
    status_data.load_run_status(1, something=1)
    assert status_data.load_kwargs == {}


def test_object_table_relative_names_and_settings(status_data):
    top = Top(name="bernina")

    tab = top.mono.run_status.table()
    assert {"time", "x", "mode", "gap"} <= set(tab.columns)
    assert list(tab["x"])[:2] == [1.0, 2.0]

    own = top.mono.x.run_status.table(runs=slice(-2, None))
    assert list(own.columns) == ["time", "value"]
    assert list(own.index) == [3, 4]

    settings = top.mono.run_status.table(settings_only=True)
    assert set(settings.columns) == {"time", "x", "gap"}


@pytest.mark.parametrize("x", ["time", "run"])
def test_plot_with_both_axes(status_data, x):
    top = Top(name="bernina")

    ax = top.mono.x.run_status.plot(x=x)

    assert ax.get_xlabel() == x
    assert len(ax.figure.axes) == 2  # the second x axis


def test_plot_without_escape_falls_back(status_data, monkeypatch):
    monkeypatch.setitem(sys.modules, "escape.plot_utilities", None)
    top = Top(name="bernina")

    ax = top.mono.run_status.plot(columns=["x", "gap"], x="run")

    assert len(ax.figure.axes) == 2


def test_plot_needs_columns_when_object_has_no_value(status_data):
    top = Top(name="bernina")

    with pytest.raises(ValueError, match="pass columns="):
        top.mono.run_status.plot()
