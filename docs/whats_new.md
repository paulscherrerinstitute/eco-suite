# What's new

What changed in eco recently, newest first, with what to watch out for when
you update. For one feature at a time, with the main call, see
{doc}`did_you_know/index`, or `features()` in a session.

## Updating a checkout

```bash
git pull                       # in your checkout
pip install -e . --no-deps     # only for a pip-installed checkout; see the new dependencies below
```

```{admonition} Upgrade notes for 25 October to 6 November 2026
:class: important

*New dependencies*

- **New dependency: `hklforge`** (core, for `recspace`). It is in the
  Bernina Python environments; a separate installation needs
  `pip install hklforge`.
- **New optional extra `eco-suite[web]`** (tornado) for `eco web`. Without
  it everything else works.
- **Adaptive scans need escape-fel 0.2.17 or newer.** Nothing else changes
  with an older escape.
- **`run_status.table()` needs `pyarrow`.** It is in the Bernina Python
  environments. The run table is cached per pgroup in
  `res/run_data/run_table/<pgroup>_runs.parquet` (about 30 MB, group
  writable); the runs' `status.json` files stay the original record.

*Scans*

- **`N_pulses` is now the exposure of the first counter.** The other
  counters of a scan follow its window (same pulses, same time) instead of
  each acquiring `N_pulses` on their own. With the default counters (the DAQ
  alone) nothing changes. See {doc}`scanning`.
- **The scans' exposure argument is called `exposure_def`.** `N_pulses=`
  still works; giving both with different values is an error.
- **A bs-stream counter bins only during its scans.** Before, it kept binning
  every shot between scans. Its per-scan data now live on the scan, so one
  counter can serve concurrent scans. It keeps only the shots the step was
  reduced from, up to 1 000 000 per step (escape's default was 1000).
- **Two counters of one scan keep their data apart.** The scan's `monitors`,
  `monitor_scan_arrays`, `fig`, ... show the first one's; a second one plots
  and stores separately.
- **Scan info gains `mesh_specs`** in `scan_parameters` and `mesh_index` in
  every step's info, next to `grid_specs`, which is unchanged.

*Diffraction (`recspace`)*

- **`recspace` works in the SwissFEL/Bernina frame** (z+ along the beam, y+
  up). Crystals stored before are turned once when they are first loaded, and
  directions are given as the crystal lies with all angles at 0. `diffcalc`
  is not affected.
- **`recspace`: a crystal's surface normal is set on its own**
  (`set_surface_normal`), no longer taken from the orientations. A new
  crystal has none until it is set, and a mode that needs it says so;
  imported diffcalc crystals keep the one diffcalc used.

*Motors*

- **Status of PowerBrick motors** gains a `cnf_pb` entry, read only when
  shown; motors behind sf-gw show a 🔑 key instead.
- **SmarAct `home()` and `calibrate_sensor()` wait** until the action has
  started and finished, with a timeout; before, `home()` on an already
  homed stage returned at once.

*`%ei`*

- **`%ei` indexes SciLog and the machine elog before its first question**:
  about 20 s and 30 s, once per account; the index in `~/.eco` grows by about
  50 MB. `"scilog_default": null` and `"elog_logbooks": []` in
  `~/.eco/ei.json` switch them off.
- **The `%ei` index moves off network shares.** Where `~/.eco` is one, as on
  gac-bernina, the index is kept in `~/.cache/eco` (copied over once);
  `$ECO_INDEX_DIR` sets it explicitly.
- **`%ei` checks the code of its answers**, and asks the model again when
  something does not fit, so such an answer takes a second model call.
  `"check_code": false` in `~/.eco/ei.json` switches it off.
```

## 10 November 2026

- eco desktop builds small GUIs without code ({doc}`simple_gui`):
  **⋯ → Add indicator** now docks the gadget in the desktop window, not in a
  separate dashboard. The workspace and **Save Startup Script...** keep
  gadgets, sub-device grids, strip charts and Designer panels
  (**Tools → Edit Gadgets in Qt Designer...** / **Load Designer Panel**).
  The script runs the Python and checkout that wrote it, so it works without
  `eco` on PATH. Two fixes: a widget closed before saving no longer comes
  back, and the saved panel sizes are restored, not reset to a grid.
- The SciLog viewer moved from `eco.start_scilog()` (removed) to
  `scilog.viewer()`, which opens the pgroup's logbook; `scilog.widget()`
  does the same, and `scilog.viewer.stop()` / `.log()` end it or show its
  output. Outside the namespace: `from eco.scilog_app import start`; the
  package is imported only when used ({doc}`scilog_viewer`).

## 9 November 2026

- `%ei -t`: the model may look things up while it answers (search,
  describe an object, check its own code, storage), up to five times, all
  read-only ({doc}`ask_eco`).
- `%ei --check-logs`: code in the logbooks that does not fit eco today
  (129 kinds in Bernina's logbooks, most of them renamed laser
  components). Such posts reach the model with a warning not to copy them.
- `%ei` gives the model short guides for scans, motors, the DAQ and the
  logbooks (`eco/ei_packs/`); a test keeps their code current.
- `%storage hexapod`: where things are kept, from the beamline's storage
  spreadsheet ({doc}`storage`).
- `features()` and `eco.tip()` show the tips again in the eco installed into
  the Python environment; it has no `docs/` folder and found none.
- The SciLog viewer writes: new posts and edits in a CKEditor dialog,
  images uploaded at once and kept at a fixed pixel size, drafts kept in
  the browser, heading levels changed from the outline, views (mine, by
  tag) and links to a single post ({doc}`scilog_viewer`). Writing is on
  only for the logbooks listed in `~/.eco/scilog_app.json`.

## 8 November 2026

- `eco.start_scilog()`: SciLog in the browser, one logbook as a flow of
  days with an outline, typo-tolerant search with previews, images at their
  set size and interactive plots inline ({doc}`scilog_viewer`).

## 5 and 6 November 2026

**Asking eco (`%ei`)** — {doc}`ask_eco`

- The code in an answer is checked against eco's source and the session
  (attributes, keywords, imports). If it does not fit, the model corrects
  it once, and what still does not fit is marked **Not verified**.
- `%ei -b`: the answer as a page in the browser. Each SciLog source is
  shown as in SciLog, with images and tables, between its neighbouring
  posts; citations link to it.
- Answers are rendered with glow in a terminal where it is installed (on
  the consoles it is): headings, lists and highlighted code.
- The wait display starts the moment you ask and names the step; an
  animation now comes with 40 % of the questions.
- Without a model key, `%ei` shows how to search and asks before the long
  first indexing. `%ei --rebuild` builds the index from scratch.

**Motors** — {doc}`smaract`

- SmarAct stages: `startup_stage()` (sensor type, frequency, calibration,
  homing) and `smaract_usd.startup([...])` for several stages at once;
  `diagnose()` finds why channels don't respond, without moving anything.

## 3 and 4 November 2026

**Runs** — {doc}`run_history`

- `run_status.table()` and `.plot()`: any device's values over all runs of a
  beamtime, from the status every run stores.

**Diffraction** — {doc}`recspace`, {doc}`recspace_crystals`

- The reflections browser filters solutions by min/max per angle, loads
  stored setups, and simulates any six angles: which hkl is in the Bragg
  condition there, and the nearest reflection.
- Its 3D view has detector presets (JF01T03, JF07T32), Brillouin zones, the
  real-space cell, and everything the detector sees.
- The surface normal is a property of the crystal (`set_surface_normal`).
- A reference for setting up crystals: hexagonal cells, reciprocal
  directions, forbidden reflections, a thin film.

**Motors** — {doc}`powerbrick_parameters`

- `motor.identify()` on any motor record: controller, drive model, encoder
  and what homing does there, read from the IOC and the drive.
- `motor.controller` on every motor record: `.type`, `.link`, `.homing`,
  looked up only when asked, never during scans.

## 2 November 2026

**Asking eco (`%ei`)** — {doc}`ask_eco`

- The machine's elog: SwissFEL commissioning and Diagnostics are searched,
  and questions about a time span ("what happened since yesterday?") get that
  span's logbook entries in order.
- SciLog is searched by default: Bernina's logbooks, as you with a personal
  token or as the beamline account, only what that account can read now.
- Every answer lists its sources with their locations, clickable in the
  terminal; `%ei --open N` shows one in its context.
- Better ranking: items containing more of the question come first.
- A spinner while the model thinks, and now and then a small animation instead.

**Scans** — {doc}`scanning`, {doc}`adaptive_scans`

- Adaptive scans: `scans.adaptive_scan(...)` chooses the next points from
  the live data.
- Every scan shows where its time goes (acquire, move, settle, callbacks).
- Detectors as counters: `counters=[daq, mon_opt.intensity, diode]`.
- The first counter sets the exposure (`exposure_def`); the others follow its
  window, or acquire on their own with `counter_options`.
- `scans.append_counter()`: a live counter in every scan, next to the DAQ.
- Every scan records its mesh, the point each step measured
  ({doc}`multidim_scan_data`).
- Simulated scan targets: `sim`, with realistic motor and detector behaviour
  ({doc}`simulated_targets`).

**eco web** — {doc}`web_app`

- A front-end for phones and tablets: `eco web -s bernina`,
  `eco.start_web()`. Motor records with jog, task queues with progress and
  controls, and the live view of the PTZ cameras.

**Motors and diffractometers**

- PowerBrick parameters (`cnf_pb`) on every PowerBrick-driven motor record
  of mono, mirrors, slits, KB and spectrometer; controllers behind sf-gw open
  with your own login ({doc}`powerbrick_parameters`).
- `xrd.module_ioc` / `gps.module_ioc` load the Kappa or SampleStage onto the
  module PowerBrick ({doc}`powerbrick_parameters`).
- `recspace`: the Bernina frame, crystal setup by directions at all angles 0,
  and `set_constraints()` with the stored Bernina setups ({doc}`recspace`).

## 30 and 31 October 2026

- `%ei`, ask eco: answers from the docs, eco's API, the command history,
  escape and your namespace; works without a model key as a search; notes
  from photos, PDFs and a webcam ({doc}`ask_eco`).
- `recspace`, the hklforge-based successor of the `diffcalc` objects, runs
  alongside them ({doc}`recspace`).
- Property grids: the setpoint next to the readback, and a **⋯** menu on
  every row with strip charts, archiver history, gadgets, memories and
  details ({doc}`widget_views`).
- Scan launcher: `scans.launcher()`, also **Tools → Scan Launcher** in eco
  desktop ({doc}`queues`).
- Log viewer: opens at once also on long histories (last 30 days by
  default), in its own process from eco desktop ({doc}`command_history`).
- eco desktop no longer crashes when its window is destroyed.

## 25 October 2026

- Qt windows stay live while a command runs: camera viewers, plots and eco
  desktop keep updating during `umv`, scans and `sleep` ({doc}`live_gui`).
- Named task queues and the `%queue` magic, with every task test-run when
  it is queued ({doc}`queues`).
- Property grids never block: moves and reads run off the GUI thread
  ({doc}`widget_views`).
- `features()` and `docs()` in the session; "Did you know?" tips.
- Archiver: long time ranges are binned on the server (`max_bins`).
- Stream plots open in named windows that refresh in place.

```{admonition} Did you know?
:class: tip

`features()` opens a small window with a random tip like the ones linked
above, and **Next** gives another. Without a display it prints one.
```
