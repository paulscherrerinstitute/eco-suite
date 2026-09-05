from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent

namespace.append_obj(
    "BerninaVacuum",
    name="vacuum",
    module_name="eco.endstations.bernina_vacuum",
    lazy=True,
)
# "vacuum" as its own top-level beamline type, same front_end/optics/hutch
# subtypes as "fel" above -- BerninaVacuum itself tags its 3 sections (see
# eco.endstations.bernina_vacuum), and each section in turn tags its own
# real devices (see eco.xoptics.aramis_vacuum._build_section), so
# namespace.beamline.vacuum.<subtype> unfolds two levels deep into the
# actual valves/gauges/pumps. z_source=90.0 here is just this top anchor
# row's own position (roughly the middle of the whole 42-144m span); the
# unfolded sections/devices each carry their own real z.
namespace.mark_beamline(
    "vacuum", types="vacuum", z_source=90.0, kind="vacuum",
    description="Bernina vacuum system (front end + optics + endstation/hutch)",
)

# Consolidated prepump/venting system (see eco.devices_general.vacuum.prepump
# for the model + spec this implements). One structured config dict holds
# every channel's valve/gauge PV names, so the whole system can be edited
# here as a single literal. A channel with all-None PVs still builds (just
# empty). Lazy, so nothing here touches EPICS until `prepump` is accessed.
# Turbo pumps are not modelled here -- they're served by the prepump system
# but not part of it (see the module docstring).
_prepump_config = {
    "gp": "SARES21-VMCP142-620",  # common prevac-line gauge Gp base
    "roots_pump": "SARES21-VPFO140-750",  # Roots pump base
    "p_target": 0.5,  # "pumped" threshold [mbar]
    "p_vent_target": 1000.0,  # "vented" threshold [mbar]
    "lines": {
        "line1_usd": {
            "gauge": "SARES21-VMCP140-600",  # G1 gauge base
            "valve_prevac": "SARES21-VVPP140-300",  # P1 roughing valve
            "valve_vent": "SARES21-VVPP142-340",  # V1 vent valve
        },
        "line2_lic": {
            "gauge": "SARES21-VMCP141-610",  # G2 gauge base
            "valve_prevac": "SARES21-VVPP141-320",  # P2 roughing valve
            "valve_vent": "SARES21-VVPP142-370",  # V2 vent valve
        },
        # channels 3-4: valve PVs noted from commissioning, gauges not yet
        # assigned -- fill in the TODOs below, then uncomment.
        "line3": {
            "gauge": "SARES21-VMCP142-570",  # TODO: G3 gauge base
            "valve_prevac": "SARES21-VVPG142-330",  # TODO: was noted as "...142-330"
            "valve_vent": "SARES21-VVPG143-350",    # TODO: was noted as "...143-350"
        },
        # "line4": {
        #     "gauge": None,  # TODO: G4 gauge base
        #     "valve_prevac": None,  # TODO: not yet noted
        #     "valve_vent": None,    # TODO: was noted as "...142-340" -- looked
        #                            # identical to line1_usd's, double check
        # },
        # two more optional slots, not yet cabled:
        # "line5": {"gauge": None, "valve_prevac": None, "valve_vent": None},
        # "line6": {"gauge": None, "valve_prevac": None, "valve_vent": None},
    },
}
namespace.append_obj(
    "make_prepump_system",
    _prepump_config,
    name="prepump",
    module_name="eco.devices_general.vacuum.prepump",
    lazy=True,
)