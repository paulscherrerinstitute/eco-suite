"""Thin HTTP client for :mod:`eco.feedback.server`.

Same methods and return values as the in-process
:class:`~eco.feedback.service.FeedbackService` (``manager.service``); errors
come back as the same :class:`~eco.feedback.service.FeedbackError`
subclasses, carrying the server's message.
"""

from __future__ import annotations

import requests

from .service import BadRequest, Conflict, FeedbackError, NotFound

_ERRORS = {400: BadRequest, 404: NotFound, 409: Conflict}


class FeedbackClient:
    def __init__(self, url="http://127.0.0.1:8010", api_key=None, timeout=10.0):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._headers = {"X-API-Key": api_key} if api_key else {}

    def _request(self, method, path, **kwargs):
        r = requests.request(method, f"{self.url}{path}", headers=self._headers,
                             timeout=self.timeout, **kwargs)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            err = _ERRORS.get(r.status_code, FeedbackError)(f"{detail}")
            err.status_code = r.status_code
            raise err
        return r.json()

    def _get(self, path, **params):
        return self._request("GET", path, params=params or None)

    # -- loops ---------------------------------------------------------------
    def loops(self) -> dict:
        return self._get("/loops")

    def status(self, name) -> dict:
        return self._get(f"/loops/{name}")

    def set(self, name, **settings) -> dict:
        """e.g. client.set("pointing_x", setpoint=1.2, kp=0.5, enabled=True)"""
        return self._request("PATCH", f"/loops/{name}", json=settings)

    def history(self, name, n=200, archive=False) -> dict:
        return self._get(f"/loops/{name}/history", n=n, archive=archive)

    def actuators(self) -> list:
        return self._get("/actuators")

    def detectors(self) -> list:
        return self._get("/detectors")

    # -- response matrices -------------------------------------------------
    def start_response_matrix(self, name, adjustable_names, detector_names,
                              **settings) -> dict:
        """e.g. client.start_response_matrix("orbit", ["cx1", "cx2"],
        ["bpm1", "bpm2"], step=0.1, settle_s=0.2)"""
        body = {"adjustable_names": list(adjustable_names),
                "detector_names": list(detector_names), **settings}
        return self._request("POST", f"/response-matrix/{name}", json=body)

    def response_matrices(self) -> dict:
        return self._get("/response-matrix")

    def response_matrix_status(self, name) -> dict:
        return self._get(f"/response-matrix/{name}")

    def delete_response_matrix(self, name) -> dict:
        return self._request("DELETE", f"/response-matrix/{name}")

    def schedule_response_matrix(self, name, adjustable_names, detector_names,
                                 interval_s, **settings) -> dict:
        body = {"adjustable_names": list(adjustable_names),
                "detector_names": list(detector_names),
                "interval_s": interval_s, **settings}
        return self._request("POST", f"/response-matrix/{name}/schedule", json=body)

    def unschedule_response_matrix(self, name) -> dict:
        return self._request("DELETE", f"/response-matrix/{name}/schedule")

    # -- closed-loop matrix correctors --------------------------------------
    def matrix_loops(self) -> dict:
        return self._get("/matrix-loops")

    def create_matrix_loop(self, name, matrix_name, **settings) -> dict:
        """e.g. client.create_matrix_loop("steering", "orbit", gain=0.3)"""
        return self._request("POST", f"/matrix-loops/{name}",
                             json={"matrix_name": matrix_name, **settings})

    def matrix_loop_status(self, name) -> dict:
        return self._get(f"/matrix-loops/{name}")

    def set_matrix_loop(self, name, **settings) -> dict:
        """e.g. client.set_matrix_loop("steering", enabled=True, gain=0.2)"""
        return self._request("PATCH", f"/matrix-loops/{name}", json=settings)

    def delete_matrix_loop(self, name) -> dict:
        return self._request("DELETE", f"/matrix-loops/{name}")

    def matrix_loop_history(self, name, n=200) -> dict:
        return self._get(f"/matrix-loops/{name}/history", n=n)

    def save_reference(self, name) -> dict:
        return self._request("POST", f"/matrix-loops/{name}/save-reference")

    # -- interlocks ----------------------------------------------------------
    def interlocks(self) -> dict:
        return self._get("/interlocks")

    def force_interlock(self, name, value) -> dict:
        return self._request("POST", f"/interlocks/{name}/force", json={"value": value})

    def clear_interlock(self, name) -> dict:
        return self._request("DELETE", f"/interlocks/{name}/force")

    # -- alarms / health -------------------------------------------------
    def alarms(self) -> dict:
        return self._get("/alarms")

    def health(self) -> dict:
        return self._get("/health")

    # -- config persistence ------------------------------------------------
    def save_config(self, path=None) -> dict:
        return self._request("POST", "/config/save", json={"path": path})

    def load_config(self, path=None) -> dict:
        return self._request("POST", "/config/load", json={"path": path})

    # -- logs --------------------------------------------------------------
    def logs(self, n=200, archive=False) -> list:
        return self._get("/logs", n=n, archive=archive)
