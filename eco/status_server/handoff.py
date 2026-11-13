"""Handing data back to the client instead of writing it for them.

Background
----------
Everything the status server produces for a run - ``status.json``,
``aliases.json``, ``namespace_monitor.ixp.h5`` - is written *by the server
process* into ``/sf/<instrument>/data/<pgroup>/res/run_data/daq/run<N>/aux``
and then handed to sf_daq_broker's ``copy_user_files``. That makes the run's
own bookkeeping depend on the server account's standing in the shared tree:
its pgroup membership, the mode/group bits of every directory level, and -
on NFSv4 with ``sec=krb5`` - a Kerberos ticket that a long-running service
account loses after a few days. None of those have anything to do with the
measurement, and all of them fail silently-ish, at a scan boundary, for a
run that is otherwise fine.

The session asking for the status has none of those problems. It runs as the
scientist, it is already writing the scan's own files into the same
directory, and its credentials are fresh by construction.

So this module adds the other half of the split: the server keeps doing the
part only it can do (holding an initialized namespace, holding thousands of
CA monitors, encoding a recording), parks the finished bytes in a **handoff
slot**, and the client picks them up and writes them itself.

What a slot is
--------------
One result, waiting to be collected exactly once:

* created ``pending`` by the route that accepts the request, so the client
  gets an id back immediately and the expensive part (a 10-20 s snapshot, an
  h5 encode of a few thousand channels) runs in the background;
* completed with either a bytes payload or a path in the server's **own**
  spool directory - local disk the server unambiguously owns, never the
  shared tree;
* fetched by the client (``GET /handoff/<id>/data``), which may arrive
  *before* the payload is ready and simply wait for it;
* released - by the fetch itself (``release=1``, the normal case), by an
  explicit ``DELETE``, or by the janitor once the slot's TTL is up.

The TTL and the slot cap are the whole leak story: a client that crashes
between asking and fetching costs one slot and its spool file for at most
``ttl`` seconds. Nothing here retries, re-sends or queues - a lost handoff
is one run missing one aux file, which is what the old server-side write
already degraded to when it could not write.

Spool files live under a per-user directory in the OS temp dir (see
eco.utilities.tempfiles for why the username has to be in the path: several
accounts run this code on the same console hosts, and whoever creates a
fixed ``/tmp`` name first owns it).
"""

from __future__ import annotations

import getpass
import io
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Kinds a slot can carry. Only used for reporting/content-type defaults -
# the store itself does not care what is in a slot.
KIND_STATUS = "status"
KIND_ALIASES = "aliases"
KIND_RECORDING = "recording"

DEFAULT_TTL = 3600.0
DEFAULT_MAX_SLOTS = 64


class HandoffError(RuntimeError):
    pass


class HandoffCapacity(HandoffError):
    """Too many slots are already waiting to be collected."""


def default_spool_dir() -> Path:
    """Where spooled payloads go: a per-user directory in the OS temp dir.

    Per-user because a fixed name under ``/tmp`` belongs to whichever
    account touches it first and is then unwritable for every other one -
    the same trap eco.utilities.tempfiles exists for. Overridable with
    ``ECO_STATUS_HANDOFF_SPOOL`` (the server config's
    ``handoff_spool_dir`` sets that, or passes the path directly).
    """
    env = os.environ.get("ECO_STATUS_HANDOFF_SPOOL")
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / f"eco_status_handoff_{getpass.getuser()}"


class HandoffSlot:
    """One pending-or-ready result. Not created directly - see
    :meth:`HandoffStore.create`."""

    __slots__ = (
        "id", "kind", "filename", "content_type", "meta", "ttl",
        "created_at", "ready_at", "expires_at", "state", "error",
        "_data", "_path", "_owns_path", "n_bytes",
        "n_fetches", "last_fetch_at", "_ready",
    )

    def __init__(self, slot_id, kind, filename, content_type, meta, ttl):
        self.id = slot_id
        self.kind = kind
        self.filename = filename
        self.content_type = content_type
        self.meta = dict(meta or {})
        self.ttl = ttl
        self.created_at = time.time()
        self.ready_at = None
        self.expires_at = self.created_at + ttl
        self.state = "pending"
        self.error = None
        self._data = None
        self._path = None
        self._owns_path = False
        self.n_bytes = None
        self.n_fetches = 0
        self.last_fetch_at = None
        self._ready = threading.Event()

    # -- state ------------------------------------------------------------

    @property
    def is_ready(self):
        return self.state == "ready"

    def wait(self, timeout=None):
        """Block until this slot is ready or failed. Returns True if the
        wait ended because the slot settled, False on timeout."""
        return self._ready.wait(timeout)

    def report(self, with_meta=True):
        rep = {
            "handoff_id": self.id,
            "kind": self.kind,
            "filename": self.filename,
            "content_type": self.content_type,
            "state": self.state,
            "error": self.error,
            "n_bytes": self.n_bytes,
            "spooled": self._path is not None,
            "created_at": self.created_at,
            "ready_at": self.ready_at,
            "expires_at": self.expires_at,
            "n_fetches": self.n_fetches,
            "last_fetch_at": self.last_fetch_at,
        }
        if with_meta:
            rep["meta"] = dict(self.meta)
        return rep

    # -- payload ----------------------------------------------------------

    def open(self):
        """``(file object, n_bytes)`` for streaming this slot's payload.

        The caller closes the file object. Raises HandoffError unless the
        slot is ready.
        """
        if self.state == "error":
            raise HandoffError(f"handoff '{self.id}' failed: {self.error}")
        if self.state != "ready":
            raise HandoffError(f"handoff '{self.id}' is '{self.state}'")
        if self._path is not None:
            return open(self._path, "rb"), self.n_bytes
        return io.BytesIO(self._data), self.n_bytes

    def _discard(self):
        self._data = None
        if self._path is not None and self._owns_path:
            try:
                os.unlink(self._path)
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("could not remove spool file %s", self._path,
                               exc_info=True)
            # The per-slot directory goes with it (see
            # HandoffStore.slot_spool_dir for why there is one), but only
            # when it is unmistakably ours and empty.
            parent = self._path.parent
            if parent.name == self.id:
                try:
                    parent.rmdir()
                except OSError:
                    pass
        self._path = None


class HandoffStore:
    """The server's set of handoff slots.

    Thread-safe; every route touching it does so from a Flask worker or a
    background capture thread.
    """

    def __init__(self, spool_dir=None, ttl=DEFAULT_TTL,
                 max_slots=DEFAULT_MAX_SLOTS, janitor=True,
                 janitor_interval=60.0):
        self.spool_dir = Path(spool_dir) if spool_dir else default_spool_dir()
        self.ttl = float(ttl)
        self.max_slots = int(max_slots)
        self._slots = {}
        self._lock = threading.Lock()
        self._janitor_interval = float(janitor_interval)
        self._janitor = None
        if janitor:
            self._janitor = threading.Thread(
                target=self._janitor_loop, name="handoff-janitor", daemon=True
            )
            self._janitor.start()

    # -- lifecycle --------------------------------------------------------

    def create(self, kind, filename, content_type="application/octet-stream",
               meta=None, ttl=None) -> HandoffSlot:
        slot_id = uuid.uuid4().hex[:16]
        slot = HandoffSlot(slot_id, kind, filename, content_type, meta,
                           float(ttl) if ttl else self.ttl)
        self.purge_expired()
        with self._lock:
            if len(self._slots) >= self.max_slots:
                # Dropping somebody else's pending result to make room would
                # turn one stuck client into a silent data loss for another.
                raise HandoffCapacity(
                    f"{len(self._slots)} handoff slots already waiting "
                    f"(max_slots={self.max_slots}); collect or release them "
                    f"first (GET /handoff)"
                )
            self._slots[slot_id] = slot
        return slot

    def get(self, slot_id) -> HandoffSlot:
        with self._lock:
            slot = self._slots.get(slot_id)
        if slot is None:
            raise KeyError(f"no such handoff '{slot_id}'")
        return slot

    def complete_bytes(self, slot_id, data: bytes):
        slot = self.get(slot_id)
        slot._data = bytes(data)
        slot.n_bytes = len(slot._data)
        slot.ready_at = time.time()
        slot.state = "ready"
        slot._ready.set()
        return slot

    def complete_file(self, slot_id, path, owns=True):
        """Complete a slot from a file the server already wrote - normally
        in :attr:`spool_dir`, which this store then deletes on release
        (``owns=True``)."""
        slot = self.get(slot_id)
        path = Path(path)
        slot._path = path
        slot._owns_path = bool(owns)
        slot.n_bytes = path.stat().st_size
        slot.ready_at = time.time()
        slot.state = "ready"
        slot._ready.set()
        return slot

    def fail(self, slot_id, error):
        slot = self.get(slot_id)
        slot.error = str(error)
        slot.state = "error"
        slot.ready_at = time.time()
        slot._ready.set()
        return slot

    def mark_fetched(self, slot_id):
        slot = self.get(slot_id)
        slot.n_fetches += 1
        slot.last_fetch_at = time.time()
        return slot

    def release(self, slot_id, missing_ok=False):
        with self._lock:
            slot = self._slots.pop(slot_id, None)
        if slot is None:
            if missing_ok:
                return None
            raise KeyError(f"no such handoff '{slot_id}'")
        slot._discard()
        return slot

    # -- housekeeping -----------------------------------------------------

    def purge_expired(self, now=None):
        now = now or time.time()
        with self._lock:
            dead = [s for s in self._slots.values() if s.expires_at < now]
            for slot in dead:
                self._slots.pop(slot.id, None)
        for slot in dead:
            if slot.state == "pending":
                # The producer thread may still be running and will complete
                # into a slot nobody can reach any more; harmless, and much
                # safer than tearing its target out from under it.
                logger.warning("handoff %s (%s) expired while still pending",
                               slot.id, slot.kind)
            elif slot.n_fetches == 0:
                logger.warning("handoff %s (%s, %s bytes) expired uncollected",
                               slot.id, slot.kind, slot.n_bytes)
            slot._discard()
        return [s.id for s in dead]

    def _janitor_loop(self):
        while True:
            time.sleep(self._janitor_interval)
            try:
                self.purge_expired()
            except Exception:  # noqa: BLE001 - a janitor must not die
                logger.warning("handoff janitor failed", exc_info=True)

    def report(self):
        with self._lock:
            slots = list(self._slots.values())
        return [s.report() for s in slots]

    def stats(self):
        with self._lock:
            slots = list(self._slots.values())
        return {
            "n_slots": len(slots),
            "max_slots": self.max_slots,
            "n_pending": sum(1 for s in slots if s.state == "pending"),
            "n_ready": sum(1 for s in slots if s.state == "ready"),
            "n_error": sum(1 for s in slots if s.state == "error"),
            "n_bytes": sum(s.n_bytes or 0 for s in slots),
            "ttl": self.ttl,
            "spool_dir": str(self.spool_dir),
        }

    def close(self):
        """Release everything - for tests and for a clean shutdown."""
        with self._lock:
            slots = list(self._slots.values())
            self._slots.clear()
        for slot in slots:
            slot._discard()

    # -- spool ------------------------------------------------------------

    def slot_spool_dir(self, slot_id) -> Path:
        """A private directory for one slot's spooled file.

        Per slot rather than one flat directory because
        :func:`eco.status_server.storage.write_monitor_recording` wants a
        directory plus a filename, and the filename is the one the client
        will write (``namespace_monitor.ixp.h5``) - two concurrent
        recordings would otherwise land on the same path.
        """
        d = self.spool_dir / slot_id
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        return d

    def clean_spool_dir(self):
        """Remove leftovers from a previous process.

        Spool files are owned by this server and nothing else reads them, so
        anything already in there when a server starts is from a run that
        did not get to release it - the process is gone, the slots are gone,
        and the bytes are dead weight.
        """
        root = self.spool_dir
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            logger.warning("could not create handoff spool dir %s", root,
                           exc_info=True)
            return 0
        removed = 0
        for child in root.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                removed += 1
            except OSError:
                logger.warning("could not remove stale spool entry %s", child,
                               exc_info=True)
        if removed:
            logger.info("removed %d stale handoff spool entries from %s",
                        removed, root)
        return removed


# -- payload encoding -----------------------------------------------------
#
# One recording, three ways out. "h5" is the canonical one: the file the
# client writes is then byte-for-byte what the server-side write produced,
# built by the very same function, so the two modes cannot drift apart in
# format. The other two exist for measuring and for clients that want the
# numbers rather than the file.

RECORDING_FORMATS = ("h5", "npz", "json")

CONTENT_TYPES = {
    "h5": "application/x-hdf5",
    "npz": "application/octet-stream",
    "json": "application/json",
}


def _as_npz_array(values):
    """A recording buffer as an array ``np.savez`` can hold, or None.

    Deliberately not storage._as_storable_array: that one encodes strings
    as h5py's variable-length object dtype, which npz cannot load back
    without allow_pickle. Fixed-width bytes is the npz-native equivalent
    and round-trips with allow_pickle off, which is how anything reading
    an untrusted file should load it.
    """
    import numpy as np

    arr = np.asarray(values)
    if arr.dtype.kind in "fiub":
        return arr
    if arr.dtype.kind in "US":
        return arr.astype("S")
    return None


def encode_recording_npz(recording: dict) -> bytes:
    """Recording buffers as a single uncompressed ``.npz``.

    Keys are ``v:<alias>`` / ``t:<alias>`` plus ``__meta__`` (a JSON blob
    with the alias list and the start/stop times), because npz has no
    nesting. Uncompressed on purpose: these are float64 timestamps and
    mostly-slowly-varying values, where zlib costs more CPU than it saves
    bytes on a link that is a beamline LAN.
    """
    import numpy as np

    buffers, skipped = {}, {}
    aliases = []
    for alias, buf in (recording.get("data") or {}).items():
        try:
            data = _as_npz_array(buf["values"])
            if data is None:
                skipped[alias] = "value type not storable"
                continue
            buffers[f"v:{alias}"] = data
            buffers[f"t:{alias}"] = np.asarray(buf["timestamps"], dtype=float)
            aliases.append(alias)
        except Exception as exc:  # noqa: BLE001 - one channel of thousands
            skipped[alias] = f"{type(exc).__name__}: {exc}"
    meta = {
        "aliases": aliases,
        "started_at": recording.get("started_at"),
        "stopped_at": recording.get("stopped_at"),
        "skipped": dict(list(skipped.items())[:50]),
        "n_skipped": len(skipped),
    }
    buffers["__meta__"] = np.frombuffer(
        json.dumps(meta).encode("utf-8"), dtype=np.uint8
    )
    out = io.BytesIO()
    np.savez(out, **buffers)
    recording["write_report"] = {"n_written": len(aliases),
                                 "n_skipped": len(skipped),
                                 "skipped": meta["skipped"]}
    return out.getvalue()


def encode_recording_json(recording: dict) -> bytes:
    from .storage import NumpyEncoder

    payload = {
        "started_at": recording.get("started_at"),
        "stopped_at": recording.get("stopped_at"),
        "data": recording.get("data") or {},
    }
    body = json.dumps(payload, cls=NumpyEncoder).encode("utf-8")
    recording["write_report"] = {"n_written": len(payload["data"]),
                                 "n_skipped": 0, "skipped": {}}
    return body


def encode_recording_h5(recording: dict, directory, filename, libver="latest"):
    """Write the canonical escape/h5 file into ``directory`` and return its
    path. Same function the server-side write uses - see the module
    docstring for why that matters."""
    from .storage import write_monitor_recording

    return write_monitor_recording(
        Path(directory), recording, filename=filename, libver=libver,
        group_writable=False,
    )
