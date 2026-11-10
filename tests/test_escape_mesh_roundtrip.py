"""eco scans -> run files -> escape: a scan's mesh survives the way to the
analysis. Real eco scans (simulated devices) write their scan info as eco
does (NumpyEncoder json); synthetic bs files stand in for the DAQ's, one per
step in the DAQ's layout (/data/<channel>/{data,pulse_id}); escape's own
SwissFEL parser reads the run. Without mesh_specs (all runs before eco wrote
it) escape must build the same mesh from grid_specs or the scan values, and
the scan itself must be unchanged."""

import copy
import json

import matplotlib

matplotlib.use("Agg")

import h5py
import numpy as np
import pytest

pytest.importorskip("escape.storage.mesh")
from escape.swissfel.cluster import parseScanEcoV03

from eco.acquisition.scan import Scans
from eco.devices_general.simulated_signals import SimulatedSignals
from eco.utilities.utilities import NumpyEncoder

CHANNEL = "SIM-DET:VALUE"
SHOTS = 10


@pytest.fixture
def sim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the monitor counter stores its data here
    return SimulatedSignals(axis="ideal")


def _write_run(scan, directory, strip_mesh=False):
    """The scan's info as eco writes it, plus one bs file per step: SHOTS
    events of value 1000 * step + shot, contiguous pulse ids."""
    info = copy.deepcopy(scan.scan_info)
    if strip_mesh:
        info["scan_parameters"].pop("mesh_specs", None)
        for step in info["scan_step_info"]:
            step.pop("mesh_index", None)
    files = []
    for step in range(len(info["scan_values"])):
        path = directory / f"step{step:04d}.BSDATA.h5"
        with h5py.File(path, "w") as f:
            group = f.create_group(f"data/{CHANNEL}")
            group["data"] = 1000.0 * step + np.arange(SHOTS)
            group["pulse_id"] = 10_000 + SHOTS * step + np.arange(SHOTS)
        files.append([path.as_posix()])
    info["scan_files"] = files
    json_path = directory / "scan_info.json"
    with open(json_path, "w") as f:
        json.dump(info, f, sort_keys=True, cls=NumpyEncoder)
    return json_path


def _load(json_path):
    arrays = parseScanEcoV03(
        json_path.as_posix(), lazyEscArrays=False, checknstore_parsing_result=False
    )
    return arrays[CHANNEL]


def _step_means(scan):
    return np.array([1000.0 * i + (SHOTS - 1) / 2 for i in range(len(scan.scan_info["scan_values"]))])


def _check_against_eco(arr, scan):
    """escape's mesh is eco's: same nodes, same node per step."""
    eco = scan.scan_info["scan_parameters"]["mesh_specs"]
    eco_index = [s["mesh_index"] for s in scan.scan_info["scan_step_info"]]
    m = arr.mesh
    assert m.dimension_names == eco["dimension_names"] and m.kind == eco["kind"]
    # escape renumbers only if some node was never measured; here all were
    assert np.allclose(m.nodes, eco["points"])
    assert list(m.step_nodes) == eco_index
    # per node: the events of all its steps pooled
    means = _step_means(scan)
    pooled = [means[[i for i, n in enumerate(eco_index) if n == node]].mean()
              for node in range(len(eco["points"]))]
    nodes = arr.mesh.mean() if not m.is_grid else None
    if nodes is None:  # a grid: compare through the grid index of each node
        grid = arr.mesh.mean()
        for node, gi in enumerate(m.mesh.node_grid_index):
            assert grid[tuple(gi)] == pytest.approx(pooled[node])
    else:
        assert list(nodes) == pytest.approx(pooled)


def test_line_scan_with_repetitions(sim, tmp_path):
    s = Scans().ascan(sim.peak.x, 0, 1, 2, 0.02, counters=[sim.peak.signal],
                      repetitions=2, return_at_end=False)
    arr = _load(_write_run(s, tmp_path))
    assert arr.scan.mean() == pytest.approx(list(_step_means(s)))  # the scan: unchanged
    assert arr.mesh.kind == "line" and len(arr.mesh) == 3
    assert list(arr.mesh.step_nodes) == [0, 1, 2, 0, 1, 2]
    _check_against_eco(arr, s)
    assert list(arr.mesh.mean().coords[0]) == [0.0, 0.5, 1.0]


def test_mesh_scan(sim, tmp_path):
    s = Scans().meshscan((sim.peak2d.x, 0, 1, 1), (sim.peak2d.y, 0, 2, 2), exposure_def=0.02,
                         counters=[sim.peak2d.signal], return_at_end=False)
    arr = _load(_write_run(s, tmp_path))
    assert arr.grid is not None and arr.mesh.is_grid and arr.mesh.shape == (2, 3)
    _check_against_eco(arr, s)
    line = arr.mesh[:, 1].mean()
    assert line.dimension_names == [s.scan_info["scan_parameters"]["mesh_specs"]["dimension_names"][0]]


def test_adaptive_scans(sim, tmp_path):
    pytest.importorskip("escape.adaptive_scan")
    s = Scans().adaptive_scan([sim.peak.x, -2.0, 3.0, 0.02], exposure_def=0.02,
                              feedback=sim.peak.signal, max_points=20,
                              return_at_end=False, plot=False)
    arr = _load(_write_run(s, tmp_path))
    assert arr.mesh.kind == "adapted" and arr.mesh.mesh.resolution == pytest.approx([0.02])
    _check_against_eco(arr, s)
    full = arr.mesh.as_grid("auto").mean()  # back on the axis grid at its spacing
    assert full.coords[0][1] - full.coords[0][0] == pytest.approx(0.02)

    d2 = tmp_path / "2d"
    d2.mkdir()
    s2 = Scans().adaptive_scan([sim.peak2d.x, -1.0, 1.0, 0.5], [sim.peak2d.y, -1.0, 1.0, 0.5],
                               exposure_def=0.02, feedback=sim.peak2d.signal, strategy="peak2d",
                               max_points=12, return_at_end=False, plot=False)
    arr2 = _load(_write_run(s2, d2))
    assert arr2.mesh.kind == "adapted" and arr2.mesh.is_grid  # a subset of its grid
    _check_against_eco(arr2, s2)


def test_runs_without_mesh_specs(sim, tmp_path):
    """All existing runs: no mesh_specs, no mesh_index."""
    s = Scans().ascan(sim.peak.x, 0, 1, 2, 0.02, counters=[sim.peak.signal],
                      repetitions=2, return_at_end=False)
    arr = _load(_write_run(s, tmp_path, strip_mesh=True))
    assert arr.scan.mean() == pytest.approx(list(_step_means(s)))
    assert arr.mesh.kind == "line" and list(arr.mesh.step_nodes) == [0, 1, 2, 0, 1, 2]

    d2 = tmp_path / "grid"
    d2.mkdir()
    g = Scans().meshscan((sim.peak2d.x, 0, 1, 1), (sim.peak2d.y, 0, 2, 2), exposure_def=0.02,
                         counters=[sim.peak2d.signal], return_at_end=False)
    arr2 = _load(_write_run(g, d2, strip_mesh=True))
    assert arr2.mesh.kind == "grid" and arr2.mesh.shape == (2, 3)
    assert np.array_equal(np.asarray(arr2.grid.mean()), np.asarray(arr2.mesh.mean()))
