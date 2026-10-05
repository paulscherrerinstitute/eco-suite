# Task: add a "Select curves" toolbar button to `escape.plot_utilities`

> **Status 2026-10-05: done.** Implemented in escape-fel as
> `escape.plot_utilities.attach_select_button` (`escape/select_gui.py`, commit
> `f779cb2`, tag `v0.2.14`; 14 tests in `tests/plot/test_select_curves.py`;
> Appendix B below passes unchanged against it). eco calls it from
> `eco/dbase/archiver.py::_attach_select_curves`, falling back to its built-in
> copy until the beamline environments have escape >= 0.2.14. This file is kept
> for reference only: do not redo the task.

For a session working in the **escape-fel** repo. You have no context from the
conversation this came from, so everything you need is here. The working checkout
is `/sf/bernina/config/personal/lemke_h/escape-fel`. Line numbers are deliberately
not quoted: grep for the names given below.

A **tested prototype** exists in the eco repo, in
`/sf/bernina/config/personal/lemke_h/eco/eco/dbase/archiver.py` (functions
`_antialiasing` ... `_attach_curve_selector`, called from `_plot_dataframe`).
Appendix A is a verbatim copy as of 2026-10-05; the eco file is authoritative if
they differ. Appendix B is a headless behaviour check that passes against the
prototype and is meant to pass, unchanged, against your implementation. Your job
is to turn the prototype into a proper escape tool that follows escape's existing
toolbar-button pattern. Don't paste it in as it is.

## 1. What this is for

eco's archiver plots (`DataHub.get_data_time_range(..., plot=True)`) now produce
figures with many curves. One Assembly gives nine: x/y/z times direction/offset/
readback. The legend covers the data, and one curve's scale (up to about 120)
dwarfs the others (0 to 25), so the interesting curves are squashed flat. The user
wants a button in the plot window's own toolbar that opens a small window with one
checkbox per curve, to show only some of them. Hiding a curve must also drop it
from the legend and re-fit the y-range, since "this one's scale dwarfs the rest"
is the usual reason to hide one.

The user decided this belongs in escape's plot helpers, next to the Fit / Peak /
Freq toolbar buttons, as an **opt-in option**, and that eco will then call it
instead of carrying its own copy (section 6). The user was specific about
placement: **the toolbar's empty slot directly to the right of the Save icon**.

Is there a stock matplotlib tool for this? Not as a ready-made popup.
`matplotlib.widgets.CheckButtons` is the building block, and the "legend picking"
recipe lets you click legend entries. The Qt figure-options dialog (the toolbar's
"Edit axis, curve ..." button) edits colour/style/labels but has **no show/hide
toggle**. The first prototype used `CheckButtons` and was dropped, see 4.2.

## 2. Required behaviour

1. **Button (Qt backends).** A toolbar button with a "list of checkboxes" icon,
   tooltip `Select curves to show`, placed **directly after Save** and in front of
   the stretchy coordinates label.
2. **Window.** Clicking opens a non-modal window titled `Select curves`, a child of
   the figure window, containing:
   - a filter box (case-insensitive substring match on the labels),
   - a scrollable list, one checkbox per curve of the active axes, each row showing
     the curve's legend handle (short coloured line with a dot) and its label,
   - `All` / `None` buttons that act on **the rows the filter currently shows**,
     so "only the readbacks" is: `None`, type `readback`, `All`.
   Ticking applies immediately. There is no OK button.
3. **Effect of hiding a curve.**
   - `line.set_visible(False)`.
   - The legend is rebuilt without it: same location, title, column count and
     frame. Other labelled artists (bands, scatter) keep their entries. If the axes
     had **no** legend, none is created. The legend look must survive the state
     where every curve is hidden (the legend is then gone, and "All" must bring it
     back).
   - The y-range is re-fit to the visible curves: `relim(visible_only=True)`,
     `autoscale(enable=True, axis="y")`, `autoscale_view(scalex=False)`. The x-range
     is left alone. This must also work **after a toolbar zoom/pan**, which switches
     y-autoscale off.
4. **Lifetime.** Clicking again reuses the same window (it keeps the filter text and
   scroll position) and brings it to the front. Closing the figure closes it.
5. **Contract**, the same as `attach_fit_button` / `attach_peak_button`:
   - a no-op, not an error, on unsupported backends,
   - failures are swallowed with a printed `[escape] ...` note,
   - idempotent per figure,
   - honours `ESCAPE_TOOLBAR_BUTTONS`,
   - accepts and runs the `before_click` hook,
   - no button for fewer than two curves.
6. **ipympl** (`%matplotlib widget`) gets the same behaviour through ipywidgets, 4.3.

## 3. Where it goes

| Where | What |
|---|---|
| `escape/select_gui.py` (new) | The engine (pure matplotlib: apply visibility, rebuild legend, refit y), the Qt dialog, and the ipywidgets panel. Import only matplotlib at module level, and qtpy/ipywidgets **lazily inside functions**. Same split as `fit_gui.py` / `freq_gui.py` and `_PeakEngine`'s "engine shared by Qt and ipywidgets front-ends". It also keeps the engine and dialog testable in a bare interpreter. |
| `escape/plot_utilities.py` | `_build_select_icon`, `_insert_after_save`, `_run_select_button`, `_attach_select_button_qt`, `_attach_select_button_ipympl`, public `attach_select_button(fig, *, before_click=None)`. Add `select=False` to `attach_escape_buttons`. Add `select_button=False` to `nfigure`, `nsubplots`, `nsubplot_mosaic`, with docstrings. Add `"_escape_select_gui"` to `_AXES_GUI_ATTRS`, so `_close_axes_guis` cleans it up. |
| `docs/api/plotting.rst` | A short "Toolbar buttons" section with `.. autofunction:: escape.plot_utilities.attach_select_button`. The other `attach_*` functions aren't listed there at present. Don't add them unasked. |
| `escape/icons/` | Probably nothing: see 4.5. |

Follow the existing pattern literally. Read, in `escape/plot_utilities.py`:
`attach_fit_button`, `_attach_fit_button_qt`, `_attach_fit_button_ipympl`,
`_run_fit_button`, `_run_before_click`, `_detect_plot_backend`,
`_track_active_axes` / `_get_active_axes`, `_get_or_create_axes_gui`,
`_defer_to_event_loop`, `ESCAPE_TOOLBAR_BUTTONS`, and `IpywidgetsPeakAnalyzer`
for how an ipywidgets panel is built, shown and closed. In `escape/_axes_selection.py`
read `snapshot_data_lines`.

Specifics:

- **`_run_select_button(fig)`**: `_run_before_click(fig)`, then `ax = _get_active_axes(fig)`
  (print and return if `None`), then
  `_get_or_create_axes_gui(ax, "_escape_select_gui", lambda: <dialog>)`.
  `_get_or_create_axes_gui` raises an existing panel to the front but does not
  `show()` a new one, so **the factory must show the dialog it returns**.
  Importing the dialog's module belongs inside the click handler, as for Fit.
- **Which lines:** the Line2D artists of the active axes. Use `snapshot_data_lines(ax)`
  for consistency with the Fit/Freq tools. It already skips escape's own overlays
  (`_escape_overlay`) and SpanSelector handle lines. If you find lines added after
  that snapshot are missing, filter `ax.get_lines()` yourself with the same
  exclusion rule instead, and say so in your report.
- **Labels:** `line.get_label()`. Matplotlib's "no legend entry" labels start with
  `_` (`_child3`): show those as `(line 3)`.
- **Multi-axes figures:** active axes only, like Fit/Peak. Lines on a `twinx` axes
  belong to another Axes object: not handled in this version, mention it in your report.
- **Default:** `select_button=False` / `select=False` (opt-in), unlike Fit.

## 4. Implementation notes (all learned the hard way while prototyping)

### 4.1 Engine
Port `_CurveSelector.apply` and `_legend_kwargs` from Appendix A. Why each piece:

- `self._legend_look` is captured once at construction and refreshed from the live
  legend on every `apply`, because with every curve hidden there is no live legend
  to read from and "All" would otherwise not bring it back. `None` means "the axes
  never had a legend: never create one".
- The rebuild goes through `ax.get_legend_handles_labels()` filtered by
  `get_visible()`, not through "the selector's lines", so labelled artists the
  selector doesn't manage (a `fill_between` band, a scatter) keep their entries.
- `ax.autoscale(enable=True, axis="y")` is needed: after a toolbar zoom/pan
  matplotlib switches autoscale off for that axis and `autoscale_view` alone then
  does nothing.
- `relim(visible_only=True)` (the default includes hidden lines), **but** `relim`
  only knows lines/patches/images, not collections. With `ax.collections` present
  (scatter, `fill_between`, escape's `errortube`) it would shrink the range to the
  lines alone, so the prototype leaves the limits alone then. If you can do better
  (compute the range from the visible artists by hand), do, and say so.
- Not preserved on the rebuilt legend: `bbox_to_anchor` (the stored value is a
  `TransformedBbox` that can't be passed back as it is). Mention it in your report.
- Legend look is read from the private `legend._loc` / `_ncols`(`_ncol` on older
  matplotlib), there is no public getter. Guarded with `getattr`.

### 4.2 Qt dialog
Port `_make_select_dialog`. It already uses `qtpy`, like the rest of escape.
Don't use `matplotlib.widgets.CheckButtons` for the list: it puts the label at 25%
of the axes width (a huge gap in a wide window), clips long labels (these are ~55
characters), and has neither scrolling nor a filter. The prototype's first version
used it and was replaced.

Compatibility points already handled in the prototype (keep them):
- No Qt enums at all (`QCheckBox.setChecked(bool)`, colour via `QColor(0,0,0,0)`),
  so it works on PyQt5/6 and PySide alike.
- `getattr(QtGui.QPainter, "Antialiasing", None) or QtGui.QPainter.RenderHint.Antialiasing`
- `getattr(QtGui, "QAction", None) or QtWidgets.QAction` (Qt6 moved it to QtGui)
- `fontMetrics().horizontalAdvance` with a `width` fallback (Qt < 5.11)
- **Every slot is wrapped** (`guarded`): PyQt5 calls `qFatal()`/`abort()` when an
  exception escapes a slot unless `sys.excepthook` was replaced, and "swallow and
  print" is escape's convention anyway. Print `[escape] ...` rather than using the
  logger the prototype uses.
- The dialog is **parented to `fig.canvas.manager.window`**, and the selector also
  closes it from a `close_event` hook. Parent-child deletion alone leaves it alive
  until Qt processes deferred deletes. Keep a Python reference (the
  `_get_or_create_axes_gui` cache does): a QWidget nobody references is garbage
  collected and silently disappears, as that function's docstring already says.

### 4.3 ipympl front-end
Mirror the Fit button's ipympl path exactly: a zero-argument callable set as an
attribute on the toolbar (`toolbar.escape_select_button = _on_click`) plus an extra
`toolbar.toolitems` entry `("Select", "Select curves to show", "<font-awesome-4 name>",
"escape_select_button")`. Try `check-square-o` or `list-ul` and look at what renders.
The click handler is `_defer_to_event_loop(lambda: _run_select_button(fig))`.

The panel is an ipywidgets `VBox`: a `Text` filter, a scrollable container of
`Checkbox` rows (`layout=widgets.Layout(max_height="400px", overflow_y="auto")`), an
`HBox` of `All`/`None` buttons. It drives the **same engine** as the Qt dialog. Show,
cache and close it the way `IpywidgetsPeakAnalyzer` does. You probably cannot run an
ipympl notebook in this environment: implement it per the pattern, then say plainly in
your report that the ipympl path is untested, so the user can try it.

### 4.4 Placing the Qt button after Save
`toolbar.addAction(...)` **appends**, and matplotlib's Qt toolbar ends in a stretchy
coordinates label (`toolbar.locLabel`), so an appended button lands at the far right,
past the label. (That is where escape's existing Fit/Peak/Freq buttons land, via
`addSeparator()` + `addAction`. Leave them alone, but tell the user: the same helper
could move them next to Save if they want.) Instead insert in front of whatever
follows Save: `toolbar._actions["save_figure"]` is matplotlib's own record of it
(private, so guard with `getattr`), then `toolbar.insertAction(next_action, action)`.
Fallbacks, in order: in front of the action that wraps `locLabel`
(`toolbar.widgetForAction(a) is toolbar.locLabel`), then plain append. See
`_add_toolbar_button` in Appendix A. Put the logic in `_insert_after_save`.

### 4.5 Icon
`_build_select_icon(size=24)`: draw it procedurally, as `_checklist_icon` does in
Appendix A, in **the toolbar's own text colour**:
`toolbar.palette().color(toolbar.foregroundRole())`. `QToolBar` paints with the
`ButtonText` role, not `WindowText`. matplotlib only recolours its own PNG icons when
the toolbar background is dark, and uses that same role. Do **not** hard-code
`"black"` as `_build_fit_icon`'s fallback does: a black glyph vanishes on dark themes.
For the same reason, don't add an SVG for this one (fixed colours): leave
`escape/icons/` alone unless you have a better idea, and say why in your report.

### 4.6 Traps when testing headless
Appendix B already contains these workarounds. For reference:
- matplotlib refuses a Qt backend when it thinks there is no display
  (`cbook._get_running_interactive_framework()` returns `"headless"`): bypass it.
- `backend_qt._create_qApp` raises `Invalid DISPLAY variable` unless a `QApplication`
  already exists: create one yourself, with `QT_QPA_PLATFORM=offscreen`, **and keep a
  reference to it**: it is destroyed as soon as it is garbage-collected.
- `QApplication.processEvents()` does not run deferred deletions.

## 5. Tests / verification

`CLAUDE.md` says there is no maintained suite, but the sibling hand-off in the eco
repo (`escape_default_eventworker_handoff.md`) assumes pytest. Check the repo's
current state: if a pytest setup exists, convert Appendix B into a test file
(`pytest.importorskip("qtpy")`, and skip when the Qt platform plugin can't start).
If not, keep it as a standalone script and say how to run it. Either way it must pass:

    QT_QPA_PLATFORM=offscreen python3 check_select_curves.py     # prints "all checks passed"

It was written against the API names in section 3: the adapter at its top imports
`attach_select_button` and finds the dialog at `fig.axes[0]._escape_select_gui`. It
falls back to the eco prototype when escape doesn't have the function yet, which is
how it was validated. If you name or cache things differently, adjust the two
adapter functions, not the checks.

Environment: per `CLAUDE.md`, the bare `python3` usually lacks the dependencies to
import `escape` itself. The check script needs only numpy, matplotlib, qtpy and a Qt
binding, so you can run it even in such an interpreter, but it only exercises your
code once `escape.plot_utilities` is importable. Use the interpreter you normally use
for this repo for that. The prototype was verified with Python 3.9, matplotlib 3.9.4,
PyQt5 and qtpy 2.4.3.

Also try it for real once, if a display is available: `fig = nfigure(select_button=True)`,
plot a few labelled lines, and look at where the button sits.

## 6. Downstream: eco switches to your version

Not your change, but it is why names matter. Once your version is released, eco's
`_plot_dataframe` (in `eco/dbase/archiver.py`) will replace its own
`_attach_curve_selector(fig, ah, lines)` call with, roughly:

    try:
        from escape.plot_utilities import attach_select_button
    except ImportError:          # older escape without the selector
        attach_select_button = None
    ...
    if attach_select_button is not None:
        attach_select_button(fig)

and delete the local copy (`_antialiasing` ... `_attach_curve_selector`). So keep
`attach_select_button(fig, *, before_click=None)` **stable**, and tell the user the
final name, the files you changed, and the version that contains it, so eco can
feature-detect or pin. eco's figures have a legend created by `plt.legend()` and `.step`
lines with `.-` markers, all on the first (only) axes, which your "active axes"
default covers.

## 7. Don't

- **Don't `git push`.** Per `CLAUDE.md`, pushing `main` auto-tags and **publishes to
  PyPI** via `.githooks/pre-push`. Commit locally only if the user asks, and let them
  decide about pushing.
- Don't change Fit/Peak/Freq placement or behaviour (see 4.4), and don't touch the
  EventWorker work in the other hand-off file.
- Don't add `matplotlib.widgets.CheckButtons` back (see 4.2).

## 8. Done when

- `attach_select_button(fig)` adds the button right after Save on a Qt figure with two
  or more labelled lines, and does nothing, silently, on a single-line figure, on a
  non-Qt/non-ipympl backend, and when `ESCAPE_TOOLBAR_BUTTONS` is off.
- `nfigure/nsubplots/nsubplot_mosaic(select_button=True)` and
  `attach_escape_buttons(fig, select=True)` do the same.
- Appendix B passes against your implementation, and the existing code paths (Fit, Peak,
  Freq buttons) behave as before.
- The docs entry builds (`sphinx-build docs docs/_build/html`, if the environment has Sphinx).
- Your report to the user states: the public name and signature, files changed, how the
  lines are chosen (`snapshot_data_lines` or your own filter), what is **not** handled
  (collections, `twinx`, `bbox_to_anchor`, ipympl untested?), whether the version needs a
  bump (it comes from git tags), and the observation about the other buttons' placement.

---

## Appendix A: the prototype (eco, 2026-10-05), verbatim

Uses `_logger` (a stdlib logger) and `from matplotlib import colors as mcolors`
from the top of that module. Port to escape's conventions: `[escape]` prints instead
of the logger, lazy qtpy imports as already done here.

```python
def _antialiasing(QtGui):
    return getattr(QtGui.QPainter, "Antialiasing", None) or QtGui.QPainter.RenderHint.Antialiasing


def _checklist_icon(toolbar, QtGui):
    """A small "list of checkboxes" icon, drawn in the toolbar's own text
    colour so it matches the built-in monochrome buttons in light and dark
    themes."""
    size, ratio = 24, 2
    pixmap = QtGui.QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(_antialiasing(QtGui))
        painter.setPen(QtGui.QPen(toolbar.palette().color(toolbar.foregroundRole()), 1.6))
        for row, y in enumerate((3, 10, 17)):
            painter.drawRect(3, y, 5, 5)
            painter.drawLine(11, y + 2, 21, y + 2)
            if row != 1:  # the middle one is the unticked one
                painter.drawLine(4, y + 3, 5, y + 4)
                painter.drawLine(5, y + 4, 8, y)
    finally:
        painter.end()
    return QtGui.QIcon(pixmap)


def _swatch_icon(QtGui, color):
    """A short line with a dot in `color`: the curve's legend handle."""
    pixmap = QtGui.QPixmap(28, 12)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(_antialiasing(QtGui))
        qcolor = QtGui.QColor(mcolors.to_hex(color))
        painter.setPen(QtGui.QPen(qcolor, 2))
        painter.drawLine(1, 6, 27, 6)
        painter.setBrush(qcolor)
        painter.drawEllipse(10, 3, 6, 6)
    finally:
        painter.end()
    return QtGui.QIcon(pixmap)


def _add_toolbar_button(fig, tooltip, callback):
    """Add a button to `fig`'s own window toolbar - right after "Save" - if
    its backend is a Qt one. Returns whether it did."""
    if "qt" not in type(fig.canvas).__module__.lower():
        return False
    try:
        from qtpy import QtGui, QtWidgets

        toolbar = getattr(fig.canvas.manager, "toolbar", None)
        if not isinstance(toolbar, QtWidgets.QToolBar):
            return False
        QAction = getattr(QtGui, "QAction", None) or QtWidgets.QAction  # Qt6: QtGui

        def run(*_):
            # an exception escaping a Qt slot can take the whole session down
            try:
                callback()
            except Exception:
                _logger.exception("%s failed", tooltip)

        action = QAction(_checklist_icon(toolbar, QtGui), tooltip, toolbar)
        action.setToolTip(tooltip)
        action.triggered.connect(run)
        actions = toolbar.actions()
        # The toolbar ends in a stretchy coordinates label: appending would
        # put the button far right of it, so insert in front of whatever
        # follows "Save" (or, without one, in front of that label).
        anchor = getattr(toolbar, "_actions", {}).get("save_figure")
        label = getattr(toolbar, "locLabel", None)
        if anchor is not None and actions.index(anchor) + 1 < len(actions):
            before = actions[actions.index(anchor) + 1]
        else:
            before = next((a for a in actions if toolbar.widgetForAction(a) is label), None)
        if before is None:
            toolbar.addAction(action)
        else:
            toolbar.insertAction(before, action)
        return True
    except Exception:
        _logger.debug("no toolbar button for %r", tooltip, exc_info=True)
        return False


def _make_select_dialog(selector):
    """The Qt window behind the toolbar button: a scrollable list with one
    checkbox per curve (with the curve's legend handle), a filter box, and
    All/None for the rows currently shown by the filter. Ticking applies
    immediately."""
    from qtpy import QtCore, QtGui, QtWidgets

    lines = selector.lines
    dialog = QtWidgets.QDialog(selector.fig.canvas.manager.window)
    dialog.setWindowTitle("Select curves")
    layout = QtWidgets.QVBoxLayout(dialog)
    filter_box = QtWidgets.QLineEdit()
    filter_box.setPlaceholderText("filter, e.g. readback")
    filter_box.setClearButtonEnabled(True)
    layout.addWidget(filter_box)

    holder = QtWidgets.QWidget()
    rows = QtWidgets.QVBoxLayout(holder)
    boxes = []
    for line in lines:
        box = QtWidgets.QCheckBox(line.get_label())
        box.setIcon(_swatch_icon(QtGui, line.get_color()))
        box.setIconSize(QtCore.QSize(28, 12))
        box.setChecked(line.get_visible())
        rows.addWidget(box)
        boxes.append(box)
    rows.addStretch(1)
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(holder)
    layout.addWidget(scroll)

    def guarded(fn):
        def run(*_):
            try:
                fn()
            except Exception:
                _logger.exception("curve selection failed")

        return run

    def sync():
        selector.apply([box.isChecked() for box in boxes])

    def set_shown(state):
        for box in boxes:
            if not box.isHidden():  # i.e. matches the filter
                box.blockSignals(True)
                box.setChecked(state)
                box.blockSignals(False)
        sync()

    def refilter():
        needle = filter_box.text().strip().lower()
        for box in boxes:
            box.setVisible(needle in box.text().lower())

    buttons = QtWidgets.QHBoxLayout()
    for text, state in (("All", True), ("None", False)):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(guarded(lambda state=state: set_shown(state)))
        buttons.addWidget(button)
    buttons.addStretch(1)
    layout.addLayout(buttons)
    for box in boxes:
        box.toggled.connect(guarded(sync))
    filter_box.textChanged.connect(guarded(refilter))

    metrics = dialog.fontMetrics()
    advance = getattr(metrics, "horizontalAdvance", None) or metrics.width
    widest = max(advance(box.text()) for box in boxes)
    dialog.resize(min(widest + 130, 1000), min(28 * len(boxes) + 140, 760))
    return dialog


def _legend_kwargs(legend):
    """What is worth carrying over from `legend` to its replacement."""
    kwargs = {"frameon": legend.get_frame_on()}
    loc = getattr(legend, "_loc", None)  # private, but the only place it is kept
    if loc is not None:
        kwargs["loc"] = loc
    ncol = getattr(legend, "_ncols", None) or getattr(legend, "_ncol", None)
    if ncol:
        kwargs["ncol"] = ncol
    title = legend.get_title().get_text()
    if title:
        kwargs["title"] = title
    return kwargs


class _CurveSelector:
    """Show/hide a figure's curves from a Qt checkbox window (see
    `_attach_curve_selector`). Hiding a curve also drops it from the legend
    and re-fits the y-range to what is left, since the usual reason to hide
    one is that its scale dwarfs the others."""

    def __init__(self, fig, ax, lines):
        self.fig = fig
        self.ax = ax
        self.lines = list(lines)
        self._dialog = None
        # None: there is no legend to maintain. Kept apart from the live one
        # so it survives the state where every curve is hidden and it is gone.
        legend = ax.get_legend()
        self._legend_look = None if legend is None else _legend_kwargs(legend)
        fig.canvas.mpl_connect("close_event", self._close_dialog)

    def _close_dialog(self, _event=None):
        try:
            if self._dialog is not None:
                self._dialog.close()
        except RuntimeError:  # already deleted along with the figure window
            pass
        self._dialog = None

    def apply(self, states):
        """Show exactly the curves whose entry in `states` (parallel to the
        curves) is true."""
        for line, state in zip(self.lines, states):
            line.set_visible(bool(state))
        shown = [line for line in self.lines if line.get_visible()]
        legend = self.ax.get_legend()
        if legend is not None:
            self._legend_look = _legend_kwargs(legend)  # as the user left it
            legend.remove()
        if shown and self._legend_look is not None:
            # rebuilt without the hidden curves; other labelled artists (bands,
            # scatter, ...) keep their entries
            entries = [
                (handle, label)
                for handle, label in zip(*self.ax.get_legend_handles_labels())
                if getattr(handle, "get_visible", lambda: True)()
            ]
            if entries:
                self.ax.legend(*zip(*entries), **self._legend_look)
        # relim() knows lines, patches and images but not collections (scatter,
        # fill_between, ...): with those present it would shrink the range to
        # the lines alone, so leave the limits alone then
        if shown and not self.ax.collections:
            self.ax.relim(visible_only=True)
            self.ax.autoscale(enable=True, axis="y")  # zooming had switched it off
            self.ax.autoscale_view(scalex=False)
        self.fig.canvas.draw_idle()

    def open_dialog(self):
        """Show the checkbox window, creating it on first use; a closed one
        is just shown again (it is a child of the figure window, so it goes
        away with that)."""
        dialog = self._dialog
        try:
            if dialog is not None:
                dialog.isVisible()  # raises RuntimeError once the window is gone
        except RuntimeError:
            dialog = None
        if dialog is None:
            dialog = self._dialog = _make_select_dialog(self)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()


def _attach_curve_selector(fig, ax, lines):
    """Give `fig` a "select curves" button in its Qt window toolbar, which
    opens a checkbox window to show/hide `lines`. Does nothing for a single
    curve, or on backends without a Qt toolbar (inline, ipympl, non-GUI)."""
    if len(lines) < 2:
        return None
    selector = _CurveSelector(fig, ax, lines)
    if not _add_toolbar_button(fig, "Select curves to show", selector.open_dialog):
        return None
    fig._eco_curve_selector = selector
    return selector
```

The call site in eco's `_plot_dataframe`, after the legend and `tight_layout()`:

```python
    ah.figure.tight_layout()
    _attach_curve_selector(fig, ah, lines)   # <- the only eco-side hook
    _show_figure(ah.figure)
```

## Appendix B: headless behaviour check

Passes against the prototype. Save as `check_select_curves.py`.

```python
"""Headless behaviour check for the "select curves" toolbar button.

Run:  QT_QPA_PLATFORM=offscreen python3 check_select_curves.py
Exits non-zero on the first failed assertion.
"""
import sys

import matplotlib
import matplotlib.cbook as cbook
import numpy as np
from qtpy import QtGui, QtWidgets

# Offscreen Qt needs no display, but matplotlib insists on one before it will
# use a Qt backend or create the QApplication itself: bypass both checks, and
# keep a reference to the app (it is destroyed the moment it is garbage-collected).
cbook._get_running_interactive_framework = lambda: None
matplotlib.use("QtAgg")
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import matplotlib.pyplot as plt  # noqa: E402

# --- adapter: the only part that differs between the eco prototype and escape ---
try:
    from escape.plot_utilities import attach_select_button  # target API

    def attach(fig):
        attach_select_button(fig)

    def get_dialog(fig):
        return getattr(fig.axes[0], "_escape_select_gui", None)

except ImportError:  # the eco prototype this task was derived from
    from eco.dbase import archiver as _proto

    def attach(fig):
        ax = fig.axes[0]
        _proto._attach_curve_selector(fig, ax, ax.get_lines())

    def get_dialog(fig):
        return fig._eco_curve_selector._dialog

TIP = "Select curves to show"


def make_figure(n_lines=9, legend=True):
    fig, ax = plt.subplots()
    x = np.arange(200.0)
    for i in range(n_lines):
        kind = ("direction", "offset", "readback")[i % 3]
        scale = 100.0 if i == 5 else 1.0  # one curve whose scale dwarfs the rest
        ax.plot(x, scale * (np.sin(x / 20 + i) + i), ".-", label=f"stage.{i // 3}.{kind}")
    if legend:
        ax.legend()
    return fig, ax


def toolbar_of(fig):
    return fig.canvas.manager.toolbar


def select_action(fig):
    return next((a for a in toolbar_of(fig).actions() if a.toolTip() == TIP), None)


def pump():
    APP.processEvents()


def open_dialog(fig):
    select_action(fig).trigger()
    pump()
    dialog = get_dialog(fig)
    assert dialog is not None and dialog.isVisible(), "dialog did not open"
    return dialog


def widgets_of(dialog):
    boxes = dialog.findChildren(QtWidgets.QCheckBox)
    edit = dialog.findChild(QtWidgets.QLineEdit)
    buttons = {b.text(): b for b in dialog.findChildren(QtWidgets.QPushButton)}
    return boxes, edit, buttons


def ylim_span(ax):
    lo, hi = ax.get_ylim()
    return hi - lo


# 1. placement: directly after Save, in front of the stretchy coordinates label
fig, ax = make_figure()
attach(fig)
tb = toolbar_of(fig)
acts = tb.actions()
save = tb._actions["save_figure"]
assert select_action(fig) is not None, "no toolbar action"
assert acts.index(select_action(fig)) == acts.index(save) + 1, "not right after Save"
assert tb.widgetForAction(acts[acts.index(select_action(fig)) + 1]) is tb.locLabel
assert not select_action(fig).icon().isNull(), "button has no icon"

# 2. dialog: one checked box per line, child of the figure window, colour swatches
dialog = open_dialog(fig)
boxes, edit, buttons = widgets_of(dialog)
assert len(boxes) == 9 and all(b.isChecked() for b in boxes)
assert not boxes[0].icon().isNull(), "rows should carry the line's legend handle"
w = dialog
while w is not None and w is not fig.canvas.manager.window:
    w = w.parent()
assert w is not None, "dialog is not a child of the figure window"
assert len(ax.get_legend().get_texts()) == 9

# 3. unticking: line hidden, legend entry gone, y-range refit to what is left
before = ylim_span(ax)
big = next(b for b in boxes if b.text() == "stage.1.readback")  # the x100 curve (i=5)
big.setChecked(False)
pump()
assert not ax.get_lines()[5].get_visible()
assert len(ax.get_legend().get_texts()) == 8
assert ylim_span(ax) < before / 10, "y-range was not refit after hiding the big curve"
big.setChecked(True)
pump()
assert len(ax.get_legend().get_texts()) == 9

# 4. a manual zoom switches y-autoscale off; hiding a curve must still refit
ax.set_ylim(-1, 1)
assert not ax.get_autoscaley_on()
boxes[0].setChecked(False)
pump()
assert ylim_span(ax) > 2.5, "y-range stayed frozen after a manual zoom"
boxes[0].setChecked(True)
pump()

# 5. filter + All/None act on the rows the filter shows
edit.setText("readback")
pump()
shown = [b for b in boxes if not b.isHidden()]
assert [b.text() for b in shown] == [b.text() for b in boxes if "readback" in b.text()] and len(shown) == 3
buttons["None"].click()
pump()
vis = [ln.get_visible() for ln in ax.get_lines()]
assert [v for v, b in zip(vis, boxes) if "readback" in b.text()] == [False] * 3
assert all(v for v, b in zip(vis, boxes) if "readback" not in b.text()), "None touched filtered-out rows"
buttons["All"].click()
pump()
assert all(ln.get_visible() for ln in ax.get_lines())
edit.setText("")
pump()
assert all(not b.isHidden() for b in boxes)

# 6. None without a filter empties the plot (and drops the legend); All restores it
buttons["None"].click()
pump()
assert not any(ln.get_visible() for ln in ax.get_lines()) and ax.get_legend() is None
buttons["All"].click()
pump()
assert len(ax.get_legend().get_texts()) == 9
# "only the readbacks": None, then filter + All
buttons["None"].click()
edit.setText("readback")
buttons["All"].click()
edit.setText("")
pump()
assert [t.get_text() for t in ax.get_legend().get_texts()] == [
    ln.get_label() for ln in ax.get_lines() if "readback" in ln.get_label()
]
buttons["All"].click()
pump()

# 7. clicking the button again reuses the same dialog
dialog.close()
pump()
select_action(fig).trigger()
pump()
assert get_dialog(fig) is dialog and dialog.isVisible()

# 8. closing the figure takes the dialog with it
plt.close(fig)
pump()
try:
    assert not dialog.isVisible()
except RuntimeError:
    pass  # C++ object already deleted: also fine

# 9. a figure without a legend keeps none (the prototype only has legend-ful plots)
fig, ax = make_figure(legend=False)
attach(fig)
dialog = open_dialog(fig)
boxes, edit, buttons = widgets_of(dialog)
boxes[0].setChecked(False)
pump()
assert ax.get_legend() is None, "a legend must not appear where there was none"
plt.close(fig)

# 9b. an existing legend keeps its position, title and column count
fig, ax = make_figure()
ax.legend(loc="lower left", title="stages", ncol=2)
loc = ax.get_legend()._loc  # private, but the only handle on where it sits
attach(fig)
dialog = open_dialog(fig)
boxes, edit, buttons = widgets_of(dialog)
boxes[0].setChecked(False)
pump()
legend = ax.get_legend()
assert legend._loc == loc and legend.get_title().get_text() == "stages"
assert len(legend.get_texts()) == 8
plt.close(fig)

# 9c. other labelled artists (a band) keep their legend entry; no exception with collections
fig, ax = make_figure()
ax.fill_between(np.arange(200.0), -1, 1, alpha=0.2, label="band")
ax.legend()
assert len(ax.get_legend().get_texts()) == 10
attach(fig)
dialog = open_dialog(fig)
boxes, edit, buttons = widgets_of(dialog)
assert len(boxes) == 9, "only the lines are selectable"
boxes[0].setChecked(False)
pump()
assert len(ax.get_legend().get_texts()) == 9 and "band" in [t.get_text() for t in ax.get_legend().get_texts()]
plt.close(fig)

# 10. one curve: nothing to select, so no button
fig, ax = make_figure(n_lines=1)
attach(fig)
assert select_action(fig) is None
plt.close(fig)

# 11. icon follows the toolbar's own colour (dark themes)
fig, ax = make_figure()
tb = toolbar_of(fig)
palette = QtGui.QPalette()
palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(240, 240, 240))
palette.setColor(QtGui.QPalette.Button, QtGui.QColor(40, 40, 40))
tb.setPalette(palette)
attach(fig)
img = select_action(fig).icon().pixmap(24, 24).toImage()
opaque = [img.pixelColor(x, y) for x in range(24) for y in range(24) if img.pixelColor(x, y).alpha() > 200]
assert opaque and all(c.red() > 200 for c in opaque), "icon is not drawn in the toolbar's text colour"
plt.close(fig)

print("all checks passed")
sys.exit(0)
```
