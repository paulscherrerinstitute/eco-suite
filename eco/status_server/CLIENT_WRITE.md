# Client-write mode — the server produces, the session writes

**Status: optional, off by default, for trial.** Nothing below changes what
the status server does today. It is a second path, switched on per session
with one setting, and a server that has these routes serves the old ones
unchanged.

## 1. The problem

Everything the status server produces for a run lands in the shared data
tree:

```
/sf/<instrument>/data/<pgroup>/res/run_data/daq/run<NNNN>/aux/status.json
                                                          /aliases.json
                                                          /namespace_monitor.ixp.h5
```

and is then handed to `sf_daq_broker`'s `copy_user_files` so it becomes part
of the raw run. Today **the server process writes all three** (see
`/status/capture`, `/aliases/capture`, `/recording/capture` in
`namespace_server.py`).

That makes a run's bookkeeping depend on things that have nothing to do with
the measurement:

* the server account must be in the run's pgroup;
* every directory level from `run_data/` down must be group-writable and
  setgid, or the first account to create one locks the others out (see the
  permissions section of the repo's `CLAUDE.md`, which exists because this
  has already gone wrong);
* on NFSv4 with `sec=krb5` the server account needs a **Kerberos ticket**,
  and SSSD stops renewing one after about a week — so a server that has been
  up long enough simply stops being able to write, while every other part of
  it keeps working perfectly;
* the server is normally one shared account (`gac-bernina`) writing into
  pgroups that belong to whoever is on shift.

The session asking for the status has none of these problems. It runs as the
scientist, it is already writing the scan's own files into the same
directory, and its credentials are fresh by construction.

## 2. The split

Keep on the server the part only the server can do — holding an initialized
namespace, holding thousands of CA monitors, encoding a recording — and move
the two steps that need standing in the data tree to the client:

```
  session (scan boundary)            status server                 relay (detached child)
  ------------------------           --------------------          ----------------------
  POST /handoff/status    ---------> create slot (pending)
     <------ handoff_id, job_id      start snapshot in background
  spawn relay, return  ..............................................> GET /handoff/<id>/data?wait=600&release=1
  (scan continues)                   snapshot done -> slot ready
                                     ------------------ bytes ----->  write aux/status.json   (as the user)
                                                                      POST broker/copy_user_files
                                                                      write ~/.eco/status_relay/<id>.result.json
```

The scan boundary still costs **one round trip** — the snapshot runs on the
server, as before, and nobody waits for it. The waiting, the write and the
broker call happen in a detached child process that has nothing to do with
the session any more.

The same shape covers all three products:

| product | server route | what is handed over |
|---|---|---|
| `status.json` | `POST /handoff/status` | the `get_status()` block as JSON (plus any `client_status` merged in) |
| `aliases.json` | `POST /handoff/aliases` | `Alias.get_all()` as JSON |
| `namespace_monitor.ixp.h5` | `POST /handoff/recording` | **the finished h5 file**, built by the same `write_monitor_recording()` the server-side write uses, into the server's own spool directory and streamed from there |

The recording is deliberately encoded server-side. The alternative — ship
raw buffers and build the h5 in the relay — would duplicate the format in
two places and let them drift. `format="npz"` and `format="json"` do exist
on that route, for measuring transport separately from encoding and for a
client that wants the numbers rather than the file.

## 3. Turning it on

Per session, from the shell:

```bash
ECO_STATUS_SERVER_WRITE_MODE=client scripts/eco-dev -s bernina
```

or live, at the prompt:

```python
bernina.daq.status_server_write_mode = "client"   # "server" to go back
bernina.daq.status_server_relay_mode = "process"  # or "thread" / "inline"
```

`Daq.use_client_write()` is the gate every call site goes through. It
requires all three of: the mode is `"client"`, a status server is in use at
all (`use_running_status_server`, `use_status_server()` — unchanged), and
the server actually has the `/handoff` routes. The last one is probed once
per session, so a session pointed at an older server prints one line and
uses the existing path.

Afterwards:

```python
bernina.daq.status_relays()        # what the relays did, newest first
```

which reads `~/.eco/status_relay/*.result.json` and so also sees jobs from
earlier sessions.

## 4. What it costs and what it does not

* **Scan boundary:** unchanged — one POST, then a `fork`/`exec`. The
  snapshot and the h5 encode still happen on the server, in the background,
  exactly as they do now.
* **Transfer:** the payload now crosses the network. A full bernina status
  block is ~3 MB of JSON; a 10 Hz-throttled namespace recording of a few
  minutes was ~22 MB in `DESIGN.md` §15, and a recording that includes
  waveform channels can be hundreds of MB. The relay streams the recording
  straight into the destination file rather than holding it in memory.
* **Server memory/disk:** a slot holds its payload until it is collected.
  Status and aliases are held in memory; a recording is spooled to the
  server's own local disk (`handoff_spool_dir`, by default a per-user
  directory in the OS temp dir). Both are released by the fetch itself
  (`release=1`) and, failing that, by the TTL (`handoff_ttl`, 1 h). The slot
  count is capped (`handoff_max_slots`, 64) and a request over the cap is
  refused rather than evicting somebody else's pending result.
* **The old routes are untouched.** `/status/capture`, `/aliases/capture`,
  `/recording/capture` and the files they write are exactly as before.

## 5. Failure modes

| what fails | what happens |
|---|---|
| the server has no `/handoff` routes | one message, session falls back to server-write mode |
| the `POST /handoff/...` fails | `_status_server_failed()` as for any other server call: warn and fall back (or raise, with `status_server_strict`) |
| the relay cannot be started | the handoff slot is released immediately, same fallback |
| the relay dies after starting | one aux file missing for one run; the result file says why, `daq.status_relays()` shows it, and the uncollected slot expires on the server |
| the client never collects | the slot expires after `handoff_ttl` and logs that it was never collected |
| the write fails (permissions after all) | recorded in the result file with a traceback, and the same `eco.utilities.datafiles` warnings the session's own writes already produce |

Nothing here retries or queues. A lost handoff is one run missing one aux
file — which is what the server-side write already degraded to when it could
not write, except that now it says so in a file owned by the person who ran
the scan.

## 6. One thing that moved

`/status/push` parks a few `scans.acquiring_scan.*` values (which have no CA
channel at all, so the server can never poll them) for the server-side
capture job to hand back with the rest of the status. In client-write mode
there is no such job: the block was collected and written the moment it was
ready. Those values are the session's own, so in client-write mode the
session merges them into the run-table row directly instead of pushing them.
Server-write mode still pushes, and does **not** also merge locally — doing
both would hide a push that silently failed.

## 7. Checking it on a real server

The production server has to be running a build that has these routes, and
restarting it mid-beamtime to try them out is not reasonable. So the usual
way in is a private second server on a port of its own, which
`scripts/status_server_sandbox` starts, waits for, runs all three checks
against and stops again:

```bash
source /sf/bernina/bin/bpy-env
cd <this checkout>
scripts/status_server_sandbox
```

It initializes a single namespace component by default, which is ready in
well under a minute and still exercises every route; `--names all` gives it
the same required set the production server has (4-11 minutes), `--names
a,b,c` a chosen few. Config, log, pid, handoff spool and output all live
under `~/.cache/eco-work/status_sandbox`, and `--keep` leaves the server up
(`scripts/status_server_sandbox stop` ends it).

Against a server that is already running, the check script is used
directly:

```bash
python scripts/status_server_handoff_check.py check
python scripts/status_server_handoff_check.py bench --repeat 5
python scripts/status_server_handoff_check.py check --record-seconds 15
```

It writes only into `--out-dir` (a fresh directory under
`~/.cache/eco-work` by default), uploads nothing unless `--pgroup`,
`--run-number` and `--upload` are all given, and starts no recording unless
`--record-seconds` is. It reports each stage's timing and payload size, the
owner/group/mode of every file it wrote, whether the collected status block
covers the same channels as a plain `/status/snapshot`, whether the written
`aliases.json` is identical to `GET /aliases`, and whether the h5 opens.

## 8. Code map

| file | what |
|---|---|
| `handoff.py` | `HandoffStore`/`HandoffSlot` — slots, TTL, spool, the three recording encoders |
| `namespace_server.py` | the `/handoff/...` routes (additive; one `HandoffStore` per app) |
| `client.py` | `supports_handoff`, `handoff_status`/`_aliases`/`_recording`, `fetch_handoff`/`fetch_handoff_to`, `handoffs`/`release_handoff` |
| `relay.py` | the client side: `run_spec`, `RelayJob`, `submit`, and `python -m eco.status_server.relay <spec.json>` |
| `config.py` | `handoff_spool_dir`, `handoff_ttl`, `handoff_max_slots` |
| `../acquisition/daq_client.py` | `status_server_write_mode`, `use_client_write()`, `_relay_status`/`_relay_aliases`/`_relay_recording`, `_wait_status_job`, `status_relays()` |
| `tests/test_status_server_handoff.py` | the store and the routes |
| `tests/test_status_relay.py` | the relay, including a real detached child against a real server |
| `tests/test_daq_client_write_mode.py` | the switch and the dispatch |
| `scripts/status_server_handoff_check.py` | the against-a-real-server check |
| `scripts/status_server_sandbox` | starts a private server, runs that check against it, stops it |

## 9. Open

* Not yet run against the production server or a real run — that is what the
  script above is for.
* The server still holds the namespace, so the staleness question
  (`status_server_max_age`) is unchanged; this changes who writes, not whose
  values they are.
* If transferring a large recording turns out to matter, the next lever is
  compression on the wire (`Accept-Encoding`/gzip on the h5 stream), not a
  different split.
* `eco.status_server.namespace_component.StatusServer` still only knows the
  server-write helpers (`start_monitoring`/`stop_monitoring`). The daq goes
  through `StatusServerClient` directly for the handoff path; if the
  component grows a `handoff_monitoring()`, the daq should use it the same
  way it uses `stop_monitoring`.
