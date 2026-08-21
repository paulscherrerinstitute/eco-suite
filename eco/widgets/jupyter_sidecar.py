"""
JupyterLab-only helper: open an eco widget in a Sidecar panel -- a real
Lumino dock widget the user can then freely drag/tab/tile via
JupyterLab's own UI, the closest browser-side analogue to the Qt side's
QDockWidget (see eco.widgets.desktop_app / camserver_panel_qt). Needs the
`sidecar` package (``pip install sidecar``) and a JupyterLab (not classic
Notebook, and NOT Voila -- Voila has no Lumino shell to dock into; use
eco.widgets.widget_tray.make_namespace_dashboard there instead) session --
degrades to a clear RuntimeError, not a traceback, if `sidecar` isn't
installed.

    from eco.widgets.jupyter_sidecar import open_in_sidecar
    sc = open_in_sidecar(namespace.cam_west, anchor="split-right")
    # keep `sc` referenced (e.g. a session-level variable) -- once it's
    # garbage collected the panel closes

`anchor`: "split-right"/"split-left"/"split-top"/"split-bottom" to tile
beside the notebook, or "tab-before"/"tab-after" to tab alongside it --
JupyterLab's own drag/drop still works on the panel afterward regardless
of the initial placement.

WORKSPACE PERSISTENCE: unlike the Qt desktop app (eco.widgets.desktop_app,
which has to implement its own save/reload since Qt has no such thing
built in), JupyterLab already persists panel layout -- including Sidecar
panels, since each becomes a real registered shell widget -- via its own
"Workspaces" feature (command palette, or `jupyter lab workspaces export/
import` on the CLI). save_workspace/load_workspace below are a thin
wrapper around that CLI, named to match the Qt side's
EcoDesktopApp.save_workspace/load_workspace for discoverability -- they
don't reimplement anything, they just save you typing the CLI command.
"""
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_FILE = Path.home() / ".eco" / "jupyterlab_workspace.json"


def open_in_sidecar(obj, title=None, anchor="split-right"):
    """Open `obj.widget()` in a JupyterLab Sidecar panel. Returns the
    Sidecar instance -- keep a reference (e.g. ``sc = open_in_sidecar(...)``)
    so it isn't garbage-collected and the panel closed with it."""
    try:
        from sidecar import Sidecar
    except ImportError as exc:
        raise RuntimeError(
            "eco.widgets.jupyter_sidecar needs the 'sidecar' package "
            "(pip install sidecar) and a JupyterLab session -- it does not "
            "work in classic Notebook or Voila (see eco.widgets.widget_tray "
            "for those instead)."
        ) from exc

    from IPython.display import display

    label = title or getattr(obj, "name", None) or repr(obj)
    sc = Sidecar(title=str(label), anchor=anchor)
    with sc:
        display(obj.widget())
    return sc


def save_workspace(path=None):
    """Export the current JupyterLab workspace (dock layout, including
    any Sidecar panels) via `jupyter lab workspaces export` -- the
    browser-side counterpart of EcoDesktopApp.save_workspace() on the Qt
    side. `path`: file to write (default: DEFAULT_WORKSPACE_FILE)."""
    path = Path(path) if path else DEFAULT_WORKSPACE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        try:
            subprocess.run(["jupyter", "lab", "workspaces", "export"], stdout=f, check=True)
        except FileNotFoundError as exc:
            raise RuntimeError("'jupyter' not found -- is JupyterLab installed?") from exc
    return path


def load_workspace(path=None):
    """Import a previously eco.widgets.jupyter_sidecar.save_workspace-d
    JupyterLab workspace via `jupyter lab workspaces import`. Only takes
    effect for JupyterLab sessions opened against that workspace
    afterward (JupyterLab reads its workspace at page load, same as the
    CLI command run by hand would) -- not a live in-place reload of the
    current page."""
    path = Path(path) if path else DEFAULT_WORKSPACE_FILE
    if not path.exists():
        raise FileNotFoundError(f"no saved JupyterLab workspace at {path}")
    try:
        subprocess.run(["jupyter", "lab", "workspaces", "import", str(path)], check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("'jupyter' not found -- is JupyterLab installed?") from exc
    return path
