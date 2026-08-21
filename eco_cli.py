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

_COMMANDS = ("console", "desktop", "webapp", "jupyterlab")


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
    if args.theme:
        cmd += ["--theme", args.theme]
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
        "from eco.{scope} import *\n".format(scope=scope)
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


def _run_jupyterlab(args):
    """Open JupyterLab on the packaged notebook (background process -- see
    below for why this can't be the usual _exec). If -s/--scope is given,
    also registers an eco-<scope> kernel (see _register_eco_kernel) and
    makes it JupyterLab's default, so a fresh Console or Notebook opened
    from its own launcher also comes preloaded -- not just the one
    pre-opened dashboard notebook. Then, unless --no-console, also opens a
    real `jupyter console` on that same kernel in *this* terminal --
    mirroring `eco desktop`'s "console on by default, --no-console to skip
    it" -- so you get an actual interactive prompt with eco.<scope>
    preloaded (bare names, exactly like `eco console`), not just a kernel
    sitting there available for JupyterLab's own launcher to pick.
    """
    import subprocess

    notebook = _package_file("voila_app.ipynb")
    os.environ["ECO_SCOPE"] = args.scope or ""
    os.environ["ECO_LAZY"] = "1" if args.lazy else "0"
    lab_cmd = ["jupyter", "lab", notebook]
    kernel_name = None
    if args.scope:
        kernel_name = _register_eco_kernel(args.scope, args.lazy)
        lab_cmd.append("--MappingKernelManager.default_kernel_name={}".format(kernel_name))
        print(
            "eco: registered Jupyter kernel '{}' (preloads eco.{}) as the "
            "default for new consoles/notebooks in this session.".format(
                kernel_name, args.scope
            ),
            file=sys.stderr,
        )

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
  jupyterlab  Open JupyterLab on that same notebook. With -s/--scope, also
              registers an eco-<scope> Jupyter kernel and makes it the
              default, so a fresh Console/Notebook you open from
              JupyterLab's own launcher starts with eco.<scope> already
              loaded too -- not just the one pre-opened notebook. Also
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
        help="scope name (instrument/beamline), e.g. bernina",
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
    # No forced default scope: omitting -s gives a plain console with no
    # Namespace launcher panel (see eco.widgets.desktop_app) instead of
    # silently defaulting to bernina.
    _add_common_args(p_desktop, defaults, scope_default=None)
    _add_console_flag(
        p_desktop,
        help_on="embedded IPython console",
        help_off="skip the embedded console (Namespace launcher panel only, if -s is given)",
    )
    p_desktop.add_argument(
        "--theme", choices=["dark", "light"], default=None,
        help="modern skin (default: none/native)",
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


if __name__ == "__main__":
    main()
