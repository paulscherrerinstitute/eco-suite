"""Daq.append_scan_monitors/end_scan_monitors: the older, adjustables-only
monitor mechanism (writes aux/scan_monitor.pkl) - distinct from the
status-server-backed start_scan_monitoring/end_scan_monitoring (see
test_daq_status_server.py), unrelated to this session's other work.

Regression test for a real bug found via a real scan on saresb-vcons-03
(pgroup p22891): end_scan_monitors built its local aux-file path with the
*raw* run number ("run79/aux/scan_monitor.pkl") instead of the zero-padded
convention every other write site (copy_scan_info_to_raw,
append_start_status_to_scan, the status server's own data_root_pattern,
...) uses ("run0079/aux/..."). p22891's real run directories are all
zero-padded (run0001..run0079, going back to 2025) - the unpadded path
was a second, spurious, never-otherwise-populated directory tree, and the
file silently "succeeded" writing there instead of into the run's real
aux/ directory.
"""
import types
from pathlib import Path

import eco.acquisition.daq_client as daq_client
from eco.acquisition.daq_client import Daq


class FakeScan:
    def __init__(self, runno):
        self.daq_run_number = types.SimpleNamespace(get_current_value=lambda: runno)
        self._scratch = {"daq": {"monitors": {}}}

    def counter_scratch(self, name):
        return self._scratch.setdefault(name, {})


class FakeResponse:
    def json(self):
        return {"status": "ok", "message": "done"}


def test_end_scan_monitors_uses_the_zero_padded_run_directory(monkeypatch):
    daq = Daq.__new__(Daq)
    daq.name = "daq"
    daq._pgroup = "p22891"
    captured = {}

    def fake_ensure_dir(path):
        captured["dir"] = Path(path)

    class FakeWritable:
        def __init__(self, path, mode):
            captured["opened_path"] = Path(path)

        def __enter__(self):
            import io

            return io.BytesIO()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(daq_client, "ensure_dir", fake_ensure_dir)
    monkeypatch.setattr(daq_client, "open_group_writable", FakeWritable)
    # Path.exists() is real (the fake run directory does not exist), so
    # end_scan_monitors takes the "write a fresh file" branch, exercising
    # both ensure_dir's and open_group_writable's paths above.

    def fake_append_aux(*file_names, run_number=None, pgroup=None, check_group=True):
        captured["file_names"] = file_names
        return FakeResponse()

    daq.append_aux = fake_append_aux

    scan = FakeScan(runno=79)
    daq.end_scan_monitors(scan)

    assert "run0079" in str(captured["dir"])
    assert "/run79/" not in str(captured["dir"])
    assert "run0079" in str(captured["opened_path"])
    assert "run0079" in captured["file_names"][0]
