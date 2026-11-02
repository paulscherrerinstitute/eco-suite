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
import time

from eco.web.viewmodels import full_name

logger = logging.getLogger(__name__)

MAX_REASON = 400
SVG_REUSE = 2.0  # s: a panel built this recently is served again to the next client


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
    def __init__(self, namespace, label=None, queues=None):
        self.namespace = namespace
        self._queues = queues  # default: the session's eco.utilities.task_queue.queues
        self.label = label or getattr(namespace, "name", None) or "eco"
        self._init_lock = threading.Lock()
        self._index_lock = threading.Lock()
        self._index = None  # (initialized names, entries) -- see search_index
        self._svg_lock = threading.Lock()
        self._svg_cache = {}  # id(obj) -> (lock, [time, text, prefix])

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
        # full names of a real namespace's items start with its own name
        # ("bernina.slit_att.hpos"); the top-level names do not
        prefix = f"{getattr(ns, 'name', None)}."
        if name.startswith(prefix) and name not in getattr(ns, "all_names", set()):
            name = name[len(prefix):]
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

    def queues(self):
        """{name: TaskQueue} of the session (eco.utilities.task_queue.queues)."""
        if self._queues is not None:
            return self._queues
        from eco.utilities import task_queue

        return task_queue.queues

    def search_index(self):
        """[(full name, kind, object)] of every adjustable/detector in the
        initialized components, rebuilt when that set changes. Shared by all
        clients; walking a big namespace takes a moment, done once."""
        from eco.web.viewmodels import kind_of

        initialized = frozenset(getattr(self.namespace, "initialized_names", set()))
        with self._index_lock:
            if self._index is not None and self._index[0] == initialized:
                return self._index[1]
            entries, seen = [], set()

            def consider(item):
                kind = kind_of(item)
                name = full_name(item)
                if kind in ("adjustable", "detector") and name not in seen:
                    seen.add(name)
                    entries.append((name, kind, item))

            for top in sorted(initialized):
                try:
                    obj = self.resolve(top)
                except Exception:
                    continue
                consider(obj)
                try:
                    items = obj.status_collection.get_list()
                except Exception:
                    items = []
                for item in items:
                    try:
                        consider(item)
                    except Exception:
                        pass
            self._index = (initialized, entries)
            return entries

    def svg(self, obj, live=True):
        """The SVG panel of `obj` as text, plus the prefix its click markers
        are relative to (Assembly.show: commands run against the assembly's
        own full name).

        Built into a file of its own (a live Qt panel of the same device
        rewrites the default one on its own timer, and a half-written file
        would be read here), one build at a time per panel, and reused for
        SVG_REUSE s: a live beamline panel reads every valve, and three
        phones refreshing it must not triple that."""
        with self._svg_lock:
            lock, cached = self._svg_cache.setdefault((id(obj), live), (threading.Lock(), [0.0, None, None]))
        with lock:
            if cached[1] is not None and time.monotonic() - cached[0] < SVG_REUSE:
                return cached[1], cached[2]
            text, prefix = self._build_svg(obj, live)
            cached[:] = [time.monotonic(), text, prefix]
            return text, prefix

    def _build_svg(self, obj, live):
        attach_ca_context()
        path = None
        if callable(getattr(type(obj), "_widget_svg_panel", None)):
            from eco.utilities.tempfiles import user_temp_svg_path

            own = user_temp_svg_path("eco_web", f"{full_name(obj)}_{os.getpid()}")
            try:
                path = obj._widget_svg_panel(path=own, live=live)
            except TypeError:  # a builder without path=
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
