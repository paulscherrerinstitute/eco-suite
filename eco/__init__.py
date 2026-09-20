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


def start_desktop(
    theme=None,
    touch=None,
    link_terminal=True,
    scope=None,
    lazy=True,
    with_console=True,
    with_namespace_panel=True,
):
    """Open the Qt desktop workbench (a Spyder/MATLAB-like window: a
    dockable device-widget launcher, plus by default an embedded IPython
    console) from a running terminal session, for easy testing without
    leaving it.

    with_console=True (default): the desktop window has an embedded
    console/kernel. False: no console at all -- the calling terminal
    stays the one and only "master" session; the Namespace panel still
    works fully (opening a widget never needed the console), you just
    can't type Python directly into the desktop window itself.

    with_namespace_panel=True (default): the dockable "Namespace" launcher
    panel is shown. False: it's skipped -- the namespace itself is still
    built and usable (in the console, and to reopen widgets from a loaded
    workspace), just without the browsable panel taking up screen space.

    touch=True (or ECO_QT_TOUCH=1 in the environment): wider dock/splitter
    grab handles and bigger buttons/checkboxes/scrollbars, for touch-screen
    use -- layers on top of `theme` (including theme=None/native), doesn't
    change colors. See eco.widgets.qt_theme.apply_modern_theme.

    Equivalent to::

        from eco.widgets.app_launchers import start_desktop
        start_desktop()
        start_desktop(with_console=False)  # calling terminal stays the only "master" session
        start_desktop(with_namespace_panel=False)  # console only, no browsable launcher panel
        start_desktop(touch=True)  # bigger grab handles/buttons/scrollbars

    See eco.widgets.app_launchers.start_desktop's own docstring for the
    rest (link_terminal, scope, lazy) and for how the namespace used is
    chosen. This wrapper mirrors that function's signature exactly rather
    than taking *args/**kwargs so tab-completion/`?` on `eco.start_desktop`
    itself shows every switch, not just "()" -- imported lazily, like
    ioc_finder above, so plain `import eco` doesn't pay for qtconsole/Qt.
    See also STARTUP_MODES.md.
    """
    from eco.widgets.app_launchers import start_desktop as _start_desktop

    return _start_desktop(
        theme=theme,
        touch=touch,
        link_terminal=link_terminal,
        scope=scope,
        lazy=lazy,
        with_console=with_console,
        with_namespace_panel=with_namespace_panel,
    )


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

