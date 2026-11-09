"""Concrete Bernina alarm-overview panels, built on `eco.elements.alarm`.

Two panels, both reachable from the operator "Alarms overview" launcher entry
(``Strip charts`` menu in ``/sf/bernina/config/launcher/S_charts.json`` ->
``/sf/bernina/bin/alarms_caqtdm`` -> ``alarms.ui``):

* :class:`BerninaAlarmsOverview` -- the main panel itself (SwissFEL / Laser /
  X-ray Pointing / Feedbacks / Timetool sections).
* :class:`PapamollAlarms` -- either of the two "Expert" panels that panel's
  ``-35 fs`` / ``-100 fs`` buttons open, for the Papamoll (Coherent) pump
  laser; pass the same key as the source filename suffix
  (``"26l_dean_1um_35fs"`` or ``"26h_orr_510nm_100fs"``).

Both are plain `eco.elements.alarm.AlarmPanel`\\ s -- see that module for what
that gets you (``.status()``, tab-completion, ``.show(live=True)`` for a
clickable colour-coded panel, ...). Channel data lives in
:mod:`eco.devices_general.alarms.data`, distilled from the real ``.ui`` panels
under ``/sf/bernina/config/src/caqtdm/alarms/`` -- see that module's docstring
for provenance and caveats.
"""

from eco.alarms.system import AlarmSystem
from eco.elements.alarm import AlarmPanel

from .data import MAIN_ALARMS_SECTIONS, PAPAMOLL_ALARMS_SECTIONS, PAPAMOLL_LABELS


class BerninaAlarmsOverview(AlarmPanel):
    """The main Bernina "Alarms overview" panel (``alarms.ui``): electron
    beam/shutter interlocks, laser synchronisation, X-ray pointing monitors,
    the mono/laser/timetool feedback-loop status messages, and the timetool
    edge-fit health."""

    #: sections that only make sense with X-rays on the sample; their alarms
    #: are inhibited while any of `NO_BEAM_INHIBIT` is not ok
    NEEDS_BEAM = ("X-ray Pointing", "Timetool")
    NO_BEAM_INHIBIT = [
        "swissfel.Electron_Beam",
        "swissfel.Photon_Shutter",
        "swissfel.Bernina_Shutter",
    ]

    def __init__(self, name=None):
        sections = [
            (
                title,
                [
                    dict({"inhibit_by": list(self.NO_BEAM_INHIBIT)}, **ch)
                    if title in self.NEEDS_BEAM
                    else ch
                    for ch in channels
                ],
            )
            for title, channels in MAIN_ALARMS_SECTIONS
        ]
        super().__init__(sections, name=name)


class PapamollAlarms(AlarmPanel):
    """One Papamoll pump-laser alarm-overview panel.

    ``config``: one of ``PAPAMOLL_ALARMS_SECTIONS`` -- currently
    ``"26l_dean_1um_35fs"`` (-35 fs, 1 µm pump) or ``"26h_orr_510nm_100fs"``
    (-100 fs, 510 nm pump), the two panels wired into the main overview's
    "Expert" buttons. Both share most sections (oscillator, amplifiers,
    timing, chillers, pointing feedback) and differ mainly in the final
    "Pump Laser ..." section.
    """

    def __init__(self, config, name=None):
        if config not in PAPAMOLL_ALARMS_SECTIONS:
            raise ValueError(
                f"unknown Papamoll alarm config {config!r}; known: "
                f"{sorted(PAPAMOLL_ALARMS_SECTIONS)}"
            )
        self.config = config
        self.config_label = PAPAMOLL_LABELS.get(config, config)
        super().__init__(PAPAMOLL_ALARMS_SECTIONS[config], name=name)


class BerninaAlarmSystem(AlarmSystem):
    """The namespace object ``alarms``: the overview panel as
    ``alarms.overview``, user-added alarms as ``alarms.custom``, plus shift
    plan and phone paging -- see `eco.alarms.system.AlarmSystem`.

    The panel's channels start at level ``display`` (shown, never paged);
    raise single ones with e.g.
    ``alarms.overview.swissfel.Electron_Beam.level("critical")``.
    """

    def __init__(self, name="alarms", namespace="eco.bernina.bernina:namespace"):
        super().__init__(
            panels={"overview": (BerninaAlarmsOverview, (), {})},
            name=name,
            namespace=namespace,
        )
