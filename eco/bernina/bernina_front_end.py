"""Front-end (`"fel"`/`"front_end"` subtype, SARFE10-prefixed, up to the
end-of-front-end shutter) component registrations, delegated out of
`bernina.py`'s own script instead of living inline there.

Submodule importing `namespace` back from `eco.bernina.bernina` and
registering its own namespace items there, so `bernina.py` only needs to
import this module rather than own every front-end registration inline -
unlike the earlier `bernina_beamline.py` draft, which referenced a bare
`namespace` name without ever importing it and would raise NameError if
actually run.

Import ordering requirement: `bernina.py` must import this module *after*
`namespace = Namespace(...)` has been assigned there - nothing here depends
on any other bernina.py global. Within this module, `pshut_und` is
registered before `att_fe` (which cross-references it via
`NamespaceComponent`, needing `pshut_und` to already be a *known* namespace
name - i.e. appended to `namespace.lazy_items` - not yet built).

This does not re-trigger `bernina.py`'s own execution: by the time this
module's `from eco.bernina.bernina import namespace` line runs, Python has
already registered the (still-executing) `eco.bernina.bernina` module in
`sys.modules`, so the import just reads `namespace` off its current
(partially-built) `__dict__` instead of re-running the module - the standard
reentrant-import behavior, not anything eco-specific.
"""

from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent





namespace.append_obj(
    "PhotonShutter",
    "SARFE10-OPSH044:REQUEST",
    name="pshut_und",
    module_name="eco.xoptics.shutters",
    lazy=True,
)
namespace.mark_beamline(
    "pshut_und", types=("fel", "front_end"), z_source=44.0, kind="shutter",
    description="first shutter after the undulators",
)

namespace.append_obj(
    "JJSlitUnd",
    name="slit_und",
    module_name="eco.xoptics.slits",
    lazy=True,
)







namespace.append_obj(
    "GasDetector",
    name="mon_und_gas",
    module_name="eco.xdiagnostics.intensity_monitors",
    lazy=True,
)
namespace.mark_beamline(
    "mon_und_gas", types=("fel", "front_end"), z_source=50.0, kind="diagnostic",
    description="gas monitor",
)

namespace.append_obj(
    "SolidTargetDetectorPBPS",
    "SARFE10-PBPS053",
    # diode_channels_raw={
    #     "up": "SARFE10-CVME-PHO6212:Lnk9Ch13-DATA-SUM",
    #     "down": "SARFE10-CVME-PHO6212:Lnk9Ch12-DATA-SUM",
    #     "left": "SARFE10-CVME-PHO6212:Lnk9Ch14-DATA-SUM",
    #     "right": "SARFE10-CVME-PHO6212:Lnk9Ch15-DATA-SUM",
    # },
    name="mon_und",
    use_calibration=False,
    module_name="eco.xdiagnostics.intensity_monitors",
    pipeline_computation="SARFE10-PBPS053_proc",
    lazy=True,
)
namespace.mark_beamline(
    "mon_und", types=("fel", "front_end"), z_source=53.0, kind="diagnostic",
    description="intensity/position monitor after the undulator",
)


namespace.append_obj(
    "Xspect",
    name="xspect",
    lazy=True,
    module_name="eco.xdiagnostics.xspect",
)
namespace.mark_beamline(
    "xspect", types=("fel", "front_end"), z_source=59.0, kind="diagnostic",
    description="single shot Xray spectrometer",
)

namespace.append_obj(
    "PhotonShutter",
    "SARFE10-OPSH059:REQUEST",
    name="pshut_fe",
    module_name="eco.xoptics.shutters",
    lazy=True,
)
namespace.mark_beamline(
    "pshut_fe", types=("fel", "front_end"), z_source=59.0, kind="shutter",
    description="photon shutter, end of front end",
)

namespace.append_obj(
    "AttenuatorAramis",
    "SARFE10-OATT053",
    shutter=NamespaceComponent(namespace, "pshut_und"),
    set_limits=[],
    module_name="eco.xoptics.attenuator_aramis",
    name="att_fe",
    lazy=True,
)
namespace.mark_beamline(
    "att_fe",
    types=("fel", "front_end"),
    z_source=53.0,
    kind="attenuator",
    description="front-end attenuator",
)

namespace.append_obj(
    "Pprm",
    "SARFE10-PPRM064",
    "SARFE10-PPRM064",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_fe",
    in_target=3,
    lazy=True,
)
namespace.mark_beamline(
    "prof_fe", types=("fel", "front_end"), z_source=64.0, kind="profile",
    description="front-end profile monitor",
)

namespace.append_obj(
    "SafetyShutter",
    "SGE01-EPKT820:BST1_oeffnen",
    name="sshut_fe",
    module_name="eco.xoptics.shutters",
    lazy=True,
)