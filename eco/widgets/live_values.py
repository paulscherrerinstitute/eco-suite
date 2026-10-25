"""Reading and writing eco items for a GUI without ever blocking it -- the
toolkit-independent part of the default property grids (Qt: display_qt,
ipywidgets/Voila: display_widget, Tk: display_tk). No GUI imports here; each
grid only decides how a result reaches its widgets (Qt: a timer flushing a
queue on the GUI thread; Tk: the same via ``after``; ipywidgets: directly,
since widget updates may come from any thread).

- :class:`LiveReader` reads a set of items in a small thread pool: items that
  can push updates (``add_value_callback`` -- motor records, SmarAct --, or
  ``set_current_value_callback`` -- PV-backed adjustables/detectors, virtual
  adjustables whose parents are) are *monitored*: a callback only requests a
  re-read, coalesced to at most 1/MIN_READ_INTERVAL per item, so the shown
  value always comes from ``get_current_value()`` (enum labels, conversions
  exactly as when polled); polling stays as a slow safety net for them and is
  the normal path for the rest, with adaptive back-off for slow items. One
  slow or absent channel no longer holds up the others.

  A subscription alone proves nothing -- e.g. a virtual detector without
  parents (vacuum valve readbacks, the prepump state) subscribes fine and
  never calls back, PshellMotor calls back only when a move ends. So an item
  counts as monitored only once an event has actually arrived, and is
  demoted back to normal polling as soon as a scheduled poll finds a change
  that no event announced.
- :func:`start_write` runs ``set_target_value`` and follows the move in a
  background thread, reporting "moving" / done / the error (a blocked move's
  own message -- e.g. eco.utilities.task_queue.MoveBlocked -- verbatim).
- :func:`prefetch_values` reads initial values concurrently with a deadline.
- :func:`initial_step` / :func:`load_ioc_step` / :func:`store_ioc_step`: the
  tweak step -- the IOC's TWV for motor records with their own tweak fields
  (eco.widgets.tweak_panel.has_ioc_tweak), else the adjustable's own tweak
  step, else 1 / 0.1.
- ``STATE_COLORS``: the status-dot colours all grids use.
"""
import enum
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as _wait_futures

PREFETCH_DEADLINE = 2.0  # s: opening a grid waits at most this long for initial values
MIN_READ_INTERVAL = 0.1  # s: monitor-triggered re-reads of one item are coalesced to this
MONITORED_POLL_INTERVAL = 10.0  # s: safety-net polling of a monitored item
READ_WORKERS = 6  # parallel readbacks per grid
POLL_SLOWDOWN_FACTOR = 10.0  # a slow item is polled every ~duration * this ...
POLL_MAX_INTERVAL = 30.0  # ... at most this far apart

PREFETCH_ERROR = object()  # sentinel: a prefetched read raised
_NO_VALUE = object()  # sentinel: not read yet
PENDING = object()  # sentinel: no value yet (the prefetch deadline passed)

# state -> (colour, explanation) of a row's status dot
STATE_COLORS = {
    "waiting": ("#9e9e9e", "waiting for the first value (connecting)"),
    "live": ("#2e9d4a", "connected, updated by monitor"),
    "subscribed": ("#2e9d4a", "connected, monitored (no update received yet, also polled)"),
    "polled": ("#3d7fd9", "connected, polled"),
    "busy": ("#f0a020", "sending"),
    "moving": ("#f0a020", "moving"),
    "error": ("#d93025", "error"),
}


def format_value(value):
    """Enum-valued readbacks by their label rather than the integer."""
    if isinstance(value, enum.Enum):
        return value.name
    return str(value)


def describe(exc):
    """An exception as shown to the user: a blocked move's own explanation
    (eco.utilities.task_queue.MoveBlocked, eco.elements.access.AccessDenied
    -- both PermissionErrors) verbatim, else type and message."""
    if isinstance(exc, PermissionError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def dot_state(state, message, read_state):
    """(colour, tooltip) for a status dot; state "idle" means "a write ended,
    show what the reads say" (`read_state`, a (state, message) pair)."""
    if state == "idle":
        state, message = read_state
    color, text = STATE_COLORS.get(state, STATE_COLORS["error"])
    return color, (f"{text}: {message}" if message else text)


# -- initial values ---------------------------------------------------------------


def prefetch_values(items, max_workers=8, deadline=None):
    """Read all initial values concurrently, so building a grid is bounded
    by the slowest single readback rather than their sum -- and by
    `deadline` (s) at most: an item without a value by then maps to PENDING
    (its read finishes in the background, unwaited). Returns {id(item):
    value | PREFETCH_ERROR | PENDING}. Never raises."""
    values = {}
    items = list(items)
    if not items:
        return values

    def _read(item):
        return item.get_current_value()

    try:
        executor = ThreadPoolExecutor(max_workers=min(max_workers, len(items)))
        futures = {executor.submit(_read, item): item for item in items}
        _wait_futures(futures, timeout=deadline)
        for future, item in futures.items():
            if not future.done():
                values[id(item)] = PENDING
                continue
            try:
                values[id(item)] = future.result()
            except Exception:
                values[id(item)] = PREFETCH_ERROR
        executor.shutdown(wait=False)
    except Exception:
        pass
    return values


# -- monitoring and polling -----------------------------------------------------------


def subscribe(item, kick):
    """Have `item` call kick() whenever its value changes, if it can push
    updates at all. Returns an unsubscribe callable, or None. May wait for a
    channel connection: call it off the GUI thread."""

    def _on_update(*args, **kwargs):
        kick()

    if callable(getattr(item, "add_value_callback", None)):
        try:
            index = item.add_value_callback(_on_update)
        except Exception:
            index = None
        else:
            clear = getattr(item, "clear_value_callback", None)

            def _unsubscribe():
                if callable(clear) and index is not None:
                    clear(index)

            return _unsubscribe
    if callable(getattr(item, "set_current_value_callback", None)):
        try:
            monitor = item.set_current_value_callback(func=_on_update)
            if monitor is None or not callable(getattr(monitor, "start", None)):
                return None
            if getattr(monitor, "children", None) == []:
                return None  # a composed value of nothing: it would never call back
            try:
                monitor.start(add_current_value=False, with_ctrlvars=False)
            except TypeError:
                monitor.start()
            return monitor.stop
        except Exception:
            return None
    return None


class LiveReader:
    """Keeps a set of items' values coming, never on the caller's thread.

    ``add(item)`` registers an item and returns its index; results arrive
    through ``on_result(index, ok, value_or_message)``, called from pool
    threads -- marshal to the GUI thread there if the toolkit needs it. A
    read returning None is reported as "no value (not connected?)", which is
    how a failed CA get comes back.
    """

    def __init__(self, on_result, poll_interval=1.0, workers=READ_WORKERS):
        self.on_result = on_result
        self.poll_interval = poll_interval
        self.workers = workers
        self.entries = []
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._executor = None
        self._thread = None

    def add(self, item, active=True):
        self.entries.append(
            {
                "item": item,
                "active": active,
                "interval": self.poll_interval,
                "next_due": 0.0,  # monotonic time; 0 => read on the first pass
                "last_read": 0.0,
                "in_flight": False,
                "kicked": False,
                "kicked_read": False,  # whether the read in flight was asked for (monitor/write)
                "monitored": False,  # subscribed, an event arrived, not demoted -- see monitored()
                "events": 0,
                "demoted": False,
                "last_value": _NO_VALUE,
                "subscribed": False,
                "unsubscribe": None,
            }
        )
        return len(self.entries) - 1

    def monitored(self, index):
        """Whether item `index` is kept current by its monitor (an event has
        arrived, and no poll has caught it missing a change since)."""
        return self.entries[index]["monitored"]

    def monitor_state(self, index):
        """"live" (its monitor reports changes), "subscribed" (monitored but
        nothing reported yet -- e.g. a motor at rest; still polled), or None
        (polled: no monitor, or one that missed a change)."""
        entry = self.entries[index]
        if entry["monitored"]:
            return "live"
        if entry["unsubscribe"] is not None and not entry["demoted"]:
            return "subscribed"
        return None

    def _on_event(self, index):
        """A monitor callback for item `index` (any thread)."""
        entry = self.entries[index]
        entry["events"] += 1
        if entry["unsubscribe"] is not None and not entry["demoted"]:
            entry["monitored"] = True
        self.request_read(index)

    def set_active(self, index, active):
        """Only active items are read and monitored (e.g. collapsed hidden
        rows are not); activating one reads it at once."""
        entry = self.entries[index]
        entry["active"] = active
        if active:
            entry["next_due"] = 0.0
        self._wake.set()

    def request_read(self, index):
        """Read item `index` as soon as possible (a monitor saw a change, a
        write finished) -- at most every MIN_READ_INTERVAL. Any thread."""
        try:
            entry = self.entries[index]
        except IndexError:
            return
        entry["kicked"] = True  # also covers a change during a read in flight, see _read
        if not entry["in_flight"]:
            entry["next_due"] = entry["last_read"] + MIN_READ_INTERVAL
        self._wake.set()

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._executor = ThreadPoolExecutor(max_workers=self.workers)
        self._thread = threading.Thread(target=self._schedule, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop reading; monitors are removed in a background thread
        (removing a CA callback can wait on the CA library)."""
        self._stop.set()
        self._wake.set()
        self._thread = None
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        unsubscribes = [e["unsubscribe"] for e in self.entries if e["unsubscribe"]]
        for entry in self.entries:  # so that start() again begins afresh
            entry.update(unsubscribe=None, subscribed=False, monitored=False, in_flight=False,
                         next_due=0.0, events=0, demoted=False)
        if unsubscribes:

            def _unsubscribe_all():
                for unsubscribe in unsubscribes:
                    try:
                        unsubscribe()
                    except Exception:
                        pass

            threading.Thread(target=_unsubscribe_all, daemon=True).start()

    # -- internals ---------------------------------------------------------------

    def _subscribe(self, index):
        entry = self.entries[index]
        unsubscribe = subscribe(entry["item"], lambda i=index: self._on_event(i))
        if self._stop.is_set():  # closed while subscribing
            if unsubscribe is not None:
                try:
                    unsubscribe()
                except Exception:
                    pass
            return
        entry["unsubscribe"] = unsubscribe  # monitored only once an event arrives, see _on_event

    def _read(self, index):
        entry = self.entries[index]
        t0 = time.monotonic()
        try:
            value = entry["item"].get_current_value()
            if value is None:
                result = (False, "no value (not connected?)")
            else:
                result = (True, value)
        except Exception as exc:
            result = (False, describe(exc))
        now = time.monotonic()
        if result[0] and entry["monitored"] and not entry["kicked_read"]:
            # a scheduled safety poll: a change no event announced means the
            # monitor doesn't report this item's changes -- poll it normally
            if _changed(entry["last_value"], result[1]):
                entry["monitored"] = False
                entry["demoted"] = True
        if result[0]:
            entry["last_value"] = result[1]
        if entry["monitored"]:
            interval = MONITORED_POLL_INTERVAL
        else:
            interval = min(POLL_MAX_INTERVAL, max(self.poll_interval, (now - t0) * POLL_SLOWDOWN_FACTOR))
        entry["interval"] = interval
        entry["last_read"] = now
        # a change reported while this read ran may not be in it: read again soon
        entry["next_due"] = now + (MIN_READ_INTERVAL if entry["kicked"] else interval)
        entry["kicked"] = False
        entry["in_flight"] = False
        self._wake.set()
        if not self._stop.is_set():
            try:
                self.on_result(index, *result)
            except Exception:
                pass

    def _schedule(self):
        # hands due reads to the pool (never more than one in flight per
        # item) and subscribes monitorable items once they are active
        while not self._stop.is_set():
            now = time.monotonic()
            next_wakeup = now + max(self.poll_interval, 0.05)
            for index, entry in enumerate(self.entries):
                if self._stop.is_set():
                    return
                if not entry["active"]:
                    continue
                executor = self._executor
                if executor is None:
                    return
                try:
                    if not entry["subscribed"]:
                        entry["subscribed"] = True
                        executor.submit(self._subscribe, index)
                    if entry["in_flight"]:
                        continue
                    if now < entry["next_due"]:
                        next_wakeup = min(next_wakeup, entry["next_due"])
                        continue
                    entry["in_flight"] = True
                    entry["kicked_read"] = entry["kicked"] or entry["next_due"] == 0.0
                    entry["kicked"] = False  # this read covers what was reported so far
                    executor.submit(self._read, index)
                except RuntimeError:  # the pool was shut down: stopping
                    return
            self._wake.wait(max(0.02, next_wakeup - time.monotonic()))
            self._wake.clear()


def _changed(old, new):
    if old is _NO_VALUE:
        return False
    try:
        return bool(old != new)
    except Exception:  # e.g. arrays: undecidable, so no verdict
        return False


# -- writes -------------------------------------------------------------------------------


def start_write(item, target_fn, on_state, on_finished=None, holder=None):
    """``item.set_target_value(target_fn())`` in a background thread,
    following the move. `target_fn` runs there too (it may read the item,
    e.g. current value + step). Reports through ``on_state(state, message)``
    from that thread: "moving" once a changer exists, then "idle" (done) or
    "error" with :func:`describe`'s text. ``on_finished()`` always runs at
    the end (e.g. to request a re-read); ``holder["changer"]`` is the
    changer, for :func:`stop_move`."""

    def run():
        try:
            target = target_fn()
            changer = item.set_target_value(target)
            if holder is not None:
                holder["changer"] = changer
            if hasattr(changer, "wait"):
                on_state("moving", f"to {target!r}")
                changer.wait()
            on_state("idle", "")
        except Exception as exc:
            on_state("error", describe(exc))
        finally:
            if on_finished is not None:
                try:
                    on_finished()
                except Exception:
                    pass

    threading.Thread(target=run, daemon=True).start()


def stop_move(item, changer, on_state):
    """Stop the move `changer` belongs to (or `item` itself), in a
    background thread."""

    def run():
        try:
            if changer is not None and callable(getattr(changer, "stop", None)):
                changer.stop()
            elif callable(getattr(item, "stop", None)):
                item.stop()
        except Exception as exc:
            on_state("error", f"stop failed: {describe(exc)}")

    threading.Thread(target=run, daemon=True).start()


# -- tweak step ------------------------------------------------------------------------------


def default_step_for(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return 1
    return 0.1


def initial_step(item, value):
    """The step a tweak field starts with before (or without) the IOC's: the
    adjustable's own tweak step if one was set up, else 1 / 0.1."""
    try:
        from eco.manual_control.step_store import tweak_step_size

        step = tweak_step_size(item)
        if step:
            return step
    except Exception:
        pass
    return default_step_for(value)


def has_ioc_step(item):
    """Whether `item` is a motor record with its own tweak step (TWV)."""
    try:
        from eco.widgets.tweak_panel import has_ioc_tweak

        return has_ioc_tweak(item)
    except Exception:
        return False


def load_ioc_step(item, on_step):
    """Read the IOC's tweak step (TWV) in a background thread and pass it to
    ``on_step(value: float)``. Returns a holder whose "pv" is the channel
    once known (for :func:`store_ioc_step`)."""
    holder = {}

    def run():
        try:
            from eco.widgets.tweak_panel import ioc_tweak_pvs

            pv = holder["pv"] = ioc_tweak_pvs(item)[0]
            value = pv.get(timeout=2.0)
            if value is not None:
                on_step(float(value))
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
    return holder


def store_ioc_step(holder, value):
    """Write a tweak step back to the IOC (TWV), in a background thread."""
    pv = holder.get("pv")
    if pv is not None:
        threading.Thread(target=lambda: pv.put(float(value)), daemon=True).start()
