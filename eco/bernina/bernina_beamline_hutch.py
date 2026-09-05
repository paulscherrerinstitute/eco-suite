from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent
from eco.devices_general.motors import SmaractStreamdevice, SmaractRecord



namespace.append_obj(
    "RefLaser_Aramis",
    "SAROP21-OLAS134",
    module_name="eco.xoptics.reflaser",
    name="reflaser_beamline",
    lazy=True,
)


namespace.append_obj(
    "SolidTargetDetectorPBPS",
    "SAROP21-PBPS133",
    use_calibration=False,
    diode_channels_raw={
        "up": "SAROP21-PBPS133:Lnk9Ch0-PP_VAL_PD1",
        "down": "SAROP21-PBPS133:Lnk9Ch0-PP_VAL_PD2",
        "left": "SAROP21-PBPS133:Lnk9Ch0-PP_VAL_PD0",
        "right": "SAROP21-PBPS133:Lnk9Ch0-PP_VAL_PD3",
    },
    name="mon_opt",
    module_name="eco.xdiagnostics.intensity_monitors",
    pipeline_computation="SAROP21-PBPS133_proc",
    lazy=True,
)




namespace.append_obj(
    "Pprm",
    "SAROP21-PPRM133",
    "SAROP21-PPRM133",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_opt",
    in_target=3,
    lazy=True,
)



namespace.append_obj(
    "SpectralEncoder",
    "SAROP21-PSEN135",
    module_name="eco.xdiagnostics.timetools",
    name="tt_opt",
    mirror_stages={
        "las_in_rx": "SLAAR21-LMOT-M538:MOT",
        "las_in_ry": "SLAAR21-LMOT-M537:MOT",
        "las_out_rx": "SLAAR21-LMOT-M536:MOT",
        "las_out_ry": "SLAAR21-LMOT-M535:MOT",
    },
    lazy=True,
)



namespace.append_obj(
    "AttenuatorAramis",
    "SAROP21-OATT135",
    shutter=NamespaceComponent(namespace,'xp'),
    set_limits=[],
    module_name="eco.xoptics.attenuator_aramis",
    name="att",
    lazy=True,
)


namespace.append_obj(
    "SlitPosWidth",
    "SAROP21-OAPU138",
    name="slit_att",
    lazy=True,
    module_name="eco.xoptics.slits",
)

namespace.append_obj(
    "Pprm",
    "SAROP21-PPRM138",
    "SAROP21-PPRM138",
    bs_channels={
        "intensity": "SAROP21-PPRM138:intensity",
        "xpos": "SAROP21-PPRM138:x_fit_mean",
        "ypos": "SAROP21-PPRM138:y_fit_mean",
    },
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_att",
    in_target=3,
    lazy=True,
)

namespace.append_obj(
    "KBMirrorBernina",
    "SAROP21-OKBV139",
    "SAROP21-OKBH140",
    module_name="eco.xoptics.kb_bernina",
    usd_table=NamespaceComponent(namespace, "usd_table"),
    name="kb",
    diffractometer=NamespaceComponent(namespace, "xrd"),
    lazy=True,
)
namespace.mark_beamline(
    "kb", types=("fel", "hutch"), z_source=139.0, kind="mirror",
    description="KB mirror pair (ver focus @ -3350 mm, hor focus @ -2600 mm)",
)


namespace.append_obj(
    "HexapodSymmetrie",
    name="usd_table",
    module_name="eco.endstations.hexapod",
    offset=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    lazy=True,
)


namespace.mark_beamline(
    "usd_table", types=("fel", "hutch"), z_source=140.4, kind="stage",
    description="upstream-diagnostics hexapod table (carries slit_kb/att_usd)",
)


namespace.append_obj(
    "SlitBladesGeneral",
    name="slit_kb",
    # normal config
    # def_blade_up={
    #     "args": [SmaractRecord, "SARES20-MCS1:MOT_2"],
    #     "kwargs": {},
    # },
    # def_blade_down={
    #     "args": [SmaractRecord, "SARES20-MCS1:MOT_1"],
    #     "kwargs": {},
    # },
    # def_blade_left={
    #     "args": [SmaractRecord, "SARES20-MCS1:MOT_9"],
    #     "kwargs": {},
    # },
    # def_blade_right={
    #     "args": [SmaractRecord, "SARES20-MCS1:MOT_4"],
    #     "kwargs": {},
    # },
    # backup config 2025-02-27 broken drivers
    def_blade_up={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_14"],
        "kwargs": {},
    },
    def_blade_down={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_13"],
        "kwargs": {},
    },
    def_blade_left={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_18"],
        "kwargs": {},
    },
    def_blade_right={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_4"],
        "kwargs": {},
    },
    module_name="eco.xoptics.slits",
    lazy=True,
)
namespace.mark_beamline(
    "slit_kb", types=("fel", "hutch"), z_source=140.15, kind="slit",
    description="slits upstream of the KB mirrors",
)

namespace.append_obj(
    "RefLaser_BerninaUSD",
    module_name="eco.xoptics.reflaser",
    name="reflaser",
    outpos_adjfs_path="/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/reflaser_usd_lastposition.json",
    lazy=True,
)

namespace.append_obj(
    "SolidTargetDetectorBerninaUSD",
    "SARES20-MCS1:MOT_12",
    channel_xpos="SARES21-PBPS141:XPOS",
    channel_ypos="SARES21-PBPS141:YPOS",
    channel_intensity="SARES21-PBPS141:INTENSITY",
    diode_channels_raw={
        "up": "SARES21-PBPS141:Lnk9Ch0-PP_VAL_PD1",
        "down": "SARES21-PBPS141:Lnk9Ch0-PP_VAL_PD2",
        "left": "SARES21-PBPS141:Lnk9Ch0-PP_VAL_PD0",
        "right": "SARES21-PBPS141:Lnk9Ch0-PP_VAL_PD3",
    },
    module_name="eco.xdiagnostics.intensity_monitors",
    pipeline_computation="SARES21-PBPS141_proc",
    name="mon_kb",
    lazy=True,
)
namespace.mark_beamline("mon_kb", types=("fel", "hutch"), z_source=140.25, kind="diagnostic")


namespace.append_obj(
    "TimetoolBerninaUSD",
    module_name="eco.timing.timing_diag",
    pvname_mirror="SARES20-MCS1:MOT_11",
    andor_spectrometer="SLAAR11-LSPC-ALCOR1",
    name="tt_kb",
    lazy=True,
)



namespace.append_obj(
    "ProfKbBernina",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_kb",
    pvname_mirror="SARES20-MCS1:MOT_11",
    lazy=True,
)
namespace.mark_beamline("prof_kb", types=("fel", "hutch"), z_source=140.25, kind="profile")


namespace.append_obj(
    "SlitBladesGeneral",
    name="slit_cleanup",
    # normal config
    # def_blade_up={
    #     "args": [SmaractRecord, "SARES20-MCS1:MOT_6"],
    #     "kwargs": {},
    # },
    # def_blade_down={
    #     "args": [SmaractRecord, "SARES20-MCS1:MOT_5"],
    #     "kwargs": {},
    # },
    # def_blade_left={
    #     "args": [SmaractRecord, "SARES20-MCS1:MOT_8"],
    #     "kwargs": {},
    # },
    # def_blade_right={
    #     "args": [SmaractRecord, "SARES20-MCS1:MOT_7"],
    #     "kwargs": {},
    # },
    # backup config 2025-02-27 broken drivers
    def_blade_up={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_6"],
        "kwargs": {},
    },
    def_blade_down={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_5"],
        "kwargs": {},
    },
    def_blade_left={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_17"],
        "kwargs": {},
    },
    def_blade_right={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_16"],
        "kwargs": {},
    },
    module_name="eco.xoptics.slits",
    lazy=True,
)
namespace.mark_beamline(
    "slit_cleanup", types=("fel", "hutch"), z_source=140.2, kind="slit",
    description="cleanup slit, upstream diagnostics",
)


namespace.append_obj(
    "Att_usd",
    name="att_usd",
    module_name="eco.xoptics.att_usd",
    xp=NamespaceComponent(namespace, "xp"),
    lazy=True,
)
namespace.mark_beamline(
    "att_usd", types=("fel", "hutch"), z_source=140.58, kind="attenuator",
    description="upstream diagnostics attenuator",
)





namespace.append_obj(
    "Bernina_XEYE",
    zoomstage_pv=NamespaceComponent(namespace,'config_bernina.xeye.zoomstage_pv._value'),
    camera_pv=NamespaceComponent(namespace,'config_bernina.xeye.camera_pv._value'),
    bshost=NamespaceComponent(namespace,'config_bernina.xeye.bshost._value'),
    bsport=NamespaceComponent(namespace,'config_bernina.xeye.bsport._value'),
    name="xeye",
    lazy=True,
    module_name="eco.xdiagnostics.profile_monitors",
)




namespace.append_obj(
    "GPS",
    module_name="eco.endstations.bernina_diffractometers",
    name="gps",
    pvname="SARES22-GPS",
    configuration=NamespaceComponent(namespace,'config_bernina.gps_config'),
    pgroup_adj=NamespaceComponent(namespace,'config_bernina.pgroup'),
    jf_config=NamespaceComponent(namespace,'config_JFs'),
    fina_hex_angle_offset="/sf/bernina/code/gac-bernina/eco_cnf_bernina/reference_values/hex_pi_angle_offset.json",
    xp=NamespaceComponent(namespace, "xp"),
    helium_control_valve={
        "pvbase": "SARES21-PS7071",
        "channel_number": 4,
        "name": "helium_control_valve",
        "pvname": "SARES20-CWAG-GPS01:DAC04",
    },
    illumination_mpod=[
        {
            "pvbase": "SARES21-PS7071",
            "channel_number": 5,
            "module_string": "LV_OMPV_1",
            "name": "illumination",
        }
    ],
    thc_config=NamespaceComponent(
        namespace, "config_bernina.thc_config", get_current_value=True
    ),
    event_master=NamespaceComponent(namespace, "event_master"),
    detectors_event_code=50,
    lazy=True,
)

namespace.append_obj(
    "StaeubliTx200",
    module_name="eco.endstations.bernina_robots",
    name="rob",
    #    pshell_url="http://PC14742:8080/",
    pshell_url="http://saresb-robot:8080/",
    robot_config=NamespaceComponent(namespace,'config_bernina.robot_config'),
    pgroup_adj=NamespaceComponent(namespace,'config_bernina.pgroup'),
    jf_config=NamespaceComponent(namespace,'config_JFs'),
    lazy=True,
)


namespace.append_obj(
    "SmarActOpenLoopRecord",
    module_name="eco.devices_general.motors",
    pvname="SARES23-USR:asyn",
    channel=14,
    name="openloop_horizontal",
    lazy=True,
)

namespace.append_obj(
    "XRDYou",
    module_name="eco.endstations.bernina_diffractometers",
    Id="SARES21-XRD",
    configuration=NamespaceComponent(namespace,'config_bernina.xrd_config'),
    pgroup_adj=NamespaceComponent(namespace,'config_bernina.pgroup'),
    jf_config=NamespaceComponent(namespace,'config_JFs'),
    invert_kappa_ellbow=NamespaceComponent(namespace,'config_bernina.invert_kappa_ellbow._value'),
    fina_hex_angle_offset="/sf/bernina/code/gac-bernina/eco_cnf_bernina/reference_values/hex_pi_angle_offset.json",
    xp=NamespaceComponent(namespace, "xp"),
    helium_control_valve={
        "pvbase": "SARES21-PS7071",
        "channel_number": 4,
        "name": "helium_control_valve",
        "pvname": "SARES20-CWAG-GPS01:DAC04",
    },
    illumination_mpod=[
        {
            "pvbase": "SARES21-PS7071",
            "channel_number": 5,
            "module_string": "LV_OMPV_1",
            "name": "illumination",
        }
    ],
    event_master=NamespaceComponent(namespace, "event_master"),
    detectors_event_code=50,
    name="xrd",
    lazy=True,
)
namespace.append_obj(
    "Crystals",
    module_name="eco.utilities.recspace",
    name="diffcalc",
    lazy=True,
)



namespace.append_obj(
    "DownstreamDiagnostic",
    name="dsd_table",
    module_name="eco.xdiagnostics.dsd",
    lazy=True,
)
namespace.mark_beamline("dsd_table", types=("fel", "hutch"), z_source=145.15, kind="stage")


namespace.append_obj(
    "Pprm_dsd",
    pvname="SARES20-DSDPPRM",
    pvname_camera="SARES20-PROF146-M1",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_dsd",
    lazy=True,
)
namespace.mark_beamline("prof_dsd", types=("fel", "hutch"), z_source=145.72, kind="profile")
# namespace.append_obj(
#    "SolidTargetDetectorPBPS",
#    "SARES20-DSDPBPS",
#    # diode_channels_raw={
#    #     "up":   "",
#    #     "down": "",
#    #     "left": "",
#    #     "right":"",
#    # },
#    module_name="eco.xdiagnostics.intensity_monitors",
#    name="mon_dsd",
#    lazy=True,
# )























