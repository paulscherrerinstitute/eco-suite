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
