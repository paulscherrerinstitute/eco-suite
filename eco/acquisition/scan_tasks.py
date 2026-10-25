"""Scan-specific helpers for eco.utilities.task_queue: checking a built but
not yet run StepScan (``Scans.xxx(..., start_immediately=False)``), and
saving a paused scan's remaining steps to resume them later as a new task."""

import json
from pathlib import Path


def check_scan_limits(scan):
    """Every planned position of every adjustable that has ``get_limits()``
    must be inside it. Returns a list of problems (empty = fine).

    Adjustables without get_limits() are skipped, and so is one whose limits
    query fails -- a failing *query* isn't evidence the position is bad.
    Limits of (x, x) (e.g. (0, 0)) are taken as "no limits set"."""
    problems = []
    for adjustable, values in zip(scan.adjustables, zip(*scan._values_todo)):
        get_limits = getattr(adjustable, "get_limits", None)
        if not callable(get_limits):
            continue
        try:
            low, high = get_limits()
        except Exception:
            continue
        if low is None or high is None or low == high:
            continue
        outside = [v for v in values if not (low <= v <= high)]
        if outside:
            name = getattr(adjustable, "name", None) or repr(adjustable)
            problems.append(
                f"{name}: {len(outside)} planned position(s) outside its limits "
                f"({low}, {high}), e.g. {outside[0]}"
            )
    return problems


def save_paused_scan(scan, path, description=""):
    """Snapshot the remaining work of a paused StepScan to `path` (json).
    Only the remaining values are saved, not live counter/adjustable state
    (see StepScan.snapshot_remaining)."""
    snap = scan.snapshot_remaining()
    snap["submitted_description"] = description
    Path(path).write_text(json.dumps(snap, indent=2, default=str))
    return path


def resume_scan_call(path, root, counters, scans_kwargs=None):
    """A callable that rebuilds a StepScan for the remaining values saved by
    save_paused_scan() and runs it -- to append to a queue. Adjustables are
    re-resolved by dotted name from `root` (e.g. the beamline namespace);
    `counters` must be given explicitly (ad hoc counters aren't addressable
    by name). Takes ``start_immediately`` like a Scans method, so the
    queue's test run can build it and check its limits."""
    snap = json.loads(Path(path).read_text())
    scans_kwargs = dict(scans_kwargs or {})
    scans_kwargs.setdefault("description", snap["description"])

    def resumed_scan(start_immediately=True):
        from eco.acquisition.scan import StepScan

        adjustables = [_resolve_dotted(root, name) for name in snap["adjustable_names"]]
        scan = StepScan(
            adjustables,
            snap["values_remaining"],
            counters=counters,
            Npulses=snap["pulses_remaining"],
            gridspecs=snap["grid_specs"],
            **scans_kwargs,
        )
        if start_immediately:
            scan.scan_all()
        return scan

    resumed_scan.description = f"resumed: {snap.get('submitted_description', '')}"
    return resumed_scan


def _resolve_dotted(root, dotted_path):
    obj = root
    for part in dotted_path.split("."):
        obj = getattr(obj, part)
    return obj
