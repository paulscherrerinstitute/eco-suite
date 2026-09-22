"""A namespace-level handle for the long-running eco.status_server process
a Daq talks to - control (restart/reinit), inspection (health/stats/gui),
and the settings that shape what a server-backed scan actually does.

Not the same thing as ``Daq(status_server=<url>)`` (a plain URL string
Daq turns into its own ``StatusServerClient`` internally, see
``Daq.status_client``): this is a separate, top-level namespace object
(``bernina.status_server``, alongside ``daq``/``scans``) meant to be
poked at directly from a session - ``bernina.status_server.gui()``,
``.status()``, ``.restart()`` - and to hold the settings a human actually
wants to see and change (recording mode, throttle interval, max element
size, ...) as real ``AdjustableFS`` children, the way any other device's
filesystem-backed configuration shows up in ``.settings()``, instead of
as hardcoded literals buried in ``bernina_daq.py`` or ``Daq``'s own
kwargs.

AdjustableFS, not AdjustableMemory, deliberately: a setting changed in one
session must be visible to *any other process* that can read the same
shared filesystem - another session, or the status server process itself
- with no REST call or other new communication of its own, the same way
``channels_JF``/``config_JFs`` already work in ``bernina_daq.py``. Each
setting is its own small JSON file under ``config_dir`` (default: the
shared ``eco_cnf_bernina/configuration`` tree), read with AdjustableFS's
own short TTL cache (``ADJUSTABLEFS_MAX_READ_PERIOD``, 0.2 s) rather than
once at import time.

Currently a read/inspect surface for the settings - nothing (not Daq, not
the server) consults these yet (``start_scan_monitoring`` still uses its
own mode="throttle"/min_interval=0.1 defaults). Being filesystem-based is
what would let the server read them directly, without inventing a new
settings-push endpoint - actually doing that is a natural next step, not
yet done.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import colorama

from eco.elements.adjustable import AdjustableFS
from eco.elements.assembly import Assembly
from eco.elements.detector import DetectorGet

from .client import StatusServerClient, warn_failed_required

# The same shared tree bernina_daq.py's channels_JF/config_JFs/etc already
# use - see eco.utilities.datafiles for why this needs to be a group-
# writable tree (AdjustableFS handles that itself, same as those).
_DEFAULT_CONFIG_DIR = "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration"


class _AdjustableFSChoice(AdjustableFS):
    """An AdjustableFS restricted to a fixed set of string choices.

    Deliberately not `eco.elements.adjustable.AdjustableEnum`: that wraps a
    base Adjustable and stores the *IntEnum's integer*, not the string
    itself -- fine for a PV-backed mbbi record, but it would change what
    lands in this setting's JSON file from the plain string
    RecordingSession.__init__ already parses (`mode not in ("all",
    "throttle", "sample")`) to an int, which only this class's own
    unwrapping would understand. `enum_strs` is enough on its own: per
    `eco.elements.protocols.AdjustableEnum`, any Adjustable exposing a
    truthy `enum_strs` structurally qualifies (no subclassing needed) --
    including getting picked up as a dropdown by widget layers'
    `_enum_options()` -- so this only adds that attribute plus write-time
    validation, storage stays a plain string, wire-compatible with the
    server as it already is.
    """

    def __init__(self, *args, enum_strs, **kwargs):
        self.enum_strs = tuple(enum_strs)
        super().__init__(*args, **kwargs)

    def set_target_value(self, value, hold=False):
        if value not in self.enum_strs:
            raise ValueError(
                f"{self.name}: {value!r} is not one of {self.enum_strs}"
            )
        return super().set_target_value(value, hold=hold)


class StatusServer(Assembly):
    def __init__(self, base_url: str, name=None, config_dir=None):
        super().__init__(name=name)
        self.base_url = base_url
        self._client = StatusServerClient(base_url)
        self._gui_proc = None
        self._health_cache = None
        self._stats_cache = None

        cfg = Path(config_dir) if config_dir is not None else Path(_DEFAULT_CONFIG_DIR)
        # RecordingSession.__init__'s own accepted modes -- see its
        # docstring (module namespace_store.py) for what each does:
        # "all" keeps every update (unbounded memory risk, hence
        # max_points_per_channel); "throttle" drops updates faster than
        # recording_min_interval per channel but still runs the callback
        # every time; "sample" only latches a "latest" slot per update and
        # copies it onto a fixed grid (recording_sample_interval) from a
        # separate thread, the cheapest per-update cost of the three.
        self._append(
            _AdjustableFSChoice, str(cfg / "status_server_recording_mode.json"),
            default_value="throttle", name="recording_mode", is_setting=True,
            enum_strs=("all", "throttle", "sample"),
        )
        self._append(
            AdjustableFS, str(cfg / "status_server_recording_min_interval.json"),
            default_value=0.1, name="recording_min_interval", is_setting=True,
        )
        self._append(
            AdjustableFS, str(cfg / "status_server_recording_sample_interval.json"),
            default_value=0.1, name="recording_sample_interval", is_setting=True,
        )
        # "maximum_element_size": drops an update *outright* if that one
        # value's own element count (e.g. an 8000-sample waveform) exceeds
        # this - a per-VALUE-WIDTH filter, aimed at exactly the waveform
        # channels that otherwise dominate a recording's file size by an
        # order of magnitude (see RecordingSession's own docstring: two
        # digitizer waveforms were 204 MB of a 239 MB file). None keeps
        # everything, including waveforms.
        self._append(
            AdjustableFS, str(cfg / "status_server_recording_max_value_elements.json"),
            default_value=None, name="recording_max_value_elements", is_setting=True,
        )
        # A per-CHANNEL-LENGTH cap instead: how many stored points (across
        # the whole recording's duration) one channel may accumulate before
        # further updates for it are dropped - protects against a single
        # fast/never-idle channel growing unbounded over a long recording
        # (the OOM incident RecordingSession's docstring mentions). Distinct
        # axis from recording_max_value_elements above: that one filters by
        # the size of each individual value, this one by how many values
        # pile up over time.
        self._append(
            AdjustableFS,
            str(cfg / "status_server_recording_max_points_per_channel.json"),
            default_value=100_000, name="recording_max_points_per_channel",
            is_setting=True,
        )

        # -- diagnostics ------------------------------------------------
        # Plain Detector children (get_current_value() only, matching the
        # generic DetectorGet wrapper used the same way in
        # eco.elements.adj_obj.py) so these show up in .status() and any
        # generic widget/property-grid layer like any other device's
        # readings, instead of being reachable only via .health()/.stats()
        # dicts or __repr__'s printed summary. Values come from
        # _cached_health()/_cached_stats() below, not straight from
        # self._client, so reading all five together (e.g. one .status()
        # sweep) costs at most 2 HTTP calls, not 5.
        self._append(
            DetectorGet(lambda: (self._cached_health() or {}).get("n_initialized"),
                        name="n_initialized"),
            call_obj=False, name="n_initialized", is_setting=False,
        )
        self._append(
            DetectorGet(lambda: (self._cached_health() or {}).get("n_failed"),
                        name="n_failed"),
            call_obj=False, name="n_failed", is_setting=False,
        )
        self._append(
            DetectorGet(lambda: (self._cached_health() or {}).get("failed_names"),
                        name="failed_names"),
            call_obj=False, name="failed_names", is_setting=False,
        )
        self._append(
            DetectorGet(self._get_last_namespace_update, name="last_namespace_update"),
            call_obj=False, name="last_namespace_update", is_setting=False,
        )
        self._append(
            DetectorGet(self._get_last_request, name="last_request"),
            call_obj=False, name="last_request", is_setting=False,
        )

    # -- diagnostics ------------------------------------------------------

    def _cached_health(self, ttl=1.0) -> dict | None:
        """health(), memoized for `ttl` seconds - one namespace-state read
        shared by n_initialized/n_failed/failed_names/last_namespace_update
        rather than one HTTP round trip per Detector. Swallows a failed
        request (server down/unreachable) to None rather than raising, since
        this backs plain status-reading Detectors, not an action a caller is
        waiting on."""
        now = time.time()
        if self._health_cache is None or now - self._health_cache[0] > ttl:
            try:
                self._health_cache = (now, self._client.health())
            except Exception:
                self._health_cache = (now, None)
        return self._health_cache[1]

    def _cached_stats(self, ttl=1.0) -> dict | None:
        """stats(), memoized for `ttl` seconds - see _cached_health."""
        now = time.time()
        if self._stats_cache is None or now - self._stats_cache[0] > ttl:
            try:
                self._stats_cache = (now, self._client.stats())
            except Exception:
                self._stats_cache = (now, None)
        return self._stats_cache[1]

    def _get_last_namespace_update(self):
        """ISO datetime the server's namespace last finished (re)building
        (health()'s last_init_finished, an epoch-seconds float) - None if it
        never has, or if the server could not be reached."""
        health = self._cached_health()
        finished = health.get("last_init_finished") if health else None
        return datetime.fromtimestamp(finished).isoformat() if finished else None

    def _get_last_request(self):
        """The most recent request this server answered and what kind it
        was: {"kind", "at" (ISO datetime), "duration_s"} - None if it has
        served nothing yet, or if it could not be reached."""
        stats = self._cached_stats()
        summary = stats.get("summary") if stats else None
        if not summary or not summary.get("n"):
            return None
        last_at = summary.get("last_at")
        return {
            "kind": summary.get("last_kind"),
            "at": datetime.fromtimestamp(last_at).isoformat() if last_at else None,
            "duration_s": summary.get("last_duration_s"),
        }

    # -- monitoring ---------------------------------------------------------

    def start_monitoring(self, pgroup, run_number, names=None, mode="throttle",
                         min_interval=0.1, **kwargs) -> tuple[str, dict]:
        """Start a namespace-wide recording for one run, named by the
        pgroup/run-number convention every scan-boundary caller uses
        (`"{pgroup}_run{run_number:04d}"`) - the one place that convention
        lives, rather than duplicated at each call site. Returns
        (recording_id, server response); see StatusServerClient.start_recording
        for what mode/min_interval/**kwargs mean."""
        recording_id = f"{pgroup}_run{int(run_number):04d}"
        result = self._client.start_recording(
            recording_id=recording_id, names=names, mode=mode,
            min_interval=min_interval, pgroup=pgroup, run_number=run_number,
            **kwargs,
        )
        return recording_id, result

    def stop_monitoring(self, recording_id, pgroup, run_number, upload=True,
                        filename="namespace_monitor.ixp.h5", **kwargs) -> dict:
        """Stop+write+upload a recording started with start_monitoring -
        thin wrapper on capture_recording, same filename convention every
        caller already uses. The server writes the file into the run's
        pgroup-specific aux directory (eco.status_server.config.data_dir)
        and uploads it there in the background; see
        StatusServerClient.capture_recording."""
        return self._client.capture_recording(
            recording_id, pgroup, run_number, upload=upload,
            filename=filename, **kwargs,
        )

    # -- control --------------------------------------------------------

    def restart(self, wait=True, timeout=1800, progress=True):
        """Re-exec the server process - the only way to pick up new code.
        Costs a full init_all() (minutes); see StatusServerClient.restart's
        own docstring."""
        return self._client.restart(wait=wait, timeout=timeout, progress=progress)

    def reinit(self, mode="failed", wait=True, **kwargs):
        """Rebuild the server's namespace in-process - cheaper than
        restart() for mode="failed" (retry only what failed), but cannot
        pick up code changes. See StatusServerClient.reinit."""
        return self._client.reinit(mode=mode, wait=wait, **kwargs)

    # -- inspection -------------------------------------------------------

    def health(self) -> dict:
        return self._client.health()

    def failures(self) -> dict:
        return self._client.failures()

    def stats(self, limit=None, kind=None) -> dict:
        return self._client.stats(limit=limit, kind=kind)

    def monitor_policy(self) -> dict:
        return self._client.monitor_policy()

    def status(self):
        """Print a fuller summary (health + recent request activity) and
        return the raw /health body - the console equivalent of the GUI's
        top panel."""
        h = self.health()
        state = h.get("state")
        n_failed = h.get("n_failed") or 0
        ok = bool(h.get("ready")) and not n_failed
        color = (
            colorama.Fore.GREEN if ok
            else colorama.Fore.RED if not h.get("ready")
            else colorama.Fore.YELLOW
        )
        reset = colorama.Style.RESET_ALL
        print(
            f"{self.base_url}: {color}{state}{reset} "
            f"({h.get('n_initialized')}/{h.get('n_target_names')} initialized, "
            f"{n_failed} failed, {h.get('n_monitorable')} monitorable), "
            f"generation {h.get('generation')}, up {h.get('uptime_s', 0)/60:.1f} min"
        )
        failed = h.get("failed_names") or []
        if failed:
            shown = ", ".join(failed[:10])
            more = f" (+{len(failed) - 10} more)" if len(failed) > 10 else ""
            red = colorama.Fore.RED + colorama.Style.BRIGHT
            print(f"  {red}failed:{reset} {shown}{more}")
        warn_failed_required(h)

        try:
            summary = self.stats().get("summary") or {}
        except Exception:
            summary = {}
        if summary.get("n"):
            last_ago = (
                time.time() - summary["last_at"] if summary.get("last_at") else None
            )
            ago = f"{last_ago:.1f}s ago" if last_ago is not None else "?"
            errs = f", {summary['n_errors']} errors" if summary.get("n_errors") else ""
            last_dur = summary.get("last_duration_s")
            dur = f"{last_dur * 1000:.0f} ms" if last_dur is not None else "?"
            print(
                f"  requests: {summary['n']} recent{errs}, last "
                f"'{summary.get('last_kind')}' {ago} ({dur})"
            )
        return h

    def __repr__(self):
        try:
            h = self.health()
            header = (
                f"StatusServer('{self.base_url}') - {h.get('state')}, "
                f"{h.get('n_initialized')}/{h.get('n_target_names')} initialized, "
                f"{h.get('n_failed')} failed\n"
            )
        except Exception as exc:
            header = f"StatusServer('{self.base_url}') - unreachable ({type(exc).__name__})\n"
        # settings (recording_mode, recording_min_interval, ...) were already
        # registered for display -- _append's own is_display defaults to
        # True, and nothing here overrides it. What was missing is this
        # override actually asking for that table: unlike a plain Assembly,
        # StatusServer replaces __repr__ outright for the health summary
        # above, so Assembly.get_display_str() (the settings table) never
        # got called.
        return header + self.get_display_str()

    # -- gui ---------------------------------------------------------------

    def gui(self):
        """Launch the Qt status/reinit monitor for this server, as a
        detached subprocess - the same window `eco-status-server gui`
        opens, not an embedded widget (see the module docstring for why
        that is a separate, not-yet-done step)."""
        self._gui_proc = subprocess.Popen(
            [sys.executable, "-m", "eco.status_server.gui", "--url", self.base_url]
        )
        return self._gui_proc
