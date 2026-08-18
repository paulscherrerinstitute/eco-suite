# Creating a listening monitor

A **monitor** subscribes to a channel and records every update it sends, with
timestamps, in the background. Unlike a `Detector` (which you *poll* with
`get_current_value()`), a monitor *listens*: EPICS pushes each new value to a
callback, so you capture the channel's history *going forward* without a polling
loop.

This is implemented by {py:class}`eco.epics.monitor.Monitor`.

## A single channel

```python
from eco.epics.monitor import Monitor

# Subscribing starts immediately by default.
mon = Monitor("SARFE10-PBPG050:HAMP-INTENSITY-CAL")

# ... let it run while your experiment does something ...

# Everything received so far, as parallel lists:
mon.data["value"]            # the values
mon.data["timestamp"]        # EPICS timestamps (seconds since epoch)
mon.data["timestamp_local"]  # local wall-clock time each callback fired
```

`Monitor` keeps collecting until you stop it:

```python
mon.stop_callback()   # unsubscribe; .data is preserved
mon.start_callback()  # resume subscribing
mon.clear()           # drop history, keep only the latest point
```

:::{tip}
Set `mon.print = True` to have each incoming value echoed to the shell as it
arrives — handy for a quick look at how often a channel actually updates and how
large the EPICS-to-local timestamp delay is.
:::

To plot what you have collected so far:

```python
import matplotlib.pyplot as plt

plt.plot(mon.data["timestamp_local"], mon.data["value"], ".-")
plt.xlabel("local time [s]")
plt.ylabel("value")
```

## Several channels at once

{py:class}`eco.epics.monitor.MultiMonitor` starts one `Monitor` per channel (in
parallel, so subscribing to many channels is fast) and can merge them onto a
common time base by interpolation:

```python
from eco.epics.monitor import MultiMonitor

mm = MultiMonitor(
    "SARFE10-PBPG050:HAMP-INTENSITY-CAL",
    "SARFE10-PBIG050-EVR0:CALCI",
)

# ... let it run ...

# Merge all channels onto one sorted timeline:
timestamps, series = mm.merge_data()
series["SARFE10-PBPG050:HAMP-INTENSITY-CAL"]   # values interpolated onto `timestamps`

mm.clear()   # trim every channel's history to its latest point
```

## When to use which

| You want to…                                             | Use                                   |
| -------------------------------------------------------- | ------------------------------------- |
| Read the current value once                              | a `Detector` — `get_current_value()`  |
| Record every update to one channel from now on           | `Monitor`                             |
| Record several channels and align them in time           | `MultiMonitor`                        |
| Retrieve *past* data (before you started listening)      | `DataHub` — see {doc}`archiver_stripchart` |
| Watch values live in a scrolling window                  | `DataHub.strip_chart` — see {doc}`archiver_stripchart` |
