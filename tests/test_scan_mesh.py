"""Every scan's mesh (eco.acquisition.mesh): the node each step measured, in
scan_info's mesh_specs and step info -- for grids, adaptive subsets of a grid
and plain 1-d scans."""

import matplotlib

matplotlib.use("Agg")

import pytest

from eco.acquisition.mesh import ScanMesh
from eco.acquisition.scan import Scans
from eco.devices_general.simulated_signals import SimulatedSignals


@pytest.fixture
def sim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return SimulatedSignals(axis="ideal")


def test_nodes_merge_within_the_resolution():
    mesh = ScanMesh(["x", "y"], ["mm", "mm"], "adapted", resolution=[0.1, 0.1])
    assert mesh.add([0.0, 0.0]) == 0
    assert mesh.add([0.5, 0.2]) == 1
    assert mesh.add([0.5000001, 0.2]) == 1  # the same node again
    assert mesh.specs["points"] == [[0.0, 0.0], [0.5, 0.2]]


def test_line_scan(sim):
    s = Scans().ascan(sim.peak.x, 0, 1, 2, 0.02, counters=[sim.peak.signal], return_at_end=False)
    specs = s.scan_info["scan_parameters"]["mesh_specs"]
    assert specs["kind"] == "line" and specs["points"] == [[0.0], [0.5], [1.0]]
    assert [st["mesh_index"] for st in s.scan_info["scan_step_info"]] == [0, 1, 2]


def test_grid_scan(sim):
    s = Scans().meshscan(
        (sim.peak2d.x, 0, 1, 1), (sim.peak2d.y, 0, 1, 1), exposure_def=0.02,
        counters=[sim.peak2d.signal], return_at_end=False,
    )
    specs = s.scan_info["scan_parameters"]["mesh_specs"]
    assert specs["kind"] == "grid" and len(specs["points"]) == 4
    gs = s.scan_info["scan_parameters"]["grid_specs"]
    for st in s.scan_info["scan_step_info"]:
        i, j = st["grid_index"]
        assert specs["points"][st["mesh_index"]] == [gs["positions"][0][i], gs["positions"][1][j]]


def test_adaptive_scan_mesh(sim):
    pytest.importorskip("escape.adaptive_scan")
    s = Scans().adaptive_scan(
        [sim.peak.x, -2.0, 3.0, 0.02], exposure_def=0.05, feedback=sim.peak.signal,
        max_points=20, return_at_end=False, plot=False,
    )
    specs = s.scan_info["scan_parameters"]["mesh_specs"]
    assert specs["kind"] == "adapted" and specs["resolution"] == pytest.approx([0.02])
    indices = [st["mesh_index"] for st in s.scan_info["scan_step_info"]]
    assert len(indices) == 20 and max(indices) == len(specs["points"]) - 1
