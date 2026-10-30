"""Where the gateway gets its objects from.

`SessionBackend` wraps a live eco session's namespace (hosting mode B in
web_frontend/ARCHITECTURE.md). A status-server backend (mode A, read-only)
is meant to implement the same small interface later:

- ``label``                 -- shown in the client's title bar
- ``names()``               -- {"initialized": [...], "lazy": [...], "failed": {name: reason}}
- ``resolve(name)``         -- a top-level object, initializing a lazy one
- ``svg(obj, live)``        -- (svg text, click prefix) for an object with a panel
"""
import logging
import os
import threading

from eco.web.viewmodels import full_name

logger = logging.getLogger(__name__)

MAX_REASON = 400


class NotFound(LookupError):
    pass


def attach_ca_context():
    """Attach this worker thread to the process's one CA context before it
    makes CA calls -- a second, implicitly created context in the same
    process is a documented segfault source (see the long comment in
    eco.widgets.desktop_app._NamespaceLauncher's init thread)."""
    try:
        import epics.ca as ca

        ca.use_initial_context()
    except Exception:
        pass


class SessionBackend:
    def __init__(self, namespace, label=None):
        self.namespace = namespace
        self.label = label or getattr(namespace, "name", None) or "eco"
        self._init_lock = threading.Lock()

    def names(self):
        ns = self.namespace
        reasons = getattr(ns, "failed_items_excpetion", {}) or {}
        failed = {}
        for name in sorted(getattr(ns, "failed_names", set())):
            reason = reasons.get(name)
            failed[name] = (f"{type(reason).__name__}: {reason}" if reason is not None else "")[:MAX_REASON]
        return {
            "initialized": sorted(getattr(ns, "initialized_names", set())),
            "lazy": sorted(getattr(ns, "lazy_names", set())),
            "failed": failed,
        }

    def resolve(self, name):
        """A top-level component (initialized first if lazy), or a dotted
        attribute path below one ("mono.energy"); private parts refused."""
        ns = self.namespace
        if name not in getattr(ns, "all_names", set()) and "." in name:
            head, *parts = name.split(".")
            if any(not part or part.startswith("_") for part in parts):
                raise NotFound(f"not a public name: {name!r}")
            obj = self.resolve(head)
            for part in parts:
                try:
                    obj = getattr(obj, part)
                except AttributeError:
                    raise NotFound(f"no {part!r} in {name!r}") from None
            return obj
        if name not in getattr(ns, "all_names", set()):
            raise NotFound(f"no component {name!r} in {self.label}")
        if name in getattr(ns, "lazy_names", set()):
            attach_ca_context()
            with self._init_lock:  # one at a time, like the desktop launcher's clicks
                if name in ns.lazy_names:
                    ns.init_name(name, raise_errors=False, verbose=False)
        if name in getattr(ns, "failed_names", set()):
            raise NotFound(f"{name} failed to initialize: {self.names()['failed'].get(name, '')}")
        obj = ns.resolve_item(name)
        from eco.utilities.config import Proxy

        if isinstance(obj, Proxy):
            obj = obj.__wrapped__
        return obj

    def svg(self, obj, live=True):
        """The SVG panel of `obj` as text, plus the prefix its click markers
        are relative to (Assembly.show: commands run against the assembly's
        own full name)."""
        attach_ca_context()
        path = None
        if callable(getattr(type(obj), "_widget_svg_panel", None)):
            path = obj._widget_svg_panel(live=live)
        if not path:
            path = object.__getattribute__(obj, "__dict__").get("_show_svg")
        if not path or not os.path.exists(path):
            raise NotFound(f"{full_name(obj)} has no SVG panel")
        with open(path, encoding="utf-8") as f:
            return f.read(), full_name(obj)


def calling_namespace():
    """The `namespace` variable of the running IPython session, as
    eco.widgets.app_launchers does for the desktop."""
    try:
        from IPython import get_ipython

        ip = get_ipython()
    except Exception:
        ip = None
    if ip is None:
        return None
    return getattr(ip, "user_ns", {}).get("namespace")
