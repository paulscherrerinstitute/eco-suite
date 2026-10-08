# Terminal plots

Plots normally go to Qt windows or notebook widgets. Where neither is possible, most commonly a plain `ssh` session without X, eco can render them as text in the terminal. This page records how that is done and what is still missing.

## Current approach: uniplot as a dependency

Terminal plots use [uniplot](https://github.com/olavolav/uniplot) (MIT, pure Python, needs only numpy and `readchar`). It is in the Bernina `sfb312` environment (conda) but not declared in `pyproject.toml`.

Checked against uniplot 0.23.2 (2026-10), and sufficient for now:

- `plot_to_string(ys, xs, width=, height=, ...)` returns the plot as a string, so eco controls where and when it is printed.
- Several series per plot, with colors and `legend_labels` / `legend_placement`.
- `histogram(xs, bins=, ...)`.
- `datetime64` x values give time tick labels (`10:00`, `10:10`, ...), which covers archiver data.
- Log axes (`x_as_log`, `y_as_log`), units (`x_unit`, `y_unit`), gridlines, fixed ranges (`x_min` ... `y_max`).
- Character sets `BLOCK` (2x2 quadrants, the default, renders cleanly in every font), `BRAILLE` (2x4, finer, but gaps in some fonts) and `ASCII`.

Plan: wrap uniplot behind a small eco module (proposed name `eco.utilities.termplot`) shaped around eco's own plots: stacked panels, strip charts, counters, archiver traces, the tweak recorder. Callers depend on that module, not on uniplot. If the gaps below start to hurt, replace the renderer with an own copy (MIT allows it; keep the copyright notice) without touching the callers.

## Shortcomings to address in an own copy

Add to this list whenever a terminal plot runs into a limit.

1. **No subplots or shared axes.** Stacks (the tweak recorder's detectors over adjustables) are built by concatenating one `plot_to_string` per panel. The x range has to be passed to every panel by hand (`x_min` / `x_max`). Repeated x tick labels can be avoided with `x_labels=False` on all but the bottom panel, but each panel still has its own frame, so a stack takes more height than it needs.
2. **No y-axis title.** Only `title` (above) and units on the tick labels. In a stack the panel name has to go into `title`, which costs one line per panel.
3. **Interactive pan/zoom only works on static data, and takes over the terminal.** `plot(..., interactive=True)` runs its own blocking `readkey()` loop: arrows / `hjkl` / `wasd` pan, `q` / Esc quits. That cannot update while new data arrives (live strip charts, counters, tweak), and it can't share the keyboard with another key loop. The tweak uses the same arrow keys. An own version should drive zoom from the caller's key loop, on live data.
4. **No managed live region.** `plot_gen()` redraws in place, but knows nothing about other output on the same terminal (status lines written with `\r`, log messages). eco needs one "live region" helper that owns the cursor movement for plots and status lines together.
5. **Limited control over ticks.** `x_labels` / `y_labels` only switch the labels on or off; their number and position are chosen automatically.
6. **Few ways to style points.** `lines` can be set per series (line or scatter), so a highlighted point such as the tweak's current one can be an extra scatter series with its own color. There are no marker shapes or sizes beyond that.
7. **No error bars or bands** (e.g. the recorder's per-point standard deviation).

## Alternatives considered (2026-10)

- **plotext**: 6.x is an API rewrite, so code written for 5.x does not run on it. It is commented out in `sfb312`.
- **plotille**: maintained and braille-based, but no subplots and no advantage over uniplot's `BRAILLE` mode.
- **termplotlib**: needs a `gnuplot` binary. **asciichartpy**: y values only, no x.
- **timg** (in `sfb312`): not a plotting library. It shows images in the terminal: kitty / iTerm2 / sixel where the terminal supports them, colored half-blocks otherwise. This makes it a complementary high-fidelity path: render the matplotlib figure eco already draws to PNG and display it. tmux/screen usually block the image protocols. Not yet tried in practice.
