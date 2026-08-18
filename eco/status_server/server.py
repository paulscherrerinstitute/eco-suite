"""REST wiring around MonitorStore. Kept intentionally thin: every route
just validates input and calls into monitor_store/storage. Uses Flask for
now (simplest option for a small synchronous internal service, matches the
thread/lock based style already used across eco) - swapping to FastAPI
later would only mean rewriting this one file.

Run with: python -m eco.status_server --config <path/to/config.json>
"""

from __future__ import annotations

import logging
import time

from flask import Flask, jsonify, request

from .channel_registry import load_channel_registry, load_flat_pv_list, merge_channels
from .config import ServerConfig
from .monitor_store import MonitorStore
from .storage import write_monitor_recording, write_status_snapshot

logger = logging.getLogger(__name__)


def create_app(config: ServerConfig) -> Flask:
    channels = load_channel_registry(config.channel_file, channeltypes=config.channeltypes)
    if config.supplementary_pv_list:
        extra = load_flat_pv_list(config.supplementary_pv_list)
        channels = merge_channels(channels, extra)
        logger.info(
            "Merged in %d channels from supplementary PV list '%s'",
            len(extra),
            config.supplementary_pv_list,
        )
    logger.info("Loaded %d channels for namespace '%s'", len(channels), config.namespace)
    store = MonitorStore(channels, connection_timeout=config.connection_timeout)

    app = Flask(config.namespace)
    app.config["ECO_CONFIG"] = config
    app.config["ECO_STORE"] = store
    app.config["ECO_START_TIME"] = time.time()

    @app.get("/health")
    def health():
        conn = store.latest.connection_report()
        n_connected = sum(conn.values())
        return jsonify(
            {
                "status": "ok",
                "namespace": config.namespace,
                "n_channels": len(channels),
                "n_connected": n_connected,
                "uptime_s": time.time() - app.config["ECO_START_TIME"],
            }
        )

    @app.get("/channels")
    def list_channels():
        return jsonify([{"alias": c.alias, "pvname": c.pvname} for c in channels])

    @app.post("/status/snapshot")
    def status_snapshot():
        body = request.get_json(force=True)
        aliases = body.get("aliases")
        snap = store.latest.snapshot(aliases=aliases)
        # attach pvname for storage/display purposes
        by_alias = {c.alias: c.pvname for c in channels}
        for alias, entry in snap.items():
            entry["pvname"] = by_alias.get(alias)

        response = {"namespace": config.namespace, "status": snap}

        if body.get("save", False):
            pgroup = body["pgroup"]
            run_number = int(body["run_number"])
            key = body.get("key", "status_run_start")
            directory = config.data_dir(pgroup, run_number)
            path = write_status_snapshot(directory, snap, key=key)
            response["saved_to"] = str(path)

        return jsonify(response)

    @app.post("/recording/start")
    def recording_start():
        body = request.get_json(force=True)
        recording_id = body["recording_id"]
        aliases = body.get("aliases")
        try:
            store.start_recording(recording_id, aliases=aliases)
        except (ValueError, KeyError) as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        return jsonify({"status": "ok", "recording_id": recording_id})

    @app.post("/recording/stop")
    def recording_stop():
        body = request.get_json(force=True)
        recording_id = body["recording_id"]
        try:
            result = store.stop_recording(recording_id)
        except KeyError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 404

        response = {
            "status": "ok",
            "recording_id": recording_id,
            "started_at": result["started_at"],
            "stopped_at": result["stopped_at"],
            "n_channels": len(result["channels"]),
        }

        if body.get("save", True):
            pgroup = body["pgroup"]
            run_number = int(body["run_number"])
            filename = body.get("filename", "monitors.esc.h5")
            directory = config.data_dir(pgroup, run_number)
            path = write_monitor_recording(directory, result, filename=filename)
            response["saved_to"] = str(path)

        return jsonify(response)

    @app.get("/recording/list")
    def recording_list():
        return jsonify(store.list_recordings())

    return app
