"""Client-side simulation of one scan's monitoring traffic against a running
eco.status_server (namespace mode): start_monitoring, an idle wait standing
in for the scan itself, then stop_monitoring - the same request pair
Daq.start_scan_monitoring()/.end_scan_monitoring() send (see
eco.status_server.namespace_component.StatusServer), without ever writing
under a real pgroup's data directory.

recording_id still follows the usual "{pgroup}_run{run_number:04d}"
convention (informational only - the server's own bookkeeping/backfill
matching keys off it), but the recording is stopped with save=False: the
server itself never writes anything to a pgroup's own directory tree,
regardless of which pgroup is named. include_data=True brings the raw
per-channel buffers back to this process instead, and this script writes
them wherever --out-dir points (a fresh tmp directory by default) as one
escape ArrayTimestamps per channel - the same file
eco.status_server.storage.write_monitor_recording would have produced
server-side, just written here instead.

A full-namespace, unfiltered 10-minute recording is genuinely large
(measured: 570-790 MB, dominated by a handful of waveform/digitizer
channels) - pick --out-dir with real headroom (a small local /tmp is not
it) and consider --max-value-elements/--names to shrink files at the
source, and --keep-last in --repeat-for-hours mode to bound total disk
usage regardless of how long the loop runs.

Usage:
    python -m eco.status_server.simulate_scan_monitoring \\
        --url http://saresb-cons-04:8091 --duration 600 \\
        --out-dir /sf/bernina/exp/itcom/res/testing

    # lighter-weight, against production, a handful of channels only:
    python -m eco.status_server.simulate_scan_monitoring \\
        --url http://saresb-cons-04:8091 --duration 60 \\
        --names bernina.mono.energy bernina.izero

    # repeat 10-minute "scans" back to back for 24 hours (soak test),
    # filtering waveforms and keeping only the last 5 runs on disk:
    python -m eco.status_server.simulate_scan_monitoring \\
        --url http://saresb-cons-04:8091 --duration 600 --repeat-for-hours 24 \\
        --out-dir /sf/bernina/exp/itcom/res/testing \\
        --max-value-elements 100 --keep-last 5
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from pathlib import Path

from .client import (
    StatusServerClient,
    warn_failed_required,
    warn_recording_failed_required,
)
from .storage import write_monitor_recording


def simulate(
    url,
    pgroup="p19641",
    run_number=None,
    duration=600.0,
    mode="throttle",
    min_interval=0.1,
    names=None,
    max_value_elements=None,
    poll_interval=30.0,
    out_dir=None,
    filename="namespace_monitor.ixp.h5",
):
    client = StatusServerClient(url)
    health = client.health()
    print(
        f"{url}: {health.get('state')} ({health.get('n_initialized')}/"
        f"{health.get('n_target_names')} initialized, {health.get('n_failed')} failed)"
    )
    warn_failed_required(health)
    if not health.get("ready"):
        raise SystemExit(
            f"server not ready ({health.get('state')}) - not starting a recording"
        )

    run_number = int(run_number if run_number is not None else time.time() % 10_000)
    recording_id = f"{pgroup}_run{run_number:04d}"

    print(f"starting recording '{recording_id}' (mode={mode}, min_interval={min_interval}) ...")
    t0 = time.time()
    result = client.start_recording(
        recording_id=recording_id, names=names, mode=mode, min_interval=min_interval,
        max_value_elements=max_value_elements, pgroup=pgroup, run_number=run_number,
    )
    print(
        f"  attached {result.get('n_channels_attached')}/"
        f"{result.get('n_channels_requested')} channels in {time.time() - t0:.1f} s"
    )
    warn_recording_failed_required(result)

    deadline = time.time() + duration
    tick = poll_interval if poll_interval and poll_interval > 0 else duration
    print(f"recording for {duration:.0f} s (standing in for the body of a scan) ...")
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(tick, remaining))
        if poll_interval and poll_interval > 0 and time.time() < deadline:
            live = client.recording(recording_id)
            print(
                f"  t+{time.time() - t0:.0f}s: {live.get('n_updates')} updates, "
                f"{live.get('n_stored')} stored, {live.get('n_channels_with_data')} "
                f"channels with data"
            )

    print("stopping recording (save=False - nothing written under any pgroup) ...")
    stopped = client.stop_recording(recording_id, save=False, include_data=True)

    out_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="namespace_monitor_sim_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = write_monitor_recording(out_dir, stopped, filename=filename)
    report = stopped.get("write_report", {})
    print(
        f"wrote {report.get('n_written')} channel(s) ({report.get('n_skipped', 0)} "
        f"skipped) to {path} ({path.stat().st_size} bytes)"
    )
    return path


def simulate_loop(
    url,
    pgroup="p19641",
    start_run_number=None,
    scan_duration=600.0,
    total_hours=24.0,
    gap=5.0,
    mode="throttle",
    min_interval=0.1,
    names=None,
    max_value_elements=None,
    poll_interval=30.0,
    out_dir=None,
    filename="namespace_monitor.ixp.h5",
    keep_last=5,
):
    """Repeat :func:`simulate` back to back - one recording per "scan" -
    until `total_hours` have elapsed, the same start/stop cycle a real
    sequence of scans produces rather than one long continuous recording.

    Each iteration gets its own recording_id (run_number incremented by one
    each time, starting from `start_run_number`) and its own subdirectory
    under `out_dir`, so nothing overwrites a previous iteration's file and a
    later failure is easy to line up against which run it happened on.

    A single iteration failing (start_recording/stop_recording raising, a
    transient server hiccup, ...) is logged and skipped rather than ending
    the whole soak test - the point of a 24 h run is to find out whether
    that happens at all, not to die the first time it does.

    keep_last bounds total on-disk footprint regardless of how long the
    loop runs: after each iteration, only the `keep_last` most recent
    run<NNNN> subdirectories under `out_dir` are kept, older ones are
    removed. None (or 0) keeps everything - only sensible if `out_dir` has
    real headroom and/or `names`/`max_value_elements` already keep each
    file small (see the ~600 MB/10 min full-namespace measurement this
    default is guarding against - a 24 h run of 144 unfiltered scans would
    otherwise be tens of GB).
    """
    run_number = int(
        start_run_number if start_run_number is not None else time.time() % 10_000
    )
    base_out_dir = Path(out_dir) if out_dir else Path(
        tempfile.mkdtemp(prefix="namespace_monitor_sim_loop_")
    )
    base_out_dir.mkdir(parents=True, exist_ok=True)
    print(f"looping {scan_duration:.0f} s scans for {total_hours:.2f} h into {base_out_dir}"
          + (f" (keeping last {keep_last})" if keep_last else " (keeping all)"))

    t_start = time.time()
    deadline = t_start + total_hours * 3600.0
    n_ok = n_failed = 0
    iteration = 0
    while time.time() < deadline:
        iteration += 1
        print(f"\n=== scan {iteration} (run {run_number:04d}, "
              f"t+{(time.time() - t_start) / 3600.0:.2f} h) ===")
        try:
            simulate(
                url, pgroup=pgroup, run_number=run_number,
                duration=min(scan_duration, max(0.0, deadline - time.time())),
                mode=mode, min_interval=min_interval, names=names,
                max_value_elements=max_value_elements, poll_interval=poll_interval,
                out_dir=base_out_dir / f"run{run_number:04d}",
                filename=filename,
            )
            n_ok += 1
        except Exception as exc:
            n_failed += 1
            print(f"  !!! scan {iteration} (run {run_number:04d}) failed: "
                  f"{type(exc).__name__}: {exc}")
        if keep_last:
            run_dirs = sorted(
                (p for p in base_out_dir.glob("run*") if p.is_dir()),
                key=lambda p: p.name,
            )
            for stale in run_dirs[:-keep_last]:
                try:
                    shutil.rmtree(stale)
                except Exception:
                    print(f"  (could not remove old {stale})")
        run_number += 1
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        if gap:
            time.sleep(min(gap, remaining))
    print(
        f"\ndone: {n_ok} scan(s) ok, {n_failed} failed, over "
        f"{(time.time() - t_start) / 3600.0:.2f} h"
    )
    return n_ok, n_failed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Simulate one scan's status-server monitoring traffic "
                    "(start_monitoring/.../stop_monitoring). The server is "
                    "always told save=False, so it never writes anything "
                    "under any pgroup's own directory itself - the file "
                    "lands wherever --out-dir points instead."
    )
    parser.add_argument(
        "--url", required=True,
        help="status server base URL, e.g. http://saresb-cons-04:8091 (production) "
             "or a disposable test-server URL",
    )
    parser.add_argument(
        "--pgroup", default="p19641",
        help="informational only (recording_id convention/backfill matching) - "
             "default is bernina's safe, dormant commissioning pgroup; nothing "
             "is ever written there since the recording is stopped with "
             "save=False (default: %(default)s)",
    )
    parser.add_argument(
        "--run-number", type=int, default=None,
        help="default: derived from the current time, to avoid colliding with "
             "a real run's recording_id",
    )
    duration_group = parser.add_mutually_exclusive_group()
    duration_group.add_argument(
        "--duration", type=float, default=600.0,
        help="how long to hold the recording open, in seconds "
             "(default: %(default)s, i.e. a 10 minute scan)",
    )
    duration_group.add_argument(
        "--hours", type=float, default=None,
        help="how long to hold the recording open, in hours - alternative "
             "to --duration, for a long soak test",
    )
    parser.add_argument("--mode", default="throttle", choices=("all", "throttle", "sample"))
    parser.add_argument("--min-interval", type=float, default=0.1)
    parser.add_argument(
        "--names", nargs="*", default=None,
        help="restrict to these channels instead of every monitorable one - "
             "useful for a lighter-weight run against production instead of "
             "recording the whole namespace",
    )
    parser.add_argument(
        "--max-value-elements", type=int, default=None,
        help="drop a channel's update outright if its value has more than "
             "this many elements - the single biggest lever on file size: "
             "a handful of waveform/digitizer channels can dominate a "
             "full-namespace recording by an order of magnitude over every "
             "scalar channel combined. None (default) keeps everything.",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=30.0,
        help="print a progress line this often while waiting; 0 disables "
             "(default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir", default=None,
        help="where to write the resulting file(s) (default: a fresh "
             "directory under the system tmp dir)",
    )
    parser.add_argument("--filename", default="namespace_monitor.ixp.h5")
    parser.add_argument(
        "--repeat-for-hours", type=float, default=None,
        help="instead of one recording, repeat scans of --duration length "
             "back to back until this many hours have elapsed in total (a "
             "soak test of many start/stop cycles rather than one long "
             "recording) - each scan gets its own subdirectory under "
             "--out-dir",
    )
    parser.add_argument(
        "--gap", type=float, default=5.0,
        help="idle seconds between consecutive scans in --repeat-for-hours "
             "mode (default: %(default)s)",
    )
    parser.add_argument(
        "--keep-last", type=int, default=5,
        help="in --repeat-for-hours mode, keep only this many most-recent "
             "run<NNNN> subdirectories under --out-dir, deleting older ones "
             "as new ones are written - bounds total disk usage regardless "
             "of how long the loop runs. 0 keeps everything (default: "
             "%(default)s)",
    )
    args = parser.parse_args(argv)
    if args.repeat_for_hours is not None and args.hours is not None:
        parser.error("--hours is not meaningful together with --repeat-for-hours "
                     "(use --duration for one scan's length there)")
    duration = args.hours * 3600.0 if args.hours is not None else args.duration

    if args.repeat_for_hours is not None:
        simulate_loop(
            args.url, pgroup=args.pgroup, start_run_number=args.run_number,
            scan_duration=duration, total_hours=args.repeat_for_hours,
            gap=args.gap, mode=args.mode, min_interval=args.min_interval,
            names=args.names, max_value_elements=args.max_value_elements,
            poll_interval=args.poll_interval,
            out_dir=args.out_dir, filename=args.filename,
            keep_last=args.keep_last,
        )
        return

    simulate(
        args.url, pgroup=args.pgroup, run_number=args.run_number,
        duration=duration, mode=args.mode, min_interval=args.min_interval,
        names=args.names, max_value_elements=args.max_value_elements,
        poll_interval=args.poll_interval,
        out_dir=args.out_dir, filename=args.filename,
    )


if __name__ == "__main__":
    main()
