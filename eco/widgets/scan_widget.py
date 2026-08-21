"""
IPython widget for Scans instances.

- Choose scan method from a dropdown (ascan, dscan, meshscan, scan, acquire,
  etc).
- For the known Scans methods (see _STRUCTURED_FORM_BUILDERS below),
  selecting a method builds a structured form: adjustables/counters are
  picked with the graphical component picker (eco.widgets.
  component_picker_widget, built on eco.widgets.component_selector_widget)
  instead of typed by hand, and multi-axis methods (meshscan, scan) get a
  growable list of axis rows via eco.widgets.scan_builder_widgets.
  scan()'s axes can each be toggled "single" or "simultaneous (co-moving)",
  mirroring Scans.scan()'s own nested-list-for-simultaneous /
  flat-tuple-for-mesh *adj_specs convention. Any other/unrecognized method
  falls back to the generic reflection-based form (signature -> free-text/
  JSON fields), same as before.
- Second tab contains a matplotlib Figure with an empty axis. The widget exposes
  `.fig` and `.ax` for plotting.
- Pressing "Run" will call a user-provided run_callback(scan_obj, method_name, args, kwargs)
  if given, otherwise it will attempt to call the scan method directly.

Usage:
    from eco.widgets.scan_widget import ScanWidget, make_scan_widget
    w = make_scan_widget(scans_instance, root=eco.bernina.bernina)
    display(w)
    # root is the namespace browsed by the adjustable/detector picker --
    # pass the actual beamline namespace so there's something to pick from.
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

from eco.widgets.component_picker_widget import (
    ComponentPickerWidget,
    MultiComponentPickerWidget,
)
from eco.widgets.component_selector import ComponentBookmarks, RecentComponents
from eco.widgets.scan_builder_widgets import ScanBuilderWidget


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


# ---------------------------------------------------------------------------
# Structured, picker-driven forms for the known Scans.* methods.
#
# The generic reflection-based form above (signature -> free-text/JSON
# fields) works for anything, but for the methods every scan actually uses
# it makes you type adjustable/detector paths by hand. These builders use
# the component picker (eco.widgets.component_picker_widget /
# eco.widgets.scan_builder_widgets) instead, and know each method's real
# calling convention (which params are positional, which flow through
# **kwargs_callbacks, snakescan not taking N_pulses at all, etc.) rather
# than guessing from inspect.signature().
# ---------------------------------------------------------------------------

_RETURN_AT_END_OPTIONS = [
    ("timeout (ask, auto-revert after 10s)", "timeout"),
    ("question (ask, wait for answer)", "question"),
    ("always revert to initial value", True),
    ("stay at final value", False),
]


def _build_common_extra_kwargs(
    root: Any,
    bookmarks: ComponentBookmarks,
    recent: RecentComponents,
    return_at_end_default: Any = "timeout",
    include_repetitions: bool = True,
):
    """The parameter block shared by (almost) every Scans method: description,
    settling_time, return_at_end, optionally repetitions, and an optional
    counters override. Returns (widget, read()) where read() -> kwargs dict
    (only including "counters" if the user actually picked any -- an empty
    picker list means "use the scan's default counters")."""
    description_w = widgets.Text(
        placeholder="description",
        description="descr.:",
        layout=widgets.Layout(width="320px"),
    )
    settling_w = widgets.FloatText(
        value=0,
        description="settling_time:",
        style={"description_width": "initial"},
        layout=widgets.Layout(width="200px"),
    )
    rae_w = widgets.Dropdown(
        options=_RETURN_AT_END_OPTIONS,
        value=return_at_end_default,
        description="return_at_end:",
        style={"description_width": "initial"},
        layout=widgets.Layout(width="320px"),
    )
    rep_w = (
        widgets.IntText(
            value=1, description="repetitions:", layout=widgets.Layout(width="180px")
        )
        if include_repetitions
        else None
    )
    counters_w = MultiComponentPickerWidget(
        root,
        kind_filter="Detector",
        empty_hint="(empty = use scan's default counters)",
        bookmarks=bookmarks,
        recent=recent,
    )

    rows = [description_w, widgets.HBox([settling_w, rae_w] + ([rep_w] if rep_w else []))]
    rows.append(widgets.VBox([widgets.HTML("<b>Counters (optional)</b>"), counters_w]))
    box = widgets.VBox(rows)

    def read() -> Dict[str, Any]:
        kw: Dict[str, Any] = dict(
            description=description_w.value,
            settling_time=settling_w.value,
            return_at_end=rae_w.value,
        )
        if rep_w is not None:
            kw["repetitions"] = rep_w.value
        counters_val = counters_w.get_value()
        if counters_val:
            kw["counters"] = counters_val
        return kw

    return box, read


def _build_acquire_form(scans_obj, root, bookmarks, recent):
    n_pulses_w = widgets.IntText(
        value=100, description="N_pulses:", layout=widgets.Layout(width="180px")
    )
    n_rep_w = widgets.IntText(
        value=1, description="N_repetitions:", layout=widgets.Layout(width="200px")
    )
    top = widgets.HBox([n_pulses_w, n_rep_w])
    extra_box, read_extra = _build_common_extra_kwargs(
        root, bookmarks, recent, return_at_end_default=True, include_repetitions=False
    )
    widget = widgets.VBox([top, extra_box])

    def get_call():
        kwargs = read_extra()
        kwargs["N_repetitions"] = n_rep_w.value
        return [n_pulses_w.value], kwargs

    return widget, get_call


def _build_ascan_like_form(scans_obj, root, bookmarks, recent):
    """Shared by ascan and dscan: same shape, only the target method call
    differs (dscan interprets start/end relative to the current position)."""
    picker = ComponentPickerWidget(root, kind_filter="Adjustable", bookmarks=bookmarks, recent=recent)
    start_w = widgets.FloatText(value=0.0, description="start:", layout=widgets.Layout(width="160px"))
    end_w = widgets.FloatText(value=1.0, description="end:", layout=widgets.Layout(width="160px"))
    n_w = widgets.IntText(value=10, description="N_intervals:", layout=widgets.Layout(width="160px"))
    n_pulses_w = widgets.IntText(value=100, description="N_pulses:", layout=widgets.Layout(width="160px"))
    top = widgets.VBox(
        [
            widgets.HTML("<b>Adjustable</b>"),
            picker,
            widgets.HBox([start_w, end_w, n_w, n_pulses_w]),
        ]
    )
    extra_box, read_extra = _build_common_extra_kwargs(root, bookmarks, recent)
    widget = widgets.VBox([top, extra_box])

    def get_call():
        args = [picker.value, start_w.value, end_w.value, int(n_w.value), n_pulses_w.value]
        return args, read_extra()

    return widget, get_call


def _build_ascan_position_list_form(scans_obj, root, bookmarks, recent):
    from eco.widgets.scan_builder_widgets import _parse_position_list

    picker = ComponentPickerWidget(root, kind_filter="Adjustable", bookmarks=bookmarks, recent=recent)
    positions_w = widgets.Text(
        placeholder="e.g. 0,0.5,1,2,5 or [0,0.5,1,2,5]",
        description="positions:",
        layout=widgets.Layout(width="100%"),
    )
    n_pulses_w = widgets.IntText(value=100, description="N_pulses:", layout=widgets.Layout(width="160px"))
    top = widgets.VBox(
        [widgets.HTML("<b>Adjustable</b>"), picker, positions_w, n_pulses_w]
    )
    extra_box, read_extra = _build_common_extra_kwargs(root, bookmarks, recent)
    widget = widgets.VBox([top, extra_box])

    def get_call():
        args = [picker.value, _parse_position_list(positions_w.value), n_pulses_w.value]
        return args, read_extra()

    return widget, get_call


def _build_snakescan_form(scans_obj, root, bookmarks, recent):
    picker_slow = ComponentPickerWidget(root, kind_filter="Adjustable", placeholder="(slow axis)", bookmarks=bookmarks, recent=recent)
    picker_fast = ComponentPickerWidget(root, kind_filter="Adjustable", placeholder="(fast axis)", bookmarks=bookmarks, recent=recent)
    step_interval_w = widgets.FloatText(value=1.0, description="step_interval:", style={"description_width": "initial"}, layout=widgets.Layout(width="200px"))
    nrows_w = widgets.IntText(value=5, description="Nrows:", layout=widgets.Layout(width="140px"))
    interval_w = widgets.FloatText(value=1.0, description="interval:", layout=widgets.Layout(width="160px"))
    top = widgets.VBox(
        [
            widgets.HTML("<b>Slow adjustable</b>"),
            picker_slow,
            step_interval_w,
            nrows_w,
            widgets.HTML("<b>Fast adjustable</b>"),
            picker_fast,
            interval_w,
        ]
    )
    # snakescan hardcodes Npulses=1 internally -- no N_pulses field here
    extra_box, read_extra = _build_common_extra_kwargs(root, bookmarks, recent)
    widget = widgets.VBox([top, extra_box])

    def get_call():
        args = [
            picker_slow.value,
            step_interval_w.value,
            int(nrows_w.value),
            picker_fast.value,
            interval_w.value,
        ]
        return args, read_extra()

    return widget, get_call


def _build_a2scan_form(scans_obj, root, bookmarks, recent):
    picker0 = ComponentPickerWidget(root, kind_filter="Adjustable", placeholder="(adjustable 0)", bookmarks=bookmarks, recent=recent)
    picker1 = ComponentPickerWidget(root, kind_filter="Adjustable", placeholder="(adjustable 1)", bookmarks=bookmarks, recent=recent)
    start0_w = widgets.FloatText(value=0.0, description="start0:", layout=widgets.Layout(width="150px"))
    end0_w = widgets.FloatText(value=1.0, description="end0:", layout=widgets.Layout(width="150px"))
    start1_w = widgets.FloatText(value=0.0, description="start1:", layout=widgets.Layout(width="150px"))
    end1_w = widgets.FloatText(value=1.0, description="end1:", layout=widgets.Layout(width="150px"))
    n_w = widgets.IntText(value=10, description="N_intervals:", layout=widgets.Layout(width="160px"))
    n_pulses_w = widgets.IntText(value=100, description="N_pulses:", layout=widgets.Layout(width="160px"))
    top = widgets.VBox(
        [
            widgets.HTML("<b>Adjustable 0</b>"),
            picker0,
            widgets.HBox([start0_w, end0_w]),
            widgets.HTML("<b>Adjustable 1</b>"),
            picker1,
            widgets.HBox([start1_w, end1_w]),
            widgets.HBox([n_w, n_pulses_w]),
        ]
    )
    extra_box, read_extra = _build_common_extra_kwargs(root, bookmarks, recent)
    widget = widgets.VBox([top, extra_box])

    def get_call():
        args = [
            picker0.value,
            start0_w.value,
            end0_w.value,
            picker1.value,
            start1_w.value,
            end1_w.value,
            int(n_w.value),
            n_pulses_w.value,
        ]
        return args, read_extra()

    return widget, get_call


def _build_grid_form(allow_simultaneous: bool):
    """Shared by meshscan (allow_simultaneous=False) and scan
    (allow_simultaneous=True): a growable list of grid axes via
    ScanBuilderWidget, the picker-driven version of *adj_specs. For scan(),
    each axis can be toggled to "simultaneous" for an a2scan-like co-moving
    group -- the nested-list-vs-flat-tuple convention scan() itself parses.
    """

    def build(scans_obj, root, bookmarks, recent):
        builder = ScanBuilderWidget(
            root, allow_simultaneous=allow_simultaneous, bookmarks=bookmarks, recent=recent
        )
        n_pulses_w = widgets.IntText(value=100, description="N_pulses:", layout=widgets.Layout(width="180px"))
        order_w = widgets.Dropdown(
            options=["last_fastest", "first_fastest"],
            value="last_fastest",
            description="scanning_order:",
            style={"description_width": "initial"},
            layout=widgets.Layout(width="260px"),
        )
        top = widgets.HBox([n_pulses_w, order_w])
        extra_box, read_extra = _build_common_extra_kwargs(root, bookmarks, recent)
        widget = widgets.VBox([builder, top, extra_box])

        def get_call():
            kwargs = read_extra()
            kwargs["N_pulses"] = n_pulses_w.value
            kwargs["scanning_order"] = order_w.value
            return builder.get_adj_specs(), kwargs

        return widget, get_call

    return build


_STRUCTURED_FORM_BUILDERS: Dict[str, Callable] = {
    "acquire": _build_acquire_form,
    "ascan": _build_ascan_like_form,
    "dscan": _build_ascan_like_form,
    "ascan_position_list": _build_ascan_position_list_form,
    "snakescan": _build_snakescan_form,
    "a2scan": _build_a2scan_form,
    "meshscan": _build_grid_form(allow_simultaneous=False),
    "scan": _build_grid_form(allow_simultaneous=True),
}


class ScanWidget(widgets.Tab):
    def __init__(
        self,
        scans_obj: Any,
        methods: Optional[List[str]] = None,
        root: Any = None,
        auto_build: bool = True,
    ):
        """
        scans_obj: instance providing scan methods and optionally get_callback_keywords(method_name).
        methods: optional list of method names to offer; if None common names will be searched on scans_obj.
        root: namespace to browse for the adjustable/detector picker on the
            structured forms (ascan, scan, ...) -- typically the beamline
            namespace, e.g. eco.bernina.bernina. Defaults to scans_obj itself
            if not given, which only helps if adjustables are actually
            reachable from there.
        """
        self.scans = scans_obj
        self.root = root if root is not None else scans_obj
        self._bookmarks = ComponentBookmarks(
            namespace_name=getattr(self.root, "name", None)
        )
        self._recent = RecentComponents(
            namespace_name=getattr(self.root, "name", None)
        )
        # set by build_for_method when a structured (picker-driven) form is
        # used for the selected method; _collect_params() prefers it over
        # the generic signature-introspection widgets below
        self._structured_get_call: Optional[Callable[[], Tuple[List[Any], Dict[str, Any]]]] = None
        # find available methods if not provided
        if methods is None:
            cand = [
                "ascan",
                "dscan",
                "ascan_position_list",
                "snakescan",
                "a2scan",
                "meshscan",
                "scan",
                "acquire",
            ]
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
        self._structured_get_call = None
        self.status_label.value = ""
        self.params_box.children = [
            widgets.Label(f"Building parameter form for {method_name}...")
        ]

        method = getattr(self.scans, method_name, None)
        if method is None or not callable(method):
            self.params_box.children = [widgets.Label("Selected method not available")]
            return

        structured_builder = _STRUCTURED_FORM_BUILDERS.get(method_name)
        if structured_builder is not None:
            form_widget, get_call = structured_builder(
                self.scans, self.root, self._bookmarks, self._recent
            )
            self._structured_get_call = get_call
            self.params_box.children = [form_widget]
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
        if self._structured_get_call is not None:
            return self._structured_get_call()
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


def make_scan_widget(
    scans_obj: Any, methods: Optional[List[str]] = None, root: Any = None
) -> ScanWidget:
    """root: namespace to browse for the adjustable/detector picker, e.g.
    the beamline namespace (eco.bernina.bernina). See ScanWidget.__init__."""
    return ScanWidget(scans_obj, methods=methods, root=root)
