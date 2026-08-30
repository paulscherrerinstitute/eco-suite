# `eco.robots` — Stäubli VAL3 robot driver

Direct Python control of the Bernina Stäubli TX200 detector arm. Replaces the
Jython half of the pshell deployment in
`/sf/bernina/config/src/python/bernina_robot/script/devices/`.

Nothing here imports the rest of eco, so it starts in milliseconds and can be
used from a plain Python process, a test, or the server in
[`eco.bernina_robot_server`](../bernina_robot_server/README.md).

## The five modules

| Module | Replaces | What it does |
|---|---|---|
| `protocol.py` | (framing inside `RobotTCP.py`) | The VAL3 TCP line protocol. No robot semantics. |
| `staeubli.py` | `RobotTCP.py` | Generic arm: modes, power, motion primitives, tasks, polling. |
| `kinematics.py` | `sph2cart`/`cart2sph` in `RobotBernina.py` | Pure spherical↔cartesian detector conversion. |
| `motors.py` | `RobotMotors.py` | Pseudo-motors: one axis of a coordinate system. |
| `bernina.py` | `RobotBernina.py` | The TX200: frames, tools, safety whitelist, recordings. |
| `simulation.py` | *(new)* | A fake controller, so all of the above runs with no hardware. |
| `persistence.py` | `elements/adjustables.py` | JSON-file-backed values (`frame`, `tool`, recordings). |

## Quick start

```python
from eco.robots import connect_bernina_robot

robot = connect_bernina_robot("129.129.243.106", 1234)   # real hardware
robot = connect_bernina_robot(simulated=True)            # no hardware

robot.setup()            # applies persisted frame/tool, creates the motors
robot.start_polling()    # background thread; everything below reads its cache

robot.get_spherical_pos()          # {'r': 3028.7, 'gamma': 94.8, 'delta': 38.6}
robot.move_spherical(gamma=20, sync=True)
robot.motors["gamma"].move(20)     # same thing, one axis at a time
```

`setup()` and `start_polling()` are **not** implicit: both have side effects on
the controller (they set the frame, the tool, and the user profile).

## The wire protocol

One request/response pair per line, over plain TCP to a VAL3 program:

```
TX:  "<nnn> <command> <arg0>|<arg1>|...\n"
RX:  "<nnn><sep><payload>\n"           sep = ' ' on success, '*' on error
```

Captured from the live controller:

```
TX '000 get_status_fast None\n'
RX '000 -78.88|105   |-12.57|-0.57|10.2 |3.12|2359.25|1888.67|-199.18|3   |-10.88|103.13|846.72|\n'
TX '003 eval tcp_b=isPowered()\n'
RX '003 \n'                       ← empty payload means success
TX '004 get_bool tcp_b\n'
RX '004 0\n'
```

Three things to know:

* Fields are **padded to fixed width** (`105   `), so strip before converting.
* A list reply ends with a **trailing `|`**, so `split('|')` yields one extra
  empty field. Index fixed positions or slice; never trust `len()`.
* Frames are capped at **150 characters** and **20 parameters**.

### Commands

| Command | Purpose |
|---|---|
| `eval <statement>` | Evaluate a VAL3 statement. The workhorse. |
| `get_var`/`get_str`/`get_bool`/`get_arr` | Read a VAL3 variable. |
| `get_trf`/`get_jnt`/`get_pnt` | Read a transformation / joint / point. |
| `get_status_fast <task>` | 13 numbers: j1..j6, x,y,z,rx,ry,rz, z_lin. |
| `get_status_env <task>` | mode, status, powered, speed, empty, settled, task code, task ret, frame, tool, 6 frame trsf, 6 tool trsf, event. |
| `dist_pnt <p1>\|<p2>...` | Distance from the current pose to named points. |
| `task_sts <t1>\|<t2>...` | VAL3 task status codes. |
| `kill <task>`, `save` | Kill a task; persist the VAL3 program. |
| `get_profile`, `set_profile <name>\|<pwd>` | Controller user profile. |
| `get_movel_interpolation`, `get_movec_interpolation` | Ask the controller to interpolate a move without executing it — this is what motion *simulation* is built on. |

Two commands are deliberately routed through `eval` instead of their own
opcode, because the opcode freezes the controller: `taskCreate` and
`movej`/`movel`/`movec`. That workaround is inherited and still required.

## Coordinate systems

Four, all live at once; each is a set of pseudo-motors over the same arm.

| System | Axes | Meaning |
|---|---|---|
| `joint` | `j1`…`j6` | Raw joint angles [deg]. |
| `cartesian` | `x y z rx ry rz` | Pose in the active **frame** [mm, deg]. |
| `spherical` | `r gamma delta` | Detector distance and scattering angles. eco calls `r` → `t_det`. |
| `linear_axis` | `z_lin` | The rail the arm hangs from [mm]. A real servo, not a pseudo-motor. |

**Spherical is the physics one.** The detector must keep pointing at the
sample, so orientation is a *function* of direction:

```
x = r·cos(delta)·sin(gamma)
y = r·sin(delta)
z = r·cos(delta)·cos(gamma)
rx, ry, rz  = f(gamma, delta)     # keeps the detector aimed at the origin
```

A spherical move is therefore **two legs**: a straight `movel` that changes
`r` at constant direction, then a `movec` arc through a computed mid-point
that changes the angles at constant `r`. Arcing keeps the detector at a fixed
distance; a straight line would cut a chord through the sample. If the arm is
not currently sitting on a spherical pose (a previous cartesian move left it
pointing elsewhere), the whole thing degrades to one cartesian move, because
arcing from an off-sphere start is meaningless.

`kinematics.sph2cart`/`cart2sph` are pure functions and were verified against
the pshell originals over 22 059 grid points: **max deviation 2.8 × 10⁻¹⁴**,
i.e. identical. They additionally return a value at 84 points where the
original raised `math domain error` or `ZeroDivisionError` (the `|delta| → 90`
pole). See "Fixes" below.

## Pseudo-motor semantics

Writing one axis has to become a whole-pose move, and three rules govern that:

1. **Setpoint memory.** `target_pos` merges every axis' last setpoint,
   defaulting to the live readback for axes never written. Moving `gamma`
   alone must not reset `r` to wherever the arm happens to be mid-flight.
2. **Move coalescing.** A second write to the *same* coordinate system
   discards the queued motion and re-issues one combined move to the merged
   target — otherwise a two-axis scan step would trace an L instead of a
   diagonal.
3. **Coordinate-system switches** drop the queue first, because the queued
   motion was computed in a frame the new command knows nothing about.

## The remote-motion safety whitelist

In `remote` working mode the arm will only execute a motion whose **start and
end both lie on a trajectory an operator previously drove by hand** with the
pendant.

```python
robot.record_motion(gamma=70, delta=15, r=2700)   # in manual mode, pendant held
```

The commanded path is densified (10 points per interval → 201 poses) and
stored with the frame and tool transforms in effect, in
`adjustables_fs/remote_allowed`. `remote_allowed()` then permits a remote
motion when some recorded trajectory, taken with a **matching frame and
tool**, passes within `remote_allowed_deltas` of both the start and the
target.

That is deliberately weaker than "the whole path is recorded": the controller
interpolates between the two poses itself, and a recording that brackets both
ends is the practical evidence the operator has driven that corridor.

`robot.set_override_remote_safety(True)` disables it entirely. That is an
operator decision, it is logged loudly, and it is broadcast in the status
payload.

> In production today there is exactly **one** recorded trajectory (a
> cartesian one). Remote motion is therefore almost entirely locked down, and
> the override is what makes the arm movable in practice.

## Threading

One socket, serialised by a lock inside `Val3Link`. A single poller thread
calls `update()` at `polling_interval`; every other caller comes from its own
thread.

* **`link.transaction()`** holds the link across a whole multi-call sequence.
  Motion methods use it so the poller cannot slip between the `set_pnt` calls
  and the `movel` that consumes them. The original had no such guard.
* **Cancellation is cooperative.** Python has no equivalent of the Java thread
  interrupt pshell's `:abort` used, so `CancellationToken` is checked at every
  protocol round trip and in every wait loop. Because the socket read is
  bounded by the per-call timeout, worst-case abort latency is one timeout
  (1 s default); measured end-to-end through the server: **30 ms**.
* **The GIL is irrelevant here.** Everything is one socket at ≤ 5 Hz.

## Fixes relative to the pshell original

Behaviour is otherwise 1:1. These were bugs:

| Where | Original | Now |
|---|---|---|
| `_sendReceive` | On a message-id mismatch, referenced an undefined `start` → `NameError`, masking every desync | Reports the desync and drops the socket so the next call resynchronises |
| `sph2cart` | `math domain error` / `ZeroDivisionError` at the `|delta| → 90` pole | Arguments clamped to `[-1, 1]`; degenerate denominator handled |
| `move_*(simulate=True)` | `& (~simulate)`: `~False` is `-1`, `~True` is `-2` — both truthy, so the remote-safety check ran on **simulated** moves and refused them in remote mode | `and not simulate` |
| `remote_allowed` (frame match) | Reused the *motion* deltas as frame tolerances — for spherical, 3 tolerances for 6 components, so the frame's rx/ry/rz were never compared | Dedicated 6-component `trsf_tolerance` |
| `remote_allowed` (joint) | `frame_trsf=None` was subtracted from an array → `TypeError` with a recording present, unconditional refusal without one | Joint motions skip frame/tool matching, which is what they mean |
| recordings store | `{"spherical": temp, "cartesian": temp, "joint": temp}` aliased **one** dict of **one** set of lists — a spherical recording also registered as cartesian and joint, and after a JSON round trip a 3-column path got range-checked against 6 joint coordinates | Independent stores per system, plus a shape guard |
| `reset_recorded_motions` | `.pop[index]` (subscript, not call) → `TypeError` every time | `.pop(index)` |
| `set_default_speed` | `set_monitor_speed(...)` without `self.` → `NameError` | Fixed |
| `get_cartesian_destination` | Computed the value and never returned it | Returns it |
| `set_motors_enabled(..., 'linear')` | Set the destination to `[]` while treating it as a dict elsewhere | `None`, consistently |
| `is_in_points` | `None < 0` — `True` on Python 2, `TypeError` on Python 3 | Explicit `None` handling |
| `cart2sph` on the y-axis | `KeyError` on `cartesian_pos["ry"]` before the first poll | Explicit `ry_fallback` |
| `doUpdate_env` | Rewrote `frame` and `tool` JSON files on **every** poll — twice a second, forever, on NFS | Write-on-change, and atomic (`os.replace`) |
| `RobotMotors` | Reached for a module-global `robot` in half its methods | Uses `self.robot` |
| `wait_end_of_move` | Waited forever for a state only the poller advances — deadlock with no poller | Drives the update itself when no poller is running |

The one **behavioural** change to be aware of: simulated robots no longer
persist to `adjustables_fs/` by default (`persist=False`), because those files
belong to the `gac-bernina` account and a stray write from a personal checkout
leaves files the real server can no longer update.
