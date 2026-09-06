"""A small ring buffer of recent status-server operations.

Exists for exactly one question, asked from the CLI/GUI rather than by
grepping the log: "is this server actually serving fast, and did anything
just fail?" `/health` answers "is the namespace healthy"; this answers "is
answering requests healthy" - the two are independent (a server can hold a
perfectly good namespace and still be slow or erroring on writes because the
NFS mount it writes status.json to is having a bad day).

Kept in-process, not persisted: a few hundred entries is enough for "what
just happened", and surviving a restart is not the point - `/health`'s
`generation`/`last_init_*` already covers "what happened across restarts".
"""

from __future__ import annotations

import threading
import time
from collections import deque


class QueryStats:
    def __init__(self, maxlen: int = 200):
        self._lock = threading.Lock()
        self._history = deque(maxlen=maxlen)

    def record(self, kind: str, duration_s: float, error: str | None = None,
              **fields) -> dict:
        """Add one completed operation. `fields` is whatever is worth
        showing for that `kind` - e.g. `n_entries`, `pgroup`, `run_number`,
        `key`, `snapshot_s`/`write_s`/`upload_s` for a capture job."""
        entry = {
            "kind": kind,
            "at": time.time(),
            "duration_s": duration_s,
            "error": error,
            **fields,
        }
        with self._lock:
            self._history.append(entry)
        return entry

    def recent(self, limit: int | None = None, kind: str | None = None) -> list[dict]:
        with self._lock:
            items = list(self._history)
        if kind:
            items = [i for i in items if i["kind"] == kind]
        if limit:
            items = items[-limit:]
        return items

    def summary(self) -> dict:
        with self._lock:
            items = list(self._history)
        if not items:
            return {"n": 0, "n_errors": 0}
        durations = [i["duration_s"] for i in items if i.get("duration_s") is not None]
        errors = [i for i in items if i.get("error")]
        last = items[-1]
        last_error = next((i for i in reversed(items) if i.get("error")), None)
        return {
            "n": len(items),
            "n_errors": len(errors),
            "last_at": last["at"],
            "last_kind": last["kind"],
            "last_duration_s": last.get("duration_s"),
            "last_error": last.get("error"),
            "last_error_at": last_error["at"] if last_error else None,
            "avg_duration_s": sum(durations) / len(durations) if durations else None,
            "max_duration_s": max(durations) if durations else None,
            "min_duration_s": min(durations) if durations else None,
        }


class timed:
    """Context manager: measure a block and record it on `stats` when done,
    whichever way it ends.

        with timed(stats, "snapshot", n_entries=len(snap["status"])) as t:
            snap = store.snapshot()
            t.fields["n_entries"] = len(snap["status"])

    An exception inside the block is recorded as the operation's error
    (str(exc)) and re-raised unchanged - this never swallows anything.
    """

    def __init__(self, stats: QueryStats, kind: str, **fields):
        self.stats = stats
        self.kind = kind
        self.fields = fields
        self.error = None

    def __enter__(self):
        self._t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.error = f"{exc_type.__name__}: {exc}"
        self.stats.record(self.kind, time.time() - self._t0, error=self.error,
                          **self.fields)
        return False
