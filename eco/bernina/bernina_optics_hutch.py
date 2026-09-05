
from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent






namespace.append_obj(
    "SlitBlades",
    "SAROP21-OAPU092",
    name="slit_switch",
    module_name="eco.xoptics.slits",
    lazy=True,
)
namespace.mark_beamline(
    "slit_switch", types=("fel", "optics"), z_source=92.0, kind="slit",
    description="switchyard slit",
)

namespace.append_obj(
    "OffsetMirrorsBernina",
    name="offset",
    lazy=True,
    module_name="eco.xoptics.offsetMirrors_new",
)
namespace.mark_beamline(
    "offset", types=("fel", "optics"), z_source=94.0, kind="mirror",
    description="offset mirror pair (mirr1@92m, mirr2@96m)",
)


namespace.append_obj(
    "Pprm",
    "SAROP11-PPRM066",
    "SAROP11-PPRM066",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_mirr_alv1",
    in_target=3,
    lazy=True,
)
namespace.mark_beamline(
    "prof_mirr_alv1", types=("fel", "optics"), z_source=66.0, kind="profile",
    description="shared Aramis switchyard profile monitor, upstream of the Alvra/Bernina split",
)

namespace.append_obj(
    "Pprm",
    "SAROP21-PPRM094",
    "SAROP21-PPRM094",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_mirr1",
    in_target=3,
    lazy=True,
)
namespace.mark_beamline("prof_mirr1", types=("fel", "optics"), z_source=94.0, kind="profile")

namespace.append_obj(
    "Pprm",
    "SAROP21-PPRM113",
    "SAROP21-PPRM113",
    bs_channels={
        "intensity": "SAROP21-PPRM113:intensity",
        "xpos": "SAROP21-PPRM113:x_fit_mean",
        "ypos": "SAROP21-PPRM113:y_fit_mean",
    },
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_mono",
    in_target=3,
    lazy=True,
)
namespace.mark_beamline("prof_mono", types=("fel", "optics"), z_source=113.0, kind="profile")


namespace.append_obj(
    "SlitBlades",
    "SAROP21-OAPU102",
    name="slit_mono",
    module_name="eco.xoptics.slits",
    lazy=True,
)
namespace.mark_beamline("slit_mono", types=("fel", "optics"), z_source=102.0, kind="slit")

namespace.append_obj(
    "SolidTargetDetectorPBPS",
    "SAROP21-PBPS103",
    use_calibration=False,
    diode_channels_raw={
        "up": "SAROP21-PBPS103:Lnk9Ch0-PP_VAL_PD1",
        "down": "SAROP21-PBPS103:Lnk9Ch0-PP_VAL_PD2",
        "left": "SAROP21-PBPS103:Lnk9Ch0-PP_VAL_PD0",
        "right": "SAROP21-PBPS103:Lnk9Ch0-PP_VAL_PD3",
    },
    name="mon_mono",
    module_name="eco.xdiagnostics.intensity_monitors",
    pipeline_computation="SAROP21-PBPS103_proc",
    lazy=True,
)
namespace.mark_beamline("mon_mono", types=("fel", "optics"), z_source=103.0, kind="diagnostic")


namespace.append_obj(
    "XrayPulsePicker",
    pvbase="SAROP21-OPPI113",
    evronoff="SGE-CPCW-72-EVR0:FrontUnivOut15-Ena-SP",
    evrsrc="SGE-CPCW-72-EVR0:FrontUnivOut15-Src-SP",
    evr_output_base="SGE-CPCW-72-EVR0:FrontUnivOut15",
    evr_pulser_base="SGE-CPCW-72-EVR0:Pul0",
    event_master=NamespaceComponent(namespace, "event_master"),
    sequencer=NamespaceComponent(namespace, "seq"),
    name="xp",
    module_name="eco.xoptics.pp",
    lazy=True,
)
namespace.mark_beamline(
    "xp", types=("fel", "optics"), z_source=113.0, kind="chopper", description="x-ray pulse picker",
)

namespace.append_obj(
    "SafetyShutter",
    "SGE01-EPKT822:BST1_oeffnen",
    name="sshut_opt",
    module_name="eco.xoptics.shutters",
    lazy=True,
)
namespace.mark_beamline(
    "sshut_opt", types=("fel", "optics"), z_source=115.0, kind="shutter",
    description="Bernina optics-hutch safety shutter",
)