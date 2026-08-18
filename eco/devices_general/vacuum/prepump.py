"""Bernina consolidated prepump / venting system as an eco object.

Models the new Bernina prepump system (spec: "Prepump system on turbo prepump
line"): a single high-performance **Roots pump** feeds a common **pump line**
(pressure gauge ``Gp``); a separate **GN2 vent line** (low overpressure) allows
controlled venting. Up to five **prepump/venting lines** branch off, each with

* a **pump valve** ``P`` (line -> common pump line),
* a **vent valve** ``V`` (line -> vent line),
* its own gauge ``G``,
* and usually a **turbo pump** it backs (the turbo is *served by* the prepump
  system, not part of it).

Because ``Gp`` rises during a pumpdown, the *other* lines must be isolated while
one line is pumped, so they don't pump against too high a pressure. That
cross-line interlock is the job of :class:`PrepumpSystem`; the per-line valve
choreography is :class:`PrepumpLine`.

Line types (one per table in the spec)
--------------------------------------
``turbo``           classic turbo prepump line: valves ``pump`` (P), ``vent`` (V).
``transport``       same valves, but a beam-transport pipe with no turbo.
``vent_access``     turbo vent-access variant (spec table 3); ``pump`` + ``vent``.
``prepump_access``  dual-access line (spec table 2) for fast sample exchange:
                    the turbo keeps spinning in its own isolated section while
                    the chamber is vented. Valves ``turbo_pump`` (P1),
                    ``turbo_access`` (T), ``chamber_pump`` (P2),
                    ``chamber_vent`` (V2).

You supply the real EPICS PV base names per component (see
:func:`make_bernina_prepump_system`, whose five lines are placeholders to fill
in). The pressure thresholds default to a single target (``p_target``, mbar):
a gauge is "pumped" when it reads at/below it, "high" otherwise -- matching the
spec's ``1e-3`` / ``high`` columns.

.. note::
   The final spec section ("usage scenarios for permanent lines 1-2") is
   truncated in the source document. The five lines / kinds in
   :func:`make_bernina_prepump_system` are therefore a **best guess** and marked
   as such -- correct them once the intended assignment is known.
"""

import time

from eco import Assembly

from .gauges import VacuumGauge
from .pumps import TurboPump
from .valves import Valve, PrePump


# --------------------------------------------------------------------------
# Verbatim reference of the spec's state tables (for audit against the doc).
# "high"/"1e-3" are the gauge *conditions* that identify a state; open/close are
# the *valve commands*. "others" = every OTHER line's pump / vent valves (the
# spec's P2-Pn / V2-Vn columns), handled by PrepumpSystem, not the line itself.
# --------------------------------------------------------------------------
#: spec table 1 -- "Actions for section 1" (classic turbo prepump line)
_DOC_TABLE_TURBO = {
    #  state                    Gp     G1      P1      V1     P2-Pn   V2-Vn
    "pump_down":              ("high", "high", "open", "close", "close", "close"),
    "pump_down_success":      ("1e-3", "1e-3", "open", "close", "open",  "close"),
    "pump_down_failed_start": ("high", "high", "close","close", "close", "close"),
    "pump_down_failed_end":   ("1e-3", "high", "close","close", "open",  "close"),
    "venting":                ("1e-3", "high", "close","open",  "open",  "close"),
}
#: spec table 2 -- "Actions for section with prepump access" (dual line)
_DOC_TABLE_PREPUMP_ACCESS = {
    #  state                    Gp     G1      P1      T       P2      V2      P3-Pn   V3-Vn
    "pump_down":              ("high", "high", "close","close", "open", "close", "close", "close"),
    "pump_down_success":      ("1e-3", "1e-3", "open", "open",  "close","close", "open",  "close"),
    "pump_down_failed_start": ("high", "high", "close","close", "close","close", "close", "close"),
    "pump_down_failed_end":   ("1e-3", "high", "close","close", "close","close", "open",  "close"),
    "venting":                ("1e-3", "high", "close","close", "close","open",  "open",  "close"),
}
#: spec table 3 -- "Vent access to turbo pumps" (V column is greyed = optional)
_DOC_TABLE_VENT_ACCESS = {
    #  state                    Gp     G1      P1      V(grey) P2-Pn   V2-Vn
    "pump_down":              ("high", "high", "open", "open",  "close", "close"),
    "pump_down_success":      ("1e-3", "1e-3", "open", "open",  "open",  "close"),
    "pump_down_failed_start": ("high", "high", "close","open",  "close", "close"),
    "pump_down_failed_end":   ("1e-3", "high", "close","open",  "open",  "close"),
    "venting":                ("1e-3", "high", "close","open",  "open",  "close"),
}


# --------------------------------------------------------------------------
# Operational state machine, per line kind: state -> {valve role: command}.
# Derived from the tables above, keeping only *this line's own* valves (the
# cross-line "others" columns are applied by PrepumpSystem via isolate/restore).
# Four operational states are enough to run everything:
#   isolated  -- all this line's valves closed (safe; the "failed_start" row)
#   pump_down -- actively roughing this line
#   connected -- nominal steady state, line on the pump line ("success" row)
#   venting   -- venting this line through the vent line
# --------------------------------------------------------------------------
#
# Every line has the two common valves ``valve_prevac`` (P: line -> common
# prevac/roots line) and ``valve_vent`` (V: line -> GN2 vent line). A
# ``prepump_access`` line adds two turbo-side valves: ``valve_turbo_prevac``
# (P1: backs the independently-spinning turbo) and ``valve_turbo_access``
# (T: isolates the turbo from the chamber). For that kind, ``valve_prevac`` /
# ``valve_vent`` are the *chamber's* direct roughing/vent valves (spec P2/V2).
# --------------------------------------------------------------------------
STATE_TABLES = {
    "turbo": {
        "isolated":  {"valve_prevac": "close", "valve_vent": "close"},
        "pump_down": {"valve_prevac": "open",  "valve_vent": "close"},
        "connected": {"valve_prevac": "open",  "valve_vent": "close"},
        "venting":   {"valve_prevac": "close", "valve_vent": "open"},
    },
    "transport": {  # no turbo, otherwise identical to a classic turbo line
        "isolated":  {"valve_prevac": "close", "valve_vent": "close"},
        "pump_down": {"valve_prevac": "open",  "valve_vent": "close"},
        "connected": {"valve_prevac": "open",  "valve_vent": "close"},
        "venting":   {"valve_prevac": "close", "valve_vent": "open"},
    },
    "vent_access": {  # spec table 3; the vent valve is the turbo's own (optional)
        "isolated":  {"valve_prevac": "close", "valve_vent": "open"},
        "pump_down": {"valve_prevac": "open",  "valve_vent": "open"},
        "connected": {"valve_prevac": "open",  "valve_vent": "open"},
        "venting":   {"valve_prevac": "close", "valve_vent": "open"},
    },
    "prepump_access": {  # spec table 2 (P1=turbo_prevac, T=turbo_access, P2=prevac, V2=vent)
        "isolated":  {"valve_turbo_prevac": "close", "valve_turbo_access": "close", "valve_prevac": "close", "valve_vent": "close"},
        "pump_down": {"valve_turbo_prevac": "close", "valve_turbo_access": "close", "valve_prevac": "open",  "valve_vent": "close"},
        "connected": {"valve_turbo_prevac": "open",  "valve_turbo_access": "open",  "valve_prevac": "close", "valve_vent": "close"},
        "venting":   {"valve_turbo_prevac": "close", "valve_turbo_access": "close", "valve_prevac": "close", "valve_vent": "open"},
    },
}

#: valve roles expected per line kind (also the order they are drawn/listed).
ROLE_SETS = {
    "turbo": ["valve_prevac", "valve_vent"],
    "transport": ["valve_prevac", "valve_vent"],
    "vent_access": ["valve_prevac", "valve_vent"],
    "prepump_access": ["valve_prevac", "valve_vent", "valve_turbo_prevac", "valve_turbo_access"],
}

#: which roles connect the line to the common prevac/roots line vs the vent
#: line -- used by the schematic and by isolate() reasoning.
_PUMP_SIDE_ROLES = {"valve_prevac", "valve_turbo_prevac"}
_VENT_SIDE_ROLES = {"valve_vent"}


class PrepumpLine(Assembly):
    """One prepump/venting line (one of the system's five "exits").

    Configure with the EPICS PV base names of its components; any left ``None``
    are simply not built, so a partially-cabled line still works for the parts
    that exist. Every line has the two common valves:

    * ``valve_prevac=`` -- P, connects the line to the common prevac/roots line
    * ``valve_vent=``   -- V, connects the line to the GN2 vent line

    and, depending on ``kind`` (see :data:`ROLE_SETS`), a ``prepump_access``
    line additionally has the turbo-side valves:

    * ``valve_turbo_prevac=`` -- P1, backs the (independently spinning) turbo
    * ``valve_turbo_access=``  -- T, isolates the turbo from the chamber

    plus ``gauge=`` (this line's ``G``) and, optionally, ``turbo=`` (the turbo
    pump this line backs/serves).
    """

    def __init__(self, name=None, kind="turbo", gauge=None, turbo=None,
                 valve_prevac=None, valve_vent=None,
                 valve_turbo_prevac=None, valve_turbo_access=None):
        super().__init__(name=name)
        if kind not in STATE_TABLES:
            raise ValueError(
                f"unknown prepump line kind {kind!r}; expected one of "
                f"{sorted(STATE_TABLES)}"
            )
        self.kind = kind
        self._roles = ROLE_SETS[kind]
        self._valve_names = []
        valve_pvs = {
            "valve_prevac": valve_prevac,
            "valve_vent": valve_vent,
            "valve_turbo_prevac": valve_turbo_prevac,
            "valve_turbo_access": valve_turbo_access,
        }
        # gauge for this line's own pressure (G1..Gn)
        if gauge is not None:
            self._append(VacuumGauge, gauge, name="gauge", is_status=True)
        # the valves that this kind of line has
        for role in self._roles:
            pv = valve_pvs.get(role)
            if pv is not None:
                self._append(Valve, pv, name=role, is_setting=True)
                self._valve_names.append(role)
        # the turbo this line backs (served by, not part of, the prepump system)
        if turbo is not None:
            self._append(TurboPump, turbo, name="turbo", is_status=True)
        # warn about valve args given but not used by this kind (typo guard)
        extra = [r for r in ("valve_prevac", "valve_vent", "valve_turbo_prevac",
                             "valve_turbo_access")
                 if valve_pvs[r] is not None and r not in self._roles]
        if extra:
            print(f"[{name}] ignoring valve args not used by kind {kind!r}: {extra}")

    # ---- low-level valve choreography ---------------------------------
    def _valve(self, role):
        v = getattr(self, role, None)
        if v is None:
            raise RuntimeError(
                f"prepump line '{self.name}': valve role '{role}' is not "
                "configured (no PV given)."
            )
        return v

    def set_state(self, state):
        """Drive this line's own valves to the named operational state
        (``isolated`` / ``pump_down`` / ``connected`` / ``venting``). Missing
        (unconfigured) valves are skipped with a warning."""
        table = STATE_TABLES[self.kind]
        if state not in table:
            raise ValueError(f"unknown state {state!r}; expected {sorted(table)}")
        for role, command in table[state].items():
            valve = getattr(self, role, None)
            if valve is None:
                print(f"[{self.name}] state '{state}': skipping unconfigured valve '{role}'")
                continue
            (valve.open if command == "open" else valve.close)()

    def isolate(self):
        """Close this line off from both pump and vent lines (safe state)."""
        self.set_state("isolated")

    def restore(self):
        """Return this line to its nominal connected steady state."""
        self.set_state("connected")

    # ---- readback ------------------------------------------------------
    def pressure(self):
        """This line's gauge pressure, or None if no gauge/reading."""
        g = getattr(self, "gauge", None)
        if g is None:
            return None
        try:
            return g.pressure.get_current_value()
        except Exception:
            return None

    def get_current_value(self, *args, **kwargs):
        """A compact {role: valve-state, 'pressure': p} snapshot."""
        out = {}
        for role in self._valve_names:
            try:
                out[role] = self._valve(role).get_current_value()
            except Exception:
                out[role] = "?"
        out["pressure"] = self.pressure()
        return out


class PrepumpSystem(Assembly):
    """The whole Bernina prepump system: one Roots pump, the common pump-line
    gauge ``Gp``, and up to five :class:`PrepumpLine` exits.

    The pump/vent *procedures* live here because they must coordinate all lines
    (isolate the others while one pumps, restore them afterwards -- the spec's
    ``P2-Pn``/``V2-Vn`` columns).

    Parameters
    ----------
    gp : str
        PV base of the common pump-line gauge ``Gp``.
    roots_pump : str
        PV base of the Roots (backing) pump.
    lines : dict[str, PrepumpLine | dict]
        The exits, keyed by name. Each value is either a ready
        :class:`PrepumpLine` or a **plain dict of its keyword args** (``kind``,
        ``valve_prevac``, ``valve_vent``, ``gauge``, ``turbo``, ...). The dict
        form is what lets the whole system be described by one editable config
        literal -- see :data:`BERNINA_PREPUMP_CONFIG` / :func:`make_prepump_system`.
    p_target : float
        Pressure [mbar] at/below which a gauge counts as "pumped" (spec ``1e-3``).
    """

    def __init__(self, name=None, gp=None, roots_pump=None, lines=None,
                 p_target=1e-3, vent_line_name="GN2"):
        super().__init__(name=name)
        self.p_target = p_target
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

    def _pressure(self, gauge_obj):
        try:
            return gauge_obj.pressure.get_current_value()
        except Exception:
            return None

    def _is_pumped(self, pressure):
        return pressure is not None and pressure <= self.p_target

    def gp_pressure(self):
        """Common pump-line pressure (Gp), or None."""
        gp = getattr(self, "gp", None)
        return None if gp is None else self._pressure(gp)

    # ---- procedures ----------------------------------------------------
    def _isolate_others(self, active):
        for line in self.lines:
            if line is not active:
                try:
                    line.isolate()
                except Exception as e:
                    print(f"  (could not isolate {line.name}: {e})")

    def _restore_others(self, active):
        for line in self.lines:
            if line is not active:
                try:
                    line.restore()
                except Exception as e:
                    print(f"  (could not restore {line.name}: {e})")

    def pump_down(self, line, wait=True, timeout=300.0, poll=2.0):
        """Pump down one line.

        Sequence (spec tables): isolate every *other* line, drive this line to
        ``pump_down``, then watch ``Gp`` and this line's ``G``:

        * both reach the target  -> **success**: this line -> ``connected``,
          other lines restored (their pump valves reopen);
        * ``Gp`` recovers but ``G`` stays high, or `timeout` elapses -> **failed**:
          this line -> ``isolated``, other lines restored.

        With ``wait=False`` it only commands the initial ``pump_down`` state and
        returns immediately (no monitoring).
        """
        line = self._get_line(line)
        print(f"[prepump] pumping down '{line.name}' ({line.kind}); isolating other lines")
        self._isolate_others(line)
        line.set_state("pump_down")
        if not wait:
            return None

        t0 = time.time()
        while time.time() - t0 < timeout:
            gp = self.gp_pressure()
            g = line.pressure()
            if self._is_pumped(gp) and self._is_pumped(g):
                print(f"[prepump] '{line.name}' pumped (Gp={gp}, G={g}) -> connected")
                line.set_state("connected")
                self._restore_others(line)
                return "success"
            time.sleep(poll)
        gp = self.gp_pressure()
        g = line.pressure()
        print(f"[prepump] '{line.name}' pumpdown FAILED (Gp={gp}, G={g}) -> isolated")
        line.set_state("isolated")
        self._restore_others(line)
        return "failed"

    def vent(self, line, turbo_spundown=None):
        """Vent one line through the GN2 vent line.

        For ``turbo``/``vent_access`` lines the turbo must be spun down first
        (the spec's "after turbo spindown"); ``prepump_access`` lines keep the
        turbo spinning (its access valve ``T`` closes instead). Pass
        ``turbo_spundown=True`` to confirm you've handled the turbo when this
        line has one and isn't a ``prepump_access`` line.
        """
        line = self._get_line(line)
        needs_spindown = line.kind in ("turbo", "vent_access") and getattr(line, "turbo", None) is not None
        if needs_spindown and not turbo_spundown:
            raise RuntimeError(
                f"'{line.name}' has a turbo: spin it down first "
                f"(e.g. {line.name}.turbo.stop()), then call "
                f"vent('{line.name}', turbo_spundown=True)."
            )
        print(f"[prepump] venting '{line.name}' via {self.vent_line_name} vent line")
        self._restore_others(line)
        line.set_state("venting")
        return "venting"

    def isolate_all(self):
        """Safe state: close every line off from pump and vent lines."""
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
        connect, at *every* depth (roots pump, ``Gp``, and every line's
        gauge/turbo/valves).

        A component that fails to connect (e.g. a typo'd or unreachable PV)
        does **not** raise -- it's swallowed where it happens and left as a
        ``FailedComponent`` placeholder (see ``Assembly._append(optional=True)``).
        ``Assembly._append`` now mirrors that failure up through every ancestor
        too (so e.g. ``prepump.get_status()`` correctly reports a failure buried
        inside a line's own valve/gauge/turbo, not just direct children) -- this
        method is the complementary, explicit form: a live re-probe of every
        configured component by path, in one table, including ones that were
        simply never given a PV (``"not configured"``, which isn't a failure).

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
            probe(f"{lname}.turbo", getattr(line, "turbo", None))
            for role in line._roles:
                probe(f"{lname}.{role}", getattr(line, role, None))

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
        EPICS); pass `live=False` for the plain kind-coloured, EPICS-free
        version. See :func:`build_prepump_svg`."""
        import os
        import tempfile

        svg_text = build_prepump_svg(self, live=live)
        if path is None:
            path = os.path.join(tempfile.gettempdir(), f"eco_prepump_{self.name or id(self)}.svg")
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
        valve/gauge/pump inspects that device; clicking a line's *pump*/*vent*
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
    """Render `system` as a P&ID-style clickable SVG string, mirroring the spec
    figures: Roots pump -> common pump line (with Gp) + GN2 vent line, and one
    vertical branch per line with its pump/vent valves, gauge and turbo. Clicks
    carry ``onclick="// eco: <path>"`` markers resolved against the system alias
    by :mod:`eco.utilities.svg_interactor`. `live=True` colours each valve
    green/red by open/closed state and each gauge green/red by its live EPICS
    alarm severity (touches EPICS); default is the plain kind colour only."""
    from eco.xoptics.beamline_svg import _symbol, _esc

    def _short(role):
        return role.replace("valve_", "")

    line_names = system._line_names
    col_w = 190
    margin = 30
    x0 = margin + 120  # room for the roots pump on the left
    # +160 on the right for the Gp gauge and the "GN2 vent line" label
    width = x0 + max(1, len(line_names)) * col_w + 160
    pump_y = 90
    vent_y = 150
    turbo_y = 250
    gauge_y = 330
    label_y = 375
    btn_y = 395
    height = btn_y + 60

    def _valve_buttons(cmd_prefix, vx, vy, r):
        """Small open/close dots pinned right next to a valve glyph at
        (vx, vy) of radius `r` -- offset just clear of the glyph itself
        (dx = r + 9, so even the largest glyph here (r=13) keeps the closest
        dot edge a few px away) and stacked vertically (dy = +-7) rather than
        sideways, since valves can sit only 50px apart horizontally
        (staggered pump-side pairs) but always have >=60px of clear space
        above/below along their own column. No visible text (no room to set
        it legibly at this size) -- colour (green/red) plus a `<title>`
        hover tooltip carry the meaning, consistent with the glyphs above."""
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
    # common pump line + vent line
    x_end = x0 + len(line_names) * col_w
    P.append(f'<line x1="{x0-40}" y1="{pump_y}" x2="{x_end}" y2="{pump_y}" stroke="#1f4e8c" stroke-width="3"/>')
    P.append(f'<line x1="{x0-40}" y1="{vent_y}" x2="{x_end+70}" y2="{vent_y}" stroke="#555" stroke-width="2"/>')
    P.append(f'<text x="{x_end+6}" y="{vent_y-6}" font-size="11" fill="#555">{_esc(system.vent_line_name)} vent line</text>')
    # Roots pump (custom symbol) + Gp
    rpx, rpy = margin + 40, pump_y
    P.append(f'<g style="cursor:pointer" onclick="// eco: roots_pump"><desc>roots_pump</desc><title>roots_pump</title>')
    P.append(f'<circle cx="{rpx}" cy="{rpy}" r="26" fill="white" stroke="#1f4e8c" stroke-width="2.5"/>')
    P.append(f'<text x="{rpx}" y="{rpy+6}" text-anchor="middle" font-size="18" fill="#1f4e8c">R</text>')
    P.append(f'<text x="{rpx}" y="{rpy+42}" text-anchor="middle" font-size="11" fill="#2e3440">roots_pump</text>')
    P.append('</g>')
    P.append(f'<line x1="{rpx+26}" y1="{pump_y}" x2="{x0-40}" y2="{pump_y}" stroke="#1f4e8c" stroke-width="3"/>')
    if getattr(system, "gp", None) is not None:
        gx = x_end + 30
        P.append(f'<g style="cursor:pointer" onclick="// eco: gp"><desc>gp</desc><title>gp (Gp)</title>')
        P.append(_symbol("gauge", gx, pump_y, 13, "#9467bd", _gauge_ok_state(system.gp) if live else None))
        P.append(f'<text x="{gx}" y="{pump_y-20}" text-anchor="middle" font-size="11" fill="#2e3440">Gp</text>')
        P.append('</g>')
        P.append(f'<line x1="{x_end}" y1="{pump_y}" x2="{gx}" y2="{pump_y}" stroke="#1f4e8c" stroke-width="3"/>')

    def clickable(cmd, title, inner, cx, label, ly):
        s = [f'<g style="cursor:pointer" onclick="// eco: {_esc(cmd)}"><desc>{_esc(cmd)}</desc><title>{_esc(title)}</title>']
        s.append(inner)
        s.append(f'<text x="{cx}" y="{ly}" text-anchor="middle" font-size="10" fill="#2e3440">{_esc(label)}</text>')
        s.append('</g>')
        return "".join(s)

    for i, lname in enumerate(line_names):
        line = getattr(system, lname)
        cx = x0 + i * col_w + col_w / 2
        # branch connector down from the pump line
        P.append(f'<line x1="{cx}" y1="{pump_y}" x2="{cx}" y2="{gauge_y}" stroke="#888" stroke-width="1.5"/>')
        # pump-side valve(s) on the pump line, vent-side valve(s) on the vent line
        pump_roles = [r for r in line._valve_names if r in _PUMP_SIDE_ROLES]
        vent_roles = [r for r in line._valve_names if r in _VENT_SIDE_ROLES]
        other_roles = [r for r in line._valve_names if r not in _PUMP_SIDE_ROLES and r not in _VENT_SIDE_ROLES]
        for j, role in enumerate(pump_roles):
            vx = cx + (j - (len(pump_roles) - 1) / 2) * 50
            # stagger labels of side-by-side pump valves so they don't overlap
            ly = pump_y - 18 - (18 if (len(pump_roles) > 1 and j % 2 == 0) else 0)
            vstate = _valve_open_state(getattr(line, role)) if live else None
            P.append(clickable(f"{lname}.{role}", f"{lname}.{role}",
                               _symbol("valve", vx, pump_y, 11, "#8c564b", vstate), vx, _short(role), ly))
            P.append(_valve_buttons(f"{lname}.{role}", vx, pump_y, 11))
        for role in vent_roles:
            vstate = _valve_open_state(getattr(line, role)) if live else None
            P.append(clickable(f"{lname}.{role}", f"{lname}.{role}",
                               _symbol("valve", cx, vent_y, 11, "#8c564b", vstate), cx, _short(role), vent_y - 16))
            P.append(_valve_buttons(f"{lname}.{role}", cx, vent_y, 11))
        # any remaining roles (e.g. turbo_access T) drawn between vent line and turbo
        for k, role in enumerate(other_roles):
            oy = vent_y + 34 + k * 30
            vstate = _valve_open_state(getattr(line, role)) if live else None
            P.append(clickable(f"{lname}.{role}", f"{lname}.{role}",
                               _symbol("valve", cx, oy, 10, "#8c564b", vstate), cx, _short(role), oy - 15))
            P.append(_valve_buttons(f"{lname}.{role}", cx, oy, 10))
        # turbo pump (if any)
        if getattr(line, "turbo", None) is not None:
            P.append(clickable(f"{lname}.turbo", f"{lname}.turbo",
                               _symbol("pump", cx, turbo_y, 15, "#7f7f7f", None), cx, "turbo", turbo_y + 26))
        # line gauge
        if getattr(line, "gauge", None) is not None:
            gstate = _gauge_ok_state(line.gauge) if live else None
            P.append(clickable(f"{lname}.gauge", f"{lname}.gauge (G)",
                               _symbol("gauge", cx, gauge_y, 13, "#9467bd", gstate), cx, "G", gauge_y + 24))
        # line label + kind
        P.append(f'<text x="{cx}" y="{label_y}" text-anchor="middle" font-size="12" font-weight="bold" fill="#2e3440">{_esc(lname)}</text>')
        P.append(f'<text x="{cx}" y="{label_y+13}" text-anchor="middle" font-size="9" fill="#6b7280">{_esc(line.kind)}</text>')
        # pump / vent action "buttons"
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
          "lines": {
            "<line name>": {
              "kind": "turbo" | "transport" | "vent_access" | "prepump_access",
              "gauge": "<G PV>",           # this line's gauge
              "turbo": "<turbo PV>",       # optional turbo it serves
              "valve_prevac": "<P PV>",    # -> prevac/roots line
              "valve_vent":   "<V PV>",    # -> GN2 vent line
              # prepump_access only:
              "valve_turbo_prevac": "<P1 PV>",
              "valve_turbo_access": "<T PV>",
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
    )


# --------------------------------------------------------------------------
# Bernina concrete config -- PV NAMES ARE PLACEHOLDERS, FILL THESE IN.
# Edit this dict as a whole (here or copied into eco/bernina/bernina.py) and
# hand it to make_prepump_system(). Line *kinds* are a GUESS (the spec's
# "usage scenarios for permanent lines 1-2" is truncated) -- adjust once known.
# --------------------------------------------------------------------------
BERNINA_PREPUMP_CONFIG = {
    "gp": None,          # common prevac-line gauge Gp base
    "roots_pump": None,  # Roots pump base
    "p_target": 1e-3,    # "pumped" threshold [mbar]
    "lines": {
        # permanent dual-access chambers (GUESS: prepump_access) -------------
        "line1": {
            "kind": "prepump_access",
            "gauge": None,               # G1 gauge base
            "turbo": None,               # turbo pump base
            "valve_prevac": None,        # P2 chamber roughing valve
            "valve_vent": None,          # V2 chamber vent valve
            "valve_turbo_prevac": None,  # P1 turbo backing valve
            "valve_turbo_access": None,  # T  turbo isolation valve
        },
        "line2": {
            "kind": "prepump_access",
            "gauge": None, "turbo": None,
            "valve_prevac": None, "valve_vent": None,
            "valve_turbo_prevac": None, "valve_turbo_access": None,
        },
        # classic turbo prepump lines (GUESS: turbo) ------------------------
        "line3": {"kind": "turbo", "gauge": None, "turbo": None,
                  "valve_prevac": None, "valve_vent": None},
        "line4": {"kind": "turbo", "gauge": None, "turbo": None,
                  "valve_prevac": None, "valve_vent": None},
        # beam-transport pipe, pre-vacuum only (GUESS: transport) -----------
        "line5": {"kind": "transport", "gauge": None,
                  "valve_prevac": None, "valve_vent": None},
    },
}


def make_bernina_prepump_system(name="prepump"):
    """Build the 5-line Bernina prepump system from :data:`BERNINA_PREPUMP_CONFIG`.

    All PVs there are ``None`` placeholders: fill them in (in the dict above, or
    in a copy of it in ``bernina.py``) then this returns a ready
    :class:`PrepumpSystem`. A line whose PVs are still ``None`` instantiates with
    its components simply absent, so the object is usable for wiring/UI work
    before the PVs exist.
    """
    return make_prepump_system(BERNINA_PREPUMP_CONFIG, name=name)
