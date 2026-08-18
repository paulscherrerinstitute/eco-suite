"""Writing snapshots and recordings to disk, in the same directory layout
and file formats already used by eco.acquisition.daq_client, so downstream
consumers (run table, analysis notebooks, elog) don't need to change.

status.json keeps the same shape as today's
``Daq.append_start_status_to_scan`` / ``append_status_to_scan_and_store``
output: ``{"status": {alias: value}, "status_channels": {alias: pvname}}``
(minus the per-channel timing/threading fields, which were an artefact of
the live-fanout approach and don't mean anything once values come from a
cache).

Monitor recordings are written as an ``escape`` h5 dataset (one
``ArrayTimestamps`` per channel), matching the format already produced by
``Daq.end_scan_monitors`` and ``EpicsDaq.store_arrays``
(eco/acquisition/daq_client.py, eco/acquisition/epics_data.py) - so this can
be a drop-in addition to, or replacement of, the ``scan_monitor.pkl`` /
``*.esc.h5`` files those already write.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)


def _ensure_group_writable(path: Path):
    """Best-effort match of an existing convention: aux files/dirs should be
    group-writable and owned by the pgroup so the daq broker's aux transfer
    (Daq.append_aux) and other pgroup members can read/write them. Silently
    ignored on permission failure, same as the existing code."""
    try:
        path.chmod(0o775)
    except Exception:
        pass
    try:
        if path.parent.exists() and not path.group() == path.parent.group():
            shutil.chown(path, group=path.parent.group())
    except Exception:
        pass


def write_status_snapshot(directory: Path, snapshot: dict, key: str = "status_run_start") -> Path:
    directory.mkdir(exist_ok=True, parents=True)
    _ensure_group_writable(directory)

    status = {alias: v["value"] for alias, v in snapshot.items()}
    status_channels = {alias: v.get("pvname") for alias, v in snapshot.items()}
    payload = {key: {"status": status, "status_channels": status_channels}}

    statusfile = directory / "status.json"
    if statusfile.exists():
        existing = json.loads(statusfile.read_text())
        existing.update(payload)
        payload = existing
    statusfile.write_text(json.dumps(payload, sort_keys=True, cls=NumpyEncoder, indent=4))
    _ensure_group_writable(statusfile)
    return statusfile


def write_monitor_recording(directory: Path, recording: dict, filename: str = "monitors.esc.h5") -> Path:
    """recording: the dict returned by MonitorStore.stop_recording()."""
    import escape

    directory.mkdir(exist_ok=True, parents=True)
    _ensure_group_writable(directory)

    # A recording isn't step-based like a scan, so there is exactly one
    # "interval" spanning the whole start/stop window - ArrayTimestamps
    # requires at least this to compute its (single-bin) parameter table.
    timestamp_intervals = np.array([[recording["started_at"], recording["stopped_at"]]])

    filepath = directory / filename
    d = escape.DataSet.create_with_new_result_file(filepath, force_overwrite=True)
    for alias, buf in recording["data"].items():
        arr = escape.ArrayTimestamps(
            data=np.array(buf["values"]),
            timestamps=np.array(buf["timestamps"]),
            timestamp_intervals=timestamp_intervals,
            name=alias,
        )
        d.append(arr, name=alias)
        arr.store()
    d.results_file.close()
    _ensure_group_writable(filepath)
    return filepath
