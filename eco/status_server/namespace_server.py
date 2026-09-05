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

import logging
import os
import sys
import threading
import time
import uuid

from flask import Flask, jsonify, request

from .config import NamespaceServerConfig
from .namespace_store import READY, NamespaceMonitorStore, NotReady, ReinitInProgress
from .storage import json_default, write_monitor_recording, write_status_snapshot

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
        }

    @app.get("/health")
    def health():
        return jsonify(_health_body())

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

    @app.post("/status/snapshot")
    def status_snapshot():
        body = request.get_json(force=True, silent=True) or {}
        try:
            snap = store.snapshot(
                allow_stale=bool(body.get("allow_stale", False)),
                max_workers=body.get("max_workers"),
            )
        except NotReady as exc:
            # health first, then the error keys: _health_body() carries its
            # own "status" field, which would otherwise clobber "error".
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

        response = {"namespace": config.module_name, **snap}

        if body.get("save", False):
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
            key = body.get("key", "status_run_start")
            directory = config.data_dir(pgroup, run_number)
            payload = _status_payload(snap)

            if body.get("write_async", False):
                job_id = uuid.uuid4().hex
                with jobs_lock:
                    jobs[job_id] = {
                        "state": "running",
                        "path": str(directory / "status.json"),
                        "started_at": time.time(),
                    }

                def _write():
                    try:
                        path = write_status_snapshot(directory, payload, key=key)
                        rec = {"state": "done", "path": str(path)}
                    except Exception as exc:  # noqa: BLE001 - reported via HTTP
                        logger.error("async status write failed", exc_info=True)
                        rec = {"state": "error", "error": f"{type(exc).__name__}: {exc}"}
                    rec["finished_at"] = time.time()
                    with jobs_lock:
                        jobs[job_id].update(rec)

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
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": f"could not write status file: "
                                f"{type(exc).__name__}: {exc}",
                            }
                        ),
                        500,
                    )
                response["saved_to"] = str(path)

        return jsonify(response)

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
            try:
                t0 = time.time()
                snap = store.snapshot()
                rec["snapshot_seconds"] = time.time() - t0
                rec["n_status"] = len(snap.get("status", {}))
                with jobs_lock:
                    jobs[job_id].update({"step": "write", **rec})

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

        threading.Thread(
            target=_capture, name=f"status-capture-{job_id[:8]}", daemon=True
        ).start()
        return (
            jsonify({"status": "ok", "job_id": job_id, "path": str(path),
                     "key": key, "pgroup": pgroup, "run_number": run_number,
                     "upload": upload}),
            202,
        )

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
        try:
            return jsonify(
                store.recording_report(
                    recording_id,
                    with_channels=request.args.get("channels") in ("1", "true", "yes"),
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
