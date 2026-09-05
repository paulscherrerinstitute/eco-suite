import io
import json
import logging
import importlib
import importlib.util
import os
from pathlib import Path
from eco.utilities.datafiles import open_group_writable
from eco.elements.adjustable import AdjustableFS, AdjustableMemory
from eco.elements.protocols import InitialisationWaitable
import sys
from time import sleep, time
from colorama import Fore as _color
from functools import partial

# from .lazy_proxy import Proxy
from ..aliases import Alias
from ..elements.assembly import Assembly, IncompleteInitialisationError
import getpass
import colorama
import socket
from importlib import import_module
from lazy_object_proxy import Proxy as Proxy_orig
from .tables import format_table
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread, Event, Lock, get_ident
import tempfile
from tqdm import tqdm
from rich import progress
from inspect import signature
from simple_term_menu import TerminalMenu

import traceback

logger = logging.getLogger(__name__)


def _is_notebook():
    try:
        from IPython import get_ipython

        ip = get_ipython()
        return ip is not None and ip.__class__.__name__ == "ZMQInteractiveShell"
    except Exception:
        return False


def _resolve_reloadable_factory(obj_factory):
    """Best-effort late-binding for a directly-passed (no ``module_name=``)
    namespace-item factory: re-look-up ``obj_factory`` by qualname from its
    defining module's *current* attributes, instead of using the frozen
    object reference. This means that if the module has since been
    ``importlib.reload()``-ed - see ``Namespace.reinitialize(reload_modules=
    True)`` - the next build picks up the new code.

    Falls back to the original object verbatim (today's behavior, unchanged)
    whenever it doesn't look like a plain, importable module-level
    definition: a lambda, a bound method, a class/function defined inside a
    function (``__qualname__`` containing ``<locals>``), a ``functools.
    partial``, or anything else missing ``__module__``/``__qualname__``.
    """
    mod_name = getattr(obj_factory, "__module__", None)
    qualname = getattr(obj_factory, "__qualname__", None)
    if not mod_name or not qualname or "<locals>" in qualname:
        return obj_factory
    try:
        resolved = import_module(mod_name)
        for part in qualname.split("."):
            resolved = getattr(resolved, part)
        return resolved
    except Exception:
        return obj_factory


class Component:
    def __init__(self, namestring):
        self.name = namestring


class NamespaceComponent:
    """Lazy cross-reference to another namespace item (or a sub-attribute of
    one), e.g. ``NamespaceComponent(namespace, "las.delaystage_pump")``.

    The namespace lookup that splits ``namestring`` into "registered item
    name" + "sub-attribute path" is deferred to first use (inside `get()`,
    itself already only called once the *consuming* item's own lazy
    construction actually reaches for this argument - see
    `replace_NamespaceComponents`), not done in `__init__`. This means
    constructing a `NamespaceComponent` never requires the name it points to
    to be registered *yet* - only by the time it's actually used - so which
    module registers which name, or in what order, doesn't matter as long as
    everything is registered before anything is actually built.
    """

    def __init__(self, namespace, namestring, get_current_value=False):
        self.namespace = namespace
        self.namestring = namestring
        self._get_current_value = get_current_value
        self.obj_name = None
        self.sub_name = None

    def _resolve(self):
        if self.obj_name is not None:
            return
        comps = self.namestring.split(".")
        for n, comp in enumerate(comps):
            tn = ".".join(comps[: n + 1])
            if tn in self.namespace.all_names:
                self.obj_name = tn
                self.sub_name = ".".join(comps[n + 1 :])
                return
        raise KeyError(
            f"NamespaceComponent: could not find '{self.namestring}' in "
            f"namespace '{self.namespace.name}' (checked on first use, not "
            f"at construction - make sure whatever registers it has run by "
            f"now)"
        )

    def get(self):
        self._resolve()
        obj = self.namespace.get_obj(self.obj_name)
        if self._get_current_value:
            if self.sub_name:
                return eval(f"obj.{self.sub_name}").get_current_value()
            else:
                return obj.get_current_value()

        else:
            if self.sub_name:
                return eval(f"obj.{self.sub_name}")
            else:
                return obj


def replace_NamespaceComponents(*args, **kwargs):
    args_out = []
    kwargs_out = {}

    for arg in args:
        if isinstance(arg, NamespaceComponent):
            args_out.append(Proxy(arg.get))
        else:
            args_out.append(arg)
            pass
    for name, value in kwargs.items():
        if isinstance(value, NamespaceComponent):
            kwargs_out[name] = Proxy(value.get)
        else:
            kwargs_out[name] = value

    return args_out, kwargs_out


def init_name_obj(obj, args, kwargs, name=None):
    try:
        return obj(*args, **kwargs, name=name)
    except TypeError:
        return obj(*args, **kwargs)


def _repr_without_initializing(value):
    """repr() for the manual-instantiation hint, minus the side effects.

    This string is built on *every* component initialization, and its
    arguments routinely include other namespace components that are still
    lazy proxies. ``Proxy.__repr__`` resolves - i.e. constructs the real
    device - so formatting a diagnostic string silently initialized whatever
    the component was passed. Caught by a SIGUSR1 thread dump on the status
    server: building ``att`` was stuck inside ``format_manual_instantiation``
    -> ``Proxy.__repr__`` -> ``init_local``, constructing ``xp``.

    ``Proxy.__class__`` is already shy about this (see :class:`Proxy`), so
    ``isinstance`` here cannot resolve anything either; ``__resolved__``
    reports whether the factory has run without triggering it.
    """
    try:
        if isinstance(value, Proxy_orig) and not object.__getattribute__(
            value, "__resolved__"
        ):
            return "<namespace component, not yet initialized>"
    except Exception:
        pass
    try:
        return repr(value)
    except Exception as exc:
        return f"<unreprable {type(value).__name__}: {exc}>"


def format_manual_instantiation(
    obj_factory, args, kwargs, name=None, accepts_name=False, module_name=None
):
    if callable(obj_factory):
        obj_name = getattr(obj_factory, "__name__", str(obj_factory))
        import_path = module_name or getattr(obj_factory, "__module__", None)
        if import_path:
            import_stmt = f"from {import_path} import {obj_name}"
        else:
            import_stmt = obj_name
    else:
        obj_name = str(obj_factory)
        import_stmt = obj_name

    call_kwargs = dict(kwargs)
    if accepts_name:
        call_kwargs["name"] = name

    call_parts = [_repr_without_initializing(arg) for arg in args] + [
        f"{key}={_repr_without_initializing(value)}"
        for key, value in call_kwargs.items()
    ]
    call_text = f"{obj_name}({', '.join(call_parts)})"

    return f"For manual instantiation copy/paste: {import_stmt}; {call_text}"


def append_manual_context(exc, manual_context):
    if manual_context and manual_context not in exc.args:
        exc.args = exc.args + (manual_context,)
    return exc


def init_device(type_string, name, args=[], kwargs={}, verbose=True, lazy=True):
    if verbose:
        print(("Configuring %s " % (name)).ljust(25), end="")
        sys.stdout.flush()
    imp_p, type_name = type_string.split(sep=":")
    imp_p = imp_p.split(sep=".")
    if verbose:
        print(("(%s)" % (type_name)).ljust(25), end="")
        sys.stdout.flush()
    try:
        tg = importlib.import_module(".".join(imp_p)).__dict__[type_name]

        if lazy:
            tdev = Proxy(partial(init_name_obj, tg, args, kwargs, name=name))
            if verbose:
                print((_color.YELLOW + "LAZY" + _color.RESET).rjust(5))
                sys.stdout.flush()
        else:
            tdev = init_name_obj(tg, args, kwargs, name=name)
            if verbose:
                print((_color.GREEN + "OK" + _color.RESET).rjust(5))
                sys.stdout.flush()
        return tdev
    except Exception as expt:
        # tb = traceback.format_exc()
        if verbose:
            print((_color.RED + "FAILED" + _color.RESET).rjust(5))
            # print(sys.exc_info())
        raise expt


def get_dependencies(inp):
    outp = []
    if isinstance(inp, dict):
        inp = inp.values()
    for ta in inp:
        if isinstance(ta, Component):
            outp.append(ta.name)
        elif isinstance(ta, dict) or isinstance(ta, list):
            outp.append(get_dependencies(ta))
    return outp


def replaceComponent(inp, dict_all, config_all, lazy=False):
    if isinstance(inp, list):
        outp = []
        for ta in inp:
            if isinstance(ta, Component):
                if ta.name in dict_all.keys():
                    outp.append(dict_all[ta.name])
                else:
                    ind = [ta.name == tca["name"] for tca in config_all].index(True)
                    outp.append(
                        initFromConfigList(
                            config_list[ind : ind + 1], config_all, lazy=lazy
                        )
                    )
            elif isinstance(ta, dict) or isinstance(ta, list):
                outp.append(replaceComponent(ta, dict_all, config_all, lazy=lazy))
            else:
                outp.append(ta)
    elif isinstance(inp, dict):
        outp = {}
        for tk, ta in inp.items():
            if isinstance(ta, Component):
                if ta.name in dict_all.keys():
                    outp[tk] = dict_all[ta.name]
                else:
                    ind = [tk.name == tca["name"] for tca in config_all].index(True)
                    outp[tk] = initFromConfigList(
                        config_list[ind : ind + 1], config_all, lazy=lazy
                    )
            elif isinstance(ta, dict) or isinstance(ta, list):
                outp[tk] = replaceComponent(ta, dict_all, config_all, lazy=lazy)
            else:
                outp[tk] = ta
    else:
        return inp
    return outp


def initFromConfigList(config_list, config_all, lazy=False):
    op = {}
    for td in config_list:
        # args = [op[ta.name] if isinstance(ta, Component) else ta for ta in td["args"]]
        # kwargs = {
        # tkwk: op[tkwv.name] if isinstance(tkwv, Component) else tkwv
        # for tkwk, tkwv in td["kwargs"].items()
        # }
        try:
            tlazy = td["lazy"]
        except:
            tlazy = lazy
        op[td["name"]] = init_device(
            td["type"],
            td["name"],
            replaceComponent(td["args"], op, config_all, lazy=lazy),
            replaceComponent(td["kwargs"], op, config_all, lazy=lazy),
            lazy=tlazy,
        )
    return op


class Configuration:
    """Configuration collector object collecting important settings for arbitrary use,
    linking to one or few standard config files in the file system. Sould also be used
    for config file writing."""

    def __init__(self, configFile, name=None):
        self.name = name
        self.configFile = Path(configFile)
        self._config = {}
        if self.configFile:
            self.readConfigFile()

    def readConfigFile(self):
        self._config = loadConfig(self.configFile)
        assert (
            type(self._config) is dict
        ), f"Problem reading {self.configFile} json file, seems not to be a valid dictionary structure!"
        # self.__dict__.update(self._config)

    def __setitem__(self, key, item):
        self._config[key] = item
        # self.__dict__.update(self._config)
        self.saveConfigFile()

    def __getitem__(self, key):
        return self._config[key]

    def saveConfigFile(self, filename=None, force=False):
        if not filename:
            filename = self.configFile
        if (not force) and filename.exists():
            if (
                not input(
                    f"File {filename.absolute().as_posix()} exists,\n would you like to overwrite? (y/n)"
                ).strip()
                == "y"
            ):
                return
        writeConfig(filename, self._config)

    def _ipython_key_completions_(self):
        return list(self._config.keys())

    def __repr__(self):
        return json.dumps(self._config, indent=4)


def loadConfig(fina):
    with open(fina, "r") as f:
        return json.load(f)


def writeConfig(fina, obj):
    # shared configuration tree -- see eco.utilities.datafiles
    with open_group_writable(fina, "w") as f:
        json.dump(obj, f, indent=4)


class ChannelList(list):
    def __init__(self, *args, **kwargs):
        self.file_name = kwargs.pop("file_name")
        # list.__init__(*args,**kwargs)
        self.load()

    def load(self):
        self.clear()
        self.extend(parseChannelListFile(self.file_name))


def parseChannelListFile(fina):
    out = []
    with open(fina, "r") as f:
        done = False
        while not done:
            d = f.readline()
            if not d:
                done = True
            if len(d) > 0:
                if not d.isspace():
                    if not d[0] == "#":
                        out.append(d.strip())
    return out


def append_to_path(*args):
    for targ in args:
        sys.path.append(targ)


def prepend_to_path(*args):
    for targ in args:
        sys.path.insert(0, targ)


class Terminal:
    def __init__(self, title="eco", scope=None):
        self.title = title
        self.scope = scope

    @property
    def user(self):
        return getpass.getuser()

    @property
    def host(self):
        return socket.gethostname()

    @property
    def user(self):
        return getpass.getuser()

    def get_string(self):
        s = f"{self.title}"
        if self.scope:
            s += f"-{self.scope}"
        s += f" ({self.user}@{self.host})"
        return s

    def set_title(self, extension=""):
        print(colorama.ansi.set_title("♻️ " + self.get_string() + extension))


class IsInitialisingError(Exception):
    """Raised exception when an object is already initializing.

    Args:
        Exception (_type_): _description_
    """

    pass


class _ThreadRoutedOutput:
    """Route stdout/stderr writes made *from registered threads* into a sink
    (a temp file), while writes from every other thread - notably the
    interactive main thread - pass through to the real streams untouched.

    Used by ``Namespace.init_all(silent=...)`` to collect the noisy
    per-component initialization chatter produced by the pool worker threads
    into a log file instead of scrolling the user's session, without hiding
    anything the main thread prints.

    Only threads registered via :attr:`initializer` (passed as the
    ``ThreadPoolExecutor(initializer=...)``) are routed. The thread that
    drives ``init_all`` is intentionally *not* registered, so its progress
    bar / summary lines still reach the terminal. Threads that a device
    ``__init__`` spawns on its own are likewise not registered, so their
    output can still leak - same caveat as the existing "silent" mode.

    ``sys.stdout``/``sys.stderr`` are process-global; routing is done per
    thread id so installing the proxy from a background init thread never
    swallows what the main (ipython) thread prints concurrently.

    EPICS/libca caveat and its fix: Channel Access messages (connection
    warnings, ``CA.Client.Exception``, virtual-circuit disconnects, ...) are
    printed by the C library straight to OS file descriptor 2, bypassing the
    Python ``sys.stderr`` proxy entirely. When ``capture_ca=True`` (the
    default) this also redirects libca's message stream into the sink via
    ``epics.ca.replace_printf_handler`` for the duration of the ``with``
    block, and restores the default (messages -> stderr) on exit. That hook
    is *process-global* while installed - there is no per-thread libca
    handler - but it is only active during the init run, so normal CA error
    reporting resumes as soon as initialization finishes. Degrades silently
    (Python-level capture only) if epics is unavailable or too old to expose
    the hook.

    ``logging`` caveat and its fix: a ``logging.StreamHandler`` holds the
    stream *object* it was built with, so swapping ``sys.stderr`` afterwards
    does not affect it at all - and something in the bernina import chain
    (``sf_databuffer.bufferutils``) calls ``logging.basicConfig`` at import
    time, installing exactly such a root handler on the original stderr at
    level INFO. Every ``logger.*`` call therefore bypassed the stream proxy
    entirely (eco's own alias warnings, paramiko's SSH handshake at INFO,
    ...). ``capture_logging=True`` (the default) handles that through
    logging's own API instead: a handler writing into the same sink is added
    to the root logger, and a thread-id filter is put on the *existing* root
    handlers so records emitted from registered threads no longer reach them.
    Same per-thread principle as the stream proxy, so records logged from the
    main thread are untouched.

    ``sys.stdout`` caveat that cannot be fixed here: prompt_toolkit's
    ``patch_stdout`` (which IPython wraps its prompt in) replaces
    ``sys.stdout``/``sys.stderr`` with a proxy that writes straight to the
    terminal, for the whole time you sit at the prompt. A ``background=True``
    pass therefore runs with *its* proxy installed, not ours, and plain
    ``print()`` from a worker reaches the terminal regardless of what this
    class does. That is why the noisy call sites in `Assembly._append` /
    `Namespace.append_obj` were converted to `logger.warning` - the logging
    route above is immune to it. Bare `print()` in third-party libraries
    (diffcalc's "Recalculating UB matrix.") still leaks, unavoidably.
    """

    def __init__(
        self,
        sink_path=None,
        enabled=True,
        name="namespace",
        capture_ca=True,
        capture_logging=True,
    ):
        self.enabled = enabled
        self.capture_ca = capture_ca
        self.capture_logging = capture_logging
        self.path = None
        self._sink = None
        self._closed = False
        self._routed = set()
        self._lock = Lock()
        self._orig_stdout = None
        self._orig_stderr = None
        self._proxy_stdout = None
        self._proxy_stderr = None
        self._ca_installed = False
        self._log_handler = None
        self._log_filtered = []
        if enabled:
            if sink_path in (True, None):
                fh = tempfile.NamedTemporaryFile(
                    mode="w",
                    delete=False,
                    prefix=f"eco_init_{name}_",
                    suffix=".log",
                )
                self.path = fh.name
                self._sink = fh
            else:
                self.path = str(sink_path)
                self._sink = open(self.path, "w")

    @property
    def initializer(self):
        """Pass as ThreadPoolExecutor(initializer=...) to route that pool's
        worker threads into the sink. ``None`` (a no-op) when disabled."""
        return self._register if self.enabled else None

    def _register(self):
        with self._lock:
            self._routed.add(get_ident())

    def _write_sink(self, s):
        with self._lock:
            if self._closed:
                # A thread the pass spawned can outlive the pass (observed:
                # smaract stage warnings arriving after the summary). Writing
                # to the closed sink would raise ValueError *inside that
                # device's thread*, so fall back to the real stream instead -
                # late output on the terminal is the lesser evil.
                real = self._orig_stderr or sys.__stderr__
                return real.write(s) if real is not None else len(s)
            return self._sink.write(s)

    def _make_proxy(self, real):
        router = self

        class _Proxy(io.TextIOBase):
            def writable(self):
                return True

            def write(self, s):
                if get_ident() in router._routed:
                    return router._write_sink(s)
                return real.write(s)

            def flush(self):
                try:
                    real.flush()
                except Exception:
                    pass

            def isatty(self):
                return getattr(real, "isatty", lambda: False)()

            def fileno(self):
                return real.fileno()

        return _Proxy()

    def _ca_message(self, msg):
        # libca invokes this (possibly from its own C threads) with each
        # Channel Access message that would otherwise hit stderr.
        try:
            self._write_sink(msg)
        except Exception:
            pass

    def _install_ca_capture(self):
        """Divert libca's message stream into the sink for the duration of
        the capture. Best-effort: no-op if epics is missing or too old."""
        try:
            import epics.ca as _ca

            if hasattr(_ca, "replace_printf_handler"):
                _ca.replace_printf_handler(self._ca_message)
                self._ca_installed = True
        except Exception:
            self._ca_installed = False

    def _restore_ca_capture(self):
        if not self._ca_installed:
            return
        try:
            import epics.ca as _ca

            # calling with no argument restores libca's default handler,
            # which writes messages to sys.stderr again.
            _ca.replace_printf_handler()
        except Exception:
            pass
        finally:
            self._ca_installed = False

    def _install_logging_capture(self):
        """Route log records emitted from registered threads into the sink,
        instead of to whatever handlers are already installed.

        Two halves, because a handler owns its stream: our own handler is
        added to the root logger to *write* those records, and a filter is
        put on the pre-existing root handlers to *stop* them writing the same
        records to the terminal. Both are keyed on `record.thread`, which is
        the emitting thread's ident - the same set the stream proxy uses - so
        anything logged from the main thread is left completely alone.

        Known gaps, all the same shape: a handler added *after* this runs, and
        a non-root logger with `propagate=False` and its own handler, are not
        filtered. Best-effort by design - it must never be able to break
        logging for the session.
        """
        router = self

        class _RoutedOnly(logging.Filter):
            def filter(self, record):
                return record.thread in router._routed

        class _NotRouted(logging.Filter):
            def filter(self, record):
                return record.thread not in router._routed

        class _SinkHandler(logging.Handler):
            def emit(self, record):
                try:
                    router._write_sink(self.format(record) + "\n")
                except Exception:
                    pass

        try:
            root = logging.getLogger()
            handler = _SinkHandler()
            handler.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
            handler.addFilter(_RoutedOnly())
            for existing in list(root.handlers):
                filt = _NotRouted()
                existing.addFilter(filt)
                self._log_filtered.append((existing, filt))
            root.addHandler(handler)
            self._log_handler = handler
        except Exception:
            self._log_handler = None

    def _restore_logging_capture(self):
        try:
            if self._log_handler is not None:
                logging.getLogger().removeHandler(self._log_handler)
        except Exception:
            pass
        finally:
            self._log_handler = None
        for existing, filt in self._log_filtered:
            try:
                existing.removeFilter(filt)
            except Exception:
                pass
        self._log_filtered = []

    def __enter__(self):
        if self.enabled:
            self._orig_stdout = sys.stdout
            self._orig_stderr = sys.stderr
            self._proxy_stdout = self._make_proxy(self._orig_stdout)
            self._proxy_stderr = self._make_proxy(self._orig_stderr)
            sys.stdout = self._proxy_stdout
            sys.stderr = self._proxy_stderr
            if self.capture_ca:
                self._install_ca_capture()
            if self.capture_logging:
                self._install_logging_capture()
        return self

    def __exit__(self, *exc):
        if self.enabled:
            # Restore the real streams first, then hand libca back its default
            # handler so replace_printf_handler() rebinds to the real stderr
            # rather than to the proxy we are about to detach.
            #
            # Only restore if our proxy is still the installed one: something
            # else may have swapped the streams while the pass ran (IPython's
            # prompt does exactly this via prompt_toolkit's patch_stdout, on
            # every prompt), and blindly assigning _orig_stdout back would
            # clobber *its* proxy rather than ours.
            for attr, proxy_attr, orig in (
                ("stdout", "_proxy_stdout", self._orig_stdout),
                ("stderr", "_proxy_stderr", self._orig_stderr),
            ):
                if getattr(sys, attr, None) is getattr(self, proxy_attr, None):
                    setattr(sys, attr, orig)
            self._restore_ca_capture()
            self._restore_logging_capture()
            try:
                self._sink.flush()
            except Exception:
                pass
            with self._lock:
                self._closed = True
                try:
                    self._sink.close()
                except Exception:
                    pass
        return False


def _resolved_object(obj):
    """The real object behind a lazy `Proxy`, or None if it hasn't been built.

    Deliberately never forces a build: reading `__wrapped__` on an unresolved
    proxy *runs the factory*, which for a summary/reporting path would mean
    initializing the very components we are reporting as not initialized.
    `__resolved__` is readable without triggering anything; a non-proxy has no
    such attribute and is returned as-is.
    """
    try:
        resolved = object.__getattribute__(obj, "__resolved__")
    except AttributeError:
        return obj
    if not resolved:
        return None
    return object.__getattribute__(obj, "__wrapped__")


def _failed_subcomponents(obj, _prefix="", _depth=0, _max_depth=6):
    """Dotted paths of the sub-components that failed inside `obj`.

    `Assembly._append` records an optional child's failure in
    `_failed_appends`, and re-records it one level up as an
    `IncompleteInitialisationError` for every ancestor, so a top-level
    namespace component only ever names its *direct* child. Recursing through
    those re-recorded errors turns `xrd -> det -> pv_x` back into the leaf that
    actually failed, which is the useful half of the information.

    Returns [] for anything that isn't an assembly with failures, including a
    still-lazy proxy (see `_resolved_object`).
    """
    obj = _resolved_object(obj)
    if obj is None:
        return []
    failed = getattr(obj, "_failed_appends", None)
    if not failed:
        return []
    out = []
    for child_name, exc in failed.items():
        path = f"{_prefix}{child_name}"
        nested = []
        if isinstance(exc, IncompleteInitialisationError) and _depth < _max_depth:
            try:
                child = object.__getattribute__(obj, "__dict__").get(child_name)
            except AttributeError:
                child = None
            if child is not None:
                nested = _failed_subcomponents(
                    child, path + ".", _depth + 1, _max_depth
                )
        out.extend(nested or [path])
    return out


def _short_exception(exc, maxlen=90):
    """One-line `TypeError: message` rendering for a summary line.

    Reads `args[0]` rather than `str(exc)`: `append_manual_context` appends
    the (multi-line, deliberately verbose) manual-instantiation hint as an
    extra arg, and `str()` on a multi-arg exception renders the whole tuple -
    which is exactly what you don't want on a one-line summary. The full
    exception stays available via `failed_items_excpetion`.
    """
    if exc is None:
        return "no error recorded"
    msg = " ".join(str(exc.args[0] if getattr(exc, "args", None) else exc).split())
    if len(msg) > maxlen:
        msg = msg[: maxlen - 1] + "…"
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


class GivenUpInitialisationError(Exception):
    """Recorded for a name that never failed outright but was still reporting
    an in-progress initialization when init_all() hit its `N_cycles` cap."""


class Namespace(Assembly):
    def __init__(
        self,
        name=None,
        root_module=None,
        alias_namespace=None,
        required_names=[],
        required_names_directory=None,
    ):
        super().__init__(name)
        # self.name = name
        self.lazy_items = {}
        self.initialized_items = {}
        self.failed_items = {}
        self.failed_items_excpetion = {}
        self.initialisation_times_lazy = {}
        self.initialisation_times = {}
        self._initialisation_start_time = {}
        self._init_priority = {}

        # name -> [NamespaceComponent, ...] found among the args/kwargs it
        # was registered with, i.e. the other namespace items it declares a
        # dependency on. Recorded at append_obj time (the only moment the
        # arguments are visible as objects rather than already-substituted
        # proxies) so init_all can order the pass by dependency instead of
        # letting every consumer race to build the same shared component.
        self._declared_dependencies = {}

        # name -> {"module_name": str|None, "obj_factory": callable|str}, as
        # passed to append_obj. Lets reinitialize(reload_modules=True) find
        # which module to importlib.reload() for a given name; see
        # _resolve_reloadable_factory() for how a directly-passed (no
        # module_name=) factory is re-looked-up after a reload.
        self._factory_info = {}

        self.names_without_alias = []
        self._initializing = []

        # Only used when init_all(..., background=True) has been used at least
        # once; makes threads waiting on a name that is already being
        # initialized wake up as soon as it is done instead of polling every
        # few seconds. Off by default, does not affect any existing behavior.
        self._responsive_locking = False
        self._init_events = {}
        self._background_init_thread = None

        # Names the last init_all() pass targeted; used by init_progress().
        self._init_target_names = set()
        # Path of the log file the most recent silent init_all() pass routed
        # the per-component chatter into; None if none was captured yet.
        self.last_init_log_path = None

        self.root_module = root_module
        self.alias_namespace = alias_namespace
        if required_names_directory:
            self._append(
                AdjustableFS,
                required_names_directory,
                name="required_names",
                is_setting=True,
                is_display=True,
            )
        else:
            self._append(
                AdjustableMemory,
                [],
                name="required_names",
                is_setting=True,
                is_display=True,
            )

    @property
    def initialisation_times_sorted(self):
        return dict(sorted(self.initialisation_times.items(), key=lambda w: w[1]))

    @property
    def initialisation_times_lazy_sorted(self):
        return dict(sorted(self.initialisation_times_lazy.items(), key=lambda w: w[1]))

    @property
    def initialized_names(self):
        return set(self.initialized_items.keys())

    @property
    def lazy_names(self):
        return set(self.lazy_items.keys())

    @property
    def failed_names(self):
        return set(self.failed_items.keys())

    @property
    def failed_items_exception(self):
        """Correctly-spelled alias of `failed_items_excpetion` (which stays
        the canonical attribute, since it is what everything already writes
        to and what the user-facing hint messages name)."""
        return self.failed_items_excpetion

    @property
    def failed_items_exception_prop(self):
        return self.failed_items_exception

    @property
    def all_names(self):
        return self.initialized_names | self.lazy_names | self.failed_names

    def resolve_item(self, name):
        """The actual object registered as `name` on this namespace -- a
        lazy proxy, a failed-but-still-accessible item, or a fully
        initialized object. NOT a bare attribute on this Namespace
        instance itself -- append_obj instead writes it onto
        sys.modules[self.root_module] (so that `from eco.<scope> import *`
        exposes it as a bare name); this is the supported way to look one
        up by name from the Namespace object directly, e.g. for a UI
        browsing this namespace's registered names. Same
        lazy-or-failed-or-initialized resolution reinitialize() itself
        uses internally.

        Deliberately membership tests, not `a.get(n) or b.get(n)`: `or`
        evaluates truthiness, and truthiness of a still-lazy Proxy resolves
        it - i.e. builds the real device just to look it up, and re-raises
        the stored exception for an item that previously failed. (A
        FailedComponent is falsy too, so the chain would also silently skip
        past it.) Found the hard way: it made the status server's
        "reinitialize whatever failed" call raise the very failure it was
        trying to clear."""
        for items in (self.lazy_items, self.failed_items, self.initialized_items):
            if name in items:
                return items[name]
        return None

    def _timeout_error(self, name, init_timeout, factory_desc=""):
        """Build a helpful exception for the lazy-init waiting/timeout path.

        If the thread that actually ran the initialization meanwhile recorded a
        concrete failure, surface *that* exception (with its manual-instantiation
        context) instead of a bland "timed out". Otherwise return an
        IsInitialisingError that says what was being built and how to jump into
        its context to debug it.
        """
        if name in self.failed_names and name in self.failed_items_excpetion:
            exc = self.failed_items_excpetion[name]
            if isinstance(exc, BaseException):
                return exc
        elapsed = time() - self._initialisation_start_time.get(name, time())
        msg = (
            f"NB: {name} initialization timed out after {elapsed:.0f}s "
            f"(limit {init_timeout}s); another thread is still initializing it "
            f"or it got stuck.\n"
            f"    building: {factory_desc}\n"
            f"    to retry from scratch:  <namespace>.move_failed_to_lazy('{name}') "
            f"then access '{name}' again\n"
            f"    to inspect the failure: <namespace>.failed_items_excpetion.get('{name}')"
        )
        return IsInitialisingError(msg)

    def move_failed_to_lazy(self, *names):
        if not names:
            names = self.failed_names
        for name in names:
            self.lazy_items[name] = self.failed_items.pop(name)
            try:
                self.failed_items_excpetion.pop(name)
            except KeyError:
                pass
            try:
                self._initializing.pop(self._initializing.index(name))
            except KeyError:
                pass
            except ValueError:
                pass

    def reinitialize(
        self, *names, verbose=True, raise_errors=False, reload_modules=False
    ):
        """Rebuild namespace item(s) from scratch at runtime, reusing their
        originally-provided factory/args/kwargs. Useful after fixing a remote
        condition (an IOC restarted, a service came back up) - e.g. a device
        that failed to initialize, or came up only partially.

        The rebuild reuses the *same* lazy proxy object: it resets the proxy's
        cache and re-runs the original construction, so references that resolve
        through the namespace - the module attribute (``b.<name>``) and
        NamespaceComponent-based cross-references - transparently follow to the
        rebuilt instance. References captured as *direct handles* to the old
        object (or its sub-parts) are NOT updated; those callers must re-fetch.

        Before rebuilding, best-effort tears down the outgoing instance by
        calling whichever of ``close``/``disconnect``/``stop`` it has, so
        open connections or background threads from the old instance don't
        linger past the rebuild.

        reload_modules : bool
            If True, ``importlib.reload()`` the module each name's factory
            was defined in *before* rebuilding, so edits made to that
            module's source since the namespace was built are picked up.
            Works for names registered either as ``append_obj("ClassName",
            ..., module_name="pkg.module")`` or with the class/function
            passed directly (late-resolved via ``_resolve_reloadable_
            factory``, as long as it's a plain module-level class/function -
            not a lambda, bound method, or locally-defined def). Only ever
            do this on a device you know is quiescent (not mid-move, not
            mid-scan): it is opt-in on purpose, so a device's code never
            changes under you by accident. Caveats inherent to Python
            module reload still apply: a module with import-time side
            effects beyond class/function definitions is riskier to reload,
            and a subclass defined in a *different*, not-reloaded module
            keeps inheriting from the pre-reload class. A namespace item
            whose class is defined directly in this namespace's own
            ``root_module`` (e.g. a class written straight into bernina.py,
            rather than imported from a dedicated device module - true for
            a handful of bernina's ~120 top-level items) is never reloaded
            by this, even when asked: reloading that module means
            re-running the entire beamline-assembly script, not one
            device's code, so it is rebuilt with the existing code instead
            and a message says so.

        With no names given, rebuilds everything currently failed. Returns
        ``{name: succeeded_bool}`` (True == ended up fully initialized).
        """
        if not names:
            names = tuple(self.failed_names)
        results = {}
        reloaded_modules = set()
        for name in names:
            # see resolve_item(): membership tests, never `or` - truthiness
            # of a lazy proxy resolves it and re-raises a stored failure,
            # which is exactly the case reinitialize() exists to fix.
            proxy = self.resolve_item(name)
            if proxy is None:
                raise KeyError(f"'{name}' is not a known namespace item.")

            if reload_modules:
                info = self._factory_info.get(name, {})
                mod_name = info.get("module_name") or getattr(
                    info.get("obj_factory"), "__module__", None
                )
                if mod_name and mod_name == self.root_module:
                    # Some namespace entries are classes defined directly in
                    # the namespace-assembly module itself (root_module,
                    # e.g. bernina.py) rather than in a dedicated device
                    # module. Reloading that module would re-execute the
                    # ENTIRE assembly script (hundreds of other append_obj
                    # calls, module-level side effects) - categorically
                    # different from reloading one device driver, and not
                    # what reload_modules is for. Refuse and rebuild with
                    # the existing code instead of silently doing that.
                    if verbose:
                        print(
                            f"Not reloading '{mod_name}' for '{name}': that is "
                            "this namespace's own assembly module (root_module), "
                            "not a dedicated device module - reloading it would "
                            "re-run the entire beamline setup script. Move the "
                            "class into its own module (like most other devices "
                            "already are) to make it hot-reloadable, or restart "
                            f"the session for changes to it. Rebuilding '{name}' "
                            "with the existing code."
                        )
                elif mod_name and mod_name in sys.modules:
                    if mod_name not in reloaded_modules:
                        try:
                            # Force a real recompile from source: reload()
                            # alone can silently keep serving a *stale*
                            # cached .pyc if the edited file happens to match
                            # the old one's size and land in the same
                            # mtime-resolution tick (very plausible for a
                            # quick edit-reload loop, or on network
                            # filesystems with coarse mtime granularity) -
                            # confirmed to actually happen, not theoretical.
                            mod = sys.modules[mod_name]
                            cache_path = getattr(mod, "__file__", None)
                            if cache_path:
                                try:
                                    os.remove(
                                        importlib.util.cache_from_source(cache_path)
                                    )
                                except FileNotFoundError:
                                    pass
                            importlib.reload(mod)
                            reloaded_modules.add(mod_name)
                            if verbose:
                                print(f"Reloaded module '{mod_name}' for '{name}'.")
                        except Exception as exc:
                            if verbose:
                                print(
                                    f"Could not reload module '{mod_name}' for "
                                    f"'{name}' ({exc}); rebuilding with existing code."
                                )
                elif verbose:
                    print(
                        f"Could not determine a reloadable module for '{name}'; "
                        "rebuilding with existing code."
                    )

            # remove any existing (old/partial) registration so the rebuild
            # does not leave duplicate aliases / status-collection entries.
            old = self.__dict__.get(name)
            if old is not None:
                # best-effort teardown so a live device (open CA channels,
                # background monitor threads, ...) doesn't leak past the
                # rebuild; silently skipped if the object has none of these.
                for teardown_name in ("close", "disconnect", "stop"):
                    teardown = getattr(old, teardown_name, None)
                    if callable(teardown):
                        try:
                            teardown()
                        except Exception:
                            pass
                        break
                try:
                    self.status_collection.remove(old)
                except Exception:
                    pass
                try:
                    self.alias.pop_object(old.alias)
                except Exception:
                    pass
                self.__dict__.pop(name, None)

            # reset the lazy proxy so the SAME proxy re-resolves to a fresh
            # instance on next access (this is what preserves proxy-based
            # references across the rebuild).
            try:
                del proxy.__wrapped__
            except Exception:
                pass

            # ensure the item sits in lazy_items and is not short-circuited by
            # a recorded failure, then trigger the rebuild.
            if name in self.failed_names:
                self.move_failed_to_lazy(name)
            elif name in self.initialized_names:
                self.lazy_items[name] = self.initialized_items.pop(name)
            self.init_name(name, verbose=verbose, raise_errors=raise_errors)
            results[name] = name in self.initialized_names
            if verbose and not results[name]:
                print(
                    f"'{name}' is still not fully initialized after reinitialize; "
                    f"see namespace.failed_items_excpetion.get('{name}')."
                )
        return results

    # --- physical manual-control box (Raspberry Pi pendant) ---------------
    def start_eco_control_box(
        self,
        box_host="ecobox",
        port=8791,
        token=None,
        token_file="~/.eco/pendant_token",
        **box_kwargs,
    ):
        """Offer this namespace to the physical manual-control box.

            bernina.namespace.start_eco_control_box()

        The box listens and we call it, so this works from any console: the
        operator standing at the box gets an "accept / take over" prompt and
        decides. If they hand the box to someone else later, this session's
        connection closes itself and says so.

        Idempotent: returns the session already running here rather than
        opening a second one. Stop it with `.stop_eco_control_box()`.
        """
        from eco.manual_control.remote.serve import connect_to_box

        existing = getattr(self, "_control_box_server", None)
        if existing is not None and not existing._stop.is_set():
            print(f"control box session already running: {existing!r}")
            return existing

        session = connect_to_box(
            self, root_name=self.name, host=box_host, port=port,
            token=token, token_file=token_file, **box_kwargs
        )
        self._control_box_server = session
        return session

    def stop_eco_control_box(self):
        """Stop this session's connection to the manual-control box."""
        session = getattr(self, "_control_box_server", None)
        if session is None:
            print("no control box session running here")
            return
        session.stop()
        self._control_box_server = None
        print("control box session stopped")

    def select_required_names(self):

        nonex_reqnames = set(self.required_names()).difference(self.all_names)
        if nonex_reqnames:
            print(
                f"WARNING: The following required names do not exist in namespace {self.name} \n and will be removed from required names: {nonex_reqnames}"
            )
            pres_req = set(self.required_names()) - nonex_reqnames
        else:
            pres_req = set(self.required_names())

        if _is_notebook():
            try:
                return self._select_required_names_notebook(pres_req)
            except Exception as exc:
                print(
                    "Notebook selection UI failed; falling back to terminal mode:",
                    exc,
                )

        terminal_menu = TerminalMenu(
            sorted(self.all_names),
            multi_select=True,
            show_multi_select_hint=True,
            preselected_entries=list(pres_req),
            title="Select required names for namespace %s" % self.name,
        )
        selected_indices = terminal_menu.show()
        selected_names = terminal_menu.chosen_menu_entries
        if selected_names:
            self.required_names(list(selected_names))

    def _select_required_names_notebook(self, preselected_names):
        try:
            from IPython.display import display
            import ipywidgets as widgets
        except Exception as exc:
            raise RuntimeError(
                "Notebook selection mode requires IPython and ipywidgets."
            ) from exc

        names = sorted(self.all_names)
        checkboxes = [
            widgets.Checkbox(
                value=(name in preselected_names),
                description=name,
                indent=False,
                layout=widgets.Layout(width="auto"),
            )
            for name in names
        ]
        select_all_button = widgets.Button(description="Select all")
        select_none_button = widgets.Button(description="Select none")
        apply_button = widgets.Button(description="Apply", button_style="success")
        status_label = widgets.HTML(value="")

        def _select_all(_):
            for checkbox in checkboxes:
                checkbox.value = True

        def _select_none(_):
            for checkbox in checkboxes:
                checkbox.value = False

        def _apply(_):
            selected_names = [cb.description for cb in checkboxes if cb.value]
            self.required_names(selected_names)
            status_label.value = f"<b>Applied required names:</b> {selected_names}"

        select_all_button.on_click(_select_all)
        select_none_button.on_click(_select_none)
        apply_button.on_click(_apply)

        box = widgets.VBox(
            [
                widgets.HTML(
                    value=f"<b>Select required names for namespace {self.name}</b>"
                ),
                *checkboxes,
                widgets.HBox(
                    [select_all_button, select_none_button, apply_button]
                ),
                status_label,
            ]
        )
        display(box)
        return box

    def init_name(self, name, verbose=True, raise_errors=False, quiet=False):
        # quiet fully suppresses per-item output, even if verbose=True; it
        # defaults to False so existing callers see unchanged output.
        starttime = time()
        try:
            titem = self.get_obj(name)
            if isinstance(titem, Proxy):
                # Proxy.__class__/__dir__ deliberately stay "shy" (report
                # LazyComponent / dir(LazyComponent)) while unresolved, so
                # that introspection - dir(), isinstance() against the
                # eventual wrapped type - can't accidentally trigger a real
                # device build (see Proxy's docstring). That means this
                # method can no longer use isinstance(titem,
                # InitialisationWaitable)/dir(titem) to *deliberately* force
                # resolution either - both now silently no-op on a still-lazy
                # proxy instead of building it, which used to leave every
                # name "successfully" init_name()'d without ever actually
                # leaving lazy_items (confirmed: init_all() reported success
                # while initialized_names stayed empty). __wrapped__ isn't
                # shy-cased, so accessing it forces the factory to run and
                # returns the real, resolved object.
                titem = titem.__wrapped__
            if isinstance(titem, InitialisationWaitable):
                titem._wait_for_initialisation()
            self.initialisation_times[name] = time() - starttime
            if verbose and not quiet:
                print(
                    ("Init %s " % (name)).ljust(40)
                    + f"{round(1000*(time()-starttime)): 6d} ms "
                    + (_color.GREEN + "OK" + _color.RESET).rjust(5)
                )
                sys.stdout.flush()

        except Exception as expt:
            self.initialisation_times[name] = time() - starttime
            if verbose and not quiet:
                print(
                    ("Init %s " % (name)).ljust(40)
                    + f"{round(1000*(time()-starttime)): 6d} ms "
                    + (_color.RED + "FAILED" + _color.RESET).rjust(5)
                )
            if raise_errors:
                raise expt

    def _default_init_log_path(self):
        """`~/.eco/init_logs/<namespace>_<timestamp>.log`, alongside the
        kernel logs written by eco.widgets.kernel_registry. Preferred over a
        /tmp temp file: it survives /tmp cleanup, is per-user by construction
        (so none of the cross-account permission trouble that fixed-name /tmp
        paths cause here), and stays greppable after the fact. Falls back to
        the old temp file if the directory can't be created."""
        from datetime import datetime

        try:
            d = Path.home() / ".eco" / "init_logs"
            d.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return str(d / f"{self.name or 'namespace'}_{stamp}.log")
        except Exception:
            return True  # -> _ThreadRoutedOutput picks a NamedTemporaryFile

    def _make_output_capture(self, silent):
        """Build the (possibly disabled) thread-routed output capture used by
        init_all(silent=...). Records the log path on the namespace so it can
        be read back later with read_init_log().

        `silent` is the single knob (there is no separate `capture_output`
        anymore): truthy means both "no chatter on the terminal" and "route
        that chatter into a log"; a str/Path chooses where."""
        if not silent:
            return _ThreadRoutedOutput(enabled=False)
        sink_path = silent if isinstance(silent, (str, Path)) else None
        if sink_path is None:
            sink_path = self._default_init_log_path()
        cap = _ThreadRoutedOutput(
            sink_path=sink_path,
            enabled=True,
            name=self.name or "namespace",
        )
        if cap.enabled:
            self.last_init_log_path = cap.path
        return cap

    def read_init_log(self):
        """Return the captured per-component init log from the most recent
        init_all(silent=...) call, or '' if none was captured."""
        path = getattr(self, "last_init_log_path", None)
        if not path:
            print("No captured init log (a silent=True init_all() writes one).")
            return ""
        try:
            with open(path) as fh:
                return fh.read()
        except FileNotFoundError:
            print(f"Captured init log no longer exists at {path}.")
            return ""

    def get_dependencies(self, name):
        """Names this item declared a dependency on, via a
        `NamespaceComponent` among its `append_obj` arguments.

        Only *declared* dependencies are visible here. A component that
        reaches into the namespace from inside its own `__init__` (rather
        than being handed a NamespaceComponent) is invisible to this, which
        is why dependency ordering is a scheduling hint and not a guarantee -
        see `_dependency_layers`.
        """
        out = set()
        for dep in self._declared_dependencies.get(name, ()):
            try:
                dep._resolve()
            except KeyError:
                # points at something never registered - not our problem
                # here, it will fail loudly when the item is actually built.
                continue
            if dep.obj_name is not None and dep.obj_name != name:
                out.add(dep.obj_name)
        return out

    def _dependency_layers(self, names, log=None):
        """Split `names` into successive layers such that everything a name
        declares a dependency on is in an earlier layer.

        The point is the shared-dependency stampede: at Bernina all nine
        EventReceivers take the same `event_master` NamespaceComponent, so a
        flat concurrent pass has up to `max_workers` of them reaching for it
        at once. One wins and builds it (a 256-PV `caget_many`); the rest sit
        in the lazy-init wait until `init_timeout` expires and come back as
        IsInitialisingError, to be retried a cycle later - which is both slow
        and, because all of that happens while channel access is saturated,
        the condition under which the EVRs' own reads start timing out.
        Building `event_master` in an earlier layer removes the collision
        entirely.

        Dependencies pointing outside `names` (already initialized, excluded,
        or never registered) impose no constraint. A dependency cycle cannot
        be layered, so the members of the cycle are emitted together as one
        layer and left to the existing retry loop - the same behaviour as
        before this method existed, applied to just the cyclic group.
        """
        remaining = set(names)
        edges = {n: self.get_dependencies(n) & remaining for n in remaining}
        layers = []
        placed = set()
        while remaining:
            ready = {n for n in remaining if not (edges[n] - placed)}
            if not ready:
                # cycle (or a dependency on something that can never be
                # placed): stop layering and hand the rest over as one group.
                if log is not None:
                    log(
                        f"dependency cycle among {len(remaining)} name(s), "
                        f"initializing them together: "
                        + ", ".join(sorted(remaining))
                    )
                layers.append(set(remaining))
                break
            layers.append(ready)
            placed |= ready
            remaining -= ready
        return layers

    def _select_names_to_init(self, required_only, exclude_names, log):
        """Which names this init_all() call needs to build, honoring
        required_only/exclude_names; warns (via `log`) about pre-existing
        hard failures and a missing required_names() list exactly the same
        way regardless of execution mode."""
        if self.failed_names:
            log(
                f"WARNING - previously hard failed items are NOT initialized:\n{self.failed_names} "
            )
        if required_only:
            if not self.required_names():
                log(
                    f"WARNING - No required names defined in namespace {self.name}, initializing all items!"
                )
                return self.all_names - self.initialized_names - set(exclude_names)
            return (
                self.all_names - self.initialized_names - set(exclude_names)
            ) & set(self.required_names())
        return self.all_names - self.initialized_names - set(exclude_names)

    def _run_init_pass(
        self,
        names_to_init,
        max_workers,
        verbose,
        silent,
        raise_errors,
        giveup_failed,
        print_summary,
        print_times,
        starttime,
        log,
        N_cycles=4,
    ):
        """Single concurrent, CA-context-safe pass over `names_to_init`,
        retrying only names that lost a race to initialize a shared
        dependency (IsInitialisingError). Shared core of init_all(): called
        either inside a spawned daemon thread (background=True) or directly
        (background=False, blocking the caller until done) - identical
        algorithm either way, only the calling context differs.

        `raise_errors` only has an effect when called synchronously (i.e.
        from background=False): a background thread has no caller left to
        catch it, so a genuinely-failed name there is always just recorded
        via `giveup_failed`, same as when raise_errors=False.

        `N_cycles` caps how many times that retry loop may run. It has to be
        capped: a name can raise IsInitialisingError on every attempt (it is
        raised on a `init_timeout` expiry while another thread builds the
        same name, and a build legitimately slower than `init_timeout` -
        several bernina components take 20-60 s - hits that every round),
        and an uncapped loop then never terminates. Observed for real:
        a status-server init sat at "76 of 87" for over ten minutes,
        cycling four names forever. Names still pending when the cap is
        reached are treated like any other failure (`giveup_failed`), i.e.
        reported rather than retried silently for ever.
        """
        # `silent` drives both halves of being quiet: it suppresses this
        # pass's own progress/per-item lines (via `log` and init_name(quiet=))
        # *and* routes the worker threads' chatter into a log file, which is
        # the only way the device __init__ prints and libca's fd-2 messages
        # can be kept off the terminal at all. Where that log went is
        # reported by the summary below, not by log() - log() is silenced by
        # the very flag that turns the capture on.
        cap = self._make_output_capture(silent)

        import epics.ca as ca

        # Every thread that touches Channel Access must attach to one
        # shared "initial context" instead of implicitly creating its own
        # (pyepics's default first-use behaviour) -- concurrent worker
        # threads each creating their own CA context and connecting at
        # once segfaults inside libca's CA-TCP-recv thread (confirmed via
        # dmesg while testing this against the real bernina namespace; see
        # also eco.status_server.parallel_init, the original prototype of
        # this fix, and eco/status_server/namespace_store.py's
        # max_workers=1-without-this-fix constraint on the plain blocking
        # ThreadPoolExecutor this replaces).
        ca.use_initial_context()

        def _thread_initializer():
            ca.use_initial_context()
            init_fn = cap.initializer
            if init_fn is not None:
                init_fn()

        first_exception = None
        # Dependency-ordered: a name is only submitted once everything it
        # declared a NamespaceComponent dependency on has been built, so
        # consumers of a shared component no longer race each other to build
        # it. Names with no declared dependencies all land in the first
        # layer, so this costs essentially no parallelism - at Bernina it
        # moves the nine EventReceivers behind `event_master` and leaves the
        # other ~70 components exactly as concurrent as before.
        layers = self._dependency_layers(names_to_init, log=log)
        if len(layers) > 1:
            log(
                f"Initializing in {len(layers)} dependency layers: "
                + ", ".join(str(len(la)) for la in layers)
                + " name(s)"
            )
        givenup = set()
        with cap, ThreadPoolExecutor(
            max_workers=max_workers, initializer=_thread_initializer
        ) as exc:
            for layer in layers:
                pending = set(layer)
                cycles_left = max(int(N_cycles), 1)
                while pending and cycles_left:
                    cycles_left -= 1
                    futs = {
                        exc.submit(
                            self.init_name,
                            name,
                            verbose=verbose,
                            raise_errors=True,
                            quiet=bool(silent),
                        ): name
                        for name in pending
                    }
                    retry = set()
                    for fut in as_completed(futs):
                        name = futs[fut]
                        try:
                            fut.result()
                        except IsInitialisingError:
                            # Lost a race for a name someone else (this pass
                            # itself, or the session directly) is already
                            # initializing as a dependency; retry next round.
                            retry.add(name)
                        except Exception as exc_:
                            if first_exception is None:
                                first_exception = exc_
                    pending = retry & (self.all_names - self.initialized_names)
                # Whatever this layer could not finish is not allowed to hold
                # up the layers behind it: the dependents are attempted
                # anyway (they may not really need it), exactly as they were
                # before layering existed.
                givenup |= pending
        pending = givenup

        if pending:
            # A warning rather than log(): log() is silenced by silent=True,
            # which is exactly how a long-running service calls this, and
            # "these components were given up on" is the one thing from that
            # pass that must not be silent.
            logger.warning(
                "Giving up on %d name(s) still reporting an in-progress "
                "initialization after %d cycles: %s",
                len(pending), N_cycles, ", ".join(sorted(pending)),
            )
            log(
                f"Giving up on {len(pending)} name(s) still reporting an "
                f"in-progress initialization after {N_cycles} cycles: "
                + ", ".join(sorted(pending))
            )

        if giveup_failed:
            failed_names = names_to_init.intersection(self.lazy_names)
            for k in failed_names:
                self.failed_items[k] = self.lazy_items.pop(k)
                # Record *why*, if nothing else did. A name given up on after
                # N_cycles never raised, so without this it lands in
                # failed_items with an empty failed_items_excpetion entry -
                # and both the summary below and _timeout_error()'s "inspect
                # the failure" hint then have nothing at all to show for it.
                self.failed_items_excpetion.setdefault(
                    k,
                    GivenUpInitialisationError(
                        f"'{k}' was still reporting an in-progress "
                        f"initialization after {N_cycles} cycles and was "
                        f"given up on; retry with "
                        f"<namespace>.reinitialize('{k}')"
                    ),
                )

        if print_summary:
            self._print_init_summary(names_to_init, starttime, cap)
        if print_times:
            self._print_init_times(names_to_init)

        if raise_errors and first_exception is not None:
            raise first_exception

    def _print_init_summary(self, names_to_init, starttime, cap=None):
        """The one thing an init_all() pass always reports, silent or not.

        Deliberately printed rather than routed through init_all()'s `log`:
        `log` is gated on `silent`, and a summary you only get by *not* being
        silent is a summary nobody ever sees (silent=True is the default and
        what every long pass uses). Safe to print from here even with the
        capture installed: only the pool's worker threads are routed into the
        log file, and this runs on the thread driving the pass.

        Splits the non-OK names into two groups, because they are genuinely
        different states: *incomplete* items came up and stay usable, they
        just have failed sub-components (which are named, resolved down to
        the leaf that actually failed); *failed* items are not there at all.
        """
        ok = self.initialized_names & names_to_init
        incomplete, failed = [], []
        for name in sorted(self.failed_names & names_to_init):
            exc = self.failed_items_excpetion.get(name)
            subs = sorted(_failed_subcomponents(self.resolve_item(name)))
            if subs:
                # A badly disconnected assembly can have dozens of failed
                # PVs; the point of the line is to say *what kind of thing*
                # is missing, not to be the full list (which is one
                # `<namespace>.<name>._failed_appends` away).
                shown = ", ".join(subs[:6])
                if len(subs) > 6:
                    shown += f", +{len(subs)-6} more"
                incomplete.append(f"{name} ({shown})")
            elif isinstance(exc, IncompleteInitialisationError):
                # Incomplete, but the object is gone/still lazy so the
                # sub-component names can only come from the stored message.
                incomplete.append(name)
            else:
                failed.append(f"{name} ({_short_exception(exc)})")

        head = (
            f"Initialized {len(ok)} of {len(names_to_init)} in namespace "
            f"{self.name} in {time()-starttime:.1f} s"
        )
        if incomplete or failed:
            head += f" ({len(incomplete)} incomplete, {len(failed)} failed)"
        print(head)
        for label, color, group in (
            ("incomplete", _color.YELLOW, incomplete),
            ("failed", _color.RED, failed),
        ):
            for i, entry in enumerate(group):
                tag = (label + ":").ljust(12) if i == 0 else " " * 12
                print(f"  {color}{tag}{_color.RESET}{entry}")
        if cap is not None and cap.enabled and cap.path:
            print(f"  {'log:':<12}{cap.path}  (<namespace>.read_init_log())")
        sys.stdout.flush()

    def _print_init_times(self, names_to_init=None, n=15):
        """Top-`n` slowest components of the last pass, printed from the pass
        itself so it works in background mode too (the old ascii_graph block
        sat in init_all()'s blocking branch and, being gated on `not silent`
        as well, could not fire at the defaults). Full data stays available
        as `initialisation_times_sorted`."""
        times = self.initialisation_times
        if names_to_init is not None:
            times = {k: v for k, v in times.items() if k in names_to_init}
        if not times:
            return
        ranked = sorted(times.items(), key=lambda kv: kv[1], reverse=True)[:n]
        width = max(len(k) for k, _ in ranked)
        print(f"Slowest {len(ranked)} of {len(times)} initialisations:")
        for name, dt in ranked:
            print(f"  {name.ljust(width)}  {dt:7.1f} s")
        sys.stdout.flush()

    def init_all(
        self,
        required_only=True,
        verbose=False,
        raise_errors=False,
        print_summary=True,
        print_times=False,
        max_workers=8,
        N_cycles=4,
        silent=True,
        giveup_failed=True,
        exclude_names=[],
        background=True,
    ):
        """Initialize namespace items.

        Single shared algorithm underneath (see `_run_init_pass`): a
        concurrent, CA-context-safe pass with up to `max_workers` workers
        (the same knob in both execution modes - there used to be a separate
        `background_max_workers`, but since every worker attaches to the
        shared CA context there has only ever been one code path and no
        reason for the split), retrying only names that lose a race to
        initialize a shared dependency (IsInitialisingError) - not a blanket
        re-run of everything. Always switches this namespace to event-based
        lazy-init locking (`_responsive_locking`), so a component you access
        directly while a pass is also touching it (e.g. as someone else's
        dependency) is handed to you the instant that single build
        completes, instead of on a fixed polling interval.

        background : bool (default True)
            True: runs in a daemon thread and returns immediately - the
            calling (e.g. ipython) session stays usable right away.
            Failures are always recorded (via `giveup_failed`), never
            raised, since a background thread has no synchronous caller
            left to catch them - `raise_errors` has no effect here.
            False: runs directly in the calling thread and blocks until
            done - e.g. `Daq.init_namespace` and the scan-start callback in
            `eco.acquisition.counters_tmp` need this, since they append
            status info that has to already be initialized before they
            return. `raise_errors=True` is meaningful in this mode: the
            first genuine failure (not a lazy-init race) is re-raised after
            the pass finishes.
        silent : bool | str | pathlib.Path (default True)
            Purely output-level, and the only thing controlling output (it
            absorbed both the old `quiet` and the old `capture_output`
            parameters, neither of which exists anymore - pass everything
            through `silent`). Does not affect execution mode - see
            `background` for that.

            Truthy means *actually* silent, which takes two halves. It
            suppresses this call's own progress and per-item lines, and it
            routes everything the pool's *worker* threads write - the device
            ``__init__`` chatter, which is the bulk of it, plus the
            optional-component warnings - into a log file instead of the
            terminal. EPICS/libca Channel Access messages, which the C
            library writes straight to fd 2 and past any Python-level
            stream proxy, are diverted into the same file via
            ``epics.ca.replace_printf_handler`` for the duration of the
            pass; the default (messages -> stderr) is restored when it
            finishes. That hook is process-global while installed, so a CA
            message you trigger yourself during a ``background=True`` init
            also lands in the log until it completes. Threads a device
            spawns on its own are not routed, so their non-CA ``print``
            output can still leak - that is the one remaining hole.

            The log goes to ``~/.eco/init_logs/<namespace>_<stamp>.log`` by
            default; pass a str/Path instead of ``True`` to choose the
            location. Either way the path ends up on
            ``self.last_init_log_path``, is named in the summary, and is
            read back with ``self.read_init_log()``.

            `silent` does *not* suppress the summary or the times table -
            those are gated only by `print_summary` / `print_times`, since
            a summary you can only get by being non-silent is one nobody
            ever sees at the (silent) defaults.
        print_summary : bool (default True)
            Print the one-block "Initialized N of M ... " report at the end
            of the pass, listing incomplete items (up and usable, but with
            failed sub-components - named down to the leaf that actually
            failed) separately from outright failed ones (with their
            error). Independent of `silent`.
        print_times : bool (default False)
            Print the slowest initialisations of the pass. Independent of
            `silent`, and works in background mode too. Off by default
            because it is long; the full data is always available as
            `initialisation_times_sorted`.
        max_workers : int (default 8)
            Pool size, in both execution modes. Safe above 1 because
            `_run_init_pass` attaches every worker to the one shared CA
            context.
        N_cycles : int (default 4)
            Maximum number of retry passes over names that reported an
            in-progress initialization (IsInitialisingError). Caps what
            would otherwise be an unbounded loop - see `_run_init_pass`.
        """

        def log(*args, **kwargs):
            if not silent:
                print(*args, **kwargs)

        # A second pass started while one is still running would submit an
        # overlapping name set to a second pool, and every collision on a
        # shared dependency is exactly the IsInitialisingError the retry loop
        # exists to work around - i.e. it makes the pass it duplicates slower
        # and more likely to give up. Hand back the running one instead.
        running = self._background_init_thread
        if running is not None and running.is_alive():
            logger.warning(
                "init_all(): a background pass is already running on namespace "
                "%s; returning it instead of starting a second one "
                "(wait_for_init() to block on it, init_progress() to watch it).",
                self.name,
            )
            return running

        starttime = time()
        names_to_init = self._select_names_to_init(required_only, exclude_names, log)

        self._responsive_locking = True

        if background:
            log(
                f"Initializing {len(names_to_init)} items in namespace {self.name} in the "
                "background; the session stays usable and any component you "
                "access directly takes priority."
            )

            def worker():
                self._run_init_pass(
                    names_to_init,
                    max_workers,
                    verbose,
                    silent,
                    raise_errors,
                    giveup_failed,
                    print_summary,
                    print_times,
                    starttime,
                    log,
                    N_cycles,
                )

            thread = Thread(
                target=worker, name=f"init_all_background[{self.name}]", daemon=True
            )
            self._init_target_names = set(names_to_init)
            self._background_init_thread = thread
            thread.start()
            return thread

        log(f"Initializing {len(names_to_init)} items in namespace {self.name} ...")
        self._init_target_names = set(names_to_init)
        # print_summary/print_times are handled inside _run_init_pass, so they
        # behave identically here and in the background branch (the times
        # table used to live out here and therefore never ran at all with the
        # default background=True).
        self._run_init_pass(
            names_to_init,
            max_workers,
            verbose,
            silent,
            raise_errors,
            giveup_failed,
            print_summary,
            print_times,
            starttime,
            log,
            N_cycles,
        )
        return None

    def wait_for_init(self, timeout=None):
        """Block until the background init_all() pass has finished.

        Returns True if no pass is running or it finished within `timeout`,
        False if it is still going. Without this, a script that follows
        `init_all()` (background by default now) has to reach into
        `_background_init_thread` itself to know when the namespace is
        actually usable.
        """
        thread = self._background_init_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def init_progress(self, names=None):
        """Counts for the pass currently running (or the last one that ran).

        `names` defaults to that pass's target set, falling back to every
        name in the namespace. Cheap and side-effect free - it only counts
        dict membership, so it never touches (and so never builds) a
        component.
        """
        if names is None:
            names = getattr(self, "_init_target_names", None) or self.all_names
        names = set(names)
        thread = self._background_init_thread
        return {
            "running": bool(thread is not None and thread.is_alive()),
            "total": len(names),
            "initialized": len(self.initialized_names & names),
            "failed": len(self.failed_names & names),
            "pending": len(self.lazy_names & names),
        }

    def init_all_new(
        self,
        verbose=False,
        raise_errors=False,
        print_summary=True,
        print_times=True,
        max_workers=5,
        N_cycles=4,
        silent=True,
        giveup_failed=True,
        exclude_names=[],
    ):
        starttime = time()

        if self.failed_names:
            print(
                f"WARNING - previously hard failed items are NOT initialized:\n{self.failed_names} "
            )
        if silent:
            print(
                f"Initializing all items in namespace {self.name} silently in background.\n Be aware of unrelated output!"
            )

            def init():
                self.exc_init = ThreadPoolExecutor(max_workers=max_workers)
                jobs = [
                    self.exc_init.submit(
                        self.init_name, name, verbose=verbose, raise_errors=raise_errors
                    )
                    for name in (self.all_names - set(exclude_names))
                ]
                self.exc_init.shutdown(wait=True)
                self.exc_init = ThreadPoolExecutor(max_workers=1)
                jobs = [
                    self.exc_init.submit(
                        self.init_name, name, verbose=verbose, raise_errors=raise_errors
                    )
                    for name in (
                        self.all_names - self.initialized_names - set(exclude_names)
                    )
                ]
                self.exc_init.shutdown(wait=True)
                if giveup_failed:
                    failed_names = self.lazy_names
                    for k in failed_names:
                        self.failed_items[k] = self.lazy_items.pop(k)
                if print_summary:
                    print(
                        f"Initialized {len(self.initialized_names)} of {len(self.all_names)}."
                    )
                    print(
                        "Failed objects: "
                        + ", ".join(self.lazy_names.union(self.failed_names))
                    )
                    print(f"Initialisation took {time()-starttime} seconds")

            Thread(target=init).start()
        else:
            if hasattr(self, "exc_init"):
                self.exc_init.shutdown(wait=False)
            with ThreadPoolExecutor(max_workers=max_workers) as exc:
                names = self.all_names - self.initialized_names - set(exclude_names)

                def tinit(name):
                    try:
                        self.init_name(name, verbose=verbose, raise_errors=raise_errors)
                    except IsInitialisingError:
                        return ["postpone", name]

                futs = []
                for tname in names:
                    futs.append(exc.submit(tinit, tname))

                while futs:
                    print(f">>>>>>>>>>>  {len(futs)} initialisations to wait for")
                    for fut in as_completed(futs):
                        futs.pop(futs.index(fut))
                        try:
                            if fut.result()[0] == "postpone":
                                tname = fut.result()[1]
                                futs.append(exc.submit(tinit, tname))
                        except:
                            pass

            print("Initializing in single thread...")
            with ThreadPoolExecutor(max_workers=1) as exc:
                list(
                    progress.track(
                        exc.map(
                            lambda name: self.init_name(
                                name, verbose=verbose, raise_errors=raise_errors
                            ),
                            self.all_names
                            - self.initialized_names
                            - set(exclude_names),
                        ),
                        description="Initializing ...",
                        total=len(
                            self.all_names - self.initialized_names - set(exclude_names)
                        ),
                        transient=True,
                    )
                )

            if giveup_failed:
                failed_names = self.lazy_names
                for k in failed_names:
                    self.failed_items[k] = self.lazy_items.pop(k)
            if print_summary:
                print(
                    f"Initialized {len(self.initialized_names)} of {len(self.all_names)}."
                )
                print(
                    "Failed objects: "
                    + ", ".join(self.lazy_names.union(self.failed_names))
                )
                print(f"Initialisation took {time()-starttime} seconds")

            if (not silent) and print_times:
                from ascii_graph import Pyasciigraph

                gr = Pyasciigraph()
                for line in gr.graph(
                    "Initialisation times",
                    [(tk, tv) for tk, tv in self.initialisation_times_sorted.items()],
                ):
                    print(line)

    def get_initialized_aliases(self, channeltypes=[]):
        aliases = []
        has_no_aliases = []
        for tn, tv in self.initialized_items.items():
            try:
                aliases += tv.alias.get_all()
            except:
                has_no_aliases.append(tn)
        aliases_out = []
        for channeltype in channeltypes:
            for alias in aliases:
                if alias["channeltype"] == channeltype:
                    aliases_out.append(alias)
        if not channeltypes:
            aliases_out = aliases
        return aliases, has_no_aliases

    def append_obj(
        self,
        obj_factory,
        *args,
        lazy=False,
        name=None,
        module_name=None,
        init_timeout=30,
        **kwargs,
    ):
        # human-readable description of what is being built, used in timeout /
        # partial-init messages so the culprit is obvious without a traceback.
        obj_factory_name = getattr(obj_factory, "__name__", str(obj_factory))
        factory_desc = f"{(module_name + ':') if module_name else ''}{obj_factory_name}"
        if name is not None:
            self._factory_info[name] = {
                "module_name": module_name,
                "obj_factory": obj_factory,
            }
            self._declared_dependencies[name] = [
                a
                for a in list(args) + list(kwargs.values())
                if isinstance(a, NamespaceComponent)
            ]
        if lazy:

            def init_local():

                if name in self.failed_names:
                    tmpexc = self.failed_items_excpetion
                    if isinstance(tmpexc[name], BaseException):
                        raise tmpexc[name]
                    else:
                        raise IsInitialisingError(
                            f"{name} failed previously to initialize."
                        )

                if name in self._initializing:
                    self._init_priority[name] += 1
                    if self._responsive_locking:
                        # Only active once init_all(background=True) has been
                        # used on this namespace: wake up as soon as the other
                        # thread finishes instead of polling every 5s, so a
                        # component accessed directly from the session is
                        # returned as fast as possible.
                        ev = self._init_events.setdefault(name, Event())
                        remaining = init_timeout - (
                            time() - self._initialisation_start_time[name]
                        )
                        ev.wait(timeout=max(remaining, 0))
                        if name in self._initializing:
                            try:
                                self._initializing.pop(self._initializing.index(name))
                            except ValueError:
                                pass
                            raise self._timeout_error(
                                name, init_timeout, factory_desc
                            )
                    else:
                        while name in self._initializing:
                            if (
                                time() - self._initialisation_start_time[name]
                            ) <= init_timeout:
                                sleep(5)
                            else:
                                #     print(f'{name} waiting init since {time()-self._initialisation_start_time[name]} s')
                                #     sleep(5)
                                # # passfailed_items_excpetion
                                self._initializing.pop(self._initializing.index(name))
                                raise self._timeout_error(
                                    name, init_timeout, factory_desc
                                )

                else:
                    self._initializing.append(name)
                    self._init_priority[name] = 0
                    self._initialisation_start_time[name] = time()
                    if self._responsive_locking:
                        self._init_events[name] = Event()

                # args, kwargs = replace_NamespaceComponents(*args, **kwargs)

                if module_name:
                    obj_maker = getattr(import_module(module_name), obj_factory)
                else:
                    obj_maker = _resolve_reloadable_factory(obj_factory)

                args_resolved, kwargs_resolved = replace_NamespaceComponents(
                    *args, **kwargs
                )
                accepts_name = "name" in signature(obj_maker).parameters
                manual_context = format_manual_instantiation(
                    obj_maker,
                    args_resolved,
                    kwargs_resolved,
                    name=name,
                    accepts_name=accepts_name,
                    module_name=module_name,
                )
                try:
                    if accepts_name:
                        obj_initialized = obj_maker(
                            *args_resolved,
                            name=name,
                            **kwargs_resolved,
                        )
                    else:
                        obj_initialized = obj_maker(
                            *args_resolved,
                            **kwargs_resolved,
                        )
                except Exception as e:
                    append_manual_context(e, manual_context)
                    self.failed_items[name] = self.lazy_items.pop(name)
                    self.failed_items_excpetion[name] = e
                    self._initializing.pop(self._initializing.index(name))
                    if self._responsive_locking:
                        ev = self._init_events.pop(name, None)
                        if ev is not None:
                            ev.set()
                    raise

                try:
                    popped = self.lazy_items.pop(name)
                except KeyError:
                    popped = self.failed_items.pop(name)
                # An assembly that came up but has optional sub-components that
                # failed to initialize is "incomplete": still usable/accessible,
                # but recorded in failed_items (not initialized_items) so the
                # partial state stays visible. See Assembly._append(optional=).
                incomplete = getattr(obj_initialized, "_failed_appends", None)
                if incomplete:
                    missing = ", ".join(incomplete)
                    self.failed_items[name] = popped
                    self.failed_items_excpetion[name] = IncompleteInitialisationError(
                        f"{name} initialized incompletely ({factory_desc}); "
                        f"missing component(s): {missing}. "
                        f"Inspect via <namespace>.{name}._failed_appends"
                    )
                    logger.warning(
                        "'%s' initialized incompletely; missing component(s): "
                        "%s. Recorded in failed_items (still accessible as "
                        "<namespace>.%s).",
                        name,
                        missing,
                        name,
                    )
                else:
                    self.initialized_items[name] = popped
                self._initializing.pop(self._initializing.index(name))
                if self._responsive_locking:
                    ev = self._init_events.pop(name, None)
                    if ev is not None:
                        ev.set()
                # if name in self.initialisation_times_lazy.keys():
                #     self.initialisation_times_lazy[name] += time() - starttime
                # else:
                self.initialisation_times_lazy[name] = (
                    time() - self._initialisation_start_time[name]
                )
                if hasattr(obj_initialized, "alias"):
                    self._append(
                        obj_initialized,
                        name=name,
                        is_setting=True,
                        is_display="recursive",
                        call_obj=False,
                    )
                if self.alias_namespace and hasattr(obj_initialized, "alias"):
                    for ta in obj_initialized.alias.get_all():
                        try:
                            self.alias_namespace.update(
                                ta["alias"], ta["channel"], ta["channeltype"]
                            )
                        except Exception as e:
                            # one record, not two prints: a missing alias
                            # silently changes what get_status() records, so
                            # the message and its cause belong together.
                            logger.warning(
                                "could not init alias %s for '%s': %s: %s",
                                ta["alias"],
                                name,
                                type(e).__name__,
                                e,
                            )
                else:
                    self.names_without_alias.append(name)
                return obj_initialized

            obj_lazy = Proxy(init_local)
            self.lazy_items[name] = obj_lazy
            if self.root_module:
                sys.modules[self.root_module].__dict__[name] = obj_lazy
            return obj_lazy

        else:
            starttime = time()
            args, kwargs = replace_NamespaceComponents(*args, **kwargs)
            if module_name:
                obj_maker = getattr(import_module(module_name), obj_factory)
            else:
                obj_maker = obj_factory
            try:
                obj = obj_maker(*args, name=name, **kwargs)
            except TypeError:
                obj = obj_maker(*args, **kwargs)
            # See the lazy branch: an assembly with failed optional components
            # is recorded as incomplete (failed_items) but stays accessible.
            incomplete = getattr(obj, "_failed_appends", None)
            if incomplete:
                missing = ", ".join(incomplete)
                self.failed_items[name] = obj
                self.failed_items_excpetion[name] = IncompleteInitialisationError(
                    f"{name} initialized incompletely ({factory_desc}); "
                    f"missing component(s): {missing}. "
                    f"Inspect via <namespace>.{name}._failed_appends"
                )
                logger.warning(
                    "'%s' initialized incompletely; missing component(s): %s. "
                    "Recorded in failed_items (still accessible as "
                    "<namespace>.%s).",
                    name,
                    missing,
                    name,
                )
            else:
                self.initialized_items[name] = obj
            self.initialisation_times_lazy[name] = time() - starttime
            if self.root_module:
                sys.modules[self.root_module].__dict__[name] = obj
            if hasattr(obj, "alias"):
                self._append(
                    obj,
                    name=name,
                    is_setting=True,
                    is_display="recursive",
                    call_obj=False,
                )
            if self.alias_namespace and hasattr(obj, "alias"):
                for ta in obj.alias.get_all():
                    try:
                        self.alias_namespace.update(
                            ta["alias"], ta["channel"], ta["channeltype"]
                        )
                    except Exception as e:
                        logger.warning(
                            "could not init alias %s for '%s': %s: %s",
                            ta["alias"],
                            name,
                            type(e).__name__,
                            e,
                        )
            else:
                self.names_without_alias.append(name)
            return obj

    def get_obj(self, name):
        if name in self.lazy_names:
            return self.lazy_items[name]
        elif name in self.initialized_names:
            return self.initialized_items[name]
        elif name in self.failed_names:
            # Incompletely-initialized assemblies are stored here but stay
            # usable (the proxy has already resolved to the partial object);
            # genuinely-failed items re-raise their stored exception on use.
            return self.failed_items[name]
        else:
            raise Exception("Name is not initialized!")

    def append_obj_from_config(self, cnf, lazy=False):
        module_name, obj_factory = cnf["type"].split(":")
        args = []
        for targ in cnf["args"]:
            if isinstance(targ, Component):
                args.append(self.get_obj(targ.name))
            else:
                args.append(targ)
        kwargs = {}
        for tk, tv in cnf["kwargs"].items():
            if isinstance(tv, Component):
                kwargs[tk] = self.get_obj(tv.name)
            else:
                kwargs[tk] = tv
        if "lazy" in cnf.keys():
            lazy = cnf["lazy"]

        self.append_obj(
            obj_factory,
            *args,
            lazy=lazy,
            name=cnf["name"],
            module_name=module_name,
            **kwargs,
        )

    def print_status(self):
        tab = []
        for name in self.initialized_names:
            tab.append([name, "initialized"])
        for name in self.lazy_names:
            tab.append([name, "lazy"])
        print(format_table(tab))


class LazyComponent:
    """Marker class a not-yet-initialized lazy namespace component reports as its
    ``__class__`` (see :class:`Proxy`). It exists only so introspection can name
    the unresolved state; it is never instantiated."""

    def __repr__(self):
        # Without this, IPython's default pretty-printer -- which checks
        # `type(obj).__repr__ is not object.__repr__` on `obj.__class__` to
        # decide whether an object has a "real" repr worth calling (see
        # `IPython.lib.pretty._default_pprint`) -- reads this class via
        # `Proxy.__class__` (see below), finds no override here (inherited
        # `object.__repr__`), concludes there's nothing to call, and prints its
        # own generic `<module.Class at 0x...>` stub INSTEAD of ever calling
        # `Proxy.__repr__`. That silently hides whatever `Proxy.__repr__`
        # would have shown -- including a real construction error, since a
        # namespace component that failed to build stays "unresolved" and
        # keeps showing this same misleading stub forever after. Simply having
        # *a* distinct `__repr__` here (even one that changes nothing else)
        # satisfies that check, so IPython calls the real `repr(obj)` and
        # `Proxy.__repr__` runs as designed.
        return object.__repr__(self)


class Proxy(Proxy_orig):
    """Lazy proxy for a namespace component that stays *shy* to introspection.

    ``lazy_object_proxy.Proxy`` forwards essentially every operation - including
    ``__class__`` - to its wrapped object, which runs the factory (i.e. actually
    initializes the device). That makes tab-completion expensive: when jedi
    builds the completion menu it asks every candidate for its "kind", which it
    determines with ``inspect.ismodule`` / ``isinstance`` -> reads ``__class__``
    -> resolves the proxy. So typing a shared prefix (``bernina.mo<TAB>``) used
    to initialize *every* sibling that matched.

    We override only ``__class__`` so that, while the proxy is still unresolved,
    it answers with the lightweight :class:`LazyComponent` marker instead of
    resolving. jedi/``isinstance``/``inspect`` then classify it as a plain
    instance and never trigger initialization. Once anything actually *uses* the
    component (any real attribute access resolves the proxy), ``__class__``
    reports the true wrapped type again, so ``isinstance`` is correct from that
    point on. The only visible effect is that ``isinstance``/``type().__mro__``
    on a still-untouched handle sees ``LazyComponent``; the first real use fixes
    it, and eco's own isinstance checks run on the resolved objects appended to
    the status collection, not on the proxies.

    ``__resolved__`` is a boolean exposed by the underlying C proxy that reports
    whether the factory has run *without* triggering it.
    """

    @property
    def __class__(self):
        if object.__getattribute__(self, "__resolved__"):
            return type(object.__getattribute__(self, "__wrapped__"))
        return LazyComponent

    def __dir__(self):
        """Same "stay shy to introspection" rule as __class__ above, for
        the same reason: dir() is NOT one of the operations
        lazy_object_proxy.Proxy special-cases, so an unresolved proxy
        would otherwise forward it straight to __wrapped__ -- resolving
        (fully constructing, real EPICS calls and all) the device.
        Confirmed for real: any code path that calls dir() on a
        still-lazy proxy -- IPython/Jedi's own tab-completion very much
        included, since building a completion menu means asking every
        candidate for its attributes -- took over a minute for a complex
        device, purely from being listed as a completion candidate, never
        mind actually being used."""
        if object.__getattribute__(self, "__resolved__"):
            return dir(object.__getattribute__(self, "__wrapped__"))
        return dir(LazyComponent)

    def __repr__(self, __getattr__=object.__getattribute__):
        try:
            target = __getattr__(self, "__target__")
        except AttributeError:
            target = self.__wrapped__

        return target.__repr__()
