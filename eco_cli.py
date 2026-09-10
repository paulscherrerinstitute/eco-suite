#!/usr/bin/env python
"""Console entry point for eco (the ``eco`` command).

This module is deliberately kept OUTSIDE the ``eco`` package and imports only
the standard library. Importing it must NOT import ``eco`` itself: the heavy
package import (EPICS, cam_server, ...) is expensive and best done once, inside
the fresh session that this launcher starts -- not in the parent process that
only needs to build a command line.

Four subcommands (``eco console`` is the default -- a bare ``eco`` is exactly
``eco console``):

    console    Interactive IPython session (the traditional eco startup).
               Equivalent to ipython --profile=eco --no-banner -i
               -c "run -m eco.startup_inline -l -s bernina"
    desktop    A Spyder/MATLAB-like Qt workbench window: an embedded IPython
               console (on by default; --no-console to skip it), plus --
               only if -s/--scope is given -- a dockable "Namespace" panel
               to browse and open device widgets. Without -s, this is just
               a plain Qt console, no namespace attached. See
               eco.widgets.desktop_app.
    webapp     Serve the packaged eco notebook (eco/voila_app.ipynb) as a
               read-only Voila dashboard: the chosen namespace's widget,
               with the assembly browser to navigate to more components.
    jupyterlab Open JupyterLab on that same notebook. If -s/--scope is
               given, this also registers (and makes the default) a Jupyter
               kernel that preloads eco.<scope> -- see _run_jupyterlab --
               so any *new* console or notebook you open from JupyterLab's
               own launcher starts with the namespace already loaded too,
               not just the one pre-opened notebook.

JupyterLab, Voila and the desktop UI's qtconsole are OPTIONAL -- they are not
hard dependencies. Install them on demand, e.g. ``pip install eco[lab]`` /
``eco[voila]`` or via conda. If the command is missing, this launcher prints a
hint instead of a traceback.

Defaults can be set in an ``.ecorc`` (INI) file so that a bare ``eco`` just
works. Lookup order (first match wins):

    1. $ECORC
    2. ./.ecorc          (current directory)
    3. ~/.ecorc          (home directory)

Example ``.ecorc``::

    [eco]
    command = console
    scope = bernina
    profile = eco
    lazy = true

Any of these can be overridden on the command line, e.g. ``eco -s alvra`` or
``eco jupyterlab -s alvra``.
"""

import argparse
import configparser
import importlib.util
import os
import sys
from pathlib import Path

# Built-in defaults, overridden by .ecorc, overridden again by CLI flags.
_BUILTIN_DEFAULTS = {
    "command": "console",
    "scope": "bernina",
    "profile": "eco",
    "lazy": True,
}

_COMMANDS = ("console", "desktop", "webapp", "jupyterlab", "box-server")


def _ecorc_path():
    """Return the .ecorc file that applies, or None."""
    candidates = []
    env = os.environ.get("ECORC")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / ".ecorc")
    candidates.append(Path.home() / ".ecorc")
    for path in candidates:
        if path.is_file():
            return path
    return None


def _load_defaults():
    """Merge built-in defaults with the [eco] section of the .ecorc, if any."""
    defaults = dict(_BUILTIN_DEFAULTS)
    path = _ecorc_path()
    if path is not None:
        cfg = configparser.ConfigParser()
        cfg.read(path)
        if cfg.has_section("eco"):
            sec = cfg["eco"]
            if "command" in sec:
                defaults["command"] = sec.get("command")
            if "scope" in sec:
                defaults["scope"] = sec.get("scope") or None
            if "profile" in sec:
                defaults["profile"] = sec.get("profile")
            if "lazy" in sec:
                defaults["lazy"] = sec.getboolean("lazy")
    return defaults, path


def _normalize_argv(argv, default_command):
    """Insert the default subcommand when none was given, so a bare `eco`
    (or `eco -s alvra`, `eco --no-lazy`, ...) works without spelling out
    `eco console` every time. Left alone if the first token already is a
    known subcommand, or is -h/--help (so `eco --help` shows the top-level
    help/subcommand list, not `console`'s)."""
    argv = list(argv)
    if not argv:
        return [default_command]
    if argv[0] in ("-h", "--help") or argv[0] in _COMMANDS:
        return argv
    return [default_command] + argv


def _package_file(filename):
    """Return the path of a file shipped inside the eco package WITHOUT
    importing eco. ``find_spec`` resolves the package location via the import
    machinery but does not execute ``eco/__init__.py``.
    """
    spec = importlib.util.find_spec("eco")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("eco: cannot locate the installed 'eco' package")
    return os.path.join(list(spec.submodule_search_locations)[0], filename)


def _exec(cmd, missing_hint):
    """Replace this process with ``cmd`` (list). On a missing executable print
    ``missing_hint`` instead of a traceback.
    """
    try:
        os.execvp(cmd[0], cmd)
    except FileNotFoundError:
        sys.exit("eco: '{}' not found. {}".format(cmd[0], missing_hint))


def _run_console(args):
    # Module-mode (`-m eco.startup_inline`), not a bare file path: IPython's
    # %run inserts the *run script's own directory* onto sys.path (mirroring
    # `python script.py`), which for a file path resolving inside the eco
    # package means eco's own install directory gets prepended. eco ships a
    # subpackage literally named eco/epics/, so that directory shadows the
    # real third-party `epics` (pyepics) package the moment it's on
    # sys.path -- `import epics.pv` elsewhere in eco then resolves to
    # eco.epics_utils (which has no `pv` submodule) instead of pyepics, raising
    # `ModuleNotFoundError: No module named 'epics.pv'`. Module-mode resolves
    # eco.startup_inline through the normal import system instead, so it
    # never adds that extra directory.
    run_cmd = "run -m eco.startup_inline"
    if args.lazy:
        run_cmd += " -l"
    if args.scope:
        run_cmd += " -s {}".format(args.scope)
    _exec(
        ["ipython", "--profile={}".format(args.profile), "--no-banner", "-i", "-c", run_cmd],
        "It ships with eco's core dependencies (IPython).",
    )


def _run_desktop(args):
    """Launch the Qt desktop workbench (see eco.widgets.desktop_app) as a
    fresh process -- exec, like the other _run_* functions, so this
    launcher module stays import-eco-free (see the module docstring).
    Without -s/--scope, this is a plain embedded Qt console with no
    namespace/launcher panel (see eco.widgets.desktop_app.EcoDesktopApp)."""
    cmd = [sys.executable, "-m", "eco.widgets.desktop_app"]
    if args.scope:
        cmd += ["--scope", args.scope]
    cmd += ["--lazy"] if args.lazy else ["--no-lazy"]
    if not args.console:
        cmd += ["--no-console"]
    if not args.namespace_panel:
        cmd += ["--no-namespace-panel"]
    if args.theme:
        cmd += ["--theme", args.theme]
    if args.workspace:
        cmd += ["--workspace", args.workspace]
    _exec(
        cmd,
        "The desktop UI needs qtconsole and a Qt binding (qtpy + PyQt5/PySide6) "
        "in this environment.",
    )


def _run_webapp(args):
    """Serve the packaged notebook (eco/voila_app.ipynb) as a read-only
    Voila dashboard, passing the scope/lazy choice through the environment
    (the notebook itself reads these -- see its first cell)."""
    notebook = _package_file("voila_app.ipynb")
    os.environ["ECO_SCOPE"] = args.scope or ""
    os.environ["ECO_LAZY"] = "1" if args.lazy else "0"
    _exec(
        ["voila", notebook],
        "Install it with e.g. `pip install eco[voila]` or `conda install voila`.",
    )


def _run_box_server(args):
    """Hold this console's connection to the physical manual-control box
    as a background service - see eco.manual_control.box_server. Exec's
    into that module (like every other _run_* here), so this launcher
    stays import-eco-free; -s/--scope picks which instrument namespace to
    offer the box (imports eco.<scope>, same as `eco console -s <scope>`).

    This is the "run it from a checkout, for quick dev/testing" path -
    same module the production `eco-box-server` wrapper script runs, just
    without that script's start/stop/systemd/log-file machinery. Runs in
    the foreground; Ctrl-C disconnects from the box and exits.
    """
    if not args.scope:
        sys.exit(
            "eco: box-server needs -s/--scope to know which instrument "
            "namespace to offer the box, e.g. `eco box-server -s bernina`"
        )
    cmd = [sys.executable, "-m", "eco.manual_control.box_server", "-s", args.scope]
    if args.box_host:
        cmd += ["--box-host", args.box_host]
    if args.box_port:
        cmd += ["--box-port", str(args.box_port)]
    if args.token_file:
        cmd += ["--token-file", args.token_file]
    cmd += ["--host", args.host, "--port", str(args.port)]
    _exec(
        cmd,
        "It ships with eco itself (eco.manual_control.box_server); "
        "the admin/health API additionally needs flask (`pip install eco[gui]`).",
    )


def _ipython_profile_dir(profile_name):
    """Where IPython keeps a profile's config/startup files -- same
    resolution IPython itself uses (~/.ipython, or $IPYTHONDIR if set)."""
    base = os.environ.get("IPYTHONDIR") or str(Path.home() / ".ipython")
    return Path(base) / "profile_{}".format(profile_name)


def _jupyter_kernel_dir(kernel_name):
    """Where a user-level Jupyter kernelspec lives -- same resolution
    `jupyter kernelspec install --user` uses (~/.local/share/jupyter/kernels
    on Linux, which is what this sandbox and every real beamline account
    both are)."""
    base = os.environ.get("JUPYTER_DATA_DIR") or str(
        Path.home() / ".local" / "share" / "jupyter"
    )
    return Path(base) / "kernels" / kernel_name


def _register_eco_kernel(scope, lazy):
    """Write (idempotently -- safe to call every launch) an IPython profile
    startup file that preloads eco.<scope> exactly like `eco console` does
    (import eco.<scope> as <scope>; from eco.<scope> import *), plus a
    Jupyter kernelspec that uses that profile. Returns the kernel's name.

    WHY a whole profile+kernelspec, not just opening one pre-built
    notebook: IPython/ipykernel run a profile's startup/*.py on every
    kernel launch under `--profile=<name>` -- not just this one process,
    every future one too. So once this kernel is registered, ANY new
    console or notebook opened from JupyterLab's own launcher (not just a
    notebook eco_cli.py happens to point jupyter at) starts with the
    namespace already loaded, matching eco desktop's console -- see
    eco.widgets.desktop_app's build_namespace_vars docstring for why a
    plain `namespace` variable alone wouldn't be enough.
    """
    kernel_name = "eco-{}".format(scope)

    profile_dir = _ipython_profile_dir(kernel_name)
    startup_dir = profile_dir / "startup"
    startup_dir.mkdir(parents=True, exist_ok=True)
    lazy_line = "ecocnf.startup_lazy = True\n" if lazy else ""
    (startup_dir / "00-eco-scope.py").write_text(
        "# Auto-generated by eco jupyterlab -- safe to delete, regenerated\n"
        "# every launch from the -s/-l flags given then.\n"
        "from eco import ecocnf\n"
        + lazy_line
        + "import eco.{scope} as {scope}\n"
        "from eco.{scope} import *\n"
        # Same reasoning as eco console/eco desktop's console: Jedi can
        # fully resolve a still-lazy device proxy just from it being a
        # completion candidate, and was independently measured slower and
        # less correct here regardless (see console_kernel.
        # build_console_widget's docstring).
        "get_ipython().Completer.use_jedi = False\n"
        # Every console/notebook run against this kernel (not just this
        # one, and not just this launch -- profile startup files run on
        # every future kernel launch under --profile=eco-{scope} too) gets
        # the same activity log eco desktop's console already has -- see
        # kernel_registry.install_shell_logger's docstring for why this
        # needs a different mechanism than that (ZMQ-message-based)
        # approach.
        "from eco.widgets import kernel_registry\n"
        "kernel_registry.install_shell_logger(kind='jupyterlab', label={scope!r})\n".format(
            scope=scope
        )
    )

    kernel_dir = _jupyter_kernel_dir(kernel_name)
    kernel_dir.mkdir(parents=True, exist_ok=True)
    import json

    (kernel_dir / "kernel.json").write_text(
        json.dumps(
            {
                "argv": [
                    sys.executable,
                    "-m",
                    "ipykernel_launcher",
                    "-f",
                    "{connection_file}",
                    "--profile={}".format(kernel_name),
                ],
                "display_name": "eco ({})".format(scope),
                "language": "python",
            }
        )
    )
    return kernel_name


def _write_jupyterlab_notebook(kernel_name, scope):
    """Copy the packaged eco/jupyterlab_app.ipynb to a writable, scope-
    specific path with its kernelspec pointed at `kernel_name` -- regenerated
    (idempotent) every launch, like _register_eco_kernel's own files.

    WHY a copy, not just opening the packaged file directly: opening an
    *existing* notebook makes JupyterLab use the kernel named in that
    file's own kernelspec metadata, not whatever --MappingKernelManager.
    default_kernel_name says (that only applies to a brand-new console/
    notebook created from the Launcher) -- so without this, the notebook
    would run on a plain, un-preloaded "python3" kernel regardless of the
    kernel _register_eco_kernel just registered.
    """
    import json

    src = _package_file("jupyterlab_app.ipynb")
    data = json.loads(Path(src).read_text())
    data["metadata"]["kernelspec"] = {
        "display_name": "eco ({})".format(scope),
        "language": "python",
        "name": kernel_name,
    }
    dest_dir = Path.home() / ".eco"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "jupyterlab_app_{}.ipynb".format(scope)
    dest.write_text(json.dumps(data, indent=1))
    return str(dest)


def _notebook_setup_code(notebook_path):
    """Source of `notebook_path`'s one code cell (see eco/jupyterlab_app.ipynb
    -- a markdown cell then exactly one code cell that builds the Namespace/
    Sidecar panels) -- what `_autorun_jupyterlab_setup` below tries to run
    for the user automatically."""
    import json

    data = json.loads(Path(notebook_path).read_text())
    for cell in data.get("cells", []):
        if cell.get("cell_type") == "code":
            return "".join(cell.get("source", []))
    return None


def _autorun_jupyterlab_setup(kernel_name, notebook_path):
    """Best-effort: run notebook_path's setup cell for the user, so the
    Namespace/Sidecar panels usually appear with no manual step, instead of
    sitting inert until someone opens the notebook and presses Shift-Enter.

    MUST be run in its own OS process (see call site -- multiprocessing.
    Process, not a thread): _run_jupyterlab always ends by exec()ing over
    itself (either straight into `jupyter lab`, or into a companion
    `jupyter console`), which would kill an in-process thread but leaves an
    already-forked child process running independently.

    Why this can only ever be best-effort, not a real fix: Jupyter kernels
    broadcast comm_open/widget-state messages over IOPub to every client
    already subscribed when they're emitted -- there's no replay for a
    client (here, the browser tab JupyterLab is about to open) that
    subscribes later. So this races the browser's own page load: create the
    session (which is also what makes JupyterLab's own frontend attach to
    THIS kernel instead of starting a second one for the same notebook path),
    wait a bit for the tab to load and its kernel websocket to subscribe,
    then execute the setup cell ourselves over a second client attached to
    that same kernel. If we win the race, the panels appear untouched by the
    user; if we lose it (slow browser start, slow network, ...), nothing
    renders and the fallback is exactly today's behaviour -- open the
    notebook, Shift-Enter the one code cell. Never lets a failure here
    propagate -- this must not affect the normal launch path.
    """
    import subprocess
    import time
    import json
    import urllib.request
    import urllib.parse

    try:
        code = _notebook_setup_code(notebook_path)
        if not code:
            return

        # 1. wait for the server to come up, and get its base URL (token
        # included) -- same "jupyter lab list" this module's sibling
        # (eco.widgets.app_launchers._find_running_jupyterlab) already
        # parses, duplicated here rather than imported since this module
        # deliberately imports nothing from the `eco` package itself.
        base_url = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and base_url is None:
            try:
                out = subprocess.check_output(
                    ["jupyter", "lab", "list"], stderr=subprocess.DEVNULL,
                    text=True, timeout=5,
                )
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith("http"):
                        base_url = line.split("::")[0].strip()
                        break
            except Exception:
                pass
            if base_url is None:
                time.sleep(0.5)
        if base_url is None:
            return

        # 2. create a session for this exact notebook on our known kernel --
        # this is also what makes JupyterLab's own frontend, when it opens
        # that same notebook path, find and attach to THIS kernel rather
        # than starting a second one.
        parsed = urllib.parse.urlsplit(base_url)
        token = urllib.parse.parse_qs(parsed.query).get("token", [None])[0]
        api_root = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        req = urllib.request.Request(
            api_root + "/api/sessions",
            data=json.dumps({
                "path": os.path.basename(str(notebook_path)),
                "type": "notebook",
                "kernel": {"name": kernel_name},
            }).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": "token " + token} if token else {}),
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            session = json.loads(resp.read())
        kernel_id = session["kernel"]["id"]

        # 3. locate that kernel's local connection file -- same machine, so
        # readable directly without going through the REST/websocket layer.
        runtime_dir = subprocess.check_output(
            ["jupyter", "--runtime-dir"], text=True, timeout=5
        ).strip()
        conn_file = Path(runtime_dir) / "kernel-{}.json".format(kernel_id)
        deadline = time.monotonic() + 10
        while not conn_file.exists() and time.monotonic() < deadline:
            time.sleep(0.3)
        if not conn_file.exists():
            return

        # 4. give the browser tab (already opened by `jupyter lab` itself)
        # a moment to load and its kernel websocket to subscribe -- see the
        # docstring above for why this specific wait is the whole ballgame.
        time.sleep(4)

        # 5. run the setup cell over a second client attached to the same
        # kernel -- fire-and-forget, we don't wait for/need the reply.
        from jupyter_client import BlockingKernelClient

        client = BlockingKernelClient()
        client.load_connection_file(str(conn_file))
        client.start_channels()
        try:
            client.execute(code)
            time.sleep(2)  # let the execute_request actually go out over ZMQ
        finally:
            client.stop_channels()
    except Exception:
        pass


def _run_jupyterlab(args):
    """Open JupyterLab on the packaged eco/jupyterlab_app.ipynb (background
    process -- see below for why this can't be the usual _exec) -- its
    Namespace panel and each opened device are real Sidecar dock panels,
    not webapp's single inline page (see eco.widgets.jupyter_sidecar; that
    notebook's own top cell has the details). If -s/--scope is given, also
    registers an eco-<scope> kernel (see _register_eco_kernel), regenerates
    a copy of that notebook pointed at it (see _write_jupyterlab_notebook
    -- opening an *existing* notebook always uses the kernel named in its
    own file, not JupyterLab's server-wide default), and makes that kernel
    JupyterLab's default too, so a fresh Console or Notebook opened from
    its own launcher also comes preloaded. Then, unless --no-console, also
    opens a real `jupyter console` on that same kernel in *this* terminal
    -- mirroring `eco desktop`'s "console on by default, --no-console to
    skip it" -- so you get an actual interactive prompt with eco.<scope>
    preloaded (bare names, exactly like `eco console`), not just a kernel
    sitting there available for JupyterLab's own launcher to pick.
    """
    import subprocess

    os.environ["ECO_SCOPE"] = args.scope or ""
    os.environ["ECO_LAZY"] = "1" if args.lazy else "0"
    kernel_name = None
    if args.scope:
        kernel_name = _register_eco_kernel(args.scope, args.lazy)
        notebook = _write_jupyterlab_notebook(kernel_name, args.scope)
        print(
            "eco: registered Jupyter kernel '{}' (preloads eco.{}) as the "
            "default for new consoles/notebooks in this session.".format(
                kernel_name, args.scope
            ),
            file=sys.stderr,
        )
    else:
        notebook = _package_file("jupyterlab_app.ipynb")
    lab_cmd = ["jupyter", "lab", notebook]
    if kernel_name:
        lab_cmd.append("--MappingKernelManager.default_kernel_name={}".format(kernel_name))

    if kernel_name:
        # Best-effort auto-run of the notebook's setup cell -- see
        # _autorun_jupyterlab_setup's docstring for what this does and why
        # it's not guaranteed. Spawned as a real child process (not a
        # thread) *before* the exec() calls below replace this process,
        # since fork survives that, a thread wouldn't.
        import multiprocessing

        multiprocessing.Process(
            target=_autorun_jupyterlab_setup, args=(kernel_name, notebook), daemon=False,
        ).start()

    if not args.console:
        _exec(
            lab_cmd,
            "Install it with e.g. `pip install eco[lab]` or `conda install jupyterlab`.",
        )
        return  # _exec only returns on a missing executable (already sys.exit'd)

    if not args.scope:
        print(
            "eco: --console needs -s/--scope to know what to preload -- "
            "no scope given, so only opening the plain JupyterLab tab "
            "(use --no-console to silence this).",
            file=sys.stderr,
        )
        _exec(
            lab_cmd,
            "Install it with e.g. `pip install eco[lab]` or `conda install jupyterlab`.",
        )
        return

    # jupyter lab runs as a genuine background server (its own long-lived
    # process, not something this launcher waits on); the console below
    # becomes THIS process's interactive surface, same as `eco console` --
    # so it has to be a real subprocess, not the usual os.execvp _exec
    # (which would replace this process and never reach the console at
    # all).
    try:
        subprocess.Popen(lab_cmd)
    except FileNotFoundError:
        sys.exit(
            "eco: 'jupyter' not found. Install it with e.g. "
            "`pip install eco[lab]` or `conda install jupyterlab`."
        )
    print(
        "eco: also opening a Jupyter console on kernel '{}' (eco.{} "
        "preloaded) -- Ctrl-D exits just the console, JupyterLab keeps "
        "running.".format(kernel_name, args.scope),
        file=sys.stderr,
    )
    _exec(
        ["jupyter", "console", "--kernel", kernel_name],
        "Install it with e.g. `pip install eco[lab]` or `conda install jupyterlab`.",
    )


_EPILOG = """\
Subcommands:
  console     Interactive IPython session (default). The traditional eco
              startup: an IPython shell with the chosen scope's devices
              loaded into its namespace, ready to use interactively.
  desktop     A Spyder/MATLAB-like Qt workbench window: an embedded
              IPython console (on by default; --no-console to skip it)
              plus, only if -s/--scope is given, a dockable "Namespace"
              panel to browse and open device widgets. Without -s, it's
              just a plain Qt console -- no namespace attached. Needs
              qtconsole and a Qt binding (qtpy + PyQt5/PySide6) in this
              environment; see eco.widgets.desktop_app.
  webapp      Serve the packaged notebook as a read-only Voila dashboard:
              widgets for the chosen namespace, with an assembly browser
              to navigate to more components. No code editing, just the UI.
  jupyterlab  Open JupyterLab on a small notebook whose Namespace panel
              and each opened device are real Sidecar dock panels (see
              eco.widgets.jupyter_sidecar) -- the closest browser-side
              analogue to eco desktop's dockable Namespace panel, not
              webapp's single inline page. With -s/--scope, also registers
              an eco-<scope> Jupyter kernel and makes it the default, so a
              fresh Console/Notebook you open from JupyterLab's own
              launcher starts with eco.<scope> already loaded too. Also
              opens a real `jupyter console` on that kernel right in this
              terminal (on by default, matching desktop; --no-console to
              skip it) -- an actual interactive prompt with eco.<scope>
              preloaded, not just a kernel sitting there for later.

Configuring defaults with .ecorc:
  A bare `eco` reads its defaults (command/scope/profile/lazy) from an
  .ecorc (INI) file, so you don't have to repeat flags every time. Lookup
  order, first match wins: $ECORC, ./.ecorc, ~/.ecorc. Example file:

      [eco]
      command = console
      scope = bernina
      profile = eco
      lazy = true

  Anything in it can still be overridden on the command line, e.g.
  `eco -s alvra` or `eco jupyterlab -s alvra`.

  --set-rcfile [PATH] writes -s/-l (and --profile, for console) exactly as
  given on THIS command line into such a file, instead of launching:

      eco jupyterlab -s alvra --set-rcfile     # writes ~/.ecorc
      eco -s alvra --set-rcfile ./.ecorc       # writes a project-local one

  so a later bare `eco` (from that directory, or anywhere if ~/.ecorc)
  picks these settings up automatically.
"""


def _write_rcfile(args):
    """Write the resolved command/scope/(profile)/lazy from this invocation
    into an .ecorc file at ``args.set_rcfile`` (see --set-rcfile)."""
    cfg = configparser.ConfigParser()
    section = {
        "command": args.command,
        "scope": args.scope or "",
        "lazy": "true" if args.lazy else "false",
    }
    if hasattr(args, "profile"):
        section["profile"] = args.profile
    cfg["eco"] = section
    path = Path(args.set_rcfile)
    with path.open("w") as fp:
        cfg.write(fp)
    print("eco: wrote {}".format(path), file=sys.stderr)
    for key, value in cfg["eco"].items():
        print("  {} = {}".format(key, value), file=sys.stderr)


def _add_common_args(parser, defaults, scope_default):
    parser.add_argument(
        "-s", "--scope", default=scope_default,
        help="scope name (instrument/beamline), e.g. bernina (default: %(default)s)",
    )
    lazy_grp = parser.add_mutually_exclusive_group()
    lazy_grp.add_argument(
        "-l", "--lazy", dest="lazy", action="store_true", default=defaults["lazy"],
        help="lazy initialisation of the scope (defer device instantiation)"
             + (" [default]" if defaults["lazy"] else ""),
    )
    lazy_grp.add_argument(
        "--no-lazy", dest="lazy", action="store_false",
        help="disable lazy initialisation (build all devices at startup)"
             + ("" if defaults["lazy"] else " [default]"),
    )
    parser.add_argument(
        "--set-rcfile", nargs="?", const=str(Path.home() / ".ecorc"), default=None,
        metavar="PATH",
        help="write this invocation's settings into an .ecorc file (PATH, "
             "default: ~/.ecorc) and exit instead of launching. See "
             "'Configuring defaults with .ecorc' below.",
    )


def _add_console_flag(parser, help_on, help_off):
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--console", dest="console", action="store_true", default=True,
        help=help_on + " [default]",
    )
    grp.add_argument("--no-console", dest="console", action="store_false", help=help_off)


def _add_namespace_panel_flag(parser):
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--namespace-panel", dest="namespace_panel", action="store_true", default=True,
        help="dockable Namespace launcher panel [default]",
    )
    grp.add_argument(
        "--no-namespace-panel", dest="namespace_panel", action="store_false",
        help="skip the Namespace launcher panel -- -s's namespace is still built/"
             "usable (in the console, and to reopen a --workspace's widgets), just "
             "without the browsable panel taking up screen space",
    )


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    defaults, ecorc = _load_defaults()
    argv = _normalize_argv(argv, defaults["command"])

    parser = argparse.ArgumentParser(
        prog="eco",
        description="Launch eco in an IPython console, a Qt desktop "
                     "workbench, a Voila dashboard, or JupyterLab.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    p_console = subparsers.add_parser("console", help="Interactive IPython session (default).")
    _add_common_args(p_console, defaults, scope_default=defaults["scope"])
    p_console.add_argument(
        "--profile", default=defaults["profile"],
        help="IPython profile for the console (default: %(default)s)",
    )

    p_desktop = subparsers.add_parser("desktop", help="Qt desktop workbench.")
    # Same scope default as every other subcommand (.ecorc's scope=, or
    # the builtin "bernina") -- .ecorc's choice must apply here too, even
    # when .ecorc's own `command=` isn't "desktop" (e.g. bare `eco`
    # defaults to console, but `eco desktop` should still pick up .ecorc's
    # scope). Explicitly pass -s "" (or set `scope =` blank in .ecorc) for
    # a plain console with no Namespace launcher panel (see
    # eco.widgets.desktop_app) instead of a scope.
    _add_common_args(p_desktop, defaults, scope_default=defaults["scope"])
    _add_console_flag(
        p_desktop,
        help_on="embedded IPython console",
        help_off="skip the embedded console (Namespace launcher panel only, if -s is given)",
    )
    _add_namespace_panel_flag(p_desktop)
    p_desktop.add_argument(
        "--theme", choices=["dark", "light", "none"], default=None,
        help="modern skin, or 'none' for native OS style (default: dark)",
    )
    p_desktop.add_argument(
        "--workspace", default=None, metavar="PATH",
        help="load this workspace file on startup (dock layout + which "
             "namespace entries to reopen) -- see the desktop window's "
             "Workspace menu, 'Save Startup Script...'",
    )

    p_webapp = subparsers.add_parser("webapp", help="Voila dashboard (read-only widgets).")
    _add_common_args(p_webapp, defaults, scope_default=defaults["scope"])

    p_jupyterlab = subparsers.add_parser("jupyterlab", help="JupyterLab, with a preloaded console/kernel.")
    _add_common_args(p_jupyterlab, defaults, scope_default=defaults["scope"])
    _add_console_flag(
        p_jupyterlab,
        help_on="also open a real `jupyter console` on the preloaded eco-<scope> kernel",
        help_off="just open JupyterLab, no separate console (the eco-<scope> kernel is still registered/default if -s is given)",
    )

    p_box_server = subparsers.add_parser(
        "box-server",
        help="Hold this console's connection to the physical manual-control "
             "box (see eco.manual_control.control_box's manual).",
    )
    # Same -s/--scope/-l/--set-rcfile as every other subcommand; -l/--lazy is
    # accepted but unused (box-server never calls init_all -- see the box's
    # own lazy-tree philosophy in control_box.py's manual) so that
    # --set-rcfile / .ecorc keep working uniformly across all subcommands.
    _add_common_args(p_box_server, defaults, scope_default=defaults["scope"])
    p_box_server.add_argument("--box-host", default="ecobox",
                              help="the box to call (default: %(default)s)")
    p_box_server.add_argument("--box-port", type=int, default=8791)
    p_box_server.add_argument("--token-file", default="~/.eco/pendant_token")
    p_box_server.add_argument("--host", default="0.0.0.0",
                              help="admin/health HTTP bind address (default: %(default)s)")
    p_box_server.add_argument("--port", type=int, default=8092,
                              help="admin/health HTTP port (default: %(default)s)")

    args = parser.parse_args(argv)

    if args.set_rcfile is not None:
        _write_rcfile(args)
        return

    if ecorc is not None:
        print("eco: using defaults from {}".format(ecorc), file=sys.stderr)

    if args.command == "console":
        _run_console(args)
    elif args.command == "desktop":
        _run_desktop(args)
    elif args.command == "webapp":
        _run_webapp(args)
    elif args.command == "jupyterlab":
        _run_jupyterlab(args)
    elif args.command == "box-server":
        _run_box_server(args)


if __name__ == "__main__":
    main()
