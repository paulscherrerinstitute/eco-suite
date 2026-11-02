# Simulated scan targets: `sim`

To try a scan, develop a scan feature or test an adaptive scan, you need
something to scan that gives a known answer. `sim` in the bernina namespace
holds in-memory motors and detectors with a signal behind them: no EPICS, no
beam, no hardware. Its motors and detectors behave like real ones, with move
overhead, settling, backlash, readback noise, slow update rates, lag and drift.
Children are built on first use, like everything else in the namespace.

```python
sim.peak.signal.scans.ascan(sim.peak.x, -2, 2, 41, 0.2)      # 41 points, 0.2 s each
scans.ascan(sim.peak.x, -2, 2, 40, 0.2, counters=[sim.peak.signal])   # the same, through scans
```

The second form works because scans take detectors as counters (see
{doc}`scanning`): `counters=` replaces the default counters, so the DAQ is
not involved.

## The signals

| Component | Axis | Signal |
|---|---|---|
| `sim.step` | `x` | an edge (error function): position and width |
| `sim.peak` | `x` | a Gaussian peak on a background |
| `sim.gradient` | `x` | mostly linear, slightly curved |
| `sim.peak2d` | `x`, `y` | a tilted, asymmetric 2-D Gaussian |
| `sim.oscillation` | `delay` | pump-probe: a step at t0 plus one damped oscillation |
| `sim.oscillations` | `delay` | the same with three overlaid modes |

Each one has its axes and a `signal` detector. A shared `sim.beam` multiplies
every signal by the same shot-to-shot intensity, which `sim.beam.i0` reads,
so normalising `signal / i0` works as on real data.

The signal's parameters (`sim.peak.center`, `.fwhm`, `.amplitude`,
`.background`, ...) are ordinary adjustables, read when each value is
produced. Move them while a scan runs to see how a scan (or an adaptive
strategy) copes with a peak that moves:

```python
sim.peak.center.mv(1.2)
sim.peak.truth([0.5, 1.0, 1.2])      # the noise-free model at these positions
```

## Behaving like a real device

Axes and detectors carry timing and noise parameters, with presets for typical
devices:

| Axis presets | Detector presets |
|---|---|
| `ideal`, `stepper`, `piezo`, `delay_stage`, `goniometer`, `electronic_delay` | `ideal`, `diode`, `epics_slow`, `electrometer`, `lockin`, `camera_roi`, `thermal` |

```python
sim.peak.x.apply_preset("stepper")           # one axis
sim.peak.signal.apply_preset("electrometer") # one detector
sim.peak.apply_presets(axis="piezo", detector="lockin")
```

or for everything at once, when building your own:

```python
from eco.devices_general.simulated_signals import SimulatedSignals
sim2 = SimulatedSignals(axis="stepper", detector="diode", delay_axis="delay_stage")
```

Axis parameters: `overhead` (latency before motion starts), `speed`,
`settle_time`, `backlash` (on reversal the load stays put until the play is
taken up; the readback shows the motor side), `readback_noise`, and `jitter`
(of the physical position, as for pump-probe timing jitter).

Detector parameters: `update_rate` (values per second, held in between),
`response_time` (a first-order low-pass, as for an electrometer or lock-in),
`dead_time` (the value describes the state that long ago), `read_latency`
(each read blocks), `noise`, `noise_rel` (relative, shot-noise-like) and
`drift` (signal units per hour).

The noise is reproducible within a detector tick: reading twice in the same
tick gives the same value, as for a real record that has not updated yet.

See also: {doc}`adaptive_scans`, which uses `sim` for its examples.

```{admonition} Did you know?
:class: tip

`sim.peak.truth(x)` is the noise-free model: compare it with what a
scan or an adaptive strategy found ({doc}`adaptive_scans`).
```
