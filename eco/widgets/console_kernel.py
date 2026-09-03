"""Shared kernel-building logic for eco's Qt console widgets (the desktop
workbench's embedded console in eco.widgets.desktop_app, and independent
consoles from eco.start_console() / eco.widgets.console_window_qt) --
factored out so both go through the same two kernel flavours and the same
input/output logging, rather than each hand-rolling its own.

Two flavours, and why both exist
---------------------------------
- **In-process** (`build_inprocess_kernel`): the kernel runs inside this
  same Python interpreter, on IPython's own `InProcessInteractiveShell`.
  Fast, and lets a caller genuinely *share* live objects (the exact same
  namespace dict, not a copy) with the console. Only safe when nothing
  else in this process has already claimed IPython's `InteractiveShell`
  singleton -- see `can_use_inprocess_kernel()`.

- **Subprocess** (`build_subprocess_kernel`): a real, separate `ipykernel`
  process, connected to via the normal Jupyter connection-file protocol --
  exactly how `jupyter qtconsole` or `jupyter console --existing` work
  against any other kernel. This is the only option once an incompatible
  IPython shell already exists in this process (calling
  eco.start_desktop() from a running `ipython` terminal session is exactly
  that case): ipykernel's `IPythonKernel.__init__` calls
  `self.shell_class.instance(...)`, and traitlets' `SingletonConfigurable`
  raises `MultipleInstanceError` the moment a *different* InteractiveShell
  subclass already holds that singleton in this process -- there is no way
  around this short of running the new kernel elsewhere. The cost: no
  literal object sharing across the process boundary (device connections,
  threads, etc. wouldn't survive being pickled anyway) -- a subprocess
  console instead re-runs `startup_code` to build its own, independent-but-
  equivalent namespace.

Both flavours register a `eco.widgets.kernel_registry.KernelSession` and
build a `LoggingJupyterWidget`, so every console opened through either path
gets the same activity log regardless of which kind of kernel is behind it.
"""
import logging

from qtconsole.rich_jupyter_widget import RichJupyterWidget

from eco.widgets import kernel_registry

logger = logging.getLogger(__name__)

# Registers eco's lazy eco.utilities.config.Proxy with IPython's
# guarded_eval allow-list, so the classic (non-jedi) completer can complete
# *past* an already-resolved Proxy (`prepump.line1_usd.<TAB>`) instead of
# silently returning nothing -- see build_console_widget's docstring and
# CLAUDE.md's "Tab completion" section for the full mechanism. Best-effort
# (guarded_eval is an internal, fairly young IPython module): a future
# IPython that reshapes it should degrade to today's behaviour, not an
# error in the console. Module-level so tests can assert on it by name
# instead of duplicating the exact source string.
GUARDED_EVAL_PROXY_PATCH_CODE = (
    "import contextlib as _contextlib\n"
    "with _contextlib.suppress(Exception):\n"
    "    import IPython.core.guarded_eval as _guarded_eval\n"
    "    _guarded_eval.EVALUATION_POLICIES['limited'].allowed_getattr_external.add(\n"
    "        ('eco.utilities.config', 'Proxy')\n"
    "    )"
)

# The guarded_eval patch above has a side effect: completing *into* a still-
# unresolved component now silently triggers its real initialisation, with
# no warning, just from pressing Tab. This installs the two-Tab confirmation
# gate (see eco.utilities.lazy_completion) that turns that into "1st Tab:
# warn, no completions; 2nd Tab: resolve and complete" instead -- see
# CLAUDE.md's "Tab completion" section.
LAZY_COMPLETION_GATE_CODE = (
    "import contextlib as _contextlib\n"
    "with _contextlib.suppress(Exception):\n"
    "    from eco.utilities.lazy_completion import install_lazy_completion_gate\n"
    "    install_lazy_completion_gate()"
)


def can_use_inprocess_kernel():
    """False if this process already has a running IPython shell (a
    terminal session, a notebook kernel, an already-open eco desktop's own
    console, ...) -- ipykernel's InProcessInteractiveShell can't coexist
    with any *other* InteractiveShell subclass already holding IPython's
    global singleton slot (see the module docstring). True (safe to use
    build_inprocess_kernel) only for a plain, non-interactive process."""
    try:
        from IPython import get_ipython

        return get_ipython() is None
    except Exception:
        return True


def build_inprocess_kernel(kind, label=None, shared_user_ns=None, push_vars=None):
    """A kernel running in *this* interpreter, via IPython's own
    InProcessInteractiveShell. Only call this when can_use_inprocess_kernel()
    is True -- see the module docstring for why.

    shared_user_ns: if given, the kernel's shell namespace *is* this dict
    object (not a copy) -- assignments made through the console or through
    whatever else holds a reference to this same dict are mutually visible.
    push_vars: if given (and shared_user_ns is not), these names/values are
    pushed into the kernel's own fresh namespace instead.

    Returns (kernel_manager, kernel_client, session).
    """
    from qtconsole.inprocess import QtInProcessKernelManager

    session = kernel_registry.register(kernel_registry.KernelSession(kind=kind, label=label))

    kernel_manager = QtInProcessKernelManager()
    kernel_manager.start_kernel()
    kernel = kernel_manager.kernel
    kernel.gui = "qt"

    if shared_user_ns is not None:
        kernel.shell.user_ns = shared_user_ns
    elif push_vars:
        kernel.shell.push(dict(push_vars))

    kernel_client = kernel_manager.client()
    kernel_client.start_channels()

    session.pid = None  # in-process: no separate PID to report
    return kernel_manager, kernel_client, session


def _subprocess_env():
    """os.environ, plus this process's *actual* sys.path folded into
    PYTHONPATH -- a spawned kernel only inherits environment variables,
    not this process's live sys.path list, so anything that got onto
    sys.path at runtime rather than via PYTHONPATH (e.g. IPython's %run,
    which is how `eco`'s own startup script typically ends up importable
    in the first place -- see startup_inline.py/startup_inline_new.py)
    would otherwise be invisible to the new kernel. Confirmed for real:
    without this, `import eco.widgets` in a freshly-spawned kernel raised
    ModuleNotFoundError even though the calling terminal (same repo
    checkout) imported it fine -- jupyter_client's default kernel
    launch only inherits os.environ, and passing env= here *replaces*
    that default rather than adding to it (see
    KernelProvisionerBase.pre_launch), so this has to start from a full
    os.environ copy, not just the PYTHONPATH bits."""
    import os
    import sys

    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(sys.path) + (os.pathsep + existing if existing else "")
    return env


def build_subprocess_kernel(kind, label=None, kernel_name="python3"):
    """A real, separate ipykernel process, connected to via the normal
    Jupyter connection-file protocol. Always safe to call, regardless of
    what else is running in this process -- see the module docstring.

    Deliberately does NOT take a startup_code= to execute here: sending it
    straight to this bare kernel_client, before any console widget has
    been attached to it, raced the iopub channel's ZMQ SUB socket
    subscribing (the "slow joiner" problem -- a PUB socket doesn't buffer
    for a SUB that subscribes late) against the kernel actually replying,
    and could silently swallow the startup code's own output/errors,
    including a genuine failure that left `namespace` undefined with no
    visible trace of why. Run startup code through the finished console
    widget's own .execute() instead (see build_console_widget below, and
    the callers in desktop_app.py/console_window_qt.py) -- constructing
    the widget with kernel_client already attached *is* what subscribes
    the iopub channel, so nothing sent afterward can be missed.

    Returns (kernel_manager, kernel_client, session).
    """
    from qtconsole.manager import QtKernelManager

    session = kernel_registry.register(kernel_registry.KernelSession(kind=kind, label=label))

    kernel_manager = QtKernelManager(kernel_name=kernel_name)
    kernel_manager.start_kernel(env=_subprocess_env())
    kernel_client = kernel_manager.client()
    kernel_client.start_channels()

    session.pid = _kernel_process_pid(kernel_manager)
    session.connection_file = getattr(kernel_manager, "connection_file", None)
    session.log_event(
        "session_start_subprocess", pid=session.pid, connection_file=session.connection_file
    )

    return kernel_manager, kernel_client, session


def build_console_widget(kernel_manager, kernel_client, session, banner="", startup_code=None):
    """Construct the LoggingJupyterWidget for a (kernel_manager,
    kernel_client, session) triple from either build_*_kernel() above,
    attach it, then -- only now that its iopub channel is actually
    subscribed -- run `startup_code` through the widget's own .execute()
    (not the bare kernel_client) so its output/errors are guaranteed to
    display, and get logged the same way anything else typed into this
    console would (see LoggingJupyterWidget).

    Also disables Jedi-based tab-completion (hidden, before startup_code):
    Jedi builds its completion menu by asking every candidate for
    introspection data, which for eco's lazy device proxies (see
    eco.utilities.config.Proxy) can mean fully resolving -- constructing,
    with real EPICS calls -- one just from it being a completion
    candidate while typing. Confirmed for real: over a minute for one
    complex device, purely from tab-completion touching it, before ever
    being used. The classic (non-jedi) completer only resolves an object
    for *dotted* attribute completion (`name.<TAB>`), not for matching
    plain top-level names -- and was also measured faster and more
    correct here regardless (matches found vs none, from Jedi silently
    failing on this dynamic a namespace).

    Also registers eco's Proxy with IPython's `guarded_eval` allow-list
    (see CLAUDE.md's "Tab completion" section, and
    eco/startup_inline.py's matching comment): with jedi off, the classic
    completer still refuses to complete *past* an already-resolved Proxy
    (e.g. `prepump.line1_usd.<TAB>`) unless its `__getattribute__` is on
    that allow-list, since guarded_eval otherwise treats any object with a
    non-stock `__getattribute__` as unsafe to evaluate mid-expression.

    Also installs eco's two-Tab completion-confirmation gate (see
    eco.utilities.lazy_completion): the guarded_eval allow-list entry above
    means completing into a still-*un*resolved component would otherwise
    silently trigger its real initialisation just from pressing Tab."""
    console = LoggingJupyterWidget(session=session)
    console.kernel_manager = kernel_manager
    console.kernel_client = kernel_client
    console.banner = banner
    console.execute("get_ipython().Completer.use_jedi = False", hidden=True)
    console.execute(GUARDED_EVAL_PROXY_PATCH_CODE, hidden=True)
    console.execute(LAZY_COMPLETION_GATE_CODE, hidden=True)
    if startup_code:
        console.execute(startup_code)
    return console


def _kernel_process_pid(kernel_manager):
    """The subprocess PID behind a real (non in-process) kernel_manager, or
    None if it can't be found. jupyter_client >= 7 launches kernels through
    a KernelProvisioner rather than exposing the Popen object as
    `.kernel` directly (that attribute doesn't exist at all in this
    version) -- `.provisioner.process` is where the actual Popen lives."""
    provisioner = getattr(kernel_manager, "provisioner", None)
    process = getattr(provisioner, "process", None)
    return getattr(process, "pid", None)


def stop_kernel(kernel_manager, kernel_client, session=None):
    """Tear down a kernel_manager/kernel_client pair built by either
    function above, and unregister its session if given. Safe to call with
    Nones (e.g. from a widget's stop() that may run before the kernel was
    ever built)."""
    if kernel_client is not None:
        try:
            kernel_client.stop_channels()
        except Exception:
            logger.exception("stopping kernel client channels failed")
    if kernel_manager is not None:
        try:
            kernel_manager.shutdown_kernel()
        except Exception:
            logger.exception("shutting down kernel failed")
    if session is not None:
        session.log_event("session_end")
        kernel_registry.unregister(session)


class LoggingJupyterWidget(RichJupyterWidget):
    """RichJupyterWidget that mirrors everything typed in (or programmatically
    .execute()'d) and everything that comes back (results, stdout/stderr,
    errors) into a kernel_registry.KernelSession -- the single choke point
    every eco console widget executes through, whether its kernel is
    in-process or a subprocess. `session` may be attached after
    construction (set_session) since the widget itself is often built
    before the kernel/session is ready."""

    def __init__(self, *args, session=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._kernel_session = session

    def set_session(self, session):
        self._kernel_session = session

    def _execute(self, source, hidden):
        if self._kernel_session is not None and not hidden and source.strip():
            self._kernel_session.log_input(source)
        return super()._execute(source, hidden)

    def _handle_execute_result(self, msg):
        super()._handle_execute_result(msg)
        if self._kernel_session is not None:
            self._kernel_session.log_output("result", **extract_result_fields(msg))

    def _handle_stream(self, msg):
        super()._handle_stream(msg)
        if self._kernel_session is not None:
            self._kernel_session.log_output("stream", **extract_stream_fields(msg))

    def _handle_error(self, msg):
        super()._handle_error(msg)
        if self._kernel_session is not None:
            self._kernel_session.log_output("error", **extract_error_fields(msg))


# -- pure message-field extraction ------------------------------------------
#
# Split out from the _handle_* overrides above so the "what do we log"
# logic is unit-testable against plain dicts, without needing a real
# RichJupyterWidget instance (constructing one has been observed to crash
# the interpreter outright under pytest+offscreen+eco's full scientific
# import stack -- the same fragility already noted for QDial construction
# in tests/test_indicator_widgets.py).


def extract_result_fields(msg):
    """Jupyter execute_result message -> kwargs for KernelSession.log_output."""
    data = msg.get("content", {}).get("data", {})
    return {"text": data.get("text/plain")}


def extract_stream_fields(msg):
    """Jupyter stream message (stdout/stderr) -> kwargs for
    KernelSession.log_output."""
    content = msg.get("content", {})
    return {"name": content.get("name"), "text": content.get("text")}


def extract_error_fields(msg):
    """Jupyter error message -> kwargs for KernelSession.log_output."""
    content = msg.get("content", {})
    return {"ename": content.get("ename"), "evalue": content.get("evalue")}
