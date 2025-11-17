"""
IPython widget for Scans instances.

- Choose scan method from a dropdown (ascan, dscan, meshscan, acquire, etc).
- Selecting a method builds a parameter form for positional and keyword args
  (inspecting the method signature) and includes dynamic callback keywords
  returned by scans.get_callback_keywords(method_name) when available.
- Second tab contains a matplotlib Figure with an empty axis. The widget exposes
  `.fig` and `.ax` for plotting.
- Pressing "Run" will call a user-provided run_callback(scan_obj, method_name, args, kwargs)
  if given, otherwise it will attempt to call the scan method directly.

Usage:
    from eco.widgets.scan_widget import ScanWidget, make_scan_widget
    w = make_scan_widget(scans_instance)
    display(w)
    # Access figure: w.fig, w.ax
    # Register custom runner:
    w.run_callback = lambda scans, m, a, k: print("would run", m, a, k)
"""

from typing import Any, Callable, Dict, List, Optional, Tuple
import inspect
import threading
import json

import ipywidgets as widgets
from IPython.display import display, clear_output

import matplotlib.pyplot as plt


# helper to coerce simple string to numeric/bool if possible
def _coerce_value(s: str) -> Any:
    if s is None:
        return None
    s = s.strip()
    if s == "":
        return ""
    # try bool
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    # try int
    try:
        iv = int(s)
        return iv
    except Exception:
        pass
    # try float
    try:
        fv = float(s)
        return fv
    except Exception:
        pass
    # try json (list/dict)
    try:
        j = json.loads(s)
        return j
    except Exception:
        pass
    return s


def _make_widget_for_default(value: Any):
    """Return (widget, reader) for a default value."""
    # None -> text input (empty)
    if isinstance(value, bool):
        w = widgets.Checkbox(value=value)
        return w, lambda: w.value
    if isinstance(value, int) and not isinstance(value, bool):
        w = widgets.IntText(value=value)
        return w, lambda: int(w.value)
    if isinstance(value, float):
        w = widgets.FloatText(value=value)
        return w, lambda: float(w.value)
    # list/tuple -> Text (JSON) so user can enter JSON-like
    if isinstance(value, (list, dict, tuple)):
        w = widgets.Text(value=json.dumps(value), layout=widgets.Layout(width="100%"))
        return w, lambda: _coerce_value(w.value)
    # fallback string
    w = widgets.Text(
        value=str(value) if value is not None else "",
        layout=widgets.Layout(width="100%"),
    )
    return w, lambda: _coerce_value(w.value)


def _make_free_arg_widget(placeholder: str = ""):
    w = widgets.Text(
        value="", placeholder=placeholder, layout=widgets.Layout(width="100%")
    )
    return w, lambda: _coerce_value(w.value)


class ScanWidget(widgets.Tab):
    def __init__(
        self,
        scans_obj: Any,
        methods: Optional[List[str]] = None,
        auto_build: bool = True,
    ):
        """
        scans_obj: instance providing scan methods and optionally get_callback_keywords(method_name).
        methods: optional list of method names to offer; if None common names will be searched on scans_obj.
        """
        self.scans = scans_obj
        # find available methods if not provided
        if methods is None:
            cand = ["ascan", "dscan", "meshscan", "acquire"]
            methods = [m for m in cand if hasattr(scans_obj, m)]
            # also include any callable attributes that look like scans
            for name in dir(scans_obj):
                if (
                    name not in methods
                    and callable(getattr(scans_obj, name))
                    and not name.startswith("_")
                ):
                    methods.append(name)
        self.methods = methods

        # top controls: dropdown, run button, get params button
        self.method_dd = widgets.Dropdown(
            options=self.methods,
            description="Method:",
            layout=widgets.Layout(width="50%"),
        )
        self.run_button = widgets.Button(description="Run", button_style="primary")
        self.get_params_button = widgets.Button(description="Get params")
        self.status_label = widgets.Label("")

        top_box = widgets.HBox(
            [self.method_dd, self.run_button, self.get_params_button, self.status_label]
        )

        # parameter area will be rebuilt per method
        self.params_box = widgets.VBox([])

        # expose run callback override
        # signature: run_callback(scans_obj, method_name, args_list, kwargs_dict)
        self.run_callback: Optional[
            Callable[[Any, str, List[Any], Dict[str, Any]], Any]
        ] = None

        # figure tab: create empty figure and axis, display into an Output widget
        self.fig = plt.Figure(figsize=(6, 4))
        self.ax = self.fig.add_subplot(111)
        self.plot_out = widgets.Output(layout=widgets.Layout(border="1px solid #ddd"))
        with self.plot_out:
            display(self.fig)

        # assemble two tab children: form and plot output
        self.form_vbox = widgets.VBox(
            [top_box, widgets.HTML("<b>Parameters</b>"), self.params_box]
        )
        children = [self.form_vbox, self.plot_out]

        super().__init__(children)
        self.set_title(0, "Form")
        self.set_title(1, "Plot")

        # wire events
        self.method_dd.observe(self._on_method_change, names="value")
        self.run_button.on_click(self._on_run)
        self.get_params_button.on_click(self._on_get_params)

        # storage for widgets mapping
        self._pos_widgets: List[Tuple[str, widgets.Widget, Callable[[], Any]]] = []
        self._kw_widgets: List[Tuple[str, widgets.Widget, Callable[[], Any]]] = []

        if auto_build:
            self.build_for_method(self.method_dd.value)

    def _on_method_change(self, change):
        if change.get("name") == "value":
            self.build_for_method(change["new"])

    def _get_dynamic_callback_keywords(self, method_name: str) -> Dict[str, Any]:
        """Call scans.get_callback_keywords(method_name) if available, return dict of kw->default/metadata."""
        fn = getattr(self.scans, "get_callback_keywords", None)
        if callable(fn):
            try:
                kws = fn(method_name)
                if isinstance(kws, dict):
                    return kws
                # try list of names -> treat as None defaults
                if isinstance(kws, (list, tuple)):
                    return {k: None for k in kws}
            except Exception:
                pass
        return {}

    def build_for_method(self, method_name: str):
        """(Re)build parameter widgets for selected method."""
        self._pos_widgets = []
        self._kw_widgets = []
        self.status_label.value = ""
        self.params_box.children = [
            widgets.Label(f"Building parameter form for {method_name}...")
        ]

        method = getattr(self.scans, method_name, None)
        if method is None or not callable(method):
            self.params_box.children = [widgets.Label("Selected method not available")]
            return

        sig = None
        try:
            sig = inspect.signature(method)
        except Exception:
            sig = None

        # dynamic callback keywords
        dyn_kws = self._get_dynamic_callback_keywords(method_name)

        pos_rows = []
        kw_rows = []

        if sig is not None:
            for pname, param in sig.parameters.items():
                if pname == "self":
                    continue
                kind = param.kind
                default = param.default if param.default is not inspect._empty else None
                if kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                ):
                    # positional parameter: create widget; if default is None treat as required
                    if default is None:
                        w, reader = _make_free_arg_widget(
                            placeholder=f"{pname} (required)"
                        )
                    else:
                        w, reader = _make_widget_for_default(default)
                    lbl = widgets.Label(pname, layout=widgets.Layout(width="25%"))
                    row = widgets.HBox([lbl, w])
                    pos_rows.append(row)
                    self._pos_widgets.append((pname, w, reader))
                elif kind == inspect.Parameter.VAR_POSITIONAL:
                    # allow multiple positional args as newline-separated or JSON list
                    w = widgets.Textarea(
                        placeholder="comma separated or JSON list",
                        layout=widgets.Layout(width="100%"),
                    )
                    reader = lambda w=w: _coerce_value(w.value)
                    lbl = widgets.Label("*" + pname, layout=widgets.Layout(width="25%"))
                    row = widgets.HBox([lbl, w])
                    pos_rows.append(row)
                    self._pos_widgets.append((pname, w, reader))
                elif kind == inspect.Parameter.KEYWORD_ONLY:
                    # keyword-only parameter
                    if default is None:
                        w, reader = _make_free_arg_widget(
                            placeholder=f"{pname} (required)"
                        )
                    else:
                        w, reader = _make_widget_for_default(default)
                    lbl = widgets.Label(pname, layout=widgets.Layout(width="25%"))
                    row = widgets.HBox([lbl, w])
                    kw_rows.append(row)
                    self._kw_widgets.append((pname, w, reader))
                elif kind == inspect.Parameter.VAR_KEYWORD:
                    # provide a Textarea for free-form kwargs (JSON or key=val lines)
                    w = widgets.Textarea(
                        placeholder='JSON object or "k=v" lines',
                        layout=widgets.Layout(width="100%"),
                    )
                    reader = lambda w=w: _coerce_value(w.value)
                    lbl = widgets.Label(
                        "**" + pname, layout=widgets.Layout(width="25%")
                    )
                    row = widgets.HBox([lbl, w])
                    kw_rows.append(row)
                    self._kw_widgets.append((pname, w, reader))
        else:
            # unknown signature: provide free args and kwargs boxes
            wpos = widgets.Textarea(
                placeholder="JSON list of positional args",
                layout=widgets.Layout(width="100%"),
            )
            rr_pos = lambda w=wpos: _coerce_value(w.value)
            self._pos_widgets.append(("args", wpos, rr_pos))
            wkw = widgets.Textarea(
                placeholder="JSON kwargs dict", layout=widgets.Layout(width="100%")
            )
            rr_kw = lambda w=wkw: _coerce_value(w.value)
            self._kw_widgets.append(("kwargs", wkw, rr_kw))
            pos_rows.append(wpos)
            kw_rows.append(wkw)

        # include dynamic callback keywords (if any) as additional kwargs (do not overwrite existing)
        for k, v in dyn_kws.items():
            if k in [name for name, _, _ in self._kw_widgets]:
                continue
            # create widget depending on provided default
            if isinstance(v, (list, tuple)):
                # treat as choices -> Dropdown
                options = [(str(opt), opt) for opt in v]
                dd = widgets.Dropdown(
                    options=options,
                    value=(v[0] if len(v) else None),
                    layout=widgets.Layout(width="60%"),
                )
                reader = lambda dd=dd: dd.value
                lbl = widgets.Label(k, layout=widgets.Layout(width="25%"))
                row = widgets.HBox([lbl, dd])
                kw_rows.append(row)
                self._kw_widgets.append((k, dd, reader))
            else:
                if v is None:
                    w, reader = _make_free_arg_widget(placeholder=f"{k} (optional)")
                else:
                    w, reader = _make_widget_for_default(v)
                lbl = widgets.Label(k, layout=widgets.Layout(width="25%"))
                row = widgets.HBox([lbl, w])
                kw_rows.append(row)
                self._kw_widgets.append((k, w, reader))

        pos_section = (
            widgets.VBox([widgets.HTML("<b>Positional / varargs</b>")] + pos_rows)
            if pos_rows
            else widgets.HTML("")
        )
        kw_section = (
            widgets.VBox([widgets.HTML("<b>Keyword args</b>")] + kw_rows)
            if kw_rows
            else widgets.HTML("")
        )

        self.params_box.children = [pos_section, kw_section]

    def _collect_params(self) -> Tuple[List[Any], Dict[str, Any]]:
        """Read widgets and return (args_list, kwargs_dict)."""
        args: List[Any] = []
        kwargs: Dict[str, Any] = {}
        # positional widgets
        for name, w, reader in self._pos_widgets:
            val = None
            try:
                val = reader()
            except Exception:
                val = None
            if name.startswith("*"):
                # not used here, but include raw
                args.append(val)
            elif name == "args":
                if isinstance(val, list):
                    args.extend(val)
                elif isinstance(val, (str,)):
                    # try parse comma separated
                    if val.strip().startswith("[") or val.strip().startswith("{"):
                        try:
                            parsed = _coerce_value(val)
                            if isinstance(parsed, list):
                                args.extend(parsed)
                            else:
                                args.append(parsed)
                        except Exception:
                            args.append(val)
                    else:
                        parts = [p.strip() for p in val.split(",") if p.strip()]
                        for p in parts:
                            args.append(_coerce_value(p))
                else:
                    args.append(val)
            else:
                # normal positional param: include value (even if None) but caller may require
                args.append(val)
        # keyword widgets
        for name, w, reader in self._kw_widgets:
            try:
                val = reader()
            except Exception:
                val = None
            if name == "kwargs" or name.startswith("**"):
                # parse as dict if possible
                if isinstance(val, dict):
                    kwargs.update(val)
                elif isinstance(val, str):
                    # attempt parse JSON
                    parsed = _coerce_value(val)
                    if isinstance(parsed, dict):
                        kwargs.update(parsed)
                    else:
                        # try parse "k=v" lines
                        for line in val.splitlines():
                            if "=" in line:
                                k, v = line.split("=", 1)
                                kwargs[k.strip()] = _coerce_value(v)
                elif isinstance(val, dict):
                    kwargs.update(val)
                else:
                    # skip unknown
                    pass
            else:
                kwargs[name] = val
        return args, kwargs

    def _on_get_params(self, _=None):
        a, k = self._collect_params()
        self.status_label.value = f"Args: {a}  Kw: {k}"

    def _on_run(self, _=None):
        method = self.method_dd.value
        args, kwargs = self._collect_params()
        self.status_label.value = "Running..."

        # allow custom callback
        def _do_call():
            try:
                if callable(self.run_callback):
                    res = self.run_callback(self.scans, method, args, kwargs)
                else:
                    fn = getattr(self.scans, method, None)
                    if not callable(fn):
                        raise RuntimeError("method not callable")
                    res = fn(*args, **kwargs)
                self.status_label.value = "Done"
            except Exception as exc:
                self.status_label.value = f"Error: {exc}"

        # run in background thread to avoid blocking UI
        t = threading.Thread(target=_do_call, daemon=True)
        t.start()


def make_scan_widget(scans_obj: Any, methods: Optional[List[str]] = None) -> ScanWidget:
    return ScanWidget(scans_obj, methods=methods)
