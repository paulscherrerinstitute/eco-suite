"""Bernina consolidated prepump / venting system as an eco object.

Models the Bernina prepump system (spec: "Prepump system on turbo prepump
line"): a single high-performance **Roots pump** feeds a common **pump line**
(pressure gauge ``Gp``); a separate **GN2 vent line** (low overpressure)
allows controlled venting. Any number of **prepump/venting channels** branch
off, each with exactly

* a **pump valve** ``valve_prevac`` (P: channel -> common pump line),
* a **vent valve** ``valve_vent`` (V: channel -> vent line),
* its own gauge ``gauge`` (G).

(A channel's turbo pump, if it has one, is served by the prepump system but
is not part of it -- it is exchangeable and plays no role in the valve
choreography, see the spec: "does not belong to the prepump system" -- so it
is deliberately not modelled here at all.)

There is only this one channel scheme (the spec's dual-access "prepump
access" variant for fast sample exchange is a separate, higher-level
procedure and not modelled here).

Because ``Gp`` rises while a channel is being pumped down, every *other*
channel's pump valve must be closed meanwhile, so it doesn't get pumped
against too high a pressure -- that cross-channel interlock plus the actual
pump-down/vent choreography is :class:`PrepumpSystem`; :class:`PrepumpLine`
is just the dumb valve/gauge group for one channel.

.. note::
   The GN2 vent line's flow meter ``Fv`` (limits vent flow/overpressure) does
   not exist as a controllable/readable device yet. Nothing here uses it --
   see the commented-out notes in :func:`build_prepump_svg` and
   :meth:`PrepumpSystem.vent` for where it would plug in once it does.
"""

import time

from eco import Assembly

from .gauges import VacuumGauge
from .valves import Valve, PrePump


class PrepumpLine(Assembly):
    """One prepump/venting channel: pump valve ``valve_prevac`` (P), vent
    valve ``valve_vent`` (V), gauge ``gauge`` (G) -- see module docstring.

    Any of ``gauge`` / ``valve_prevac`` / ``valve_vent`` left ``None`` is
    simply not built, so a not-yet-cabled channel still works for the parts
    that exist.
    """

    def __init__(self, name=None, gauge=None, valve_prevac=None, valve_vent=None):
        super().__init__(name=name)
        if gauge is not None:
            self._append(VacuumGauge, gauge, name="gauge", is_status=True)
        if valve_prevac is not None:
            self._append(Valve, valve_prevac, name="valve_prevac", is_setting=True)
        if valve_vent is not None:
            self._append(Valve, valve_vent, name="valve_vent", is_setting=True)

    def pressure(self):
        """This channel's gauge pressure, or None if no gauge/reading."""
        gauge = getattr(self, "gauge", None)
        if gauge is None:
            return None
        try:
            return gauge.pressure.get_current_value()
        except Exception:
            return None

    def isolate(self):
        """Close this channel off from both the pump and vent line (safe state)."""
        if getattr(self, "valve_prevac", None) is not None:
            self.valve_prevac.close()
        if getattr(self, "valve_vent", None) is not None:
            self.valve_vent.close()

    def get_current_value(self, *args, **kwargs):
        """A compact {valve role: valve state, 'pressure': p} snapshot."""
        out = {}
        for role in ("valve_prevac", "valve_vent"):
            valve = getattr(self, role, None)
            out[role] = valve.get_current_value() if valve is not None else "?"
        out["pressure"] = self.pressure()
        return out


class PrepumpSystem(Assembly):
    """The whole Bernina prepump system: one Roots pump, the common pump-line
    gauge ``Gp``, and any number of :class:`PrepumpLine` channels.

    The pump-down/vent *procedures* live here because they must coordinate
    all channels (isolate the others while one is pumped, restore them
    afterwards).

    Parameters
    ----------
    gp : str
        PV base of the common pump-line gauge ``Gp``.
    roots_pump : str
        PV base of the Roots (backing) pump.
    lines : dict[str, PrepumpLine | dict]
        The channels, keyed by name. Each value is either a ready
        :class:`PrepumpLine` or a **plain dict of its keyword args**
        (``gauge``, ``valve_prevac``, ``valve_vent``). The dict form is
        what lets the whole system be described by one editable config
        literal -- see :data:`BERNINA_PREPUMP_CONFIG` /
        :func:`make_prepump_system`.
    p_target : float
        Pressure [mbar] at/below which a gauge counts as "pumped".
    p_vent_target : float
        Pressure [mbar] at/above which a channel counts as "vented" (close
        enough to atmosphere -- tune to what the channel's own gauge can
        actually read up to).
    """

    def __init__(self, name=None, gp=None, roots_pump=None, lines=None,
                 p_target=1e-3, p_vent_target=500.0, vent_line_name="GN2"):
        super().__init__(name=name)
        self.p_target = p_target
        self.p_vent_target = p_vent_target
        self.vent_line_name = vent_line_name
        if roots_pump is not None:
            self._append(PrePump, roots_pump, name="roots_pump", is_status=True)
        if gp is not None:
            self._append(VacuumGauge, gp, name="gp", is_status=True)
        self._line_names = []
        for lname, line in (lines or {}).items():
            if isinstance(line, dict):
                # build a PrepumpLine from a plain kwargs dict (config form);
                # the dict's own "name" (if any) is overridden by the key.
                line = PrepumpLine(**{**line, "name": lname})
            self._append(line, name=lname, is_status=True)
            self._line_names.append(lname)

    # ---- helpers -------------------------------------------------------
    @property
    def lines(self):
        return [getattr(self, n) for n in self._line_names]

    def _get_line(self, line):
        if isinstance(line, PrepumpLine):
            return line
        got = getattr(self, line, None)
        if not isinstance(got, PrepumpLine):
            raise KeyError(f"no prepump line named {line!r}")
        return got

    def gp_pressure(self):
        """Common pump-line pressure (Gp), or None."""
        gp = getattr(self, "gp", None)
        if gp is None:
            return None
        try:
            return gp.pressure.get_current_value()
        except Exception:
            return None

    def _is_pumped(self, pressure):
        return pressure is not None and pressure <= self.p_target

    def _is_vented(self, pressure):
        return pressure is not None and pressure >= self.p_vent_target

    # ---- procedures ------------------------------------------------------
    def pump_down(self, line, timeout=300.0, poll=1.0):
        """Pump down one channel.

        Close its vent valve, isolate every *other* channel (close their
        pump valves, so they don't get pumped against the rising ``Gp``),
        then open this channel's pump valve and wait for both ``Gp`` and its
        own gauge to reach ``p_target``. Reopens the other channels'
        pump valves again either way (success or timeout). Returns ``True``
        on success, ``False`` if `timeout` elapses first.
        """
        line = self._get_line(line)
        others = [l for l in self.lines if l is not line]

        print(f"[prepump] pump down '{line.name}': isolating other channels")
        for other in others:
            other.valve_prevac.close()

        line.valve_vent.close()
        line.valve_prevac.open()

        t0 = time.time()
        while not (self._is_pumped(self.gp_pressure()) and self._is_pumped(line.pressure())):
            if time.time() - t0 > timeout:
                print(f"[prepump] '{line.name}' pump down FAILED after {timeout}s "
                      f"(Gp={self.gp_pressure()}, G={line.pressure()}) -- isolating")
                line.valve_prevac.close()
                for other in others:
                    other.valve_prevac.open()
                return False
            time.sleep(poll)

        print(f"[prepump] '{line.name}' pumped down (Gp={self.gp_pressure()}, G={line.pressure()})")
        for other in others:
            other.valve_prevac.open()
        return True

    def vent(self, line, timeout=300.0, poll=1.0):
        """Vent one channel to atmosphere through the GN2 vent line.

        Close its pump valve, open its vent valve, and wait for its own
        gauge to reach ``p_vent_target``, then close the vent valve again.
        Returns ``True`` on success, ``False`` if `timeout` elapses first.

        # planned, once the vent line's flow meter Fv exists: open at full
        # flow first, then throttle down as the channel nears atmosphere
        # (see the spec's "low overpressure, limited flow" GN2 vent line);
        # for now the vent valve is simply open, then closed.
        """
        line = self._get_line(line)

        print(f"[prepump] venting '{line.name}'")
        line.valve_prevac.close()
        line.valve_vent.open()

        t0 = time.time()
        while not self._is_vented(line.pressure()):
            if time.time() - t0 > timeout:
                print(f"[prepump] '{line.name}' venting FAILED after {timeout}s (G={line.pressure()})")
                line.valve_vent.close()
                return False
            time.sleep(poll)

        print(f"[prepump] '{line.name}' vented (G={line.pressure()})")
        line.valve_vent.close()
        return True

    def isolate_all(self):
        """Safe state: close every channel off from pump and vent lines."""
        for line in self.lines:
            try:
                line.isolate()
            except Exception as e:
                print(f"  (could not isolate {line.name}: {e})")

    def get_current_value(self, *args, **kwargs):
        return {
            "Gp": self.gp_pressure(),
            **{n: getattr(self, n).get_current_value() for n in self._line_names},
        }

    def check(self, verbose=True):
        """Diagnose which configured components are missing or failed to
        connect, at *every* depth (roots pump, ``Gp``, and every channel's
        gauge/valves).

        A component that fails to connect (e.g. a typo'd or unreachable PV)
        does **not** raise -- it's swallowed where it happens and left as a
        ``FailedComponent`` placeholder (see ``Assembly._append(optional=True)``).
        ``Assembly._append`` mirrors that failure up through every ancestor
        too (so e.g. ``prepump.get_status()`` correctly reports a failure
        buried inside a channel's own valve/gauge, not just direct
        children) -- this method is the complementary, explicit form: a live
        re-probe of every configured component by path, in one table,
        including ones that were simply never given a PV (``"not configured"``,
        which isn't a failure).

        Returns a list of ``(path, status, detail)`` with ``status`` one of
        ``"ok"``, ``"FAILED"``, ``"not configured"``; prints a table if
        ``verbose``.
        """
        from eco.elements.assembly import FailedComponent

        rows = []

        def probe(path, obj):
            if obj is None:
                rows.append((path, "not configured", ""))
            elif isinstance(obj, FailedComponent):
                rows.append((path, "FAILED", f"{type(obj.exception).__name__}: {obj.exception}"))
            else:
                try:
                    rows.append((path, "ok", str(obj.get_current_value())))
                except Exception as e:
                    rows.append((path, "FAILED", f"{type(e).__name__}: {e}"))

        probe("roots_pump", getattr(self, "roots_pump", None))
        probe("gp", getattr(self, "gp", None))
        for lname in self._line_names:
            line = getattr(self, lname)
            probe(f"{lname}.gauge", getattr(line, "gauge", None))
            probe(f"{lname}.valve_prevac", getattr(line, "valve_prevac", None))
            probe(f"{lname}.valve_vent", getattr(line, "valve_vent", None))

        if verbose:
            for path, status, detail in rows:
                marker = {"ok": "OK     ", "FAILED": "FAILED ", "not configured": "-      "}[status]
                print(f"{marker}{path:32s} {detail}")
            n_failed = sum(1 for _, s, _ in rows if s == "FAILED")
            n_ok = sum(1 for _, s, _ in rows if s == "ok")
            print(f"\n{n_ok} ok, {n_failed} failed, "
                  f"{sum(1 for _, s, _ in rows if s == 'not configured')} not configured")
        return rows

    # ---- clickable schematic panel -------------------------------------
    # `_svg()` is the dynamic-panel hook `Assembly.show()` looks for -- see
    # its docstring. Underscore-prefixed (not part of the public namespace
    # a user tab-completes into) since `.show()`/`.show(live=True)` alone is
    # now enough to open the panel; `svg_panel()` below just stays around as
    # a discoverable, explicitly-named alias.
    def _svg(self, path=None, live=True):
        """Build a clickable P&ID-style SVG of the prepump system and return
        its file path (a temp file if ``path`` is None). `live=True` (the
        default -- this system is small enough that reading every valve's
        open/closed state and every gauge's alarm severity is cheap; revisit
        if that stops being true) colours valves green/red by open/closed
        state and gauges green/red by live EPICS alarm severity (touches
        EPICS); pass `live=False` for the plain, EPICS-free version. See
        :func:`build_prepump_svg`."""
        from eco.utilities.tempfiles import user_temp_svg_path

        svg_text = build_prepump_svg(self, live=live)
        if path is None:
            path = user_temp_svg_path("eco_prepump", self.name or id(self))
        with open(path, "w") as f:
            f.write(svg_text)
        return path

    def show(self, in_window=False, exclude_group_ids=None, live=True):
        """Same as `Assembly.show`, but defaults to `live=True` here (see
        `_svg`'s docstring for why this system's live reads are cheap enough
        to default on)."""
        return super().show(in_window=in_window, exclude_group_ids=exclude_group_ids, live=live)

    def svg_panel(self, in_window=False, live=True, exclude_group_ids=None):
        """Open the clickable prepump schematic in the interactive viewer
        (Jupyter cell, or a native Qt window with ``in_window=True``). Clicking a
        valve/gauge/pump inspects that device; clicking a channel's *pump*/*vent*
        label, or a valve's small green/red open/close dot, runs the
        corresponding action against this system. `live=True` (default)
        colours valves/gauges by their current state (touches EPICS); this is
        just a clearly-named alias for `show(...)` -- see `Assembly.show`."""
        return self.show(in_window=in_window, live=live, exclude_group_ids=exclude_group_ids)


# --------------------------------------------------------------------------
# Clickable schematic
# --------------------------------------------------------------------------
def _valve_open_state(valve):
    """True/False/None: is `valve` (a vacuum.Valve) open, closed, or its state
    unreadable right now."""
    try:
        return bool(valve.is_open.get_current_value())
    except Exception:
        return None


#: the two common lines' colours -- pump line (to the Roots pump) black,
#: vent line (to the GN2 vent) blue.
_PUMP_COLOR = "#1a1a1a"
_VENT_COLOR = "#1f4e8c"


def _fmt_pressure(p):
    """Compact scientific-notation string for a gauge reading, or "?" if
    `p` is None/unreadable."""
    if p is None:
        return "?"
    try:
        return f"{p:.1e}"
    except Exception:
        return "?"


def _gauge_ok_state(gauge):
    """True/False/None: is `gauge` (a VacuumGauge) reading in range (EPICS
    alarm severity NO_ALARM), in alarm (MINOR/MAJOR, i.e. some HIHI/LOLO/HIGH/
    LOW limit configured on the PV itself is tripped), or unreadable/no
    severity available at all (INVALID, disconnected). Uses whatever range is
    already configured on the PV -- no threshold invented/duplicated here."""
    try:
        severity = gauge.pressure.get_severity()
    except Exception:
        return None
    if severity == 0:
        return True
    if severity in (1, 2):
        return False
    return None


def build_prepump_svg(system, live=False):
    """Render `system` as a P&ID-style clickable SVG string, mirroring the
    spec figure's own orientation: each channel is drawn top-down as it would
    be plumbed -- channel label at the top (a not-yet-modelled chamber would
    connect there), its gauge just below, then its valves, down to the common
    Roots pump line (with Gp) and GN2 vent line at the bottom. Clicks carry
    ``onclick="// eco: <path>"`` markers resolved against the system alias by
    :mod:`eco.utilities.svg_interactor`. `live=True` colours each valve
    green/red by open/closed state and each gauge green/red by its live EPICS
    alarm severity (touches EPICS); default is the plain, EPICS-free version."""
    from eco.xoptics.beamline_svg import _symbol, _esc

    #: panel background colour, repeated here (matches the `<rect>` fill
    #: below) as an opaque disc behind every device glyph -- `_symbol`'s
    #: unicode glyphs (Ⓟ/⧔/⊚) are just outline strokes, so without this the
    #: common pump/vent lines passing behind a channel's icons show through
    #: their middle instead of looking like they end at the icon.
    _PANEL_BG = "#f7f8fa"

    def _opaque_symbol(kind, cx, cy, r, color, state):
        return (f'<circle cx="{cx}" cy="{cy}" r="{r+3}" fill="{_PANEL_BG}"/>'
                + _symbol(kind, cx, cy, r, color, state))

    line_names = system._line_names
    col_w = 190
    margin = 30
    x0 = margin + 120  # room for the roots pump on the left
    # +160 on the right for the Gp gauge and the "GN2 vent line" label
    width = x0 + max(1, len(line_names)) * col_w + 160
    label_y = 68
    gauge_y = 115
    pump_y = 195
    vent_y = 255
    btn_y = vent_y + 35
    height = btn_y + 55

    def _valve_buttons(cmd_prefix, vx, vy, r):
        """Small open/close dots pinned right next to a valve glyph at
        (vx, vy) of radius `r` -- offset just clear of the glyph itself
        (dx = r + 9) and stacked vertically (dy = +-7). No visible text (no
        room to set it legibly at this size) -- colour (green/red) plus a
        `<title>` hover tooltip carry the meaning, consistent with the
        glyphs above."""
        bx = vx + r + 9
        s = []
        for dy, cmd, col in ((-7, f"{cmd_prefix}.open()", "#2e7d32"),
                             (7, f"{cmd_prefix}.close()", "#c62828")):
            s.append(f'<g style="cursor:pointer" onclick="// eco: {_esc(cmd)}">'
                      f'<desc>{_esc(cmd)}</desc><title>{_esc(cmd)}</title>')
            s.append(f'<circle cx="{bx}" cy="{vy+dy}" r="5" fill="white" '
                      f'stroke="{col}" stroke-width="1.5"/>')
            s.append('</g>')
        return "".join(s)

    P = []
    P.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#f7f8fa"/>'
        f'<text x="{margin}" y="{margin}" font-size="18" font-weight="bold" fill="#2e3440">'
        f'{_esc(system.name or "prepump")} '
        f'<tspan font-size="12" font-weight="normal" fill="#6b7280">(click a device to inspect; '
        f'click pump/vent, or a valve\'s small green/red dot, to run)</tspan></text>'
    )
    # common pump line (black) + vent line (blue)
    x_end = x0 + len(line_names) * col_w
    P.append(f'<line x1="{x0-40}" y1="{pump_y}" x2="{x_end}" y2="{pump_y}" stroke="{_PUMP_COLOR}" stroke-width="3"/>')
    P.append(f'<line x1="{x0-40}" y1="{vent_y}" x2="{x_end+70}" y2="{vent_y}" stroke="{_VENT_COLOR}" stroke-width="2"/>')
    P.append(f'<text x="{x_end+6}" y="{vent_y-6}" font-size="11" fill="{_VENT_COLOR}">{_esc(system.vent_line_name)} vent line</text>')
    # planned: flow meter Fv on the vent line, once it exists as a device --
    # e.g. right after the last channel's branch:
    #   fx = x_end + 70
    #   P.append(_symbol("flowmeter", fx, vent_y, 11, _VENT_COLOR, None))
    #   P.append(f'<text x="{fx}" y="{vent_y+18}" text-anchor="middle" font-size="10">Fv</text>')
    # Roots pump (custom symbol) + Gp
    rpx, rpy = margin + 40, pump_y
    P.append(f'<g style="cursor:pointer" onclick="// eco: roots_pump"><desc>roots_pump</desc><title>roots_pump</title>')
    P.append(f'<circle cx="{rpx}" cy="{rpy}" r="26" fill="white" stroke="{_PUMP_COLOR}" stroke-width="2.5"/>')
    P.append(f'<text x="{rpx}" y="{rpy+6}" text-anchor="middle" font-size="18" fill="{_PUMP_COLOR}">R</text>')
    P.append(f'<text x="{rpx}" y="{rpy+42}" text-anchor="middle" font-size="11" fill="#2e3440">roots_pump</text>')
    P.append('</g>')
    P.append(f'<line x1="{rpx+26}" y1="{pump_y}" x2="{x0-40}" y2="{pump_y}" stroke="{_PUMP_COLOR}" stroke-width="3"/>')
    if getattr(system, "gp", None) is not None:
        gx = x_end + 30
        gp_label = _fmt_pressure(system.gp_pressure()) if live else "Gp"
        P.append(f'<g style="cursor:pointer" onclick="// eco: gp"><desc>gp</desc><title>gp (Gp)</title>')
        P.append(_opaque_symbol("gauge", gx, pump_y, 13, "#9467bd", _gauge_ok_state(system.gp) if live else None))
        P.append(f'<text x="{gx}" y="{pump_y-20}" text-anchor="middle" font-size="11" fill="#2e3440">{_esc(gp_label)}</text>')
        P.append('</g>')
        P.append(f'<line x1="{x_end}" y1="{pump_y}" x2="{gx}" y2="{pump_y}" stroke="{_PUMP_COLOR}" stroke-width="3"/>')

    def clickable(cmd, title, inner, cx, label, ly):
        s = [f'<g style="cursor:pointer" onclick="// eco: {_esc(cmd)}"><desc>{_esc(cmd)}</desc><title>{_esc(title)}</title>']
        s.append(inner)
        s.append(f'<text x="{cx}" y="{ly}" text-anchor="middle" font-size="10" fill="#2e3440">{_esc(label)}</text>')
        s.append('</g>')
        return "".join(s)

    for i, lname in enumerate(line_names):
        line = getattr(system, lname)
        cx = x0 + i * col_w + col_w / 2
        # channel label at the top -- stands in for the (not modelled) chamber
        P.append(f'<text x="{cx}" y="{label_y}" text-anchor="middle" font-size="12" font-weight="bold" fill="#2e3440">{_esc(lname)}</text>')
        # dashed stub above the gauge: this channel connects to its chamber up here
        P.append(f'<line x1="{cx}" y1="{label_y+10}" x2="{cx}" y2="{gauge_y-16}" '
                 f'stroke="#aaa" stroke-width="1.5" stroke-dasharray="3,3"/>')
        # branch stem down from the gauge through the valves to the common lines
        P.append(f'<line x1="{cx}" y1="{gauge_y}" x2="{cx}" y2="{vent_y}" stroke="#888" stroke-width="1.5"/>')
        # channel gauge, right below the chamber connection -- its live
        # pressure reading is the label, on this upper side of the branch
        if getattr(line, "gauge", None) is not None:
            gstate = _gauge_ok_state(line.gauge) if live else None
            glabel = _fmt_pressure(line.pressure()) if live else "G"
            P.append(clickable(f"{lname}.gauge", f"{lname}.gauge (G)",
                               _opaque_symbol("gauge", cx, gauge_y, 13, "#9467bd", gstate), cx, glabel, gauge_y - 20))
        # pump valve on the pump line, vent valve on the vent line (both below)
        if getattr(line, "valve_prevac", None) is not None:
            vstate = _valve_open_state(line.valve_prevac) if live else None
            P.append(clickable(f"{lname}.valve_prevac", f"{lname}.valve_prevac",
                               _opaque_symbol("valve", cx, pump_y, 11, "#8c564b", vstate), cx, "prevac", pump_y - 18))
            P.append(_valve_buttons(f"{lname}.valve_prevac", cx, pump_y, 11))
        if getattr(line, "valve_vent", None) is not None:
            vstate = _valve_open_state(line.valve_vent) if live else None
            P.append(clickable(f"{lname}.valve_vent", f"{lname}.valve_vent",
                               _opaque_symbol("valve", cx, vent_y, 11, "#8c564b", vstate), cx, "vent", vent_y - 16))
            P.append(_valve_buttons(f"{lname}.valve_vent", cx, vent_y, 11))
        # pump / vent action "buttons", below the common lines
        for bx, blabel, cmd, col in (
            (cx - 40, "pump", f"pump_down('{lname}')", "#2e7d32"),
            (cx + 4, "vent", f"vent('{lname}')", "#c62828"),
        ):
            P.append(f'<g style="cursor:pointer" onclick="// eco: {cmd}"><desc>{cmd}</desc><title>{_esc(cmd)}</title>')
            P.append(f'<rect x="{bx}" y="{btn_y}" width="36" height="20" rx="3" fill="white" stroke="{col}" stroke-width="1.5"/>')
            P.append(f'<text x="{bx+18}" y="{btn_y+14}" text-anchor="middle" font-size="10" fill="{col}">{blabel}</text>')
            P.append('</g>')

    P.append("</svg>")
    return "".join(P)


# --------------------------------------------------------------------------
# Config-dict driven construction
# --------------------------------------------------------------------------
def make_prepump_system(config, name="prepump"):
    """Build a :class:`PrepumpSystem` from one structured config dict.

    ``config`` layout (all PV entries are EPICS base names, or ``None`` until
    cabled)::

        {
          "gp": "<Gp gauge PV>",           # common prevac-line gauge
          "roots_pump": "<roots pump PV>",
          "p_target": 1e-3,                # optional, mbar (default 1e-3)
          "p_vent_target": 500.0,          # optional, mbar (default 500.0)
          "lines": {
            "<channel name>": {
              "gauge": "<G PV>",           # this channel's gauge
              "valve_prevac": "<P PV>",    # -> prevac/roots line
              "valve_vent":   "<V PV>",    # -> GN2 vent line
            },
            ...
          },
        }

    This is the form meant to be written/edited as a single literal (e.g. in
    ``eco/bernina/bernina.py`` when registering the system in the namespace).
    The dict is not mutated.
    """
    import copy

    cfg = copy.deepcopy(config)
    return PrepumpSystem(
        name=name,
        gp=cfg.get("gp"),
        roots_pump=cfg.get("roots_pump"),
        lines=cfg.get("lines", {}),
        p_target=cfg.get("p_target", 1e-3),
        p_vent_target=cfg.get("p_vent_target", 500.0),
    )


# --------------------------------------------------------------------------
# Bernina concrete config -- PV NAMES ARE PLACEHOLDERS, FILL THESE IN.
# Edit this dict as a whole (here or copied into eco/bernina/bernina.py) and
# hand it to make_prepump_system().
# --------------------------------------------------------------------------
BERNINA_PREPUMP_CONFIG = {
    "gp": None,          # common prevac-line gauge Gp base
    "roots_pump": None,  # Roots pump base
    "p_target": 1e-3,    # "pumped" threshold [mbar]
    "p_vent_target": 500.0,  # "vented" threshold [mbar]
    "lines": {
        "line1": {"gauge": None, "valve_prevac": None, "valve_vent": None},
        "line2": {"gauge": None, "valve_prevac": None, "valve_vent": None},
        # further channels, not yet cabled:
        # "line3": {"gauge": None, "valve_prevac": None, "valve_vent": None},
        # "line4": {"gauge": None, "valve_prevac": None, "valve_vent": None},
    },
}


def make_bernina_prepump_system(name="prepump"):
    """Build the Bernina prepump system from :data:`BERNINA_PREPUMP_CONFIG`.

    All PVs there are ``None`` placeholders: fill them in (in the dict above, or
    in a copy of it in ``bernina.py``) then this returns a ready
    :class:`PrepumpSystem`. A channel whose PVs are still ``None`` instantiates
    with its components simply absent, so the object is usable for
    wiring/UI work before the PVs exist.
    """
    return make_prepump_system(BERNINA_PREPUMP_CONFIG, name=name)
