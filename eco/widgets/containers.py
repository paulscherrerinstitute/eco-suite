"""
Backend-agnostic widget containers -- "super widgets" built by stacking
vertical/horizontal groups of pieces (aligned left/center/right), where
each piece comes from one of eco's four existing widget "flavours"::

    from eco.widgets.containers import (
        stack, assembly_widget, adjustable_control, detector_indicator, viewer,
    )

    panel = stack(
        assembly_widget(some_assembly),
        adjustable_control(some_assembly.motor),
        detector_indicator(some_assembly.gauge),
        viewer(some_camera),
        direction="vertical", align="left",
    )

Picks Qt or ipywidgets automatically via
`eco.utilities.utilities.is_notebook()` -- the same convention already used
by `Assembly.widget()`/`eco.utilities.svg_interactor.launch_svg_viewer` --
lazily importing the right toolkit, and returning the toolkit-native
shape: an object with `.window`/`.stop()` on Qt (eco's usual Qt
widget-wrapper convention, see `EcoDesktopApp._dock_widget_object`), a
plain `ipywidgets.Widget` on notebook (no wrapper class needed there --
`eco.widgets.widget_tray.teardown_widget` already closes a widget's whole
subtree and calls `.stop()` on it and every descendant that has one).

`assembly_widget`/`adjustable_control`/`detector_indicator`/`viewer` each
return a zero-arg *builder* callable, not a built widget -- `stack()`
builds each child lazily, in the SAME backend it resolves to itself, so a
builder never has to guess which toolkit it's being built for. A `stack()`
child may also be an already-built widget (skipping the builder step) for
a caller that built one some other way.

Alignment ("left"/"right"/"center", plus "top"/"bottom"/"start"/"end" as
synonyms -- "start"/"end" map to left/right in a vertical stack, top/bottom
in a horizontal one) is the CROSS-axis position: for a vertical stack,
each child's horizontal position; for a horizontal stack, its vertical
position. `stack()`'s own `align=` is the default for every child; wrap an
individual child in `aligned(child, align)` to override just that one.
"""

_ALIGN_TO_CSS = {
    "left": "flex-start", "start": "flex-start", "top": "flex-start",
    "center": "center",
    "right": "flex-end", "end": "flex-end", "bottom": "flex-end",
}


class _Aligned:
    """Wrapper produced by aligned() -- see the module docstring."""

    __slots__ = ("child", "align")

    def __init__(self, child, align):
        self.child = child
        self.align = align


def aligned(child, align):
    """Wrap `child` (a builder callable or already-built widget, see
    stack()) with a per-child alignment override -- takes precedence over
    the stack's own `align=` for just this one child."""
    return _Aligned(child, align)


def _resolve(child):
    """`child` (a builder callable, an aligned() wrapper around either, or
    an already-built widget), resolved to (built_widget,
    align_override_or_None)."""
    align = None
    if isinstance(child, _Aligned):
        align, child = child.align, child.child
    widget = child() if callable(child) else child
    return widget, align


def _qt_align_flag(direction, align):
    from qtpy import QtCore

    if direction == "vertical":
        flags = {
            "left": QtCore.Qt.AlignLeft, "start": QtCore.Qt.AlignLeft,
            "center": QtCore.Qt.AlignHCenter,
            "right": QtCore.Qt.AlignRight, "end": QtCore.Qt.AlignRight,
        }
        return flags.get(align, QtCore.Qt.AlignLeft)
    flags = {
        "top": QtCore.Qt.AlignTop, "start": QtCore.Qt.AlignTop,
        "center": QtCore.Qt.AlignVCenter,
        "bottom": QtCore.Qt.AlignBottom, "end": QtCore.Qt.AlignBottom,
    }
    return flags.get(align, QtCore.Qt.AlignTop)


def _build_qt(children, direction, align):
    from qtpy import QtWidgets

    window = QtWidgets.QWidget()
    layout_cls = QtWidgets.QVBoxLayout if direction == "vertical" else QtWidgets.QHBoxLayout
    layout = layout_cls(window)

    stoppers = []
    for child in children:
        built, override = _resolve(child)
        inner = getattr(built, "window", built)
        if not isinstance(inner, QtWidgets.QWidget):
            continue  # doesn't fit either convention -- skip rather than crash
        # hide before reparenting, same reasoning as
        # EcoDesktopApp._dock_widget_object: avoids a visible flash if the
        # builder already showed it as a top-level window
        inner.hide()
        flag = _qt_align_flag(direction, override if override is not None else align)
        layout.addWidget(inner, 0, flag)
        inner.show()
        stop = getattr(built, "stop", None)
        if callable(stop):
            stoppers.append(stop)

    class _QtStack:
        def __init__(self):
            self.window = window

        def stop(self):
            for stop_fn in stoppers:
                try:
                    stop_fn()
                except Exception:
                    pass
            if self.window is not None:
                self.window.close()
                self.window = None

    window.show()
    return _QtStack()


def _build_notebook(children, direction, align):
    import ipywidgets as widgets

    built_children = []
    for child in children:
        built, override = _resolve(child)
        eff_align = override if override is not None else align
        try:
            built.layout.align_self = _ALIGN_TO_CSS.get(eff_align, "flex-start")
        except Exception:
            pass
        built_children.append(built)

    box_cls = widgets.VBox if direction == "vertical" else widgets.HBox
    box = box_cls(
        built_children,
        layout=widgets.Layout(align_items=_ALIGN_TO_CSS.get(align, "flex-start")),
    )

    def stop():
        for child in built_children:
            stop_fn = getattr(child, "stop", None)
            if callable(stop_fn):
                try:
                    stop_fn()
                except Exception:
                    pass

    box.stop = stop
    return box


def stack(*children, direction="vertical", align="start"):
    """A VBox (`direction="vertical"`, the default) or HBox
    (`"horizontal"`) of `children`, picking Qt or ipywidgets automatically
    -- see the module docstring for the full contract (children shape,
    alignment vocabulary, return shape per backend)."""
    if direction not in ("vertical", "horizontal"):
        raise ValueError(f"direction must be 'vertical' or 'horizontal', not {direction!r}")
    # lazy import, re-resolved on every call (not bound once at module
    # import time) -- same convention Assembly.widget() itself uses, so a
    # test monkeypatching eco.utilities.utilities.is_notebook actually
    # takes effect here
    from eco.utilities.utilities import is_notebook

    if is_notebook():
        return _build_notebook(children, direction, align)
    return _build_qt(children, direction, align)


def assembly_widget(item, **kwargs):
    """Builder: `item`'s plain property-grid widget, via
    `item._widget_assembly()` if present (see `Assembly._widget_assembly`)
    else `item.widget(normal=True, **kwargs)` -- either way, bypasses any
    `_default_widget` override, so this always gets the generic grid
    regardless of what `item.widget()` alone would otherwise dispatch to."""

    def _build():
        override = getattr(item, "_widget_assembly", None)
        if callable(override):
            return override(**kwargs)
        return item.widget(normal=True, **kwargs)

    return _build


def adjustable_control(item, poll_interval=1.0, title=None):
    """Builder: the tweak/enum/stop-reset control row for one Adjustable
    (or a read-only row for a plain Detector -- see detector_indicator),
    self-polling (own thread, own `.stop()`) -- built from the same
    extracted per-item logic `DisplayQt._build_row`/
    `display_widget.py`'s per-item block use for the normal assembly
    grid, not a reimplementation. See
    `eco.widgets.display_qt.build_adjustable_control_qt`/
    `eco.widgets.display_widget.build_adjustable_control_widget`."""

    def _build():
        from eco.utilities.utilities import is_notebook

        if is_notebook():
            from eco.widgets.display_widget import build_adjustable_control_widget

            return build_adjustable_control_widget(
                item, poll_interval=poll_interval, title=title
            )
        from eco.widgets.display_qt import build_adjustable_control_qt

        return build_adjustable_control_qt(item, poll_interval=poll_interval, title=title)

    return _build


def detector_indicator(item, poll_interval=1.0, title=None):
    """Builder: a read-only live value display for one Detector -- the
    exact same underlying widget as adjustable_control() (it already
    renders read-only for a plain, non-Adjustable Detector); a distinct
    name purely for call-site clarity when the item is known to be
    read-only. Plain label style on both backends -- see
    `eco.widgets.indicator_widgets` for the separate, richer Qt-only
    gauge/LED/dial dashboard-gadget system, unrelated to this one."""
    return adjustable_control(item, poll_interval=poll_interval, title=title)


def viewer(obj, **kwargs):
    """Builder: `obj`'s own custom widget as-is -- `obj.widget(**kwargs)`
    if it has one, else `obj` itself (already a built widget/builder).
    Exists mainly for naming symmetry with the other three in a stack()
    call, e.g. `stack(viewer(cam), adjustable_control(cam.gain), ...)`."""

    def _build():
        widget_fn = getattr(obj, "widget", None)
        if callable(widget_fn):
            return widget_fn(**kwargs)
        return obj

    return _build
