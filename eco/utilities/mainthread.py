"""Keep main-thread-bound work alive while the main thread is busy.

Qt windows (camera viewers, matplotlib figures with a Qt backend, the eco
desktop) can only be serviced by the main thread, and an interactive session
runs the user's own code on that same thread. IPython services the Qt event
loop only while it sits at the prompt, so every blocking statement -- a
``umv``, a scan, a ``time.sleep`` -- freezes every open window, and in the
eco desktop (whose in-process kernel runs each cell on the Qt main thread)
the whole workbench, console included.

The heartbeat
-------------
Everything stays in this process and on the main thread; nothing is moved to
another thread or process:

- a watchdog thread notices when the main thread has stopped servicing its
  loop: every loop-driven tick (a QTimer, i.e. Qt's own event loop) refreshes
  a timestamp, and when that timestamp is older than ``stall`` seconds the
  main thread must be busy in user code;
- it then sends ``SIGUSR2`` to the main thread only (``pthread_kill``).
  Python runs signal handlers on the main thread between bytecodes -- also
  inside ``time.sleep`` and timed lock waits -- and the handler runs one
  *tick*: ``QApplication.processEvents()``, the registered services, and the
  queued ``call_soon`` calls. Then the interrupted code simply continues.

So the main thread takes turns between the user's code and the GUI, about
every ``interval`` seconds. While the user's code waits (a motor move) the
GUI fills otherwise idle time; while it computes, each tick takes a few ms.

Measured on a Bernina console with a 20 Hz camera viewer: 0 frames/s without
the heartbeat during sleep/join/pure-Python/file-IO blocking, 19-20 frames/s
with it, for both the terminal shell and a docked viewer in the desktop.

Rules the implementation relies on
----------------------------------
- It only interrupts the main thread while a watched IPython shell runs a
  cell (``scope="cells"``, the default; the session's own shell and the
  desktop's in-process kernel are watched). Anywhere else the main thread may
  be inside a Qt handler -- a window's closeEvent tearing it down -- and
  pumping events from in there re-enters Qt mid-way; this was a real
  segfault before the rule existed. eco's own close/stop paths additionally
  run under :func:`hold`, since user code can trigger them from a cell.
- Ctrl-C is deferred while a tick runs and re-raised afterwards, i.e. in the
  interrupted user code. Without this a Ctrl-C landing during
  ``processEvents`` was raised inside a Qt slot and swallowed (measured: the
  user's loop was aborted 1 time out of 10 instead of 10/10).
- ``SIGUSR2`` is installed with ``SA_RESTART`` (``siginterrupt(False)``), so C
  libraries doing socket/terminal I/O on the main thread do not see ``EINTR``.
  The price: an *untimed* lock wait (``Thread.join()``, ``Event.wait()``) is
  not interrupted, so the handler cannot run during it. Timed waits are fine.
  eco's own chokepoints therefore wait in slices (see
  ``eco.utilities.utilities.PropagatingThread.join`` and :func:`join`/
  :func:`wait_event` here).
- The watchdog never calls ``print``/``logging``/``import`` or takes a lock:
  any of those can wait on the main thread and would stall the watchdog
  exactly when it is needed. Registries are replaced copy-on-write instead of
  being locked, for the same reason.
- A single long C call on the main thread (a large numpy operation) cannot be
  interrupted; the GUI resumes when it returns.

Services and calls
------------------
Besides pumping Qt, a tick runs registered services and queued calls --
the general part, meant for anything that must happen on the main thread
(other GUI toolkits, a status line, GUI work requested by a background
scan/queue thread later on). Both run *inside whatever user code happens to be
executing*, so they must be short, non-blocking, and touch only their own
state:

    mainthread.add_service("status", refresh_status_line, interval=0.5)
    future = mainthread.call_soon(open_plot, data)       # from any thread
    mainthread.run_on_main(func, *args)                   # same, and wait

``ECO_NO_HEARTBEAT=1`` disables installation; ``uninstall()`` removes it from
a running session; ``status()`` reports what it has been doing.
"""

import collections
import contextlib
import functools
import os
import signal
import sys
import threading
import time
import traceback
from concurrent.futures import Future

INTERVAL = 0.025  # s between ticks while the main thread is busy
STALL = 0.06  # s without a loop-driven tick before the main thread counts as busy
SLICE = 0.05  # s, see join()/wait_event()
QT_CHECK_INTERVAL = 0.5  # s between watchdog checks for a newly created QApplication
SERVICE_ERRORS_MAX = 3  # consecutive failures before a service is disabled

_SIGNAL = signal.SIGUSR2
_heartbeat = None

_QT_WIDGET_MODULES = (
    "qtpy.QtWidgets",
    "PyQt5.QtWidgets",
    "PyQt6.QtWidgets",
    "PySide6.QtWidgets",
    "PySide2.QtWidgets",
)


def _qt_application():
    """The running QApplication, if any, without importing anything: only
    looks at Qt bindings some other code already imported."""
    for name in _QT_WIDGET_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            try:
                return module.QApplication.instance()
            except Exception:
                return None
    return None


def _on_main_thread():
    return threading.current_thread() is threading.main_thread()


class _Service:
    __slots__ = ("func", "interval", "next_due", "errors")

    def __init__(self, func, interval):
        self.func = func
        self.interval = interval
        self.next_due = 0.0
        self.errors = 0


_qt_driver_class = None


def _make_qt_driver(app):
    """A QObject living on the main thread with a ``wake`` signal (for
    call_soon from other threads) and a child QTimer (loop-driven ticks)."""
    global _qt_driver_class
    from qtpy import QtCore

    if _qt_driver_class is None:

        class _QtDriver(QtCore.QObject):
            wake = QtCore.Signal()

        _qt_driver_class = _QtDriver
    driver = _qt_driver_class(app)
    driver.timer = QtCore.QTimer(driver)
    return driver


class Heartbeat:
    """See the module docstring. Use :func:`install` rather than constructing
    this directly -- there must only ever be one per process."""

    def __init__(self, interval=INTERVAL, stall=STALL, scope="cells"):
        if not _on_main_thread():
            raise RuntimeError("the heartbeat can only be installed from the main thread")
        if scope not in ("cells", "always"):
            raise ValueError(f"scope must be 'cells' or 'always', not {scope!r}")
        previous = signal.getsignal(_SIGNAL)
        if previous not in (signal.SIG_DFL, None):
            raise RuntimeError(f"{_SIGNAL.name} already has a handler ({previous!r})")
        self.interval = interval
        self.stall = stall
        self.scope = scope
        self._cells = 0  # depth of cells currently executing in watched shells
        self._watched_shells = []
        self._services = {}  # replaced copy-on-write, never mutated in place
        self._calls = collections.deque()
        self._last_tick = time.monotonic()  # any tick, for the max_gap_s statistic
        self._last_loop_tick = self._last_tick  # ticks driven by Qt's own loop only
        self._in_tick = False
        self._hold = 0
        self._qt = None  # _QtDriver, created on the main thread once Qt is serviced
        self._qt_seen = False  # a QApplication exists (set by the watchdog or want_qt)
        self._stop = threading.Event()
        self._main_ident = threading.main_thread().ident
        self.stats = {
            "signal_ticks": 0,
            "loop_ticks": 0,
            "max_gap_s": 0.0,
            "calls_run": 0,
            "deferred_interrupts": 0,
        }
        self.last_error = None
        signal.signal(_SIGNAL, self._on_signal)
        signal.siginterrupt(_SIGNAL, False)
        self._watchdog = threading.Thread(
            target=self._watch, name="eco-mainthread-heartbeat", daemon=True
        )
        self._watchdog.start()

    # -- watchdog thread -------------------------------------------------------
    # No print/logging/import/locks in here: see the module docstring.

    def _watch(self):
        next_qt_check = 0.0
        while not self._stop.wait(self.interval):
            now = time.monotonic()
            if not self._qt_seen and now >= next_qt_check:
                next_qt_check = now + QT_CHECK_INTERVAL
                if _qt_application() is not None:
                    self._qt_seen = True
            if self._hold or not self._may_interrupt():
                continue
            if self._qt_seen and self._loop_alive(now):
                continue  # Qt's own loop is running and ticks by itself
            due = (
                self._qt_seen
                or self._calls
                or any(service.next_due <= now for service in self._services.values())
            )
            if due:
                try:
                    signal.pthread_kill(self._main_ident, _SIGNAL)
                except Exception:
                    return  # main thread gone: interpreter shutting down

    def _loop_alive(self, now):
        return (
            self._qt is not None
            and self._timer_running
            and now - self._last_loop_tick <= self.stall
        )

    def _may_interrupt(self):
        """Whether the main thread may be interrupted for a tick right now.
        With scope="cells" only while a watched IPython shell runs a cell,
        or when there is no Qt at all: anywhere else the main thread may be
        inside some Qt handler -- a window's closeEvent tearing it down, a
        widget being built -- and pumping events from in there re-enters Qt
        mid-way (seen as a segfault: an event delivered to a window inside
        its own closeEvent). Outside cells Qt's own loop runs anyway."""
        return self.scope == "always" or self._cells > 0 or not self._qt_seen

    # -- main thread -----------------------------------------------------------

    _timer_running = False

    def _on_signal(self, signum, frame):
        if self._may_interrupt():
            self._tick(from_signal=True)

    def watch_shell(self, shell):
        """Count cells executed by this IPython shell (see _may_interrupt).
        Idempotent per shell."""
        if any(watched is shell for watched in self._watched_shells):
            return
        self._watched_shells.append(shell)
        shell.events.register("pre_run_cell", self._on_pre_run_cell)
        shell.events.register("post_run_cell", self._on_post_run_cell)

    def _on_pre_run_cell(self, *args):
        self._cells += 1
        self._last_loop_tick = time.monotonic()  # a cell starts: a full stall of grace
        self._ensure_qt()
        self._update_timer()

    def _on_post_run_cell(self, *args):
        self._cells = max(0, self._cells - 1)
        self._update_timer()

    def _ensure_qt(self):
        """Create the Qt driver (main thread) once a QApplication exists."""
        if self._qt is not None:
            return
        app = _qt_application()
        if app is None:
            return
        try:
            driver = _make_qt_driver(app)
            driver.timer.setInterval(max(1, int(self.interval * 1000)))
            driver.timer.timeout.connect(self._on_qt_timer)
            from qtpy import QtCore

            driver.wake.connect(self._on_wake, QtCore.Qt.QueuedConnection)
        except Exception:
            self.last_error = ("qt", traceback.format_exc())
            return
        self._qt = driver
        self._qt_seen = True
        self._timer_running = False
        self._last_tick = self._last_loop_tick = time.monotonic()

    def _update_timer(self):
        """Run the loop-driven timer only while something needs it: a cell
        is executing (liveness for the watchdog), services exist, or
        scope="always". An idle session costs nothing in Qt's loop."""
        if self._qt is None:
            return
        wanted = self.scope == "always" or self._cells > 0 or bool(self._services)
        if wanted == self._timer_running:
            return
        try:
            if wanted:
                self._last_loop_tick = time.monotonic()
                self._qt.timer.start()
            else:
                self._qt.timer.stop()
        except RuntimeError:  # the QApplication (and with it the driver) is gone
            self._qt, self._qt_seen = None, False
            wanted = False
        self._timer_running = wanted

    def _on_qt_timer(self):
        if self._in_tick:
            return  # fired by our own processEvents(): not a sign the loop is alive
        self._last_loop_tick = time.monotonic()
        self._tick(from_signal=False)

    def _on_wake(self):
        self._update_timer()
        if not self._in_tick:
            self._tick(from_signal=False)

    def _tick(self, from_signal):
        if self._in_tick or self._hold:
            return
        self._in_tick = True
        interrupted = []
        previous_sigint = None
        if from_signal:
            # Ctrl-C landing while we pump must end up in the interrupted
            # user code, not inside a Qt slot where it would be swallowed.
            try:
                previous_sigint = signal.signal(
                    signal.SIGINT, lambda signum, frame: interrupted.append(signum)
                )
            except Exception:
                previous_sigint = None
        try:
            now = time.monotonic()
            # time the main thread went unserviced: since the last tick of any
            # kind, a loop tick, or the start of the current cell
            gap = now - max(self._last_tick, self._last_loop_tick)
            if from_signal:
                self.stats["signal_ticks"] += 1
                if gap > self.stats["max_gap_s"]:
                    self.stats["max_gap_s"] = gap
            else:
                self.stats["loop_ticks"] += 1
            if self._qt is None and self._qt_seen:
                self._ensure_qt()
                self._update_timer()
            if from_signal and self._qt is not None:
                # a loop-driven tick comes from inside Qt's own loop already
                self._pump_qt()
            self._run_services(now)
            self._run_calls(interrupted)
        finally:
            if previous_sigint is not None:
                try:
                    signal.signal(signal.SIGINT, previous_sigint)
                except Exception:
                    pass
            self._last_tick = time.monotonic()
            self._in_tick = False
        if interrupted and from_signal:
            self.stats["deferred_interrupts"] += 1
            raise KeyboardInterrupt

    def _pump_qt(self):
        app = _qt_application()
        if app is None:
            self._qt, self._qt_seen = None, False  # the application went away
            return
        try:
            app.processEvents()
        except Exception:
            self.last_error = ("qt", traceback.format_exc())

    def _run_services(self, now):
        for name, service in self._services.items():
            if now < service.next_due:
                continue
            service.next_due = now + service.interval
            try:
                service.func()
                service.errors = 0
            except Exception:
                service.errors += 1
                self.last_error = (name, traceback.format_exc())
                if service.errors >= SERVICE_ERRORS_MAX:
                    self.remove_service(name)

    def _run_calls(self, interrupted):
        for _ in range(len(self._calls)):
            try:
                future, func, args, kwargs = self._calls.popleft()
            except IndexError:
                return
            if not future.set_running_or_notify_cancel():
                continue
            self.stats["calls_run"] += 1
            try:
                future.set_result(func(*args, **kwargs))
            except KeyboardInterrupt as exc:
                future.set_exception(exc)
                interrupted.append(signal.SIGINT)
            except BaseException as exc:
                future.set_exception(exc)

    # -- registry, any thread ----------------------------------------------------

    def _wake_main_loop(self):
        """Have the main thread's Qt loop run a tick (and update the timer)
        soon. Emitting a signal is thread-safe; the slot is queued."""
        driver = self._qt
        if driver is None:
            return
        try:
            driver.wake.emit()
        except RuntimeError:
            pass

    def add_service(self, name, func, interval=None):
        services = dict(self._services)
        services[name] = _Service(func, self.interval if interval is None else interval)
        self._services = services
        self._wake_main_loop()

    def remove_service(self, name):
        if name in self._services:
            services = dict(self._services)
            services.pop(name, None)
            self._services = services
            if not self._in_tick:
                self._wake_main_loop()

    def call_soon(self, func, *args, **kwargs):
        future = Future()
        self._calls.append((future, func, args, kwargs))
        self._wake_main_loop()
        return future

    def want_qt(self):
        """Service the running QApplication from now on. Call on the main
        thread (the driver has to live there); elsewhere this only tells the
        watchdog to look."""
        self._qt_seen = self._qt_seen or _qt_application() is not None
        if _on_main_thread():
            self._ensure_qt()
            self._update_timer()

    def stop(self):
        self._stop.set()
        self._watchdog.join(timeout=1)
        for shell in self._watched_shells:
            for event, callback in (
                ("pre_run_cell", self._on_pre_run_cell),
                ("post_run_cell", self._on_post_run_cell),
            ):
                try:
                    shell.events.unregister(event, callback)
                except Exception:
                    pass
        self._watched_shells = []
        if self._qt is not None:
            try:
                self._qt.timer.stop()
                self._qt.deleteLater()
            except Exception:
                pass
            self._qt = None
        if _on_main_thread():
            signal.signal(_SIGNAL, signal.SIG_DFL)

    def status(self):
        return {
            "scope": self.scope,
            "shells_watched": len(self._watched_shells),
            "in_cell": self._cells > 0,
            "interval_s": self.interval,
            "stall_s": self.stall,
            "qt": self._qt is not None,
            "timer_running": self._timer_running,
            "services": sorted(self._services),
            "pending_calls": len(self._calls),
            "held": bool(self._hold),
            **self.stats,
            "last_error": self.last_error[0] if self.last_error else None,
        }


# -- module-level API ----------------------------------------------------------


def install(interval=INTERVAL, stall=STALL, scope="cells"):
    """Install the heartbeat (idempotent) and return it, or None when it is
    disabled (``ECO_NO_HEARTBEAT=1``), not called from the main thread, or
    ``SIGUSR2`` is already taken by someone else.

    scope="cells" (default): only interrupt the main thread while a watched
    IPython shell executes a cell -- this process's own shell, if any, is
    watched right away; see :func:`watch_shell` for others (the desktop's
    in-process kernel). Outside cells the main thread may be inside any Qt
    handler, and Qt's own loop runs anyway. scope="always": whenever the
    main thread stalls -- for plain scripts and tests, at the risk described
    in ``Heartbeat._may_interrupt``."""
    global _heartbeat
    if _heartbeat is None:
        if os.environ.get("ECO_NO_HEARTBEAT", "").strip() not in ("", "0"):
            return None
        if not _on_main_thread():
            return None
        try:
            _heartbeat = Heartbeat(interval=interval, stall=stall, scope=scope)
        except Exception as exc:
            print(f"eco.utilities.mainthread: heartbeat not installed: {exc}")
            return None
    _watch_own_shell(_heartbeat)
    return _heartbeat


def _watch_own_shell(heartbeat):
    ipython = sys.modules.get("IPython")  # not imported: no shell to watch (and no 1 s import)
    try:
        shell = ipython.get_ipython() if ipython is not None else None
    except Exception:
        shell = None
    if shell is not None:
        heartbeat.watch_shell(shell)


def watch_shell(shell):
    """Let cells executed by ``shell`` (an IPython InteractiveShell in this
    process, e.g. an in-process kernel's) be kept live by the heartbeat."""
    heartbeat = install()
    if heartbeat is not None:
        heartbeat.watch_shell(shell)


def install_qt():
    """:func:`install`, and service the running QApplication from now on.
    eco's own Qt windows call this when they open; Qt applications created
    by anything else are noticed by the watchdog within ~0.5 s."""
    heartbeat = install()
    if heartbeat is not None:
        heartbeat.want_qt()
    return heartbeat


def uninstall():
    global _heartbeat
    if _heartbeat is not None:
        _heartbeat.stop()
        _heartbeat = None


def get():
    """The installed Heartbeat, or None."""
    return _heartbeat


def status():
    return {"installed": False} if _heartbeat is None else {"installed": True, **_heartbeat.status()}


def add_service(name, func, interval=None):
    """Run ``func()`` on the main thread every ``interval`` s (default: every
    tick), whether the main thread is idle in its loop or busy in user code.
    ``func`` must be short, non-blocking and touch only its own state. A
    service raising three times in a row is removed (see ``status()``)."""
    heartbeat = install()
    if heartbeat is None:
        raise RuntimeError("the main-thread heartbeat is not installed")
    heartbeat.add_service(name, func, interval=interval)


def remove_service(name):
    if _heartbeat is not None:
        _heartbeat.remove_service(name)


def call_soon(func, *args, **kwargs):
    """Run ``func(*args, **kwargs)`` on the main thread at its next tick and
    return a ``concurrent.futures.Future`` for the result. Callable from any
    thread. Runs within ~0.1 s whether the main thread is idle or busy; like a
    service, ``func`` runs inside whatever the main thread is doing."""
    if _heartbeat is None:
        raise RuntimeError("the main-thread heartbeat is not installed")
    return _heartbeat.call_soon(func, *args, **kwargs)


def run_on_main(func, *args, timeout=None, **kwargs):
    """``call_soon(...)`` and wait for the result; on the main thread itself
    just calls ``func`` directly."""
    if _on_main_thread():
        return func(*args, **kwargs)
    return call_soon(func, *args, **kwargs).result(timeout=timeout)


def on_main(func):
    """Decorator for GUI code (creating/updating figures, Qt widgets): called
    from another thread -- a scan running on a queue's worker thread -- it
    runs on the main thread via :func:`call_soon` and the caller waits for
    it; on the main thread, or without a heartbeat, it is a plain call."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if _on_main_thread() or _heartbeat is None:
            return func(*args, **kwargs)
        return _heartbeat.call_soon(func, *args, **kwargs).result()

    return wrapper


@contextlib.contextmanager
def hold():
    """Suspend ticks for a block of main-thread code that must not be
    interleaved with GUI events or services. Nests."""
    heartbeat = _heartbeat
    if heartbeat is None:
        yield
        return
    heartbeat._hold += 1
    try:
        yield
    finally:
        heartbeat._hold -= 1


def join(thread, timeout=None):
    """``thread.join(timeout)``, in slices when called untimed on the main
    thread, so the heartbeat (and Ctrl-C) get to run while waiting."""
    if timeout is None and _on_main_thread():
        while thread.is_alive():
            thread.join(timeout=SLICE)
    thread.join(timeout=timeout)


def wait_event(event, timeout=None):
    """``event.wait(timeout)``, in slices when called untimed on the main
    thread, for the same reason as :func:`join`."""
    if timeout is None and _on_main_thread():
        while not event.wait(SLICE):
            pass
        return True
    return event.wait(timeout)
