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


class NamespaceDashboard:
    """Return value of open_namespace_dashboard() -- the JupyterLab
    counterpart to eco.widgets.desktop_app.EcoDesktopApp: a Namespace
    launcher panel (browse every registered name, initialize lazy ones on
    click, Required checkboxes -- same eco.widgets.widget_tray.
    NamespaceLauncherWidget table the Voila dashboard uses) docked in its
    own Sidecar panel, with each opened device routed into its own
    Sidecar panel too. Keeps every Sidecar it creates referenced (this is
    what actually keeps them from being garbage-collected/closed) --
    call .close() to tear the whole dashboard down at once."""

    def __init__(self, namespace, anchor_launcher, anchor_widgets):
        from IPython.display import display

        from eco.widgets.widget_tray import NamespaceLauncherWidget
        from sidecar import Sidecar

        self._anchor_widgets = anchor_widgets
        self._opened = {}  # name -> Sidecar

        self.launcher = NamespaceLauncherWidget(namespace, on_open=self._on_open)
        self.launcher_sidecar = Sidecar(title="Namespace", anchor=anchor_launcher)
        with self.launcher_sidecar:
            display(self.launcher)

    def _on_open(self, obj, label):
        existing = self._opened.get(label)
        if existing is not None:
            return  # already open -- Sidecar has no "bring to front"; leave it be
        self._opened[label] = open_in_sidecar(obj, title=label, anchor=self._anchor_widgets)

    def close(self):
        """Close every Sidecar panel this dashboard opened, including the
        launcher itself."""
        for sc in self._opened.values():
            try:
                sc.close()
            except Exception:
                logger.exception("closing a sidecar panel failed")
        self._opened.clear()
        try:
            self.launcher_sidecar.close()
        except Exception:
            logger.exception("closing the launcher sidecar failed")


def open_namespace_dashboard(namespace, anchor_launcher="split-left", anchor_widgets="split-right"):
    """JupyterLab-only: open eco's Namespace launcher (the same Name/
    Required table eco desktop's dockable panel and the Voila dashboard
    both use) in its own narrow Sidecar panel, and route each device you
    open from it into its own Sidecar panel too -- the closest browser-
    side analogue to eco desktop's dockable Namespace panel plus tiled
    device docks, instead of eco.widgets.widget_tray.make_namespace_
    dashboard's single flat inline page (built for Voila, which has no
    docking shell for this to make sense in). Keep the returned object
    referenced (e.g. a session-level variable) -- once every reference to
    it (and the Sidecars it holds) is gone, the panels close.

        from eco.widgets.jupyter_sidecar import open_namespace_dashboard
        dashboard = open_namespace_dashboard(bernina.namespace)
    """
    try:
        import sidecar  # noqa: F401 -- see the ImportError message below
    except ImportError as exc:
        raise RuntimeError(
            "eco.widgets.jupyter_sidecar needs the 'sidecar' package "
            "(pip install sidecar) and a JupyterLab session -- it does not "
            "work in classic Notebook or Voila (see eco.widgets.widget_tray "
            "for those instead)."
        ) from exc
    return NamespaceDashboard(namespace, anchor_launcher, anchor_widgets)


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
