"""Thin client for the namespace status/monitor server.

This is what ``eco.acquisition.daq_client.Daq`` talks to when it is
constructed with ``status_server="http://<host>:<port>"`` - see
``Daq.init_namespace`` / ``Daq.append_start_status_to_scan``. It is also
usable standalone from any eco session to inspect or drive a server:

    from eco.status_server.client import StatusServerClient
    c = StatusServerClient("http://saresb-cons-04:8091")
    c.health()
    c.wait_ready(timeout=900, progress=True)
    c.get_status()                      # same dict as namespace.get_status()
    c.reinit(mode="failed", wait=True)  # retry components that failed
"""

from __future__ import annotations

import time

import colorama
import requests


def warn_failed_required(health):
    """Shout, in red, about required components the server could not build.

    A component in ``required_names()`` is one the setup is not supposed to
    fail. If one did, the status this server serves is missing something
    that matters - silently, since every remaining channel still answers
    fine - so a client taking status from it needs to be told at the moment
    it starts relying on the server, not left to discover the gap in the
    file afterwards. Non-required components failing is expected and stays
    quiet.

    Returns the list it warned about (empty if there was nothing to say),
    so a caller can decide to do more than print.
    """
    failed = list((health or {}).get("failed_required") or [])
    if not failed:
        return []
    red, reset = colorama.Fore.RED + colorama.Style.BRIGHT, colorama.Style.RESET_ALL
    print(
        f"{red}!!! status server: {len(failed)} REQUIRED component(s) failed to "
        f"initialize: {', '.join(failed)}{reset}",
        flush=True,
    )
    print(
        f"{red}    Status recorded from this server is missing them. Inspect "
        f"with client.failures(), or rebuild with client.reinit().{reset}",
        flush=True,
    )
    return failed


class StatusServerError(RuntimeError):
    pass


class StatusServerNotReady(StatusServerError):
    def __init__(self, health):
        self.health = health or {}
        super().__init__(
            f"status server is '{self.health.get('state', 'unknown')}': "
            f"{self.health.get('busy_reason')}"
        )


class StatusServerClient:
    def __init__(self, base_url: str, timeout: float = 10.0,
                 snapshot_timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        # Two timeouts on purpose: /health and the admin routes answer in
        # milliseconds and a short timeout is what makes "the server is
        # down" fail fast, while a snapshot is a full get_status() fan-out
        # over ~14k channels and legitimately takes 10-20 s on bernina. One
        # shared 10 s timeout made every real snapshot fail.
        self.timeout = timeout
        self.snapshot_timeout = snapshot_timeout

    # -- low level ---------------------------------------------------------

    def _get(self, path, timeout=None):
        r = requests.get(f"{self.base_url}{path}", timeout=timeout or self.timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path, body=None, timeout=None, ok_codes=(200, 202)):
        r = requests.post(
            f"{self.base_url}{path}", json=body or {}, timeout=timeout or self.timeout
        )
        if r.status_code == 503:
            raise StatusServerNotReady(r.json())
        if r.status_code not in ok_codes:
            r.raise_for_status()
        return r.json()

    # -- state -------------------------------------------------------------

    def health(self) -> dict:
        return self._get("/health")

    def is_ready(self) -> bool:
        try:
            return bool(self.health().get("ready"))
        except requests.RequestException:
            return False

    def names(self) -> dict:
        return self._get("/names")

    def aliases(self, channeltypes=None) -> list:
        """The namespace's current alias list: ``[{alias, channel,
        channeltype}, ...]`` - the same shape (and, since both come from the
        same ``Alias.get_all()``, the same content) as
        ``self.namespace.alias.get_all()`` would give locally, without
        having to initialize that namespace to get it.
        """
        path = "/aliases"
        if channeltypes:
            path += "?" + "&".join(f"channeltype={c}" for c in channeltypes)
        return self._get(path)["aliases"]

    def stats(self, limit: int = None, kind: str = None) -> dict:
        """Recent /status/snapshot and /status/capture operations this
        server has served: `{"summary": {...}, "recent": [...]}`. See
        eco.status_server.query_stats - it answers "is the server serving
        requests well", independent of `/health`'s "is the namespace
        healthy"."""
        path = "/stats"
        params = []
        if limit:
            params.append(f"limit={int(limit)}")
        if kind:
            params.append(f"kind={kind}")
        if params:
            path += "?" + "&".join(params)
        return self._get(path)

    def failures(self) -> dict:
        return self._get("/failures")["failures"]

    def wait_ready(self, timeout=1800, poll=2.0, progress=False,
                   min_generation=None, instance_id_not=None):
        """Block until the server reports ready.

        Returns the final /health body. Raises TimeoutError if it does not
        get there in `timeout` seconds.

        min_generation: also require ``generation >= min_generation`` - use
        after asking for a reinit, so a server that has not *started* the
        rebuild yet (still reporting the previous ready state) is not
        mistaken for one that has finished it.
        instance_id_not: also require a different ``instance_id`` - used
        after /admin/restart, where the new process starts at generation 0.
        """
        deadline = time.time() + timeout
        last_line = None
        while True:
            try:
                h = self.health()
            except requests.RequestException as exc:
                h = None
                if progress:
                    line = f"waiting for {self.base_url} ({exc.__class__.__name__})"
                    if line != last_line:
                        print(line, flush=True)
                        last_line = line
            if h is not None:
                ok = h.get("ready")
                if ok and min_generation is not None:
                    ok = (h.get("generation") or 0) >= min_generation
                if ok and instance_id_not is not None:
                    ok = h.get("instance_id") != instance_id_not
                if ok:
                    if progress:
                        print(
                            f"status server ready: {h.get('n_initialized')}/"
                            f"{h.get('n_target_names')} components, "
                            f"{h.get('n_monitored')} monitored, "
                            f"{h.get('n_failed')} failed",
                            flush=True,
                        )
                    warn_failed_required(h)
                    return h
                if progress:
                    line = (
                        f"{h.get('state')}: {h.get('n_initialized')}/"
                        f"{h.get('n_target_names')} initialized"
                        f" ({h.get('n_failed')} failed)"
                    )
                    if line != last_line:
                        print(line, flush=True)
                        last_line = line
            if time.time() > deadline:
                raise TimeoutError(
                    f"status server at {self.base_url} not ready after "
                    f"{timeout} s (last health: {h})"
                )
            time.sleep(poll)

    # -- status ------------------------------------------------------------

    def get_status(self, allow_stale=False) -> dict:
        """Return the same dict shape as ``namespace.get_status(base=None)``."""
        return self.snapshot(allow_stale=allow_stale)

    def snapshot(
        self,
        pgroup: str = None,
        run_number: int = None,
        save: bool = False,
        key: str = "status_run_start",
        allow_stale: bool = False,
        write_async: bool = False,
        max_workers: int = None,
        timeout: float = None,
    ) -> dict:
        body = {"save": save, "key": key, "allow_stale": allow_stale,
                "write_async": write_async}
        if max_workers:
            body["max_workers"] = int(max_workers)
        if save:
            body["pgroup"] = pgroup
            body["run_number"] = run_number
        return self._post(
            "/status/snapshot",
            body,
            timeout=timeout or self.snapshot_timeout,
            ok_codes=(200,),
        )

    def capture(self, pgroup, run_number, key="status_run_start",
                upload=True, wait=False, timeout=600,
                keep_status=False) -> dict:
        """Have the server snapshot, write status.json and upload it to the
        run - in the background, returning as soon as the job is accepted.

        This is what a scan callback wants. `snapshot(save=True)` does the
        same work but the caller waits for all of it and gets the whole
        status dict back (~3 MB, ~20 s on bernina): 40 s of dead time per
        run for a result it mostly does not read.

        wait=True blocks until the job finishes, for a standalone call where
        you do want to know it landed.
        """
        body = {"pgroup": pgroup, "run_number": int(run_number),
                "key": key, "upload": upload, "keep_status": keep_status}
        started = self._post("/status/capture", body, ok_codes=(200, 202))
        if not wait:
            return started
        return self.wait_write_job(started["job_id"], timeout=timeout)

    def capture_aliases(self, pgroup, run_number, upload=True, wait=False,
                        timeout=600, channeltypes=None) -> dict:
        """Have the server compute the alias list from its own namespace,
        write aliases.json and upload it to the run - in the background,
        returning as soon as the job is accepted. See :meth:`capture` for
        the status.json equivalent; use :meth:`wait_write_job` (job ids are
        shared across both) to wait on the returned ``job_id`` later.

        wait=True blocks until the job finishes, for a standalone call.
        """
        body = {"pgroup": pgroup, "run_number": int(run_number), "upload": upload}
        if channeltypes:
            body["channeltypes"] = list(channeltypes)
        started = self._post("/aliases/capture", body, ok_codes=(200, 202))
        if not wait:
            return started
        return self.wait_write_job(started["job_id"], timeout=timeout)

    def push_status(self, pgroup, run_number, values: dict,
                    key: str = "status_run_start") -> dict:
        """Hand the server a small dict of values it could never poll
        itself -- anything with no CA channel at all (e.g.
        scans.acquiring_scan.*, built from DetectorMemory, whose alias has
        channel=None -- see eco.aliases.aliases.Alias.get_all(), which only
        ever returns an alias that has one). This session already resolved
        the real object and has the value in hand.

        Merged into the matching /status/capture job's status the next
        time it is read with include_status=True (see
        wait_write_job(include_status=True)) -- consumed once, and dropped
        if nothing reads it in time (see the server-side
        NamespaceMonitorStore.pop_pushed_status).
        """
        body = {"pgroup": pgroup, "run_number": int(run_number),
                "key": key, "values": dict(values)}
        return self._post("/status/push", body, ok_codes=(200,))

    def write_job(self, job_id: str, include_status=False) -> dict:
        suffix = "?include_status=1" if include_status else ""
        return self._get(f"/status/job/{job_id}{suffix}")["job"]

    def wait_write_job(self, job_id: str, timeout=60, poll=0.2,
                       include_status=False) -> dict:
        """Block until a capture/write job finishes.

        include_status collects the status values the job produced along
        with it - the server hands them over once and then drops them, so
        ask for them only on the poll that finds the job done.
        """
        deadline = time.time() + timeout
        while True:
            job = self.write_job(job_id)
            if job["state"] != "running":
                if include_status:
                    job = self.write_job(job_id, include_status=True)
                if job["state"] == "error":
                    raise StatusServerError(
                        f"server-side status write failed: {job.get('error')}"
                    )
                return job
            if time.time() > deadline:
                raise TimeoutError(f"status write job {job_id} unfinished after {timeout} s")
            time.sleep(poll)

    # -- recording ---------------------------------------------------------

    def monitorable_count(self) -> int:
        return self._get("/recording")["n_monitorable"]

    def start_recording(self, recording_id=None, names=None, mode="all",
                        min_interval=0.0, sample_interval=0.1,
                        max_points_per_channel=100_000,
                        max_value_elements=None,
                        subscription_mask=None) -> dict:
        """Start monitoring every monitorable status channel on the server.

        mode:
          "all"      - store every CA update (truest, unbounded).
          "throttle" - store at most one point per `min_interval` per
                       channel. Cuts stored data, not the update rate.
          "sample"   - keep only the latest value per channel and copy all
                       of them onto a fixed `sample_interval` grid. Bounded
                       memory, constant per-update cost, common time base.

        max_value_elements caps how big a single value may be to be stored,
        which is how you keep waveform channels out: measured on bernina,
        two 8000-sample digitizer waveforms were 204 MB of a 239 MB
        three-minute recording, against 8 MB for all 7 677 scalar channels
        put together.

        subscription_mask="log" additionally subscribes to the IOC's archive
        deadband stream (DBE_LOG) instead of DBE_VALUE - the only option
        here that reduces how often the IOC actually sends.
        """
        body = {
            "recording_id": recording_id,
            "names": list(names) if names is not None else None,
            "mode": mode,
            "min_interval": min_interval,
            "sample_interval": sample_interval,
            "max_points_per_channel": max_points_per_channel,
            "max_value_elements": max_value_elements,
            "subscription_mask": subscription_mask,
        }
        return self._post("/recording/start", body, ok_codes=(200, 202))

    def recording(self, recording_id, channels=False) -> dict:
        suffix = "?channels=1" if channels else ""
        return self._get(f"/recording/{recording_id}{suffix}")

    def recordings(self) -> list:
        return self._get("/recording")["recordings"]

    def stop_recording(self, recording_id, pgroup=None, run_number=None,
                       save=True, filename="monitors.esc.h5",
                       include_data=False, drop=True, timeout=None) -> dict:
        """Stop a recording and (by default) have the server write it as one
        escape ArrayTimestamps per channel into the run's aux directory."""
        body = {
            "recording_id": recording_id,
            "save": save,
            "filename": filename,
            "include_data": include_data,
            "drop": drop,
        }
        if save:
            body["pgroup"] = pgroup
            body["run_number"] = run_number
        return self._post(
            "/recording/stop", body,
            timeout=timeout or self.snapshot_timeout, ok_codes=(200,),
        )

    # -- admin -------------------------------------------------------------

    def reinit(self, mode="restart", names=None, reload_modules=False,
               new_names=None, wait=True, timeout=1800, progress=True,
               delay=0.5):
        """Rebuild the server's namespace.

        mode="restart" (the default) re-execs the whole server process: the
        namespace, every eco module and every CA connection are thrown away
        and built again from current source. It is the only mode that is
        unconditionally correct - the in-process modes below cannot unload
        code that live objects still reference - and it costs a full
        init_all() (minutes).

        The cheaper in-process modes, when a full restart is not warranted:
        "failed" (retry only components that failed - the "the IOC is back
        now" case), "names" (rebuild the given names), "full" (rebuild the
        whole target set), "init" (only initialize what is still lazy),
        "reimport" (drop every eco.* module and re-import - see
        NamespaceMonitorStore.start_reinit for what that can and cannot
        reclaim).

        Waits for the rebuild to finish by default; pass wait=False to fire
        and poll yourself.
        """
        if mode == "restart":
            return self.restart(wait=wait, timeout=timeout, progress=progress,
                                delay=delay)
        before = self.health()
        body = {"mode": mode, "reload_modules": reload_modules}
        if names is not None:
            body["names"] = list(names)
        if new_names is not None:
            body["new_names"] = list(new_names)
        r = requests.post(
            f"{self.base_url}/admin/reinit", json=body, timeout=self.timeout
        )
        if r.status_code == 409:
            raise StatusServerNotReady(r.json())
        r.raise_for_status()
        started = r.json()
        if not wait:
            return started
        return self.wait_ready(
            timeout=timeout,
            progress=progress,
            min_generation=(before.get("generation") or 0) + 1,
        )

    def restart(self, wait=False, timeout=1800, progress=False, delay=0.5):
        """Re-exec the server process - the only way to pick up edits to
        eco's core or to the namespace's own assembly module."""
        before = self.health()
        r = requests.post(
            f"{self.base_url}/admin/restart", json={"delay": delay},
            timeout=self.timeout,
        )
        r.raise_for_status()
        if not wait:
            return r.json()
        # Give the old process time to actually go away first, otherwise the
        # very first poll can still be answered by it.
        time.sleep(max(delay, 0.5) + 0.5)
        return self.wait_ready(
            timeout=timeout, progress=progress,
            instance_id_not=before.get("instance_id"),
        )
