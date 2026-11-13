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

import datetime
import json
import time

import colorama
import numpy as np
import requests


def _json_default(obj):
    """Best-effort ``default=`` for ``json.dumps`` on a request body this
    client builds - not the server's own NumpyEncoder (eco.status_server.
    storage), which pulls in h5py and belongs server-side only; this stays
    numpy-only plus the couple of extra types a caller of push_status()/
    capture_recording() etc. might reasonably hand over.

    Exists because ``requests.post(..., json=body)`` serializes with plain
    stdlib ``json.dumps`` and no encoder hook at all - confirmed live: a
    raw ``datetime`` in a push_status() call crashed with
    ``TypeError: Object of type datetime is not JSON serializable`` before
    this existed. The str() fallback degrades one odd field rather than
    failing the whole request over it, same reasoning as NumpyEncoder's.
    """
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.timestamp() if isinstance(obj, datetime.datetime) else obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    return str(obj)


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


def warn_recording_failed_required(result):
    """Shout, in red, about required-component channels a recording could
    not attach to.

    Same "everyone else failing is expected, this failing is not"
    distinction as warn_failed_required, but for a running recording's own
    attach failures - not to be confused with that one: a channel can fail
    to attach here (demoted, disconnected, no underlying PV) even though
    its component initialized perfectly fine, so this deliberately does not
    suggest client.reinit() the way warn_failed_required does - rebuilding
    the namespace would not fix a channel that simply never connects.

    Returns the list it warned about (empty if there was nothing to say).
    """
    failed = list((result or {}).get("failed_required") or [])
    if not failed:
        return []
    red, reset = colorama.Fore.RED + colorama.Style.BRIGHT, colorama.Style.RESET_ALL
    print(
        f"{red}!!! monitoring: {len(failed)} REQUIRED component channel(s) "
        f"could not be attached: {', '.join(failed)}{reset}",
        flush=True,
    )
    print(
        f"{red}    This run's namespace_monitor.ixp.h5 will be missing them - see "
        f"client.recording(recording_id).{reset}",
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
        # data=json.dumps(..., default=_json_default), not json=body: the
        # latter serializes with plain stdlib json.dumps and no encoder
        # hook, which raises TypeError outright on a datetime, a numpy
        # scalar, or anything else `requests` does not already know -
        # confirmed live, see _json_default's docstring.
        r = requests.post(
            f"{self.base_url}{path}",
            data=json.dumps(body or {}, default=_json_default),
            headers={"Content-Type": "application/json"},
            timeout=timeout or self.timeout,
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

    def compare_channels(self, list_names=None) -> dict:
        """Compare the server's initialized namespace against the DAQ's
        recorded-channel lists (channels_JF/channels_BS/channels_BSCAM):
        ``{list_name: {"channeltype", "missing", "exceeding", "n_required",
        "n_recorded"}}``. See eco.aliases.channel_lists and
        eco.acquisition.daq_client.Daq.compare_channels, which calls this
        when a status server is configured and healthy.
        """
        path = "/channels/compare"
        if list_names:
            path += "?" + "&".join(f"list={n}" for n in list_names)
        return self._get(path)["channels"]

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

    def monitor_policy(self) -> dict:
        """What eco.epics_utils.ca_tuning's adaptive monitor policy
        currently believes in the server's own process - max_rate_hz (the
        threshold a channel gets demoted above), how many channels are
        monitored/known-fast, whether a sensitive period is active. Read-
        only - see eco.status_server.namespace_server's /monitor_policy."""
        return self._get("/monitor_policy")

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
                keep_status=False, client_status=None) -> dict:
        """Have the server snapshot, write status.json and upload it to the
        run - in the background, returning as soon as the job is accepted.

        This is what a scan callback wants. `snapshot(save=True)` does the
        same work but the caller waits for all of it and gets the whole
        status dict back (~3 MB, ~20 s on bernina): 40 s of dead time per
        run for a result it mostly does not read.

        wait=True blocks until the job finishes, for a standalone call where
        you do want to know it landed.

        client_status : dict, optional
            ``{"status": {...}, "status_channels": {...}, "status_times":
            {...}}`` (same shape the server's own snapshot uses) - merged
            into the server's snapshot before it writes status.json, for
            values the server structurally cannot see on its own (e.g. a
            scan's own daq_run_number/initial_values - see
            eco.acquisition.daq_client.Daq._client_status_for_scan). Merged
            in, never overwriting the server's own entries wholesale - see
            the /status/capture handler.
        """
        body = {"pgroup": pgroup, "run_number": int(run_number),
                "key": key, "upload": upload, "keep_status": keep_status}
        if client_status:
            body["client_status"] = client_status
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
                        subscription_mask=None,
                        pgroup=None, run_number=None, timeout=None) -> dict:
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

        pgroup/run_number are optional and purely for later cross-
        referencing: pass them (as start_scan_monitoring does) so a status
        capture for the same run can opportunistically backfill a channel
        that has not updated on its own yet - see
        NamespaceMonitorStore.backfill_running_recordings.

        This call still blocks until the server has attached (or failed
        to attach) every requested channel - RecordingSession.start()
        fans that attach loop out over a thread pool server-side now, but
        it is not async from this client's point of view. It therefore
        defaults its own timeout to `snapshot_timeout` (180 s), not the
        much shorter general-purpose `timeout` (10 s) - the same
        distinction `stop_recording`/`capture_recording` already make, and
        the actual root cause of "start monitoring failed ... falling
        back" against bernina's ~13 000+ channels before this: the call
        was reliably slower than 10 s even before it started timing out,
        just not given the timeout that would have covered it.
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
            "pgroup": pgroup,
            "run_number": run_number,
        }
        return self._post(
            "/recording/start", body,
            timeout=timeout or self.snapshot_timeout, ok_codes=(200, 202),
        )

    def recording(self, recording_id, channels=False, size=False,
                  size_top_n=None) -> dict:
        """size=True adds "size_per_channel": a projected-bandwidth
        estimate per channel (rate * value size - "frequency * data shape
        * bitdepth"), sorted biggest first, from this recording's own
        buffers so far - works on a still-running recording, meant for
        judging storage/bandwidth cost from a short trial before
        committing to a long one. size_top_n caps how many channels come
        back (the point is usually "what are the biggest contributors")."""
        params = []
        if channels:
            params.append("channels=1")
        if size:
            params.append("size=1")
        if size_top_n:
            params.append(f"size_top_n={int(size_top_n)}")
        suffix = "?" + "&".join(params) if params else ""
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

    def capture_recording(self, recording_id, pgroup, run_number, upload=True,
                          filename="monitors.esc.h5", drop=True, wait=False,
                          timeout=600) -> dict:
        """Stop a recording, write it and upload it to the run - in the
        background, returning as soon as the job is accepted. The
        recording-stop equivalent of :meth:`capture`/:meth:`capture_aliases`;
        use this instead of :meth:`stop_recording` at a scan boundary, where
        the write+upload time (real, scales with how much was recorded)
        should not be on the scan's clock.

        wait=True blocks until the job finishes, for a standalone call.
        """
        body = {"recording_id": recording_id, "pgroup": pgroup,
                "run_number": int(run_number), "upload": upload,
                "filename": filename, "drop": drop}
        started = self._post("/recording/capture", body, ok_codes=(200, 202))
        if not wait:
            return started
        return self.wait_write_job(started["job_id"], timeout=timeout)

    # -- handoff (client-write mode) ---------------------------------------
    #
    # The mirror image of capture()/capture_aliases()/capture_recording():
    # the server produces, this side collects and writes. See
    # eco/status_server/CLIENT_WRITE.md for when that is worth doing, and
    # eco.status_server.relay for the piece that does the writing out of
    # the session's way.

    def supports_handoff(self) -> bool:
        """Whether this server has the /handoff routes at all.

        A straight feature probe, because the two modes are meant to
        coexist during the trial: an older server simply 404s, and a client
        configured for client-write mode then has to know to fall back
        rather than fail the run.
        """
        try:
            r = requests.get(f"{self.base_url}/handoff", timeout=self.timeout)
        except requests.RequestException:
            return False
        return r.status_code == 200

    def handoffs(self) -> dict:
        """Slots currently waiting to be collected, plus the store's own
        counters - the place to look when a relay died between asking and
        fetching."""
        return self._get("/handoff")

    def handoff(self, handoff_id: str) -> dict:
        return self._get(f"/handoff/{handoff_id}")

    def release_handoff(self, handoff_id: str) -> dict:
        r = requests.delete(f"{self.base_url}/handoff/{handoff_id}",
                            timeout=self.timeout)
        if r.status_code == 404:
            return {"status": "error", "message": "no such handoff"}
        r.raise_for_status()
        return r.json()

    def handoff_status(self, pgroup=None, run_number=None,
                       key="status_run_start", client_status=None,
                       allow_stale=False, max_workers=None, ttl=None) -> dict:
        """Ask for a status snapshot to be parked for pickup. Returns at
        once with ``handoff_id``/``job_id``; the snapshot itself runs on the
        server.

        pgroup/run_number are not needed to produce the block (this side
        decides where it goes) but are passed anyway so a recording running
        for the same run gets the same backfill /status/capture does.
        """
        body = {"key": key, "allow_stale": allow_stale}
        if pgroup is not None:
            body["pgroup"] = pgroup
        if run_number is not None:
            body["run_number"] = int(run_number)
        if client_status:
            body["client_status"] = client_status
        if max_workers:
            body["max_workers"] = int(max_workers)
        if ttl:
            body["ttl"] = float(ttl)
        return self._post("/handoff/status", body, ok_codes=(200, 202))

    def handoff_aliases(self, pgroup=None, run_number=None,
                        channeltypes=None, ttl=None) -> dict:
        body = {}
        if pgroup is not None:
            body["pgroup"] = pgroup
        if run_number is not None:
            body["run_number"] = int(run_number)
        if channeltypes:
            body["channeltypes"] = list(channeltypes)
        if ttl:
            body["ttl"] = float(ttl)
        return self._post("/handoff/aliases", body, ok_codes=(200, 202))

    def handoff_recording(self, recording_id, pgroup=None, run_number=None,
                          format="h5", filename="namespace_monitor.ixp.h5",
                          libver="latest", drop=True, ttl=None,
                          timeout=None) -> dict:
        """Stop a recording and have its data parked for pickup.

        The stop happens before this returns (same as capture_recording), so
        it gets the longer snapshot_timeout: detaching callbacks from
        thousands of channels is not instant.
        """
        body = {"recording_id": recording_id, "format": format,
                "filename": filename, "libver": libver, "drop": drop}
        if pgroup is not None:
            body["pgroup"] = pgroup
        if run_number is not None:
            body["run_number"] = int(run_number)
        if ttl:
            body["ttl"] = float(ttl)
        return self._post(
            "/handoff/recording", body,
            timeout=timeout or self.snapshot_timeout, ok_codes=(200, 202),
        )

    def _handoff_response(self, handoff_id, wait, release):
        params = []
        if wait:
            params.append(f"wait={float(wait)}")
        if release:
            params.append("release=1")
        url = f"{self.base_url}/handoff/{handoff_id}/data"
        if params:
            url += "?" + "&".join(params)
        # read timeout must outlast the long poll, or the client gives up on
        # exactly the wait it asked the server for.
        r = requests.get(url, stream=True,
                         timeout=(self.timeout, (wait or 0) + self.timeout))
        if r.status_code == 404:
            raise StatusServerError(f"no such handoff '{handoff_id}'")
        if r.status_code == 202:
            raise TimeoutError(
                f"handoff '{handoff_id}' still pending after {wait} s"
            )
        if r.status_code != 200:
            raise StatusServerError(
                f"handoff '{handoff_id}' failed: {r.text[:400]}"
            )
        return r

    @staticmethod
    def _handoff_meta(response) -> dict:
        raw = response.headers.get("X-Eco-Handoff-Meta")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {"raw": raw}

    def fetch_handoff(self, handoff_id, wait=600, release=True) -> tuple:
        """Collect a slot's payload into memory: ``(bytes, meta)``.

        ``wait`` long-polls on the server rather than busy-polling from
        here, so a client can fire the request and immediately ask for the
        answer.
        """
        r = self._handoff_response(handoff_id, wait, release)
        return r.content, self._handoff_meta(r)

    def fetch_handoff_to(self, handoff_id, fileobj, wait=600, release=True,
                         chunk_size=1 << 20) -> dict:
        """Stream a slot's payload into an already-open binary file object
        and return its meta.

        Takes a file object rather than a path because the whole point of
        this mode is *who* creates the file: the caller opens it through
        eco.utilities.datafiles.open_group_writable, under its own account,
        with the group bits the shared tree needs.
        """
        r = self._handoff_response(handoff_id, wait, release)
        n = 0
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                fileobj.write(chunk)
                n += len(chunk)
        meta = self._handoff_meta(r)
        meta["n_bytes"] = n
        return meta

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
