"""CounterValue's per-step redraw: the heartbeat is held while the figure is
laid out and drawn (a tick there could fire the live plot's timer, whose
ax.cla() removed the artists tight_layout was walking -- "'NoneType' object
has no attribute 'bbox'" aborted scans), and a plot that fails anyway does
not abort the scan."""

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")

import pytest

from eco.acquisition import scan as scan_module
from eco.acquisition.scan import Scans
from eco.devices_general.simulated_signals import SimulatedSignals
from eco.utilities import mainthread


@pytest.fixture
def sim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scan_module, "get_ipython", lambda: None)
    return SimulatedSignals(axis="ideal")


def test_the_redraw_runs_with_the_heartbeat_held(sim, monkeypatch):
    heartbeat = SimpleNamespace(_hold=0)
    monkeypatch.setattr(mainthread, "_heartbeat", heartbeat)
    monkeypatch.setattr(mainthread, "_on_main_thread", lambda: True)
    held = []
    from matplotlib.figure import Figure

    original = Figure.tight_layout
    monkeypatch.setattr(
        Figure, "tight_layout", lambda self, *a, **k: held.append(heartbeat._hold) or original(self, *a, **k)
    )
    Scans().ascan(sim.peak.x, 0, 1, 3, 0.05, counters=[sim.peak.signal], return_at_end=False)
    assert held and all(h > 0 for h in held)
    assert heartbeat._hold == 0


def test_a_failing_redraw_does_not_abort_the_scan(sim, monkeypatch, capsys):
    from matplotlib.figure import Figure

    def broken(self, *a, **k):
        raise AttributeError("'NoneType' object has no attribute 'bbox'")

    monkeypatch.setattr(Figure, "tight_layout", broken)
    s = Scans().ascan(sim.peak.x, 0, 1, 3, 0.05, counters=[sim.peak.signal], return_at_end=False)
    out = capsys.readouterr().out
    assert s.next_step == 4
    assert "live plot not updated (AttributeError" in out
    assert "Scan aborted" not in out
