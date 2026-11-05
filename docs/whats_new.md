# What's new

What changed in eco recently, newest first, with what to watch out for when
you update. For one feature at a time, with the main call, see
{doc}`did_you_know/index`, or `features()` in a session.

## Updating a checkout

```bash
git pull                       # in your checkout
pip install -e . --no-deps     # only if you install it; new dependencies below
```

```{admonition} Upgrade notes for 25 October to 3 November 2026
:class: important

- **New dependency: `hklforge`** (core, for `recspace`). It is in the
  Bernina Python environments; a separate installation needs
  `pip install hklforge`.
- **New optional extra `eco-suite[web]`** (tornado) for `eco web`. Without
  it everything else works.
- **Adaptive scans need escape-fel 0.2.17 or newer.** Nothing else changes
  with an older escape.
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
- **`recspace` works in the SwissFEL/Bernina frame** (z+ along the beam, y+
  up). Crystals stored before are turned once when they are first loaded, and
  directions are given as the crystal lies with all angles at 0. `diffcalc`
  is not affected.
- **`%ei` indexes SciLog and the machine elog before its first question**:
  about 20 s and 30 s, once per account; the index in `~/.eco` grows by about
  50 MB. `"scilog_default": null` and `"elog_logbooks": []` in
  `~/.eco/ei.json` switch them off.
- **Status of PowerBrick motors** gains a `cnf_pb` entry, read only when
  shown; motors behind sf-gw show a 🔑 key instead.
```

## 5 November 2026

- `%ei` without a model key (e.g. on gac-bernina) no longer builds its long
  first index unasked: it says that its answers will be a search, shows the
  search syntax and asks once whether to index now. `%ei --index` builds it
  later ({doc}`ask_eco`).
- `%ei -b`: the answer as a page in the browser. Each SciLog source is
  shown as in SciLog, with images and tables, between its neighbouring
  posts; citations link to it ({doc}`ask_eco`).

## 3 November 2026

- `motor.identify()` on any motor record: controller, drive model, encoder
  and what homing does there, read from the IOC and the drive
  ({doc}`powerbrick_parameters`).
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
- A spinner while the model thinks, and now and then something else.

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
