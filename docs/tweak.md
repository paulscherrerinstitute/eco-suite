# Tweaking with live detector readout

`tweak` moves an adjustable step by step from the keyboard. It can now record
detectors at every step and plot them live, tweak up to four axes at once, and
run as a panel in notebooks and the desktop.

## Tweak and watch a signal

```python
mot.tweak(0.1, detectors=[mon_opt.intensity])
```

Use the arrow keys to move (left/right) and to double or halve the step
(up/down). `s` returns to the start position and `q` ends the tweak. While
you tweak, the detectors are read at every position (averaged while the
position stays put) and plotted live against the position. Afterwards the data
is still there:

```python
mot._tweak_instance.recorder.to_dataframe()   # Adjustables
mot._tweak_recorder.to_dataframe()            # EPICS motor records
```

When the tweak ends it prints the number of points recorded and where to find
them.

## Several axes: stacked keyboard rows

```python
from eco.elements.adjustable import Tweak

Tweak((m1, 0.1), (m2, 0.1), (m3, 0.05), detectors=mon_opt.intensity).tweak()
```

One adjustable uses the arrow keys and two use the arrows as x/y. Three or four
adjustables each get a keyboard row (first adjustable lowest):

| Adjustable | neg dir | step × 2 | step / 2 | pos dir |
|------------|---------|----------|----------|---------|
| 1st | `j` | `k` | `l` | `;` |
| 2nd | `u` | `i` | `o` | `p` |
| 3rd | `7` | `8` | `9` | `0` |

With four adjustables the rows shift down one: the 1st uses `m` `,` `.` `/`,
and the others take the rows above. `stacked=True` forces this layout for one
or two adjustables too. With several axes the detector plot is stacked over the
step number.

## Front-ends: terminal, Qt panel, notebook panel

eco picks the front-end automatically. In a terminal you get the keyboard loop
above; in a notebook or the desktop console you get a tweak panel with buttons
and the same keys under "keypress control". Force one with `ui=`:

```python
mot.tweak(0.1, detectors=[diode], ui="terminal")   # or "qt", "ipy"
```

While a terminal tweak waits for keys, other open GUI windows (live plots,
camera viewers) keep updating.

## No display? Plots in the terminal

Over plain ssh without X, or with `ECO_NO_X=1` set, no Qt windows are opened
and the detector plot is drawn as text right in the terminal. See
{doc}`terminal_plots`.
