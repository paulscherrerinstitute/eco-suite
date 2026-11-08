"""Shared JSON files behind the alarm system.

Everything the alarm system shares between processes -- operator settings,
user-added alarms, the armed flag, the monitor's live state -- lives in small
JSON files in one directory, so a limit changed in any session reaches the
monitor (and every display) without an extra network service. Default
directory: ``/sf/bernina/code/gac-bernina/eco_cnf_bernina/alarms``, override
with ``ECO_ALARM_DIR``. Without either (a workstation, a test) the files live
in memory only.

Files are rewritten *in place* under an ``fcntl`` lock rather than replaced
through a temp file: on NFS, opening an existing path re-validates its
attributes (close-to-open consistency) while a path that now points to a new
inode can be served from a stale directory cache for up to ``acdirmax``. A
reader that catches a half-written file simply retries.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import threading
import time
from pathlib import Path

DEFAULT_ALARM_DIR = "/sf/bernina/code/gac-bernina/eco_cnf_bernina/alarms"

_lock = threading.Lock()
_memory_files = {}


def alarm_dir():
    """The shared alarm directory, or `None` for in-memory operation."""
    d = os.environ.get("ECO_ALARM_DIR")
    if d:
        return Path(d)
    p = Path(DEFAULT_ALARM_DIR)
    if p.exists() or p.parent.exists():
        return p
    return None


class JsonFile:
    """One shared JSON document (a dict), read with a short cache.

    `ttl`: seconds a read may be served from cache. Writes always go to the
    file and refresh the cache.
    """

    def __init__(self, name, directory=None, ttl=2.0, default=None):
        self.name = name
        self._directory = directory
        self.ttl = ttl
        self._default = default if default is not None else {}
        self._cache = None
        self._cache_t = 0.0
        self._mtime = None

    @property
    def path(self):
        d = Path(self._directory) if self._directory else alarm_dir()
        return None if d is None else d / self.name

    # -- reading -----------------------------------------------------------
    def read(self, max_age=None):
        """Return a deep copy of the document."""
        max_age = self.ttl if max_age is None else max_age
        now = time.monotonic()
        if self._cache is not None and now - self._cache_t < max_age:
            return copy.deepcopy(self._cache)
        path = self.path
        if path is None:
            doc = _memory_files.get(self.name)
        else:
            doc = self._read_file(path)
        if doc is None:
            doc = copy.deepcopy(self._default)
        self._cache, self._cache_t = doc, now
        return copy.deepcopy(doc)

    def _read_file(self, path):
        for attempt in range(5):
            try:
                with open(path, "r") as f:
                    text = f.read()
            except FileNotFoundError:
                return None
            except OSError:
                return self._cache
            if not text.strip():
                # truncated by a concurrent in-place write
                time.sleep(0.02)
                continue
            try:
                return json.loads(text)
            except ValueError:
                time.sleep(0.02)
        return self._cache

    # -- writing -----------------------------------------------------------
    def update(self, func):
        """Read-modify-write under a lock: `func(doc)` changes `doc` in
        place (or returns a replacement). Returns the stored document."""
        with _lock:
            path = self.path
            if path is None:
                doc = copy.deepcopy(_memory_files.get(self.name, self._default))
                new = func(doc)
                doc = doc if new is None else new
                _memory_files[self.name] = copy.deepcopy(doc)
            else:
                doc = self._update_file(path, func)
            self._cache, self._cache_t = copy.deepcopy(doc), time.monotonic()
            return copy.deepcopy(doc)

    def write(self, doc):
        return self.update(lambda _old: doc)

    def _update_file(self, path, func):
        from eco.utilities.datafiles import ensure_dir, ensure_group_writable

        if not path.parent.exists():
            ensure_dir(path.parent)
        created = not path.exists()
        # "a+" creates without truncating, so the lock is taken before
        # anything is read or written
        with open(path, "a+") as f:
            try:
                fcntl.lockf(f, fcntl.LOCK_EX)
            except OSError:
                pass
            try:
                f.seek(0)
                text = f.read()
                try:
                    doc = json.loads(text) if text.strip() else copy.deepcopy(self._default)
                except ValueError:
                    doc = copy.deepcopy(self._default)
                new = func(doc)
                doc = doc if new is None else new
                out = json.dumps(doc, indent=1, sort_keys=True, default=_json_default)
                f.seek(0)
                f.truncate()
                f.write(out)
                f.flush()
                os.fsync(f.fileno())
            finally:
                try:
                    fcntl.lockf(f, fcntl.LOCK_UN)
                except OSError:
                    pass
        if created:
            ensure_group_writable(path)
        return doc


def append_jsonl(name, record, directory=None):
    """Append one record to a shared JSONL log (the alarm event history).
    Silently does nothing in in-memory mode; never raises."""
    d = Path(directory) if directory else alarm_dir()
    if d is None:
        _memory_files.setdefault(name, []).append(record)
        return
    try:
        from eco.utilities.datafiles import ensure_dir, ensure_group_writable

        if not d.exists():
            ensure_dir(d)
        path = d / name
        created = not path.exists()
        with open(path, "a") as f:
            f.write(json.dumps(record, default=_json_default) + "\n")
        if created:
            ensure_group_writable(path)
    except Exception:
        pass


def read_jsonl(name, directory=None, last=None):
    d = Path(directory) if directory else alarm_dir()
    if d is None:
        recs = list(_memory_files.get(name, []))
    else:
        recs = []
        try:
            with open(d / name) as f:
                for line in f:
                    try:
                        recs.append(json.loads(line))
                    except ValueError:
                        pass
        except FileNotFoundError:
            pass
    return recs[-last:] if last else recs


def _json_default(o):
    try:
        import numpy as np

        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    return str(o)


# The documents the alarm system uses, one place to find their names.
SETTINGS = JsonFile("settings.json")  # per-alarm operator overrides
CUSTOM = JsonFile("custom_alarms.json")  # alarms added on arbitrary detectors
SYSTEM = JsonFile("system.json")  # armed flag, escalation, local shift overrides
PEOPLE = JsonFile("people.json", ttl=30.0)  # name -> pushover user key
STATE = JsonFile("state.json", ttl=1.0)  # written by the monitor
SHIFT_CACHE = JsonFile("shift_plan_cache.json", ttl=10.0)
EVENTS_LOG = "events.jsonl"
