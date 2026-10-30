# Starting eco: front-ends and launchers

eco has five ways to come up, all from one command-line entry point
(`eco_cli.py`, installed as the `eco` console script). Pick the one that fits
the task: the classic IPython terminal, a Qt workbench, or a browser.

:::{note}
The facility wrapper `/sf/bernina/bin/eco` is a separate script that ignores
its arguments. To use the subcommands below from a checkout, run
`scripts/eco-dev` with the same arguments. It runs *that* checkout, not
whatever eco is installed in the environment.
:::

## Subcommands

```bash
eco console    -s bernina     # IPython session (the default)
eco desktop    -s bernina     # Qt workbench: console + Namespace browser
eco jupyterlab -s bernina     # JupyterLab with an eco-bernina kernel
eco webapp     -s bernina     # read-only Voila dashboard
eco box-server -s bernina     # serve the handheld control box, see below
```

| Subcommand | What you get |
|------------|--------------|
| `console` | The traditional session: IPython with the scope's devices in the namespace. |
| `desktop` | A Spyder/MATLAB-like Qt window: an embedded IPython console plus, with `-s`, a dockable **Namespace** panel. Lazy names are greyed out; click one to initialize it and open its widget. `--no-console`, `--no-namespace-panel` and `--theme dark\|light\|none` adjust it. |
| `jupyterlab` | JupyterLab whose Namespace panel and each opened device are real Sidecar dock panels. With `-s` it also registers an `eco-<scope>` kernel and makes it the default, and opens a `jupyter console` on that kernel in your terminal (`--no-console` to skip). |
| `webapp` | The packaged notebook served as a Voila dashboard: widgets only, no code editing. |
| `box-server` | Keeps a connection to the manual-control box open without an interactive session. |

Common flags: `-s/--scope` (`bernina`, `alvra`, ...), `-l/--lazy` and
`--no-lazy` (lazy namespace initialization).

The subcommand goes **first**: `eco desktop -s bernina`, not
`eco -s bernina desktop`. Anything that does not start with a subcommand is
handed to the default command.

## Remember your choice: `.ecorc`

A bare `eco` reads its defaults from an INI file, using the first one found:
`$ECORC`, then `./.ecorc`, then `~/.ecorc`.

```ini
[eco]
command = console
scope = bernina
profile = eco
lazy = true
```

You don't have to write it by hand. Add `--set-rcfile` to any command line, and
eco stores those settings instead of launching:

```bash
eco jupyterlab -s alvra --set-rcfile        # writes ~/.ecorc
eco -s alvra --set-rcfile ./.ecorc          # a project-local one
```

Flags given on the command line still override the file.

## Opening another front-end from a running session

Already in a session? Open any other front-end next to it without blocking
your prompt:

```python
eco.start_desktop()        # Qt workbench; with_console=False, with_namespace_panel=False, theme="dark", touch=True
eco.start_console()        # a second, independent Qt console kernel (kind="jupyterlab" also works)
eco.start_jupyterlab()
eco.start_webapp()         # Voila; port=... to choose the port
```

`start_console()` starts a **fresh** kernel with its own namespace; it does not
share variables with the session you called it from. The desktop's console is
in-process (shares your namespace) when that is possible. When you call
`start_desktop()` from a terminal IPython session it can't be, so the console
falls back to a subprocess kernel; the banner says which case you are in.

## Desktop workspaces and dashboard scripts

eco desktop saves its dock layout and the list of open widgets when its window
closes. A new window always starts empty; the **Workspace** menu brings the
last one back:

- **Reload Last Workspace**: restore the dock layout and reopen the widgets.
- **Save Workspace Now**: save mid-session, not only on close.
- **Save Startup Script...**: write one standalone `.sh` that starts
  `eco desktop` with just this session's open widgets. The layout is embedded
  in the script (`--workspace-json`), so there is one file to share. With lazy
  loading, nothing else in the namespace is initialized: a fast, minimal
  dashboard, e.g. for a control-room screen.

**Tools → Combine Panels into Tabs...** stacks two or more open panels as tabs
in one spot, the same as dropping one panel's title bar on another's. Drag a
tab off the tab bar to split it out again.

## See also

- {doc}`command_history`: every one of these consoles logs its input and
  output, and the Log Viewer shows them all on one timeline.
- {doc}`widget_containers`: building your own panel for the desktop or a
  notebook.
