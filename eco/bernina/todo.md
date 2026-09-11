# Status: what happened on this machine (2026-09-10 / 2026-09-11)

Snapshot taken 2026-09-11 from `/home/lemke_h/mypy/eco` (branch `master`,
working tree clean as of this snapshot — `git status` empty, latest commit
`e001165`). Several Claude Code sessions were active on this exact checkout
at the same time while this was written (`ListAgents` showed 4 other local
interactive sessions plus 2 Remote Control sessions); this summary is built
from `git log`/`git show`, not from asking each session, so it reflects
whatever ended up committed, not necessarily who wrote which line.

## 2026-09-10 — status server hardening (author `lemke_h`, all with
`Co-Authored-By: Claude Sonnet 5`)

Chronological, oldest first:

1. **`8209056`** Add async `/recording/capture` (mirrors `/status/capture` /
   `/aliases/capture`: stop synchronously, write+upload in the background).
   Added (but not yet wired in) `Daq.start_scan_monitoring()` /
   `end_scan_monitoring()`.
2. **`5cbe334`** Per-scan monitor recording named `aux/namespace_monitor.h5`,
   registered as the `"monitors"` scan parameter. Added
   `eco/status_server/NAMESPACE_MONITOR_FORMAT.md` (handoff spec for
   `escape_fel`).
3. **`a641127`** Wired `start_scan_monitoring`/`end_scan_monitoring` into
   `callbacks_start_scan`/`callbacks_end_scan` — server-only, no local
   fallback (a local recording would mean holding a live CA monitor per
   channel for the whole scan in this session, exactly what the status
   server exists to avoid).
4. **`f91ad41`** Two-tier fix for "a channel with zero updates is simply
   absent from the recording": seed a channel with its current value for
   free at attach when it's already monitored+connected (no extra CA
   traffic), and backfill the rest opportunistically from a status
   snapshot taken for another reason (`backfill_running_recordings`).
5. **`f837cde`** `DetectorVirtual` gets push-based monitoring (via the same
   `CallbackComposedValue` `AdjustableVirtual` already used), so a computed
   Detector can be live-monitored instead of always counting as a failed
   attach.
6. **`d69ae87`** Fixed a `TypeError` crash in `push_status`:
   `requests.post(json=...)` has no datetime/numpy encoder —
   `StatusServerClient._post()` now serializes the body itself with the
   same encoder `status.json` already uses.
7. **`7a0cc14`** New `bernina.status_server` namespace object
   (`eco.status_server.namespace_component.StatusServer`): `.status()`,
   `.stats()`, `.failures()`, `.monitor_policy()`, `.restart()`/`.reinit()`,
   `.gui()` (detached Qt monitor subprocess), plus recording settings
   (`recording_mode`, `min_interval`, ...) as real, inspectable
   `AdjustableMemory` children.
8. **`e876c1a`** Follow-ups: those recording settings moved to
   `AdjustableFS` (shared-filesystem visible across processes);
   `RecordingSession.size_report()` (per-channel projected bandwidth, `GET
   /recording/<id>?size=1`); `failed_required` reporting for recordings
   (a required channel failing to attach is flagged distinctly from an
   optional one).
9. **`8637d80`** `Memory.setup_path()` now falls back to a private
   per-account directory when the shared `eco_cnf_bernina/memory/` tree
   isn't writable by this account's group (was crashing `Assembly`
   construction, e.g. for the new `status_server` object, on accounts not
   in `unx-sf_bernina_bs`).
10. **`bf0223a`** `StatusServer.__repr__` now actually shows the recording
    settings (they were `is_display=True` all along; `__repr__` just never
    called `get_display_str()`).
11. **`d41fb10`** `StatusServer.status()` shows failed component *names*
    (not just a count) plus a red/yellow/green indicator and a `/stats`
    activity summary; `recording_mode` became a validated
    `_AdjustableFSChoice` (`"all"|"throttle"|"sample"`) instead of a
    freeform string.

Also same-day, terser/no Claude trailer (author `Henrik Lemke`):
`6b06799` "scannable fix", `6bf998b` "Push `scans.acquiring_scan.*` status
to the status server for the run table" (has a full body, no trailer),
`e308f5e` "alias debug fix".

## 2026-09-11 — today

1. **`80e359f` "fixes mpod"**, **`ca62667` "sc fix"**, **`442e08e` "more"**
   (author `Henrik Lemke`, terse messages, no commit body — reconstructed
   from the diffs):
   - New `eco/widgets/subprocess_embed.py`: reusable core for "spawn a Qt
     app as a separate OS process, capture its window id, embed it into a
     local Qt container via X11 reparenting" — extracted out of
     `CamServerPanelQt`'s viewer-spawning code so
     `cameras_swissfel.py`'s `separate_process=True` single-viewer path can
     reuse it without depending on the multi-dock-widget panel structure.
   - `eco/devices_general/cameras_swissfel.py`, `camserver_panel_qt.py`,
     `camserver_stream_qt.py`, `desktop_app.py`: reworked around the above.
   - `eco/elements/assembly.py`: default-widget selection changes (new
     `tests/test_assembly_default_widget.py`).
   - `eco/utilities/svg_interactor.py`: reworked (110 lines changed).
   - `eco/devices_general/powersockets.py` (MPOD): fixed, then a dead
     `MpodVoltageAdjustable` wrapper class was removed again in `442e08e`
     ("more") as part of the same cleanup.
   - `.vscode/settings.json`: 28 lines removed (local editor config, not
     library behavior).
   - New/expanded tests: `test_cameras_swissfel.py`,
     `test_camserver_stream_qt.py`, `test_subprocess_embed.py`,
     `test_svg_interactor.py`, `test_desktop_app.py`,
     `test_assembly_default_widget.py`.

2. **`e001165` "add bs channel for jungfrau."** (author `Henrik Lemke`) —
   bundles two independent pieces of work, both landing in this one commit:

   a. **Jungfrau custom DAP script support** (`eco/detector/jungfrau.py`):
      - `Jungfrau.upload_custom_dap_script(source, name=None,
        allowed_modules=DEFAULT_ALLOWED_DAP_MODULES, max_time=0.1)` —
        accepts a `.py` file path or a live `(meta, image, mask) -> result`
        function, reconstructs standalone source for a live function
        (`_dap_script_source_from_function`), and before uploading:
        statically checks its imports against an allow-list
        (`check_dap_script_imports`, `DEFAULT_ALLOWED_DAP_MODULES =
        ("numpy", "scipy", "math")` — sf_daq_broker execs uploaded scripts
        unsandboxed against live detector data, so this is the only gate
        against e.g. `import os`), and actually runs it once against
        synthetic data (`_test_dap_script_function`) to refuse anything
        that mutates its inputs in place or is too slow to run per frame.
      - `Jungfrau.get_active_custom_script()` / `disable_custom_script()`
        to inspect/turn off the live script (documented as *not* a full
        undo — the uploaded file itself isn't deleted, and a re-upload
        under the same name may not reach an already-running worker
        process because `dap`'s `load_custom` caches indefinitely per
        `"beamline:name"`).
      - `list_dap_env_packages()` / `check_dap_env_modules()` — ground-truth
        checks (`pip list` / a real `import`) against the live `dap`/
        `sf-dap` conda envs under `/sf/jungfrau/...`, since the allow-list
        default is documentation-derived, not confirmed live.
      - Explicitly flagged in the new code's own docstrings as **not yet
        verified against a live dap worker** (no reachable shell on
        `sf-daq-11.psi.ch` as of 2026-09) — treat as implemented-but-unverified.
   b. A new `DetectorBsStream` child (`"{jf_id}:roi_intensities"`, named
      `intensity_roi`, `optional=True`) appended to `Jungfrau` — the
      commit's namesake "bs channel for jungfrau".

   Also bundled into the same commit (this session's work, described
   below): the whole DAQ channel-list comparison feature.

3. **DAQ recorded-channel-list vs. namespace comparison** (this session,
   `eco/aliases/channel_lists.py`, `eco/acquisition/daq_client.py`,
   `eco/status_server/{namespace_store,namespace_server,client}.py`):
   - `channels_JF`/`channels_BS`/`channels_BSCAM` (the hand-maintained,
     `AdjustableFS`-backed lists `sf_daq_broker` is told to record) can now
     be compared against what the *initialized* namespace's `Alias` tree
     (tagged `channeltype="JF"/"BS"/"BSCAM"`) actually provides:
     `Daq.compare_channels()` returns, per list, `missing` (namespace has
     it, the recorded list doesn't → won't land in the run's raw data) and
     `exceeding` (recorded, but no live namespace component backs it).
   - Server-first: if `Daq`'s status server is configured and healthy,
     the comparison runs against **the server's own** already-initialized
     namespace (new `GET /channels/compare` route, `NamespaceMonitorStore.
     compare_channels()`, `StatusServerClient.compare_channels()`) —
     this session's own (deliberately lazy) namespace is never forced just
     to compute this. Falls back to the local namespace on any server
     failure, same pattern as `write_status()`/`copy_aliases_to_scan()`.
   - Only `channels_JF`/`channels_BS`/`channels_BSCAM` for now — `channels_CA`
     has no "compare against the live namespace" story yet.
   - **Not yet run against the real environment** — this sandbox has no
     `/sf` filesystem and none of the Bernina pixi deps (flask, epics,
     escape...), so only parsing/logic was verified here, not by actually
     running pytest. See "How to test" below.

## How to test this from a Claude session running locally

All of this needs the real Bernina Python environment — this sandbox
cannot import `eco` at all (no `flask`, `epics`, `escape`, ...). From a
session with `/sf/bernina` mounted:

```bash
source /sf/bernina/bin/bpy-env   # or use the interpreter path directly below
```

Non-interactive/agent-safe invocation (explicit interpreter + PYTHONPATH +
headless Qt — see this repo's CLAUDE.md for why both matter):

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=/sf/bernina/config/personal/lemke_h/eco:$PYTHONPATH \
  /sf/bernina/applications/python/.pixi/envs/bpy312/bin/python -m pytest <file> -q
```

Run **per file**, not as one `pytest tests/` invocation (the whole-suite run
reliably dumps core partway through the Qt+pytest+offscreen interaction).

- **This session's channel-comparison feature:**
  `tests/test_channel_lists.py tests/test_daq_status_server.py tests/test_status_server.py`
- **Yesterday's status-server hardening:**
  `tests/test_status_server.py tests/test_status_server_gui.py tests/test_status_server_namespace_component.py tests/test_status_server_query_stats.py tests/test_daq_status_server.py`
- **Today's MPOD/camera/widget work:**
  `tests/test_cameras_swissfel.py tests/test_camserver_stream_qt.py tests/test_subprocess_embed.py tests/test_svg_interactor.py tests/test_desktop_app.py tests/test_assembly_default_widget.py`
  (Qt-backed; need `QT_QPA_PLATFORM=offscreen`, skip cleanly via
  `pytest.importorskip("qtpy")` where Qt isn't installed.)
- **Jungfrau DAP script support:** no automated tests exist yet. Manual
  check only makes sense on a Bernina console with `/sf/jungfrau` mounted:
  `eco.detector.jungfrau.list_dap_env_packages()` /
  `check_dap_env_modules()` against the real `dap`/`sf-dap` conda envs, and
  `upload_custom_dap_script(...)` end-to-end against a real detector +
  `sf_daq_broker` — this has explicitly **not** been done yet (no reachable
  shell on the daq node as of 2026-09, per the new code's own docstrings).

Known-flaky/pre-existing failures to not misattribute to this work (from
CLAUDE.md): `tests/test_config_lazy_init.py` (2 tests), and
`test_desktop_app.py::test_init_all_reconciles_and_unblocks_each_entry_as_it_finishes`
(a real timing race, ~2-3/5 runs).

For anything hardware/beamline-facing (MPOD, camera streams, the Jungfrau
DAP upload, a real scan exercising `compare_channels()` against a live
status server), the only real test is on a Bernina console session
(`scripts/eco-dev -s bernina -l --ui shell` or `--ui desktop`), not from a
local/offline Claude session — flag this explicitly rather than claiming
"tested" from here.

## Open items worth a follow-up

- `Daq.compare_channels()` is not yet wired into any scan callback — it's a
  standalone diagnostic (`daq.compare_channels()`), by design ("for now
  only" show missing/exceeding).
- Jungfrau custom-DAP-script feature has zero automated test coverage and
  is explicitly unverified against a live worker.
- Today's `80e359f`/`ca62667`/`442e08e` commits have no descriptive bodies
  ("fixes mpod", "sc fix", "more") — worth a proper writeup if this history
  needs to be understood later without re-reading the diffs.
- Multiple sessions are editing this exact checkout concurrently (confirmed
  live: a 398-line uncommitted diff to `jungfrau.py` was committed by
  another actor mid-way through writing this summary). Treat any "current
  working tree state" snapshot as provisional.
