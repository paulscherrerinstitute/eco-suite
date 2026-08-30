"""Generic alarm/threshold layer over Detector instances.

Introduces :class:`Alarm`: a Detector-protocol wrapper that watches another
Detector's live value and reports whether it is "in alarm", either against an
explicit soft ``[min, max]`` range -- mirroring the caQtDM alarm-overview
panels' own ``NAME=...,PV=...,MIN=...,MAX=...`` macros, see
:mod:`eco.devices_general.alarms` -- or, when no range is given, the wrapped
detector's own EPICS alarm severity (the same ``get_severity()`` convention
already used by :class:`eco.epics_utils.detector.DetectorPvData` and
``eco.devices_general.vacuum.prepump._gauge_ok_state``).

:class:`AlarmGroup` and :class:`AlarmPanel` build a tree of named ``Alarm``
objects the same way any other eco device tree is built (``Assembly._append``),
so a whole alarm-overview panel is a first-class eco object -- ``.status()``,
``.get_tree()``, tab-completion, all of it -- and, via ``AlarmPanel._widget_svg_panel``, a
clickable live panel through the same ``Assembly.show()`` hook
``PrepumpSystem``/``XrayBeamline`` use (see ``eco/devices_general/vacuum/
prepump.py``, ``eco/xoptics/beamline_assembly.py``).
"""

import re
from enum import IntEnum

import colorama

from eco.aliases import Alias
from eco.elements.assembly import Assembly

_RESET = colorama.Style.RESET_ALL

#: colour per 3-tier terminal-display state -- see Alarm._proximity_state().
#: Deliberately separate from AlarmSeverity/get_severity() (which stays
#: OK/ALARM-only for min/max bounds, used by worst_severity()/the SVG panel):
#: this is purely a display concern, so a near-edge "warning" shading here
#: never changes what .show()/worst_severity() report.
_STATE_COLOR = {
    "ok": colorama.Fore.GREEN,
    "warning": colorama.Fore.YELLOW,
    "alarm": colorama.Fore.RED + colorama.Style.BRIGHT,
    "unknown": colorama.Style.DIM,
}


def _colored(text, state):
    return f"{_STATE_COLOR.get(state, '')}{text}{_RESET}"


def _fmt_num(v):
    """Compact display form for a channel value/bound: `None` -> em-dash,
    a float -> 4 significant digits (`AlarmPanel`/`AlarmGroup`'s terminal
    table is meant for a quick glance, not full readback precision),
    anything else left as `str()`."""
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


class AlarmSeverity(IntEnum):
    OK = 0
    WARNING = 1
    ALARM = 2
    UNKNOWN = 3

    def __str__(self):
        return self.name


#: EPICS alarm severity (0=NO_ALARM, 1=MINOR, 2=MAJOR, 3=INVALID -- see
#: `DetectorPvData.get_severity`) -> AlarmSeverity, used as the fallback when
#: an `Alarm` has no explicit min/max of its own.
_EPICS_SEVERITY = {
    0: AlarmSeverity.OK,
    1: AlarmSeverity.WARNING,
    2: AlarmSeverity.ALARM,
    3: AlarmSeverity.UNKNOWN,
}


#: Terminal-table columns for AlarmGroup/AlarmPanel.get_display_str():
#: a leading LED dot, the channel name, then Min/Value/Max -- replacing the
#: generic Assembly table's single, full-precision "value" column with the
#: three numbers that actually determine the alarm.
_ALARM_TABLE_HEADERS = ["", "Channel", "Min", "Value", "Max"]
_ALARM_TABLE_COLALIGN = ["center", "left", "right", "right", "right"]


def _alarm_row(name, alarm):
    try:
        v = alarm.get_current_value()
    except Exception:
        v = None
    state = alarm._proximity_state()
    led = _colored("●", state)
    value_str = _colored(_fmt_num(v), state)
    return [led, name, _fmt_num(alarm.min), value_str, _fmt_num(alarm.max)]


def _render_alarm_table(rows, group_keys=None, tablefmt="simple"):
    if not rows:
        return ""
    from eco.utilities.tables import format_table, section_row_styles

    row_styles = section_row_styles(group_keys) if group_keys else None
    return format_table(
        rows,
        headers=_ALARM_TABLE_HEADERS,
        tablefmt=tablefmt,
        # cap the Value column so one long free-text status message (e.g. a
        # feedback-loop ":MSG" string, which has no min/max of its own) can't
        # stretch the whole table -- same idea as the base Assembly table's
        # own maxcolwidths for its description column.
        maxcolwidths=[None, None, None, 40, None],
        colalign=_ALARM_TABLE_COLALIGN,
        row_styles=row_styles,
    )


def _safe_attr_name(label):
    """Turn a free-text section label (e.g. "X-ray Pointing", "ESB_800nm
    Pointing Feedback") into a valid, unique-enough Python identifier for use
    as an `AlarmPanel` attribute name."""
    s = re.sub(r"\W+", "_", label.strip()).strip("_").lower()
    if not s or s[0].isdigit():
        s = f"g_{s}"
    return s


class Alarm:
    """One alarm channel: a Detector-protocol wrapper around another
    Detector's live value plus an optional soft alarm range.

    Parameters
    ----------
    detector : Detector
        Anything with ``get_current_value()`` -- typically a
        ``DetectorPvData(pvname)``, but any Detector works (a computed
        ``DetectorGet``, another Adjustable's readback, ...).
    min, max : float or None
        Soft alarm bounds, as used by the caQtDM ``alarm_channel.ui``
        NAME/PV/MIN/MAX macros this mirrors -- display-side thresholds set in
        the panel config, not necessarily the underlying PV's own EPICS alarm
        limits. Either/both may be `None` for a one-sided bound. If *both*
        are `None`, severity instead falls back to ``detector.get_severity()``
        when the detector has one, else stays `AlarmSeverity.UNKNOWN`.
    name : str
    unit : str, optional
    """

    def __init__(self, detector, min=None, max=None, name=None, unit=None):
        self.name = name
        self.alias = Alias(name)
        self.detector = detector
        self.min = min
        self.max = max
        self.unit = unit

    def get_current_value(self):
        return self.detector.get_current_value()

    value = property(get_current_value)

    def get_severity(self):
        """`AlarmSeverity` for the current reading."""
        if self.min is not None or self.max is not None:
            try:
                v = self.get_current_value()
            except Exception:
                return AlarmSeverity.UNKNOWN
            if v is None:
                return AlarmSeverity.UNKNOWN
            try:
                if self.min is not None and v < self.min:
                    return AlarmSeverity.ALARM
                if self.max is not None and v > self.max:
                    return AlarmSeverity.ALARM
            except TypeError:
                return AlarmSeverity.UNKNOWN
            return AlarmSeverity.OK
        get_severity = getattr(self.detector, "get_severity", None)
        if get_severity is None:
            return AlarmSeverity.UNKNOWN
        try:
            sev = get_severity()
        except Exception:
            sev = None
        if sev is None:
            return AlarmSeverity.UNKNOWN
        return _EPICS_SEVERITY.get(sev, AlarmSeverity.UNKNOWN)

    def is_ok(self):
        return self.get_severity() == AlarmSeverity.OK

    def _proximity_state(self):
        """3-tier "how close to the alarm bounds is the live value" state
        used for terminal-table colouring: "ok" (green), "warning" (yellow,
        within 20% of either bound), "alarm" (red, outside the bounds), or
        "unknown" (grey, unreadable / no bounds and no EPICS severity
        available). Independent of `get_severity()` (OK/ALARM-only for
        min/max bounds, used by `worst_severity()`/the SVG panel) -- this is
        purely a display nuance, so it never changes what those report."""
        if self.min is not None or self.max is not None:
            try:
                v = self.get_current_value()
            except Exception:
                return "unknown"
            if v is None:
                return "unknown"
            try:
                if (self.min is not None and v < self.min) or (
                    self.max is not None and v > self.max
                ):
                    return "alarm"
                if self.min is not None and self.max is not None:
                    margin = 0.2 * (self.max - self.min)
                    if v < self.min + margin or v > self.max - margin:
                        return "warning"
                return "ok"
            except TypeError:
                return "unknown"
        return {
            AlarmSeverity.OK: "ok",
            AlarmSeverity.WARNING: "warning",
            AlarmSeverity.ALARM: "alarm",
            AlarmSeverity.UNKNOWN: "unknown",
        }[self.get_severity()]

    def __repr__(self):
        try:
            v = self.get_current_value()
        except Exception:
            v = None
        state = self._proximity_state()
        bounds = (
            f" [{_fmt_num(self.min)}, {_fmt_num(self.max)}]"
            if (self.min is not None or self.max is not None)
            else ""
        )
        unit = f" {self.unit}" if self.unit else ""
        led = _colored("●", state)
        value = _colored(_fmt_num(v), state)
        return f"{led} {self.name}: {value}{unit}{bounds} -- {_colored(state.upper(), state)}"


class AlarmGroup(Assembly):
    """One labelled section of an alarm panel (a caQtDM group box, e.g.
    "Oscillator"/"Amplifiers") -- a flat collection of named `Alarm`s built
    through `Assembly._append` like any other eco device tree.
    """

    def __init__(self, channels, name=None, label=None):
        """`channels`: iterable of dicts, each either
        ``{"name": ..., "detector": <Detector instance>, "min": ..., "max": ...}``
        or ``{"name": ..., "pv": <PV name string>, "min": ..., "max": ...}``
        (the ``pv`` form lazily builds a
        `eco.epics_utils.detector.DetectorPvData`). ``label`` is the
        human-readable section title if it differs from `name` (which must be
        a valid attribute name); defaults to `name`.
        """
        super().__init__(name=name)
        self.label = label if label is not None else name
        self._channel_names = []
        for ch in channels:
            ch = dict(ch)
            cname = ch.pop("name")
            detector = ch.pop("detector", None)
            if detector is None:
                pv = ch.pop("pv")
                from eco.epics_utils.detector import DetectorPvData

                detector = DetectorPvData(pv, name=f"{cname}_pv")
            self._append(Alarm, detector=detector, name=cname, is_status=True, **ch)
            self._channel_names.append(cname)

    def get_severities(self):
        return {n: getattr(self, n).get_severity() for n in self._channel_names}

    def worst_severity(self):
        sevs = list(self.get_severities().values())
        return max(sevs) if sevs else AlarmSeverity.UNKNOWN

    def get_display_str(self, tablefmt="simple", **kwargs):
        """Overrides `Assembly.get_display_str` (so `.status()`/`repr()`
        pick this up automatically): a LED + Min/Value/Max table instead of
        the generic single full-precision "value" column -- see
        `_alarm_row`/`_render_alarm_table`. Extra kwargs (`with_base_name`,
        `maxcolwidths`, `show_triggers`, ...) are accepted for interface
        compatibility with the base method's callers but don't apply here:
        every row in an `AlarmGroup` is a plain `Alarm` channel, never a
        trigger or a further-nested sub-assembly."""
        rows = [_alarm_row(cname, getattr(self, cname)) for cname in self._channel_names]
        return _render_alarm_table(rows, tablefmt=tablefmt)


class AlarmPanel(Assembly):
    """A full alarm-overview panel: a named collection of `AlarmGroup`
    sections -- one eco object per caQtDM alarm-overview ``.ui`` file (see
    `eco.devices_general.alarms`).
    """

    def __init__(self, sections, name=None):
        """`sections`: iterable of ``(section_label, channels)`` pairs, where
        `channels` is the same list-of-dicts `AlarmGroup` takes."""
        super().__init__(name=name)
        self._group_names = []
        for label, channels in sections:
            gname = _safe_attr_name(label)
            while gname in self._group_names:
                gname += "_"
            self._append(
                AlarmGroup, channels, name=gname, label=label, is_display="recursive"
            )
            self._group_names.append(gname)

    def get_severities(self):
        """``{"<group>.<channel>": AlarmSeverity}`` for every channel."""
        out = {}
        for gname in self._group_names:
            group = getattr(self, gname)
            for cname, sev in group.get_severities().items():
                out[f"{gname}.{cname}"] = sev
        return out

    def worst_severity(self):
        sevs = list(self.get_severities().values())
        return max(sevs) if sevs else AlarmSeverity.UNKNOWN

    def get_display_str(self, tablefmt="simple", **kwargs):
        """Same LED + Min/Value/Max table as `AlarmGroup.get_display_str`,
        flattened across every section, with each section's rows given
        their own background shade (`section_row_styles`, the same
        convention `Assembly.get_display_str` itself uses for nested
        sub-assemblies) keyed by the section's human label."""
        rows, group_keys = [], []
        for gname in self._group_names:
            group = getattr(self, gname)
            for cname in group._channel_names:
                rows.append(_alarm_row(f"{group.label}.{cname}", getattr(group, cname)))
                group_keys.append(group.label)
        return _render_alarm_table(rows, group_keys=group_keys, tablefmt=tablefmt)

    # ---- clickable panel -------------------------------------------------
    # `_widget_svg_panel()` is the dynamic-panel hook `Assembly.show()`
    # looks for (see its docstring) -- same `_widget_`-prefixed convention
    # as `PrepumpSystem._widget_svg_panel`/`XrayBeamline._widget_svg_panel`.
    def _widget_svg_panel(self, live=False, cols=6, path=None, **kwargs):
        """Build a panel SVG (grouped by section, one cell per channel,
        green/red by `AlarmSeverity` when ``live=True``) and return its file
        path. Reuses `eco.xoptics.beamline_svg.build_beamline_svg` -- the same
        grouped-grid-of-clickable-symbols renderer the beamline/vacuum panels
        use -- rather than a parallel implementation."""
        from eco.utilities.tempfiles import user_temp_svg_path
        from eco.xoptics.beamline_svg import build_beamline_svg

        items = []
        for gname in self._group_names:
            group = getattr(self, gname)
            for cname in group._channel_names:
                alarm = getattr(group, cname)
                state = None
                if live:
                    sev = alarm.get_severity()
                    if sev == AlarmSeverity.OK:
                        state = True
                    elif sev in (AlarmSeverity.WARNING, AlarmSeverity.ALARM):
                        state = False
                items.append(
                    {
                        "relpath": f"{gname}.{cname}",
                        "label": cname,
                        "kind": "alarm",
                        "section": group.label,
                        "z": None,
                        "state": state,
                    }
                )
        svg_text = build_beamline_svg(items, title=self.name or "alarms", cols=cols)
        if path is None:
            path = user_temp_svg_path("eco_alarms", self.name or id(self))
        with open(path, "w") as f:
            f.write(svg_text)
        return path
