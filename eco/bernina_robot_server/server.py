"""HTTP API -- wire-compatible with the pshell server clients already speak.

eco's ``bernina.rob`` talks to this through
:class:`eco.pshell.client.PShellClient`, which was written against pshell's
REST interface. Every endpoint that client can reach is implemented here with
the same paths, the same JSON field names and the same semantics, so the eco
side needs no change at all.

The contract, in the order a client uses it
-------------------------------------------

``GET /state``
    ``"Ready"`` | ``"Busy"`` | ``"Fault"`` | ``"Initializing"`` | ``"Closing"``.
    JSON-encoded (i.e. a quoted string). BUSY means a *foreground* command
    holds the server; clients are expected to refuse to issue moves then.

``GET /evalAsync/<statement>``
    Submit a statement, get back its integer command id immediately. A
    statement ending in ``&`` runs in the background pool and leaves the
    server READY -- this is how eco reads values without locking others out.

``GET /result/<id>``
    ``{"id", "status", "return", "exception", ...}`` with ``status`` one of
    ``running`` / ``completed`` / ``failed`` / ``aborted``.

``GET /eval/<statement>``
    Submit and block. Also the channel for the two control words eco sends:
    ``:abort`` (cancel the foreground command) and ``:restart``.

``GET /events``
    Server-sent events. Names and payloads:

    ``polling``       every poll: ``{"pos": {...}, "mode": ..., "frame": ...}``
    ``state``         server state string, on change
    ``motion``        human-readable motion notices
    ``reset_motion``  queued motions were discarded, with the reason
    ``stop``          motion was stopped
    ``shell``         driver-level messages, e.g. ``"Update error: ..."``
    ``robot_state``   the arm's own Ready/Busy/Paused/Offline

Endpoints beyond the pshell surface live under ``/api/`` and are the ones to
prefer for anything new -- they return structured JSON and need no eval.
"""

from __future__ import annotations

import json
import logging
import time

from flask import Flask, Response, jsonify, request

logger = logging.getLogger(__name__)

#: SSE comment sent when idle, so proxies and NAT keep the connection open.
SSE_KEEPALIVE_INTERVAL = 15.0


def create_app(server_app):
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    robot = server_app.robot
    executor = server_app.executor

    # ------------------------------------------------------------------
    # pshell-compatible surface
    # ------------------------------------------------------------------

    @app.get("/version")
    def version():
        return server_app.version

    @app.get("/state")
    def state():
        return jsonify(server_app.state)

    @app.get("/config")
    def config():
        return jsonify(server_app.config.to_dict())

    @app.get("/logs")
    def logs():
        return jsonify(server_app.log_handler.snapshot())

    @app.get("/devices")
    def devices():
        """``[name, type, state, value, age]`` per device, as pshell returned."""
        rows = [[
            robot.name, type(robot).__name__, str(robot.state),
            str(robot.get_current_point_cached()), "",
        ]]
        for name, motor in sorted(robot.motors.items()):
            rows.append([name, type(motor).__name__, str(robot.state),
                         str(motor.get_readback()), ""])
        return jsonify(rows)

    @app.get("/eval/<path:statement>")
    def eval_blocking(statement):
        # Two control words travel over the eval channel in pshell; eco sends
        # both. They are not evaluated in the namespace.
        if statement.strip() == ":abort":
            executor.abort()
            return jsonify("aborted")
        if statement.strip() == ":restart":
            return jsonify(server_app.restart())
        result = executor.run_sync(statement)
        if result.status == "failed":
            return Response(result.exception, status=400, mimetype="text/plain")
        return Response(_as_text(result.value), mimetype="text/plain")

    @app.get("/eval-json/<path:statement>")
    def eval_json(statement):
        result = executor.run_sync(statement)
        if result.status == "failed":
            return jsonify({"exception": result.exception}), 400
        return jsonify(result.value)

    @app.get("/evalAsync/<path:statement>")
    def eval_async(statement):
        result = executor.submit(statement)
        return Response(str(result.id), mimetype="text/plain")

    @app.get("/result/<int:command_id>")
    def result(command_id):
        found = executor.get(command_id)
        if found is None:
            return jsonify({"id": command_id, "status": "failed",
                            "return": None,
                            "exception": f"no such command id: {command_id} "
                                         f"(expired or never existed)"}), 404
        return jsonify(found.to_dict())

    @app.get("/abort")
    @app.get("/abort/<int:command_id>")
    def abort(command_id=None):
        return jsonify(executor.abort(command_id))

    @app.get("/stop")
    def stop_motion():
        robot.stop("stop requested over HTTP")
        return jsonify(True)

    @app.get("/resume")
    def resume_motion():
        robot.resume()
        return jsonify(True)

    @app.get("/pause")
    def pause_motion():
        robot.stop("pause requested over HTTP")
        return jsonify(True)

    @app.get("/update")
    def update():
        robot.update()
        return jsonify(True)

    @app.get("/reinit")
    def reinit():
        return jsonify(server_app.restart())

    @app.put("/set-var")
    def set_var():
        payload = request.get_json(force=True, silent=True) or {}
        name, value = payload.get("name"), payload.get("value")
        if not name:
            return jsonify({"exception": "name is required"}), 400
        executor.namespace[name] = value
        return jsonify(True)

    @app.get("/history/<int:index>")
    def history(index):
        entries = [r["statement"] for r in reversed(executor.snapshot())]
        if index >= len(entries):
            return Response("", mimetype="text/plain")
        return Response(entries[index], mimetype="text/plain")

    @app.get("/events")
    def events():
        """SSE stream. One generator per connected client."""
        names = request.args.get("events")
        subscriber = server_app.events.subscribe(
            name=f"{request.remote_addr}",
            events=names.split(",") if names else None,
        )

        def stream():
            last_sent = time.time()
            try:
                while True:
                    try:
                        frame = subscriber.queue.get(timeout=1.0)
                        yield frame
                        last_sent = time.time()
                    except Exception:
                        if time.time() - last_sent > SSE_KEEPALIVE_INTERVAL:
                            yield ": keepalive\n\n"
                            last_sent = time.time()
            finally:
                server_app.events.unsubscribe(subscriber)

        return Response(stream(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",       # defeat nginx response buffering
            "Connection": "keep-alive",
        })

    # ------------------------------------------------------------------
    # Structured API -- prefer these for anything new
    # ------------------------------------------------------------------

    @app.get("/api/status")
    def api_status():
        return jsonify(server_app.status())

    @app.get("/api/health")
    def api_health():
        healthy = server_app.state in ("Ready", "Busy") and robot.connected
        return jsonify({"healthy": healthy, "state": server_app.state,
                        "connected": robot.connected}), (200 if healthy else 503)

    @app.get("/api/positions")
    def api_positions():
        return jsonify(robot.positions())

    @app.get("/api/poll")
    def api_poll():
        """The exact payload the ``polling`` SSE event carries."""
        return jsonify(robot.on_poll_status)

    @app.get("/api/config")
    def api_robot_config():
        return jsonify(robot.on_poll_info())

    @app.post("/api/move")
    def api_move():
        """``{"gamma": 20, "delta": 3}`` -> one general_motion, no eval."""
        payload = request.get_json(force=True, silent=True) or {}
        wait = bool(payload.pop("wait", True))
        if not payload:
            return jsonify({"exception": "no axes given"}), 400
        statement = f"robot.general_motion(**{payload!r})"
        if not wait:
            statement += "&"
        result = executor.submit(statement) if not wait else \
            executor.run_sync(statement)
        return jsonify(result.to_dict()), (200 if result.status != "failed" else 400)

    @app.post("/api/stop")
    def api_stop():
        executor.abort()
        robot.stop("stop requested over /api/stop")
        robot.reset_motion(msg="stop requested over /api/stop")
        robot.resume()
        return jsonify({"stopped": True})

    @app.get("/api/subscribers")
    def api_subscribers():
        return jsonify(server_app.events.stats())

    @app.get("/api/commands")
    def api_commands():
        return jsonify(executor.snapshot())

    @app.errorhandler(500)
    def on_error(exc):                                   # pragma: no cover
        logger.exception("unhandled error serving %s", request.path)
        return jsonify({"exception": str(exc)}), 500

    return app


def _as_text(value):
    """pshell returned eval results as text; dicts/lists come back as JSON."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)
