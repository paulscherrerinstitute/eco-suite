"""Let the log-viewer tests run where the full `eco` package can't be
imported (a CI container or a laptop without tkinter/epics/the PSI
packages): if `import eco` fails, register a bare `eco` package object
pointing at this checkout instead, exactly like eco.widgets.
log_viewer_app.BOOTSTRAP does for the viewer subprocess. eco.logs and the
eco.widgets.log_timeline_* modules need nothing from eco/__init__.py.

On a beamline machine `import eco` works and this does nothing.
"""
import sys
import types
from pathlib import Path


def ensure_eco_importable():
    try:
        import eco  # noqa: F401
        return "full"
    except Exception:
        pass
    root = Path(__file__).resolve().parents[1]
    pkg = types.ModuleType("eco")
    pkg.__path__ = [str(root / "eco")]
    sys.modules["eco"] = pkg
    return "light"
