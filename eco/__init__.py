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

