# Command history, the Log Viewer and the `...` picker

Every eco console keeps a full record of what was typed and what came back.
eco uses that record to show you a searchable timeline, to give you your most
recent and most used commands, and to offer them in an inline picker.

## What is logged

Every console opened through eco is logged automatically, input and output, as
JSON lines under `~/.eco/kernel_logs/`. That covers the `eco` terminal session,
the desktop console, `eco.start_console()`, and JupyterLab/Voila kernels.

If you use eco as a plain library (`from eco import bernina` in your own
IPython), switch it on yourself:

```python
from eco import globallogger
globallogger.start()          # optional: label="my analysis", log_dir=...
```

## The Log Viewer

```python
eco.logs.widget()             # timeline of the last 30 days, all sessions
eco.logs.widget(days=None)    # ... of everything on disk (slower)
eco.logs.tail(20)             # the last 20 entries, as text
```

The viewer opens as a Qt window when there is a usable Qt session, renders
inline in Jupyter, or is written to an HTML file otherwise
(`prefer="qt"|"html"|"webapp"|"subprocess"` forces one). It is zoomable, can
show several sessions at once, and has **Copy Selected as Script**, which turns
the entries you select back into runnable code.

Filtering is done with three buttons above the list. Each opens a searchable
checklist:

- **Kinds**: what an entry is: `input` (a command you typed), `result`,
  `stream` (printed output), `error`, `widget_control` (a GUI click that
  changed something), `session` (start and end of a console).
- **Sources**: which kind of console it came from, such as `desktop:bernina`
  or `console:bernina`.
- **Sessions**: one entry per console session (log file), newest first.

An entry is shown when its kind, source and session are all ticked. **Find**
searches the text of the entries that are shown.

In the desktop the viewer is under **Tools → Log Viewer**. It runs as a
separate process there, so a problem in the viewer cannot affect the desktop.
The JupyterLab/Voila dashboards have a Log Viewer button.

## Recent and frequent commands

```python
eco.logs.recent_commands(20)      # most recent first, de-duplicated
eco.logs.frequent_commands(20)    # ranked by how often you used them
```

## The `...` picker

In an eco session, a bare `...` written where an expression belongs means
"let me pick something here". It is switched on by default in the `eco`
session.

### In the terminal: `...` + Tab

```text
In [1]: (1 / ...<TAB>
          bernina.mono.energy               recent
          bernina.attenuator.transmission   recommended
          bernina.slit1.width               namespace
```

Tab opens IPython's own completion menu with three sections:

- **Recent**: what you picked last.
- **Recommended**: what you use most, counted per user and namespace.
- **Namespace**: a live walk of the whole namespace tree.

Typing after the trigger (`...ene<TAB>`) narrows the namespace section by
substring.

`...h<TAB>` does the same for your previous **commands** instead of
components.

In both cases the pick is only *inserted at the cursor*. Nothing runs until
you press Enter on the finished line yourself.

### Press Enter: the Qt picker

If a `...` is still in the line when you press Enter, a small picker window
opens with two tabs, **Components** and **Commands**:

```text
In [1]: (1 / ...).plot()
        <pick "mono.energy", press Enter>
In [2]: (1 / bernina.mono.energy).plot()     <- pre-filled, not yet run
```

The expanded line is placed at the next prompt for you to check, edit or just
submit. If a line has several `...`, each one asks in turn, left to right.
A single click only highlights a row; picking takes Enter, a double-click or
**Go**. Closing the picker leaves the literal `...` in place.

`arr[...]` (numpy's Ellipsis subscript) is left alone. Without a display, an
unexpanded `...` reaching Enter prints a message instead of opening the picker.

In a library session, turn it on and off yourself:

```python
from eco import ipymagic
ipymagic.start(bernina)
ipymagic.stop()
```
