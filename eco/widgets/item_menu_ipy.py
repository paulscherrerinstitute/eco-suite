"""
The per-item "⋯" menu of the ipywidgets property grid (eco.widgets.
display_widget), for JupyterLab and Voila. What it offers and why: see
eco.widgets.item_menu; the Qt counterpart is eco.widgets.item_menu_qt.

ipywidgets has no popup menu, so the button is a narrow Dropdown showing
"⋯" (the browser's own list shows the full labels); choosing an entry runs
it and resets the dropdown. What it opens -- a view, the live strip chart,
the history plot, an indicator, details -- appears as a closable panel right
below that row, in the same page (so it also works in Voila, where no
separate window can appear). Closing a panel stops whatever it reads.

Plots are rendered with matplotlib's Agg backend into a plain Image widget:
no extra widget library, independent of the notebook's matplotlib backend,
and safe to refresh from a background thread (widget values may be set from
any thread, see display_widget).
"""
import html
import io
import threading

import ipywidgets as widgets

from eco.widgets import item_menu
from eco.widgets.live_values import LiveReader

STRIP_REDRAW_INTERVAL = 1.0  # s

# matplotlib is not thread-safe (shared font cache, text layout): renders
# from the charts' threads and the kernel thread take turns
_render_lock = threading.Lock()


def _render_png(draw, width=6.0, height=2.6, dpi=80):
    """PNG bytes of a fresh Figure that `draw(figure)` filled."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    with _render_lock:
        figure = Figure(figsize=(width, height), dpi=dpi, layout="constrained")
        FigureCanvasAgg(figure)
        draw(figure)
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png")
    return buffer.getvalue()


def _image():
    return widgets.Image(format="png", layout=widgets.Layout(max_width="100%", height="auto"))


class StripChartIpy(widgets.VBox):
    """A rolling time plot of one item's value: monitored where the item
    can push changes, polled every STRIP_POLL_INTERVAL otherwise, redrawn
    every STRIP_REDRAW_INTERVAL. `.stop()` ends both."""

    def __init__(self, item, title=None):
        self.item = item
        self.title = title or item_menu.full_name(item)
        self.buffer = item_menu.SeriesBuffer(item)
        self._windows = dict(item_menu.STRIP_WINDOWS)
        self.window_dd = widgets.Dropdown(
            options=[label for label, _ in item_menu.STRIP_WINDOWS],
            value=item_menu.DEFAULT_STRIP_WINDOW,
            description="window",
            layout=widgets.Layout(width="170px"),
        )
        self.pause = widgets.ToggleButton(description="pause", layout=widgets.Layout(width="70px"))
        clear = widgets.Button(description="clear", layout=widgets.Layout(width="60px"))
        clear.on_click(lambda b: (self.buffer.clear(), self.redraw()))
        self.window_dd.observe(lambda change: self.redraw(), names="value")
        self.value_label = widgets.Label("--")
        self.image = _image()
        super().__init__(
            [widgets.HBox([self.window_dd, self.pause, clear, self.value_label]), self.image]
        )

        self._stop = threading.Event()
        self._reader = LiveReader(self._on_result, poll_interval=item_menu.STRIP_POLL_INTERVAL, workers=1)
        self._reader.add(item)
        self._reader.start()
        threading.Thread(target=self._redraw_loop, daemon=True).start()

    def _on_result(self, index, ok, payload):
        if ok:
            self.buffer.add(payload)

    def redraw(self):
        window = self._windows.get(self.window_dd.value, 60)
        self.image.value = _render_png(
            lambda figure: item_menu.draw_strip(figure.add_subplot(), self.buffer, window)
        )
        self.value_label.value = self.buffer.last_text

    def _redraw_loop(self):
        while not self._stop.wait(STRIP_REDRAW_INTERVAL):
            if self.pause.value:
                continue
            try:
                self.redraw()
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        self._reader.stop()


class HistoryPanelIpy(widgets.VBox):
    """The archived history of one item over a chosen time range, fetched
    in a background thread (eco.widgets.item_menu.fetch_history)."""

    def __init__(self, item, title=None):
        self.item = item
        self.title = title or item_menu.full_name(item)
        self._ranges = dict(item_menu.HISTORY_RANGES)
        self._generation = 0
        self.range_dd = widgets.Dropdown(
            options=[label for label, _ in item_menu.HISTORY_RANGES],
            value=item_menu.DEFAULT_HISTORY_RANGE,
            description="last",
            layout=widgets.Layout(width="170px"),
        )
        reload_btn = widgets.Button(description="reload", layout=widgets.Layout(width="70px"))
        reload_btn.on_click(lambda b: self.load())
        self.range_dd.observe(lambda change: self.load(), names="value")
        self.status = widgets.Label("")
        self.image = _image()
        super().__init__([widgets.HBox([self.range_dd, reload_btn, self.status]), self.image])
        self.load()

    def load(self):
        self._generation += 1
        generation = self._generation
        label = self.range_dd.value
        seconds = self._ranges.get(label, 3600)
        self.status.value = f"loading the last {label} from the archiver…"

        def run():
            try:
                data = item_menu.fetch_history(self.item, seconds)
                if generation != self._generation:
                    return
                if data is None or data.empty:
                    self.status.value = f"no archived data in the last {label}"
                    return
                drawn = {}

                def draw(figure):
                    ax = figure.add_subplot()
                    drawn["ok"] = item_menu.plot_history(ax, data)
                    ax.set_title(self.title, fontsize="small", loc="left")

                png = _render_png(draw, height=3.2)
                if generation != self._generation:
                    return
                if drawn.get("ok"):
                    self.image.value = png
                    self.status.value = f"last {label}, {len(data)} rows"
                else:
                    self.status.value = "archived data is not numeric -- nothing to plot"
            except Exception as exc:
                if generation == self._generation:
                    self.status.value = f"failed: {type(exc).__name__}: {exc}"

        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self._generation += 1  # ignore a fetch still in flight


def _details(item):
    out = widgets.HTML("<i>reading…</i>")

    def run():
        try:
            text = item_menu.details_text(item_menu.describe_item(item))
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
        out.value = f'<pre style="margin:0; white-space:pre-wrap">{html.escape(text)}</pre>'

    threading.Thread(target=run, daemon=True).start()
    return out


def _strip_plot_window(item):
    status = widgets.Label("opening the strip plot window…")

    def run():
        try:
            item.strip_plot()
            status.value = "opened in its own window (on this kernel's display)"
        except Exception as exc:
            status.value = f"failed: {type(exc).__name__}: {exc}"

    threading.Thread(target=run, daemon=True).start()
    return status


def _open_view(item, method_name):
    result = item_menu.call_widget_method(item, method_name, "ipy")
    if isinstance(result, widgets.Widget):
        return result
    return widgets.Label(f"{method_name}() opened {type(result).__name__}, not a notebook view")


def actions_for(item, name):
    """[(label, key, build)] -- `build()` returns the widget to show below
    the row (stopped with ``.stop()`` when its panel is closed)."""
    actions = []
    for label, method_name in item_menu.widget_methods(item):
        actions.append((f"Open: {label}", f"open:{method_name}", lambda m=method_name: _open_view(item, m)))
    if item_menu.is_readable(item):
        actions.append(("Live strip chart", "strip", lambda: StripChartIpy(item, title=name)))
    if item_menu.has_strip_plot(item) and item_menu.can_open_windows():
        actions.append(("Strip plot window (archiver)", "strip_window", lambda: _strip_plot_window(item)))
    if item_menu.has_history(item):
        actions.append(("History (archiver)", "history", lambda: HistoryPanelIpy(item, title=name)))
    if item_menu.is_readable(item):
        try:
            from eco.widgets import indicator_widgets_ipy
        except Exception:
            indicator_widgets_ipy = None
        if indicator_widgets_ipy is not None:
            for kind, _factory, _needs_range in indicator_widgets_ipy.INDICATOR_TYPES:
                actions.append(
                    (
                        f"Indicator: {kind}",
                        f"indicator:{kind}",
                        lambda k=kind: indicator_widgets_ipy.create_indicator(k, item, title=name),
                    )
                )
    if item_menu.has_memory(item):

        def memories():
            from eco.widgets.memory_widget import make_memory_browser_ipywidgets

            return make_memory_browser_ipywidgets(item)

        actions.append(("Memories", "memories", memories))
    actions.append(("Details", "details", lambda: _details(item)))
    return actions


def _stop_widget(widget):
    stop = getattr(widget, "stop", None)
    if callable(stop):
        try:
            stop()
        except Exception:
            pass


class ItemPanels(widgets.VBox):
    """Where a row's menu shows what it opened: one closable panel each,
    below the row (a full-width child of the row's wrapping flex box)."""

    def __init__(self):
        super().__init__(layout=widgets.Layout(width="100%", display="none", margin="2px 0 6px 24px"))

    def add(self, title, body):
        close = widgets.Button(
            description="✕", tooltip="close", layout=widgets.Layout(width="32px", height="24px")
        )
        header = widgets.HBox(
            [widgets.HTML(f"<b>{html.escape(title)}</b>"), close],
            layout=widgets.Layout(justify_content="space-between", align_items="center"),
        )
        panel = widgets.VBox([header, body], layout=widgets.Layout(border="1px solid #ccc", padding="4px", margin="2px 0"))
        panel._eco_body = body

        def on_close(b):
            self.remove(panel)

        close.on_click(on_close)
        self.children = self.children + (panel,)
        self.layout.display = None
        return panel

    def remove(self, panel):
        _stop_widget(panel._eco_body)
        self.children = tuple(c for c in self.children if c is not panel)
        if not self.children:
            self.layout.display = "none"

    def stop(self):
        for panel in self.children:
            _stop_widget(panel._eco_body)
        self.children = ()
        self.layout.display = "none"


def make_menu(item, name, panels):
    """The "⋯" dropdown for `item`'s row; what it opens goes into `panels`
    (an ItemPanels)."""
    actions = actions_for(item, name)
    builders = {key: (label, build) for label, key, build in actions}
    menu = widgets.Dropdown(
        options=[("⋯", "")] + [(label, key) for label, key, _ in actions],
        value="",
        layout=widgets.Layout(width="48px", min_width="48px", flex="0 0 auto"),
        tooltip="More for this item: views, strip chart, history, indicators, details",
    )
    resetting = [False]

    def on_choice(change):
        if resetting[0] or not change["new"]:
            return
        label, build = builders[change["new"]]
        resetting[0] = True
        try:
            menu.value = ""
        finally:
            resetting[0] = False
        try:
            body = build()
        except Exception as exc:
            body = widgets.Label(f"failed: {type(exc).__name__}: {exc}")
        panels.add(f"{name} -- {label}", body)

    menu.observe(on_choice, names="value")
    return menu
