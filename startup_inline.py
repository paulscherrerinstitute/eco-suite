#!/usr/bin/env python

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
# Must stay False, and must match eco/startup_inline.py (which has the long
# form of this comment) -- this file had drifted to True, which silently
# breaks tab completion in exactly the session the facility launcher
# `/sf/bernina/bin/eco` starts (it runs startup_inline_new.py -> this file).
# Jedi doesn't just make completion slow on eco's dynamic namespace/Assembly
# objects, it returns *nothing*: measured on an initialized `prepump`,
# use_jedi=True gave 0 matches where the classic completer gave 33 (the
# 5.8s vs 0.001s / 0-vs-63 figures in eco/startup_inline.py are the same
# effect on a bigger object). The classic completer goes through `dir()`,
# which eco's lazy Proxy.__dir__ answers correctly -- staying "shy" while
# a component is still unresolved (so completing a shared prefix doesn't
# initialize every sibling) and forwarding to the real object once it is.
_ipy.Completer.use_jedi = False

# Must also match eco/startup_inline.py's guarded_eval allow-list addition
# (see CLAUDE.md's "Tab completion" section) -- without it, completing
# *into* an already-resolved component (`prepump.line1_usd.<TAB>`) still
# returns nothing even with jedi off, because IPython's classic completer
# rejects attribute access through eco's lazy Proxy (custom
# `__getattribute__`) when evaluating the multi-level dotted expression.
try:
    import IPython.core.guarded_eval as _guarded_eval

    _guarded_eval.EVALUATION_POLICIES["limited"].allowed_getattr_external.add(
        ("eco.utilities.config", "Proxy")
    )
except Exception:
    pass

# Must also match eco/startup_inline.py's lazy-completion-gate install: the
# guarded_eval fix above means completing into a still-unresolved component
# now silently resolves it (real EPICS init) just from pressing Tab -- this
# turns that into a two-Tab confirmation instead. See CLAUDE.md's "Tab
# completion" section.
from eco.utilities.lazy_completion import install_lazy_completion_gate

install_lazy_completion_gate()
