"""eco.scilog_app -- the SciLog viewer in eco: the scilog-viewer package
(github.com/swissfel/scilog-viewer, ``pip install scilog-viewer`` or the
``eco-suite[scilog]`` extra), started with eco's own parts
(eco.scilog_viewer_plugin: the beamline account, eco's token and settings).

    scilog.viewer()                             # in a session: the pgroup's logbook
    from eco.scilog_app import start; start()   # anywhere else: a separate server process
    python -m eco.scilog_app [--port 8798]      # on its own, without a session

Everything else of scilog_viewer is reachable here too (eco.scilog_app.content,
.server, ...). Nothing in eco imports this package except scilog.viewer() /
widget(), and only when they are called. See docs/scilog_viewer.md.
"""

from pathlib import Path

DEFAULT_PORT = 8798
PLUGIN = "eco.scilog_viewer_plugin"
#: where eco is imported from, for the viewer's server process
_ECO_ROOT = Path(__file__).resolve().parents[2]

INSTALL_HINT = ("The SciLog viewer is the package scilog-viewer: pip install scilog-viewer "
                "(or pip install 'eco-suite[scilog]').")


def _viewer():
    try:
        import scilog_viewer
    except ImportError:
        raise ImportError(INSTALL_HINT) from None
    return scilog_viewer


def start(port=DEFAULT_PORT, open_browser=None, timeout=60, logbook=None):
    """Start the viewer's server (once per session), with eco's parts, and
    print its address; see scilog_viewer.start."""
    return _viewer().start(port=port, open_browser=open_browser, timeout=timeout, logbook=logbook,
                           plugin=PLUGIN, path=[_ECO_ROOT])


def stop():
    """End the viewer's server."""
    return _viewer().stop()


def log():
    """The server's last output lines (warnings, errors)."""
    return _viewer().log()


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    import importlib

    viewer = _viewer()
    try:
        return getattr(viewer, name)
    except AttributeError:
        return importlib.import_module(f"scilog_viewer.{name}")
