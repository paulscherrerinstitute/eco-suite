"""Thin HTTP client for eco.manual_control.box_server.

Small counterpart to eco.status_server.client.StatusServerClient, kept as
its own tiny module (not a copy of that one) since the surface here is a
few endpoints, not a whole namespace API:

    from eco.manual_control.box_server_client import BoxServerClient
    c = BoxServerClient("http://saresb-cons-05:8092")
    c.health()
    c.disconnect()   # the safety button
    c.reconnect()    # offer a session again; the box operator must accept
    c.restart()      # re-exec the process to pick up new device code
"""

from __future__ import annotations

import requests


class BoxServerError(RuntimeError):
    pass


class BoxServerClient:
    def __init__(self, base_url, timeout=10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self):
        r = requests.get(f"{self.base_url}/health", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def disconnect(self):
        """Release the box now - it falls back to its waiting screen."""
        r = requests.post(f"{self.base_url}/admin/disconnect", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def reconnect(self):
        """Offer this session to the box again. Same as any other session -
        the operator standing at the box still has to accept it."""
        r = requests.post(f"{self.base_url}/admin/reconnect", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def restart(self, delay=0.5):
        """Re-exec the server process, so device modules edited since it
        started are picked up. Disconnects the box first."""
        r = requests.post(f"{self.base_url}/admin/restart", json={"delay": delay},
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()
