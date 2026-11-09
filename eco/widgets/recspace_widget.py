"""Visualisation for ``RecSpaceCrystal`` (eco.utilities.recspace_hklforge).

Two front-ends around the same 3D reciprocal-space view
(``escape.bernina.VIEWER_HTML``, or eco's copy
``eco/utilities/diffractometer_3d.html`` until escape has it; a self-contained
three.js page that polls a hidden ``#diff3d-payload`` element in its parent
document for the angles, UB, abs(k), surface normal and target reflections):

- ``geom_page``: a static page opened in the browser, for terminal sessions.
  A sidebar lists the current position, every stored reflection and, if an
  hkl is given, every solution for it (marked when outside the motor
  limits); clicking one moves the view and shows its angles.
- ``ReflectionsApp``: the ipywidgets solution browser, modelled on
  bernina-scripts ``apps/diffraction_reflections.ipynb`` but working on the
  crystal's own UB: hkl, energy, constraints with "fix" boxes and min/max
  solution filters (including the two-reflection mode, and loading a stored
  setup), a solution table with the motor-limit check, an angle simulator
  (any position -> hkl and virtual angles) and the 3D view. Constraints
  edited here are only used here until "store" writes them to the RecSpace
  constraints and solution ranges.

Nothing here moves a motor.
"""

import html
import json
import webbrowser
from pathlib import Path

import numpy as np

from eco.utilities import recspace_calc as rc
from eco.utilities.tempfiles import user_temp_path

VIEWER_HTML = rc.VIEWER_HTML     # escape.bernina's 3D page (or eco's copy until escape has it)

CIRCLES = ("gamma", "delta", "mu", "eta", "chi", "phi")
VIRTUAL_CONSTRAINTS = ("qaz", "naz", "alpha", "beta", "betain", "betaout", "psi", "omega")
BOOL_CONSTRAINTS = ("a_eq_b", "bin_eq_bout", "bisect")
TABLE_ROWS = CIRCLES + ("theta", "ttheta", "qaz", "naz", "alpha", "beta", "psi", "tau", "betain",
                        "betaout", "omega")


def payload(crystal, angles, energy, label="", targets=None, n=0):
    """The message the 3D view reads: angles (view naming), abs(k), UB, unit
    surface normal in the phi frame, optional target reflections.

    The view computes its mechanism on You's axes and only labels them in
    the Bernina frame, so UB and the normal are handed over in the You frame.
    """
    ub = crystal._ub()
    n_surf = rc.vector_phi(crystal._surface, ub)   # None: no surface normal defined
    msg = {
        "angles": {c: float(angles.get(c, 0.0)) for c in CIRCLES},
        "kMag": 2 * np.pi / rc.en2lam(energy),
        "ub": rc.to_you(ub).tolist(),
        "nPhi": None if n_surf is None else rc.to_you(n_surf).tolist(),
        "label": label,
        "n": n,
    }
    if targets:
        msg["targets"] = [list(map(float, t)) for t in targets]
    return msg


def _limits_ok(crystal, sol):
    try:
        return crystal.fit_target_into_limits(**sol) is not None
    except Exception:
        return None


def _fmt(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, (bool, np.bool_)):
        return "yes" if v else "no"
    return f"{float(v):.3f}"


def _num_text(v):
    return "" if v == "" or v is None else f"{float(v):g}"


def _parse_num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _presets(crystal):
    from eco.utilities.recspace_hklforge import constraint_presets

    try:
        return constraint_presets(crystal.config_dir)
    except Exception:
        return {}


# --- static page (terminal sessions) ------------------------------------------


def geom_entries(crystal, hkl=None, energy=None, h2=None, detector=None):
    """Sidebar entries (group, label, payload, table rows, limit flag)."""
    if energy is None:
        energy = crystal.get_energy()
    entries = []

    def add(group, label, angles, en, targets=None, extra=None, ok=None):
        hkl_now = rc.calc_hkl(crystal._ub(), en, angles)
        rows = [[c, _fmt(angles[c])] for c in CIRCLES]
        rows += [["h k l", " ".join(f"{x:.3f}" for x in hkl_now)], ["energy", f"{en:.1f} eV"]]
        rows += [[k, _fmt(v)] for k, v in (extra or {}).items()]
        entries.append({"group": group, "label": label, "ok": ok, "rows": rows,
                        "payload": payload(crystal, angles, en, label, targets)})

    try:
        cur = crystal._angles()
        add("position", "current", cur, energy, extra=crystal.pseudo_angles(**cur))
    except Exception:
        pass
    for i, r in enumerate(crystal.reflections()):
        tag = r.get("tag") or f"#{i}"
        add("stored reflections", f"{tag}  ({' '.join(f'{x:g}' for x in r['hkl'])})",
            rc.position_to_angles(r["position"]), r["energy"])
    if hkl is not None:
        df = crystal.calc_angles(*hkl, energy=energy, h2=h2, detector=detector)
        targets = [hkl, h2] if h2 is not None else None
        title = f"solutions for ({' '.join(f'{x:g}' for x in hkl)})"
        if h2 is not None:
            title += f" + ({' '.join(f'{x:g}' for x in h2)})"
        for idx, row in df.iterrows():
            sol = row.to_dict()
            extra = {k: v for k, v in sol.items() if k not in CIRCLES}
            if h2 is not None:
                sp = rc.spots(crystal._ub(), energy, sol, [hkl, h2])
                extra.update(separation=sp["separation"], tdet_max_mm=sp["tdet_max"])
            ok = _limits_ok(crystal, sol)
            add(title, str(idx) + ("" if ok is not False else "  (outside limits)"), sol, energy,
                targets=targets, extra=extra, ok=ok)
    return entries


def build_page(entries, title="Reciprocal space geometry"):
    srcdoc = html.escape(VIEWER_HTML.read_text())
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  :root {{ --bg:#fff; --fg:#222; --muted:#666; --line:#ccc; --sel:#cfe8fb; --selb:#7ab8e6; --bad:#b33; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#1e1e1e; --fg:#ddd; --muted:#999; --line:#444; --sel:#21445e; --selb:#3d7fae; --bad:#e77; }}
  }}
  html, body {{ margin:0; height:100%; font-family:sans-serif; background:var(--bg); color:var(--fg); }}
  #layout {{ display:flex; height:100vh; }}
  #sidebar {{ width:300px; overflow:auto; border-right:1px solid var(--line); padding:8px; box-sizing:border-box; }}
  h3 {{ margin:10px 0 4px; font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }}
  button.e {{ display:block; width:100%; text-align:left; margin:2px 0; padding:5px 6px; font-size:12px;
             border:1px solid var(--line); background:var(--bg); color:var(--fg); cursor:pointer; }}
  button.e.bad {{ color:var(--bad); }}
  button.e.active {{ background:var(--sel); border-color:var(--selb); }}
  table {{ border-collapse:collapse; font-size:12px; margin-top:10px; width:100%; }}
  td {{ padding:1px 4px; font-variant-numeric:tabular-nums; }}
  td:first-child {{ color:var(--muted); }}
  td:last-child {{ text-align:right; }}
  #frame {{ flex:1; min-width:0; }}
  iframe {{ width:100%; height:100%; border:0; }}
  @media (max-width: 700px) {{ #layout {{ flex-direction:column; }} #sidebar {{ width:100%; height:40vh; }} }}
</style>
</head>
<body>
<div id="layout">
  <div id="sidebar"><div id="list"></div><table id="rows"></table></div>
  <div id="frame"><iframe id="diff3d" srcdoc="{srcdoc}"></iframe></div>
</div>
<span id="diff3d-payload" style="display:none"></span>
<script>
const ENTRIES = {json.dumps(entries)};
const list = document.getElementById('list');
const buttons = [];
let lastGroup = null;
// the reciprocal cell and vectors on at first, to see the crystal's
// orientation; later clicks leave the viewer's own toggles alone
let display = {{showCell: true, showRecip: true, showHklCell: true}};
function show(i) {{
  const p = Object.assign({{}}, ENTRIES[i].payload, {{n: Date.now()}});
  if (display) {{ p.display = display; display = null; }}
  document.getElementById('diff3d-payload').textContent = JSON.stringify(p);
  buttons.forEach((b, j) => b.classList.toggle('active', j === i));
  const t = document.getElementById('rows');
  t.innerHTML = '';
  ENTRIES[i].rows.forEach(([k, v]) => {{
    const tr = t.insertRow(); tr.insertCell().textContent = k; tr.insertCell().textContent = v;
  }});
}}
ENTRIES.forEach((e, i) => {{
  if (e.group !== lastGroup) {{
    const h = document.createElement('h3'); h.textContent = e.group; list.appendChild(h); lastGroup = e.group;
  }}
  const b = document.createElement('button');
  b.className = 'e' + (e.ok === false ? ' bad' : '');
  b.textContent = e.label;
  b.onclick = () => show(i);
  list.appendChild(b); buttons.push(b);
}});
const isSolution = e => e.group.startsWith('solutions');
let firstSolution = ENTRIES.findIndex(e => isSolution(e) && e.ok !== false);
if (firstSolution < 0) firstSolution = ENTRIES.findIndex(isSolution);
if (ENTRIES.length) show(firstSolution >= 0 ? firstSolution : 0);
</script>
</body>
</html>
"""


def geom_page(crystal, hkl=None, energy=None, h2=None, detector=None, open_browser=True):
    crystal._ub()   # without a UB there is nothing to draw: say so instead of an empty page
    entries = geom_entries(crystal, hkl, energy, h2, detector)
    if not entries:
        # the current position could not be read: show the crystal at all angles 0
        zero = {c: 0.0 for c in rc.POSITION_ORDER}
        en = crystal.get_energy() if energy is None else energy
        entries = [{"group": "position", "label": "all angles 0", "ok": None,
                    "rows": [[c, "0.000"] for c in CIRCLES],
                    "payload": payload(crystal, zero, en, "all angles 0")}]
    path = Path(user_temp_path(f"eco_recspace_{crystal.name}.html"))
    path.write_text(build_page(entries, f"RecSpace {crystal.name}"))
    if open_browser:
        webbrowser.open(f"file://{path}")
        print(f"Opened reciprocal-space view: {path}")
    return path


# --- ipywidgets solution browser --------------------------------------------------


class ReflectionsApp:
    """Interactive solution browser for one crystal; ``.widget`` is the box to display."""

    SEL_CURRENT, SEL_ZERO, SEL_SIM = "current", "zero", "sim"

    def __init__(self, crystal, hkl=None, h2=None):
        import ipywidgets as w

        self.w = w
        self.c = crystal
        self._suspend = False
        self._sim_suspend = False
        self._pushes = 0
        self.sols = []
        self.selected = self.SEL_CURRENT
        L = w.Layout

        try:
            energy = float(crystal.get_energy())
        except Exception:
            energy = 9000.0
        if hkl is None:
            try:
                hkl = [round(float(x), 4) for x in crystal.calc_hkl(energy=energy)]
            except Exception:
                hkl = [1.0, 0.0, 0.0]
        stored = dict(crystal.constraints._base_dict())

        def num(desc, value, width="92px"):
            return w.FloatText(value=value, description=desc, step=0.1,
                               layout=L(width=width), style={"description_width": "18px"})

        self.hkl = [num(n, float(v)) for n, v in zip("hkl", hkl)]
        self.energy = w.FloatText(value=energy, description="energy [eV]", step=10,
                                  layout=L(width="200px"), style={"description_width": "80px"})
        self.energy_btn = w.Button(description="beamline", tooltip="read the mono/machine energy",
                                   layout=L(width="80px"))
        self.energy_btn.on_click(self._read_energy)

        # constraints: value + fix box, initialised from the stored ones; min/max
        # filter the solutions (the circles' start from the solution ranges)
        self.cons = {}
        self.filters = {}
        ranges = crystal._ranges()
        rows = [w.HTML("<span style='font-size:11px;color:#888'>"
                       "<span style='display:inline-block;width:62px'></span>value"
                       "<span style='display:inline-block;width:44px'></span>min"
                       "<span style='display:inline-block;width:28px'></span>max</span>")]
        for name in CIRCLES + VIRTUAL_CONSTRAINTS:
            val = stored.get(name)
            box = w.Checkbox(value=val is not None, description="fix", indent=False, layout=L(width="44px"))
            fld = w.FloatText(value=float(val) if val is not None else 0.0, layout=L(width="76px"))
            lo, hi = ranges.get(name, ("", ""))
            self.filters[name] = (w.Text(value=_num_text(lo), placeholder="min", layout=L(width="52px")),
                                  w.Text(value=_num_text(hi), placeholder="max", layout=L(width="52px")))
            self.cons[name] = (fld, box)
            rows.append(w.HBox([w.Label(name, layout=L(width="62px")), fld, *self.filters[name], box]))
        for name in BOOL_CONSTRAINTS:
            box = w.Checkbox(value=bool(stored.get(name)), description=name, indent=False, layout=L(width="150px"))
            self.cons[name] = (None, box)
            rows.append(box)
        h2_stored = stored.get("h2")
        self.two = w.Checkbox(value=bool(h2_stored) or h2 is not None, description="keep a second reflection in Bragg",
                              indent=False, layout=L(width="260px"))
        h2v = h2 if h2 is not None else (h2_stored or [0.0, 0.0, 1.0])
        self.h2 = [num(n, float(v)) for n, v in zip("hkl", h2v)]
        self.detector = w.Dropdown(options=[("detector on reflection 1", "h1"), ("detector between both", "between")],
                                   value=stored.get("h2_detector") or "h1", layout=L(width="240px"))
        self.only_limits = w.Checkbox(value=False, description="only solutions within motor limits", indent=False,
                                      layout=L(width="260px"))
        store = w.Button(description="store constraints", tooltip="write these constraints and the "
                         "circles' min/max (solution ranges) to the RecSpace (used by h, k, l, hkl)",
                         layout=L(width="140px"))
        store.on_click(self._store)
        reload_ = w.Button(description="reload stored", layout=L(width="110px"))
        reload_.on_click(self._reload)
        self.msg = w.HTML()
        self.presets = _presets(crystal)
        self.preset = w.Dropdown(options=[("load a setup ...", "")] + [(n, n) for n in self.presets],
                                 layout=L(width="300px"))
        self.preset.observe(lambda ch: self._load_preset(ch["new"]), names="value")

        summary = w.HTML(f"<pre style='font-size:11px;line-height:1.25'>{html.escape(crystal.summary())}</pre>")
        controls = w.VBox([
            w.HTML(f"<b>{html.escape(crystal.name)}</b> &mdash; target reflection"),
            w.HBox(self.hkl), w.HBox([self.energy, self.energy_btn]),
            w.HTML("<b>constraints</b> <span style='font-size:11px;color:#888'>(3 of them: circles "
                   "and/or virtual angles; min/max filter the solutions)</span>"),
            self.preset, w.VBox(rows),
            w.HTML("<b>two reflections</b>"), self.two, w.HBox(self.h2), self.detector,
            self.only_limits, w.HBox([store, reload_]), self.msg,
            w.Accordion(children=[summary], titles=("crystal",)) if hasattr(w.Accordion, "titles") else summary,
        ], layout=L(width="350px", flex="0 0 auto"))

        self.buttons = w.HBox([])
        self.table = w.HTML()
        self.solve_msg = w.HTML()
        stored_opts = [("current position", self.SEL_CURRENT), ("all zero", self.SEL_ZERO),
                       ("simulated angles", self.SEL_SIM)] + [
            (f"stored {r.get('tag') or i}: {r['hkl']}", f"refl:{i}") for i, r in enumerate(crystal.reflections())]
        self.show_stored = w.Dropdown(options=stored_opts, description="view", layout=L(width="300px"),
                                      style={"description_width": "40px"})
        self.show_stored.observe(lambda ch: self.select(ch["new"]), names="value")

        # simulator: any six circle angles -> hkl, virtual angles, limits, 3D view
        self.sim = {c: w.FloatText(value=0.0, description=c, step=0.5, layout=L(width="150px"),
                                   style={"description_width": "48px"}) for c in CIRCLES}
        from_view = w.Button(description="from view", tooltip="start from the angles the 3D view shows "
                             "(a solution, a stored reflection, ...)", layout=L(width="96px"))
        from_view.on_click(self._sim_from_view)
        from_current = w.Button(description="current", tooltip="start from the diffractometer position",
                                layout=L(width="80px"))
        from_current.on_click(lambda _b: self._sim_from(self.SEL_CURRENT))
        self.sim_out = w.HTML()
        sim_box = w.VBox([
            w.HTML("<b>simulate angles</b> &mdash; any position: which hkl is in Bragg condition there"),
            w.HBox([w.VBox([self.sim[c] for c in CIRCLES[:3]]), w.VBox([self.sim[c] for c in CIRCLES[3:]])]),
            w.HBox([from_view, from_current]), self.sim_out,
        ], layout=L(margin="14px 0 0 0"))
        for fld in self.sim.values():
            fld.observe(self._sim_changed, names="value")

        table_panel = w.VBox([
            w.HTML("<b>solutions</b> &mdash; click a column to show it in the 3D view"),
            self.solve_msg, self.buttons, self.table, self.show_stored, sim_box,
        ], layout=L(flex="0 0 auto", margin="0 0 0 12px"))

        view = w.HTML(f'<iframe srcdoc="{html.escape(VIEWER_HTML.read_text())}" '
                      'style="width:100%;height:720px;border:1px solid #ddd;"></iframe>', layout=L(width="100%"))
        self.payload_holder = w.HTML()
        view_panel = w.VBox([view, self.payload_holder],
                            layout=L(flex="1 1 480px", min_width="0", margin="0 0 0 12px"))

        self.widget = w.HBox([controls, table_panel, view_panel],
                             layout=L(flex_flow="row wrap", width="100%", align_items="flex-start"))
        watched = [*self.hkl, self.energy, self.two, *self.h2, self.detector, self.only_limits]
        for fld, box in self.cons.values():
            watched += [box] + ([fld] if fld is not None else [])
        for lo, hi in self.filters.values():
            watched += [lo, hi]
        for x in watched:
            x.observe(self.recompute, names="value")
        self.recompute()

    # --- inputs ---------------------------------------------------------------

    def constraint_dict(self):
        cons = {}
        for name, (fld, box) in self.cons.items():
            if name in BOOL_CONSTRAINTS:
                cons[name] = True if box.value else None
            else:
                cons[name] = float(fld.value) if box.value else None
        cons["h2"] = [float(x.value) for x in self.h2] if self.two.value else None
        cons["h2_detector"] = self.detector.value
        return cons

    def _read_energy(self, _b=None):
        try:
            self.energy.value = float(self.c.get_energy())
        except Exception as e:
            self.msg.value = f"<span style='color:#b00'>{html.escape(str(e))}</span>"

    def filter_ranges(self):
        """{name: (min or None, max or None)} of the filled-in min/max boxes."""
        out = {}
        for name, (lo, hi) in self.filters.items():
            a, b = _parse_num(lo.value), _parse_num(hi.value)
            if a is not None or b is not None:
                out[name] = (a, b)
        return out

    def _passes(self, sol, ranges):
        for name, (a, b) in ranges.items():
            v = sol.get(name)
            if v is None or not np.isfinite(v):
                continue
            if name in CIRCLES and a is not None and b is not None:
                ok = rc.wrap_into_limits(float(v), (a, b), strict=False) is not None   # as solution_ranges
            else:
                ok = (a is None or v >= a) and (b is None or v <= b)
            if not ok:
                return False
        return True

    def _store(self, _b=None):
        base = self.c.constraints._base_dict
        base.set_target_value({**base(), **self.constraint_dict()}).wait()
        text = "stored as RecSpace constraints"
        if self.c._solution_ranges is not None:
            ranges = {n: [a, b] for n, (a, b) in self.filter_ranges().items()
                      if n in CIRCLES and a is not None and b is not None}
            self.c._solution_ranges.set_target_value(ranges).wait()
            text += ", circle min/max as solution ranges" if ranges else ", no solution ranges"
            skipped = [n for n, (a, b) in self.filter_ranges().items() if n not in ranges]
            if skipped:
                text += f" (only used here: {', '.join(skipped)})"
        self.msg.value = f"<span style='color:#282;font-size:11px'>{html.escape(text)}</span>"

    def _load_preset(self, name):
        if not name:
            return
        preset = self.presets[name]
        cons, ranges = preset.get("constraints", {}), preset.get("ranges") or {}
        self._suspend = True
        try:
            for n, (fld, box) in self.cons.items():
                val = cons.get(n)
                box.value = bool(val) if n in BOOL_CONSTRAINTS else val is not None
                if fld is not None and val is not None:
                    fld.value = float(val)
            for n, (lo, hi) in self.filters.items():
                lo.value, hi.value = (_num_text(v) for v in ranges.get(n, ("", "")))
            self.two.value = False
            self.preset.value = ""
        finally:
            self._suspend = False
        self.msg.value = (f"<span style='color:#555;font-size:11px'>setup {html.escape(name)}: "
                          f"{html.escape(preset.get('description', ''))} (used here; store to keep)</span>")
        self.recompute()

    def _reload(self, _b=None):
        stored = self.c.constraints._base_dict()
        ranges = self.c._ranges()
        self._suspend = True
        try:
            for name, (lo, hi) in self.filters.items():
                lo.value, hi.value = (_num_text(v) for v in ranges.get(name, ("", "")))
            for name, (fld, box) in self.cons.items():
                val = stored.get(name)
                box.value = bool(val) if name in BOOL_CONSTRAINTS else val is not None
                if fld is not None and val is not None:
                    fld.value = float(val)
            self.two.value = bool(stored.get("h2"))
            if stored.get("h2"):
                for x, v in zip(self.h2, stored["h2"]):
                    x.value = float(v)
            self.detector.value = stored.get("h2_detector") or "h1"
        finally:
            self._suspend = False
        self.recompute()

    # --- solving and display ------------------------------------------------------

    def recompute(self, *_):
        if self._suspend:
            return
        hkl = [float(x.value) for x in self.hkl]
        cons = self.constraint_dict()
        energy = float(self.energy.value)
        self.sols = []
        try:
            sols = rc.solve(self.c._ub(), energy, hkl, cons, self.c._reference, self.c._surface)
            two = cons["h2"] is not None
            for s in sols:
                s["in limits"] = _limits_ok(self.c, s)
                if two:
                    sp = rc.spots(self.c._ub(), energy, s, [hkl, cons["h2"]])
                    s["separation"], s["tdet max [mm]"] = sp["separation"], sp["tdet_max"]
            n_all = len(sols)
            if self.only_limits.value:
                sols = [s for s in sols if s["in limits"]]
            ranges = self.filter_ranges()
            sols = [s for s in sols if self._passes(s, ranges)]
            self.sols = sols
            hidden = f" ({n_all - len(sols)} hidden by the limits or min/max filters)" if n_all > len(sols) else ""
            self.solve_msg.value = ("" if sols else
                                    f"<span style='color:#a60;font-size:12px'>no solution{hidden}</span>")
        except Exception as e:
            self.solve_msg.value = f"<span style='color:#b00;font-size:12px'>{html.escape(str(e))}</span>"
        if isinstance(self.selected, int) and self.selected >= len(self.sols):
            self.selected = self.SEL_CURRENT
        self._rebuild_buttons()
        self.select(self.selected)

    # --- simulator ---------------------------------------------------------------

    def sim_angles(self):
        return {c: float(self.sim[c].value) for c in CIRCLES}

    def _sim_from(self, sel):
        try:
            angles = self._view(sel)[0]
        except Exception as e:
            self.sim_out.value = f"<span style='color:#b00;font-size:12px'>{html.escape(str(e))}</span>"
            return
        self._sim_suspend = True
        try:
            for c in CIRCLES:
                self.sim[c].value = round(float(angles.get(c, 0.0)), 4)
        finally:
            self._sim_suspend = False
        self.select(self.SEL_SIM)

    def _sim_from_view(self, _b=None):
        if self.selected != self.SEL_SIM:
            self._sim_from(self.selected)

    def _sim_changed(self, *_):
        if not self._sim_suspend:
            self.select(self.SEL_SIM)

    def _render_sim(self):
        angles, energy = self.sim_angles(), float(self.energy.value)
        try:
            ub = self.c._ub()
            hkl = rc.calc_hkl(ub, energy, angles)
            virt = rc.pseudo_angles(ub, angles, self.c._reference, self.c._surface)
        except Exception as e:
            self.sim_out.value = f"<span style='color:#b00;font-size:12px'>{html.escape(str(e))}</span>"
            return
        near = np.round(hkl)
        rows = [("h k l", "&nbsp;&nbsp;".join(f"{x:.3f}" for x in hkl)),
                ("nearest", "(" + " ".join(f"{int(x)}" for x in near + 0.0) + f")&nbsp; off by "
                 f"{np.linalg.norm(hkl - near):.3f}")]
        rows += [(r, _fmt(virt.get(r))) for r in TABLE_ROWS if r not in CIRCLES and r in virt]
        ok = _limits_ok(self.c, angles)
        rows.append(("in limits", f"<span style='color:#b33'>no</span>" if ok is False else _fmt(ok)))
        self.sim_out.value = ("<table style='border-collapse:collapse;font-size:12px'>" + "".join(
            f"<tr><td style='width:90px;text-align:right;padding:1px 6px;font-weight:600'>{html.escape(r)}</td>"
            f"<td style='padding:1px 6px;font-variant-numeric:tabular-nums'>{v}</td></tr>" for r, v in rows)
            + "</table>")

    def _rebuild_buttons(self):
        w = self.w
        btns = []
        for i in range(len(self.sols)):
            b = w.Button(description=f"sol. {i}", layout=w.Layout(width="86px", margin="0"))
            b.on_click(lambda _b, i=i: self.select(i))
            b._sel = i
            btns.append(b)
        self.buttons.children = [w.Label("", layout=w.Layout(width="90px"))] + btns

    def _render_table(self):
        if not self.sols:
            self.table.value = ""
            return
        extra = [k for k in ("in limits", "separation", "tdet max [mm]") if k in self.sols[0]]
        rows = [r for r in TABLE_ROWS if any(r in s for s in self.sols)] + extra
        sel = self.selected
        bg = lambda i: "background:#cfe8fb;color:#000;" if i == sel else ""
        body = []
        for r in rows:
            cells = []
            for i, s in enumerate(self.sols):
                v = s.get(r)
                style = bg(i) + ("color:#b33;" if r == "in limits" and v is False else "")
                cells.append(f"<td style='width:86px;text-align:right;padding:1px 6px;"
                             f"font-variant-numeric:tabular-nums;{style}'>{_fmt(v)}</td>")
            body.append(f"<tr><td style='width:90px;text-align:right;padding:1px 6px;font-weight:600'>"
                        f"{html.escape(r)}</td>{''.join(cells)}</tr>")
        self.table.value = "<table style='border-collapse:collapse;font-size:12px'>" + "".join(body) + "</table>"

    def select(self, sel):
        self.selected = sel
        for b in self.buttons.children:
            if hasattr(b, "_sel"):
                b.button_style = "info" if b._sel == sel else ""
        if not isinstance(sel, int) and sel != self.show_stored.value:
            self.show_stored.value = sel          # fires select(sel) once more, harmless
        self._render_table()
        self._render_sim()
        self._push()

    def _view(self, sel):
        """(angles, label, energy, target reflections) of a view selection."""
        energy = float(self.energy.value)
        hkl = [float(x.value) for x in self.hkl]
        targets = [hkl, [float(x.value) for x in self.h2]] if self.two.value else [hkl]
        if isinstance(sel, int):
            return self.sols[sel], f"sol. {sel}", energy, targets
        if sel == self.SEL_ZERO:
            return {}, "zero", energy, targets
        if sel == self.SEL_SIM:
            return self.sim_angles(), "simulated", energy, targets
        if isinstance(sel, str) and sel.startswith("refl:"):
            r = self.c.reflections()[int(sel[5:])]
            return rc.position_to_angles(r["position"]), f"stored {r['hkl']}", r["energy"], [r["hkl"]]
        return self.c._angles(), "current", energy, targets

    def _push(self):
        try:
            angles, label, energy, targets = self._view(self.selected)
            msg = payload(self.c, angles, energy, label, targets, n=self._pushes)
        except Exception as e:
            self.solve_msg.value = f"<span style='color:#b00;font-size:12px'>{html.escape(str(e))}</span>"
            return
        self._pushes += 1
        self.payload_holder.value = ('<span id="diff3d-payload" style="display:none">'
                                     + html.escape(json.dumps(msg)) + "</span>")
