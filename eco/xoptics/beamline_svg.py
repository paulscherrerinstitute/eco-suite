"""Render a beamline / vacuum model as a clickable SVG control panel.

Turns the positioned components of an
:class:`~eco.xoptics.beamline_assembly.Beamline` into a schematic panel of
**standardized device symbols** (valves, gauges, pumps, mirrors, slits, ...),
grouped by section and ordered by position. Every symbol is a clickable SVG
group carrying an ``onclick="// eco: <path>"`` marker, so the existing viewer in
:mod:`eco.utilities.svg_interactor` -- which works in *both* a Jupyter notebook
(Dash) and a native Qt window (WebKitGTK), picked automatically -- runs
``<assembly>.<path>`` in the live session when the symbol is clicked (see
:meth:`Beamline.svg_panel`).

This module only builds SVG text; it has no EPICS/Qt/notebook dependency, so it
can be unit-tested and previewed on its own. Live open/closed colouring, when
requested, is a snapshot taken by the caller and passed in via each item's
``state`` field.

Symbol legend (standardized per ``kind``):

    valve/shutter/stopper  ⧔ open / ⧓ closed
    gauge                  Ⓟ
    pump                   ⊚
    mirror/mono/optic      diamond ◆
    slit                   ] [
    attenuator             hatched square
    profile/diagnostic     ◇ with cross
    sample                 ☉ target
    (other)                small square
"""

# per-kind fill colours, aligned with Beamline.KIND_COLORS
KIND_COLORS = {
    "source": "#444444", "mirror": "#1f77b4", "mono": "#1f77b4", "optic": "#1f77b4",
    "slit": "#2ca02c", "attenuator": "#bcbd22", "shutter": "#d62728",
    "stopper": "#d62728", "valve": "#8c564b", "gauge": "#9467bd", "pump": "#7f7f7f",
    "vacuum": "#8c564b", "profile": "#17becf", "diagnostic": "#17becf",
    "chopper": "#e377c2", "stage": "#7f7f7f", "sample": "#000000", "marker": "#999999",
}
_DEFAULT_COLOR = "#555555"

# "good"/"flagged" state -> colour, shared by two uses: (a) the glyph fill for
# valve/shutter/stopper/gauge kinds (open/in-range = green, closed/alarm =
# red), and (b) the small outline ring drawn around any blocking kind's symbol
# (see `_symbol`). None -> `_symbol` falls back to the plain kind colour / no
# ring is drawn (state unknown/not read live).
_STATE_COLOR = {True: "#2e7d32", False: "#c62828", None: None}


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _symbol(kind, cx, cy, r, color, state):
    """Return SVG markup for one standardized device symbol centred at
    (cx, cy) with radius ~r, filled `color` by default; `state` (True/False/
    None) adds an open/closed ring and, for valve/shutter/stopper/gauge/
    profile/diagnostic, also recolours the glyph itself (green/red/`color`)
    -- see `_STATE_COLOR`. The *meaning* of True/False is caller-defined but
    always "green case" / "red case": beam passes for a valve/shutter/stopper
    or an in/out (`is_in_beam`) device like a profile screen, in-range for a
    gauge's alarm severity -- read live by `Beamline._svg_items`, which is
    also where `is_in_beam`'s inverted sense (True = blocking = NOT "open")
    gets resolved into this same True="green"/False="red" convention."""
    p = []
    # green for the "good" state, red for the "flagged" state, else the plain
    # kind colour -- shared by both valve-open/closed and gauge-alarm coding
    # so a `state=None` (never read, or genuinely unknown) always falls back
    # to the same neutral, un-opinionated colour rather than guessing.
    glyph_color = _STATE_COLOR.get(state) or color
    if kind in ("valve", "shutter", "stopper"):
        # ⧓ closed, ⧔ open (state is None -> unknown -> shown as open glyph,
        # same "not confirmed closed" default the old bowtie fill used)
        glyph = "⧓" if state is False else "⧔"
        p.append(
            f'<text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central" '
            f'font-size="{2*r:.1f}" fill="{glyph_color}">{glyph}</text>'
        )
    elif kind == "gauge":
        p.append(
            f'<text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central" '
            f'font-size="{2*r:.1f}" fill="{glyph_color}">Ⓟ</text>'
        )
    elif kind in ("pump", "vacuum"):
        p.append(
            f'<text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central" '
            f'font-size="{2*r:.1f}" fill="{color}">⊚</text>'
        )
    elif kind in ("mirror", "mono", "optic"):
        p.append(f'<path d="M{cx},{cy-r} L{cx+r},{cy} L{cx},{cy+r} L{cx-r},{cy} Z" fill="{color}" stroke="{color}"/>')
    elif kind == "slit":
        p.append(f'<rect x="{cx-r-2}" y="{cy-r}" width="4" height="{2*r}" fill="{color}"/>')
        p.append(f'<rect x="{cx+r-2}" y="{cy-r}" width="4" height="{2*r}" fill="{color}"/>')
    elif kind == "attenuator":
        p.append(f'<rect x="{cx-r}" y="{cy-r}" width="{2*r}" height="{2*r}" fill="none" stroke="{color}" stroke-width="2"/>')
        p.append(f'<line x1="{cx-r}" y1="{cy+r}" x2="{cx+r}" y2="{cy-r}" stroke="{color}" stroke-width="1.5"/>')
        p.append(f'<line x1="{cx-r}" y1="{cy}" x2="{cx}" y2="{cy-r}" stroke="{color}" stroke-width="1"/>')
    elif kind in ("profile", "diagnostic"):
        # in/out (is_in_beam) devices, e.g. an inserted profile screen -- same
        # green(out)/red(in-beam)/plain-colour(unknown) coding as valve/gauge
        p.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{glyph_color}" stroke-width="2"/>')
        p.append(f'<line x1="{cx-r}" y1="{cy}" x2="{cx+r}" y2="{cy}" stroke="{glyph_color}" stroke-width="1"/>')
        p.append(f'<line x1="{cx}" y1="{cy-r}" x2="{cx}" y2="{cy+r}" stroke="{glyph_color}" stroke-width="1"/>')
    elif kind == "sample":
        p.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="2"/>')
        p.append(f'<circle cx="{cx}" cy="{cy}" r="{r*0.35:.1f}" fill="{color}"/>')
    else:
        p.append(f'<rect x="{cx-r*0.8:.1f}" y="{cy-r*0.8:.1f}" width="{1.6*r:.1f}" height="{1.6*r:.1f}" rx="2" fill="{color}"/>')
    # open/closed status ring
    ring = _STATE_COLOR.get(state)
    if ring:
        p.append(f'<circle cx="{cx}" cy="{cy}" r="{r+5}" fill="none" stroke="{ring}" stroke-width="2.5"/>')
    return "".join(p)


def build_beamline_svg(items, title="beamline", ref="source", cols=8,
                       cell_w=140, cell_h=82, symbol_r=15):
    """Build the panel SVG from `items` and return it as a string.

    `items` is a list of dicts, each with keys:
        relpath     -- attribute path from the panel's root assembly (the eco
                       command run on click), e.g. "endstation.vptm140_700"
        label       -- short visible label (the component name)
        kind        -- device kind (selects symbol + colour)
        z           -- position (for ordering / display; may be None)
        section     -- section name (groups cells under a header)
        state       -- True (green)/False (red)/None (plain kind colour):
                       open-state for valve/shutter/stopper, in-range alarm
                       severity for gauge, or "clear"/"in beam" for profile/
                       diagnostic (`is_in_beam` devices)
    Cells are grouped by section (in first-seen order) and, within a section,
    ordered by `z`.
    """
    unit = "m" if ref == "source" else "mm"
    # group by section, preserve first-seen order, sort each group by z
    order, groups = [], {}
    for it in items:
        sec = it.get("section") or ""
        if sec not in groups:
            groups[sec] = []
            order.append(sec)
        groups[sec].append(it)
    for sec in order:
        groups[sec].sort(key=lambda it: (it.get("z") is None, it.get("z")))

    margin = 20
    header_h = 30
    width = margin * 2 + cols * cell_w
    parts = []
    y = margin + 34  # leave room for the title
    total_cells = 0
    for sec in order:
        cells = groups[sec]
        total_cells += len(cells)
        # section header band
        parts.append(
            f'<rect x="{margin}" y="{y}" width="{width-2*margin}" height="{header_h}" '
            f'fill="#eceff4" stroke="#c8ccd4"/>'
        )
        parts.append(
            f'<text x="{margin+8}" y="{y+20}" font-family="sans-serif" font-size="14" '
            f'font-weight="bold" fill="#2e3440">{_esc(sec)} '
            f'<tspan font-weight="normal" fill="#6b7280">({len(cells)})</tspan></text>'
        )
        y += header_h + 6
        # cells
        for i, it in enumerate(cells):
            col = i % cols
            if col == 0 and i > 0:
                y += cell_h
            cx = margin + col * cell_w + cell_w / 2
            cy = y + symbol_r + 8
            color = KIND_COLORS.get(it["kind"], _DEFAULT_COLOR)
            zlabel = "" if it.get("z") is None else f'{it["z"]:.1f} {unit}'
            cmd = _esc(it["relpath"])
            # a clickable group: onclick marker (authoritative) + <desc> fallback
            parts.append(f'<g style="cursor:pointer" onclick="// eco: {cmd}">')
            parts.append(f'<desc>{cmd}</desc>')
            parts.append(f'<title>{cmd}</title>')
            # transparent hit-area so the whole cell is clickable
            parts.append(
                f'<rect x="{margin+col*cell_w+4}" y="{y}" width="{cell_w-8}" '
                f'height="{cell_h-6}" fill="#ffffff" fill-opacity="0.01" rx="4"/>'
            )
            parts.append(_symbol(it["kind"], cx, cy, symbol_r, color, it.get("state")))
            parts.append(
                f'<text x="{cx:.1f}" y="{cy+symbol_r+16:.1f}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="11" fill="#2e3440">{_esc(it["label"])}</text>'
            )
            if zlabel:
                parts.append(
                    f'<text x="{cx:.1f}" y="{cy+symbol_r+28:.1f}" text-anchor="middle" '
                    f'font-family="sans-serif" font-size="9" fill="#6b7280">{zlabel}</text>'
                )
            parts.append("</g>")
        y += cell_h + 10

    height = y + margin
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#f7f8fa"/>'
        f'<text x="{margin}" y="{margin+16}" font-size="18" font-weight="bold" '
        f'fill="#2e3440">{_esc(title)} '
        f'<tspan font-size="12" font-weight="normal" fill="#6b7280">'
        f'({total_cells} components, click to inspect)</tspan></text>'
    )
    return head + "".join(parts) + "</svg>"
