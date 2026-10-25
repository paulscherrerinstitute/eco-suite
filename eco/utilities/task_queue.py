"""Named queues of commands that run one after another on a worker thread,
while the interactive session stays free.

    %queue scans.ascan(mot, 0, 1, 50, 0.1)      # append to the "default" queue
    %queue edge = float(input("edge position? "))
    %queue scans.ascan(mot, edge - .05, edge + .05, 100, 0.1)
    %queue                                       # status of the default queue
    %queue pause_after_running_task
    %queue repeat_last 3
    %queue -q align scans.dscan(gon.x, -1, 1, 20, 0.1)   # another, independent queue
    %%queue                                      # a whole cell as one task

or the same from Python: ``q = queues["default"]``, ``q.append(...)``,
``q.test_run()``, ``q.pause_after_running_task()``, ...

What a task is
--------------
A statement (a string, as typed after ``%queue``) or a callable with its
arguments. Statements run in the session's own namespace and are evaluated
*when they run*: a variable changed at the prompt in the meantime is seen by
the queue, and a variable a task sets (``edge = ...`` above) is seen by later
tasks and at the prompt.

Checked before it runs
----------------------
Every appended task is test-run right away (``test=False`` skips that), and
:meth:`TaskQueue.test_run` checks everything still pending -- without
moving anything:

- the statement compiles, and every name it uses exists (in the namespace,
  or assigned by an earlier task) -- with a "did you mean" for typos;
- attribute chains (``mono.energy``) resolve; properties are not evaluated;
- calls whose arguments are plain expressions are checked against the
  callable's signature; a ``Scans`` method (``scans.ascan(...)``) is built
  without running (``start_immediately=False``) and every planned position is
  checked against the adjustables' limits; ``mv``/``set_target_value``/
  ``umv`` targets are checked against limits too.

Running unattended
------------------
The worker thread runs with :func:`eco.utilities.user_input.unattended`:
a plain ``input()`` question in a task is shown on the main thread and the
task waits for the answer; a timed question (``input_timeout``) takes its
default at once unless asked with ``escalate=True``. GUI work from a task
(live plots of a scan) is handed to the main thread through
eco.utilities.mainthread. :func:`current_task` tells eco code whether it runs
inside a queue.

When a task fails, the queue pauses (``on_error="pause"``, the default) so
the remaining tasks don't run on top of a failed step; ``resume()`` goes on.
"""

import ast
import builtins
import contextlib
import ctypes
import difflib
import inspect
import itertools
import logging
import shlex
import threading
import time
import traceback

from eco.utilities import user_input

logger = logging.getLogger(__name__)
_local = threading.local()

OUTPUT_KEEP = 2_000_000  # characters of output kept per task


def current_task():
    """The Task the calling thread is running for a queue, or None."""
    return getattr(_local, "task", None)


def in_queue():
    return current_task() is not None


def test_building():
    """True while the test run builds a scan only to check it (eco code then
    skips what only a real scan should do, e.g. replacing
    ``scans.acquiring_scan``)."""
    return getattr(_local, "test_building", False)


class QueueTestError(Exception):
    """A task failed its test run; ``.problems`` lists why."""

    def __init__(self, task, problems):
        self.task = task
        self.problems = problems
        super().__init__(f"{task.label}: " + "; ".join(problems))


# -- tasks ------------------------------------------------------------------------


class Task:
    """One queued command. Returned by :meth:`TaskQueue.append`; not built
    directly."""

    _ids = itertools.count(1)

    def __init__(self, command, args=(), kwargs=None, description=""):
        self.id = next(Task._ids)
        self.command = command  # a statement (str) or a callable
        self.args = tuple(args)
        self.kwargs = dict(kwargs or {})
        self._description = description
        self.state = "pending"  # pending -> running -> done | error | cancelled
        self.result = None
        self.exception = None
        self.traceback = None
        self.started = None
        self.finished = None
        self.meta = {}  # free-form, editable while the task runs
        self.scans = []  # StepScans started by this task (see register_scan)
        self.queue = None
        self._done = threading.Event()
        self._output = []  # what the task printed (see output)
        self._output_size = 0

    @property
    def source(self):
        """The command as text."""
        if isinstance(self.command, str):
            return self.command.strip()
        name = getattr(self.command, "__qualname__", None) or repr(self.command)
        parts = [repr(a) for a in self.args] + [f"{k}={v!r}" for k, v in self.kwargs.items()]
        return f"{name}({', '.join(parts)})"

    @property
    def output(self):
        """Everything this task (and the threads it started) printed, as one
        string -- kept also when the queue is muted. The oldest part is
        dropped beyond OUTPUT_KEEP characters."""
        return "".join(self._output)

    def _record(self, text):
        self._output.append(text)
        self._output_size += len(text)
        while self._output_size > OUTPUT_KEEP and len(self._output) > 1:
            self._output_size -= len(self._output.pop(0))
        if self.queue is not None:
            self.queue._emit(text)

    @property
    def scan(self):
        """The StepScan this task is running (its most recent one), or None."""
        return self.scans[-1] if self.scans else None

    @property
    def label(self):
        queue = f"queue {self.queue.name!r}, " if self.queue is not None else ""
        return f"{queue}task #{self.id}"

    @property
    def description(self):
        return self._description

    @description.setter
    def description(self, text):
        """Editable while the task runs: also updates the description of the
        scans it is running (StepScan.set_description)."""
        self._description = text
        for scan in self.scans:
            try:
                scan.set_description(text)
            except Exception:
                pass

    def wait(self, timeout=None):
        """Block until the task has finished; returns its result, re-raises
        its exception."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._done.wait(0.05):  # sliced: see mainthread.wait_event
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"{self.label} still {self.state}")
        if self.exception is not None:
            raise self.exception
        return self.result

    def copy(self):
        return Task(self.command, self.args, self.kwargs, self._description)

    def __repr__(self):
        return f"<Task #{self.id} {self.state} {self.source[:60]!r}>"


# -- the queue ---------------------------------------------------------------------


class TaskQueue:
    """A named queue with its own worker thread (started on first use). Get
    one with ``queues[name]``; see the module docstring."""

    def __init__(self, name="default", namespace=None, on_error="pause"):
        self.name = name
        self._namespace = namespace
        self.on_error = on_error
        self._pending = []
        self.history = []
        self.current = None
        self._paused = False
        self._lock = threading.Lock()  # guards _pending/current/history; never held while running
        self._wake = threading.Condition(self._lock)
        self._worker = None
        self._closing = False
        self.quiet = False  # see mute()
        self._listeners = []  # callables(text), see _emit
        self._record_file = None

    # -- the namespace statements run in ----------------------------------------

    @property
    def namespace(self):
        """The dict statements run in: the session's own (shared with the
        prompt), unless one was given to the queue."""
        if self._namespace is not None:
            return self._namespace
        try:
            from IPython import get_ipython

            shell = get_ipython()
        except Exception:
            shell = None
        if shell is not None:
            return shell.user_ns
        import __main__

        return __main__.__dict__

    # -- adding and editing --------------------------------------------------------

    def append(self, command, *args, test=True, task_description=None, **kwargs):
        """Append a task: a statement (str), or a callable with the
        arguments to call it with (``*args``/``**kwargs`` go to the
        callable). It is test-run first (see :meth:`test`); a task that
        fails is not appended and QueueTestError tells why -- ``test=False``
        appends it anyway. ``task_description`` labels the task (default: the
        callable's own ``description`` argument, if it has one -- a scan's).
        Returns the Task."""
        return self.insert(
            len(self._pending), command, *args, test=test, task_description=task_description, **kwargs
        )

    def insert(self, index, command, *args, test=True, task_description=None, **kwargs):
        """Like :meth:`append`, at position ``index`` among the pending
        tasks (0 = next to run)."""
        if isinstance(command, Task):
            task = command
        else:
            description = task_description if task_description is not None else kwargs.get("description", "")
            task = Task(command, args, kwargs, description or "")
        task.queue = self
        if test:
            with self._lock:
                before = self._pending[: max(0, index)]
            problems = self._test(task, before)
            if problems:
                raise QueueTestError(task, problems)
        with self._wake:
            self._pending.insert(index, task)
            self._wake.notify_all()
        self._ensure_worker()
        return task

    def remove(self, task):
        """Drop a pending task (a Task or its id). Returns it, or None if it
        isn't pending (any more)."""
        with self._lock:
            found = self._find(task)
            if found is None:
                return None
            self._pending.remove(found)
        found.state = "cancelled"
        found._done.set()
        return found

    def move(self, task, index):
        """Move a pending task to position ``index`` (0 = next to run)."""
        with self._lock:
            found = self._find(task)
            if found is None:
                raise ValueError(f"{task!r} is not pending in queue {self.name!r}")
            self._pending.remove(found)
            self._pending.insert(index, found)
        return found

    def repeat_last(self, N=1, test=True):
        """Append the last task (the most recently appended one, pending or
        already run) ``N`` more times. Returns the new tasks."""
        with self._lock:
            last = self._pending[-1] if self._pending else (
                self.current if self.current is not None else (self.history[-1] if self.history else None)
            )
        if last is None:
            raise ValueError(f"queue {self.name!r} has no task to repeat")
        return [self.append(last.copy(), test=test) for _ in range(N)]

    def repeat(self, task, N=1, test=True):
        """Append a copy of ``task`` (a Task or its id, pending or run) N times."""
        found = task if isinstance(task, Task) else self._find(task, everywhere=True)
        if found is None:
            raise ValueError(f"no task {task!r} in queue {self.name!r}")
        return [self.append(found.copy(), test=test) for _ in range(N)]

    def clear(self):
        """Drop every pending task (the running one continues)."""
        with self._lock:
            dropped, self._pending = self._pending, []
        for task in dropped:
            task.state = "cancelled"
            task._done.set()
        return dropped

    def cancel_after_running_task(self):
        """Let the running task finish, then stop: every pending task is
        dropped. Same as :meth:`clear`."""
        return self.clear()

    # -- flow control --------------------------------------------------------------

    def pause(self):
        """Don't start any further task (the running one continues)."""
        with self._lock:
            self._paused = True

    def pause_after_running_task(self):
        """Let the running task finish, then hold the rest until
        :meth:`resume`. Same as :meth:`pause`."""
        self.pause()

    def resume(self):
        """Start running pending tasks again."""
        with self._wake:
            self._paused = False
            self._wake.notify_all()
        self._ensure_worker()

    @property
    def paused(self):
        return self._paused

    def pause_running_task(self):
        """Pause the scan the running task is in, at its next step boundary
        (StepScan.pause) -- mid-scan, unlike :meth:`pause`. Returns the task,
        or None if nothing scan-like is running."""
        task = self.current
        scan = task.scan if task is not None else None
        if scan is None:
            return None
        scan.pause()
        return task

    def resume_running_task(self):
        """Resume a scan paused by :meth:`pause_running_task`."""
        task = self.current
        scan = task.scan if task is not None else None
        if scan is None:
            return None
        scan.resume()
        return task

    def save_paused(self, task, path):
        """Save the remaining steps of the running task's paused scan to
        `path` (json), to resume later with :meth:`resume_from_file` --
        possibly in another session."""
        from eco.acquisition.scan_tasks import save_paused_scan

        task = task if isinstance(task, Task) else self._find(task, everywhere=True)
        if task is None or task.scan is None:
            raise ValueError("no scan to save in that task (not started yet?)")
        return save_paused_scan(task.scan, path, description=task.description)

    def resume_from_file(self, path, root, counters, scans_kwargs=None, test=True):
        """Append a task finishing a scan saved by :meth:`save_paused`.
        Adjustables are re-resolved by name from `root` (e.g. the beamline
        namespace); `counters` are given explicitly."""
        from eco.acquisition.scan_tasks import resume_scan_call

        call = resume_scan_call(path, root, counters, scans_kwargs)
        return self.append(call, test=test, task_description=call.description)

    def release(self, adjustable):
        """Free an adjustable the running task has claimed, so it can be
        moved from elsewhere again (for the rest of the task -- the task
        re-claims it if it moves it again). See :func:`check_claim`."""
        from eco.elements.protocols import resolve_lazy

        with _claims_lock:
            return _claims.pop(id(resolve_lazy(adjustable)), None) is not None

    # -- output ------------------------------------------------------------------

    def mute(self):
        """Stop printing this queue's output at the prompt (a chatty scan
        then doesn't scroll through what you type). It is still kept per task
        (``task.output``), recorded (:meth:`record`) and shown live
        (:meth:`show_output`)."""
        self.quiet = True

    def unmute(self):
        """Print this queue's output at the prompt again."""
        self.quiet = False

    def record(self, path=None):
        """Append everything the queue prints (with a header per task) to a
        file, from now on -- e.g. to follow it with ``tail -f`` in another
        terminal, or to look back later. Default path:
        ~/.eco/queue_logs/<date>_<queue>.log. Returns the path."""
        from pathlib import Path

        if path is None:
            path = Path.home() / ".eco" / "queue_logs" / f"{time.strftime('%Y%m%d')}_{self.name}.log"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_recording()
        self._record_file = open(path, "a", buffering=1)
        self._listeners.append(self._write_record)
        self._record_file.write(f"=== recording queue {self.name!r} since {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        print(f"recording queue {self.name!r} to {path}  (follow it: tail -f {path})")
        return path

    def stop_recording(self):
        """Stop :meth:`record`."""
        if self._write_record in self._listeners:
            self._listeners.remove(self._write_record)
        if self._record_file is not None:
            self._record_file.close()
            self._record_file = None

    def _write_record(self, text):
        file = self._record_file
        if file is not None:
            try:
                file.write(text)
            except Exception:
                pass

    def show_output(self, dock=False):
        """A window following this queue's output live -- what the tasks
        printed so far, then everything new. It stays live while the session
        is busy (see eco.utilities.mainthread). dock=True docks it into the
        eco desktop, if one is open."""
        from eco.utilities import mainthread

        def build():
            from eco.widgets.queue_output_qt import QueueOutputQt

            window = QueueOutputQt(self)
            if dock:
                try:
                    from eco.widgets.desktop_app import _live_desktop_apps

                    live = [a for a in _live_desktop_apps if a.window is not None]
                    if live:
                        live[-1]._dock_widget_object(f"queue {self.name}", window)
                except Exception:
                    pass
            return window

        return mainthread.run_on_main(build)

    def _emit(self, text):
        for listener in list(self._listeners):
            try:
                listener(text)
            except Exception:
                pass

    def stop_running_task(self):
        """Ask the running task to stop: its scans stop at their next step
        (StepScan.request_stop); anything else gets a KeyboardInterrupt raised
        in the worker thread at its next Python instruction (a motor already
        moving keeps moving, as after Ctrl-C). The queue pauses."""
        task = self.current
        if task is None:
            return None
        self.pause()
        if task.scans:
            for scan in task.scans:
                try:
                    scan.request_stop()
                except Exception:
                    pass
        elif self._worker is not None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(self._worker.ident), ctypes.py_object(KeyboardInterrupt)
            )
        return task

    def stop(self):
        """Stop the running task and drop everything pending."""
        dropped = self.clear()
        self.stop_running_task()
        return dropped

    def wait(self, timeout=None):
        """Block until nothing is running or pending (or the queue is paused
        with tasks left)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                idle = self.current is None and (not self._pending or self._paused)
            if idle:
                return
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"queue {self.name!r} still busy")
            time.sleep(0.05)

    # -- checking --------------------------------------------------------------------

    def test(self, task):
        """Test-run one task (a Task, its id, or a new command) as if it ran
        after the current pending ones: returns the list of problems found
        (empty = fine)."""
        if not isinstance(task, Task):
            found = self._find(task, everywhere=True) if isinstance(task, int) else None
            task = found or Task(task)
        with self._lock:
            before = [t for t in self._pending if t is not task]
            if task in self._pending:
                before = self._pending[: self._pending.index(task)]
        return self._test(task, before)

    def test_run(self, verbose=True):
        """Test-run every pending task in order, without running anything
        (see the module docstring for what is checked). Prints a report and
        returns {task: problems} for the tasks with problems."""
        with self._lock:
            pending = list(self._pending)
        report = {}
        for i, task in enumerate(pending):
            problems = self._test(task, pending[:i])
            if problems:
                report[task] = problems
            if verbose:
                status = "ok" if not problems else "PROBLEM: " + "; ".join(problems)
                print(f"  #{task.id:<4} {task.source[:70]:70s} {status}")
        if verbose:
            print(f"queue {self.name!r}: {len(pending)} pending, {len(report)} with problems")
        return report

    def _test(self, task, before):
        defined = set()
        for earlier in before:
            if isinstance(earlier.command, str):
                defined |= _assigned_names(earlier.command)
        try:
            return check_task(task, self.namespace, defined)
        except Exception as exc:  # a broken check must not block queueing
            return [f"test failed itself: {type(exc).__name__}: {exc}"]

    # -- running ---------------------------------------------------------------------

    def _ensure_worker(self):
        _install_output_router()
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, name=f"eco-queue-{self.name}", daemon=True)
            self._worker.start()

    def _run(self):
        while True:
            with self._wake:
                while (self._paused or not self._pending) and not self._closing:
                    self._wake.wait(timeout=0.5)
                if self._closing:
                    return
                task = self._pending.pop(0)
                self.current = task
            self._execute(task)
            with self._lock:
                self.current = None
                self.history.append(task)
                if task.state == "error" and self.on_error == "pause":
                    self._paused = True

    def _execute(self, task):
        task.state = "running"
        task.started = time.time()
        self._emit(f"\n=== {task.label} started {time.strftime('%H:%M:%S')}: {task.source}\n")
        _local.task = task
        try:
            with user_input.unattended(task.label):
                if isinstance(task.command, str):
                    task.result = _run_statement(task.command, self.namespace, f"<{task.label}>")
                else:
                    task.result = task.command(*task.args, **task.kwargs)
            task.state = "done"
        except BaseException as exc:  # KeyboardInterrupt from stop_running_task too
            task.exception = exc
            task.traceback = traceback.format_exc()
            task.state = "error"
            message = (
                f"\n[{task.label} failed: {type(exc).__name__}: {exc} -- "
                f"queue {'paused' if self.on_error == 'pause' else 'continues'}]\n"
            )
            task._record(message)
            _print_to_session(message)  # shown even when the queue is muted
        finally:
            _local.task = None
            _release_task(task)
            task.finished = time.time()
            self._emit(f"=== {task.label} {task.state} after {task.finished - task.started:.1f} s\n")
            task._done.set()

    # -- inspection ------------------------------------------------------------------

    @property
    def pending(self):
        with self._lock:
            return list(self._pending)

    @property
    def last(self):
        return self.history[-1] if self.history else None

    def status(self):
        """Print the queue: running task, pending tasks, the last few done."""
        print(self._format())

    def _format(self, n_history=5):
        def row(task):
            took = ""
            if task.started:
                took = f"{(task.finished or time.time()) - task.started:7.1f} s"
            desc = f"  ({task.description})" if task.description else ""
            return f"  #{task.id:<4} {task.state:9s} {took:>9s}  {task.source[:70]}{desc}"

        with self._lock:
            pending, current, history = list(self._pending), self.current, self.history[-n_history:]
        lines = [f"queue {self.name!r}" + (" -- PAUSED" if self._paused else "")]
        lines += [row(t) for t in history]
        if current is not None:
            lines.append(row(current))
        lines += [row(t) for t in pending]
        if len(lines) == 1:
            lines.append("  (empty)")
        return "\n".join(lines)

    def __repr__(self):
        return self._format()

    def _find(self, task, everywhere=False):
        pool = list(self._pending)
        if everywhere:
            pool += self.history + ([self.current] if self.current else [])
        for candidate in pool:
            if candidate is task or candidate.id == task:
                return candidate
        return None

    def close(self):
        with self._wake:
            self._closing = True
            self._wake.notify_all()


def _run_statement(source, namespace, filename):
    """exec a statement in `namespace`; a lone expression returns its value
    (like the prompt)."""
    tree = ast.parse(source, filename=filename, mode="exec")
    if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
        return eval(compile(ast.Expression(tree.body[0].value), filename, "eval"), namespace)
    exec(compile(tree, filename, "exec"), namespace)
    return None


class _Queues(dict):
    """All queues of this session by name; a missing name creates one."""

    def __missing__(self, name):
        queue = self[name] = TaskQueue(name)
        return queue

    def __repr__(self):
        return "\n".join(q._format(n_history=1) for q in self.values()) or "(no queues yet)"


queues = _Queues()


_output = {"stdout": None, "stderr": None}


class _QueueOutput:
    """Stands in for sys.stdout/sys.stderr once :func:`send_output_to` was
    called: writes from a queue's worker thread go to the configured stream,
    everything else to the stream it replaced."""

    def __init__(self, name, fallback):
        self._name = name
        self._fallback = fallback

    def _stream(self):
        target = _output[self._name]
        if target is not None and current_task() is not None:
            return target
        return self._fallback

    def write(self, text):
        task = current_task()
        if task is None:
            return self._fallback.write(text)
        task._record(text)
        if task.queue is not None and task.queue.quiet:
            return len(text)
        return self._stream().write(text)

    def flush(self):
        return self._stream().flush()

    def __getattr__(self, name):
        return getattr(self._stream(), name)


def _print_to_session(text):
    """Write where the session's output goes (the desktop console, or the
    terminal), bypassing a queue's muting and recording."""
    import sys

    out = sys.stdout
    if isinstance(out, _QueueOutput):
        out = _output["stdout"] or out._fallback
    try:
        out.write(text)
        out.flush()
    except Exception:
        pass


def _install_output_router():
    """Put _QueueOutput in front of sys.stdout/sys.stderr (idempotent):
    writes from other threads pass straight through, a queue task's are also
    recorded (Task.output, TaskQueue.record/show_output) and muted if asked."""
    import sys

    for name in ("stdout", "stderr"):
        current = getattr(sys, name)
        if current is not None and not isinstance(current, _QueueOutput):
            setattr(sys, name, _QueueOutput(name, current))


def send_output_to(stdout, stderr=None):
    """Print what queued tasks print to these streams -- the eco desktop's
    console (its in-process kernel's own streams). Without this, an
    in-process kernel only shows output written while one of *its* cells
    runs; a task's output in between went to the terminal the desktop was
    started from. A terminal session doesn't need this (prompt_toolkit
    already prints other threads' output above the prompt)."""
    import sys

    _output["stdout"], _output["stderr"] = stdout, stderr or stdout
    _install_output_router()


def register_scan(scan):
    """Called by StepScan when it starts: ties it to the queue task running
    it (if any), so the task's description and stop requests reach it, and
    claims the scan's adjustables for the task (see :func:`check_claim`)."""
    task = current_task()
    if task is not None:
        task.scans.append(scan)
        from eco.elements.protocols import resolve_lazy

        for adjustable in getattr(scan, "adjustables", []):
            try:
                _claim(resolve_lazy(adjustable), task)
            except Exception:
                pass


# -- claims: what a running task moves can't be moved from elsewhere ---------------
#
# Checked in the one place every write goes through (Changer.__init__, see
# eco.devices_general.utilities._claim_gate, next to the access gate). A task
# claims the adjustables of the scans it runs as they start, and anything else
# the first time it moves it -- which also covers the real motors underneath a
# virtual adjustable, since their Changers run in threads that carry the task
# (see thread_context). Claims end with the task. stop() is not a write and is
# never blocked.

_claims = {}  # id(adjustable) -> (adjustable, task)
_claims_lock = threading.Lock()  # never taken in a heartbeat tick


class MoveBlocked(PermissionError):
    """A write to an adjustable that a running queue task is using. Raised
    before anything moves; the message says how to move it anyway."""


@contextlib.contextmanager
def forced():
    """Move adjustables a running queue task is using, from this thread, for
    the duration of the block -- e.g. to fix a stuck move while the task waits
    for it::

        with forced():
            mot.mv(0.5)

    Each forced move is logged as a warning."""
    previous = getattr(_local, "forced", False)
    _local.forced = True
    try:
        yield
    finally:
        _local.forced = previous


def thread_context():
    """What a thread spawned from here inherits (the running task, forcing):
    captured by eco.utilities.utilities.PropagatingThread, which every eco
    Changer/Acquisition runs in."""
    return current_task(), getattr(_local, "forced", False)


def set_thread_context(context):
    _local.task, _local.forced = context


def _claim(adjustable, task):
    with _claims_lock:
        entry = _claims.get(id(adjustable))
        if entry is None or entry[1] is not task and entry[1].state != "running":
            _claims[id(adjustable)] = (adjustable, task)


def _release_task(task):
    with _claims_lock:
        for key in [k for k, (_, owner) in _claims.items() if owner is task]:
            del _claims[key]


def _display_name(adjustable):
    try:
        return adjustable.alias.get_full_name()
    except Exception:
        return getattr(adjustable, "name", None) or repr(adjustable)


def check_claim(adjustable):
    """The write gate for claims, called for every Changer: a write from the
    task that holds the claim (or one of its threads) goes through and claims
    an unclaimed adjustable; a write from anywhere else to a claimed one
    raises :class:`MoveBlocked` -- unless inside :func:`forced`."""
    task = current_task()
    with _claims_lock:
        entry = _claims.get(id(adjustable))
        if entry is not None and entry[1].state != "running":
            del _claims[id(adjustable)]  # the owner finished without releasing
            entry = None
        if entry is None:
            if task is not None:
                _claims[id(adjustable)] = (adjustable, task)
            return
        owner = entry[1]
        if owner is task:
            return
    name = _display_name(adjustable)
    if getattr(_local, "forced", False):
        logger.warning(f"forced move of {name} while {owner.label} uses it")
        return
    queue = owner.queue.name if owner.queue is not None else "default"
    raise MoveBlocked(
        f"{name} is in use by {owner.label} ({owner.source[:70]}) -- not moved.\n"
        f"  To move it anyway, e.g. to fix a stuck move while the task waits:\n"
        f"      with forced():\n"
        f"          <your move of {name}>\n"
        f"  (forced is eco.utilities.task_queue.forced). To free it for the rest of the task:\n"
        f"      queues[{queue!r}].release(<{name}>)\n"
        f"  {name}.stop() is never blocked; to end the task: %queue -q {queue} stop_running_task"
    )


def claims():
    """{full name: task} of every adjustable a running task holds."""
    with _claims_lock:
        return {_display_name(adj): task for adj, task in _claims.values() if task.state == "running"}


# -- checks (the "test run") -------------------------------------------------------------

_SAFE_CALLS = {"range", "len", "float", "int", "abs", "min", "max", "round", "list", "tuple", "dict", "str", "bool"}
_MOVE_METHODS = {"mv", "umv", "set_target_value", "set_target", "mvr", "umvr"}


def check_task(task, namespace, defined=()):
    """Problems found by test-running `task` (empty list = fine). See the
    module docstring for what is checked; nothing is moved."""
    if isinstance(task.command, str):
        return _check_statement(task.command, namespace, set(defined))
    return _check_call(task.command, task.args, task.kwargs, task.source)


def _check_statement(source, namespace, defined):
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg} (line {exc.lineno})"]
    problems = []
    known = set(namespace) | set(dir(builtins)) | defined | _assigned_names(source)
    for name in sorted(_loaded_names(tree) - known):
        close = difflib.get_close_matches(name, [k for k in namespace if not k.startswith("_")], n=1)
        problems.append(f"name {name!r} is not defined" + (f" (did you mean {close[0]!r}?)" if close else ""))
    if problems:
        return problems
    resolver = _Resolver(namespace)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            problem = resolver.check_chain(node)
            if problem:
                problems.append(problem)
    if problems:
        return sorted(set(problems))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            try:
                func = resolver.value(node.func)
            except (_NotPlain, AttributeError):
                continue  # not a plain callee: only checked when it runs
            if func is _UNKNOWN:
                continue
            try:
                args = [resolver.value(a) for a in node.args]
                kwargs = {k.arg: resolver.value(k.value) for k in node.keywords if k.arg is not None}
            except _NotPlain:
                continue
            if any(a is _UNKNOWN for a in args) or any(v is _UNKNOWN for v in kwargs.values()):
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
                continue
            problems += _check_call(func, args, kwargs, ast.unparse(node))
    return problems


def _check_call(func, args, kwargs, text):
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        try:
            signature.bind(*args, **kwargs)
        except TypeError as exc:
            return [f"{text}: {exc}"]
    if signature is not None and "start_immediately" in signature.parameters:
        return _check_scan(func, args, kwargs, text)
    name = getattr(func, "__name__", "")
    owner = getattr(func, "__self__", None)
    if name in _MOVE_METHODS and owner is not None and args:
        return _check_move(owner, name, args[0], text)
    return []


def _check_scan(method, args, kwargs, text):
    """Build the scan without running it and check its planned positions."""
    from eco.acquisition.scan_tasks import check_scan_limits

    kwargs = dict(kwargs, start_immediately=False)
    kwargs.pop("queue", None)  # build it here, don't queue it again
    _local.test_building = True
    try:
        scan = method(*args, **kwargs)
    except Exception as exc:
        return [f"{text}: building the scan failed: {type(exc).__name__}: {exc}"]
    finally:
        _local.test_building = False
    if scan is None:
        return []
    return [f"{text}: {problem}" for problem in check_scan_limits(scan)]


def _check_move(adjustable, method, value, text):
    get_limits = getattr(adjustable, "get_limits", None)
    if not callable(get_limits):
        return []
    try:
        low, high = get_limits()
        target = value
        if method in ("mvr", "umvr"):
            target = adjustable.get_current_value() + value
        if low is not None and high is not None and low != high and not (low <= target <= high):
            return [f"{text}: target {target} is outside the limits ({low}, {high})"]
    except Exception:
        pass  # a limits/readback query failing is not evidence the move is bad
    return []


class _NotPlain(Exception):
    pass


_UNKNOWN = object()


class _Resolver:
    """Evaluates *plain* expressions only -- names, attribute chains,
    constants, arithmetic, containers and a few pure builtins -- for the
    test run. Attribute access goes through inspect.getattr_static first so
    properties (which may read hardware) are never evaluated."""

    def __init__(self, namespace):
        self.namespace = namespace

    def value(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self.namespace:
                return self.namespace[node.id]
            if hasattr(builtins, node.id):
                return getattr(builtins, node.id)
            return _UNKNOWN  # assigned by an earlier task: known only when it runs
        if isinstance(node, ast.Attribute):
            base = self.value(node.value)
            if base is _UNKNOWN:
                return _UNKNOWN
            return _get_attribute(base, node.attr)
        if isinstance(node, (ast.Tuple, ast.List)):
            items = [self.value(e) for e in node.elts]
            if any(i is _UNKNOWN for i in items):
                return _UNKNOWN
            return tuple(items) if isinstance(node, ast.Tuple) else items
        if isinstance(node, ast.UnaryOp):
            operand = self.value(node.operand)
            if operand is _UNKNOWN:
                return _UNKNOWN
            return _apply_unary(node.op, operand)
        if isinstance(node, ast.BinOp):
            left, right = self.value(node.left), self.value(node.right)
            if left is _UNKNOWN or right is _UNKNOWN:
                return _UNKNOWN
            return _apply_binary(node.op, left, right)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _SAFE_CALLS:
            args = [self.value(a) for a in node.args]
            if any(a is _UNKNOWN for a in args) or node.keywords:
                return _UNKNOWN
            return getattr(builtins, node.func.id)(*args)
        if isinstance(node, ast.Call) and _is_numpy_call(node, self.namespace):
            func = self.value(node.func)
            args = [self.value(a) for a in node.args]
            kwargs = {k.arg: self.value(k.value) for k in node.keywords if k.arg}
            if func is _UNKNOWN or any(a is _UNKNOWN for a in args) or any(v is _UNKNOWN for v in kwargs.values()):
                return _UNKNOWN
            return func(*args, **kwargs)
        raise _NotPlain(ast.unparse(node))

    def check_chain(self, node):
        """'x.y.z': does every link exist? None if fine (or not checkable)."""
        chain = []
        while isinstance(node, ast.Attribute):
            chain.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id not in self.namespace:
            return None
        obj = self.namespace[node.id]
        path = node.id
        for attr in reversed(chain):
            try:
                obj = _get_attribute(obj, attr)
            except AttributeError:
                options = [a for a in dir(obj) if not a.startswith("_")]
                close = difflib.get_close_matches(attr, options, n=1)
                return f"{path} has no attribute {attr!r}" + (f" (did you mean {close[0]!r}?)" if close else "")
            if obj is _UNKNOWN:
                return None
            path = f"{path}.{attr}"
        return None


def _get_attribute(obj, attr):
    """getattr, except that a property/descriptor is not evaluated (returns
    _UNKNOWN when it exists). Lazy eco proxies are resolved -- the device is
    initialised, as it would be when the task runs."""
    try:
        static = inspect.getattr_static(obj, attr)
    except AttributeError:
        return getattr(obj, attr)  # dynamic (__getattr__, lazy proxy): raises if missing
    if isinstance(static, (property, staticmethod, classmethod)) or (
        hasattr(type(static), "__get__") and not inspect.isfunction(static)
        and not inspect.ismethoddescriptor(static) and not inspect.isbuiltin(static)
    ):
        if isinstance(static, (staticmethod, classmethod)):
            return getattr(obj, attr)
        return _UNKNOWN
    return getattr(obj, attr)


def _apply_unary(op, operand):
    import operator

    return {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_, ast.Invert: operator.invert}[type(op)](operand)


def _apply_binary(op, left, right):
    import operator

    ops = {
        ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    }
    if type(op) not in ops:
        raise _NotPlain(type(op).__name__)
    return ops[type(op)](left, right)


def _is_numpy_call(node, namespace):
    """np.linspace(...)/numpy.arange(...) and friends: pure, safe to evaluate."""
    func = node.func
    while isinstance(func, ast.Attribute):
        func = func.value
    if not isinstance(func, ast.Name):
        return False
    module = namespace.get(func.id)
    return getattr(module, "__name__", "").split(".")[0] == "numpy"


def _loaded_names(tree):
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _assigned_names(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            names |= {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
    return names


# -- the %queue magic ----------------------------------------------------------------------

_SUBCOMMANDS = (
    "status", "test_run", "pause", "pause_after_running_task", "resume",
    "cancel_after_running_task", "clear", "pause_running_task", "resume_running_task",
    "stop_running_task", "stop",
    "repeat_last", "repeat", "remove", "move", "wait", "test",
    "show_output", "mute", "unmute", "record", "stop_recording", "release",
)


def _magic_doc():
    lines = [
        "%queue [-q NAME] [--no-test] STATEMENT   append STATEMENT to queue NAME (default: 'default')",
        "%queue [-q NAME] SUBCOMMAND [ARGS]       run a queue method, e.g. %queue repeat_last 3",
        "%queue [-q NAME]                         status",
        "%%queue [-q NAME] [--no-test]            the whole cell as one task",
        "%queue -- STATEMENT                      a statement that starts like a subcommand",
        "",
        "Subcommands (methods of eco.utilities.task_queue.TaskQueue):",
    ]
    for name in _SUBCOMMANDS:
        doc = inspect.getdoc(getattr(TaskQueue, name)) or ""
        lines.append(f"  {name:28s} {doc.splitlines()[0] if doc else ''}")
    lines += ["", "See eco.utilities.task_queue for the details (what the test run checks, input"]
    lines += ["questions from queued tasks, the shared namespace)."]
    return "\n".join(lines)


def queue_magic(line, cell=None):
    queue_name, test, rest = "default", True, line.strip()
    while rest.startswith("-"):
        if rest.startswith("-- ") or rest == "--":
            rest = rest[2:].strip()
            return _magic_append(queues[queue_name], rest if cell is None else cell, test)
        token, _, remainder = rest.partition(" ")
        if token in ("-q", "--queue"):
            queue_name, _, rest = remainder.strip().partition(" ")
            rest = rest.strip()
        elif token == "--no-test":
            test, rest = False, remainder.strip()
        else:
            raise ValueError(f"unknown option {token!r}\n\n{_magic_doc()}")
    queue = queues[queue_name]
    if cell is not None:
        return _magic_append(queue, cell, test)
    if not rest:
        queue.status()
        return None
    word, _, args = rest.partition(" ")
    if word == "help":
        print(_magic_doc())
        return None
    if word in _SUBCOMMANDS and _is_call_arguments(args):
        from IPython import get_ipython

        shell = get_ipython()
        namespace = shell.user_ns if shell is not None else {}
        result = eval(f"_queue_method({args})", dict(namespace, _queue_method=getattr(queue, word)))
        # these print their own report; returning it too would show it twice (Out[...])
        return None if word in ("status", "test_run", "wait") else result
    return _magic_append(queue, rest, test)


def _is_call_arguments(text):
    try:
        ast.parse(f"f({text})", mode="eval")
        return True
    except SyntaxError:
        return False


def _magic_append(queue, source, test):
    try:
        task = queue.append(source, test=test)
    except QueueTestError as exc:
        print(f"not queued -- test run found: " + "; ".join(exc.problems))
        print("(append anyway with %queue --no-test ...)")
        return None
    print(f"queued as #{task.id} in {queue.name!r}" + (" (paused)" if queue.paused else ""))
    return None


queue_magic.__doc__ = _magic_doc()


def _show_move_blocked(shell, etype, value, tb, tb_offset=None):
    """IPython custom exception handler: a blocked move shows its message
    (who holds the adjustable, how to force it), not a traceback through
    mv/set_target_value/Changer."""
    print(f"{etype.__name__}: {value}")


def register_magics(shell):
    """Register %queue / %%queue on an IPython shell (idempotent), and the
    short display of MoveBlocked -- only if the shell has no custom
    exception handler of its own (IPython allows one)."""
    if shell is None:
        return
    shell.register_magic_function(queue_magic, magic_kind="line_cell", magic_name="queue")
    if not getattr(shell, "custom_exceptions", ()):
        shell.set_custom_exc((MoveBlocked,), _show_move_blocked)
