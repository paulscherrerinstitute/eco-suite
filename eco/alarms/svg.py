"""Alarm tile panel (SVG) for `AlarmPanel.show()` / `AlarmSystem.show()`.

One tile per alarm, grouped by section: label, live value, range, state and
time in state; fill colour by state, a heavy border for ``critical`` alarms,
a thin one for ``warning``, a dashed one for ``display``. Click contract as in
:mod:`eco.xoptics.beamline_svg` (``onclick="// eco: <relpath>"`` plus
``<desc>``), so `svg_interactor` runs ``<panel>.<relpath>`` on click.
"""

from __future__ import annotations

from eco.alarms import tracker as tr

# (fill, text) per state
_COLORS = {
    tr.ACTIVE: ("#d7263d", "#ffffff"),
    tr.CLEARING: ("#c06cd6", "#ffffff"),
    tr.ACKED: ("#f2a3a8", "#5a0c14"),
    tr.PENDING: ("#ffd166", "#3d2c00"),
    tr.STATE_OK: ("#bfe5c4", "#123d1b"),
    tr.STATE_UNKNOWN: ("#d9dce1", "#4b5563"),
    tr.INHIBITED: ("#cfe0f5", "#1e3a5f"),
    tr.SHELVED: ("#d3f0f0", "#134e4a"),
    tr.DISABLED: ("#eef0f3", "#9aa1ab"),
    None: ("#ffffff", "#2e3440"),
}

_BORDER = {
    "critical": ("#7a0010", 3.0, None),
    "warning": ("#6b4e00", 1.5, None),
    "display": ("#9aa1ab", 1.0, "4 3"),
}


def _esc(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_alarm_svg(tiles, title="alarms", cols=6, tile_w=168, tile_h=86, header=None):
    """`tiles`: dicts with relpath, label, section, state, value, range,
    level, duration, count (see `eco.elements.alarm.alarm_tile`).
    `header`: optional extra line under the title (armed / on call)."""
    order, groups = [], {}
    for t in tiles:
        sec = t.get("section") or ""
        if sec not in groups:
            groups[sec] = []
            order.append(sec)
        groups[sec].append(t)

    margin, gap, sec_h = 16, 8, 26
    width = margin * 2 + cols * (tile_w + gap) - gap
    parts = []
    y = margin + 30 + (18 if header else 0)
    for sec in order:
        items = groups[sec]
        n_bad = sum(1 for t in items if t.get("state") in tr.RAISED_STATES)
        parts.append(
            f'<text x="{margin}" y="{y + 17}" font-size="14" font-weight="bold" '
            f'fill="#2e3440">{_esc(sec)} <tspan font-weight="normal" fill="#6b7280">'
            f'({len(items)}{", %d raised" % n_bad if n_bad else ""})</tspan></text>'
        )
        y += sec_h
        for i, t in enumerate(items):
            col = i % cols
            if col == 0 and i > 0:
                y += tile_h + gap
            x = margin + col * (tile_w + gap)
            fill, ink = _COLORS.get(t.get("state"), _COLORS[None])
            bcol, bw, dash = _BORDER.get(t.get("level"), _BORDER["display"])
            cmd = _esc(t["relpath"])
            parts.append(f'<g style="cursor:pointer" onclick="// eco: {cmd}">')
            parts.append(f"<desc>{cmd}</desc><title>{cmd}</title>")
            parts.append(
                f'<rect x="{x}" y="{y}" width="{tile_w}" height="{tile_h}" rx="6" '
                f'fill="{fill}" stroke="{bcol}" stroke-width="{bw}"'
                + (f' stroke-dasharray="{dash}"' if dash else "")
                + "/>"
            )
            label = t.get("label") or ""
            if len(label) > 22:
                label = label[:21] + "…"
            parts.append(
                f'<text x="{x + 8}" y="{y + 17}" font-size="12" font-weight="bold" '
                f'fill="{ink}">{_esc(label)}</text>'
            )
            if t.get("value") is not None:
                val = str(t["value"])
                # numbers big; text (status messages) small and cut to fit
                size, room = (17, 15) if len(val) <= 15 else (10, 27)
                if len(val) > room:
                    val = val[: room - 1] + "…"
                parts.append(
                    f'<text x="{x + 8}" y="{y + 42}" font-size="{size}" fill="{ink}">'
                    f"{_esc(val)}</text>"
                )
            parts.append(
                f'<text x="{x + 8}" y="{y + 60}" font-size="10" fill="{ink}" '
                f'fill-opacity="0.8">{_esc(t.get("range", ""))}</text>'
            )
            st = t.get("state")
            st_txt = "" if st is None else (st.upper() if st in tr.RAISED_STATES else st)
            dur = t.get("duration") or ""
            parts.append(
                f'<text x="{x + 8}" y="{y + 77}" font-size="10" font-weight="bold" '
                f'fill="{ink}">{_esc(st_txt)}'
                + (f' <tspan font-weight="normal">{_esc(dur)}</tspan>' if dur else "")
                + "</text>"
            )
            if t.get("count"):
                parts.append(
                    f'<text x="{x + tile_w - 8}" y="{y + 77}" font-size="10" '
                    f'text-anchor="end" fill="{ink}">{int(t["count"])}×</text>'
                )
            if t.get("level") == "critical":
                parts.append(
                    f'<text x="{x + tile_w - 8}" y="{y + 17}" font-size="9" '
                    f'text-anchor="end" font-weight="bold" fill="{ink}">CRIT</text>'
                )
            parts.append("</g>")
        y += tile_h + gap + 10

    height = y + margin
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#f7f8fa"/>'
        f'<text x="{margin}" y="{margin + 18}" font-size="18" font-weight="bold" '
        f'fill="#2e3440">{_esc(title)}</text>'
    )
    if header:
        head += (
            f'<text x="{margin}" y="{margin + 38}" font-size="12" fill="#4b5563">'
            f"{_esc(header)}</text>"
        )
    return head + "".join(parts) + "</svg>"
