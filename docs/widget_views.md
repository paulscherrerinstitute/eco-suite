# Adjustable/Detector widget views

Every {py:class}`~eco.elements.protocols.Adjustable` or
{py:class}`~eco.elements.protocols.Detector` in an assembly can be looked at
live from either front end — the Qt desktop workbench or a browser
(JupyterLab/Voila, via ipywidgets). Both build one row per item from the same
underlying `get_current_value()`/`set_target_value()` interface, but they are
two independent implementations with different capabilities, described below.

## Default per-item row

### Qt desktop (`eco.widgets.display_qt`)

The desktop app's dockable property grid ({py:class}`~eco.widgets.display_qt.DisplayQt`,
also used standalone via `make_assembly_qt_window`) shows a **name / current /
control** row per item, polled on a background thread:

![Default Qt property-grid rows for a numeric Adjustable ("energy"), an enum Adjustable ("shutter"), and a read-only Detector ("intensity"). The numeric row has a step field, up/down tweak buttons, an absolute-value entry (commits on Enter/focus-out), and Stop/Reset buttons; the enum row is a dropdown with the same Stop/Reset pair; the Detector row is plain read-only text.](images/widget_default_row_qt.png)

- **Detector-only** items are read-only text — no controls.
- **Numeric Adjustables** get a step-size field, ▲/▼ tweak buttons (move
  relative to the *live* current value, not the last-displayed one), an
  absolute-value entry, and 🛑 Stop / ↺ Reset (back to the value from when the
  row was built).
- **Enum-valued Adjustables** (anything exposing `enum_strs`, e.g.
  `AdjustablePvEnum`) get a dropdown instead of step/absolute controls, same
  Stop/Reset pair.
- Right-clicking a name label opens **"Add indicator"** — see below.

### ipywidgets (`eco.widgets.assembly_browser`)

The browser-side namespace/assembly browser used by the `lab`/`voila`
front ends builds each row as a plain ipywidgets `HBox`
({py:class}`~eco.widgets.assembly_browser._ItemWidget`):

![Default ipywidgets rows for the same three items: name, type, current value (repr), then either a "new value" text box + Set button (Adjustable) or a Refresh button (Detector).](images/widget_default_row_ipywidgets.png)

- Name, Python type, and `repr()` of the current value are always shown.
- **Adjustables** get a free-text entry plus a **Set** button (best-effort
  type coercion against the current value — bool/int/float/fallback JSON);
  there is no step/tweak concept here, no Stop, and no live polling — value
  updates only on explicit **Refresh** (Detectors) or after a successful
  **Set** (Adjustables).
- **Detectors** get a **Refresh** button only.

This is a simpler, more manual sibling of the Qt row — it works anywhere a
kernel/comm can reach a browser (including Voila, with no desktop Qt
involved) but does not poll live and has no notion of enum dropdowns, tweak
steps, or in-flight move stopping.

## Indicator / gadget widgets

For a LabVIEW-style live panel — LEDs, gauges, dials — instead of a grid row,
eco provides small standalone gadgets, one per item: a richer Qt version and a
first-pass, stock-`ipywidgets` browser version.

### Qt (`eco.widgets.indicator_widgets`)

`eco.widgets.indicator_widgets` provides gadgets meant to be pulled out of
the property grid and dropped onto a
{py:class}`~eco.widgets.dashboard_qt.Dashboard`:

![Five indicator gadgets against dummy items: a green LED for a boolean shutter state, a vertical bar gauge for a pressure Detector, a semicircular analog gauge with a needle for an energy readback, a rotary Dial in enum "mode-select" form showing every choice (standby/ready/running/fault) around the ring, and a horizontal Slider with a numeric readout for a position Adjustable.](images/widget_indicator_gadgets_qt.png)

| Gadget | Read/write | Notes |
|---|---|---|
| `LEDIndicator` | writes (click to toggle) if settable | green/red/gray for on/off/unknown; `threshold=` for "value ≥ X" |
| `BarGauge` | read-only | vertical or horizontal fill against `[vmin, vmax]` |
| `AnalogGauge` | read-only | semicircular needle gauge |
| `StripChart` | read-only | rolling line plot of the last N readings |
| `NumericTile` | read-only | large numeric readout + sparkline (KPI-tile style) |
| `Dial` | writes if settable | continuous rotary knob for plain numerics; a mode-select ring (every enum choice shown at once) when the item exposes `enum_strs` |
| `Slider` | writes if settable | horizontal slider with numeric readout |

No dropdown, text entry, or hidden menu is needed to read a value at a
glance — that's the point of these versus the default grid row. Getting one
onto screen is a right-click away: right-click a parameter's name in the
normal Qt property grid → **Add indicator** → pick a gadget kind (see
`eco.widgets.display_qt`'s `_build_row`, wired via
`eco.widgets.indicator_widgets.attach_indicator_menu`). A gadget is also a
valid Qt Designer "promoted widget", and round-trips through a saved
dashboard layout via the item's alias path.

### ipywidgets (`eco.widgets.indicator_widgets_ipy`)

A first pass at the same seven gadgets for the `lab`/`voila` front ends,
built **entirely from stock ipywidgets controls** rather than a new
charting/gauge library — "something that works" now, not a redraw of the Qt
look:

![The same seven gadgets, built from stock ipywidgets: a green LED-styled Button for shutter_open, a vertical FloatProgress for pressure, a horizontal FloatProgress standing in for the needle gauge on energy, a FloatProgress showing intensity's position within its observed min/max as a strip-chart stand-in, a large HTML number for counts, a row of ToggleButtons for mode's enum choices, and a FloatSlider for position.](images/widget_indicator_gadgets_ipy.png)

| Gadget | Stock control used | Notes |
|---|---|---|
| `led_indicator` | `Button` (`button_style`) | a plain `HTML` div can't take a click back from the browser, so this is a styled Button, not a bare circle |
| `bar_gauge` | `FloatProgress` | vertical or horizontal, exact match to the Qt bar gauge |
| `analog_gauge` | `FloatProgress` (horizontal) | **approximation** — no needle; see below |
| `strip_chart` | `FloatProgress` + label | **approximation** — shows position within the min/max seen so far, not a rolling line; see below |
| `numeric_tile` | `HTML` | large number, no sparkline (same reasoning as `strip_chart`) |
| `dial` | `ToggleButtons` (enum) or `FloatSlider` (numeric) | enum case matches the Qt "every choice visible" idea, laid out as buttons instead of a ring; dragging a value around a circle isn't idiomatic web UX, so the numeric case reuses the slider rather than faking a knob |
| `slider` | `FloatSlider` | exact match to the Qt slider |

Five of the seven are exact stock-widget matches. The other two
(`analog_gauge`, `strip_chart`) are deliberately left as plain bar/value
approximations rather than reaching for a new dependency — see
`eco.widgets.indicator_widgets_ipy`'s module docstring. If a real needle
gauge or a real rolling plot is wanted later, the candidates worth reviewing
first:

- **Plotly `go.Indicator`** (`mode="gauge+number"` for the needle gauge,
  `mode="number+delta"` for a nicer KPI tile) — its `FigureWidget` is a real
  `ipywidgets.DOMWidget`, so it drops straight into the same `VBox`/`HBox`
  rows these gadgets already use, and updates live via
  `fig.data[0].value = x`. The single-best "one new dependency, covers both
  gaps" option.
- **bqplot** — a proper live-updating line plot (`Lines`/`Figure`) for
  `strip_chart`; itself a real Jupyter widget, no needle-gauge support.
- **Panel** (`panel.widgets.indicators.Gauge`/`Dial`/`BooleanStatus`) — the
  closest visual match to the Qt gadgets overall, but a separate framework
  built on Bokeh; its `pn.ipywidget()` bridge into plain `ipywidgets`
  layouts has known rough edges (no JS-linking, some nested-container
  issues), and it pulls in Bokeh as a second rendering stack.
- **anywidget** — the modern, low-effort way to wrap any JS/web-component
  (e.g. a real rotary-drag knob, a glowing LED) as a first-class Jupyter
  widget, if a literal LabVIEW look is ever wanted beyond what the above
  give. Worth knowing about, not needed for anything on this page.

There's no "Add indicator" menu for the ipywidgets side yet (there's no
natural right-click-a-label hook the way Qt has one) — call the function
directly, e.g. `eco.widgets.indicator_widgets_ipy.bar_gauge(my_assembly.pressure, vmin=0, vmax=100)`.
