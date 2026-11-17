"""HTTP server exposing a FeedbackManager.

Three ways to run it:

- in a running eco session, in a background thread::

      srv = fb.serve(port=8010)        # fb: a FeedbackManager
      srv.url, srv.stop()

- as its own process, from a setup script that builds the manager::

      python -m eco.feedback serve my_loops.py --port 8010

  (``my_loops.py`` defines ``manager`` or ``build_manager()``; see
  ``eco/feedback/examples/``);

- from your own script: ``serve(manager, host=..., port=...)`` (blocking).

Every route is a thin wrapper around :class:`~eco.feedback.service.FeedbackService`,
so the HTTP API and the in-process ``manager.service`` cannot drift apart.

Pass ``api_key=...`` to require an ``X-API-Key`` header on every mutating
route (PATCH/POST/DELETE); GET routes always stay open. The default host is
``127.0.0.1``: reach it from elsewhere through ``ssh -L``, or bind another
host on purpose.

Interactive API reference: ``/docs`` (Swagger) or ``/redoc``. Narrative docs:
``/docs/api`` and ``/docs/gui``.

Needs ``fastapi`` and ``uvicorn`` (``pip install eco[feedback]``); nothing
else in :mod:`eco.feedback` does.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .core import FeedbackManager
from .service import FeedbackError, FeedbackService

_DOCS_DIR = Path(__file__).resolve().parent / "docs"


class LoopSettings(BaseModel):
    setpoint: float | None = None
    rate_hz: float | None = None
    kp: float | None = None
    ki: float | None = None
    kd: float | None = None
    enabled: bool | None = None
    alarm_after_s: float | None = None
    auto_disable_on_alarm: bool | None = None


class MeasurementRequest(BaseModel):
    adjustable_names: list[str]
    detector_names: list[str]
    step: float | dict[str, float] = 0.1
    settle_s: float = 0.2
    repeats: int = 3
    bidirectional: bool = True


class ScheduleRequest(MeasurementRequest):
    interval_s: float


class MatrixLoopCreate(BaseModel):
    matrix_name: str
    gain: float = 0.3
    rcond: float = 1e-3
    rate_hz: float = 10.0
    out_min: float | dict[str, float] | None = None
    out_max: float | dict[str, float] | None = None
    alarm_after_s: float = 5.0
    auto_disable_on_alarm: bool = False
    interlocks: list[str] = []


class MatrixLoopSettings(BaseModel):
    gain: float | None = None
    rcond: float | None = None
    enabled: bool | None = None
    reference: dict[str, float] | None = None
    weights: dict[str, float] | None = None


class ForceRequest(BaseModel):
    value: bool


class ConfigPathRequest(BaseModel):
    path: str | None = None


def _render_doc(page_name: str, title: str) -> str:
    path = _DOCS_DIR / f"{page_name}.md"
    if not path.exists():
        raise HTTPException(404, f"documentation file {path.name!r} not found in {_DOCS_DIR}")
    import markdown

    body = markdown.markdown(path.read_text(), extensions=["toc", "fenced_code", "tables"])
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:48rem;"
        "margin:2rem auto;padding:0 1rem;line-height:1.5}"
        "code{background:#f0f0f0;padding:0.1em 0.3em;border-radius:3px}"
        "pre code{display:block;padding:0.6em}</style></head>"
        f"<body>{body}</body></html>"
    )


def make_app(manager: FeedbackManager, api_key: str | None = None) -> FastAPI:
    svc = manager.service if isinstance(manager, FeedbackManager) else FeedbackService(manager)
    app = FastAPI(title="eco feedback", description=(
        "Feedback server for eco detectors/adjustables (under development). "
        "Narrative docs at /docs/api and /docs/gui."
    ))

    @app.exception_handler(FeedbackError)
    def _feedback_error(request: Request, exc: FeedbackError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    def require_key(x_api_key: str | None = Header(default=None)):
        if api_key is not None and x_api_key != api_key:
            raise HTTPException(401, "missing or invalid X-API-Key")

    auth = [Depends(require_key)]

    # -- landing / docs ------------------------------------------------
    @app.get("/", response_class=HTMLResponse, summary="Landing page")
    def root():
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>eco feedback</title></head><body "
            "style='font-family:system-ui,sans-serif;max-width:32rem;"
            "margin:3rem auto'>"
            "<h1>eco feedback</h1>"
            "<ul>"
            "<li><a href='/docs'>API reference (Swagger)</a></li>"
            "<li><a href='/redoc'>API reference (ReDoc)</a></li>"
            "<li><a href='/docs/api'>API guide</a></li>"
            "<li><a href='/docs/gui'>GUI guide</a></li>"
            "</ul></body></html>"
        )

    @app.get("/docs/api", response_class=HTMLResponse, summary="Narrative API guide")
    def docs_api():
        return _render_doc("API", "eco feedback -- API guide")

    @app.get("/docs/gui", response_class=HTMLResponse, summary="Narrative GUI guide")
    def docs_gui():
        return _render_doc("GUI", "eco feedback -- GUI guide")

    # -- loops -----------------------------------------------------------
    @app.get("/loops", summary="List all feedback loops and their status")
    def list_loops():
        return svc.loops()

    @app.get("/loops/{name}", summary="Status of one feedback loop")
    def loop_status(name: str):
        return svc.status(name)

    @app.patch("/loops/{name}", dependencies=auth,
               summary="Update settings of one feedback loop",
               description="Any field left unset is left unchanged. Setting "
                           "`enabled` resets the PID's integral term.")
    def loop_settings(name: str, s: LoopSettings):
        return svc.set(name, **s.model_dump(exclude_none=True))

    @app.get("/loops/{name}/history", summary="Recent (t, value, output) samples",
             description="With `archive=true`, reads from the persistent "
                         "SQLite store instead of the in-memory window.")
    def loop_history(name: str, n: int = 200, archive: bool = False):
        return svc.history(name, n=n, archive=archive)

    # -- actuator / detector registry ------------------------------------
    @app.get("/actuators", summary="Names of all known adjustables")
    def list_actuators():
        return svc.actuators()

    @app.get("/detectors", summary="Names of all known detectors")
    def list_detectors():
        return svc.detectors()

    # -- response-matrix acquisition -------------------------------------
    @app.post("/response-matrix/{name}", dependencies=auth,
              summary="Start a response-matrix measurement",
              description="Steps each named actuator (by `step`), records "
                          "each named detector's response, and assembles a "
                          "matrix. Runs in the background; poll GET to "
                          "watch progress. A loop driving a stepped actuator "
                          "is disabled for that step and restored afterward.")
    def start_response_matrix(name: str, req: MeasurementRequest):
        body = req.model_dump()
        return svc.start_response_matrix(
            name, body.pop("adjustable_names"), body.pop("detector_names"), **body)

    @app.get("/response-matrix", summary="List all response-matrix measurements")
    def list_response_matrices():
        return svc.response_matrices()

    @app.get("/response-matrix/{name}", summary="Status/result of one measurement")
    def response_matrix_status(name: str):
        return svc.response_matrix_status(name)

    @app.delete("/response-matrix/{name}", dependencies=auth,
                summary="Discard a stored measurement")
    def delete_response_matrix(name: str):
        return svc.delete_response_matrix(name)

    @app.post("/response-matrix/{name}/schedule", dependencies=auth,
              summary="Periodically re-run a response-matrix measurement")
    def start_schedule(name: str, req: ScheduleRequest):
        body = req.model_dump()
        return svc.schedule_response_matrix(
            name, body.pop("adjustable_names"), body.pop("detector_names"),
            body.pop("interval_s"), **body)

    @app.delete("/response-matrix/{name}/schedule", dependencies=auth,
                summary="Stop periodic re-measurement")
    def stop_schedule(name: str):
        return svc.unschedule_response_matrix(name)

    # -- closed-loop matrix correctors ------------------------------------
    @app.get("/matrix-loops", summary="List all closed-loop matrix correctors")
    def list_matrix_loops():
        return svc.matrix_loops()

    @app.post("/matrix-loops/{name}", dependencies=auth,
              summary="Create a closed-loop corrector from a named response matrix",
              description="Created disabled -- enable explicitly.")
    def create_matrix_loop(name: str, req: MatrixLoopCreate):
        body = req.model_dump()
        return svc.create_matrix_loop(name, body.pop("matrix_name"), **body)

    @app.get("/matrix-loops/{name}", summary="Status of one matrix corrector")
    def matrix_loop_status(name: str):
        return svc.matrix_loop_status(name)

    @app.patch("/matrix-loops/{name}", dependencies=auth,
               summary="Update settings of one matrix corrector",
               description="`reference`/`weights` are merged per key.")
    def matrix_loop_settings(name: str, s: MatrixLoopSettings):
        return svc.set_matrix_loop(name, **s.model_dump(exclude_none=True))

    @app.delete("/matrix-loops/{name}", dependencies=auth,
                summary="Stop and remove a matrix corrector")
    def delete_matrix_loop(name: str):
        return svc.delete_matrix_loop(name)

    @app.get("/matrix-loops/{name}/history", summary="Recent history for one matrix corrector")
    def matrix_loop_history(name: str, n: int = 200):
        return svc.matrix_loop_history(name, n=n)

    @app.post("/matrix-loops/{name}/save-reference", dependencies=auth,
              summary="Capture current detector readings as the new reference")
    def save_reference(name: str):
        return svc.save_reference(name)

    # -- interlocks --------------------------------------------------------
    @app.get("/interlocks", summary="List all interlocks and their permit state")
    def list_interlocks():
        return svc.interlocks()

    @app.post("/interlocks/{name}/force", dependencies=auth,
              summary="Force an interlock's permit state")
    def force_interlock(name: str, req: ForceRequest):
        return svc.force_interlock(name, req.value)

    @app.delete("/interlocks/{name}/force", dependencies=auth,
                summary="Clear a forced override, resume reading the live condition")
    def clear_interlock_force(name: str):
        return svc.clear_interlock(name)

    # -- alarms / health -----------------------------------------------------
    @app.get("/alarms", summary="Currently alarmed loops and matrix correctors")
    def alarms():
        return svc.alarms()

    @app.get("/health", summary="Liveness check for the server and every loop thread")
    def health():
        return svc.health()

    # -- config persistence ------------------------------------------------
    @app.post("/config/save", dependencies=auth,
              summary="Save current loop/matrix-loop/matrix settings to YAML")
    def config_save(req: ConfigPathRequest = ConfigPathRequest()):
        return svc.save_config(req.path)

    @app.post("/config/load", dependencies=auth,
              summary="Load loop/matrix-loop/matrix settings from YAML")
    def config_load(req: ConfigPathRequest = ConfigPathRequest()):
        return svc.load_config(req.path)

    # -- logs --------------------------------------------------------------
    @app.get("/logs", summary="Recent event-log entries")
    def logs(n: int = 200, archive: bool = False):
        return svc.logs(n=n, archive=archive)

    return app


def serve(manager: FeedbackManager, host="127.0.0.1", port=8010, api_key=None):
    """Run the server in the foreground (blocks)."""
    import uvicorn

    uvicorn.run(make_app(manager, api_key=api_key), host=host, port=port)


class BackgroundServer:
    """A uvicorn server running in a daemon thread of this process."""

    def __init__(self, manager, host="127.0.0.1", port=8010, api_key=None):
        import uvicorn

        self.manager = manager
        self.host, self.port = host, port
        config = uvicorn.Config(
            make_app(manager, api_key=api_key), host=host, port=port,
            log_level="warning",
            log_config=None,  # leave the session's logging configuration alone
        )
        self._server = uvicorn.Server(config)
        # Signal handlers belong to the session, never to a helper thread.
        self._server.install_signal_handlers = lambda: None
        self._thread = threading.Thread(target=self._server.run, daemon=True,
                                        name=f"feedback-server-{port}")

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    def start(self, timeout=10.0):
        self._thread.start()
        t0 = time.monotonic()
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError(f"feedback server on {self.url} did not start "
                                   "(port in use?)")
            if time.monotonic() - t0 > timeout:
                raise TimeoutError(f"feedback server on {self.url} did not start "
                                   f"within {timeout}s")
            time.sleep(0.05)
        return self

    def stop(self, timeout=5.0):
        self._server.should_exit = True
        self._thread.join(timeout=timeout)

    @property
    def running(self):
        return self._thread.is_alive()

    def __repr__(self):
        return f"<feedback server {self.url} {'running' if self.running else 'stopped'}>"


def serve_in_background(manager, host="127.0.0.1", port=8010, api_key=None):
    return BackgroundServer(manager, host=host, port=port, api_key=api_key).start()
