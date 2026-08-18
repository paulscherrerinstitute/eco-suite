"""Namespace-hosted status/monitor store - hosts the live eco namespace
(e.g. bernina) directly inside the server process, instead of a bare
alias/PV registry (see channel_registry.py / monitor_store.py).

Why this exists: a plain PV registry can only ever answer for channels it
knows the names of. It has no way to represent values backed by
AdjustableMemory/DetectorMemory (pure in-process Python values) or
AdjustableFS (JSON-file-backed settings) - both of which namespace.get_status()
includes. Matching namespace.get_status() 1:1 means walking the *live*
status_collection tree, which means the server must hold the real namespace
object, not just a list of channel names.

Trade-offs versus the channel_registry.py/monitor_store.py approach, found
by testing against the real bernina namespace, not assumed:

- Startup is slow and depends on external systems. Importing the module is
  cheap (components are lazy proxies), but namespace.init_all() itself was
  observed taking well over 2 minutes for the ~63 "required" bernina
  components even before finishing, in part because some components
  authenticate to auxiliary machines over SSH during __init__ - this is
  not a fast operation you can retry casually or hide behind a short
  request timeout.

- init_all() MUST run with max_workers=1 (the eco default - see
  eco/utilities/config.py:588). Overriding it to run concurrently
  (max_workers=8) reproducibly segfaulted the interpreter inside libca's
  CA-TCP-recv thread while testing this module (confirmed via dmesg, not
  guessed) - i.e. the CA client library is not safe for multiple threads
  to be independently creating/connecting channels at once. Production
  code (Daq.init_namespace in daq_client.py) already only ever calls
  init_all() with the default, so this isn't a newly discovered production
  bug, but it is a hard constraint on this module: never pass
  max_workers>1 here.

- AdjustableMemory/DetectorMemory values are process-local. If this
  server runs as its own process (as opposed to being embedded in the
  interactive session), it holds its OWN instances of these objects -
  changes made through a *different* eco session (e.g. an interactive
  ipython session a scientist is using) are invisible to the server and
  vice versa. This is a fundamental limitation of hosting a second,
  separate copy of the namespace, not a bug to fix - it needs to be an
  accepted, documented trade-off of running the server as its own process
  at all.
"""

from __future__ import annotations

import importlib
import logging
from threading import RLock, Thread

from eco.elements.protocols import Detector, MonitorableValueUpdate

logger = logging.getLogger(__name__)


class ReinitInProgress(Exception):
    pass


class NamespaceMonitorStore:
    def __init__(
        self,
        module_name: str,
        attr_name: str = "namespace",
        init_required_only: bool = False,
    ):
        self.module_name = module_name
        self.attr_name = attr_name
        self.init_required_only = init_required_only
        self._lock = RLock()
        self.busy = False
        self.busy_reason = None
        self.namespace = None
        self._monitors = {}  # full_name -> callback handle (monitored case)
        self._direct = {}  # full_name -> live Detector object (read fresh at snapshot time)
        self._channels = {}  # full_name -> pv/channel name, where known
        self._load_and_init()

    # -- setup -----------------------------------------------------------

    def _import_namespace(self):
        module = importlib.import_module(self.module_name)
        return getattr(module, self.attr_name)

    def _load_and_init(self):
        namespace = self._import_namespace()
        # See module docstring: max_workers=1 is not a performance default
        # to "optimize" - a higher value was observed to segfault libca.
        # (init_all()'s own concurrent background=True pass is now
        # CA-context-safe at higher worker counts - see
        # Namespace._run_init_pass in eco/utilities/config.py - but this
        # call deliberately stays blocking (background=False) and at
        # max_workers=1, unchanged, since that's not what's being
        # revisited here.)
        namespace.init_all(
            required_only=self.init_required_only,
            max_workers=1,
            background=False,
            silent=True,
        )
        self.namespace = namespace
        self._build_monitors()

    def _build_monitors(self):
        self._stop_all_monitors()
        monitors, direct, channels = {}, {}, {}
        for ts in self.namespace.status_collection.get_list():
            if not isinstance(ts, Detector):
                continue
            try:
                full_name = ts.alias.get_full_name(base=self.namespace)
            except Exception:
                full_name = getattr(ts, "name", repr(ts))
            try:
                channels[full_name] = ts.alias.channel
            except Exception:
                pass
            if isinstance(ts, MonitorableValueUpdate):
                try:
                    mon = ts.set_current_value_callback(func="latest")
                    mon.start()
                    monitors[full_name] = mon
                    continue
                except Exception:
                    logger.warning(
                        "Could not start a monitor for %s, falling back to a "
                        "direct read at snapshot time",
                        full_name,
                        exc_info=True,
                    )
            # Not monitorable (or monitor setup failed) - AdjustableMemory,
            # DetectorMemory, AdjustableFS and similar land here. These are
            # process-local/file reads, not CA gets, so reading them fresh
            # on every snapshot is cheap - they were never the source of
            # the network-traffic problem this service exists to fix.
            direct[full_name] = ts
        self._monitors = monitors
        self._direct = direct
        self._channels = channels

    def _stop_all_monitors(self):
        for mon in self._monitors.values():
            try:
                mon.stop()
            except Exception:
                logger.warning("Error stopping a monitor during teardown", exc_info=True)
        self._monitors = {}

    # -- serving -----------------------------------------------------------

    def snapshot(self):
        if self.busy:
            raise ReinitInProgress(self.busy_reason or "reinitializing")
        status = {}
        for name, mon in self._monitors.items():
            status[name] = mon.data.get("value")
        for name, obj in self._direct.items():
            try:
                status[name] = obj.get_current_value()
            except Exception:
                logger.warning("Could not read %s directly", name, exc_info=True)
        return {"status": status, "status_channels": dict(self._channels)}

    def connection_report(self):
        return {
            "n_monitored": len(self._monitors),
            "n_direct_read": len(self._direct),
        }

    # -- reinit -----------------------------------------------------------

    def start_reinit(self, reload_modules=None):
        """Best-effort re-initialization - NOT a surgical "reload only what
        changed". Python has no reliable, general way to detect what
        changed on disk, so this always fully tears down and rebuilds:
        stops every monitor, re-imports (and optionally importlib.reload()s)
        modules, then re-runs init_all() from scratch - in a background
        thread, since init_all() alone was observed taking multiple minutes
        against the real bernina namespace, far too long to hold an HTTP
        request open.

        `reload_modules`: extra module names (beyond `self.module_name`,
        which is always reloaded) to importlib.reload() first - e.g. a
        device class's module you just edited on disk. Reloading a
        module does NOT retroactively change objects already constructed
        from its old code - only instances created afterwards (i.e. by the
        init_all() call made right after) pick up the change. If you're
        not sure a given change is safe to hot-reload this way, prefer
        restarting the whole process (systemd) instead - that guarantees
        every module is imported fresh, with none of importlib.reload()'s
        edge cases around stale class identities.

        Raises ReinitInProgress synchronously (before returning) if a
        reinit is already running, so the REST layer can turn that into a
        409 immediately. Otherwise returns immediately, having set `busy`
        True - snapshot() raises ReinitInProgress (-> REST 503) until the
        background thread finishes and clears it.
        """
        with self._lock:
            if self.busy:
                raise ReinitInProgress(self.busy_reason or "reinitializing")
            self.busy = True
            self.busy_reason = "reinitializing"
        Thread(target=self._reinit_worker, args=(reload_modules,), daemon=True).start()

    def _reinit_worker(self, reload_modules):
        try:
            self._stop_all_monitors()
            module = importlib.import_module(self.module_name)
            importlib.reload(module)
            for mod_name in reload_modules or []:
                importlib.reload(importlib.import_module(mod_name))
            namespace = getattr(
                importlib.import_module(self.module_name), self.attr_name
            )
            namespace.init_all(
                required_only=self.init_required_only,
                max_workers=1,
                silent=False,
                quiet=True,
                print_summary=False,
            )
            self.namespace = namespace
            self._build_monitors()
        except Exception:
            logger.error("Background reinit failed", exc_info=True)
        finally:
            with self._lock:
                self.busy = False
                self.busy_reason = None
