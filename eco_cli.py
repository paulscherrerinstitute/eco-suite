#!/usr/bin/env python
"""Console entry point for eco (the ``eco`` command).

This module is deliberately kept OUTSIDE the ``eco`` package and imports only
the standard library. Importing it must NOT import ``eco`` itself: the heavy
package import (EPICS, cam_server, ...) is expensive and best done once, inside
the fresh session that this launcher starts -- not in the parent process that
only needs to build a command line.

Four front-ends are available via ``--ui`` (default: ``shell``):

    shell   Interactive IPython session (the traditional eco startup). Equivalent to
            ipython --profile=eco --no-banner -i -c "run <eco>/startup_inline.py -l -s bernina"
    lab     Open JupyterLab on the packaged eco notebook (eco/voila_app.ipynb).
    voila   Serve that notebook as a Voila dashboard: the chosen namespace's
            widget, with the assembly browser to navigate to more components.
    desktop A Spyder/MATLAB-like Qt workbench window: an embedded IPython
            console running the namespace, plus a dockable panel to browse
            and open device widgets. See eco.widgets.desktop_app.

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
    scope = bernina
    profile = eco
    lazy = true
    ui = shell

Any of these can be overridden on the command line, e.g. ``eco -s alvra`` or
``eco --ui voila``.
"""

import argparse
import configparser
import importlib.util
import os
import sys
from pathlib import Path

# Built-in defaults, overridden by .ecorc, overridden again by CLI flags.
_BUILTIN_DEFAULTS = {
    "scope": "bernina",
    "profile": "eco",
    "lazy": True,
    "ui": "shell",
}

_UI_CHOICES = ("shell", "lab", "voila", "desktop")


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
            if "scope" in sec:
                defaults["scope"] = sec.get("scope") or None
            if "profile" in sec:
                defaults["profile"] = sec.get("profile")
            if "lazy" in sec:
                defaults["lazy"] = sec.getboolean("lazy")
            if "ui" in sec:
                defaults["ui"] = sec.get("ui")
    return defaults, path


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


def _run_shell(args):
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


def _run_notebook(args, tool):
    """Launch JupyterLab (tool='lab') or Voila (tool='voila') on the packaged
    notebook, passing the scope/lazy choice through the environment.
    """
    notebook = _package_file("voila_app.ipynb")
    # The notebook reads these to know which namespace to open.
    os.environ["ECO_SCOPE"] = args.scope or ""
    os.environ["ECO_LAZY"] = "1" if args.lazy else "0"
    if tool == "lab":
        _exec(
            ["jupyter", "lab", notebook],
            "Install it with e.g. `pip install eco[lab]` or `conda install jupyterlab`.",
        )
    else:  # voila
        _exec(
            ["voila", notebook],
            "Install it with e.g. `pip install eco[voila]` or `conda install voila`.",
        )


def _run_desktop(args):
    """Launch the Qt desktop workbench (see eco.widgets.desktop_app) as a
    fresh process -- exec, like _run_shell/_run_notebook above, so this
    launcher module stays import-eco-free (see the module docstring)."""
    cmd = [sys.executable, "-m", "eco.widgets.desktop_app"]
    if args.scope:
        cmd += ["--scope", args.scope]
    cmd += ["--lazy"] if args.lazy else ["--no-lazy"]
    _exec(
        cmd,
        "The desktop UI needs qtconsole and a Qt binding (qtpy + PyQt5/PySide6) "
        "in this environment.",
    )


_EPILOG = """\
UI front-ends (--ui):
  shell    Interactive IPython session (default). The traditional eco
           startup: an IPython shell with the chosen scope's devices
           loaded into its namespace, ready to use interactively.
  lab      Open JupyterLab on the packaged eco notebook
           (eco/voila_app.ipynb), for notebook-based work.
  voila    Serve that notebook as a read-only Voila dashboard: widgets
           for the chosen namespace, with an assembly browser to
           navigate to more components. No code editing, just the UI.
  desktop  A Spyder/MATLAB-like Qt workbench window: an embedded
           IPython console running the namespace, plus a dockable
           panel to browse and open device widgets. Needs qtconsole
           and a Qt binding (qtpy + PyQt5/PySide6) in this environment;
           see eco.widgets.desktop_app.

Configuring defaults with .ecorc:
  A bare `eco` reads its defaults (scope/profile/lazy/ui) from an .ecorc
  (INI) file, so you don't have to repeat flags every time. Lookup order,
  first match wins: $ECORC, ./.ecorc, ~/.ecorc. Example file:

      [eco]
      scope = bernina
      profile = eco
      lazy = true
      ui = shell

  Anything in it can still be overridden on the command line, e.g.
  `eco -s alvra` or `eco --ui voila`.

  --set-rcfile [PATH] writes -s/--profile/-l/--ui exactly as given on
  THIS command line into such a file, instead of launching anything:

      eco -s alvra --ui voila --set-rcfile     # writes ~/.ecorc
      eco -s alvra --set-rcfile ./.ecorc       # writes a project-local one

  so a later bare `eco` (from that directory, or anywhere if ~/.ecorc)
  picks these settings up automatically.
"""


def _write_rcfile(args):
    """Write the resolved scope/profile/lazy/ui from this invocation into an
    .ecorc file at ``args.set_rcfile`` (see --set-rcfile)."""
    cfg = configparser.ConfigParser()
    cfg["eco"] = {
        "scope": args.scope or "",
        "profile": args.profile,
        "lazy": "true" if args.lazy else "false",
        "ui": args.ui,
    }
    path = Path(args.set_rcfile)
    with path.open("w") as fp:
        cfg.write(fp)
    print("eco: wrote {}".format(path), file=sys.stderr)
    for key, value in cfg["eco"].items():
        print("  {} = {}".format(key, value), file=sys.stderr)


def main(argv=None):
    defaults, ecorc = _load_defaults()

    parser = argparse.ArgumentParser(
        prog="eco",
        description="Launch eco in an IPython shell, JupyterLab, a Voila "
                     "dashboard, or a Qt desktop workbench.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-s", "--scope", default=defaults["scope"],
        help="scope name (instrument/beamline), e.g. bernina",
    )
    parser.add_argument(
        "--ui", choices=_UI_CHOICES, default=defaults["ui"],
        help="front-end to start: shell (IPython), lab (JupyterLab), voila "
             "(widget dashboard), or desktop (Qt workbench, see below). "
             "Default: %(default)s",
    )
    parser.add_argument(
        "--profile", default=defaults["profile"],
        help="IPython profile for the shell UI (default: %(default)s)",
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
        help="write -s/--profile/-l/--ui from this invocation into an .ecorc "
             "file (PATH, default: ~/.ecorc) and exit instead of launching. "
             "See 'Configuring defaults with .ecorc' below.",
    )
    args = parser.parse_args(argv)

    if args.set_rcfile is not None:
        _write_rcfile(args)
        return

    if ecorc is not None:
        print("eco: using defaults from {}".format(ecorc), file=sys.stderr)

    if args.ui == "shell":
        _run_shell(args)
    elif args.ui == "desktop":
        _run_desktop(args)
    else:
        _run_notebook(args, args.ui)


if __name__ == "__main__":
    main()
