"""Collecting a status-server handoff and writing it, out of the session's way.

This is the client half of client-write mode (see handoff.py for the server
half and CLIENT_WRITE.md for the whole picture). The server produces the
bytes; *this* process writes them into the run's aux directory and hands the
file to sf_daq_broker - as the scientist's own account, with the scientist's
own pgroup membership and Kerberos ticket, which is the entire point.

Why a separate process by default
---------------------------------
The work is: wait (seconds to tens of seconds) for a snapshot or an h5
encode, then write a file over NFS, then wait on the broker. None of that
needs the session, and all of it would otherwise sit either on the scan's
clock or on a thread competing with it for the GIL while a scan is running.
A detached child does it with no effect on the session at all, and keeps
going if the session is busy, interrupted, or closed.

The cost is that the result has to come back through the filesystem rather
than through a return value. Each job therefore writes:

    ~/.eco/status_relay/<job_id>.spec.json    what to do
    ~/.eco/status_relay/<job_id>.log          stdout/stderr of the child
    ~/.eco/status_relay/<job_id>.result.json  what happened

and :class:`RelayJob` polls the result file. ``mode="thread"`` runs the same
``run_spec`` in-process (same files, same result) for tests and for callers
that want it in the session; ``mode="inline"`` runs it synchronously, which
is what a standalone ``daq.write_status()`` wants.

The result file is written atomically (temp + ``os.replace``), so a poller
never reads half a result, and it is the only state a relay keeps - nothing
here retries, queues or resumes. A relay that dies loses one aux file for
one run, which is exactly what the server-side write already degraded to
when its own write failed; the handoff slot it did not collect expires on
the server after an hour.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

#: Where spec/log/result files go. Per user by construction (it is under
#: $HOME), so the shared-console ``/tmp`` ownership trap does not apply.
DEFAULT_JOB_DIR = "~/.eco/status_relay"

_prune_lock = threading.Lock()
_last_prune = 0.0


def job_dir() -> Path:
    d = Path(os.environ.get("ECO_STATUS_RELAY_DIR", DEFAULT_JOB_DIR)).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def checkout_root() -> str:
    """The eco checkout this module lives in, for the child's PYTHONPATH.

    A session started from a personal checkout must relay with that same
    checkout, not with whatever ``eco`` happens to be installed in the
    environment - otherwise a fix being tested in the session silently does
    not apply to the half of the work that actually writes the file.
    """
    return str(Path(__file__).resolve().parents[2])


def _atomic_write_json(path: Path, obj) -> Path:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


# -- the work -------------------------------------------------------------


def _upload(spec, path, result):
    """Hand the written file to sf_daq_broker's copy_user_files - the same
    call eco.acquisition.daq_client.Daq.append_aux makes, done here so the
    upload happens in the same process that wrote the file rather than
    needing the session to be told it exists."""
    import requests

    pgroup = spec.get("pgroup")
    run_number = spec.get("run_number")
    if pgroup is None or run_number is None:
        result["upload"] = {"status": "skipped",
                            "message": "no pgroup/run_number in the spec"}
        return
    path = Path(path)
    # Same group fixup append_aux does. open_group_writable already set the
    # group in the normal case; this covers a directory whose group is not
    # the pgroup, and must never fail the upload on its own.
    try:
        if path.group() != pgroup:
            shutil.chown(path, group=pgroup)
    except (KeyError, LookupError, PermissionError, OSError) as exc:
        result.setdefault("warnings", []).append(
            f"could not set group of {path} to {pgroup}: "
            f"{type(exc).__name__}: {exc}"
        )
    broker = (spec.get("broker_address_aux") or "").rstrip("/")
    if not broker:
        result["upload"] = {"status": "skipped", "message": "no broker address"}
        return
    r = requests.post(
        broker + "/copy_user_files",
        json={"pgroup": pgroup, "run_number": int(run_number),
              "files": [str(path)]},
        timeout=float(spec.get("upload_timeout", 120)),
    )
    try:
        body = r.json()
    except ValueError:
        body = {"status": "unknown", "message": r.text[:200]}
    result["upload"] = {"http_status": r.status_code, **body}


def run_spec(spec: dict) -> dict:
    """Do one relay job and return (and persist) its result.

    Runs in whatever process calls it - the detached child, a thread, or the
    session itself. Never raises: a relay failure is reported in the result,
    because the caller is usually nobody.
    """
    t_start = time.time()
    directory = Path(spec["directory"])
    filename = spec.get("filename") or "status.json"
    result = {
        "job_id": spec.get("job_id"),
        "kind": spec.get("kind"),
        "handoff_id": spec.get("handoff_id"),
        "base_url": spec.get("base_url"),
        "pgroup": spec.get("pgroup"),
        "run_number": spec.get("run_number"),
        "key": spec.get("key"),
        "path": str(directory / filename),
        "state": "running",
        "started_at": t_start,
        "pid": os.getpid(),
    }
    try:
        from .client import StatusServerClient

        client = StatusServerClient(
            spec["base_url"],
            timeout=float(spec.get("timeout", 10.0)),
            snapshot_timeout=float(spec.get("snapshot_timeout", 180.0)),
        )
        wait = float(spec.get("wait", 600.0))
        handoff_id = spec["handoff_id"]
        kind = spec.get("kind")

        if kind == "recording":
            # Streamed straight into the destination: a namespace recording
            # is the one payload here big enough that holding it in memory
            # on both sides at once would be a choice rather than an
            # accident.
            from eco.utilities.datafiles import ensure_dir, open_group_writable

            ensure_dir(directory, pgroup=spec.get("pgroup"))
            dest = directory / filename
            # Through a .part file: a transfer that dies halfway would
            # otherwise leave a truncated h5 at the run's own filename,
            # which reads as a corrupt recording rather than a missing one.
            # os.replace keeps the mode and group open_group_writable set.
            part = dest.with_name(dest.name + ".part")
            t0 = time.time()
            try:
                with open_group_writable(part, "wb",
                                         pgroup=spec.get("pgroup")) as f:
                    meta = client.fetch_handoff_to(handoff_id, f, wait=wait)
                os.replace(part, dest)
            except BaseException:
                try:
                    part.unlink()
                except OSError:
                    pass
                raise
            result["transfer_seconds"] = time.time() - t0
            written = dest
        else:
            t0 = time.time()
            payload, meta = client.fetch_handoff(handoff_id, wait=wait)
            result["fetch_seconds"] = time.time() - t0
            result["n_bytes_fetched"] = len(payload)
            doc = json.loads(payload.decode("utf-8"))
            t0 = time.time()
            if kind == "status":
                from .storage import write_status_snapshot

                block = doc.get("block") or {}
                key = doc.get("key") or spec.get("key") or "status_run_start"
                written = write_status_snapshot(directory, block, key=key)
                result["key"] = key
                result["n_status"] = len(block.get("status") or {})
                if spec.get("keep_status"):
                    # The session's run-table append waits for exactly this:
                    # the values it would otherwise have to re-read over CA.
                    result["status"] = block.get("status") or {}
            elif kind == "aliases":
                from .storage import write_aliases_file

                written = write_aliases_file(directory, doc, filename=filename)
                result["n_aliases"] = len(doc)
            else:
                raise ValueError(f"unknown relay kind {kind!r}")
            result["write_seconds"] = time.time() - t0

        result["path"] = str(written)
        result["meta"] = meta
        try:
            result["file_bytes"] = Path(written).stat().st_size
        except OSError:
            pass

        if spec.get("upload", True):
            t0 = time.time()
            _upload(spec, written, result)
            result["upload_seconds"] = time.time() - t0
        result["state"] = "done"
    except Exception as exc:  # noqa: BLE001 - the whole point is to report it
        result["state"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    result["finished_at"] = time.time()
    result["duration_seconds"] = result["finished_at"] - t_start
    if spec.get("result_path"):
        try:
            _atomic_write_json(Path(spec["result_path"]), result)
        except OSError:
            traceback.print_exc()
    return result


# -- submitting -----------------------------------------------------------


class RelayJob:
    """Handle on one relay job, whichever way it was started.

    Deliberately shaped like the server-side capture job dicts
    (``job_id``/``path``/``state``), so the daq client can hold either and
    wait on it the same way.
    """

    def __init__(self, spec, process=None, thread=None, result=None):
        self.spec = spec
        self.job_id = spec["job_id"]
        self.kind = spec["kind"]
        self.path = str(Path(spec["directory"]) / spec["filename"])
        self.result_path = Path(spec["result_path"])
        self.log_path = Path(spec["log_path"]) if spec.get("log_path") else None
        self.process = process
        self.thread = thread
        self._result = result

    def __repr__(self):
        return (f"<RelayJob {self.kind} {self.job_id[:8]} "
                f"{self.state} -> {self.path}>")

    def result(self):
        """The finished result dict, or None while it is still running."""
        if self._result is not None:
            return self._result
        try:
            text = self.result_path.read_text()
        except (OSError, FileNotFoundError):
            return None
        try:
            self._result = json.loads(text)
        except ValueError:
            # Should not happen (the writer is atomic), but a half-read
            # result must not look like a crash to the caller.
            return None
        return self._result

    @property
    def state(self):
        res = self.result()
        if res is not None:
            return res.get("state", "unknown")
        if self.process is not None and self.process.poll() is not None:
            # The child is gone without leaving a result: it was killed, or
            # died before it could write one. Say so rather than polling
            # forever.
            return "lost"
        return "running"

    def wait(self, timeout=600, poll=0.2):
        """Block until the job finishes. Returns the result dict.

        Raises TimeoutError if it does not finish in time, and
        RuntimeError if the child vanished without writing a result.
        """
        deadline = time.time() + timeout
        while True:
            res = self.result()
            if res is not None:
                return res
            if self.state == "lost":
                rc = self.process.returncode
                tail = ""
                if self.log_path and self.log_path.exists():
                    tail = self.log_path.read_text()[-2000:]
                raise RuntimeError(
                    f"status relay {self.job_id} exited with code {rc} "
                    f"without a result. Log: {self.log_path}\n{tail}"
                )
            if time.time() > deadline:
                raise TimeoutError(
                    f"status relay {self.job_id} ({self.kind}) unfinished "
                    f"after {timeout} s; see {self.result_path}"
                )
            time.sleep(poll)

    def as_dict(self):
        """The shape the daq client stores in the scan's scratch, so a run's
        bookkeeping records which relay wrote which file."""
        return {"job_id": self.job_id, "path": self.path, "kind": self.kind,
                "relay": True, "result_path": str(self.result_path),
                "log_path": str(self.log_path) if self.log_path else None}


def prune_jobs(max_age_days=7.0, min_interval=600.0, force=False):
    """Drop spec/log/result files older than ``max_age_days``.

    Called from :func:`submit`, at most every ``min_interval`` seconds per
    process - a few thousand small files in a home directory is not a
    problem worth a scan of it at every scan boundary.
    """
    global _last_prune
    with _prune_lock:
        if not force and time.time() - _last_prune < min_interval:
            return 0
        _last_prune = time.time()
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        entries = list(job_dir().iterdir())
    except OSError:
        return 0
    for p in entries:
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def recent_jobs(limit=20):
    """Most recent relay results, newest first - ``daq.status_relays()``
    reads this. Diagnostics only."""
    try:
        results = sorted(
            job_dir().glob("*.result.json"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )[:limit]
    except OSError:
        return []
    out = []
    for p in results:
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return out


def submit(spec: dict, mode="process") -> RelayJob:
    """Start one relay job.

    mode="process" (default) detaches a child, "thread" runs it in this
    process, "inline" runs it here and now and returns a finished job.
    """
    spec = dict(spec)
    spec.setdefault("job_id", uuid.uuid4().hex[:16])
    spec.setdefault("filename", "status.json")
    d = job_dir()
    spec.setdefault("result_path", str(d / f"{spec['job_id']}.result.json"))
    spec.setdefault("log_path", str(d / f"{spec['job_id']}.log"))
    prune_jobs()

    if mode == "inline":
        result = run_spec(spec)
        return RelayJob(spec, result=result)

    if mode == "thread":
        job = RelayJob(spec)
        job.thread = threading.Thread(
            target=run_spec, args=(spec,),
            name=f"status-relay-{spec['kind']}-{spec['job_id'][:8]}",
            daemon=True,
        )
        job.thread.start()
        return job

    if mode != "process":
        raise ValueError(f"unknown relay mode {mode!r}")

    spec_path = d / f"{spec['job_id']}.spec.json"
    _atomic_write_json(spec_path, spec)
    env = dict(os.environ)
    root = checkout_root()
    env["PYTHONPATH"] = (
        root + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else root
    )
    # Nothing in the child draws, plots or talks to Qt, and inheriting the
    # session's heartbeat/signal setup into a child that has no main-thread
    # Qt loop is pure risk (see eco.utilities.mainthread).
    env["ECO_NO_HEARTBEAT"] = "1"
    env["MPLBACKEND"] = "Agg"
    log = open(spec["log_path"], "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "eco.status_server.relay", str(spec_path)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            cwd=str(d), env=env,
            # Its own session: a Ctrl-C in the terminal must not take the
            # write of a finished run's status with it, and the child must
            # outlive the session that started it.
            start_new_session=True,
        )
    finally:
        log.close()
    return RelayJob(spec, process=proc)


# -- building specs -------------------------------------------------------


def status_spec(base_url, directory, pgroup=None, run_number=None,
                handoff_id=None, key="status_run_start", upload=True,
                broker_address_aux=None, keep_status=False, **kwargs):
    return {"kind": "status", "base_url": base_url,
            "directory": str(directory), "filename": "status.json",
            "handoff_id": handoff_id, "pgroup": pgroup,
            "run_number": run_number, "key": key, "upload": upload,
            "broker_address_aux": broker_address_aux,
            "keep_status": keep_status, **kwargs}


def aliases_spec(base_url, directory, pgroup=None, run_number=None,
                 handoff_id=None, upload=True, broker_address_aux=None,
                 filename="aliases.json", **kwargs):
    return {"kind": "aliases", "base_url": base_url,
            "directory": str(directory), "filename": filename,
            "handoff_id": handoff_id, "pgroup": pgroup,
            "run_number": run_number, "upload": upload,
            "broker_address_aux": broker_address_aux, **kwargs}


def recording_spec(base_url, directory, pgroup=None, run_number=None,
                   handoff_id=None, upload=True, broker_address_aux=None,
                   filename="namespace_monitor.ixp.h5", **kwargs):
    return {"kind": "recording", "base_url": base_url,
            "directory": str(directory), "filename": filename,
            "handoff_id": handoff_id, "pgroup": pgroup,
            "run_number": run_number, "upload": upload,
            "broker_address_aux": broker_address_aux, **kwargs}


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Collect one status-server handoff and write it into a "
                    "run's aux directory."
    )
    parser.add_argument("spec", help="path to a relay spec JSON file")
    args = parser.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text())
    result = run_spec(spec)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("state") == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
