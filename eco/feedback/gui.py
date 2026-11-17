"""Control panel (ipywidgets) with dedicated Settings / Plots / Matrix / Logs
/ System / Help panels, mirroring a DOOCS/JDDD-style feedback GUI.

In a notebook / JupyterLab / Voila, against a server::

    from eco.feedback.gui import FeedbackPanel
    FeedbackPanel("http://127.0.0.1:8010", api_key="...").widget

or in-process on a manager of this session (no server needed)::

    fb.panel()          # == FeedbackPanel(client=fb.service).widget

The panel refreshes from a background thread, so nothing here may go
through pyplot or ``with output:`` (neither works off the kernel's main
thread; the first creates Qt objects in an eco session): plots are rendered
with matplotlib's Agg canvas into ``Image`` widgets, text by assigning
``Output.outputs``.
"""

from __future__ import annotations

import atexit
import io
import threading
import time
import weakref

import ipywidgets as w
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from .client import FeedbackClient


_PANELS = weakref.WeakSet()


@atexit.register
def _stop_panels():
    """Stop refresh threads before the interpreter finalizes: a daemon
    thread still rendering a figure (C code in matplotlib/PIL) during
    finalization segfaults the process at exit."""
    for panel in list(_PANELS):
        panel.stop(join=True)


def _figure(figsize):
    fig = Figure(figsize=figsize)
    FigureCanvasAgg(fig)
    return fig, fig.add_subplot(1, 1, 1)


def _png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=90)
    return buf.getvalue()


def _show_text(output, text):
    """Replace an Output widget's content; safe from any thread."""
    output.outputs = ({"output_type": "stream", "name": "stdout", "text": text + "\n"},)


def _panel_header(url, anchor, title):
    if not url:
        return w.HTML(f"<b style='font-size:1.1em'>{title}</b>")
    return w.HTML(
        "<div style='display:flex;justify-content:space-between;"
        f"align-items:baseline'><b style='font-size:1.1em'>{title}</b>"
        f"<a href='{url}/docs/gui#{anchor}' target='_blank' "
        "style='font-size:0.85em'>\U0001F4D6 docs ↗</a></div>"
    )


def _help_widget(url):
    if not url:
        return w.HTML(
            "<h3>eco feedback control panel (in-process)</h3>"
            "<p>This panel drives a FeedbackManager of this session directly. "
            "<b>Settings</b> toggles and tunes each loop, <b>Plots</b> shows "
            "value/output trends, <b>Matrix</b> measures response matrices and "
            "manages correctors built from them, <b>Logs</b> shows events, "
            "<b>System</b> covers interlocks, alarms, health and config "
            "save/load.</p><p>Guides: <code>eco/feedback/docs/GUI.md</code>, "
            "<code>eco/feedback/docs/API.md</code>, <code>eco/feedback/README.md</code>.</p>")
    return w.HTML(
        "<h3>eco feedback control panel</h3>"
        "<p><b>Settings</b> toggles and tunes each loop. <b>Plots</b> shows "
        "live value/output trends. <b>Matrix</b> measures response matrices "
        "and manages closed-loop correctors built from them. <b>Logs</b> "
        "shows recent events. <b>System</b> covers interlocks, alarms, "
        "server health, and config save/load.</p>"
        "<ul>"
        f"<li><a href='{url}/docs/gui' target='_blank'>GUI guide</a></li>"
        f"<li><a href='{url}/docs/api' target='_blank'>API guide</a></li>"
        f"<li><a href='{url}/docs' target='_blank'>API reference (Swagger)</a></li>"
        "</ul>"
    )


class LoopTab:
    def __init__(self, client: FeedbackClient, name: str, status: dict):
        self.client, self.name = client, name

        self.enabled = w.ToggleButton(description="enabled",
                                      value=status["enabled"])
        self.setpoint = w.FloatText(description="setpoint",
                                    value=status["setpoint"])
        self.rate = w.BoundedFloatText(description="rate [Hz]", min=0.1,
                                       max=10000, value=status["rate_hz"])
        self.kp = w.FloatText(description="kp", value=status["kp"])
        self.ki = w.FloatText(description="ki", value=status["ki"])
        self.kd = w.FloatText(description="kd", value=status["kd"])
        self.readback = w.HTML()

        self.enabled.observe(lambda c: self._send(enabled=c.new), "value")
        self.setpoint.observe(lambda c: self._send(setpoint=c.new), "value")
        self.rate.observe(lambda c: self._send(rate_hz=c.new), "value")
        self.kp.observe(lambda c: self._send(kp=c.new), "value")
        self.ki.observe(lambda c: self._send(ki=c.new), "value")
        self.kd.observe(lambda c: self._send(kd=c.new), "value")

        self.widget = w.VBox([
            w.HBox([self.enabled, self.readback]),
            w.HBox([self.setpoint, self.rate]),
            w.HBox([self.kp, self.ki, self.kd]),
        ])

    def _send(self, **settings):
        try:
            self.client.set(self.name, **settings)
        except Exception:
            pass

    def refresh(self):
        s = self.client.status(self.name)
        v, r = s["last_value"], s["actual_rate_hz"]
        flags = []
        if s.get("held"):
            flags.append("HELD")
        if s.get("alarmed"):
            flags.append("ALARM")
        if s.get("error"):
            flags.append("ERROR")
        flag_str = f" <b style='color:#c00'>[{', '.join(flags)}]</b>" if flags else ""
        names = (f"<br><small>{s.get('detector_name')} &rarr; "
                 f"{s.get('adjustable_name')}</small>" if s.get("detector_name") else "")
        err = (f"<br><small style='color:#c00'>{s['error']}</small>"
               if s.get("error") else "")
        self.readback.value = (
            (f"&nbsp; value: <b>{v:.4g}</b> &nbsp; ({r:.1f} Hz){flag_str}"
             if v is not None else f"&nbsp; no data{flag_str}") + names + err
        )


class LoopPlot:
    """Redraws a matplotlib trend plot for one loop into an Output widget."""

    def __init__(self, name: str):
        self.name = name
        self.output = w.Image(format="png")

    def update(self, hist: dict, setpoint: float):
        fig, ax = _figure((6, 2.5))
        t0 = hist["t"][-1] if hist["t"] else 0.0
        hist = dict(hist, t=[t - t0 for t in hist["t"]])
        ax.plot(hist["t"], hist["value"], lw=1, label="value")
        ax.axhline(setpoint, ls="--", lw=1, color="grey", label="setpoint")
        outputs = [o for o in hist["output"] if o is not None]
        if outputs:
            ax2 = ax.twinx()
            ax2.plot(hist["t"], hist["output"], lw=1, color="tab:orange",
                     alpha=0.7, label="output")
            ax2.set_ylabel("output")
        ax.set_xlabel("time relative to now [s]")
        ax.set_ylabel("value")
        ax.set_title(self.name)
        ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()
        self.output.value = _png(fig)


class MatrixLoopTab:
    """One closed-loop matrix corrector: enable, gain, rcond, per-detector
    weight, reference/error readback, save-reference button."""

    def __init__(self, client: FeedbackClient, name: str, status: dict):
        self.client, self.name = client, name

        self.enabled = w.ToggleButton(description="enabled", value=status["enabled"])
        self.gain = w.FloatText(description="gain", value=status["gain"])
        self.rcond = w.FloatText(description="rcond", value=status["rcond"],
                                 layout=w.Layout(width="160px"))
        self.save_ref_btn = w.Button(description="Save reference")
        self.save_ref_btn.on_click(self._on_save_reference)
        self.readback = w.HTML()
        self.weight_boxes = {
            n: w.FloatText(description=n, value=status["weights"].get(n, 1.0),
                          layout=w.Layout(width="160px"))
            for n in status["detector_names"]
        }

        self.enabled.observe(lambda c: self._send(enabled=c.new), "value")
        self.gain.observe(lambda c: self._send(gain=c.new), "value")
        self.rcond.observe(lambda c: self._send(rcond=c.new), "value")
        for det_name, box in self.weight_boxes.items():
            box.observe(lambda c, det_name=det_name: self._send(weights={det_name: c.new}), "value")

        self.widget = w.VBox([
            w.HBox([self.enabled, self.readback]),
            w.HBox([self.gain, self.rcond, self.save_ref_btn]),
            w.HTML("<i>per-detector weight (0 masks it out):</i>"),
            w.HBox(list(self.weight_boxes.values())),
        ])

    def _send(self, **settings):
        try:
            self.client.set_matrix_loop(self.name, **settings)
        except Exception:
            pass

    def _on_save_reference(self, _btn):
        try:
            self.client.save_reference(self.name)
        except Exception:
            pass

    def refresh(self):
        try:
            s = self.client.matrix_loop_status(self.name)
        except Exception:
            return
        flags = []
        if s["held"]:
            flags.append("HELD")
        if s["alarmed"]:
            flags.append("ALARM")
        flag_str = f" <b style='color:#c00'>[{', '.join(flags)}]</b>" if flags else ""
        if s["last_values"]:
            errors = ", ".join(
                f"{n}={v - s['reference'].get(n, 0.0):+.3g}"
                for n, v in zip(s["detector_names"], s["last_values"])
            )
            self.readback.value = f"&nbsp; error: {errors}{flag_str}"
        else:
            self.readback.value = f"&nbsp; no data{flag_str}"


class MatrixPanel:
    """Picks actuators/detectors, triggers a response-matrix measurement on
    the server, displays progress/result, and manages closed-loop matrix
    correctors built from a completed measurement."""

    def __init__(self, client: FeedbackClient):
        self.client = client
        self._current_name = None
        self._shown_name = None
        self.matrix_loop_tabs: dict[str, MatrixLoopTab] = {}

        self.adj_select = w.SelectMultiple(options=[], description="actuators",
                                           layout=w.Layout(width="45%"))
        self.det_select = w.SelectMultiple(options=[], description="detectors",
                                           layout=w.Layout(width="45%"))
        self.step = w.FloatText(value=0.1, description="step")
        self.settle = w.FloatText(value=0.2, description="settle [s]")
        self.repeats = w.BoundedIntText(value=3, min=1, max=20, description="repeats")
        self.bidirectional = w.Checkbox(value=True, description="bidirectional")
        self.measure_btn = w.Button(description="Measure", button_style="primary")
        self.measure_btn.on_click(self._on_measure)
        self.progress = w.IntProgress(min=0, max=1, value=0, description="progress")
        self.progress_label = w.Label("")
        self.output = w.Image(format="png")

        self.new_loop_name = w.Text(value="", description="corrector name",
                                    layout=w.Layout(width="220px"))
        self.create_loop_btn = w.Button(description="Use for closed-loop correction",
                                        disabled=True)
        self.create_loop_btn.on_click(self._on_create_loop)
        self.create_loop_label = w.Label("")

        self.matrix_loops_box = w.Accordion(children=[])

        self._refresh_names()
        self.widget = w.VBox([
            w.HBox([self.adj_select, self.det_select]),
            w.HBox([self.step, self.settle, self.repeats, self.bidirectional]),
            w.HBox([self.measure_btn, self.progress, self.progress_label]),
            self.output,
            w.HBox([self.new_loop_name, self.create_loop_btn, self.create_loop_label]),
            w.HTML("<b>Closed-loop correctors</b>"),
            self.matrix_loops_box,
        ])

    def _refresh_names(self):
        try:
            self.adj_select.options = self.client.actuators()
            self.det_select.options = self.client.detectors()
        except Exception:
            pass

    def _on_measure(self, _btn):
        adjustables, detectors = list(self.adj_select.value), list(self.det_select.value)
        if not adjustables or not detectors:
            self.progress_label.value = "pick at least one actuator and detector"
            return
        self._current_name = f"gui_{int(time.time() * 1000) % 1_000_000}"
        self._shown_name = None
        self.measure_btn.disabled = True
        self.create_loop_btn.disabled = True
        self.progress_label.value = "starting..."
        try:
            self.client.start_response_matrix(
                self._current_name, adjustables, detectors, step=self.step.value,
                settle_s=self.settle.value, repeats=self.repeats.value,
                bidirectional=self.bidirectional.value,
            )
        except Exception as exc:
            self.measure_btn.disabled = False
            self.progress_label.value = f"error: {exc}"

    def _on_create_loop(self, _btn):
        name = self.new_loop_name.value.strip()
        if not name:
            self.create_loop_label.value = "enter a name first"
            return
        try:
            self.client.create_matrix_loop(name, self._current_name)
            self.create_loop_label.value = f"created {name!r} (disabled -- enable below)"
            self.new_loop_name.value = ""
        except Exception as exc:
            self.create_loop_label.value = f"error: {exc}"

    def refresh(self):
        self._refresh_names()
        self._refresh_matrix_loops()
        if self._current_name is None:
            return
        try:
            status = self.client.response_matrix_status(self._current_name)
        except Exception:
            return

        total = max(status["total_steps"], 1)
        self.progress.max = total
        self.progress.value = status["step_index"] + (1 if status["state"] == "done" else 0)

        if status["state"] == "running":
            self.progress_label.value = f"measuring {status['current_actuator']!r}..."
        elif status["state"] == "error":
            self.progress_label.value = f"error: {status['error']}"
            self.measure_btn.disabled = False
        elif status["state"] == "done":
            self.progress_label.value = (
                f"done — condition number {status['condition_number']:.3g}")
            self.measure_btn.disabled = False
            self.create_loop_btn.disabled = False
            if self._shown_name != self._current_name:
                self._show_matrix(status)
                self._shown_name = self._current_name

    def _refresh_matrix_loops(self):
        try:
            all_status = self.client.matrix_loops()
        except Exception:
            return
        for name, status in all_status.items():
            if name not in self.matrix_loop_tabs:
                self.matrix_loop_tabs[name] = MatrixLoopTab(self.client, name, status)
                self.matrix_loops_box.children = [t.widget for t in self.matrix_loop_tabs.values()]
                for i, n in enumerate(self.matrix_loop_tabs):
                    self.matrix_loops_box.set_title(i, n)
        for tab in self.matrix_loop_tabs.values():
            try:
                tab.refresh()
            except Exception:
                pass

    def _show_matrix(self, status):
        matrix = np.asarray(status["matrix"])
        adj_names, det_names = status["adjustable_names"], status["detector_names"]
        vmax = max(float(np.abs(matrix).max()), 1e-9)
        fig, ax = _figure((1.2 + len(adj_names), 1.2 + len(det_names)))
        im = ax.imshow(matrix, cmap="RdBu_r", vmin=-vmax, vmax=vmax)
        ax.set_xticks(range(len(adj_names)))
        ax.set_xticklabels(adj_names, rotation=45, ha="right")
        ax.set_yticks(range(len(det_names)))
        ax.set_yticklabels(det_names)
        for i in range(len(det_names)):
            for j in range(len(adj_names)):
                ax.text(j, i, f"{matrix[i, j]:.3g}", ha="center", va="center", fontsize=8)
        fig.colorbar(im, ax=ax, shrink=0.8, label="d(detector)/d(actuator)")
        fig.tight_layout()
        self.output.value = _png(fig)


class LogPanel:
    """Polls the server's event log and renders it as scrolling text."""

    def __init__(self, client: FeedbackClient):
        self.client = client
        self.widget = w.Output(layout=w.Layout(
            height="320px", overflow_y="auto", border="1px solid #ccc"))

    def refresh(self):
        try:
            entries = self.client.logs(n=200)
        except Exception:
            return
        lines = [
            f"{time.strftime('%H:%M:%S', time.localtime(e['ts']))} "
            f"[{e['level'].upper():7s}] {e['source']}: {e['message']}"
            for e in entries
        ]
        _show_text(self.widget, "\n".join(lines) if lines else "(no log entries yet)")


class SystemPanel:
    """Interlocks, alarms, server health, and config save/load."""

    def __init__(self, client: FeedbackClient):
        self.client = client
        self.interlocks_out = w.Output()
        self.alarms_out = w.Output()
        self.health_out = w.Output()

        self.config_path = w.Text(value="", description="path",
                                  placeholder="(server default if blank)",
                                  layout=w.Layout(width="400px"))
        self.save_btn = w.Button(description="Save config", button_style="primary")
        self.load_btn = w.Button(description="Load config", button_style="warning")
        self.config_label = w.Label("")
        self.save_btn.on_click(self._on_save)
        self.load_btn.on_click(self._on_load)

        self._interlock_widgets: dict[str, tuple] = {}
        self.interlocks_box = w.VBox([])

        self.widget = w.VBox([
            w.HTML("<b>Interlocks</b>"),
            self.interlocks_box,
            w.HTML("<b>Alarms</b>"),
            self.alarms_out,
            w.HTML("<b>Health</b>"),
            self.health_out,
            w.HTML("<b>Config</b>"),
            w.HBox([self.config_path, self.save_btn, self.load_btn, self.config_label]),
        ])

    def _on_save(self, _btn):
        try:
            r = self.client.save_config(self.config_path.value or None)
            self.config_label.value = f"saved to {r['saved']}"
        except Exception as exc:
            self.config_label.value = f"error: {exc}"

    def _on_load(self, _btn):
        try:
            r = self.client.load_config(self.config_path.value or None)
            self.config_label.value = f"loaded from {r['loaded']}"
        except Exception as exc:
            self.config_label.value = f"error: {exc}"

    def _make_interlock_row(self, name):
        label = w.HTML()
        force_true = w.Button(description="Force OK", layout=w.Layout(width="90px"))
        force_false = w.Button(description="Force trip", layout=w.Layout(width="90px"))
        clear = w.Button(description="Clear", layout=w.Layout(width="70px"))
        force_true.on_click(lambda _b, n=name: self._force(n, True))
        force_false.on_click(lambda _b, n=name: self._force(n, False))
        clear.on_click(lambda _b, n=name: self._clear(n))
        row = w.HBox([label, force_true, force_false, clear])
        self._interlock_widgets[name] = (label, row)
        return row

    def _force(self, name, value):
        try:
            self.client.force_interlock(name, value)
        except Exception:
            pass

    def _clear(self, name):
        try:
            self.client.clear_interlock(name)
        except Exception:
            pass

    def refresh(self):
        try:
            interlocks = self.client.interlocks()
        except Exception:
            interlocks = {}
        for name in interlocks:
            if name not in self._interlock_widgets:
                row = self._make_interlock_row(name)
                self.interlocks_box.children = tuple(self.interlocks_box.children) + (row,)
        for name, status in interlocks.items():
            label, _row = self._interlock_widgets[name]
            state = "OK" if status["ok"] else "TRIPPED"
            color = "#080" if status["ok"] else "#c00"
            forced = f" (forced {status['forced']})" if status["forced"] is not None else ""
            label.value = (f"<span style='color:{color};font-weight:bold'>{state}</span> "
                           f"{name}{forced}")

        try:
            alarms = self.client.alarms()
        except Exception:
            alarms = None
        if alarms:
            _show_text(self.alarms_out, "\n".join(f"ALARM: {name}" for name in alarms))
        else:
            _show_text(self.alarms_out, "(no alarms)" if alarms is not None else "(unavailable)")

        try:
            health = self.client.health()
        except Exception:
            health = None
        if health is None:
            _show_text(self.health_out, "(unavailable)")
        else:
            lines = [f"status: {health['status']}  uptime: {health['uptime_s']:.0f}s"]
            for group in ("loops", "matrix_loops"):
                for name, h in health[group].items():
                    tag = "alive" if h["alive"] else "STALE"
                    lines.append(f"  {name}: {tag} (last cycle {h['last_cycle_age_s']:.2f}s ago)")
            _show_text(self.health_out, "\n".join(lines))


class FeedbackPanel:
    def __init__(self, url="http://127.0.0.1:8010", refresh_s=1.0, api_key=None,
                 client=None):
        """``client``: a FeedbackClient or an in-process ``manager.service``;
        if given, ``url``/``api_key`` are ignored."""
        if client is None:
            client = FeedbackClient(url, api_key=api_key)
        self.client = client
        self.url = getattr(client, "url", None)
        self._stopped = threading.Event()
        loops = self.client.loops()
        self.tabs = [LoopTab(self.client, n, s) for n, s in loops.items()]
        self.plots = [LoopPlot(n) for n in loops]
        self.matrix_panel = MatrixPanel(self.client)
        self.log_panel = LogPanel(self.client)
        self.system_panel = SystemPanel(self.client)

        settings_box = w.VBox([
            _panel_header(self.url, "settings", "Settings"),
            w.Tab(children=[t.widget for t in self.tabs],
                 titles=[t.name for t in self.tabs]),
        ])
        plots_box = w.VBox([
            _panel_header(self.url, "plots", "Plots"),
            w.Tab(children=[p.output for p in self.plots],
                 titles=[p.name for p in self.plots]),
        ])
        matrix_box = w.VBox([
            _panel_header(self.url, "matrix", "Matrix"), self.matrix_panel.widget])
        logs_box = w.VBox([
            _panel_header(self.url, "logs", "Logs"), self.log_panel.widget])
        system_box = w.VBox([
            _panel_header(self.url, "system", "System"), self.system_panel.widget])
        help_box = _help_widget(self.url)

        self.widget = w.Tab(
            children=[settings_box, plots_box, matrix_box, logs_box, system_box, help_box],
            titles=["Settings", "Plots", "Matrix", "Logs", "System", "Help"],
        )
        self._start_refresh(refresh_s)

    def _start_refresh(self, interval):
        def tick():
            for tab in self.tabs:
                try:
                    tab.refresh()
                except Exception:
                    pass
            for tab, plot in zip(self.tabs, self.plots):
                try:
                    plot.update(self.client.history(plot.name, n=500), tab.setpoint.value)
                except Exception:
                    pass
            try:
                self.matrix_panel.refresh()
            except Exception:
                pass
            try:
                self.log_panel.refresh()
            except Exception:
                pass
            try:
                self.system_panel.refresh()
            except Exception:
                pass

        def run():
            while not self._stopped.wait(interval):
                tick()

        tick()
        self._thread = threading.Thread(target=run, daemon=True, name="feedback-panel")
        _PANELS.add(self)
        self._thread.start()

    def stop(self, join=False):
        """Stop refreshing (the panel stays as it is)."""
        self._stopped.set()
        if join and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def close(self):
        self.stop()
        self.widget.close()
