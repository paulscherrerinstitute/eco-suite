#!/usr/bin/env python
"""Check and time the status server's client-write path against a real server.

Client-write mode (eco/status_server/CLIENT_WRITE.md) splits what the status
server does today in two: the server still holds the namespace and the CA
monitors and still produces status.json / aliases.json /
namespace_monitor.ixp.h5, but it parks the result in a handoff slot and the
*client* writes it and hands it to the broker. That removes the server
account from the shared data tree entirely - no pgroup membership, no
group-writable run directories, no Kerberos ticket to expire.

This script exercises that path end to end against a running server and
prints a report: whether the routes are there, how long each stage takes,
how big the payloads are, and what the files it wrote actually look like on
disk (owner, group, mode) - which is the point of the exercise.

Safe by default:

* nothing is written outside --out-dir, which defaults to a fresh directory
  under $HOME/.cache/eco-work;
* nothing is uploaded to sf_daq_broker unless --pgroup, --run-number and
  --upload are all given;
* no recording is started unless --record-seconds is given (a namespace-wide
  recording is real load on the server, so it is opt-in).

Typical use on a beamline console:

    source /sf/bernina/bin/bpy-env
    cd <this checkout>
    python scripts/status_server_handoff_check.py check
    python scripts/status_server_handoff_check.py bench --repeat 5
    python scripts/status_server_handoff_check.py check --record-seconds 15

The server has to be one that already has the /handoff routes; `check` says
so in its first line if it does not, and stops there.
"""

import argparse
import getpass
import grp
import json
import os
import pwd
import statistics
import stat
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eco.status_server import relay  # noqa: E402
from eco.status_server.client import StatusServerClient  # noqa: E402

DEFAULT_URL = os.environ.get("ECO_STATUS_SERVER", "http://saresb-cons-04:8091")


# -- small output helpers --------------------------------------------------


class Report:
    """Collects stage timings so the summary table can be printed at the
    end as well as line by line while it runs (a full-namespace snapshot
    takes 10-20 s and watching it is more useful than waiting in silence)."""

    def __init__(self):
        self.rows = []

    def add(self, stage, seconds=None, size=None, note=""):
        self.rows.append((stage, seconds, size, note))
        print(f"  {stage:<34} {_fmt_s(seconds):>9}  {_fmt_b(size):>10}  {note}",
              flush=True)

    def table(self):
        out = [f"  {'stage':<34} {'seconds':>9}  {'bytes':>10}  note",
               "  " + "-" * 72]
        for stage, seconds, size, note in self.rows:
            out.append(f"  {stage:<34} {_fmt_s(seconds):>9}  "
                       f"{_fmt_b(size):>10}  {note}")
        return "\n".join(out)


def _fmt_s(seconds):
    if seconds is None:
        return "-"
    return f"{seconds:.3f}"


def _fmt_b(size):
    if size is None:
        return "-"
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} kB"
    return f"{size / 1024 ** 2:.1f} MB"


def header(text):
    print()
    print(text)
    print("=" * len(text))


def describe_file(path):
    """Owner, group and mode of a file we just wrote - the thing the whole
    change is about, and the thing a permission problem shows up in."""
    path = Path(path)
    st = path.stat()
    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    return (f"{stat.filemode(st.st_mode)} {owner}:{group} "
            f"{st.st_size} bytes")


# -- stages ----------------------------------------------------------------


def stage_server(client, report):
    header(f"server {client.base_url}")
    t0 = time.time()
    health = client.health()
    report.add("GET /health", time.time() - t0, None,
               f"{health.get('state')}, generation {health.get('generation')}")
    print(f"  namespace      : {health.get('namespace')}")
    print(f"  initialized    : {health.get('n_initialized')}/"
          f"{health.get('n_target_names')} ({health.get('n_failed')} failed)")
    print(f"  monitored      : {health.get('n_monitored')}")
    print(f"  uptime         : {health.get('uptime_s', 0) / 3600:.1f} h")
    print(f"  rss            : {health.get('rss_mb', 0):.0f} MB, "
          f"{health.get('n_threads')} threads")
    built = health.get("last_init_finished")
    if built:
        print(f"  namespace age  : {(time.time() - built) / 3600:.1f} h")
    if "handoff" in health:
        print(f"  handoff store  : {health['handoff']}")
    return health


def stage_status(client, out_dir, report, pgroup=None, run_number=None,
                 upload=False, broker=None, relay_mode="inline",
                 key="status_run_start"):
    header("status")
    t0 = time.time()
    started = client.handoff_status(pgroup=pgroup, run_number=run_number,
                                    key=key)
    t_request = time.time() - t0
    report.add("POST /handoff/status", t_request, None,
               f"handoff {started['handoff_id']}")

    spec = relay.status_spec(
        client.base_url, out_dir, pgroup=pgroup, run_number=run_number,
        handoff_id=started["handoff_id"], key=key,
        upload=bool(upload and pgroup and run_number),
        broker_address_aux=broker, keep_status=True, wait=600,
    )
    t0 = time.time()
    job = relay.submit(spec, mode=relay_mode)
    result = job.wait(timeout=900) if relay_mode != "inline" else job.result()
    t_relay = time.time() - t0
    if result.get("state") != "done":
        report.add("relay (collect+write)", t_relay, None,
                   f"FAILED: {result.get('error')}")
        print(result.get("traceback", ""))
        return result
    report.add("relay (collect+write)", t_relay, result.get("file_bytes"),
               f"{result.get('n_status')} status entries")
    report.add("  of which: fetch", result.get("fetch_seconds"),
               result.get("n_bytes_fetched"),
               f"snapshot + transfer; server-side snapshot "
               f"{_fmt_s((result.get('meta') or {}).get('produce_seconds'))} s")
    report.add("  of which: write", result.get("write_seconds"), None,
               str(result["path"]))
    if result.get("upload_seconds") is not None:
        report.add("  of which: upload", result.get("upload_seconds"), None,
                   str(result.get("upload", {}).get("status")))
    print(f"  file           : {describe_file(result['path'])}")

    # Same production work, the way the server does it today, so the two
    # numbers in the report are comparable. save=False: this must not write
    # into anybody's run directory.
    t0 = time.time()
    snap = client.snapshot(save=False)
    report.add("POST /status/snapshot (today)", time.time() - t0, None,
               f"{len(snap.get('status', {}))} status entries")

    written = json.loads(Path(result["path"]).read_text())
    block = written.get(key, {})
    collected, plain = set(block.get("status", {})), set(snap.get("status", {}))
    same = collected == plain
    print(f"  same channel set as a plain snapshot: {same}")
    if not same:
        # Two consecutive snapshots of a freshly initialized namespace are
        # not the same set - components keep appearing for a while after the
        # server reports ready, and the first status walk sees fewer of them
        # than the second. Since the handoff is always taken first here, that
        # settling shows up as a handoff defect unless it is ruled out, so
        # ask the server for one more snapshot and compare it to its own
        # previous one.
        t0 = time.time()
        again = client.snapshot(save=False)
        later = set(again.get("status", {}))
        report.add("POST /status/snapshot (again)", time.time() - t0, None,
                   f"{len(later)} status entries")
        if later != plain:
            print(f"    the server's own two snapshots differ as well "
                  f"({len(plain)} then {len(later)}): the namespace is still "
                  f"settling, which is what this difference is.")
        else:
            print(f"    the server's own two snapshots agree ({len(plain)}), "
                  f"so this is a real difference in the handoff path.")
    if not same:
        only_file = set(block.get("status", {})) - set(snap.get("status", {}))
        only_snap = set(snap.get("status", {})) - set(block.get("status", {}))
        print(f"    only in the written file: {sorted(only_file)[:10]}")
        print(f"    only in the snapshot    : {sorted(only_snap)[:10]}")
    return result


def stage_aliases(client, out_dir, report, pgroup=None, run_number=None,
                  upload=False, broker=None):
    header("aliases")
    t0 = time.time()
    started = client.handoff_aliases(pgroup=pgroup, run_number=run_number)
    report.add("POST /handoff/aliases", time.time() - t0, None,
               f"handoff {started['handoff_id']}")

    spec = relay.aliases_spec(
        client.base_url, out_dir, pgroup=pgroup, run_number=run_number,
        handoff_id=started["handoff_id"],
        upload=bool(upload and pgroup and run_number),
        broker_address_aux=broker, wait=300,
    )
    t0 = time.time()
    result = relay.submit(spec, mode="inline").result()
    if result.get("state") != "done":
        report.add("relay (collect+write)", time.time() - t0, None,
                   f"FAILED: {result.get('error')}")
        return result
    report.add("relay (collect+write)", time.time() - t0,
               result.get("file_bytes"), f"{result.get('n_aliases')} aliases")
    print(f"  file           : {describe_file(result['path'])}")

    t0 = time.time()
    direct = client.aliases()
    report.add("GET /aliases (today)", time.time() - t0, None,
               f"{len(direct)} aliases")
    written = json.loads(Path(result["path"]).read_text())
    print(f"  identical to GET /aliases: {written == direct}")
    return result


def stage_recording(client, out_dir, report, seconds, pgroup=None,
                    run_number=None, upload=False, broker=None,
                    mode="throttle", min_interval=0.1, fmt="h5"):
    header(f"recording ({seconds:.0f} s, mode={mode})")
    recording_id = f"handoff_check_{getpass.getuser()}_{uuid.uuid4().hex[:6]}"
    t0 = time.time()
    started = client.start_recording(recording_id=recording_id, mode=mode,
                                     min_interval=min_interval,
                                     pgroup=pgroup, run_number=run_number)
    report.add("POST /recording/start", time.time() - t0, None,
               f"{started.get('n_channels_attached')}/"
               f"{started.get('n_channels_requested')} attached")

    try:
        time.sleep(seconds)
        rep = client.recording(recording_id)
        print(f"  stored         : {rep.get('n_stored')} points over "
              f"{rep.get('n_channels_with_data')} channels "
              f"({rep.get('stored_per_s', 0) or 0:.0f} points/s, "
              f"{rep.get('n_updates')} updates seen)")

        t0 = time.time()
        handed = client.handoff_recording(recording_id, pgroup=pgroup,
                                          run_number=run_number, format=fmt)
        report.add("POST /handoff/recording", time.time() - t0, None,
                   "stop is synchronous; encode is not")

        spec = relay.recording_spec(
            client.base_url, out_dir, pgroup=pgroup, run_number=run_number,
            handoff_id=handed["handoff_id"],
            upload=bool(upload and pgroup and run_number),
            broker_address_aux=broker, wait=1800,
            filename=f"namespace_monitor.ixp.{'h5' if fmt == 'h5' else fmt}",
        )
        t0 = time.time()
        result = relay.submit(spec, mode="inline").result()
    except BaseException:
        # Never leave a recording running on a production server because
        # this script was interrupted.
        try:
            client.stop_recording(recording_id, save=False)
        except Exception:
            pass
        raise

    if result.get("state") != "done":
        report.add("relay (encode+transfer+write)", time.time() - t0, None,
                   f"FAILED: {result.get('error')}")
        print(result.get("traceback", ""))
        return result
    meta = result.get("meta") or {}
    report.add("relay (encode+transfer+write)", time.time() - t0,
               result.get("file_bytes"),
               f"{meta.get('n_written')} channels written, "
               f"{meta.get('n_skipped')} skipped")
    report.add("  of which: server-side encode", meta.get("produce_seconds"),
               None, "escape/h5, same work the server does today")
    report.add("  of which: transfer+write", result.get("transfer_seconds"),
               None, "long poll, so it overlaps the encode")
    print(f"  file           : {describe_file(result['path'])}")
    # The full list does not fit in the response header once a recording
    # skips hundreds of channels, so the server sends a tally instead; print
    # whichever of the two arrived.
    if meta.get("skipped_reasons"):
        print("  why channels were skipped:")
        for why, n in meta["skipped_reasons"].items():
            print(f"    {n:6d}  {why}")
        for alias, why in list((meta.get("skipped_examples") or {}).items())[:3]:
            print(f"    e.g. {alias}: {why}")
    elif meta.get("skipped"):
        print("  skipped channels (first few):")
        for alias, why in list(meta["skipped"].items())[:5]:
            print(f"    {alias}: {why}")
    if fmt == "h5":
        try:
            import h5py

            with h5py.File(result["path"]) as f:
                groups = list(f.keys())
            print(f"  h5 opens, {len(groups)} top-level groups, "
                  f"e.g. {groups[:3]}")
        except Exception as exc:
            print(f"  h5 could NOT be opened: {type(exc).__name__}: {exc}")
    return result


def stage_detached(client, out_dir, report):
    """The default relay mode, as it would actually run from a scan: a real
    detached child. Separate from the stages above (which run inline) so a
    failure here points at the environment - interpreter, PYTHONPATH,
    permission to fork - rather than at the handoff."""
    header("detached relay process")
    started = client.handoff_status(key="status_run_start")
    spec = relay.status_spec(
        client.base_url, out_dir / "detached", handoff_id=started["handoff_id"],
        upload=False, wait=600,
    )
    t0 = time.time()
    job = relay.submit(spec, mode="process")
    print(f"  pid            : {job.process.pid}")
    print(f"  log            : {job.log_path}")
    try:
        result = job.wait(timeout=900, poll=0.2)
    except Exception as exc:
        report.add("detached relay", time.time() - t0, None,
                   f"FAILED: {type(exc).__name__}: {exc}")
        return None
    report.add("detached relay", time.time() - t0, result.get("file_bytes"),
               result.get("state"))
    if result.get("state") != "done":
        print(f"  error          : {result.get('error')}")
        print(result.get("traceback", ""))
    else:
        print(f"  file           : {describe_file(result['path'])}")
    return result


def stage_leftovers(client):
    header("handoff slots left on the server")
    try:
        body = client.handoffs()
    except Exception as exc:
        print(f"  could not list: {type(exc).__name__}: {exc}")
        return
    print(f"  {body.get('n_slots')} slots, {_fmt_b(body.get('n_bytes'))}, "
          f"spool {body.get('spool_dir')}")
    for slot in body.get("handoffs", []):
        print(f"    {slot['handoff_id']} {slot['kind']:<10} {slot['state']:<8} "
              f"{_fmt_b(slot['n_bytes'])} fetched {slot['n_fetches']}x")
    print("  (anything listed here is either in flight or was never "
          "collected; slots expire on their own)")


# -- commands --------------------------------------------------------------


def cmd_check(args):
    client = StatusServerClient(args.url, timeout=args.timeout,
                                snapshot_timeout=args.snapshot_timeout)
    report = Report()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"client-write check against {args.url}")
    print(f"writing into {out_dir}")
    print(f"running as {getpass.getuser()} on {os.uname().nodename}, "
          f"python {sys.version.split()[0]}")
    print(f"eco from {Path(relay.__file__).resolve().parents[2]}")

    health = stage_server(client, report)
    if not health.get("ready"):
        print("\nserver is not ready; nothing else to check.")
        return 1
    if not client.supports_handoff():
        print("\nThis server has no /handoff routes - it is running a build "
              "from before client-write mode. Restart it from a checkout "
              "that has eco/status_server/handoff.py and run this again.")
        return 2

    stage_status(client, out_dir, report, pgroup=args.pgroup,
                 run_number=args.run_number, upload=args.upload,
                 broker=args.broker)
    stage_aliases(client, out_dir, report, pgroup=args.pgroup,
                  run_number=args.run_number, upload=args.upload,
                  broker=args.broker)
    if args.record_seconds > 0:
        stage_recording(client, out_dir, report, args.record_seconds,
                        pgroup=args.pgroup, run_number=args.run_number,
                        upload=args.upload, broker=args.broker,
                        mode=args.record_mode,
                        min_interval=args.record_min_interval,
                        fmt=args.record_format)
    if not args.no_detached:
        stage_detached(client, out_dir, report)
    stage_leftovers(client)

    header("summary")
    print(report.table())
    print()
    print(f"files written under {out_dir}")
    return 0


def cmd_bench(args):
    client = StatusServerClient(args.url, timeout=args.timeout,
                                snapshot_timeout=args.snapshot_timeout)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"status timing against {args.url}, {args.repeat} repeats")
    print(f"writing into {out_dir}")
    handoff = client.supports_handoff()
    if not handoff:
        print("no /handoff routes on this server; only the existing path "
              "will be timed.")

    rows = {"POST /handoff/status": [], "collect+write (client)": [],
            "POST /status/snapshot": []}
    for i in range(args.repeat):
        if handoff:
            t0 = time.time()
            started = client.handoff_status(key="status_run_start")
            rows["POST /handoff/status"].append(time.time() - t0)
            spec = relay.status_spec(
                client.base_url, out_dir / f"rep{i}",
                handoff_id=started["handoff_id"], upload=False, wait=600,
            )
            t0 = time.time()
            result = relay.submit(spec, mode="inline").result()
            rows["collect+write (client)"].append(time.time() - t0)
            if result.get("state") != "done":
                print(f"  repeat {i}: relay failed: {result.get('error')}")
        t0 = time.time()
        client.snapshot(save=False)
        rows["POST /status/snapshot"].append(time.time() - t0)
        print(f"  repeat {i + 1}/{args.repeat} done", flush=True)

    header("timings (seconds)")
    print(f"  {'stage':<28} {'n':>3} {'min':>8} {'median':>8} {'max':>8}")
    print("  " + "-" * 62)
    for stage, values in rows.items():
        if not values:
            continue
        print(f"  {stage:<28} {len(values):>3} {min(values):>8.3f} "
              f"{statistics.median(values):>8.3f} {max(values):>8.3f}")
    print()
    print("  'POST /handoff/status' is what a scan boundary pays in "
          "client-write mode;")
    print("  'collect+write' is what the relay does afterwards, off the "
          "scan's clock.")
    print()
    print("  'POST /status/snapshot' is the snapshot itself, and is NOT what "
          "a scan")
    print("  boundary costs today: with status_server_async (the default) "
          "/status/capture")
    print("  also returns as soon as the job is queued. Both modes are "
          "asynchronous at")
    print("  the boundary - read this row as the cost of the work, not as "
          "the cost of")
    print("  the mode.")
    return 0


def default_out_dir():
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "eco-work" / f"handoff_check_{time.strftime('%Y%m%d_%H%M%S')}"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See eco/status_server/CLIENT_WRITE.md for what this checks.",
    )
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"status server base url (default {DEFAULT_URL})")
    parser.add_argument("--out-dir", default=None,
                        help="where the collected files are written "
                             "(default: a fresh dir under ~/.cache/eco-work)")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--snapshot-timeout", type=float, default=300.0)
    parser.add_argument("--pgroup", default=None,
                        help="only needed to upload to a real run")
    parser.add_argument("--run-number", type=int, default=None)
    parser.add_argument("--upload", action="store_true",
                        help="hand the written files to sf_daq_broker "
                             "(needs --pgroup and --run-number)")
    parser.add_argument("--broker", default="http://sf-daq:10003",
                        help="broker_address_aux, used only with --upload")

    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="run every stage once and report")
    check.add_argument("--record-seconds", type=float, default=0.0,
                       help="also record the namespace for this long and "
                            "collect it (0 = skip; this is real load on the "
                            "server)")
    check.add_argument("--record-mode", default="throttle",
                       choices=["all", "throttle", "sample"])
    check.add_argument("--record-min-interval", type=float, default=0.1)
    check.add_argument("--record-format", default="h5",
                       choices=["h5", "npz", "json"])
    check.add_argument("--no-detached", action="store_true",
                       help="skip the real subprocess stage")
    check.set_defaults(func=cmd_check)

    bench = sub.add_parser("bench", help="time the status path repeatedly")
    bench.add_argument("--repeat", type=int, default=5)
    bench.set_defaults(func=cmd_bench)

    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args((argv or sys.argv[1:]) + ["check"])
    if args.out_dir is None:
        args.out_dir = str(default_out_dir())
    if args.upload and not (args.pgroup and args.run_number is not None):
        parser.error("--upload needs --pgroup and --run-number")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
