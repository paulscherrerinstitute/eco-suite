#!/usr/bin/env python
"""Interactive eco startup script.

This is executed *inside* an IPython session (via ``%run``) so that the
pylab / numpy star-imports and the scope namespace land in the interactive
user namespace. It is normally launched through the ``eco`` console command
(see :mod:`eco.cli`), which is equivalent to::

    ipython --profile=eco --no-banner -i -c "run <this file> -l -s bernina"
"""

import os
os.environ["EPICS_CA_MAX_ARRAY_BYTES"] = "120000000"

## pylab activity >>>>
import numpy
import matplotlib
from matplotlib import pylab, mlab, pyplot

np = numpy
plt = pyplot

from IPython.core.pylabtools import figsize, getfigs

from pylab import *
from numpy import *

plt.ion()
## pylab activity <<<<


from eco import ecocnf
from eco.utilities.config import Terminal
import sys

import argparse

parser = argparse.ArgumentParser(description="eco startup utility")

parser.add_argument(
    "-s",
    "--scope",
    type=str,
    default=None,
    help="scope name, usually instrument or beamline",
)
parser.add_argument(
    "-a",
    "--scopes_available",
    action="store_true",
    default=False,
    help="print available scopes.",
)
parser.add_argument(
    "-l", "--lazy", action="store_true", default=False, help="lazy initialisation"
)
parser.add_argument(
    "--shell", action="store_true", default=False, help="open eco in ipython shell"
)
parser.add_argument(
    "--pylab", type=bool, default=True, help="open ipython shell in pylab mode"
)

arguments = parser.parse_args()

scope = arguments.scope
# scope = 'bernina'

if arguments.scopes_available:
    print("{:<15s}{:<15s}{:<15s}".format("module", "name", "facility"))
    for ts in ecocnf.scopes:
        print(
            " {:<14s} {:<14s} {:<14s}".format(ts["module"], ts["name"], ts["facility"])
        )


print(
    "                       ___ _______\n                      / -_) __/ _ \ \n Experiment Control   \__/\__/\___/ \n\n"
)

term = Terminal(scope=scope)

if scope:
    if arguments.lazy:
        ecocnf.startup_lazy = True
    exec(f"import eco.{scope} as {scope}")
    exec(f"from eco.{scope} import *")

term.set_title()
from IPython import get_ipython

_ipy = get_ipython()
# Jedi-based completion asks every candidate for introspection data to
# build its menu -- for eco's lazy device proxies (eco.utilities.config.
# Proxy) that used to mean fully resolving one (real EPICS calls) just
# from typing its name (see console_kernel.build_console_widget's
# docstring, which disables this the same way for eco desktop's console,
# for the fuller measurement: 62.7s for one complex device). Proxy.__dir__
# fixes the worst of that regardless of this setting now, but Jedi was
# also independently measured slower (5.8s vs 0.001s) and less correct
# (0 matches vs 63) than the classic completer for this dynamic a
# namespace -- so still off here too.
_ipy.Completer.use_jedi = False

# use_jedi=False alone is not enough to complete *into* an already-resolved
# namespace component (e.g. `prepump.line1_usd.<TAB>` after `prepump` has
# been used) -- see CLAUDE.md's "Tab completion" section for the full
# writeup. Short version: IPython's classic completer still evaluates a
# multi-level dotted expression through `IPython.core.guarded_eval`, which
# refuses to `getattr()` through any object whose `__getattribute__` isn't
# the stock one from a small allow-list -- and eco's lazy Proxy necessarily
# overrides `__getattribute__` to do its forwarding, so it's rejected even
# once fully resolved. Registering Proxy in the same allow-list slot
# IPython already uses for pandas' DataFrame/Series (objects with a custom
# but "safe" __getattr__) fixes it. Best-effort: guarded_eval is an
# internal, fairly young IPython module, so a future IPython that reshapes
# it should degrade to today's (partially broken) behaviour, not a startup
# crash.
try:
    import IPython.core.guarded_eval as _guarded_eval

    _guarded_eval.EVALUATION_POLICIES["limited"].allowed_getattr_external.add(
        ("eco.utilities.config", "Proxy")
    )
except Exception:
    pass

# The guarded_eval fix above has a side effect: completing *into* a still-
# unresolved component now silently triggers its real initialisation, with
# no warning, just from pressing Tab -- see CLAUDE.md's "Tab completion"
# section. This turns that silent resolve into a two-Tab confirmation
# instead (1st Tab: warn, no completions; 2nd Tab: resolve and complete).
from eco.utilities.lazy_completion import install_lazy_completion_gate

install_lazy_completion_gate()

from eco.widgets import kernel_registry

kernel_registry.install_shell_logger(kind="console", label=scope or "console")
