import io
import json
import importlib
import importlib.util
import os
from pathlib import Path
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

    call_parts = [repr(arg) for arg in args] + [
        f"{key}={repr(value)}" for key, value in call_kwargs.items()
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
    with open(fina, "w") as f:
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

    Used by ``Namespace.init_all(capture_output=...)`` to collect the noisy
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
    """

    def __init__(self, sink_path=None, enabled=True, name="namespace", capture_ca=True):
        self.enabled = enabled
        self.capture_ca = capture_ca
        self.path = None
        self._sink = None
        self._routed = set()
        self._lock = Lock()
        self._orig_stdout = None
        self._orig_stderr = None
        self._ca_installed = False
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
            n = self._sink.write(s)
        return n

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

    def __enter__(self):
        if self.enabled:
            self._orig_stdout = sys.stdout
            self._orig_stderr = sys.stderr
            sys.stdout = self._make_proxy(self._orig_stdout)
            sys.stderr = self._make_proxy(self._orig_stderr)
            if self.capture_ca:
                self._install_ca_capture()
        return self

    def __exit__(self, *exc):
        if self.enabled:
            # Restore the real streams first, then hand libca back its default
            # handler so replace_printf_handler() rebinds to the real stderr
            # rather than to the proxy we are about to detach.
            sys.stdout = self._orig_stdout
            sys.stderr = self._orig_stderr
            self._restore_ca_capture()
            try:
                self._sink.flush()
            except Exception:
                pass
            try:
                self._sink.close()
            except Exception:
                pass
        return False


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
        uses internally."""
        return (
            self.lazy_items.get(name)
            or self.failed_items.get(name)
            or self.initialized_items.get(name)
        )

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
            proxy = (
                self.lazy_items.get(name)
                or self.failed_items.get(name)
                or self.initialized_items.get(name)
            )
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

    def _make_output_capture(self, capture_output):
        """Build the (possibly disabled) thread-routed output capture used by
        init_all(capture_output=...). Records the log path on the namespace so
        it can be read back later with read_init_log()."""
        cap = _ThreadRoutedOutput(
            sink_path=capture_output,
            enabled=bool(capture_output),
            name=self.name or "namespace",
        )
        if cap.enabled:
            self.last_init_log_path = cap.path
        return cap

    def read_init_log(self):
        """Return the captured per-component init log from the most recent
        init_all(capture_output=...) call, or '' if none was captured."""
        path = getattr(self, "last_init_log_path", None)
        if not path:
            print("No captured init log (call init_all(capture_output=True) first).")
            return ""
        try:
            with open(path) as fh:
                return fh.read()
        except FileNotFoundError:
            print(f"Captured init log no longer exists at {path}.")
            return ""

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
        quiet,
        capture_output,
        raise_errors,
        giveup_failed,
        print_summary,
        starttime,
        log,
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
        """
        cap = self._make_output_capture(capture_output)
        if cap.enabled:
            log(f"Capturing per-component init output to {cap.path}")

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
        pending = set(names_to_init)
        with cap, ThreadPoolExecutor(
            max_workers=max_workers, initializer=_thread_initializer
        ) as exc:
            while pending:
                futs = {
                    exc.submit(
                        self.init_name,
                        name,
                        verbose=verbose,
                        raise_errors=True,
                        quiet=quiet,
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

        if giveup_failed:
            failed_names = names_to_init.intersection(self.lazy_names)
            for k in failed_names:
                self.failed_items[k] = self.lazy_items.pop(k)

        if print_summary:
            log(
                f"Initialized {len(self.initialized_names & names_to_init)} of {len(names_to_init)}."
            )
            failed = self.failed_names & names_to_init
            if failed:
                log("Failed objects: " + ", ".join(failed))
            log(f"Initialisation took {time()-starttime:.1f} seconds")

        if raise_errors and first_exception is not None:
            raise first_exception

    def init_all(
        self,
        required_only=True,
        verbose=False,
        raise_errors=False,
        print_summary=True,
        print_times=True,
        max_workers=1,
        N_cycles=4,
        silent=True,
        giveup_failed=True,
        exclude_names=[],
        background=True,
        background_max_workers=8,
        capture_output=False,
    ):
        """Initialize namespace items.

        Single shared algorithm underneath (see `_run_init_pass`): a
        concurrent, CA-context-safe pass with up to `background_max_workers`
        (if `background=True`) or `max_workers` (if `background=False`)
        workers, retrying only names that lose a race to initialize a
        shared dependency (IsInitialisingError) - not a blanket re-run of
        everything. Always switches this namespace to event-based lazy-init
        locking (`_responsive_locking`), so a component you access directly
        while a pass is also touching it (e.g. as someone else's
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
        silent : bool (default True)
            Purely output-level now, and the only thing left controlling
            output (this replaced the old separate `quiet` parameter,
            which no longer exists - pass everything through `silent`
            instead). If True, suppresses progress/per-item/summary
            messages regardless of `verbose`/`print_summary`/`print_times`.
            Does not affect execution mode - see `background` for that.
        capture_output : bool | str | pathlib.Path
            If truthy, the noisy per-component chatter produced by the pool
            *worker* threads (device ``__init__`` prints, per-item lines) is
            routed into a log file instead of the terminal, while whatever
            `silent=False` still prints - the summary - reaches the screen.
            Pass ``True`` to write to an auto-named temp file, or a path to
            choose the location. The path is stored on
            ``self.last_init_log_path``; read it back with
            ``self.read_init_log()``. EPICS/libca Channel Access messages
            (which the C library writes straight to fd 2, past the Python
            proxy) are also diverted into the log for the duration of the
            init via ``epics.ca.replace_printf_handler``, and the default
            (messages -> stderr) is restored when init finishes; that hook
            is process-global while active, so a CA message you trigger
            yourself during a ``background=True`` init also lands in the
            log until it completes. Note: threads a device spawns on its
            own are not routed, so their non-CA ``print`` output can still
            leak.
        background_max_workers : int
            Worker count used only when `background=True`; `max_workers`
            is used only when `background=False`.
        """

        def log(*args, **kwargs):
            if not silent:
                print(*args, **kwargs)

        starttime = time()
        names_to_init = self._select_names_to_init(required_only, exclude_names, log)

        self.silently_initializing = True
        self._responsive_locking = True

        if background:
            log(
                f"Initializing {len(names_to_init)} items in namespace {self.name} in the "
                "background; the session stays usable and any component you "
                "access directly takes priority."
            )

            def worker():
                try:
                    self._run_init_pass(
                        names_to_init,
                        background_max_workers,
                        verbose,
                        silent,
                        capture_output,
                        raise_errors,
                        giveup_failed,
                        print_summary,
                        starttime,
                        log,
                    )
                finally:
                    self.silently_initializing = False

            thread = Thread(
                target=worker, name=f"init_all_background[{self.name}]", daemon=True
            )
            self._background_init_thread = thread
            thread.start()
            return thread

        log(f"Initializing {len(names_to_init)} items in namespace {self.name} ...")
        try:
            self._run_init_pass(
                names_to_init,
                max_workers,
                verbose,
                silent,
                capture_output,
                raise_errors,
                giveup_failed,
                print_summary,
                starttime,
                log,
            )
        finally:
            self.silently_initializing = False

        if print_times and not silent:
            try:
                from collections import Iterable
            except:
                import collections.abc

                collections.Iterable = collections.abc.Iterable
            from ascii_graph import Pyasciigraph

            gr = Pyasciigraph()
            for line in gr.graph(
                "Initialisation times",
                [(tk, tv) for tk, tv in self.initialisation_times_sorted.items()],
            ):
                print(line)
        return None

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
            self.silently_initializing = True
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
                self.silently_initializing = False
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
                    print(
                        _color.RED
                        + f"WARNING: '{name}' initialized incompletely; missing "
                        + f"component(s): {missing}. Recorded in failed_items "
                        + f"(still accessible as <namespace>.{name})."
                        + _color.RESET
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
                            print(f'could not init alias {ta["alias"]}')
                            print("error message", e)
                            # traceback.print_tb(e)
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
                print(
                    _color.RED
                    + f"WARNING: '{name}' initialized incompletely; missing "
                    + f"component(s): {missing}. Recorded in failed_items "
                    + f"(still accessible as <namespace>.{name})."
                    + _color.RESET
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
                        print(f'could not init alias {ta["alias"]}')
                        print("error message", e)
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
