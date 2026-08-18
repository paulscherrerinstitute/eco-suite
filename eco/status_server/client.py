"""Thin client for the status/monitor server, meant as a drop-in
alternative to the direct-CA-fanout calls in eco.acquisition.daq_client.

This module is not imported anywhere yet - it exists so the REST calls a
future daq_client integration would make are visible and reviewable as
actual code, not just prose in DESIGN.md. See DESIGN.md, section
"Illustrative integration sketch", for how this would replace the body of
Daq.append_start_status_to_scan / Daq.append_status_to_scan_and_store.
"""

from __future__ import annotations

import requests


class StatusServerClient:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict:
        r = requests.get(f"{self.base_url}/health", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def snapshot(
        self,
        pgroup: str = None,
        run_number: int = None,
        aliases: list[str] = None,
        save: bool = False,
        key: str = "status_run_start",
    ) -> dict:
        body = {"aliases": aliases, "save": save, "key": key}
        if save:
            body["pgroup"] = pgroup
            body["run_number"] = run_number
        r = requests.post(
            f"{self.base_url}/status/snapshot", json=body, timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()

    def start_recording(self, recording_id: str, aliases: list[str] = None) -> dict:
        r = requests.post(
            f"{self.base_url}/recording/start",
            json={"recording_id": recording_id, "aliases": aliases},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def stop_recording(
        self,
        recording_id: str,
        pgroup: str = None,
        run_number: int = None,
        save: bool = True,
        filename: str = "monitors.esc.h5",
    ) -> dict:
        body = {"recording_id": recording_id, "save": save, "filename": filename}
        if save:
            body["pgroup"] = pgroup
            body["run_number"] = run_number
        r = requests.post(
            f"{self.base_url}/recording/stop", json=body, timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()
