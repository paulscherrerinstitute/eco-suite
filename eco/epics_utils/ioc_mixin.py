"""Mixin adding IOC identification + basic remote-management to a controller.

`IOCMixin` gives any eco `Assembly` whose PVs all live on one EPICS IOC three
methods -- `ioc_status()`, `ioc_host_ping()`, `restart_IOC()` -- built on top
of the headless lookup/console helpers in `eco.epics_utils.iocinfo` (the same
backend `eco.widgets.ioc_finder_widget`/`ioc_finder_qt` use, minus the Qt/
ipywidgets GUI layer).

IOC identification is itself lazy and cached: nothing is looked up at
construction time or import time, only on first call to `.ioc()` (or any
method that needs it) -- via `iocinfo.find_ioc()` searched against
`_ioc_search_pattern()` (by default the object's `prefix`/`pvbase`/`P`
attribute, whichever exists). Set `self._ioc_name` directly (or pass
`ioc_name=` through a subclass `__init__`) for a controller whose IOC name is
already known, to skip the search and go straight to `get_ioc()`.
"""

from eco.epics_utils import iocinfo


class IOCMixin:
    _ioc_name = None
    _ioc_match = None

    def _ioc_search_pattern(self):
        """PV pattern to search for this controller's IOC. Default: the
        first of `self.prefix`/`self.pvbase`/`self.P` that's set; override
        for anything else."""
        for attr in ("prefix", "pvbase", "P"):
            value = getattr(self, attr, None)
            if value:
                return value
        raise AttributeError(
            f"{type(self).__name__} has no prefix/pvbase/P to search an IOC "
            "for -- set self._ioc_name directly or override _ioc_search_pattern()"
        )

    def ioc(self, refresh=False):
        """The `iocinfo.IocMatch` for this controller's IOC -- looked up
        once (`iocinfo.find_ioc`) and cached on the instance; pass
        `refresh=True` to force a fresh lookup (e.g. after a restart)."""
        if self._ioc_match is None or refresh:
            pattern = self._ioc_name or self._ioc_search_pattern()
            matches = iocinfo.find_ioc(pattern, fuzzy=True)
            if not matches:
                raise LookupError(f"no IOC found for {pattern!r}")
            if self._ioc_name:
                exact = [m for m in matches if m.ioc == self._ioc_name]
                matches = exact or matches
            self._ioc_match = matches[0]
            self._ioc_name = self._ioc_match.ioc
        return self._ioc_match

    def ioc_status(self):
        """Dict summary of this controller's IOC: name, facility, boot host,
        console address, and whether the console is currently reachable
        (via `ioc_host_ping()`; `None` if the console address is unknown)."""
        m = self.ioc()
        return {
            "ioc": m.ioc,
            "facility": m.facility,
            "boot_host": m.boot.hostname if m.boot else None,
            "console": f"{m.console_host}:{m.console_port}" if m.console_host else None,
            "console_reachable": self.ioc_host_ping() if m.console_host else None,
        }

    def ioc_host_ping(self, timeout=2.0):
        """True/False: is this controller's IOC console TCP port reachable
        right now? Pure liveness probe (`iocinfo.check_console_reachable`) --
        opens and immediately closes a TCP connection, sends/reads nothing.
        `None` if the console address itself couldn't be resolved."""
        m = self.ioc()
        if not m.console_host:
            return None
        return iocinfo.check_console_reachable(m.console_host, m.console_port, timeout=timeout)

    def restart_IOC(self, confirm=True):
        """Restart this controller's IOC process (procServ's Ctrl-X
        restart-child hotkey via `iocinfo.restart_ioc`) -- a real, disruptive,
        live action with no undo. Confirmed operationally only for the
        Bernina MForce IOCs (SARES20-CSSU-MF1/-MF2) as of 2026-08-18; may not
        hold for other IOCs/procServ configs -- see the `iocinfo` module
        docstring.

        Prompts for interactive y/N confirmation unless `confirm=False` --
        passing `confirm=False` is still an explicit choice by the caller,
        this method never restarts silently on its own.
        """
        m = self.ioc()
        if not m.console_host:
            raise LookupError(f"no known console address for IOC {m.ioc!r}; cannot restart")
        if confirm:
            answer = input(
                f"Really restart IOC {m.ioc!r} at {m.console_host}:{m.console_port}? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return
        iocinfo.restart_ioc(m.console_host, m.console_port)
