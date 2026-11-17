"""python -m eco.scilog_app [--port N] [--no-browser]: the SciLog viewer's
server (python -m scilog_viewer) with eco's parts (--plugin
eco.scilog_viewer_plugin)."""

import runpy
import sys

from eco.scilog_app import INSTALL_HINT, PLUGIN

try:
    import scilog_viewer  # noqa: F401
except ImportError:
    raise SystemExit(INSTALL_HINT) from None

if "--plugin" not in sys.argv:
    sys.argv[1:1] = ["--plugin", PLUGIN]
runpy.run_module("scilog_viewer", run_name="__main__", alter_sys=True)
