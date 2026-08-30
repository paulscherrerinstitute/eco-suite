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

import h5py
import numpy as np


class NumpyEncoder(json.JSONEncoder):
    """Same numpy handling as eco.utilities.NumpyEncoder (which is what the
    daq client writes status.json with), plus a last-resort ``str()`` for
    anything else.

    That fallback is deliberate here and not in the daq client: a status
    value of an unexpected type is a cosmetic problem in one entry, but
    without it a single such value makes the whole HTTP response - or the
    whole status file - fail. Status collection is best-effort by design
    (see get_status(raise_on_incomplete=False)), so degrade the one entry.
    """

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, (np.bool_,)):
            return bool(obj)
        elif isinstance(obj, bytes):
            return obj.decode("utf-8", "replace")
        elif isinstance(obj, (set, frozenset, tuple)):
            return list(obj)
        try:
            return json.JSONEncoder.default(self, obj)
        except TypeError:
            return str(obj)


def json_default(obj):
    """The `default=` hook matching NumpyEncoder, for json.dumps callers
    that cannot pass a cls= (Flask's JSON provider)."""
    return NumpyEncoder().default(obj)


def _ensure_group_writable(path: Path, mode=None):
    """Best-effort match of an existing convention: aux files/dirs should be
    group-writable and owned by the pgroup so the daq broker's aux transfer
    (Daq.append_aux) and other pgroup members can read/write them. Silently
    ignored on permission failure, same as the existing code.

    Directories need the execute bit, a JSON file does not - hence the
    explicit mode rather than 0o775 for both."""
    if mode is None:
        mode = 0o775 if path.is_dir() else 0o664
    try:
        path.chmod(mode)
    except Exception:
        pass
    try:
        if path.parent.exists() and not path.group() == path.parent.group():
            shutil.chown(path, group=path.parent.group())
    except Exception:
        pass


def write_status_snapshot(
    directory: Path, snapshot: dict, key: str = "status_run_start"
) -> Path:
    """Write/merge one status block into ``<directory>/status.json``.

    ``snapshot`` is either

    * a ``namespace.get_status()``-shaped dict (``{"status": {...},
      "status_channels": {...}, ...}``) - written through unchanged, which is
      what makes the file byte-compatible with what
      ``Daq.append_start_status_to_scan`` writes today; or
    * the flat ``{alias: {"value": ..., "pvname": ...}}`` shape produced by
      the bare-registry mode (server.py), which is converted to the above.

    Merges into an existing file rather than replacing it, so
    ``status_run_end`` can be added next to an already-written
    ``status_run_start`` (the daq client keeps both blocks in memory and
    rewrites the whole file; a stateless server has to merge on disk).
    """
    directory.mkdir(exist_ok=True, parents=True)
    _ensure_group_writable(directory)

    if "status" in snapshot and isinstance(snapshot.get("status"), dict):
        block = snapshot
    else:
        block = {
            "status": {alias: v["value"] for alias, v in snapshot.items()},
            "status_channels": {
                alias: v.get("pvname") for alias, v in snapshot.items()
            },
        }
    payload = {key: block}

    statusfile = directory / "status.json"
    if statusfile.exists():
        try:
            existing = json.loads(statusfile.read_text())
        except (ValueError, OSError):
            # A truncated/half-written file from an interrupted run must not
            # stop this write - the fresh block is worth more than the
            # unreadable remains.
            existing = {}
        if isinstance(existing, dict):
            existing.update(payload)
            payload = existing
    statusfile.write_text(
        json.dumps(payload, sort_keys=True, cls=NumpyEncoder, indent=4)
    )
    _ensure_group_writable(statusfile)
    return statusfile


def _as_storable_array(values):
    """Turn a recorded value list into an array h5 can hold, or None.

    CA channels in a namespace are not all numbers: enum/string PVs give
    str, and waveform PVs give arrays whose length can change between
    updates. numpy turns the first into a unicode dtype (fine, h5py needs it
    as bytes) and the second into an object array (h5py cannot store it at
    all). Rather than failing the whole file for one such channel, convert
    what is convertible and let the caller skip the rest.
    """
    arr = np.asarray(values)
    if arr.dtype.kind in "fiub":
        return arr
    if arr.dtype.kind in "US":
        # h5py has no unicode dtype; variable-length bytes is the usual
        # encoding and round-trips through h5py's str decoding.
        return arr.astype(h5py.string_dtype(encoding="utf-8"))
    return None


def write_monitor_recording(
    directory: Path, recording: dict, filename: str = "monitors.esc.h5",
    libver: str | None = "latest",
) -> Path:
    """Write a recording (as returned by NamespaceMonitorStore.stop_recording,
    or MonitorStore.stop_recording) as one ``escape.ArrayTimestamps`` per
    channel.

    Returns the path; the number of channels actually written, and why any
    were skipped, is reported in ``recording["write_report"]`` which this
    function fills in.
    """
    import escape

    directory.mkdir(exist_ok=True, parents=True)
    _ensure_group_writable(directory)

    # A recording isn't step-based like a scan, so there is exactly one
    # "interval" spanning the whole start/stop window - ArrayTimestamps
    # requires at least this to compute its (single-bin) parameter table.
    timestamp_intervals = np.array([[recording["started_at"], recording["stopped_at"]]])

    filepath = directory / filename
    written, skipped = [], {}
    if libver:
        # A namespace recording is thousands of tiny datasets, and with
        # HDF5's default (backwards-compatible) object-header and group
        # layout the *structure* dwarfs the data: measured on a real bernina
        # recording, 170 000 points - 2.7 MB of numbers - came to a 200 MB
        # file. libver="latest" uses the compact formats and cuts the
        # per-channel overhead severalfold at no cost in write time. The
        # price is that the file needs a reasonably modern HDF5 to read;
        # pass libver=None for maximum compatibility instead.
        results_file = h5py.File(filepath, "w", libver=libver)
        d = escape.DataSet(results_file=results_file, mode="w")
    else:
        d = escape.DataSet.create_with_new_result_file(filepath, force_overwrite=True)
    try:
        for alias, buf in recording["data"].items():
            try:
                data = _as_storable_array(buf["values"])
                if data is None:
                    skipped[alias] = "value type not storable in h5"
                    continue
                arr = escape.ArrayTimestamps(
                    data=data,
                    timestamps=np.asarray(buf["timestamps"], dtype=float),
                    timestamp_intervals=timestamp_intervals,
                    name=alias,
                )
                d.append(arr, name=alias)
                arr.store()
                written.append(alias)
            except Exception as exc:  # noqa: BLE001 - one bad channel of thousands
                skipped[alias] = f"{type(exc).__name__}: {exc}"
    finally:
        d.results_file.close()
    _ensure_group_writable(filepath)
    recording["write_report"] = {
        "n_written": len(written),
        "n_skipped": len(skipped),
        "skipped": dict(list(skipped.items())[:50]),
    }
    return filepath
