# eco startup modes

eco has four front-ends, selected with `--ui` (see `eco_cli.py`). **The
default remains `shell` (the traditional terminal session) and nothing
about it changes** — `/sf/bernina/bin/eco`, the script everyone actually
runs today, is untouched and keeps working exactly as before. Everything
below is additive and opt-in.

| `--ui`    | What it is                                                    | Status |
|-----------|----------------------------------------------------------------|--------|
| `shell`   | Traditional interactive IPython session (default)              | live, unchanged |
| `lab`     | JupyterLab on the packaged notebook (`eco/voila_app.ipynb`)     | existing, not yet wired into `/sf/bernina/bin/eco` |
| `voila`   | That notebook served as a standalone Voila dashboard            | existing, not yet wired |
| `desktop` | A Spyder/MATLAB-like Qt workbench window                        | new (this round), not yet wired |

None of `lab`/`voila`/`desktop` are reachable from `/sf/bernina/bin/eco`
yet — `eco_cli.py` (the module that implements all four) isn't installed
as the live `eco` command in `bpy312`. Try them directly for now:

```bash
source /sf/bernina/bin/bpy-env
cd /sf/bernina/code/gac-bernina/eco
python3 eco_cli.py --ui desktop            # the new Qt workbench
python3 eco_cli.py --ui desktop --theme dark --lazy
python3 eco_cli.py --ui lab                # existing JupyterLab notebook
python3 eco_cli.py --ui voila              # existing Voila dashboard
```

Wiring `eco_cli.py` up as the actual `eco` command (so `eco --ui desktop`
just works) is a separate, deliberately-not-yet-taken step — it touches
the live launcher script everyone uses, so it wants its own explicit
go-ahead rather than happening as a side effect of this work.

## `desktop` — the Qt workbench

`eco/widgets/desktop_app.py`. One window:

- **Center**: a live IPython console (`qtconsole`) with `namespace`
  already available. Its kernel is in-process (same Python process as the
  window itself, so it can literally share the calling terminal's own
  namespace dict) whenever that's safe — but *not* when this process
  already has its own running IPython shell (the normal case for calling
  `eco.start_desktop()` from an `ipython` terminal session): a second
  in-process kernel can't coexist with one already running (IPython's
  `InteractiveShell` is a process-wide singleton), so the console falls
  back to a real subprocess kernel with its own, freshly-rebuilt
  `namespace` instead. See `eco.widgets.console_kernel` for the full
  explanation — the banner shown when the console opens says which case
  you're in. Pass `with_console=False` to skip this entirely: no console,
  no kernel, no MultipleInstanceError/PYTHONPATH subtleties to worry
  about — the calling terminal stays the one and only "master" session
  (see the "New independent console(s)" section below for the opposite:
  a genuinely separate second session).
- **Left dock, "Namespace"**: every registered name in the namespace,
  filterable. Names already initialized are opened immediately on click.
  Lazy (not-yet-built) names are shown grayed out; clicking one triggers
  initialization in the background (a spinner shows on that row while it
  runs) and opens it automatically once ready. Failed names are shown in
  red and are not retried automatically.
- "Opening" a name calls `<name>.widget()` directly (not through the
  console — see `EcoDesktopApp._open_widget`'s docstring for why calling
  it straight from the launcher's own Qt code already gets a real QWidget
  back, the same as typing it at the calling terminal's own prompt), so
  this works identically whether or not `with_console` is on. **Known v1
  limitation**: each opened widget is still its own top-level window, not
  its own dock panel — true `QDockWidget` embedding is a reasonable next
  step, not attempted in this first pass.
- A per-class hook lets a device supply a purpose-built view instead of
  the generic property grid: set `_default_widget = "some_method_name"`
  on the `Assembly` subclass (see `eco.elements.assembly.Assembly.widget`).
  `AxisPTZ` already does this — `_default_widget = "viewer"` opens its
  live-video viewer instead of a plain slider grid.
- A modern theme is available: pass `theme="dark"`/`"light"` — uses the
  real `qt-material` package (now installed) if present, else a
  hand-built approximation of its default teal-on-charcoal look; see
  `eco.widgets.qt_theme`. Same switch used by
  `eco.widgets.camserver_panel_qt`/`camserver_stream_qt`.
- **Workspace menu**: "Save Workspace Now" writes the dock layout plus
  which namespace entries are currently open to
  `~/.eco/desktop_workspace.json` (also happens automatically when the
  window closes); "Reload Last Workspace" restores both — reopening each
  widget the same way clicking it would (lazy entries get re-initialized
  first). A fresh window always starts empty; reloading is only ever an
  explicit action, never automatic on startup. "New (Empty) Workspace"
  just forgets the tracked open-widget list for the current session.

## Testing any of this from a running terminal session, without leaving it

Once eco has started (`namespace` exists in your session — the normal
case), three functions are available directly as `eco.X()`:

```python
eco.start_desktop(theme="dark")     # opens the Qt workbench, non-blocking;
                                     # its console shares *this* session's own
                                     # namespace when that's possible (see above)
eco.start_desktop(with_console=False)  # no embedded console/kernel at all --
                                     # this terminal stays the only "master"
                                     # session; the Namespace panel still
                                     # works fully either way (see below)
eco.start_console()                 # a brand-new, INDEPENDENT console/kernel --
                                     # a second working session, not another view
                                     # onto this one. kind="qt" (default) or
                                     # kind="jupyterlab"
eco.start_jupyterlab()              # attaches to a running JupyterLab server if
                                     # there is one, else starts a background one
eco.start_webapp()                  # starts (or reuses) a background Voila server
```

None of these block your terminal — the whole point is to try any of the
other front-ends side by side with the one you're already in. See
`eco/widgets/app_launchers.py` for the full signatures.

### Every kernel is tracked, with its full input/output history

Whether opened via `start_desktop()` or `start_console()`, every kernel's
activity — everything typed or run, everything that came back (results,
stdout/stderr, errors) — is logged as it happens, tagged with which kernel
it belongs to, so a sequence of commands across several
consoles/widgets can be reconstructed after the fact instead of only being
visible in whichever window happened to be open at the time:

```python
from eco.widgets import kernel_registry
kernel_registry.sessions_summary()   # what's running right now, and where its log is
kernel_registry.find_all_logs()      # every session's log on disk, including past ones
session.tail(20)                     # last 20 events for one KernelSession
```

Each session's log is a plain JSONL file under `~/.eco/kernel_logs/`, one
line per event (`session_start`, `input`, `result`, `stream`, `error`),
timestamped. See `eco.widgets.kernel_registry` and
`eco.widgets.console_kernel.LoggingJupyterWidget` (the console widget both
`start_desktop()` and `start_console()` build on, which is what actually
does the logging).

Try it from Python directly instead of the CLI:

```python
from eco.widgets.desktop_app import EcoDesktopApp, build_namespace
namespace = build_namespace(scope="bernina", lazy=True)
app = EcoDesktopApp(namespace, theme="dark")   # blocks until the window closes
```

## `lab`/`voila` — the browser/ipywidgets side

Already built (a prior round, not this one) in `eco/widgets/widget_tray.py`
(`WidgetTray`, `make_namespace_dashboard`) and `eco/voila_app.ipynb`. This
is the Voila-compatible answer to the same "browse the namespace, open
and close widgets without leaking EPICS polling" problem — it works in
Voila (no JupyterLab shell needed) as well as JupyterLab/Notebook, which
`sidecar` (below) cannot.

New this round: `eco/widgets/jupyter_sidecar.py`'s `open_in_sidecar(obj,
anchor=...)` — a thin wrapper around the `sidecar` package
(`pip install sidecar`) for **JupyterLab specifically**: it opens
`obj.widget()` in a real Lumino dock panel that you can then freely
drag/tab/tile via JupyterLab's own UI, the closest browser-side analogue
to the Qt side's dockable panels. It does **not** work in Voila or
classic Notebook (no Lumino shell to dock into there) — use
`widget_tray`/`voila_app.ipynb` for those. Degrades to a clear
`RuntimeError` (not a traceback) if `sidecar` isn't installed.

JupyterLab's own **Workspaces** feature (`jupyter lab workspaces export/
import`, also in the command palette) is the closest browser-side
equivalent of the Qt side's window-layout persistence — not eco code,
just worth knowing it exists if a sidecar-heavy layout is worth saving.

## What's genuinely new vs. what already existed

New in this round: `eco/widgets/desktop_app.py` (the whole Qt workbench),
`eco/widgets/jupyter_sidecar.py`, the `_default_widget` hook on
`Assembly.widget()` (`eco/elements/assembly.py`) plus `AxisPTZ` using it,
the `desktop` choice wired into `eco_cli.py`, and this file. Everything
under `lab`/`voila` (`widget_tray.py`, `voila_app.ipynb`, `eco_cli.py`
itself) already existed going into this round and is described here only
for the full picture.

Newer still: `eco/widgets/console_kernel.py` (the in-process/subprocess
kernel-building + input/output logging shared by every console widget),
`eco/widgets/kernel_registry.py` (the cross-kernel activity log described
above), `eco/widgets/console_window_qt.py` and `eco.start_console()` (a
genuinely independent second console/kernel). These replaced
`desktop_app.py`'s original always-in-process kernel, which crashed
(`ipykernel`'s `MultipleInstanceError`) the moment `eco.start_desktop()`
was called from an already-running IPython terminal session — the single
most common real way to actually use it. See
`eco.widgets.console_kernel`'s module docstring for the full explanation.
