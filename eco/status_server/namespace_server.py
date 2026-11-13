"""REST wiring around NamespaceMonitorStore - the namespace-hosted mode.
Separate from server.py (the bare-registry mode) because the two modes'
setup, failure modes and config are different enough that combining them
into one Flask app would obscure which trade-offs apply where. See
namespace_store.py's module docstring before choosing this mode.

The app is created *immediately* and the (minutes-long) namespace init runs
in the background, so ``/health`` can report progress from the first
request rather than the socket simply not accepting connections until
everything is up. Every route that needs live values answers 503 with the
current state until the store is ready; clients poll ``/health`` (or use
``client.StatusServerClient.wait_ready``).

Run with: python -m eco.status_server --mode namespace --config <path>
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid

from flask import Flask, Response, jsonify, request, stream_with_context

from .config import NamespaceServerConfig
from .handoff import (
    KIND_ALIASES,
    KIND_RECORDING,
    KIND_STATUS,
    CONTENT_TYPES,
    HandoffCapacity,
    HandoffStore,
    encode_recording_h5,
    encode_recording_json,
    encode_recording_npz,
)
from .namespace_store import READY, NamespaceMonitorStore, NotReady, ReinitInProgress
from .query_stats import QueryStats
from .storage import (
    NumpyEncoder,
    json_default,
    write_aliases_file,
    write_monitor_recording,
    write_status_snapshot,
)

logger = logging.getLogger(__name__)

# Canonical keys of a namespace.get_status() result; the extra bookkeeping
# keys snapshot() adds are useful over HTTP but must not end up in the
# on-disk status.json, which downstream consumers read as get_status output.
_STATUS_KEYS = ("status", "status_channels", "status_times", "selections")


def _status_payload(snap):
    return {k: snap[k] for k in _STATUS_KEYS if k in snap}


def _process_stats():
    """Cheap self-measurements for /health: CPU seconds, resident memory and
    thread count, straight from /proc.

    Worth having on a service whose whole job is to hold thousands of CA
    subscriptions: "is monitoring costing anything?" is otherwise only
    answerable by shelling into the host. Silently empty where /proc is not
    Linux's.
    """
    try:
        with open("/proc/self/stat") as f:
            fields = f.read().rsplit(") ", 1)[1].split()
        ticks = os.sysconf("SC_CLK_TCK")
        utime, stime = int(fields[11]) / ticks, int(fields[12]) / ticks
        num_threads = int(fields[17])
        with open("/proc/self/statm") as f:
            rss_pages = int(f.read().split()[1])
        return {
            "cpu_seconds": utime + stime,
            "rss_mb": rss_pages * os.sysconf("SC_PAGE_SIZE") / 1e6,
            "n_threads": num_threads,
        }
    except Exception:
        return {}


def _restart_argv():
    """The argv to re-exec this process with.

    ``[sys.executable] + sys.argv`` is wrong for the normal way this server
    is started (``python -m eco.status_server``): sys.argv[0] is then the
    path to ``__main__.py``, and running that file directly fails on its
    relative imports ("attempted relative import with no known parent
    package"). Reconstruct the ``-m <package>`` form instead when that is
    how the process was started.
    """
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if spec is not None and getattr(spec, "name", None):
        module = spec.parent or spec.name
        return [sys.executable, "-m", module] + sys.argv[1:]
    return [sys.executable] + sys.argv


def create_namespace_app(
    config: NamespaceServerConfig, store=None, start_store=True
) -> Flask:
    if store is None:
        logger.info(
            "Starting namespace '%s.%s' (required_only=%s, names=%s) in the "
            "background; /health reports progress until it is ready.",
            config.module_name,
            config.attr_name,
            config.init_required_only,
            "explicit list" if config.names else None,
        )
        store = NamespaceMonitorStore(
            config.module_name,
            attr_name=config.attr_name,
            init_required_only=config.init_required_only,
            names=config.names,
            exclude_names=config.exclude_names,
            use_monitors=config.use_monitors,
            init_workers=config.init_workers,
            init_cycles=config.init_cycles,
            init_retry_passes=config.init_retry_passes,
            retry_workers=config.retry_workers,
            read_workers=config.read_workers,
            start=start_store,
        )

    # Alarm monitor (eco/alarms/monitor.py): pages phones for raised alarms.
    # Opt-in per server, so a test or second server never pages anyone.
    if start_store and os.environ.get("ECO_ALARM_MONITOR") == "1":
        from eco.alarms.monitor import start_in_status_server

        start_in_status_server(store)

    app = Flask(config.module_name)
    # Status values are whatever the beamline's Detectors return - numpy
    # scalars and arrays are routine (waveform PVs, image stats), and
    # Flask's default provider raises TypeError on them, which turns one odd
    # value into a 500 for the entire snapshot. Use the same numpy handling
    # the status file is written with.
    app.json.default = json_default
    app.config["ECO_CONFIG"] = config
    app.config["ECO_STORE"] = store
    app.config["ECO_START_TIME"] = time.time()
    # id of *this process*: a client that asked for a restart watches this
    # change to know the new process is up, which state/generation alone
    # cannot tell it (a restarted process starts again at generation 0).
    app.config["ECO_INSTANCE_ID"] = uuid.uuid4().hex
    jobs = {}
    jobs_lock = threading.Lock()
    # Recent /status/snapshot and /status/capture operations, for the CLI's
    # `stats` command and the GUI's query-stats table - see query_stats.py's
    # module docstring for why this is separate from /health.
    stats = QueryStats()
    # Results waiting to be collected and written by the client instead of
    # by this process - the /handoff routes below, off by default in the
    # sense that nothing calls them unless a client asks. See
    # handoff.py's module docstring and CLIENT_WRITE.md.
    handoffs = HandoffStore(
        spool_dir=getattr(config, "handoff_spool_dir", None),
        ttl=getattr(config, "handoff_ttl", 3600.0),
        max_slots=getattr(config, "handoff_max_slots", 64),
        janitor=start_store,
    )
    if start_store:
        handoffs.clean_spool_dir()
    app.config["ECO_HANDOFF"] = handoffs

    def _health_body():
        report = store.connection_report()
        return {
            "status": "ok" if report["ready"] else report["state"],
            "state": report["state"],
            "ready": report["ready"],
            "busy_reason": None if report["ready"] else report["state_detail"],
            "namespace": config.module_name,
            "instance_id": app.config["ECO_INSTANCE_ID"],
            "pid": os.getpid(),
            "uptime_s": time.time() - app.config["ECO_START_TIME"],
            **_process_stats(),
            **{k: v for k, v in report.items() if k not in ("state", "ready")},
            "handoff": handoffs.stats(),
        }

    @app.get("/health")
    def health():
        return jsonify(_health_body())

    @app.get("/stats")
    def query_stats_endpoint():
        """Recent /status/snapshot, /status/capture and async /status/job
        write operations - separate from /health, which is about the
        namespace, not about whether requests are being served well. See
        query_stats.py's module docstring.
        """
        limit = request.args.get("limit", type=int)
        kind = request.args.get("kind")
        return jsonify({
            "summary": stats.summary(),
            "recent": stats.recent(limit=limit, kind=kind),
        })

    @app.get("/names")
    def names():
        ns = store.namespace
        body = {
            "target_names": sorted(store.target_names),
            "configured_names": store.configured_names,
            "init_required_only": store.init_required_only,
        }
        if ns is not None:
            body.update(
                {
                    "all_names": sorted(ns.all_names),
                    "initialized_names": sorted(ns.initialized_names),
                    "failed_names": sorted(ns.failed_names),
                    "lazy_names": sorted(ns.lazy_names),
                    # what init_all is working on right now: the only way to
                    # tell "slow device" from "stuck" while state is
                    # 'initializing' without attaching a debugger.
                    "initializing_names": sorted(getattr(ns, "_initializing", [])),
                }
            )
        return jsonify(body)

    @app.get("/failures")
    def failures():
        return jsonify({"failures": store.failure_details()})

    @app.get("/monitor_policy")
    def monitor_policy():
        """What eco.epics_utils.ca_tuning's adaptive monitor policy
        currently believes *in this server process* - the max-rate
        threshold a channel gets demoted above, how many channels are
        monitored/known-fast, and whether a sensitive period is active.
        Read-only: the threshold itself is a module constant, not (yet)
        remotely adjustable.
        """
        from eco.epics_utils import ca_tuning

        return jsonify(ca_tuning.monitor_report())

    @app.get("/aliases")
    def aliases():
        """The namespace's current alias list: ``[{alias, channel,
        channeltype}, ...]``, straight from ``Namespace.alias.get_all()``.

        Unlike a status snapshot this touches no CA channel - the alias tree
        is built as a side effect of the components this server already
        holds initialized, so this is a fast, synchronous in-memory walk.
        Optional repeated ``?channeltype=CA&channeltype=BS`` filters it, same
        as ``Alias.get_all(channeltypes=...)``.
        """
        if store.state != READY:
            return (
                jsonify({**_health_body(), "status": "error",
                         "message": f"namespace store is '{store.state}'"}),
                503,
            )
        ns = store.namespace
        if ns is None:
            return jsonify({"status": "error", "message": "no namespace"}), 503
        channeltypes = request.args.getlist("channeltype") or None
        t0 = time.time()
        alias_list = ns.alias.get_all(channeltypes=channeltypes)
        stats.record("aliases", time.time() - t0, n_entries=len(alias_list))
        return jsonify({"aliases": alias_list, "n_aliases": len(alias_list)})

    @app.post("/aliases/capture")
    def aliases_capture():
        """Compute the current alias list and write/upload
        ``aux/aliases.json`` - in the background, mirroring
        ``/status/capture``. ``Alias.get_all()`` involves no CA traffic, so
        this is fast; going async mainly means the caller does not wait on
        the broker's own ``copy_user_files`` round trip.
        """
        body = request.get_json(force=True, silent=True) or {}
        try:
            pgroup = body["pgroup"]
            run_number = int(body["run_number"])
        except (KeyError, TypeError, ValueError):
            return (
                jsonify({"status": "error",
                         "message": "'pgroup' and 'run_number' are required"}),
                400,
            )
        if store.state != READY:
            return (
                jsonify({**_health_body(), "status": "error",
                         "message": f"namespace store is '{store.state}'"}),
                503,
            )
        ns = store.namespace
        if ns is None:
            return jsonify({"status": "error", "message": "no namespace"}), 503

        upload = bool(body.get("upload", True))
        channeltypes = body.get("channeltypes")
        directory = config.data_dir(pgroup, run_number)
        path = directory / "aliases.json"
        job_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[job_id] = {
                "state": "running", "step": "compute", "kind": "aliases",
                "pgroup": pgroup, "run_number": run_number,
                "path": str(path), "started_at": time.time(),
            }

        def _capture():
            rec = {}
            t0_total = time.time()
            try:
                t0 = time.time()
                alias_list = ns.alias.get_all(channeltypes=channeltypes)
                rec["compute_seconds"] = time.time() - t0
                rec["n_aliases"] = len(alias_list)
                with jobs_lock:
                    jobs[job_id].update({"step": "write", **rec})

                t0 = time.time()
                written = write_aliases_file(directory, alias_list)
                rec["write_seconds"] = time.time() - t0
                rec["path"] = str(written)
                with jobs_lock:
                    jobs[job_id].update({"step": "upload", **rec})

                if upload:
                    t0 = time.time()
                    rec["upload"] = _append_aux(pgroup, run_number, [written])
                    rec["upload_seconds"] = time.time() - t0
                rec["state"] = "done"
            except Exception as exc:  # noqa: BLE001 - reported via the job
                logger.error("aliases capture failed", exc_info=True)
                rec["state"] = "error"
                rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["step"] = None
            rec["finished_at"] = time.time()
            with jobs_lock:
                jobs[job_id].update(rec)
            stats.record(
                "aliases", time.time() - t0_total, error=rec.get("error"),
                pgroup=pgroup, run_number=run_number,
                n_entries=rec.get("n_aliases"),
            )

        threading.Thread(
            target=_capture, name=f"aliases-capture-{job_id[:8]}", daemon=True
        ).start()
        return (
            jsonify({"status": "ok", "job_id": job_id, "path": str(path),
                     "pgroup": pgroup, "run_number": run_number, "upload": upload}),
            202,
        )

    @app.get("/channels/compare")
    def channels_compare():
        """Compare the namespace's currently-initialized channels against
        the DAQ's recorded-channel lists (channels_JF/channels_BS/
        channels_BSCAM): ``{list_name: {channeltype, missing, exceeding,
        n_required, n_recorded}}`` -- see
        eco.aliases.channel_lists.compare_channel_lists.
        ``Daq.compare_channels()`` calls this when a status server is
        configured and healthy, so the comparison runs against THIS
        server's namespace instead of forcing the calling session's own
        (deliberately lazy) one.

        Like /aliases, this touches no CA channel -- both sides are
        already in-process. Optional repeated ``?list=channels_JF&list=
        channels_BS`` restricts which lists are compared (default: all
        three known ones).
        """
        if store.state != READY:
            return (
                jsonify({**_health_body(), "status": "error",
                         "message": f"namespace store is '{store.state}'"}),
                503,
            )
        list_names = request.args.getlist("list") or None
        t0 = time.time()
        try:
            channels = store.compare_channels(list_names=list_names)
        except NotReady as exc:
            return (
                jsonify({**_health_body(), "status": "error",
                         "state": exc.state, "message": str(exc)}),
                503,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("channels/compare failed", exc_info=True)
            return (
                jsonify({"status": "error",
                         "message": f"{type(exc).__name__}: {exc}"}),
                500,
            )
        stats.record("channels_compare", time.time() - t0, n_lists=len(channels))
        return jsonify({"status": "ok", "channels": channels})

    @app.post("/status/snapshot")
    def status_snapshot():
        body = request.get_json(force=True, silent=True) or {}
        t0 = time.time()
        fields = {"save": bool(body.get("save", False))}
        error = None
        try:
            try:
                snap = store.snapshot(
                    allow_stale=bool(body.get("allow_stale", False)),
                    max_workers=body.get("max_workers"),
                )
            except NotReady as exc:
                error = f"NotReady: {exc}"
                # health first, then the error keys: _health_body() carries
                # its own "status" field, which would otherwise clobber
                # "error".
                return (
                    jsonify(
                        {
                            **_health_body(),
                            "status": "error",
                            "state": exc.state,
                            "message": str(exc),
                        }
                    ),
                    503,
                )

            fields["n_entries"] = len(snap.get("status", {}))
            response = {"namespace": config.module_name, **snap}

            if body.get("save", False):
                try:
                    pgroup = body["pgroup"]
                    run_number = int(body["run_number"])
                except (KeyError, TypeError, ValueError):
                    error = "save=true requires 'pgroup' and 'run_number'"
                    return (
                        jsonify({"status": "error", "message": error}),
                        400,
                    )
                key = body.get("key", "status_run_start")
                fields.update(pgroup=pgroup, run_number=run_number, key=key)
                directory = config.data_dir(pgroup, run_number)
                payload = _status_payload(snap)

                # Same opportunistic backfill as /status/capture - this
                # snapshot already read every channel, so a running
                # recording for the same run can borrow a value from it.
                try:
                    store.backfill_running_recordings(
                        pgroup, run_number, snap.get("status", {}),
                        snap.get("status_times"),
                    )
                except Exception:
                    logger.debug("recording backfill from status failed",
                                exc_info=True)

                if body.get("write_async", False):
                    job_id = uuid.uuid4().hex
                    with jobs_lock:
                        jobs[job_id] = {
                            "state": "running",
                            "path": str(directory / "status.json"),
                            "started_at": time.time(),
                        }

                    def _write():
                        t0w = time.time()
                        werror = None
                        try:
                            path = write_status_snapshot(directory, payload, key=key)
                            rec = {"state": "done", "path": str(path)}
                        except Exception as exc:  # noqa: BLE001 - via HTTP
                            logger.error("async status write failed", exc_info=True)
                            werror = f"{type(exc).__name__}: {exc}"
                            rec = {"state": "error", "error": werror}
                        rec["finished_at"] = time.time()
                        with jobs_lock:
                            jobs[job_id].update(rec)
                        stats.record("write", time.time() - t0w, error=werror,
                                    pgroup=pgroup, run_number=run_number, key=key)

                    threading.Thread(
                        target=_write, name=f"status-write-{job_id[:8]}", daemon=True
                    ).start()
                    response["write_job_id"] = job_id
                    response["saved_to"] = str(directory / "status.json")
                else:
                    try:
                        path = write_status_snapshot(directory, payload, key=key)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("status write failed", exc_info=True)
                        error = f"could not write status file: {type(exc).__name__}: {exc}"
                        return (
                            jsonify({"status": "error", "message": error}),
                            500,
                        )
                    response["saved_to"] = str(path)

            return jsonify(response)
        finally:
            stats.record("snapshot", time.time() - t0, error=error, **fields)

    def _append_aux(pgroup, run_number, files):
        """Hand files to sf_daq_broker's copy_user_files, the same call
        Daq.append_aux makes - done here so a client does not have to wait
        for the snapshot just to learn the path it then uploads itself."""
        import requests as _requests

        r = _requests.post(
            config.broker_address_aux.rstrip("/") + "/copy_user_files",
            json={"pgroup": pgroup, "run_number": int(run_number),
                  "files": [str(f) for f in files]},
            timeout=60,
        )
        try:
            body = r.json()
        except ValueError:
            body = {"status": "unknown", "message": r.text[:200]}
        return {"http_status": r.status_code, **body}

    @app.post("/status/capture")
    def status_capture():
        """Snapshot, write status.json, and upload it to the run - all in the
        background, answering immediately with a job id.

        This is the endpoint a scan callback should use. /status/snapshot
        does the same work but makes the caller wait for it and ships the
        whole status dict back (~3 MB, ~20 s on bernina); at a scan boundary
        that is 40 s of dead time per run for a result the client mostly
        does not read. Here the client pays one round trip and the run does
        not wait for the beamline's own bookkeeping.
        """
        body = request.get_json(force=True, silent=True) or {}
        try:
            pgroup = body["pgroup"]
            run_number = int(body["run_number"])
        except (KeyError, TypeError, ValueError):
            return (
                jsonify({"status": "error",
                         "message": "'pgroup' and 'run_number' are required"}),
                400,
            )
        if store.state != READY:
            return (
                jsonify({**_health_body(), "status": "error",
                         "message": f"namespace store is '{store.state}'"}),
                503,
            )

        key = body.get("key", "status_run_start")
        upload = bool(body.get("upload", True))
        # Client-side status (e.g. a scan's own daq_run_number/
        # initial_values - see Daq._client_status_for_scan) this server's
        # own Alias.get_all()-driven snapshot structurally cannot see -
        # merged into the snapshot below, before it's written, rather than
        # replacing anything from it.
        client_status = body.get("client_status") or {}
        # Keep the status values in the job so the caller can collect them
        # afterwards without a second fan-out and without reading the file
        # back over NFS (where they are not visible for some seconds after
        # the write - the run table needs them, and needs them reliably).
        keep_status = bool(body.get("keep_status", False))
        directory = config.data_dir(pgroup, run_number)
        path = directory / "status.json"
        job_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[job_id] = {
                "state": "running", "step": "snapshot", "key": key,
                "pgroup": pgroup, "run_number": run_number,
                "path": str(path), "started_at": time.time(),
            }

        def _capture():
            rec = {}
            t0_total = time.time()
            try:
                t0 = time.time()
                snap = store.snapshot()
                if client_status:
                    for k in ("status", "status_channels", "status_times"):
                        block = client_status.get(k)
                        if block:
                            snap.setdefault(k, {}).update(block)
                rec["snapshot_seconds"] = time.time() - t0
                rec["n_status"] = len(snap.get("status", {}))
                with jobs_lock:
                    jobs[job_id].update({"step": "write", **rec})

                # Opportunistic: this status capture was going to read every
                # channel anyway, so any running recording for the same run
                # can borrow a value from it for a channel that has not
                # updated on its own yet - see
                # NamespaceMonitorStore.backfill_running_recordings. Must
                # never turn a successful status capture into a failed one.
                try:
                    store.backfill_running_recordings(
                        pgroup, run_number, snap.get("status", {}),
                        snap.get("status_times"),
                    )
                except Exception:
                    logger.debug("recording backfill from status failed",
                                exc_info=True)

                if keep_status:
                    with jobs_lock:
                        jobs[job_id]["status"] = snap.get("status", {})

                t0 = time.time()
                written = write_status_snapshot(
                    directory, _status_payload(snap), key=key
                )
                rec["write_seconds"] = time.time() - t0
                rec["path"] = str(written)
                with jobs_lock:
                    jobs[job_id].update({"step": "upload", **rec})

                if upload:
                    t0 = time.time()
                    rec["upload"] = _append_aux(pgroup, run_number, [written])
                    rec["upload_seconds"] = time.time() - t0
                rec["state"] = "done"
            except Exception as exc:  # noqa: BLE001 - reported via the job
                logger.error("status capture failed", exc_info=True)
                rec["state"] = "error"
                rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["step"] = None
            rec["finished_at"] = time.time()
            with jobs_lock:
                jobs[job_id].update(rec)
            stats.record(
                "capture", time.time() - t0_total, error=rec.get("error"),
                pgroup=pgroup, run_number=run_number, key=key,
                n_entries=rec.get("n_status"),
                snapshot_s=rec.get("snapshot_seconds"),
                write_s=rec.get("write_seconds"),
                upload_s=rec.get("upload_seconds"),
            )

        threading.Thread(
            target=_capture, name=f"status-capture-{job_id[:8]}", daemon=True
        ).start()
        return (
            jsonify({"status": "ok", "job_id": job_id, "path": str(path),
                     "key": key, "pgroup": pgroup, "run_number": run_number,
                     "upload": upload}),
            202,
        )

    @app.post("/status/push")
    def status_push():
        """Merge a small client-supplied dict into the *next* /status/job
        read for this (pgroup, run_number, key) -- for values this store
        can never poll itself. See NamespaceMonitorStore.push_status's
        docstring for why (no CA channel at all -- e.g. scans.acquiring_scan.*
        is DetectorMemory-backed). The pushing session already resolved the
        real object and has its value in hand; this just hands it over
        instead of the server trying, and permanently failing, to poll
        something that was never pollable.

        Consumed once by the next /status/job?include_status=1 read at the
        same key, not held indefinitely -- see pop_pushed_status.
        """
        body = request.get_json(force=True, silent=True) or {}
        try:
            pgroup = body["pgroup"]
            run_number = int(body["run_number"])
            values = body["values"]
        except (KeyError, TypeError, ValueError):
            return (
                jsonify({"status": "error",
                         "message": "'pgroup', 'run_number' and 'values' are required"}),
                400,
            )
        if not isinstance(values, dict):
            return jsonify({"status": "error", "message": "'values' must be an object"}), 400
        key = body.get("key", "status_run_start")
        store.push_status(pgroup, run_number, key, values)
        return jsonify({"status": "ok", "pgroup": pgroup, "run_number": run_number,
                        "key": key, "n_values": len(values)})

    @app.get("/status/job/<job_id>")
    def status_job(job_id):
        want_status = request.args.get("include_status") in ("1", "true", "yes")
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return jsonify({"status": "error", "message": "unknown job id"}), 404
            body = dict(job)
            if want_status:
                # handed over once: holding a few MB of values per finished
                # job for the life of the process is how a long-running
                # service quietly turns into a memory leak.
                if job.get("state") != "running":
                    job.pop("status", None)
            else:
                body.pop("status", None)
        # Merge in any values pushed for this same (pgroup, run_number, key)
        # -- only meaningful once the job is done and only for genuine
        # status/capture jobs, which are the only job kind that sets "key"
        # explicitly. aliases-capture and recording-capture jobs also carry
        # pgroup/run_number (for their own reporting) but no "key" - do not
        # default it to "status_run_start" here, or a job that happens to
        # finish first would steal a push meant for the real status job of
        # the same run (found while adding the recording-capture job kind,
        # which would otherwise have hit the exact same gap aliases-capture
        # already had - neither ever exercised include_status=1, so it
        # never surfaced).
        if want_status and body.get("state") != "running":
            pgroup, run_number, key = (
                body.get("pgroup"), body.get("run_number"), body.get("key")
            )
            if pgroup is not None and run_number is not None and key is not None:
                pushed = store.pop_pushed_status(pgroup, run_number, key)
                if pushed:
                    body["status"] = {**(body.get("status") or {}), **pushed}
                    body["n_pushed"] = len(pushed)
        return jsonify({"status": "ok", "job": body})

    # -- recording ---------------------------------------------------------

    def _subscription_mask(value):
        """Translate the request's `subscription_mask` into what pyepics
        wants for `pv.auto_monitor`.

        "value" (the default) is pyepics's normal DBE_VALUE|DBE_ALARM
        subscription. "log" is DBE_LOG, the IOC's *archive* deadband stream:
        the only setting here that makes a fast channel genuinely send
        fewer updates, rather than merely storing fewer of them. It is not
        the default because ADEL is often 0 (in which case DBE_LOG delivers
        the same rate as DBE_VALUE) and because which updates it drops is
        the IOC's choice, not this server's.
        """
        if value in (None, True, "value", "default"):
            return True
        if value in ("log", "archive"):
            from epics.dbr import DBE_LOG

            return DBE_LOG
        if isinstance(value, int):
            return value
        raise ValueError(f"unknown subscription_mask {value!r}")

    @app.get("/recording")
    def recording_list():
        return jsonify({"recordings": store.list_recordings(),
                        "n_monitorable": len(store.monitorable_names())})

    @app.get("/recording/<recording_id>")
    def recording_get(recording_id):
        size_top_n = request.args.get("size_top_n", type=int)
        try:
            return jsonify(
                store.recording_report(
                    recording_id,
                    with_channels=request.args.get("channels") in ("1", "true", "yes"),
                    with_size=request.args.get("size") in ("1", "true", "yes"),
                    size_top_n=size_top_n,
                )
            )
        except KeyError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 404

    @app.post("/recording/start")
    def recording_start():
        body = request.get_json(force=True, silent=True) or {}
        recording_id = body.get("recording_id") or uuid.uuid4().hex[:12]
        try:
            session = store.start_recording(
                recording_id,
                names=body.get("names"),
                mode=body.get("mode", "all"),
                min_interval=float(body.get("min_interval", 0.0)),
                sample_interval=float(body.get("sample_interval", 0.1)),
                max_points_per_channel=int(
                    body.get("max_points_per_channel", 100_000)
                ),
                max_value_elements=body.get("max_value_elements"),
                subscription_mask=_subscription_mask(body.get("subscription_mask")),
                pgroup=body.get("pgroup"),
                run_number=(
                    int(body["run_number"]) if body.get("run_number") is not None
                    else None
                ),
            )
        except NotReady as exc:
            return (
                jsonify({**_health_body(), "status": "error", "message": str(exc)}),
                503,
            )
        except KeyError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
        return jsonify({"status": "ok", **session.report()}), 202

    @app.post("/recording/stop")
    def recording_stop():
        body = request.get_json(force=True, silent=True) or {}
        recording_id = body.get("recording_id")
        try:
            result = store.stop_recording(recording_id)
        except KeyError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409

        data = result.pop("data")
        result.pop("channels", None)
        response = {"status": "ok", **result}

        if body.get("save", True):
            try:
                pgroup = body["pgroup"]
                run_number = int(body["run_number"])
            except (KeyError, TypeError, ValueError):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "save=true requires 'pgroup' and 'run_number'",
                        }
                    ),
                    400,
                )
            directory = config.data_dir(pgroup, run_number)
            payload = {
                "started_at": result["started_at"],
                "stopped_at": result["stopped_at"],
                "data": data,
            }
            t0 = time.time()
            try:
                path = write_monitor_recording(
                    directory, payload,
                    filename=body.get("filename", "monitors.esc.h5"),
                    libver=body.get("libver", "latest"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("monitor recording write failed", exc_info=True)
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"could not write recording: "
                            f"{type(exc).__name__}: {exc}",
                            **result,
                        }
                    ),
                    500,
                )
            response["saved_to"] = str(path)
            response["write_seconds"] = time.time() - t0
            response["file_bytes"] = path.stat().st_size
            response.update(payload.get("write_report", {}))

        # The buffers are the memory this process is holding; a client that
        # wanted the values in the response has them now, and anything else
        # is a leak waiting to happen.
        if body.get("include_data", False):
            response["data"] = data
        if body.get("drop", True):
            try:
                store.drop_recording(recording_id)
            except Exception:
                logger.debug("could not drop recording %s", recording_id, exc_info=True)
        return jsonify(response)

    @app.post("/recording/capture")
    def recording_capture():
        """Stop a recording, write it, and upload it to the run - all in the
        background, mirroring /status/capture and /aliases/capture. This is
        the endpoint a scan boundary should use instead of /recording/stop
        with save=true: stopping itself (detaching callbacks) is instant,
        but the write (one ArrayTimestamps per channel) and the broker
        upload both scale with how much was recorded and how busy
        broker_address_aux is - real cost, not something a scan should wait
        on. Stopping happens synchronously here, before responding, so the
        recording is unambiguously over by the time the caller gets a job
        id; only the write+upload are backgrounded.
        """
        body = request.get_json(force=True, silent=True) or {}
        recording_id = body.get("recording_id")
        try:
            pgroup = body["pgroup"]
            run_number = int(body["run_number"])
        except (KeyError, TypeError, ValueError):
            return (
                jsonify({"status": "error",
                         "message": "'pgroup' and 'run_number' are required"}),
                400,
            )
        try:
            result = store.stop_recording(recording_id)
        except KeyError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409

        data = result.pop("data")
        result.pop("channels", None)
        upload = bool(body.get("upload", True))
        drop = bool(body.get("drop", True))
        filename = body.get("filename", "monitors.esc.h5")
        directory = config.data_dir(pgroup, run_number)
        path = directory / filename
        job_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[job_id] = {
                "state": "running", "step": "write", "kind": "recording",
                "pgroup": pgroup, "run_number": run_number,
                "recording_id": recording_id, "path": str(path),
                "started_at": time.time(), **result,
            }

        def _capture():
            rec = {}
            t0_total = time.time()
            try:
                payload = {
                    "started_at": result["started_at"],
                    "stopped_at": result["stopped_at"],
                    "data": data,
                }
                t0 = time.time()
                written = write_monitor_recording(
                    directory, payload, filename=filename,
                    libver=body.get("libver", "latest"),
                )
                rec["write_seconds"] = time.time() - t0
                rec["path"] = str(written)
                rec.update(payload.get("write_report", {}))
                with jobs_lock:
                    jobs[job_id].update({"step": "upload", **rec})

                if upload:
                    t0 = time.time()
                    rec["upload"] = _append_aux(pgroup, run_number, [written])
                    rec["upload_seconds"] = time.time() - t0
                rec["state"] = "done"
            except Exception as exc:  # noqa: BLE001 - reported via the job
                logger.error("recording capture failed", exc_info=True)
                rec["state"] = "error"
                rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["step"] = None
            rec["finished_at"] = time.time()
            with jobs_lock:
                jobs[job_id].update(rec)
            stats.record(
                "recording_capture", time.time() - t0_total, error=rec.get("error"),
                pgroup=pgroup, run_number=run_number, recording_id=recording_id,
                n_entries=result.get("n_stored"),
                write_s=rec.get("write_seconds"), upload_s=rec.get("upload_seconds"),
            )
            if drop:
                try:
                    store.drop_recording(recording_id)
                except Exception:
                    logger.debug("could not drop recording %s", recording_id,
                                exc_info=True)

        threading.Thread(
            target=_capture, name=f"recording-capture-{job_id[:8]}", daemon=True
        ).start()
        return (
            jsonify({"status": "ok", "job_id": job_id, "path": str(path),
                     "recording_id": recording_id, "pgroup": pgroup,
                     "run_number": run_number, "upload": upload, **result}),
            202,
        )

    # -- handoff: the client writes, this process only produces ------------
    #
    # Same three products as /status/capture, /aliases/capture and
    # /recording/capture, minus the two steps that need this account to have
    # standing in the shared data tree: the write and the broker upload. The
    # payload is parked in a handoff slot and collected by the client, which
    # writes it under its own credentials. See handoff.py and
    # CLIENT_WRITE.md; nothing below changes what the capture routes do, and
    # a server nobody asks keeps no slots.

    def _handoff_accepted(slot, job_id, **extra):
        return (
            jsonify({"status": "ok", "job_id": job_id,
                     "handoff_id": slot.id, "kind": slot.kind,
                     "filename": slot.filename,
                     "expires_at": slot.expires_at, **extra}),
            202,
        )

    def _start_handoff_job(slot, kind, work, **job_fields):
        """Run `work()` in the background, completing `slot` with the bytes
        (or path) it returns and recording the usual job/stats entries.

        `work` returns either bytes, or (path, meta) for a spooled file, or
        (bytes, meta).
        """
        job_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[job_id] = {
                "state": "running", "step": "produce", "kind": kind,
                "handoff_id": slot.id, "started_at": time.time(),
                **job_fields,
            }

        def _run():
            rec = {}
            t0 = time.time()
            try:
                result = work()
                meta = {}
                if isinstance(result, tuple):
                    result, meta = result
                meta = dict(meta or {})
                # How long this server spent producing it - the part a
                # client-write caller is waiting on and cannot measure for
                # itself (its own clock also contains the transfer).
                meta["produce_seconds"] = time.time() - t0
                # Updated *before* completing: once the slot is ready a
                # fetch can arrive at any moment, and it carries the meta.
                slot.meta.update(meta)
                if isinstance(result, (bytes, bytearray)):
                    handoffs.complete_bytes(slot.id, result)
                else:
                    handoffs.complete_file(slot.id, result)
                rec["n_bytes"] = slot.n_bytes
                rec["state"] = "done"
            except Exception as exc:  # noqa: BLE001 - reported via the job
                logger.error("handoff %s (%s) failed", slot.id, kind,
                             exc_info=True)
                rec["state"] = "error"
                rec["error"] = f"{type(exc).__name__}: {exc}"
                handoffs.fail(slot.id, rec["error"])
            rec["produce_seconds"] = time.time() - t0
            rec["step"] = None
            rec["finished_at"] = time.time()
            with jobs_lock:
                jobs[job_id].update(rec)
            stats.record(
                f"handoff_{kind}", rec["produce_seconds"], error=rec.get("error"),
                n_bytes=rec.get("n_bytes"), handoff_id=slot.id,
                **{k: v for k, v in job_fields.items()
                   if k in ("pgroup", "run_number", "key", "recording_id")},
            )

        threading.Thread(
            target=_run, name=f"handoff-{kind}-{slot.id[:8]}", daemon=True
        ).start()
        return job_id

    @app.get("/handoff")
    def handoff_list():
        return jsonify({"handoffs": handoffs.report(), **handoffs.stats()})

    @app.get("/handoff/<handoff_id>")
    def handoff_get(handoff_id):
        try:
            return jsonify({"status": "ok", **handoffs.get(handoff_id).report()})
        except KeyError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 404

    @app.delete("/handoff/<handoff_id>")
    def handoff_delete(handoff_id):
        slot = handoffs.release(handoff_id, missing_ok=True)
        if slot is None:
            return jsonify({"status": "error", "message": "no such handoff"}), 404
        return jsonify({"status": "ok", "handoff_id": handoff_id,
                        "n_fetches": slot.n_fetches})

    @app.get("/handoff/<handoff_id>/data")
    def handoff_data(handoff_id):
        """Collect a slot's payload.

        `wait=<seconds>` long-polls: a client that asked for a snapshot a
        millisecond ago can issue this immediately and block here until the
        bytes exist, which is the whole point of the relay subprocess on the
        other side - one request, one wait, one write.

        `release=1` (what the relay uses) frees the slot the moment the body
        has been sent. Without it the slot survives until its TTL, which is
        what makes a retry possible at the cost of holding the bytes.
        """
        wait = request.args.get("wait", type=float) or 0.0
        release = request.args.get("release") in ("1", "true", "yes")
        try:
            slot = handoffs.get(handoff_id)
        except KeyError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 404
        if wait > 0 and slot.state == "pending":
            slot.wait(wait)
        if slot.state == "pending":
            # 202, not an error: "ask again" is a different thing from
            # "this failed", and a client polling without `wait` needs to
            # tell them apart.
            return jsonify({"status": "pending", **slot.report()}), 202
        if slot.state == "error":
            return jsonify({"status": "error", **slot.report()}), 500

        fh, n_bytes = slot.open()
        handoffs.mark_fetched(handoff_id)

        def _stream():
            try:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    yield chunk
            finally:
                fh.close()
                if release:
                    handoffs.release(handoff_id, missing_ok=True)

        resp = Response(stream_with_context(_stream()),
                        mimetype=slot.content_type)
        resp.headers["Content-Length"] = str(n_bytes)
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{slot.filename}"'
        )
        resp.headers["X-Eco-Handoff-Id"] = handoff_id
        resp.headers["X-Eco-Handoff-Kind"] = slot.kind
        # The client records this next to the file it writes. Trimmed rather
        # than dropped when a recording skipped a lot of channels: a header
        # a proxy refuses to forward would lose the whole response, and the
        # count is the part that matters.
        meta = json.dumps(slot.meta, default=str)
        if len(meta) > 6000:
            trimmed = {k: v for k, v in slot.meta.items() if k != "skipped"}
            trimmed["skipped_omitted"] = True
            # Keep the shape of the problem even when the list itself cannot
            # travel: a few examples, plus how many channels each distinct
            # reason accounts for. Dropping "skipped" outright made a real
            # run's "887 skipped" unanswerable without server-side digging -
            # i.e. the header lost its information exactly when the number
            # was large enough to be worth reading.
            skipped = slot.meta.get("skipped") or {}
            if isinstance(skipped, dict) and skipped:
                reasons = {}
                for why in skipped.values():
                    kind = str(why).split(":")[0][:80]
                    reasons[kind] = reasons.get(kind, 0) + 1
                trimmed["skipped_reasons"] = dict(
                    sorted(reasons.items(), key=lambda kv: -kv[1])[:5]
                )
                trimmed["skipped_examples"] = dict(list(skipped.items())[:3])
            meta = json.dumps(trimmed, default=str)
            if len(meta) > 6000:
                # Still too long: the examples are the expendable part, the
                # counts are not. Never truncate the string itself - a proxy
                # forwards it fine, but the client cannot parse half a JSON
                # object, which would lose the counts as well.
                trimmed.pop("skipped_examples", None)
                meta = json.dumps(trimmed, default=str)
            if len(meta) > 6000:
                meta = json.dumps({
                    "handoff_meta_too_long": True,
                    "n_written": slot.meta.get("n_written"),
                    "n_skipped": slot.meta.get("n_skipped"),
                }, default=str)
        resp.headers["X-Eco-Handoff-Meta"] = meta
        return resp

    @app.post("/handoff/status")
    def handoff_status():
        """Snapshot the namespace and park the status block for pickup.

        `pgroup`/`run_number` are optional here - this route writes nothing,
        so it does not need to know where the file goes. Pass them anyway
        (the client does) and a recording running for the same run gets the
        same opportunistic backfill /status/capture already does.
        """
        body = request.get_json(force=True, silent=True) or {}
        if store.state != READY:
            return (
                jsonify({**_health_body(), "status": "error",
                         "message": f"namespace store is '{store.state}'"}),
                503,
            )
        key = body.get("key", "status_run_start")
        pgroup = body.get("pgroup")
        run_number = (int(body["run_number"])
                      if body.get("run_number") is not None else None)
        client_status = body.get("client_status") or {}
        allow_stale = bool(body.get("allow_stale", False))
        max_workers = body.get("max_workers")
        try:
            slot = handoffs.create(
                KIND_STATUS, "status.json", CONTENT_TYPES["json"],
                meta={"key": key, "pgroup": pgroup, "run_number": run_number},
                ttl=body.get("ttl"),
            )
        except HandoffCapacity as exc:
            return jsonify({"status": "error", "message": str(exc)}), 503

        def _work():
            snap = store.snapshot(allow_stale=allow_stale,
                                  max_workers=max_workers)
            if client_status:
                for k in ("status", "status_channels", "status_times"):
                    block = client_status.get(k)
                    if block:
                        snap.setdefault(k, {}).update(block)
            if pgroup is not None and run_number is not None:
                try:
                    store.backfill_running_recordings(
                        pgroup, run_number, snap.get("status", {}),
                        snap.get("status_times"),
                    )
                except Exception:
                    logger.debug("recording backfill from status failed",
                                 exc_info=True)
            payload = {"key": key, "block": _status_payload(snap),
                       "namespace": config.module_name,
                       "generation": snap.get("generation")}
            body_bytes = json.dumps(payload, cls=NumpyEncoder).encode("utf-8")
            return body_bytes, {"n_status": len(snap.get("status", {})),
                                "snapshot_seconds": snap.get("snapshot_seconds")}

        job_id = _start_handoff_job(
            slot, KIND_STATUS, _work,
            pgroup=pgroup, run_number=run_number, key=key,
        )
        return _handoff_accepted(slot, job_id, key=key, pgroup=pgroup,
                                 run_number=run_number)

    @app.post("/handoff/aliases")
    def handoff_aliases():
        """Park the namespace's alias list for the client to write as
        aliases.json."""
        body = request.get_json(force=True, silent=True) or {}
        if store.state != READY:
            return (
                jsonify({**_health_body(), "status": "error",
                         "message": f"namespace store is '{store.state}'"}),
                503,
            )
        ns = store.namespace
        if ns is None:
            return jsonify({"status": "error", "message": "no namespace"}), 503
        channeltypes = body.get("channeltypes")
        try:
            slot = handoffs.create(
                KIND_ALIASES, "aliases.json", CONTENT_TYPES["json"],
                meta={"pgroup": body.get("pgroup"),
                      "run_number": body.get("run_number")},
                ttl=body.get("ttl"),
            )
        except HandoffCapacity as exc:
            return jsonify({"status": "error", "message": str(exc)}), 503

        def _work():
            alias_list = ns.alias.get_all(channeltypes=channeltypes)
            payload = json.dumps(alias_list, cls=NumpyEncoder).encode("utf-8")
            return payload, {"n_aliases": len(alias_list)}

        job_id = _start_handoff_job(
            slot, KIND_ALIASES, _work,
            pgroup=body.get("pgroup"), run_number=body.get("run_number"),
        )
        return _handoff_accepted(slot, job_id)

    @app.post("/handoff/recording")
    def handoff_recording():
        """Stop a recording and park its data for the client to write.

        The recording is stopped synchronously, before this answers, exactly
        as /recording/capture does - so "the recording is over" is true by
        the time the caller gets an id, and only the encoding runs in the
        background.

        `format="h5"` (default) has the server build the very same
        escape/h5 file /recording/capture would have written, into its own
        spool directory, and the client copies those bytes to their
        destination. "npz" and "json" hand over the raw buffers instead -
        useful for measuring transport separately from encoding, and for a
        client that wants the numbers rather than the file.
        """
        body = request.get_json(force=True, silent=True) or {}
        recording_id = body.get("recording_id")
        fmt = body.get("format", "h5")
        if fmt not in CONTENT_TYPES:
            return (
                jsonify({"status": "error",
                         "message": f"unknown format {fmt!r}"}),
                400,
            )
        filename = body.get("filename", "namespace_monitor.ixp.h5")
        libver = body.get("libver", "latest")
        drop = bool(body.get("drop", True))
        try:
            result = store.stop_recording(recording_id)
        except KeyError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409

        data = result.pop("data")
        result.pop("channels", None)
        try:
            slot = handoffs.create(
                KIND_RECORDING, filename, CONTENT_TYPES[fmt],
                meta={"recording_id": recording_id, "format": fmt,
                      "pgroup": body.get("pgroup"),
                      "run_number": body.get("run_number"),
                      "started_at": result.get("started_at"),
                      "stopped_at": result.get("stopped_at")},
                ttl=body.get("ttl"),
            )
        except HandoffCapacity as exc:
            # The recording is already stopped; refusing now would throw the
            # data away. Say so loudly instead and let the caller retry the
            # old way - /recording/stop with include_data still has it.
            return jsonify({"status": "error", "message": str(exc),
                            "recording_stopped": True, **result}), 503

        def _work():
            payload = {"started_at": result["started_at"],
                       "stopped_at": result["stopped_at"], "data": data}
            if fmt == "h5":
                path = encode_recording_h5(
                    payload, handoffs.slot_spool_dir(slot.id), filename,
                    libver=libver,
                )
                out = path
            elif fmt == "npz":
                out = encode_recording_npz(payload)
            else:
                out = encode_recording_json(payload)
            meta = dict(payload.get("write_report") or {})
            meta["n_channels"] = len(data)
            if drop:
                try:
                    store.drop_recording(recording_id)
                except Exception:
                    logger.debug("could not drop recording %s", recording_id,
                                 exc_info=True)
            return out, meta

        job_id = _start_handoff_job(
            slot, KIND_RECORDING, _work,
            pgroup=body.get("pgroup"), run_number=body.get("run_number"),
            recording_id=recording_id,
        )
        return _handoff_accepted(
            slot, job_id, format=fmt,
            # result carries recording_id itself (plus started_at, n_stored,
            # ...) - splatting it next to an explicit one is a TypeError.
            **result,
        )

    @app.post("/admin/reinit")
    def admin_reinit():
        body = request.get_json(force=True, silent=True) or {}
        try:
            store.start_reinit(
                mode=body.get("mode", "failed"),
                names=body.get("names"),
                reload_modules=bool(body.get("reload_modules", False)),
                new_names=body.get("new_names"),
            )
        except NotReady as exc:
            return (
                jsonify({"status": "error", "state": exc.state, "message": str(exc)}),
                409,
            )
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        return (
            jsonify(
                {
                    **_health_body(),
                    "status": "ok",
                    "message": "reinit started in background",
                    "generation": store.generation,
                }
            ),
            202,
        )

    @app.post("/admin/restart")
    def admin_restart():
        """Re-exec the whole process.

        The in-process reinit cannot pick up changes to eco's core modules or
        to the namespace's own assembly module (Namespace.reinitialize
        deliberately refuses to reload root_module - that would re-run the
        entire beamline setup script). A process restart is the honest,
        reliable way to get those, at the cost of the full init time again.
        """
        delay = float((request.get_json(force=True, silent=True) or {}).get("delay", 0.5))

        def _restart():
            time.sleep(delay)  # let this response flush first
            # Hand the port back before exec'ing. werkzeug's run_simple()
            # marks the listening socket inheritable, so without this the
            # re-exec'd process finds the port still taken by the socket it
            # inherited from itself and exits - observed live, and the
            # reason __main__ now uses make_server directly.
            server = app.config.get("ECO_WSGI_SERVER")
            if server is not None:
                try:
                    server.server_close()
                except Exception:
                    logger.warning("could not close the listening socket",
                                   exc_info=True)
            argv = _restart_argv()
            logger.warning("restarting process on /admin/restart request: %s", argv)
            os.execv(sys.executable, argv)

        threading.Thread(target=_restart, name="restart", daemon=True).start()
        return (
            jsonify(
                {
                    "status": "ok",
                    "message": f"process re-exec in {delay} s",
                    "instance_id": app.config["ECO_INSTANCE_ID"],
                    "pid": os.getpid(),
                }
            ),
            202,
        )

    return app
