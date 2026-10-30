# Queues: run commands one after another, keep the prompt

A queue runs commands one after another on its own thread, so the session
stays free: you can look at data, edit things, or queue more while it works.

```python
%queue scans.ascan(mot, 0, 1, 50, 0.1)
%queue edge = float(input("edge position? "))
%queue scans.ascan(mot, edge - 0.05, edge + 0.05, 100, 0.1)
%queue                        # show the queue
```

The second task asks a question. It appears at your prompt when that task
runs, and the queue waits for the answer. The third task uses the answer.

## Commands run in your namespace, when their turn comes

A queued statement runs in the session's own namespace and is evaluated when
it runs, not when you queue it. If you change a variable at the prompt before
then, the queue uses the new value. A variable a task sets is visible to
later tasks and at the prompt.

## Every task is test-run when you queue it

Nothing moves during the test. It checks that:

- the statement is valid Python, and every name exists, either in the
  namespace or assigned by an earlier task. Typos get a suggestion:
  `name 'scnas' is not defined (did you mean 'scans'?)`;
- attribute chains like `mono.energy` exist. Properties are not evaluated;
- calls match the function's signature;
- scans (`scans.ascan(...)` and friends) can be built, with every planned
  position inside the adjustables' limits;
- `mv` / `umv` / `mvr` / `set_target_value` targets are inside the limits.

A task that fails its test is not queued, and the reason is printed. Use
`%queue --no-test ...` to queue it anyway. `%queue test_run` re-checks
everything still pending.

Arguments that are not plain expressions (names, attributes, numbers,
arithmetic, lists, `np.linspace(...)`) are only checked when the task runs.

## Controlling a queue

Every queue method is also a `%queue` sub-command:

| `%queue ...` | what it does |
|---|---|
| `status` (or nothing) | running, pending and recent tasks |
| `test_run` | test every pending task again |
| `pause_after_running_task` / `pause` | finish the running task, start nothing new |
| `resume` | go on |
| `cancel_after_running_task` / `clear` | finish the running task, drop all pending ones |
| `pause_running_task` / `resume_running_task` | pause/resume the running scan at its next step |
| `stop_running_task` | stop the running task now (a scan stops at its next step) |
| `stop` | stop the running task and drop all pending ones |
| `repeat_last 3` | append the last task three more times |
| `repeat 12, 2` | append task #12 twice |
| `remove 14` / `move 14, 0` | drop task #14 / make it the next one to run |
| `wait` | block until the queue is idle |
| `mute` / `unmute` | stop/restart printing the queue's output at the prompt |
| `show_output` | a window following the queue's output live |
| `record` | append the queue's output to a file (`tail -f` it elsewhere) |
| `release mot` | let `mot` be moved from elsewhere while the task runs |
| `help` | this list |

When a task fails, the queue pauses so later tasks don't run on a broken
step. Look at the error, then `%queue resume`.

## Several queues

```python
%queue -q align scans.dscan(gon.x, -1, 1, 20, 0.1)
%queue -q align status
queues                        # all queues
```

Queues run independently of each other, each on its own thread.

## From Python, and from scans

```python
q = queues["default"]
task = q.append(scans.ascan, mot, 0, 1, 50, 0.1)   # a callable with its arguments
task.description = "after realignment"             # editable while it runs
q.test_run()
scans.ascan(mot, 0, 1, 50, 0.1, queue=True)        # same as appending it
```

## From a form: the scan launcher

```python
scans.launcher()                  # queue="default"; in eco desktop: Tools -> Scan Launcher
```

A form for the common scans (`ascan`, `dscan`, `ascan_position_list`,
`snakescan`, `a2scan`, `meshscan`, `scan`, `acquire`; any other method gets a
form built from its signature). Adjustables and counters are chosen with a
device picker that browses the namespace, with your bookmarks and recent picks.
**Run** appends the scan to the queue, test-run like any other task. The panel
next to the form shows the progress of what you launched, and pauses, resumes
or stops it.

In a notebook, `scans.launcher()` is the ipywidgets version of the form. It
runs the scan directly instead of through a queue.

## Questions from queued tasks

- **A plain question** (`input()`, a `(y/n)` confirmation) blocks the task
  and appears on the main thread: above the prompt if you are idle, or right
  away if a command is running. In the desktop it is a dialog. Ctrl-C
  cancels the question, which counts as an error, so the queue pauses.
- **A timed question with a default**, such as "Change back to initial
  values? ... in 10 seconds" at the end of a scan, takes its default at once
  inside a queue, since nobody is expected to watch. Code that must ask
  anyway uses `input_timeout(..., escalate=True)`
  (`eco.utilities.user_input`).

Live plots of queued scans work as usual. Their windows are created and
updated on the main thread.

## What a running task moves can't be moved from elsewhere

While a task runs, the adjustables it uses are claimed by it: a scan's
adjustables from its start, anything else from the task's first move of it,
including the motors underneath a virtual adjustable. Moving a claimed
adjustable from the prompt, a GUI panel or another queue is stopped before
anything moves:

```text
MoveBlocked: mot is in use by queue 'default', task #1 (scans.ascan(mot, 0, 1, 50, 0.1)) -- not moved.
  To move it anyway, e.g. to fix a stuck move while the task waits:
      with forced():
          <your move of mot>
  ...
```

- `with forced(): mot.mv(0.5)` moves it anyway, e.g. to fix a stuck move the
  scan is waiting for. Each forced move is logged.
- `%queue release mot` frees it for the rest of the task. The task claims it
  again if it moves it again.
- `mot.stop()` is never blocked.

Claims end when the task ends. `task_queue.claims()` lists the current ones.

## Seeing and keeping the output

Every task keeps what it printed, including output from the threads it
started, as `task.output`.

```python
%queue show_output            # live window; stays live while the session is busy
%queue mute                   # print it only there, not at the prompt
%queue record                 # append to ~/.eco/queue_logs/<date>_<queue>.log
```

`record` prints the file name. `tail -f` it in a second terminal to follow
the queue from elsewhere. A failing task's error is shown at the prompt even
when the queue is muted.

## Limits

- `stop_running_task` stops plain Python code at its next instruction; a
  motor already moving keeps moving, as after Ctrl-C.
- Claims cover moves that go through eco (every `mv`/`set_target_value`). A
  `caput` from outside eco is not stopped.
