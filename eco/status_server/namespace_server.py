"""REST wiring around NamespaceMonitorStore - the namespace-hosted mode.
Separate from server.py (the bare-registry mode) because the two modes'
setup, failure modes and config are different enough that combining them
into one Flask app would obscure which trade-offs apply where. See
namespace_store.py's module docstring before choosing this mode.

Run with: python -m eco.status_server.namespace_server --config <path>
"""

from __future__ import annotations

import logging
import time

from flask import Flask, jsonify, request

from .config import NamespaceServerConfig
from .namespace_store import NamespaceMonitorStore, ReinitInProgress
from .storage import write_status_snapshot

logger = logging.getLogger(__name__)


def create_namespace_app(config: NamespaceServerConfig) -> Flask:
    logger.info(
        "Loading namespace '%s.%s' (required_only=%s) - this can take "
        "several minutes; the process is not ready to serve until it's done.",
        config.module_name,
        config.attr_name,
        config.init_required_only,
    )
    store = NamespaceMonitorStore(
        config.module_name,
        attr_name=config.attr_name,
        init_required_only=config.init_required_only,
    )

    app = Flask(config.module_name)
    app.config["ECO_CONFIG"] = config
    app.config["ECO_STORE"] = store
    app.config["ECO_START_TIME"] = time.time()

    @app.before_request
    def _busy_guard():
        # /health always answers (it reports busy state itself); every
        # other route fails fast with a clear 503 instead of racing the
        # in-progress rebuild in namespace_store.reinit().
        if request.endpoint != "health" and store.busy:
            return (
                jsonify({"status": "error", "message": store.busy_reason or "busy"}),
                503,
            )

    @app.get("/health")
    def health():
        conn = store.connection_report()
        return jsonify(
            {
                "status": "reinitializing" if store.busy else "ok",
                "busy_reason": store.busy_reason,
                "n_monitored": conn["n_monitored"],
                "n_direct_read": conn["n_direct_read"],
                "uptime_s": time.time() - app.config["ECO_START_TIME"],
            }
        )

    @app.post("/status/snapshot")
    def status_snapshot():
        body = request.get_json(force=True, silent=True) or {}
        try:
            snap = store.snapshot()
        except ReinitInProgress as exc:
            return jsonify({"status": "error", "message": str(exc)}), 503

        response = {"namespace": config.module_name, **snap}

        if body.get("save", False):
            pgroup = body["pgroup"]
            run_number = int(body["run_number"])
            key = body.get("key", "status_run_start")
            directory = config.data_dir(pgroup, run_number)
            # write_status_snapshot expects {alias: {"value":..}}; adapt
            # the {"status": {...}, "status_channels": {...}} shape here.
            adapted = {
                name: {"value": value, "pvname": snap["status_channels"].get(name)}
                for name, value in snap["status"].items()
            }
            path = write_status_snapshot(directory, adapted, key=key)
            response["saved_to"] = str(path)

        return jsonify(response)

    @app.post("/admin/reinit")
    def admin_reinit():
        body = request.get_json(force=True, silent=True) or {}
        reload_modules = body.get("reload_modules")
        try:
            store.start_reinit(reload_modules=reload_modules)
        except ReinitInProgress as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
        return jsonify({"status": "ok", "message": "reinit started in background"}), 202

    return app
