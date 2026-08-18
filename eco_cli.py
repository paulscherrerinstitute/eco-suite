#!/usr/bin/env python
"""Console entry point for eco (the ``eco`` command).

This module is deliberately kept OUTSIDE the ``eco`` package and imports only
the standard library. Importing it must NOT import ``eco`` itself: the heavy
package import (EPICS, cam_server, ...) is expensive and best done once, inside
the fresh session that this launcher starts -- not in the parent process that
only needs to build a command line.

Three front-ends are available via ``--ui`` (default: ``shell``):

    shell   Interactive IPython session (the traditional eco startup). Equivalent to
            ipython --profile=eco --no-banner -i -c "run <eco>/startup_inline.py -l -s bernina"
    lab     Open JupyterLab on the packaged eco notebook (eco/voila_app.ipynb).
    voila   Serve that notebook as a Voila dashboard: the chosen namespace's
            widget, with the assembly browser to navigate to more components.

JupyterLab and Voila are OPTIONAL -- they are not hard dependencies. Install
them on demand, e.g. ``pip install eco[lab]`` / ``eco[voila]`` or via conda.
If the command is missing, this launcher prints a hint instead of a traceback.

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

_UI_CHOICES = ("shell", "lab", "voila")


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
    script = _package_file("startup_inline.py")
    run_cmd = "run {}".format(script)
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


def main(argv=None):
    defaults, ecorc = _load_defaults()

    parser = argparse.ArgumentParser(
        prog="eco",
        description="Launch eco in an IPython shell, JupyterLab, or a Voila dashboard.",
    )
    parser.add_argument(
        "-s", "--scope", default=defaults["scope"],
        help="scope name (instrument/beamline), e.g. bernina",
    )
    parser.add_argument(
        "--ui", choices=_UI_CHOICES, default=defaults["ui"],
        help="front-end to start: shell (IPython), lab (JupyterLab), or voila "
             "(widget dashboard). Default: %(default)s",
    )
    parser.add_argument(
        "--profile", default=defaults["profile"],
        help="IPython profile for the shell UI (default: %(default)s)",
    )
    lazy_grp = parser.add_mutually_exclusive_group()
    lazy_grp.add_argument(
        "-l", "--lazy", dest="lazy", action="store_true", default=defaults["lazy"],
        help="lazy initialisation of the scope (defer device instantiation)",
    )
    lazy_grp.add_argument(
        "--no-lazy", dest="lazy", action="store_false",
        help="disable lazy initialisation (build all devices at startup)",
    )
    args = parser.parse_args(argv)

    if ecorc is not None:
        print("eco: using defaults from {}".format(ecorc), file=sys.stderr)

    if args.ui == "shell":
        _run_shell(args)
    else:
        _run_notebook(args, args.ui)


if __name__ == "__main__":
    main()
