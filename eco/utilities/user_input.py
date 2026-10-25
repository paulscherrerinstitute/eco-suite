"""Questions to the user that work from any thread, and know about queues.

eco code asks the user things with ``input()`` (``"(y/n)"`` confirmations, a
user name, ...) and with a timed input (``"Change back to initial values?
... in 10 seconds"``). Both read the terminal, which only the main thread may
do -- and with commands now also running on a queue's worker thread
(eco.utilities.task_queue) a question from there must still reach the user:

- :func:`ask` (and the thread-aware ``input()`` installed by
  :func:`install_input_routing`): from the main thread it simply asks; from
  any other thread it shows the question *on the main thread* and blocks the
  asking thread until it is answered. The main thread blocks while the
  question is open, too -- whether the user sits idle at the prompt (the
  question is printed above it, via prompt_toolkit's ``run_in_terminal``) or
  a cell is running (it is asked right there, from a heartbeat tick, see
  eco.utilities.mainthread). Qt windows keep updating meanwhile; Ctrl-C
  cancels the question (:class:`QuestionCancelled` in the asking thread).
  In the eco desktop (no terminal) the question is a Qt input dialog.
- :func:`input_timeout`: a question with a default after ``timeout``
  seconds, raising :class:`InputTimeout` (a subclass of
  ``inputimeout.TimeoutOccurred``, so existing ``except`` clauses keep
  working). In an unattended thread -- a queue worker, see
  :func:`unattended` -- nobody is expected to watch, so by default the
  default is taken *immediately*, without waiting out the timeout;
  ``escalate=True`` asks on the main thread instead (with the timeout).

Where no channel exists (no terminal, no Qt), a question stays pending:
:func:`pending_questions` lists it and :func:`answer` resolves it -- which
also works from any other place, e.g. typed at the prompt.
"""

import builtins
import contextlib
import itertools
import select
import signal
import sys
import threading
import time

try:  # so existing `except inputimeout.TimeoutOccurred` clauses catch ours
    from inputimeout import TimeoutOccurred as _TimeoutBase
except Exception:  # pragma: no cover - inputimeout is in the beamline env

    class _TimeoutBase(Exception):
        pass


class InputTimeout(_TimeoutBase):
    """No answer within the timeout -- the caller takes its default."""


class QuestionCancelled(KeyboardInterrupt):
    """The user cancelled the question (Ctrl-C, or Cancel in the dialog). A
    KeyboardInterrupt, so code that already treats Ctrl-C at its prompt as
    "abort" (e.g. the end-of-scan question) keeps doing so."""


_local = threading.local()


@contextlib.contextmanager
def unattended(label="queue"):
    """Mark the calling thread as running unattended for the block, e.g. a
    queue's worker thread. :func:`input_timeout` then takes defaults at once
    unless asked with ``escalate=True``; plain questions still go to the
    user."""
    previous = getattr(_local, "unattended", None)
    _local.unattended = label
    try:
        yield
    finally:
        _local.unattended = previous


def unattended_label():
    """The label passed to :func:`unattended` for this thread, or None."""
    return getattr(_local, "unattended", None)


def _on_main_thread():
    return threading.current_thread() is threading.main_thread()


# -- the question objects --------------------------------------------------------

_ids = itertools.count(1)
_pending = {}  # id -> Question, while unanswered
_dispatch_lock = threading.Lock()  # only ever taken by asking threads, never in a tick


class Question:
    def __init__(self, prompt, timeout=None, origin=None):
        self.id = next(_ids)
        self.prompt = prompt
        self.timeout = timeout
        self.origin = origin  # e.g. "queue 'default', task #3"
        self.created = time.monotonic()
        self._done = threading.Event()
        self._answer = None
        self._error = None

    def resolve(self, answer=None, error=None):
        """First resolution wins (an answer typed at the prompt via answer()
        and the dialog racing each other is fine)."""
        if self._done.is_set():
            return False
        self._answer, self._error = answer, error
        _pending.pop(self.id, None)
        self._done.set()
        return True

    def wait(self):
        while not self._done.wait(0.05):  # sliced: see mainthread.wait_event
            pass
        if self._error is not None:
            raise self._error
        return self._answer

    def __repr__(self):
        return f"<Question #{self.id} {self.prompt!r} from {self.origin or 'main thread'}>"


def pending_questions():
    """Questions asked from other threads that nobody has answered yet."""
    return list(_pending.values())


def answer(value, question=None):
    """Answer a pending question (the oldest one, or ``question``: a
    Question or its id) from wherever you are. Returns True if it was still
    open."""
    if question is None:
        if not _pending:
            return False
        question = min(_pending)
    if isinstance(question, int):
        question = _pending.get(question)
    return question is not None and question.resolve(str(value))


# -- channels (all run on the main thread) ------------------------------------


def _terminal_shell():
    try:
        from IPython import get_ipython

        shell = get_ipython()
    except Exception:
        return None
    if type(shell).__name__ != "TerminalInteractiveShell":
        return None
    try:
        if not sys.stdin.isatty():
            return None
    except Exception:
        return None
    return shell


def _running_prompt_app(shell):
    """prompt_toolkit's Application while the shell sits at its prompt."""
    app = getattr(getattr(shell, "pt_app", None), "app", None)
    if app is not None and getattr(app, "is_running", False):
        return app
    return None


@contextlib.contextmanager
def _sigint_raises():
    """Ctrl-C raises KeyboardInterrupt here, whatever handler (e.g. the
    heartbeat's deferral during a tick) was installed around us."""
    try:
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    except Exception:
        previous = None
    try:
        yield
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


def _header(question):
    return f"\n[{question.origin} asks]\n" if question.origin else ""


def _read_terminal_line(question):
    """Prompt and read one line from the terminal (cooked mode), keeping Qt
    serviced and honouring the timeout. Main thread only."""
    from eco.utilities.mainthread import _qt_application

    # the real terminal, not sys.stdout: at the prompt that is prompt_toolkit's
    # StdoutProxy, which only flushes through the event loop this question
    # blocks -- the question would wait, invisible
    out = sys.__stdout__
    out.write(_header(question) + question.prompt)
    if question.timeout is not None:
        out.write(f" [{question.timeout:g} s] ")
    out.flush()
    deadline = None if question.timeout is None else time.monotonic() + question.timeout
    fd = sys.stdin.fileno()
    with _sigint_raises():
        try:
            while True:
                if select.select([fd], [], [], 0.02)[0]:
                    line = sys.stdin.readline()
                    return line.rstrip("\n")
                app = _qt_application()
                if app is not None:
                    app.processEvents()
                if deadline is not None and time.monotonic() > deadline:
                    out.write("\n")
                    raise InputTimeout(f"no answer within {question.timeout} s")
        except KeyboardInterrupt:
            out.write("\n")
            raise QuestionCancelled(question.prompt) from None


def _ask_qt_dialog(question):
    """A modal input dialog (the eco desktop has no terminal). Main thread."""
    from qtpy import QtCore, QtWidgets

    dialog = QtWidgets.QInputDialog()
    dialog.setWindowTitle("eco question")
    label = (_header(question).strip() + "\n" if question.origin else "") + question.prompt
    if question.timeout is not None:
        label += f"\n(default in {question.timeout:g} s)"
    dialog.setLabelText(label)
    timed_out = []
    if question.timeout is not None:

        def _expire():
            timed_out.append(True)
            dialog.reject()

        QtCore.QTimer.singleShot(int(question.timeout * 1000), _expire)
    accepted = dialog.exec_() == QtWidgets.QDialog.Accepted
    if timed_out:
        raise InputTimeout(f"no answer within {question.timeout} s")
    if not accepted:
        raise QuestionCancelled(question.prompt)
    return dialog.textValue()


def _ask_here(question):
    """Answer `question` synchronously, on the main thread. Returns the
    answer or raises InputTimeout/QuestionCancelled; returns NotImplemented
    if there is no channel at all."""
    from eco.utilities.mainthread import _qt_application

    if _terminal_shell() is not None:
        return _read_terminal_line(question)
    if _qt_application() is not None:
        return _ask_qt_dialog(question)
    return NotImplemented


def _ask_plain(question):
    """No IPython terminal and no Qt (a script, a service): read stdin like
    before this module existed -- ``inputimeout`` for a timed question."""
    if question.timeout is None:
        return _original_input(question.prompt)
    import inputimeout

    try:
        return inputimeout.inputimeout(prompt=question.prompt, timeout=question.timeout)
    except inputimeout.TimeoutOccurred as exc:
        raise InputTimeout(str(exc)) from exc


def _resolve_from(question, func):
    try:
        result = func(question)
    except BaseException as exc:  # InputTimeout, QuestionCancelled, ...
        question.resolve(error=exc)
        return
    if result is NotImplemented:
        _announce_pending(question)
    else:
        question.resolve(result)


def _announce_pending(question):
    _pending[question.id] = question
    print(
        f"{_header(question)}{question.prompt}\n"
        f"  (no terminal or Qt to ask in -- answer with "
        f"eco.utilities.user_input.answer(<value>, {question.id}))"
    )


def _dispatch(question):
    """Runs on the main thread (via mainthread.call_soon): show the question
    now. Idle at the terminal prompt it is handed to prompt_toolkit, which
    suspends the prompt, runs it, and redraws the prompt afterwards; this
    returns immediately (we may be inside the prompt's input hook). Anywhere
    else it is answered synchronously right here."""
    shell = _terminal_shell()
    app = _running_prompt_app(shell) if shell is not None else None
    if app is None:
        _resolve_from(question, _ask_here)
        return
    from prompt_toolkit.application import run_in_terminal
    from prompt_toolkit.application.current import set_app

    async def _show():
        with set_app(app):
            await run_in_terminal(lambda: _resolve_from(question, _ask_here))

    try:
        app.loop.call_soon_threadsafe(lambda: app.create_background_task(_show()))
    except Exception:
        _resolve_from(question, _ask_here)


# -- the public API ------------------------------------------------------------------


def ask(prompt="", timeout=None, origin=None):
    """Ask the user and return the answer (a str). From the main thread this
    asks directly; from any other thread the question is shown on the main
    thread and this blocks until it is answered. ``timeout`` (s): raise
    :class:`InputTimeout` when nobody answers in time. Ctrl-C (or Cancel)
    raises :class:`QuestionCancelled`. ``origin`` labels who is asking (a
    queue task, ...); defaults to the unattended label of this thread."""
    question = Question(prompt, timeout=timeout, origin=origin or unattended_label())
    if _on_main_thread():
        result = _ask_here(question)
        if result is NotImplemented:  # a script/service: plain stdin, as before
            return _ask_plain(question)
        return result
    from eco.utilities import mainthread

    with _dispatch_lock:  # one question at a time on the main thread
        try:
            mainthread.call_soon(_dispatch, question)
        except RuntimeError:  # no heartbeat: nobody to run it on the main thread
            _announce_pending(question)
        return question.wait()


def input_timeout(prompt, timeout, escalate=False):
    """Ask with a default after ``timeout`` seconds: returns the answer, or
    raises :class:`InputTimeout` (an ``inputimeout.TimeoutOccurred``) so the
    caller takes its default.

    In an unattended thread (a queue's worker, see :func:`unattended`) the
    default is taken at once -- nobody is expected to be watching, so the
    timeout is not waited out -- unless ``escalate=True``, which asks the
    user on the main thread instead (still with the timeout). Use
    ``escalate=True`` only for questions where the default is not safe to
    take silently."""
    label = unattended_label()
    if label is not None and not escalate:
        print(f"{prompt}\n  [{label}: running unattended, default taken without waiting]")
        raise InputTimeout("unattended: default taken")
    return ask(prompt, timeout=timeout)


# -- thread-aware input() ------------------------------------------------------------

_original_input = builtins.input


def _routed_input(prompt=""):
    if _on_main_thread():
        return _original_input(prompt)
    return ask(prompt)


_routed_input.__doc__ = (
    "input(), routed: from any thread but the main one the question is shown "
    "on the main thread (see eco.utilities.user_input)."
)
_routed_input._eco_routed = True


def install_input_routing():
    """Make the builtin ``input()`` thread-aware (see module docstring).
    Idempotent. eco/startup_inline.py calls it."""
    global _original_input
    if getattr(builtins.input, "_eco_routed", False):
        return
    _original_input = builtins.input
    builtins.input = _routed_input


def route_kernel_input(kernel):
    """Same for an in-process ipykernel, which swaps ``builtins.input`` for
    its own ``raw_input`` while a cell runs: calls from other threads then
    go to the main thread (a Qt dialog in the desktop) instead of into the
    kernel's stdin channel from the wrong thread."""
    original = kernel.raw_input
    if getattr(original, "_eco_routed", False):
        return

    def raw_input(prompt=""):
        if _on_main_thread():
            return original(prompt)
        return ask(prompt)

    raw_input._eco_routed = True
    kernel.raw_input = raw_input
