"""Aramis (Bernina / SAROP21 branch) vacuum, as a sequential beamline model.

This replicates the vacuum layout of the **central Bernina beam path** --
shared front end ``SARFE10`` -> Bernina optics branch ``SAROP21`` -> Bernina
endstation ``SARES21`` -- as an :class:`~eco.xoptics.beamline_assembly.Beamline`
whose components are the real vacuum devices (valves, gauges, turbo / ion getter
/ NEG / pre-pumps), each placed at its position along the beam and wrapped by
the generic eco classes in :mod:`eco.devices_general.vacuum`.

Where the numbers come from
---------------------------
The device inventory below was read out of the SwissFEL VCS caqtdm panels under
``/sf/vcs/config/qt`` by walking the Aramis photonics menu upstream from the
Bernina endstation:

    S_VCS_SARFE10-FE10 / -FE30-FE40 / -FE50   (front end, ~42-64 m)
    S_VCS_SAROP_90-100 / _110-120 / ...        (optics,   ~97-114 m)
    S_VCS_SARES21-ES                           (endstation, ~140-144 m)

Nothing here *controls* differently from those panels; it just re-expresses
them as eco objects. The middle number of a device tag encodes its approximate
``z_source`` in metres downstream of the undulator (a SwissFEL naming
convention -- e.g. ``SARES21-VPIG140-400`` sits near 140 m), which is what makes
the list **sequential by position**.

Only the well-characterised device families are modelled (gauges VMFR/VMCP/VMCC,
valves VVPG/VVPP, fast valves VVFV, turbo VPTM, ion pumps VPIG, NEG pumps VPNG,
pre-pumps VPFO). A few specialty gauge tags on this path (``VMMS`` / ``VMTC`` /
``VMFS``) and the ``EVKE`` fast-valve interlock signals are deliberately left
out -- their PV interface was not verified, and this module does not guess.

Sections
--------
The path is split into three sequential :class:`Beamline` sub-sections --
``front_end``, ``optics``, ``endstation`` -- each carrying its own live
"beam-path clear" condition (all its isolation valves open). They are joined
with ``+`` into one beamline, so ``aramis_vacuum.optics`` etc. address a
section, and the whole thing merges into the main Bernina beamline model the
same way its hutches already do.

Usage::

    from eco.xoptics.aramis_vacuum import make_aramis_vacuum, VACUUM_KINDS
    vac = make_aramis_vacuum()          # lazy: no EPICS touched until accessed
    vac.show_layout(ref="source")       # full position table (text)
    vac.show_layout(ref="source", kinds=VACUUM_KINDS)   # vacuum only
    vac.svg_panel()                     # clickable SVG panel (Jupyter or Qt window)
    vac.endstation.vptm140_700.speed()  # reach a real device

    # merge into the existing Bernina beamline model:
    from eco.xoptics.beamline_bernina import (
        make_bernina_front_end, make_bernina_experiment_hutch)
    bernina = make_bernina_front_end() + make_bernina_experiment_hutch()
    bernina_with_vacuum = bernina + make_aramis_vacuum()
    bernina_with_vacuum.show_layout(kinds=NON_VACUUM_KINDS)  # hide vacuum
"""

from .beamline_assembly import Beamline

#: nominal Bernina sample position [m downstream of the Aramis undulator],
#: matching ``beamline_bernina.Z0_SOURCE_BERNINA_SAMPLE``; used as the local
#: sample reference so ``z_sample`` is derivable for every section.
Z0_SOURCE_BERNINA_SAMPLE = 142.0

#: beamline ``kind`` tags that denote a vacuum component. Pass as
#: ``show_layout(kinds=VACUUM_KINDS)`` to view *only* vacuum, or use
#: :data:`NON_VACUUM_KINDS` to hide it. This is how a beamline view lists
#: vacuum devices in or leaves them out.
VACUUM_KINDS = {"valve", "gauge", "pump"}

#: every other ``kind`` used across the beamline model (see
#: ``Beamline.KIND_COLORS``) -- i.e. "show the optics, hide the vacuum".
NON_VACUUM_KINDS = {
    "source", "mirror", "mono", "optic", "slit", "attenuator", "shutter",
    "stopper", "profile", "diagnostic", "chopper", "stage", "sample",
    "marker", "zone",
}

# --- device-type -> (eco class name, beamline kind tag) -------------------
# Fast valves and pre/turbo/ion/NEG pumps are tagged with the generic
# "valve"/"pump" kinds so the beamline's blocking-beam and vacuum-filter logic
# treats them uniformly; the specific device class still carries the full
# behaviour. NEG pumps (VPNG) share the 4UHV controller interface of ion pumps,
# so they reuse IonPump.
_TYPE_TO_CLASS = {
    "gauge": ("VacuumGauge", "gauge"),
    "valve": ("Valve", "valve"),
    "fastvalve": ("FastValve", "valve"),
    "turbo": ("TurboPump", "pump"),
    "ionpump": ("IonPump", "pump"),
    "negpump": ("IonPump", "pump"),
    "prepump": ("PrePump", "pump"),
}

# --- position-ordered device inventory of the Bernina beam path -----------
# (tag, z_source [m], device type). Read from the VCS panels; see module
# docstring. Grouped into the three sequential vacuum sections.
FRONT_END_DEVICES = [
    ("SARFE10-VMFR042-A010", 42, "gauge"),
    ("SARFE10-VMFR042-B010", 42, "gauge"),
    ("SARFE10-VPIG042-A010", 42, "ionpump"),
    ("SARFE10-VPIG042-B010", 42, "ionpump"),
    ("SARFE10-VVFV042-B010", 42, "fastvalve"),
    ("SARFE10-VVPG042-A010", 42, "valve"),
    ("SARFE10-VPIG043-B010", 43, "ionpump"),
    ("SARFE10-VMFR048-B030", 48, "gauge"),
    ("SARFE10-VPIG048-B030", 48, "ionpump"),
    ("SARFE10-VMFR051-C070", 51, "gauge"),
    ("SARFE10-VMFR052-D020", 52, "gauge"),
    ("SARFE10-VPIG052-D010", 52, "ionpump"),
    ("SARFE10-VVPG052-D010", 52, "valve"),
    ("SARFE10-VMFR053-D030", 53, "gauge"),
    ("SARFE10-VPIG053-D020", 53, "ionpump"),
    ("SARFE10-VVPG053-010", 53, "valve"),
    ("SARFE10-VVPG054-010", 54, "valve"),
    ("SARFE10-VMFR055-010", 55, "gauge"),
    ("SARFE10-VPIG055-010", 55, "ionpump"),
    ("SARFE10-VMCP056-010", 56, "gauge"),
    ("SARFE10-VPFO056-010", 56, "prepump"),
    ("SARFE10-VPIG056-020", 56, "ionpump"),
    ("SARFE10-VVPP056-010", 56, "valve"),
    ("SARFE10-VMFR059-030", 59, "gauge"),
    ("SARFE10-VMFR059-050", 59, "gauge"),
    ("SARFE10-VPIG059-030", 59, "ionpump"),
    ("SARFE10-VVPG059-020", 59, "valve"),
    ("SARFE10-VMFR062-040", 62, "gauge"),
    ("SARFE10-VPIG062-040", 62, "ionpump"),
    ("SARFE10-VMCP063-050", 63, "gauge"),
    ("SARFE10-VMFR063-060", 63, "gauge"),
    ("SARFE10-VPIG063-060", 63, "ionpump"),
    ("SARFE10-VVPG063-060", 63, "valve"),
    ("SARFE10-VPIG064-070", 64, "ionpump"),
]

OPTICS_DEVICES = [
    ("SAROP21-VVPG097-030", 97, "valve"),
    ("SAROP21-VMFR098-030", 98, "gauge"),
    ("SAROP21-VPIG098-030", 98, "ionpump"),
    ("SAROP21-VVPG098-040", 98, "valve"),
    ("SAROP21-VPIG099-040", 99, "ionpump"),
    ("SAROP21-VPIG102-050", 102, "ionpump"),
    ("SAROP21-VMFR103-050", 103, "gauge"),
    ("SAROP21-VPIG103-060", 103, "ionpump"),
    ("SAROP21-VMFR104-050", 104, "gauge"),
    ("SAROP21-VVPG104-050", 104, "valve"),
    ("SAROP21-VMFR105-060", 105, "gauge"),
    ("SAROP21-VPIG105-070", 105, "ionpump"),
    ("SAROP21-VVFV105-010", 105, "fastvalve"),
    ("SAROP21-VVPG105-050", 105, "valve"),
    ("SAROP21-VMFR110-070", 110, "gauge"),
    ("SAROP21-VPIG110-080", 110, "ionpump"),
    ("SAROP21-VMFR112-080", 112, "gauge"),
    ("SAROP21-VPIG112-A010", 112, "ionpump"),
    ("SAROP21-VVPG113-060", 113, "valve"),
    ("SAROP21-VMCP114-100", 114, "gauge"),
    ("SAROP21-VMFR114-090", 114, "gauge"),
    ("SAROP21-VMFR114-100", 114, "gauge"),
    ("SAROP21-VPIG114-090", 114, "ionpump"),
    ("SAROP21-VPIG114-100", 114, "ionpump"),
]

ENDSTATION_DEVICES = [
    ("SARES21-VMCP140-600", 140, "gauge"),
    ("SARES21-VMFR140-500", 140, "gauge"),
    ("SARES21-VMFR140-510", 140, "gauge"),
    ("SARES21-VPFO140-750", 140, "prepump"),
    ("SARES21-VPIG140-400", 140, "ionpump"),
    ("SARES21-VPIG140-410", 140, "ionpump"),
    ("SARES21-VPTM140-700", 140, "turbo"),
    ("SARES21-VVPG140-230", 140, "valve"),
    ("SARES21-VVPG140-240", 140, "valve"),
    ("SARES21-VVPG140-290", 140, "valve"),
    ("SARES21-VVPP140-300", 140, "valve"),
    ("SARES21-VMCC141-530", 141, "gauge"),
    ("SARES21-VMCP141-531", 141, "gauge"),
    ("SARES21-VMCP141-610", 141, "gauge"),
    ("SARES21-VMFR141-520", 141, "gauge"),
    ("SARES21-VMFR141-531", 141, "gauge"),
    ("SARES21-VPFO141-760", 141, "prepump"),
    ("SARES21-VPIG141-420", 141, "ionpump"),
    ("SARES21-VPIG141-430", 141, "ionpump"),
    ("SARES21-VPIG141-431", 141, "ionpump"),
    ("SARES21-VPNG141-470", 141, "negpump"),
    ("SARES21-VPTM141-710", 141, "turbo"),
    ("SARES21-VVPG141-250", 141, "valve"),
    ("SARES21-VVPG141-260", 141, "valve"),
    ("SARES21-VVPG141-270", 141, "valve"),
    ("SARES21-VVPG141-310", 141, "valve"),
    ("SARES21-VVPP141-320", 141, "valve"),
    ("SARES21-VMCC142-540", 142, "gauge"),
    ("SARES21-VMCC142-560", 142, "gauge"),
    ("SARES21-VMCC142-580", 142, "gauge"),
    ("SARES21-VMCP142-550", 142, "gauge"),
    ("SARES21-VMCP142-570", 142, "gauge"),
    ("SARES21-VMCP142-590", 142, "gauge"),
    ("SARES21-VMCP142-620", 142, "gauge"),
    ("SARES21-VMCP142-630", 142, "gauge"),
    ("SARES21-VMCP142-640", 142, "gauge"),
    ("SARES21-VPIG142-450", 142, "ionpump"),
    ("SARES21-VPIG142-460", 142, "ionpump"),
    ("SARES21-VPNG142-440", 142, "negpump"),
    ("SARES21-VPTM142-720", 142, "turbo"),
    ("SARES21-VPTM142-730", 142, "turbo"),
    ("SARES21-VPTM142-740", 142, "turbo"),
    ("SARES21-VMFR143-600", 143, "gauge"),
    ("SARES21-VPIG143-390", 143, "ionpump"),
    ("SARES21-VMFR144-600", 144, "gauge"),
    ("SARES21-VPIG144-400", 144, "ionpump"),
]

#: the three sequential vacuum sections, upstream -> downstream.
SECTIONS = [
    ("front_end", FRONT_END_DEVICES, "SARFE10 shared front end (~42-64 m)"),
    ("optics", OPTICS_DEVICES, "SAROP21 Bernina optics branch (~97-114 m)"),
    ("endstation", ENDSTATION_DEVICES, "SARES21 Bernina endstation (~140-144 m)"),
]


def _name_for(tag):
    """``SARES21-VPIG140-400`` -> ``sares21_vpig140_400`` -- a valid, unique
    attribute name (the full tag disambiguates same-position devices that
    differ only by suffix, e.g. ...-A010 vs ...-B010)."""
    return tag.lower().replace("-", "_")


def _valve_names(devices):
    """Names of the isolation/fast valves in a device list (kind == valve)."""
    return [
        _name_for(tag) for tag, _z, typ in devices
        if _TYPE_TO_CLASS[typ][1] == "valve"
    ]


def _make_beam_path_clear_condition(section, valve_names):
    """Return a zero-arg bool callable: True iff every valve in this section
    currently reads open. Defensive -- a valve that can't be read (no live
    connection / DummyComponent) makes the whole condition fall back to False
    rather than raising, mirroring how the caqtdm synoptic only lights a path
    segment when all its valves are confirmed open.
    """

    def condition():
        for vname in valve_names:
            try:
                valve = getattr(section, vname)
                if not bool(valve.is_open.get_current_value()):
                    return False
            except Exception:
                return False
        return True

    return condition


def _class_for(device_type):
    """Resolve a device-type string to its eco class (imported lazily so this
    module imports with no EPICS/hardware dependency) and beamline kind tag."""
    from eco.devices_general import vacuum as _vac

    class_name, kind = _TYPE_TO_CLASS[device_type]
    return getattr(_vac, class_name), kind


def _build_section(section_name, devices, description, lazy=True, strict=False, subtype=None):
    """Build one vacuum section as a Beamline of positioned devices plus a
    live 'beam-path clear' zone condition spanning it.

    Each device is *also* registered via `Assembly.mark_beamline` (types=
    ``("vacuum", subtype)``, `subtype` defaulting to `section_name` -- see
    `BerninaVacuum`, which overrides it to "hutch" for the "endstation"
    section to match that view's vocabulary) -- purely additive, alongside
    (not instead of) the `add_component` call above that drives this
    module's own `Beamline`-based position table/diagram. This is what lets
    `namespace.beamline.vacuum.<subtype>` unfold into each section's real
    devices once `BerninaVacuum` in turn tags `section` itself (see there).
    """
    subtype = subtype or section_name
    section = Beamline(
        name=section_name,
        z0_source=Z0_SOURCE_BERNINA_SAMPLE,
        source_name="undulator",
        description=description,
    )
    for tag, z, device_type in devices:
        cls, kind = _class_for(device_type)
        name = _name_for(tag)
        section.add_component(
            cls, tag, name=name, z_source=z, kind=kind,
            description=f"{device_type} ({tag})", lazy=lazy, strict=strict,
        )
        section.mark_beamline(
            name, types=("vacuum", subtype), z_source=z, kind=kind,
            description=f"{device_type} ({tag})",
        )
    valve_names = _valve_names(devices)
    if valve_names:
        z_first = min(z for _t, z, _ty in devices)
        z_last = max(z for _t, z, _ty in devices)
        section.add_zone_condition(
            f"{section_name}_beam_path_clear",
            _make_beam_path_clear_condition(section, valve_names),
            z_source=z_first,
            z_source_end=z_last,
            description="all isolation/fast valves in this section open",
        )
    return section


def make_aramis_vacuum(name="aramis_vacuum", lazy=True, strict=False):
    """Build the full Bernina-path vacuum model: the three sequential sections
    (:data:`SECTIONS`) joined into one :class:`Beamline`.

    Parameters
    ----------
    lazy : bool
        Defer building each device until first accessed (default True), so
        constructing the model touches no EPICS -- the layout table/diagram
        still list every device immediately from the position registry.
    strict : bool
        If False (default), a device whose constructor fails becomes a
        ``DummyComponent`` instead of taking the model down.

    Returns
    -------
    Beamline
        with ``.front_end`` / ``.optics`` / ``.endstation`` sub-sections; ``+``
        with the main Bernina beamline to merge vacuum into it.
    """
    sections = [
        _build_section(sname, devs, descr, lazy=lazy, strict=strict)
        for sname, devs, descr in SECTIONS
    ]
    joined = sections[0]
    for section in sections[1:]:
        joined = joined + section
    joined.name = name
    return joined


def demo_layout():
    """Hardware-free sanity check: build the model lazily and print the
    position table without touching EPICS. Run as
    ``python -c 'from eco.xoptics.aramis_vacuum import demo_layout; demo_layout()'``.
    """
    vac = make_aramis_vacuum(lazy=True)
    print(f"# {sum(len(d) for _n, d, _ in SECTIONS)} vacuum devices on the "
          "Bernina path (SARFE10 -> SAROP21 -> SARES21)\n")
    vac.show_layout(ref="source")
    return vac
