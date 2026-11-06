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
# for the model + spec this implements). Its PVs live in one place,
# BERNINA_PREPUMP_CONFIG in that module, shared with the caQtDM panel's
# buttons (eco.devices_general.vacuum.prepump_cli), which import only that
# module, not this namespace. Lazy, so nothing here touches EPICS until
# `prepump` is accessed. Turbo pumps are not modelled here -- they're served
# by the prepump system but not part of it (see the module docstring).
namespace.append_obj(
    "make_bernina_prepump_system",
    name="prepump",
    module_name="eco.devices_general.vacuum.prepump",
    lazy=True,
)
