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

from eco.aliases import Alias
from eco.elements.assembly import Assembly


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

    def __repr__(self):
        try:
            v = self.get_current_value()
        except Exception:
            v = "?"
        bounds = f" [{self.min}, {self.max}]" if (self.min is not None or self.max is not None) else ""
        unit = f" {self.unit}" if self.unit else ""
        return f"{self.name}: {v}{unit}{bounds} -- {self.get_severity()}"


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
