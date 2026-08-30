"""Small JSON-file-backed values, the driver's equivalent of AdjustableFS.

The pshell deployment kept ``frame``/``tool``/``remote_allowed*`` in one-value
JSON files under ``adjustables_fs/`` via a hand-rolled ``AdjustableFS`` class,
and this package keeps the same files and the same on-disk format
(``{"value": ...}``) so a migrated server picks up the existing state --
in particular the recorded remote-motion trajectories, which represent real
operator effort and cannot be regenerated.

Two things are fixed relative to the original:

* **Atomic writes.** The original opened the file with ``"w"`` (truncate) and
  serialised straight into it. Anything reading concurrently -- another eco
  session, a second server, the poller itself -- could observe a truncated or
  half-written file. Writes here go to a temporary file in the same directory
  and are then ``os.replace``d, which is atomic on POSIX.
* **Write-on-change only.** ``doUpdate_env`` called ``self.frame(sts[8])`` and
  ``self.tool(sts[9])`` on *every* environment poll, i.e. it rewrote two JSON
  files once a second forever, on NFS. Setting an unchanged value is now a
  no-op.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class JsonValue:
    """One persisted value. Callable like the original ``AdjustableFS``.

    ``v()`` reads, ``v(x)`` writes. Note the original's quirk, kept for
    compatibility: ``v(None)`` *reads* rather than writing ``None``.
    """

    def __init__(self, name=None, default_value=None, file_path=None, base_path=None):
        if file_path is None:
            if base_path is None or name is None:
                raise ValueError("give either file_path, or both base_path and name")
            file_path = Path(base_path) / name
        self.file_path = Path(file_path)
        self.name = name if name is not None else self.file_path.name
        self._lock = threading.Lock()
        self._cached = None
        self._loaded = False
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self.write_value(default_value)

    def get_current_value(self):
        with self._lock:
            try:
                self._cached = json.loads(self.file_path.read_text())["value"]
            except (OSError, ValueError, KeyError) as exc:
                if not self._loaded:
                    raise
                # Keep serving the last good value rather than taking the
                # server down because NFS hiccuped mid-read.
                logger.warning("re-reading %s failed (%s); using cached value",
                               self.file_path, exc)
            self._loaded = True
            return self._cached

    def write_value(self, value):
        with self._lock:
            tmp = self.file_path.with_name(f".{self.file_path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"value": value}, indent=4))
            os.replace(tmp, self.file_path)
            self._cached = value
            self._loaded = True

    def set_if_changed(self, value) -> bool:
        """Write only when the value actually differs. Returns True if written."""
        try:
            if self.get_current_value() == value:
                return False
        except Exception:
            pass
        self.write_value(value)
        return True

    def __call__(self, value=None):
        if value is not None:
            self.write_value(value)
            return None
        return self.get_current_value()

    def __repr__(self):
        return f"<JsonValue {self.name}={self._cached!r} at {self.file_path}>"


class MemoryValue:
    """Same interface, no file. For tests and for simulated servers."""

    def __init__(self, name=None, default_value=None, **_):
        self.name = name
        self._value = default_value

    def get_current_value(self):
        return self._value

    def write_value(self, value):
        self._value = value

    def set_if_changed(self, value) -> bool:
        if self._value == value:
            return False
        self._value = value
        return True

    def __call__(self, value=None):
        if value is not None:
            self._value = value
            return None
        return self._value

    def __repr__(self):
        return f"<MemoryValue {self.name}={self._value!r}>"
