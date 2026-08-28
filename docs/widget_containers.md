# Widget containers ("super widgets")

`eco.widgets.containers` lets you compose a custom panel out of eco's
existing widget pieces — stack them vertically or horizontally, align each
one left/center/right, and mix any of the four widget "flavours" already
used throughout eco. It works identically whether the session is the Qt
desktop workbench or a notebook (JupyterLab/Voila) — see
{doc}`widget_views` for background on the per-item row these containers are
built from.

```python
from eco.widgets.containers import (
    stack, aligned, assembly_widget, adjustable_control, detector_indicator, viewer,
)

panel = stack(
    assembly_widget(some_assembly),
    adjustable_control(some_assembly.motor),
    detector_indicator(some_assembly.gauge),
    aligned(viewer(some_camera), align="center"),
    direction="vertical", align="left",
)
```

## The `_widget_*` convention

Every widget-building method an eco object offers — beyond the generic
`.widget()` entry point itself — is named `_widget_<something>`, so the
full set of widgets an object can produce is discoverable just by grepping
(or tab-completing) that prefix. Four rules tie this together:

1. **`.widget()` is the entry point.** It returns *the* widget for this
   object, and any object may override `.widget()` itself directly if it
   wants full control over that.
2. **Everything else is `_widget_<something>`.** `Assembly`'s own generic
   property grid lives at `_widget_assembly()`; `.widget()`'s base
   implementation is a thin dispatcher that returns `self._widget_assembly()`
   unless the class sets `_default_widget = "_widget_<something>"` to point
   at a different one (e.g. a camera's live-stream view). Either way of
   customizing — overriding `.widget()`, or pointing `_default_widget` at
   an alternate `_widget_*` method — is valid; `_default_widget` is just
   the lower-effort option when you don't need to touch dispatch logic
   itself.
3. **SVG panels follow the same pattern, for `.show()`.** Any assembly
   with a dynamic SVG panel (see {doc}`widget_views`'s background on
   `Assembly.show()`) implements it as `_widget_svg_panel(self, live=False,
   **kwargs)` — every SVG panel, now and in the future, uses this exact
   name, the same way `_widget_assembly` is Assembly's fixed name.
   `show()` picks it up automatically if it exists; `show()` itself can
   also be overridden outright by a subclass that needs to (e.g.
   `PrepumpSystem.show()` just changes a default), same escape hatch as
   `.widget()`.
4. **A `_widget_viewer` (or any other name) is just an ordinary per-device
   method** — nothing in `Assembly` or `eco.widgets.containers` treats
   "viewer" as a generic, defaulted concept. It only exists, and only
   becomes the default, where a specific device class defines it and
   points `_default_widget` at it (today: `AxisPTZStreamQt`/`CameraBasler`/
   `CameraPCO`'s live-stream views). `containers.viewer()` below is a thin
   pass-through to whatever `.widget()` resolves to — it doesn't assume or
   require a `_widget_viewer` method to exist.

## The four widget flavours

Each of these returns a zero-argument *builder* — nothing is built until
`stack()` actually calls it, in whichever backend the stack itself resolves
to (Qt vs. notebook, decided once, at the top). A builder is never told
which backend it's building for; it figures that out the same way
`Assembly.widget()` itself does.

- {py:func}`~eco.widgets.containers.assembly_widget` — the plain
  property-grid for an `Assembly` (see {doc}`widget_views`'s "Default
  per-item row"): `item._widget_assembly()` if present, else
  `item.widget(normal=True)` as a fallback for anything that hasn't got
  around to exposing `_widget_assembly` directly. Bypasses any
  `_default_widget` override either way.
- {py:func}`~eco.widgets.containers.adjustable_control` — the
  tweak/enum/stop-reset control row for one `Adjustable` (or a read-only
  row if given a plain `Detector` — see `detector_indicator` below),
  self-polling in its own background thread. This is the exact same
  per-item control logic the normal assembly grid uses
  ({py:class}`~eco.widgets.display_qt.DisplayQt`/
  `eco.widgets.display_widget.make_assembly_widget`) — extracted into a
  shared function both the grid and this builder call, not a second
  implementation.
- {py:func}`~eco.widgets.containers.detector_indicator` — a read-only live
  value display for one `Detector`. Literally the same builder as
  `adjustable_control` (which already renders read-only for a
  non-Adjustable `Detector`); a separate name purely so a call site can say
  what it means. Plain label style on both backends, deliberately — see
  {doc}`widget_views`'s "Indicator / gadget widgets" section for the
  separate, richer Qt-only LED/gauge/dial dashboard system, which this
  does not replace.
- {py:func}`~eco.widgets.containers.viewer` — any custom, purpose-built
  widget as-is: `obj.widget(**kwargs)` if `obj` has one (a camera stream
  viewer, an SVG panel, ...), else `obj` itself if it's already a built
  widget. Nothing defaults here (see rule 4 above) — exists purely for
  naming symmetry with the other three inside a `stack()` call.

A `stack()` child can also be an already-built widget instead of a
builder — useful if you built one some other way and just want to place
it.

## `stack()` and `aligned()`

```{py:function} eco.widgets.containers.stack(*children, direction="vertical", align="start")
```

`direction`: `"vertical"` (the default, a column) or `"horizontal"` (a
row). `align`: the CROSS-axis position of each child — for a vertical
stack, each child's horizontal position (`"left"`/`"center"`/`"right"`);
for a horizontal stack, its vertical position
(`"top"`/`"center"`/`"bottom"`). `"start"`/`"end"` work as backend-neutral
synonyms for left/top and right/bottom respectively.

Wrap an individual child in `aligned(child, align)` to override the
stack's own default for just that one child:

```python
stack(a, aligned(b, "center"), c, align="left")  # a and c stay left, b is centered
```

**Return shape** follows eco's existing per-backend convention (nothing
new here — the same shapes `Assembly.widget()` itself already returns):

- **Qt**: an object with `.window` (a `QWidget` wrapping a
  `QVBoxLayout`/`QHBoxLayout`) and `.stop()` (stops every child that has
  one — same idea as `EcoDesktopApp._dock_widget_object`'s dock teardown).
  Dock it into the desktop workbench, or call `.window.show()`/`.stop()`
  directly.
- **Notebook**: the built `ipywidgets.VBox`/`HBox` directly — no wrapper
  needed, since `eco.widgets.widget_tray.teardown_widget` already closes a
  widget's whole subtree and calls `.stop()` on it and every descendant
  that has one.

A stack nests: `stack(...)`'s own return value is itself a valid child of
another `stack(...)` call, on either backend.
