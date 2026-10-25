# The status server and per-scan namespace recording

Recording the state of the whole namespace (thousands of channels) used to be
done by every scanning session itself: slow, and incomplete wherever that
session had not initialized everything. The **status server** is one
long-running process that holds the fully initialized namespace and does this
work for every session. With it:

- `status.json` and `aliases.json` in each run's `aux` folder are complete,
  because they come from the server's fully initialized namespace rather than
  from whatever your session happened to touch.
- Each scan can record the **update history of every monitorable channel**
  for its whole duration (`namespace_monitor.ixp.h5`), not just snapshots at
  the start and the end.
- Taking those snapshots no longer makes the scan wait; the server writes and
  uploads them in the background.

## Looking at and controlling the server

The server is available in the namespace as `status_server`:

```python
status_server                 # repr: health and recording settings
status_server.status()        # colored health line, failed devices, recent requests
status_server.gui()           # Qt status / reinit window
status_server.reinit()        # re-initialize the components that failed
status_server.restart()       # restart it, wherever it runs
```

From a shell, the `eco-status-server` script does the same and more:

```bash
eco-status-server status          # query its /health
eco-status-server respawn -w      # restart the real server over HTTP, from any host; -w waits until ready
eco-status-server logs -f
eco-status-server gui
```

`respawn` reaches the server wherever it actually runs, on any host and under
any account. `start` and `restart` refuse to create a second server on another
host while a healthy one is already running (`start -f` overrides). Two
servers would drift apart and disturb each other.

## Switching it off from your session

All of these can be changed live, without restarting the session:

```python
daq.use_running_status_server = False   # master switch: back to local status/aliases
daq.scan_monitoring = False             # keep the server, but skip the per-scan recording
```

To start a session without it at all, set `ECO_STATUS_SERVER=off` in the
environment first.

The per-scan recording is new. If it slows down scan starts, or warns at
every scan that monitoring failed, set `daq.scan_monitoring = False` and
report it.

## The per-scan recording: `namespace_monitor.ixp.h5`

```text
<pgroup>/res/run_data/daq/run<NNNN>/aux/namespace_monitor.ixp.h5
```

It holds one escape `ArrayTimestamps` per channel, grouped by the channel's
full dotted alias, with updates throttled to 10 Hz by default. It is
registered in the run's `scan_info_rel.json` under
`scan_parameters["monitors"]`, next to `aliases` and `status`. It can be
opened with escape:

```python
import escape
mon = escape.DataSet(results_file=".../aux/namespace_monitor.ixp.h5")
mon.bernina.mono.energy        # nested by alias
```

The file only exists for runs taken with a status server; there is no local
fallback. Some channels can be missing, for example if they could not be
attached. The full format description is in
`eco/status_server/NAMESPACE_MONITOR_FORMAT.md` in the repository.
