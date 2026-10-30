# Qt → PWA feature parity

Inventory of `eco/widgets/` Qt functionality (state of 2026-10-25) and what each
takes in the PWA. Effort is a rough guess for one developer who knows eco, given
the gateway (SPIKE + phase 1) already exists — **d = days, w = weeks.**

Columns: **Qt** / **ipy** = exists for Qt / ipywidgets today. **Logic reusable** =
a toolkit-free module the gateway can drive as-is. **Mobile value** = how much it
matters on a phone/tablet.

## Tier 1 — core, port first

| Feature | Qt module(s) | ipy | Logic reusable | PWA work | Effort | Mobile value |
|---|---|---|---|---|---|---|
| Namespace browser (lazy/initialized/failed, tap to initialize, search) | `desktop_app._NamespaceLauncher`, `namespace_selector` | `widget_tray.NamespaceLauncherWidget` | Namespace API (`all_names`, `lazy_names`, `initialized_names`, `failed_names`, `__resolved__`) | list + filter view; init request runs on gateway pool with progress | 3–4 d | high |
| Assembly property grid (rows, groups/colours, ▲/▼ tweak, entry, status dot, Stop) | `display_qt` | `display_widget` | **`live_values`** (all of it), `utilities.tables.section_row_styles` | grid component; touch-sized ▲/▼; numeric entry sheet; per-row state dot | 1–1.5 w | high |
| Sub-assembly drill-down (`is_display='recursive'`) | `display_qt` | `display_widget` | Assembly tree | navigation stack (back button), breadcrumbs | 2 d | high |
| SVG panels (beamline synoptic, prepump, alarm panel) | `svg_interactor` (Dash/WebKit), `_widget_svg_panel()` | same | `_widget_svg_panel()` returns the SVG | serve SVG inline, live colouring by id, pinch-zoom, tap → device view (no command exec) | 3–5 d | **very high** — cheapest win, already web-native |
| Live-update transport, staleness, reconnect | — | — | `LiveReader` | websocket client, value ages, reconnect + resubscribe | (in spike) | essential |

## Tier 2 — operating an experiment

| Feature | Qt module(s) | ipy | Logic reusable | PWA work | Effort | Mobile value |
|---|---|---|---|---|---|---|
| Task queue view (tasks, state, pause/resume/abort, output follow) | `scan_launcher_qt.ScanQueuePanel`, `queue_output_qt` | partly (`%queue` only) | `task_queue` (listeners, `Task.output`) | list + live log; abort/pause behind confirm | 4–5 d | **very high** ("is my scan still running?") |
| Running scan progress (step n/N, current positions, ETA) | partly in launcher | `scan_widget` | `scans.acquiring_scan`, `StepScan` | progress card | 2–3 d | very high |
| Scan launcher (method forms, pickers, append to queue with test-run) | `scan_launcher_qt` (1.4k lines) | `scan_widget`, `scan_builder_widgets` | `task_queue` test-run, `Scans` signatures, `component_selector` | forms are much easier in HTML than Qt; picker is the work | 1.5–2 w | medium (tablet yes, phone rarely) |
| Component / command picker | `component_selector_qt` | `component_selector_widget`, `component_picker_widget` | **`component_selector`** | tree/search list | 3 d | needed by launcher |
| Tweak panel (1–4 axes, steps, keys) | `tweak_panel_qt` | `tweak_panel_ipy` | **`tweak_panel.TweakController`** | big touch buttons; keys still work on a laptop | 3–4 d | high (alignment from a tablet at the sample) |
| Memory browser (filter, unfold, recall, save) | `memory_widget` (1.9k, Qt+Tk) | yes | **`elements/memory.py`** | list + diff view; recall behind confirm | 1 w | medium |
| Indicator gadgets (LED, bar, strip chart, KPI tile, dial, slider) | `indicator_widgets`, `indicator_widgets_qt_simple` | `indicator_widgets_ipy` | value stream from `LiveReader` | HTML/SVG gadgets + uPlot strip chart — easier than Qt | 3–4 d | high |
| User dashboard (arrange gadgets) | `dashboard_qt` (dock widgets) | — | — | CSS grid of cards, reorder; layout stored per client (localStorage) or per user on gateway | 3 d | high |
| Alarms | `elements/alarm.py` panel | — | `AlarmSeverity` | alarm list + badge; in-app sound/vibration while open (no push, see ARCHITECTURE §6) | 2–3 d | very high |

## Tier 3 — heavy or specialised

| Feature | Qt module(s) | ipy | Logic reusable | PWA work | Effort | Mobile value |
|---|---|---|---|---|---|---|
| Camera viewer (cam_server stream, colormap, histogram, ROI/analysis, calibration) | `camserver_stream_qt` (2.8k), `camserver_panel_qt`, `camera_stream_qt` | `camera_stream_widget` | `FrameProcessor` / `ScreenpanelAnalysis` partly toolkit-free — check | gateway subscribes to bsread, downsamples, sends JPEG/WebP or 8-bit + colormap index to `<canvas>`; analysis numbers as data | basic view 1 w; full parity 3–4 w | medium — basic view yes, calibration/analysis stay Qt |
| Stream plots / live plots (matplotlib windows) | stream plots, `CounterValue.plot_arrays` | inline mpl | the data, not the figures | send arrays, render with uPlot | 1 w | medium |
| Log timeline | `log_timeline_qt` | `log_timeline_html` (already HTML!) | `log_timeline_common` | reuse the HTML renderer's approach | 2–3 d | low |
| IOC finder (search, status, console output, restart) | `ioc_finder_qt` | `ioc_finder_widget` | `epics_utils.iocinfo` | search + status read-only easy; **restart stays desktop-only** | 2 d (read-only) | low–medium |
| Status server monitor (health, reinit) | `status_server/gui.py` | — | `/health`, `/stats` | small status page | 1 d | medium |
| Tips ("Did you know?") | `tip_qt` | — | `utilities/tips` | trivial | 0.5 d | low |

## Deliberately NOT ported

| Feature | Why |
|---|---|
| Embedded IPython console (`desktop_app`, `console_kernel`, `kernel_registry`) | Arbitrary code execution from a phone is the wrong risk/benefit; JupyterLab through the same SSH tunnel already provides a browser console for laptops. |
| Executing SVG `onclick` / `<desc>` commands | Same reason; the web client maps a click to "open device view". |
| Dock/tab/float window management | Replaced by a mobile navigation model (stack + dashboard grid), not emulated. |
| Camera calibration dialog, IOC restart, other admin actions | Rare, consequential, desktop-only by policy. |
| Qt heartbeat (`utilities/mainthread`) | Not needed — the gateway never uses the main thread. |
| `containers.stack()` arbitrary composition | Possibly later as "saved layouts"; not a phase-1 concern. |

## Totals (rough)

- Spike: ~1 w.
- Tier 1: ~3–4 w → a useful read-mostly mobile app.
- Tier 2: ~6–8 w → you can operate an experiment from a tablet.
- Tier 3 basic parts: ~3 w. Full camera parity is the one big item and may never
  be worth it.

Full Qt parity is not the goal; "everything you'd want away from the console" is
roughly Tier 1 + Tier 2 + basic camera view, i.e. **~3 months of part-time work**,
front-loadable so the first useful version arrives after ~1 month.
