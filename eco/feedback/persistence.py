"""Durable storage: a SQLite-backed archive for history/logs, and YAML
config save/load.

Kept separate from core.py: those classes are control-loop domain logic;
this module is purely an I/O concern.
"""

from __future__ import annotations

import queue
import sqlite3
import threading
import time

import numpy as np
import yaml

from .core import FeedbackManager, ResponseMatrix


class PersistentStore:
    """SQLite archive for loop history and the event log.

    Writes go through a queue drained by one background thread that batches
    inserts (time- or size-triggered flush), so the hot control-loop threads
    never block on disk I/O -- they only ever call the non-blocking
    ``record_*`` methods.
    """

    def __init__(self, path, flush_every_s=1.0, batch_size=500):
        self.path = path
        self.flush_every_s = flush_every_s
        self.batch_size = batch_size
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._init_schema()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="persistence")
        self._thread.start()

    def _init_schema(self):
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS history "
            "(loop TEXT, ts REAL, value REAL, output REAL)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS logs "
            "(ts REAL, level TEXT, source TEXT, message TEXT)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_loop_ts ON history(loop, ts)")
        self._conn.commit()

    # -- non-blocking write side, called from control-loop threads ---------
    def record_history(self, loop_name, t, value, output):
        self._queue.put(("history", (loop_name, t, value, output)))

    def record_log(self, entry: dict):
        self._queue.put(("log", (entry["ts"], entry["level"],
                                 entry["source"], entry["message"])))

    # -- background writer ---------------------------------------------------
    def _run(self):
        batch_hist, batch_log = [], []
        last_flush = time.monotonic()
        while True:
            try:
                kind, row = self._queue.get(timeout=0.2)
                (batch_hist if kind == "history" else batch_log).append(row)
            except queue.Empty:
                pass
            due = time.monotonic() - last_flush >= self.flush_every_s
            full = len(batch_hist) + len(batch_log) >= self.batch_size
            if (due or full) and (batch_hist or batch_log):
                self._flush(batch_hist, batch_log)
                batch_hist, batch_log = [], []
                last_flush = time.monotonic()
            if self._stop.is_set() and self._queue.empty():
                break
        self._flush(batch_hist, batch_log)

    def _flush(self, batch_hist, batch_log):
        with self._conn:
            if batch_hist:
                self._conn.executemany(
                    "INSERT INTO history VALUES (?,?,?,?)", batch_hist)
            if batch_log:
                self._conn.executemany(
                    "INSERT INTO logs VALUES (?,?,?,?)", batch_log)

    # -- read side -----------------------------------------------------------
    def history(self, loop_name, since=None, limit=1000) -> dict:
        q = "SELECT ts, value, output FROM history WHERE loop=?"
        params = [loop_name]
        if since is not None:
            q += " AND ts >= ?"
            params.append(since)
        q += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(q, params).fetchall()
        rows.reverse()
        return {
            "t": [r[0] for r in rows],
            "value": [r[1] for r in rows],
            "output": [r[2] for r in rows],
        }

    def logs(self, n=200) -> list[dict]:
        rows = self._conn.execute(
            "SELECT ts, level, source, message FROM logs "
            "ORDER BY ts DESC LIMIT ?", (n,)).fetchall()
        rows.reverse()
        return [{"ts": r[0], "level": r[1], "source": r[2], "message": r[3]}
                for r in rows]

    def close(self):
        self._stop.set()
        self._thread.join(timeout=5)
        self._conn.close()


# -- config save/load (YAML) --------------------------------------------------

def save_config(manager: FeedbackManager, path):
    data = {
        "loops": {
            name: {
                "setpoint": loop.setpoint, "rate_hz": loop.rate_hz,
                "enabled": loop.enabled, "kp": loop.pid.kp, "ki": loop.pid.ki,
                "kd": loop.pid.kd, "alarm_after_s": loop.alarm_after_s,
                "auto_disable_on_alarm": loop.auto_disable_on_alarm,
            }
            for name, loop in manager.loops.items()
        },
        "matrix_loops": {
            name: {
                "matrix_name": ml.matrix_name, "enabled": ml.enabled,
                "gain": ml.gain, "rcond": ml.rcond, "rate_hz": ml.rate_hz,
                "reference": ml.reference, "weights": ml.weights,
            }
            for name, ml in manager.matrix_loops.items()
        },
        "response_matrices": {
            name: rm.to_dict() for name, rm in manager.matrices.items()
        },
        "interlocks_forced": {
            name: il.forced for name, il in manager.interlocks.items()
            if il.forced is not None
        },
    }
    # The config usually lives on a tree other beamline accounts must be
    # able to rewrite -- same rule as every other eco write there.
    from ..utilities.datafiles import open_group_writable

    with open_group_writable(path, "w") as f:
        yaml.safe_dump(_plain(data), f, sort_keys=False)


def _plain(obj):
    """numpy scalars/arrays -> plain Python, so ``safe_dump`` accepts them."""
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def load_config(manager: FeedbackManager, path):
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    for name, cfg in data.get("response_matrices", {}).items():
        manager.matrices[name] = ResponseMatrix(
            adjustable_names=cfg["adjustable_names"],
            detector_names=cfg["detector_names"],
            matrix=np.array(cfg["matrix"]), step=cfg["step"],
            timestamp=cfg["timestamp"],
        )

    for name, cfg in data.get("loops", {}).items():
        loop = manager.loops.get(name)
        if loop is None:
            continue
        loop.setpoint = cfg.get("setpoint", loop.setpoint)
        loop.rate_hz = cfg.get("rate_hz", loop.rate_hz)
        loop.pid.kp = cfg.get("kp", loop.pid.kp)
        loop.pid.ki = cfg.get("ki", loop.pid.ki)
        loop.pid.kd = cfg.get("kd", loop.pid.kd)
        loop.alarm_after_s = cfg.get("alarm_after_s", loop.alarm_after_s)
        loop.auto_disable_on_alarm = cfg.get(
            "auto_disable_on_alarm", loop.auto_disable_on_alarm)
        loop.enable() if cfg.get("enabled") else loop.disable()

    for name, cfg in data.get("matrix_loops", {}).items():
        ml = manager.matrix_loops.get(name)
        if ml is None:
            matrix_name = cfg["matrix_name"]
            if matrix_name not in manager.matrices:
                continue
            ml = manager.add_matrix_loop(
                name, matrix_name, gain=cfg.get("gain", 0.3),
                rcond=cfg.get("rcond", 1e-3), rate_hz=cfg.get("rate_hz", 10.0))
        ml.gain = cfg.get("gain", ml.gain)
        ml.rcond = cfg.get("rcond", ml.rcond)
        ml.reference.update(cfg.get("reference", {}))
        ml.weights.update(cfg.get("weights", {}))
        ml.enable() if cfg.get("enabled") else ml.disable()

    for name, forced in data.get("interlocks_forced", {}).items():
        if name in manager.interlocks:
            manager.interlocks[name].force(forced)
