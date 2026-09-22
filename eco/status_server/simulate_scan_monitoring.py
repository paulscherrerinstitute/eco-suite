"""Client-side simulation of one scan's monitoring traffic against a running
eco.status_server (namespace mode): start_monitoring, an idle wait standing
in for the scan itself, then stop_monitoring - the same request pair
Daq.start_scan_monitoring()/.end_scan_monitoring() send (see
eco.status_server.namespace_component.StatusServer), without ever writing
under a real pgroup's data directory.

recording_id still follows the usual "{pgroup}_run{run_number:04d}"
convention (informational only - the server's own bookkeeping/backfill
matching keys off it), but the recording is stopped with save=False: the
server never writes anything to a pgroup's own directory tree, regardless of
which pgroup is named. include_data=True brings the raw per-channel buffers
back to this process instead, and this script writes them into a local tmp
directory (or --out-dir, if given) as one escape ArrayTimestamps per
channel - the same file eco.status_server.storage.write_monitor_recording
would have produced server-side, just written here instead.

Usage:
    python -m eco.status_server.simulate_scan_monitoring \\
        --url http://saresb-cons-04:8091 --duration 600

    # lighter-weight, against production, a handful of channels only:
    python -m eco.status_server.simulate_scan_monitoring \\
        --url http://saresb-cons-04:8091 --duration 60 \\
        --names bernina.mono.energy bernina.izero
"""

from __future__ import annotations

import argparse
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
        pgroup=pgroup, run_number=run_number,
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Simulate one scan's status-server monitoring traffic "
                    "(start_monitoring/.../stop_monitoring), writing only to a "
                    "local tmp directory - no pgroup data is ever touched."
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
        "--poll-interval", type=float, default=30.0,
        help="print a progress line this often while waiting; 0 disables "
             "(default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir", default=None,
        help="where to write the resulting file (default: a fresh directory "
             "under the system tmp dir)",
    )
    parser.add_argument("--filename", default="namespace_monitor.ixp.h5")
    args = parser.parse_args(argv)
    duration = args.hours * 3600.0 if args.hours is not None else args.duration

    simulate(
        args.url, pgroup=args.pgroup, run_number=args.run_number,
        duration=duration, mode=args.mode, min_interval=args.min_interval,
        names=args.names, poll_interval=args.poll_interval,
        out_dir=args.out_dir, filename=args.filename,
    )


if __name__ == "__main__":
    main()
