"""Long-lived EPICS Channel-Access monitoring for a whole namespace.

This is the piece that actually removes the network traffic problem in
``Daq.append_start_status_to_scan`` / ``append_status_to_scan_and_store``
(eco/acquisition/daq_client.py). Today, every scan start and every scan end
calls ``namespace.get_status()``, which issues one live ``PV.get()`` per
channel (most Detector* wrappers in eco/epics/detector.py are constructed
with ``auto_monitor=False``) for every aliased channel in the namespace -
~1120 of them for bernina - fanned out over a 20-worker thread pool
(eco/elements/assembly.py:285). That's a burst of ~1100 concurrent CA GET
round trips, from scratch, at every single scan boundary.

Here, each channel gets exactly one CA subscription (``auto_monitor=True``),
created once at service startup and kept open for the lifetime of the
process. Two things build on top of that single subscription:

* LatestValueCache - always on. Keeps only the most recent
  (value, timestamp) per channel. Answering a "status snapshot" request is
  then just a dict copy - zero additional CA traffic, regardless of how
  many snapshots are requested or by how many concurrent scans/sessions.

* RecordingSession - opt-in, started/stopped per run via the REST API.
  Appends every update of a chosen channel subset to an in-memory buffer,
  for later export to an ArrayTimestamps-style h5 file (see storage.py).
  Recording reuses the very same PV objects as the latest-value cache
  (adds a second callback on top) rather than opening a second CA
  subscription per channel.

This mirrors the monitor/callback patterns already used elsewhere in eco
(eco.epics.utilities_epics.Monitor, eco.epics.detector.CallbackEpics) -
same idea (attach/detach an add_callback on a live PV), just centralised
and made permanent instead of per-scan.
"""

from __future__ import annotations

import logging
from threading import RLock
from time import time as _time_now
from typing import Iterable, Optional

from epics import PV

from .channel_registry import Channel

logger = logging.getLogger(__name__)


class LatestValueCache:
    def __init__(self, pvs: dict[str, PV]):
        self._pvs = pvs
        self._lock = RLock()
        self._latest: dict[str, dict] = {}
        self._callback_ids: dict[str, int] = {}
        for alias, pv in pvs.items():
            # with_ctrlvars=False matters at this scale: pyepics's default
            # (True) makes add_callback() issue a blocking get_ctrlvars()
            # CA round trip (units/limits) for every already-connected PV,
            # which for ~7000 channels reproduces the exact get-storm this
            # service exists to avoid - confirmed by hanging a test run of
            # this at full namespace scale before this fix.
            self._callback_ids[alias] = pv.add_callback(
                self._make_callback(alias), with_ctrlvars=False
            )
            # Seed the cache immediately so a snapshot taken before the
            # first live update still has a value, instead of missing the
            # channel entirely. One-time cost at connection, not per call.
            if pv.connected:
                self._record(alias, pv.get(), pv.timestamp)

    def _make_callback(self, alias):
        def _on_update(value=None, timestamp=None, **kwargs):
            self._record(alias, value, timestamp)

        return _on_update

    def _record(self, alias, value, timestamp):
        with self._lock:
            self._latest[alias] = {
                "value": value,
                "timestamp": timestamp,
                "timestamp_local": _time_now(),
            }

    def snapshot(self, aliases: Optional[Iterable[str]] = None) -> dict:
        with self._lock:
            if aliases is None:
                return {a: dict(v) for a, v in self._latest.items()}
            return {a: dict(self._latest[a]) for a in aliases if a in self._latest}

    def connection_report(self) -> dict:
        return {alias: pv.connected for alias, pv in self._pvs.items()}


class RecordingSession:
    """One requested "monitor all values while this run is going" job."""

    def __init__(self, recording_id: str, channels: list[Channel], pvs: dict[str, PV]):
        self.recording_id = recording_id
        self.channels = channels
        self.started_at = None
        self.stopped_at = None
        self._pvs = pvs
        self._lock = RLock()
        self._buffers = {
            ch.alias: {"values": [], "timestamps": [], "timestamps_local": []}
            for ch in channels
        }
        self._callback_ids: dict[str, int] = {}

    def start(self):
        self.started_at = _time_now()
        for ch in self.channels:
            pv = self._pvs[ch.alias]
            # see the comment in LatestValueCache.__init__ - same reason.
            self._callback_ids[ch.alias] = pv.add_callback(
                self._make_callback(ch.alias), with_ctrlvars=False
            )

    def _make_callback(self, alias):
        def _on_update(value=None, timestamp=None, **kwargs):
            with self._lock:
                buf = self._buffers[alias]
                buf["values"].append(value)
                buf["timestamps"].append(timestamp)
                buf["timestamps_local"].append(_time_now())

        return _on_update

    def stop(self) -> dict:
        for alias, cb_id in self._callback_ids.items():
            self._pvs[alias].remove_callback(cb_id)
        self._callback_ids = {}
        self.stopped_at = _time_now()
        with self._lock:
            return {alias: dict(buf) for alias, buf in self._buffers.items()}

    @property
    def is_running(self) -> bool:
        return self.started_at is not None and self.stopped_at is None


class MonitorStore:
    """Owns the one CA subscription per channel and hands out both the
    always-on latest-value cache and on-demand recording sessions on top
    of it."""

    def __init__(self, channels: list[Channel], connection_timeout: float = 5.0):
        self.channels = channels
        self._pvs: dict[str, PV] = {}
        for ch in channels:
            pv = PV(ch.pvname, auto_monitor=True, connection_timeout=connection_timeout)
            self._pvs[ch.alias] = pv
        unconnected = [alias for alias, pv in self._pvs.items() if not pv.wait_for_connection(timeout=connection_timeout)]
        if unconnected:
            logger.warning(
                "%d/%d channels did not connect within %.1fs: %s",
                len(unconnected),
                len(channels),
                connection_timeout,
                ", ".join(unconnected[:20]) + (" ..." if len(unconnected) > 20 else ""),
            )
        self.latest = LatestValueCache(self._pvs)
        self._recordings: dict[str, RecordingSession] = {}
        self._recordings_lock = RLock()

    def start_recording(self, recording_id: str, aliases: Optional[Iterable[str]] = None) -> RecordingSession:
        with self._recordings_lock:
            if recording_id in self._recordings and self._recordings[recording_id].is_running:
                raise ValueError(f"recording '{recording_id}' is already running")
            if aliases is None:
                channels = self.channels
            else:
                by_alias = {ch.alias: ch for ch in self.channels}
                missing = [a for a in aliases if a not in by_alias]
                if missing:
                    raise KeyError(f"unknown channel alias(es): {missing}")
                channels = [by_alias[a] for a in aliases]
            session = RecordingSession(recording_id, channels, self._pvs)
            session.start()
            self._recordings[recording_id] = session
            return session

    def stop_recording(self, recording_id: str) -> dict:
        with self._recordings_lock:
            session = self._recordings.get(recording_id)
            if session is None:
                raise KeyError(f"no such recording '{recording_id}'")
            data = session.stop()
            return {
                "recording_id": recording_id,
                "started_at": session.started_at,
                "stopped_at": session.stopped_at,
                "channels": [ch.alias for ch in session.channels],
                "data": data,
            }

    def list_recordings(self) -> list[dict]:
        with self._recordings_lock:
            return [
                {
                    "recording_id": rid,
                    "running": s.is_running,
                    "started_at": s.started_at,
                    "stopped_at": s.stopped_at,
                    "n_channels": len(s.channels),
                }
                for rid, s in self._recordings.items()
            ]
