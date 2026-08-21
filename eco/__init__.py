try:
    from eco.elements.protocols import (
        Adjustable,
        AdjustableEnum,
        Detector,
        DetectorEnum,
        MonitorableValueUpdate,
    )
except:
    print("cannot import Prototypic protocol classes")

from eco.elements.assembly import Assembly

import eco.logs

from eco import defaults


def ioc_finder(*args, **kwargs):
    """Launch the IOC finder GUI (search IOCs by name/PV, check console
    status, view console output, restart) in its own, non-blocking window.

    Equivalent to::

        from eco.widgets.ioc_finder_qt import make_ioc_finder_qt_window
        make_ioc_finder_qt_window()

    Imported lazily -- the qtpy/Qt and requests dependencies are only
    touched when this is actually called, so plain `import eco` doesn't pay
    for them. Returns the `IocFinderQtWindow` handle (`.stop()` to close it).
    """
    from eco.widgets.ioc_finder_qt import make_ioc_finder_qt_window

    return make_ioc_finder_qt_window(*args, **kwargs)


def start_desktop(*args, **kwargs):
    """Open the Qt desktop workbench (a Spyder/MATLAB-like window: a
    dockable device-widget launcher, plus by default an embedded IPython
    console) from a running terminal session, for easy testing without
    leaving it.

    Equivalent to::

        from eco.widgets.app_launchers import start_desktop
        start_desktop()
        start_desktop(with_console=False)  # calling terminal stays the only "master" session

    See eco.widgets.app_launchers.start_desktop for the full signature
    (theme, link_terminal, scope, lazy, with_console) -- imported lazily,
    like ioc_finder above, so plain `import eco` doesn't pay for
    qtconsole/Qt. See also STARTUP_MODES.md.
    """
    from eco.widgets.app_launchers import start_desktop as _start_desktop

    return _start_desktop(*args, **kwargs)


def start_console(*args, **kwargs):
    """Open a brand-new, independent console (its own Jupyter kernel --
    not sharing state with this session or any other eco window), for a
    second working session alongside the one you're already in.

    Equivalent to::

        from eco.widgets.app_launchers import start_console
        start_console()               # a standalone Qt console window
        start_console(kind="jupyterlab")  # a fresh JupyterLab notebook/kernel

    See eco.widgets.app_launchers.start_console for the full signature
    (kind, scope, lazy, label) -- imported lazily, like start_desktop
    above. Every kernel opened this way (and every eco.start_desktop()
    kernel) is tracked, with its full input/output history, in
    eco.widgets.kernel_registry -- see eco.widgets.kernel_registry.
    sessions_summary() to list what's running. See also STARTUP_MODES.md.
    """
    from eco.widgets.app_launchers import start_console as _start_console

    return _start_console(*args, **kwargs)


def start_jupyterlab(*args, **kwargs):
    """Open eco's packaged notebook in JupyterLab -- attaches to an
    already-running JupyterLab server if there is one, else starts a new
    one as a background subprocess (your terminal session keeps working
    either way). See eco.widgets.app_launchers.start_jupyterlab for the
    full signature (attach, notebook). See also STARTUP_MODES.md."""
    from eco.widgets.app_launchers import start_jupyterlab as _start_jupyterlab

    return _start_jupyterlab(*args, **kwargs)


def start_webapp(*args, **kwargs):
    """Serve eco's packaged notebook as a Voila dashboard, as a background
    subprocess (your terminal session keeps working). See
    eco.widgets.app_launchers.start_webapp for the full signature (attach,
    notebook, port). See also STARTUP_MODES.md."""
    from eco.widgets.app_launchers import start_webapp as _start_webapp

    return _start_webapp(*args, **kwargs)

