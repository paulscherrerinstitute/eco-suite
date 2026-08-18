"""Bernina vacuum system: the beamline-wide interlock plus the full device
inventory in physical order from the front end.

The device inventory (SARFE10 front end -> SAROP21 Bernina optics ->
SARES21 Bernina endstation, ~104 valves/gauges/pumps) is not duplicated here:
it's built from the same ``SECTIONS`` table and section-builder used by
``eco.xoptics.aramis_vacuum`` (see that module for where the numbers come
from), so there is one source of truth for the inventory.
"""

from eco import Assembly
from eco.epics.adjustable import AdjustablePvEnum


class BerninaVacuum(Assembly):
    """``all_valves_open`` is the real beamline-wide PLC interlock
    (``SAROP21-VVPG-0010:PLC_OPEN_F``). With ``with_devices=True`` (default)
    the full device inventory is attached as three sequential sections, in
    physical order from the front end: :attr:`front_end`, :attr:`optics`,
    :attr:`endstation` (each a ``Beamline`` with its own
    ``*_beam_path_clear`` condition -- see ``eco.xoptics.aramis_vacuum``).

    ``with_devices=False`` gives the original, lean single-PV object -- used
    by ``eco.xoptics.beamline_bernina`` where ``BerninaVacuum`` is embedded as
    one interlock component inside a beamline that (optionally) already
    carries the same device inventory itself, so attaching it twice would be
    redundant.

    ``lazy`` (default True) is forwarded to the section builder: devices are
    not constructed (no EPICS touched) until actually accessed.
    """

    #: `eco.xoptics.aramis_vacuum` section name -> the subtype word used by
    #: `namespace.beamline.vacuum.<subtype>` (see `mark_beamline`'s path/
    #: subtype doc) -- "endstation" reads as "hutch" there, to match the
    #: same vocabulary used for the "fel" type's own hutch components
    #: (see `eco.bernina.bernina`); the other two sections keep their name.
    SECTION_SUBTYPES = {"endstation": "hutch"}

    def __init__(self, name=None, with_devices=True, lazy=True):
        super().__init__(name=name)
        self._append(
            AdjustablePvEnum, "SAROP21-VVPG-0010:PLC_OPEN_F", name="all_valves_open"
        )
        if with_devices:
            from eco.xoptics.aramis_vacuum import SECTIONS, _build_section

            for section_name, devices, description in SECTIONS:
                subtype = self.SECTION_SUBTYPES.get(section_name, section_name)
                section = _build_section(
                    section_name, devices, description, lazy=lazy, strict=False,
                    subtype=subtype,
                )
                self._append(section, name=section_name, is_status="recursive")
                # Anchor position for this section on BerninaVacuum itself --
                # its own z is the section's first device, and its real
                # per-device breakdown unfolds underneath (see
                # aramis_vacuum._build_section's mark_beamline calls, which
                # tagged `section`'s own devices with the same subtype).
                self.mark_beamline(
                    section_name, types=("vacuum", subtype),
                    z_source=min(z for _tag, z, _type in devices),
                    kind="vacuum", description=description,
                )
