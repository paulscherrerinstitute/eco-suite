"""
ipywidgets tweak panel, the notebook/lab/voila counterpart of
eco.widgets.tweak_panel_qt: same rows, buttons and key bindings (logic in
eco.widgets.tweak_panel).

Keypress control uses ipyevents on the box that shows the key manual: keys act
while the mouse pointer is over that box (ipyevents' rule), so typing anywhere
else in the notebook never moves anything. Without ipyevents installed the
panel still works by buttons, and says so.
"""

import threading

import ipywidgets as widgets

from eco.widgets.tweak_panel import _fmt

_ARROW_KEYS = {
    "ArrowLeft": "Left",
    "ArrowUp": "Up",
    "ArrowDown": "Down",
    "ArrowRight": "Right",
}


def normalize_key_event(event):
    """ipyevents keydown dict -> eco.widgets.tweak_panel normalized key."""
    key = event.get("key", "")
    if key in _ARROW_KEYS:
        name = _ARROW_KEYS[key]
        return "Ctrl+" + name if event.get("ctrlKey") else name
    if key == "Escape":
        return "Escape"
    if len(key) == 1:
        return key.lower()
    return None


def _manual_html(rows):
    body = "".join(
        f"<tr><td style='padding-right:12px'><b><code>{keys}</code></b></td><td>{meaning}</td></tr>"
        for keys, meaning in rows
    )
    return (
        "<table>" + body + "</table>"
        "<i>keys act while the mouse pointer is over this box</i>"
    )


def render_png(recorder):
    """The recorder's plot as PNG bytes (Agg, no pyplot: safe off the main
    thread)."""
    import io

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from eco.widgets.tweak_recorder import draw_tweak_plot, figure_size

    fig = Figure(figsize=figure_size(recorder), dpi=90)
    FigureCanvasAgg(fig)
    draw_tweak_plot(fig, recorder)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


class TweakPanelIpy:
    def __init__(
        self, controller, title=None, poll_interval=0.3, keypress=True, recorder=None
    ):
        self.controller = controller
        self.recorder = recorder
        self._stop_event = threading.Event()
        self.status = widgets.HTML("")
        controller.notify = self._message

        key_of = {action: key for key, action in controller.bindings.items()}
        btn_layout = widgets.Layout(width="40px")
        num_layout = widgets.Layout(width="100px")

        self._value_labels = []
        self._step_inputs = []
        self._go_inputs = []
        self._reset_inputs = []
        rows = [
            widgets.HTML(
                f"<b>{title or 'tweak ' + ', '.join(controller.names)}</b>"
            )
        ]
        for i, axis in enumerate(controller.axes):
            value_label = widgets.HTML("…", layout=widgets.Layout(width="110px"))
            self._value_labels.append(value_label)

            buttons = []
            for text, tip, action in (
                ("◀", "neg dir", ("move", i, -1)),
                ("×2", "step*2", ("step", i, 2.0)),
                ("÷2", "step/2", ("step", i, 0.5)),
                ("▶", "pos dir", ("move", i, +1)),
            ):
                key = key_of.get(action)
                btn = widgets.Button(
                    description=text,
                    tooltip=f"{tip} (key: {key})" if key else tip,
                    layout=btn_layout,
                )
                btn.on_click(lambda _b, a=action: self._do(a))
                buttons.append(btn)

            step_input = widgets.FloatText(
                value=axis.step, layout=num_layout, continuous_update=False
            )
            step_input.observe(
                lambda change, i=i: self._set_step(i, change["new"]), names="value"
            )
            self._step_inputs.append(step_input)
            go_input = widgets.FloatText(layout=num_layout)
            go_btn = widgets.Button(description="Go", layout=widgets.Layout(width="45px"))
            go_btn.on_click(
                lambda _b, i=i: self.controller.go(i, self._go_inputs[i].value)
            )
            self._go_inputs.append(go_input)
            reset_input = widgets.FloatText(layout=num_layout)
            reset_btn = widgets.Button(
                description="Set",
                tooltip="redefine the current position as this value",
                layout=widgets.Layout(width="45px"),
            )
            reset_btn.on_click(
                lambda _b, i=i: self.controller.reset_current_value_to(
                    i, self._reset_inputs[i].value
                )
            )
            self._reset_inputs.append(reset_input)

            rows.append(
                widgets.HBox(
                    [
                        widgets.HTML(f"<b>{axis.name}</b>", layout=widgets.Layout(width="160px")),
                        value_label,
                        *buttons,
                        widgets.HTML("&nbsp;step"),
                        step_input,
                        widgets.HTML("&nbsp;go to"),
                        go_input,
                        go_btn,
                        widgets.HTML("&nbsp;reset to"),
                        reset_input,
                        reset_btn,
                    ]
                )
            )

        start_btn = widgets.Button(
            description="Back to start",
            tooltip="all axes back to "
            + ", ".join(_fmt(a.start_value) for a in controller.axes)
            + " (key: s)",
        )
        start_btn.on_click(lambda _b: self.controller.back_to_start())
        stop_btn = widgets.Button(
            description="Stop", button_style="danger", tooltip="stop all axes (key: Esc)"
        )
        stop_btn.on_click(lambda _b: self.controller.stop())
        close_btn = widgets.Button(description="Close")
        close_btn.on_click(lambda _b: self.close())
        self.keypress_box = widgets.Checkbox(
            value=keypress, description="keypress control", indent=False
        )
        rows.append(widgets.HBox([start_btn, stop_btn, self.keypress_box, close_btn]))

        self.manual = widgets.HTML(_manual_html(controller.manual()))
        self.key_area = widgets.Box(
            [self.manual],
            layout=widgets.Layout(
                border="2px solid #2e9b45", padding="4px 8px", width="fit-content"
            ),
        )
        self._key_event = None
        try:
            from ipyevents import Event

            self._key_event = Event(
                source=self.key_area,
                watched_events=["keydown"],
                prevent_default_action=True,
            )
            self._key_event.on_dom_event(self._on_key)
        except Exception:
            self.manual.value += (
                "<br><b>keypress control needs the ipyevents package, "
                "which is not available - use the buttons</b>"
            )
        self.keypress_box.observe(
            lambda change: self._keypress_toggled(change["new"]), names="value"
        )
        rows += [self.key_area, self.status]
        self.plot = None
        self._plotted_version = None
        if recorder is not None:
            recorder.start()
            self.plot = widgets.Image(format="png")
            self.ui = widgets.HBox([widgets.VBox(rows), self.plot])
        else:
            self.ui = widgets.VBox(rows)
        self._keypress_toggled(keypress)

        self._poll_interval = poll_interval
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _do(self, action):
        kind, i, arg = action
        if kind == "move":
            self.controller.move(i, arg)
        elif self.controller.scale_step(i, arg):
            self._show_step(i)

    def _show_step(self, i):
        step_input = self._step_inputs[i]
        step = self.controller.axes[i].step
        if step_input.value != step:
            step_input.value = step  # observer: set_step with the same value

    def _set_step(self, i, value):
        if value != self.controller.axes[i].step and value:
            self.controller.set_step(i, value)

    def _keypress_toggled(self, on):
        self.key_area.layout.display = None if on else "none"

    def _on_key(self, event):
        if not self.keypress_box.value:
            return
        key = normalize_key_event(event)
        action = self.controller.handle_key(key) if key else None
        if action is None:
            return
        kind = action[0]
        if kind == "step":
            self._show_step(action[1])
        elif kind == "release":
            self.keypress_box.value = False
        elif kind in ("focus_go", "focus_reset"):
            inputs = self._go_inputs if kind == "focus_go" else self._reset_inputs
            try:
                inputs[action[1]].focus()
            except Exception:
                pass

    def _poll_loop(self):
        first = True
        while not self._stop_event.is_set():
            for i, axis in enumerate(self.controller.axes):
                try:
                    value = axis.get_value()
                    text = _fmt(value)
                except Exception as exc:
                    value, text = None, f"error: {exc}"
                if self._value_labels[i].value != text:
                    self._value_labels[i].value = text
                if first and value is not None:
                    try:
                        self._go_inputs[i].value = value
                        self._reset_inputs[i].value = value
                    except Exception:
                        pass
            first = False
            self._refresh_plot()
            self._stop_event.wait(self._poll_interval)

    def _refresh_plot(self):
        rec = self.recorder
        if rec is None or self._plotted_version == rec.version:
            return
        self._plotted_version = rec.version
        try:
            self.plot.value = render_png(rec)
        except Exception as exc:
            self._message(f"plot failed: {exc}")

    def _message(self, text):
        self.status.value = f"<b>{text}</b>"

    def close(self):
        self._stop_event.set()
        if self.recorder is not None:
            self.recorder.stop()
        if self._key_event is not None:
            try:
                self._key_event.close()
            except Exception:
                pass
        self.ui.close()

    def _ipython_display_(self):
        from IPython.display import display

        display(self.ui)


def tweak_panel_ipy(
    controller, title=None, poll_interval=0.3, keypress=True, display=False, recorder=None
):
    """display=False returns the panel for the caller/cell to show."""
    panel = TweakPanelIpy(
        controller,
        title=title,
        poll_interval=poll_interval,
        keypress=keypress,
        recorder=recorder,
    )
    if display:
        from IPython.display import display as _display

        _display(panel.ui)
    return panel
