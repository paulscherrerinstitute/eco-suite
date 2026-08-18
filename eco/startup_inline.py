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
_ipy.Completer.use_jedi = True
