"""Recently-used-component tracking, shared by the picker UI
(eco.widgets.component_picker_widget / component_selector_widget /
component_selector_qt) and the central adjustable-write chokepoint
(eco.devices_general.utilities.Changer) -- so the picker's "Recent" list
reflects real usage (typing ``mono.mv(5)`` in a shell), not just prior picks
made through the picker itself.

Scoped per *user* (eco.elements.access.current_identity()), not per OS home
directory: beamline consoles log into one shared account, so
``Path.home()`` alone can't tell two operators apart -- same rationale
access.py already uses for write-access identity.

Lives in eco.elements (not eco.widgets) so eco.devices_general.utilities --
loaded for every adjustable, on the hottest path in the codebase -- can
depend on it without a widgets/ipywidgets import chain hanging off of every
device write.
"""
import threading
from pathlib import Path
from typing import Optional

from eco.elements.adjustable import AdjustableFS

# Module-level kill switch (mirrors eco.elements.access.enforce): flip to
# False to stop the write-chokepoint from touching the recent list at all.
enabled = True

# How long to hold new touches in memory before flushing to disk, so a fast
# scan doing thousands of writes coalesces into one write instead of
# thousands -- same trailing-edge-debounce idea as
# daq_client.py's _debounced_append_aux.
default_debounce_seconds = 2.0


def _current_user() -> str:
    try:
        from eco.elements.access import current_identity

        return current_identity().name
    except Exception:
        return "unknown"


def _full_name(component) -> str:
    try:
        return component.alias.get_full_name()
    except Exception:
        return getattr(component, "name", repr(component))


class RecentComponents:
    """Lightweight MRU cache of recently *used* component paths, most-
    recently-touched first, capped at `max_items`. Backed by the same
    `AdjustableFS`-persisted-json primitive `eco.elements.memory.Memory`
    and `ComponentBookmarks` use, so it survives process restarts.

    `touch()` is safe to call from a hot path: the in-memory list updates
    synchronously, but the on-disk write is debounced.
    """

    def __init__(
        self,
        path=None,
        namespace_name: Optional[str] = None,
        user: Optional[str] = None,
        max_items: int = 15,
        debounce_seconds: float = default_debounce_seconds,
        name: str = "component_selector_recent",
    ):
        self.user = user if user is not None else _current_user()
        if path is None:
            base_dir = Path.home() / ".eco" / "component_selector"
            parts = ["recent"]
            if namespace_name:
                parts.append(str(namespace_name))
            parts.append(self.user)
            path = base_dir / ("_".join(parts) + ".json")
        self.max_items = max_items
        self.debounce_seconds = debounce_seconds
        # group_writable=False: this cache is scoped to one user (see class
        # docstring), never meant for another account to write -- skip the
        # shared-tree group-writable machinery instead of having it fail and
        # warn on every flush (observed under gac-bernina: a 0700 $HOME
        # keeps other accounts out regardless of the file's own mode, so the
        # chmod attempt could never have accomplished anything anyway).
        self._fs = AdjustableFS(path, default_value=[], name=name, group_writable=False)
        # RLock, not Lock: touch() calls all() (also lock-guarded) while
        # already holding the lock -- a plain Lock would self-deadlock.
        self._lock = threading.RLock()
        self._cache: Optional[list] = None  # in-memory, ahead of the debounced write
        self._timer: Optional[threading.Timer] = None

    @property
    def path(self) -> Path:
        return self._fs.file_path

    def all(self) -> list:
        with self._lock:
            if self._cache is not None:
                return list(self._cache)
        return list(self._fs.get_current_value())

    def touch(self, dotted_path: str) -> None:
        """Record a use: move `dotted_path` to the front, deduping and
        capping at `max_items`. No-op for an empty path."""
        if not dotted_path:
            return
        with self._lock:
            items = list(self._cache) if self._cache is not None else self.all()
            items = [p for p in items if p != dotted_path]
            items.insert(0, dotted_path)
            del items[self.max_items :]
            self._cache = items
            self._schedule_flush()

    def flush(self) -> None:
        """Force the pending write to disk now. Mainly for tests/shutdown;
        touch() alone is enough in normal use."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            pending = self._cache
        if pending is not None:
            self._fs.write_value_direct(pending)

    def clear(self) -> None:
        with self._lock:
            self._cache = []
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        # caller already holds self._lock
        if self._timer is not None:
            self._timer.cancel()

        def fire():
            with self._lock:
                pending = self._cache
                self._timer = None
            try:
                # write_value_direct, not set_target_value: this bypasses
                # Changer (and thus this very module's own write-chokepoint
                # hook) -- routing it through Changer would record this
                # write as a "use" of the recent-cache's own storage file,
                # scheduling another flush, forever.
                self._fs.write_value_direct(pending)
            except Exception:
                pass  # never let a debounced write raise into the caller's thread

        self._timer = threading.Timer(self.debounce_seconds, fire)
        self._timer.daemon = True
        self._timer.start()


class FrequencyCounts:
    """Per-user use-*count* tracking -- "Recommended" in the picker UI is
    ranked by how often a component has actually been used (real writes via
    Changer, or picks made through any picker UI), which is a different
    question from RecentComponents' "most recently" -- a component you use
    constantly but not in the last hour should still surface here even
    after newer, one-off components have pushed it out of Recent.

    Same AdjustableFS-persisted-json pattern, same per-user scoping, same
    debounced flush as RecentComponents -- see that class for the fuller
    rationale, not repeated here.
    """

    def __init__(
        self,
        path=None,
        namespace_name: Optional[str] = None,
        user: Optional[str] = None,
        debounce_seconds: float = default_debounce_seconds,
        name: str = "component_selector_frequency",
    ):
        self.user = user if user is not None else _current_user()
        if path is None:
            base_dir = Path.home() / ".eco" / "component_selector"
            parts = ["frequency"]
            if namespace_name:
                parts.append(str(namespace_name))
            parts.append(self.user)
            path = base_dir / ("_".join(parts) + ".json")
        self.debounce_seconds = debounce_seconds
        self._fs = AdjustableFS(path, default_value={}, name=name, group_writable=False)
        self._lock = threading.RLock()
        self._cache: Optional[dict] = None
        self._timer: Optional[threading.Timer] = None

    @property
    def path(self) -> Path:
        return self._fs.file_path

    def all(self) -> dict:
        """`{dotted_path: count}`, unordered."""
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
        return dict(self._fs.get_current_value())

    def touch(self, dotted_path: str) -> None:
        """Record one more use of `dotted_path`. No-op for an empty path."""
        if not dotted_path:
            return
        with self._lock:
            counts = dict(self._cache) if self._cache is not None else self.all()
            counts[dotted_path] = counts.get(dotted_path, 0) + 1
            self._cache = counts
            self._schedule_flush()

    def top(self, n: int = 15) -> list:
        """Up to `n` dotted paths, most-used first (ties broken
        alphabetically, for a stable order across calls)."""
        counts = self.all()
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [path for path, _count in ranked[:n]]

    def flush(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            pending = self._cache
        if pending is not None:
            self._fs.write_value_direct(pending)

    def clear(self) -> None:
        with self._lock:
            self._cache = {}
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        # caller already holds self._lock
        if self._timer is not None:
            self._timer.cancel()

        def fire():
            with self._lock:
                pending = self._cache
                self._timer = None
            try:
                self._fs.write_value_direct(pending)
            except Exception:
                pass  # never let a debounced write raise into the caller's thread

        self._timer = threading.Timer(self.debounce_seconds, fire)
        self._timer.daemon = True
        self._timer.start()


# --- process-wide, per-user instances used by the Changer write chokepoint ---

_instances = {}
_instances_lock = threading.Lock()
_frequency_instances = {}
_frequency_instances_lock = threading.Lock()


def _instance_for_current_user() -> RecentComponents:
    user = _current_user()
    with _instances_lock:
        inst = _instances.get(user)
        if inst is None:
            inst = RecentComponents(user=user)
            _instances[user] = inst
        return inst


def _frequency_instance_for_current_user() -> FrequencyCounts:
    user = _current_user()
    with _frequency_instances_lock:
        inst = _frequency_instances.get(user)
        if inst is None:
            inst = FrequencyCounts(user=user)
            _frequency_instances[user] = inst
        return inst


def touch_from_write(component) -> None:
    """Called from eco.devices_general.utilities.Changer.__init__ for every
    adjustable write. Deliberately swallows everything -- a broken recent-
    tracking call must never break device motion."""
    if not enabled:
        return
    try:
        name = _full_name(component)
        _instance_for_current_user().touch(name)
        _frequency_instance_for_current_user().touch(name)
    except Exception:
        pass
