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
eco provides small standalone gadgets, one per item. The current direction is
a **matched, as-simple-as-possible pair**, one module per backend, both built
entirely from each toolkit's own stock controls (no new dependency, no
custom-painted graphics) and sharing the same function names/signatures so
either is a drop-in for the other:

- {py:mod}`eco.widgets.indicator_widgets_ipy` — ipywidgets (`lab`/`voila`)
- `eco.widgets.indicator_widgets_qt_simple` — Qt desktop

![The seven stock-ipywidgets gadgets: a green LED-styled Button for shutter_open, a vertical FloatProgress for pressure, a horizontal FloatProgress standing in for the needle gauge on energy, a FloatProgress showing intensity's position within its observed min/max as a strip-chart stand-in, a large HTML number for counts, a row of ToggleButtons for mode's enum choices, and a FloatSlider for position.](images/widget_indicator_gadgets_ipy.png)

*ipywidgets — `eco.widgets.indicator_widgets_ipy`*

![The same seven gadgets on the Qt side, built from plain QWidgets: a green circular QPushButton for shutter_open, a vertical QProgressBar for pressure, a horizontal QProgressBar for energy, a QProgressBar plus a "value [lo .. hi]" label for intensity, a large QLabel for counts, a row of checkable QPushButtons for mode's enum choices, and a QSlider for position.](images/widget_indicator_gadgets_qt_simple.png)

*Qt — `eco.widgets.indicator_widgets_qt_simple`*

| Gadget (same name, both modules) | ipywidgets control | Qt control | Notes |
|---|---|---|---|
| `led_indicator` | `Button` (`button_style`) | `QPushButton` (stylesheet) | click to toggle if settable; a plain label/HTML can't take a click back |
| `bar_gauge` | `FloatProgress` | `QProgressBar` | vertical or horizontal fill against `[vmin, vmax]` |
| `analog_gauge` | `FloatProgress` (horizontal) | `QProgressBar` (horizontal) | **approximation** — no needle; see below |
| `strip_chart` | `FloatProgress` + label | `QProgressBar` + label | **approximation** — position within the min/max seen so far, not a rolling line; see below |
| `numeric_tile` | `HTML` | `QLabel` (enlarged font) | large number, no sparkline (same reasoning as `strip_chart`) |
| `dial` | `ToggleButtons` (enum) / `FloatSlider` (numeric) | checkable `QPushButton` row (enum) / `QSlider` (numeric) | enum case shows every choice at once; dragging a value in a circle isn't idiomatic UX on either toolkit, so the numeric case reuses the slider rather than faking a knob |
| `slider` | `FloatSlider` | `QSlider` | writes on release if settable |

Five of the seven are exact stock-widget matches on both sides. The other two
(`analog_gauge`, `strip_chart`) are deliberately left as plain bar/value
approximations rather than reaching for a new dependency — see either
module's docstring. If a real needle gauge or a real rolling plot is wanted
later, the candidates worth reviewing first:

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

Neither module has a discovery UI yet ("right-click a name → add gadget" on
Qt, or an equivalent on the notebook side) — call the function directly for
now, e.g. `eco.widgets.indicator_widgets_ipy.bar_gauge(my_assembly.pressure, vmin=0, vmax=100)`
or the equivalent `eco.widgets.indicator_widgets_qt_simple.bar_gauge(...)`.

### The older, hand-painted Qt gadgets

`eco.widgets.indicator_widgets` — the original, richer Qt-only module (real
needle gauge, a radial mode-select dial face, a live rolling strip-chart) —
still exists and is unrelated to the simple pair above, not superseded by
it. It's wired into the desktop app's right-click **"Add indicator"** menu
and a droppable {py:class}`~eco.widgets.dashboard_qt.Dashboard`, and its
gadgets double as valid Qt Designer "promoted widgets":

![Five hand-painted indicator gadgets against dummy items: a green LED for a boolean shutter state, a vertical bar gauge for a pressure Detector, a semicircular analog gauge with a needle for an energy readback, a rotary Dial in enum "mode-select" form showing every choice (standby/ready/running/fault) around the ring, and a horizontal Slider with a numeric readout for a position Adjustable.](images/widget_indicator_gadgets_qt.png)

See its module docstring for the full gadget list and
`eco.widgets.indicator_widgets.attach_indicator_menu` for how it's wired
into `eco.widgets.display_qt`'s property grid. Reach for this one if the
simple pair's two approximations (no needle, no rolling line) actually
matter for a given panel; otherwise the matched simple pair above is the
current default recommendation for new work, on either backend.
