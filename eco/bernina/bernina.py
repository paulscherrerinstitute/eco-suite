import json
from pathlib import Path
from threading import Thread
import eco
from eco.acquisition.scan import NumpyEncoder
from eco.devices_general.digitizers import DigitizerIoxosBoxcarChannel
from eco.devices_general.powersockets import MpodModule
from eco.devices_general.wago import AnalogOutput
from eco.elements.adjustable import AdjustableFS
from eco.elements.adjustable import AdjustableVirtual
from eco.loptics.bernina_experiment import DelayCompensation
from eco.devices_general.cameras_swissfel import CameraBasler
from epics import PV
import time
import pickle

# from eco.endstations.bernina_sample_environments import Organic_crystal_breadboard_old
from eco.motion.smaract import SmaractController
from eco.timing.event_timing_new_new import EvrOutput
from .config import components

# from .config import config as config_berninamesp
from ..utilities.config import Namespace, NamespaceComponent
from ..aliases import NamespaceCollection
import pyttsx3

from ..utilities.path_alias import PathAlias
import sys, os, shutil
import numpy as np
from IPython import get_ipython


path_aliases = PathAlias()
sys.path.append("/sf/bernina/config/src/python/bernina_analysis")

namespace = Namespace(
    name="bernina", root_module=__name__, alias_namespace=NamespaceCollection().bernina
)
namespace.alias_namespace.data = []

# Adding stuff that might be relevant for stuff configured below (e.g. config)
_config_bernina_dict = AdjustableFS(
    "/sf/bernina/config/eco/configuration/bernina_config.json",
    name="_config_bernina_dict",
)
from eco.elements.adj_obj import AdjustableObject

namespace.append_obj(AdjustableObject, _config_bernina_dict, name="config_bernina")

namespace.append_obj(
    "RunData",
    config_bernina.pgroup,
    name="runs",
    load_kwargs={
        # "checknstore_parsing_result": "/sf/bernina/data/{pgroup}/res",
        "checknstore_parsing_result": "/sf/bernina/data/{pgroup}/scratch",
        "load_dap_data": True,
        "lazyEscArrays": True,
        "exclude_from_files": ["PVDATA"],
    },
    module_name="eco.acquisition.scan_data",
)

namespace.append_obj(
    "StatusData",
    config_bernina.pgroup,
    name="run_status",
    load_kwargs={},
    module_name="eco.acquisition.scan_data",
    lazy=False,
)

namespace.append_obj(
    "Elog",
    "https://elog-gfa.psi.ch/Bernina",
    screenshot_directory="/tmp",
    name="elog_gfa",
    module_name="eco.utilities.elog",
    lazy=True,
)

namespace.append_obj(
    "Elog",
    pgroup_adj=config_bernina.pgroup,
    name="scilog",
    module_name="eco.utilities.elog_scilog",
    lazy=True,
)

namespace.append_obj(
    "ElogsMultiplexer",
    scilog,
    elog_gfa,
    name="elog",
    module_name="eco.utilities.elog",
    lazy=True,
)
# namespace.append_obj(
#     "Elog",
#     "https://elog-gfa.psi.ch/Bernina",
#     screenshot_directory="/tmp",
#     name="elog",
#     module_name="eco.utilities.elog",
# )

eco.defaults.ELOG = elog
namespace.append_obj(
    "DummyAdjustable",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="dummy_adjustable",
)
namespace.append_obj(
    "set_global_memory_dir",
    "/sf/bernina/config/eco/memory",
    module_name="eco.elements.memory",
    name="path_memory",
    lazy=False,
)

namespace.append_obj(
    "DataApi",
    name="archiver",
    module_name="eco.dbase.archiver",
    pv_pulse_id="SARES20-CVME-01-EVR0:RX-PULSEID",
    add_to_cnf=True,
    lazy=True,
)
eco.defaults.ARCHIVER = archiver

namespace.append_obj(
    "get_strip_chart_function",
    name="strip_chart",
    module_name="eco.dbase.strip_chart",
    lazy=True,
)

namespace.append_obj(
    "EventWorker",
    name="bs_worker",
    module_name="escape.stream",
    lazy=True,
)

namespace.append_obj(
    "BerninaEnv",
    name="env_log",
    module_name="eco.fel.atmosphere",
    lazy=True,
)
namespace.append_obj(
    "BerninaEnvironment",
    name="env",
    module_name="eco.devices_general.env_sensors",
    lazy=True,
)

namespace.append_obj(
    "AdjustableFS",
    "/photonics/home/gac-bernina/eco/configuration/run_table_channels_CA",
    name="_env_channels_ca",
    module_name="eco.elements.adjustable",
    lazy=True,
)

# adding all stuff from the config components the "old" way of configuring.
# whatever is added, it is available by the configured name in this module
# afterwards, and can be used immediately, e.g. as input argument for the next thing.

for tk in components:
    namespace.append_obj_from_config(tk, lazy=True)


# Adding stuff the "new" way

# namespace.append_obj(
#     "EventReceiver",
#     "",
#     lazy=True,
#     name="cam_north",
#     module_name="eco.devices_general.cameras_ptz",
# )

namespace.append_obj(
    "BerninaVacuum",
    name="vacuum",
    module_name="eco.endstations.bernina_vacuum",
    lazy=True,
)


## Old stuff that was still in config and might be needed
namespace.append_obj(
    "Pulsepick",
    Id="SAROP21-OPPI113",
    evronoff="SGE-CPCW-72-EVR0:FrontUnivOut15-Ena-SP",
    evrsrc="SGE-CPCW-72-EVR0:FrontUnivOut15-Src-SP",
    name="xp",
    module_name="eco.xoptics.pp",
    lazy=True,
)
namespace.append_obj(
    "laser_shutter",
    "SLAAR21-LTIM01-EVR0",
    name="laser_shutter",
    module_name="eco.loptics.laser_shutter",
    lazy=True,
)
namespace.append_obj(
    "PhotonShutter",
    "SARFE10-OPSH044:REQUEST",
    name="pshut_und",
    module_name="eco.xoptics.shutters",
    lazy=True,
)
namespace.append_obj(
    "PhotonShutter",
    "SARFE10-OPSH059:REQUEST",
    name="pshut_fe",
    module_name="eco.xoptics.shutters",
    lazy=True,
)
namespace.append_obj(
    "SafetyShutter",
    "SGE01-EPKT822:BST1_oeffnen",
    name="sshut_opt",
    module_name="eco.xoptics.shutters",
    lazy=True,
)
namespace.append_obj(
    "SafetyShutter",
    "SGE01-EPKT820:BST1_oeffnen",
    name="sshut_fe",
    module_name="eco.xoptics.shutters",
    lazy=True,
)
namespace.append_obj(
    "AttenuatorAramis",
    "SARFE10-OATT053",
    shutter=pshut_und,
    set_limits=[],
    module_name="eco.xoptics.attenuator_aramis",
    name="att_fe",
    lazy=True,
)


namespace.append_obj(
    "Bernina_XEYE",
    zoomstage_pv=config_bernina.xeye.zoomstage_pv._value,
    camera_pv=config_bernina.xeye.camera_pv._value,
    bshost=config_bernina.xeye.bshost._value,
    bsport=config_bernina.xeye.bsport._value,
    name="xeye",
    lazy=True,
    module_name="eco.xdiagnostics.profile_monitors",
)


## beamline components ##

namespace.append_obj(
    "JJSlitUnd",
    name="slit_und",
    module_name="eco.xoptics.slits",
    lazy=True,
)
namespace.append_obj(
    "SlitBlades",
    "SAROP21-OAPU092",
    name="slit_switch",
    module_name="eco.xoptics.slits",
    lazy=True,
)

# namespace.append_obj(
#     "Pprm",
#     "SARFE10-PPRM064",
#     "SARFE10-PPRM064",
#     name= "prof_fe",
#     # "z_und": 64,
#     # "desc": "Profile monitor after Front End",
#     module_name="eco.xdiagnostics.profile_monitors",
#     )
# namespace.append_obj(
#     "Pprm",
#     "SAROP11-PPRM066",
#     "SAROP11-PPRM066",
#     name= "prof_mirr_alv1",
#     # "z_und": 66,
#     # "desc": "Profile monitor after Alvra Mirror 1",
#     module_name="eco.xdiagnostics.profile_monitors",
#     )
# namespace.append_obj(
#     "Pprm",
#     "SAROP21-PPRM094",
#     "SAROP21-PPRM094",
#     name= "prof_mirr1",
#     # "z_und": 94,
#     # "desc": "Profile monitor after Mirror 1",
#     module_name="eco.xdiagnostics.profile_monitors",
#     )


namespace.append_obj(
    "OffsetMirrorsBernina",
    name="offset",
    lazy=True,
    module_name="eco.xoptics.offsetMirrors_new",
)

namespace.append_obj(
    "SlitBlades",
    "SAROP21-OAPU102",
    name="slit_mono",
    module_name="eco.xoptics.slits",
    lazy=True,
)
# namespace.append_obj(
#     "SolidTargetDetectorPBPS",
#     "SAROP21-PBPS103",
#     diode_channels_raw={
#         "up": "SAROP21-CVME-PBPS1:Lnk9Ch3-DATA-SUM",
#         "down": "SAROP21-CVME-PBPS1:Lnk9Ch4-DATA-SUM",
#         "left": "SAROP21-CVME-PBPS1:Lnk9Ch2-DATA-SUM",
#         "right": "SAROP21-CVME-PBPS1:Lnk9Ch1-DATA-SUM",
#     },
#     fe_digi_channels={
#         "left": "SAROP21-CVME-PBPS1:Lnk9Ch2",
#         "right": "SAROP21-CVME-PBPS1:Lnk9Ch1",
#         "up": "SAROP21-CVME-PBPS1:Lnk9Ch3",
#         "down": "SAROP21-CVME-PBPS1:Lnk9Ch4",
#     },
#     name="mon_mono_old",
#     module_name="eco.xdiagnostics.intensity_monitors",
#     lazy=True,
# )

namespace.append_obj(
    "SolidTargetDetectorPBPS",
    "SAROP21-PBPS103",
    use_calibration=False,
    # channel_xpos="SLAAR21-LTIM01-EVR0:CALCX",
    # channel_ypos="SLAAR21-LTIM01-EVR0:CALCY",
    # channel_intensity="SLAAR21-LTIM01-EVR0:CALCI",
    # diode_channels_raw={
    #     "up": "SLAAR21-LSCP1-FNS:CH6:VAL_GET",
    #     "down": "SLAAR21-LSCP1-FNS:CH7:VAL_GET",
    #     "left": "SLAAR21-LSCP1-FNS:CH4:VAL_GET",
    #     "right": "SLAAR21-LSCP1-FNS:CH5:VAL_GET",
    # },
    # calibration_records={
    #     "intensity": "SLAAR21-LTIM01-EVR0:CALCI",
    #     "xpos": "SLAAR21-LTIM01-EVR0:CALCX",
    #     "ypos": "SLAAR21-LTIM01-EVR0:CALCY",
    # },
    name="mon_mono",
    module_name="eco.xdiagnostics.intensity_monitors",
    pipeline_computation="SAROP21-PBPS103_proc",
    lazy=True,
)

from eco.devices_general.motors import SmaractStreamdevice, SmaractRecord

namespace.append_obj(
    "SlitBladesGeneral",
    name="slit_kb",
    def_blade_up={
        "args": [SmaractRecord, "SARES23-LIC:MOT_2"],
        "kwargs": {},
    },
    def_blade_down={
        "args": [SmaractRecord, "SARES23-LIC:MOT_1"],
        "kwargs": {},
    },
    def_blade_left={
        "args": [SmaractRecord, "SARES23-LIC:MOT_9"],
        "kwargs": {},
    },
    def_blade_right={
        "args": [SmaractRecord, "SARES23-LIC:MOT_4"],
        "kwargs": {},
    },
    module_name="eco.xoptics.slits",
    lazy=True,
)
# namespace.append_obj(
#     "SlitBladesGeneral",
#     name="slit_kb",
#     def_blade_up={
#         "args": [SmaractStreamdevice, "SARES23-LIC2"],
#         "kwargs": {
#             "offset_file": "/sf/bernina/config/eco/reference_values/slit_kb_up.json",
#         },
#     },
#     def_blade_down={
#         "args": [SmaractStreamdevice, "SARES23-LIC1"],
#         "kwargs": {
#             "offset_file": "/sf/bernina/config/eco/reference_values/slit_kb_down.json",
#         },
#     },
#     def_blade_left={
#         "args": [SmaractStreamdevice, "SARES23-LIC3"],
#         "kwargs": {
#             "offset_file": "/sf/bernina/config/eco/reference_values/slit_kb_left.json",
#         },
#     },
#     def_blade_right={
#         "args": [SmaractStreamdevice, "SARES23-LIC4"],
#         "kwargs": {
#             "offset_file": "/sf/bernina/config/eco/reference_values/slit_kb_right.json",
#         },
#     },
#     module_name="eco.xoptics.slits",
#     lazy=True,
# )


namespace.append_obj(
    "SlitBladesGeneral",
    name="slit_cleanup",
    def_blade_up={
        "args": [SmaractRecord, "SARES23-LIC:MOT_6"],
        "kwargs": {},
    },
    def_blade_down={
        "args": [SmaractRecord, "SARES23-LIC:MOT_5"],
        "kwargs": {},
    },
    def_blade_left={
        "args": [SmaractRecord, "SARES23-LIC:MOT_8"],
        "kwargs": {},
    },
    def_blade_right={
        "args": [SmaractRecord, "SARES23-LIC:MOT_7"],
        "kwargs": {},
    },
    module_name="eco.xoptics.slits",
    lazy=True,
)
# namespace.append_obj(
#     "SlitBladesGeneral",
#     name="slit_cleanup",
#     def_blade_up={
#         "args": [SmaractStreamdevice, "SARES23-LIC6"],
#         "kwargs": {
#             "offset_file": "/sf/bernina/config/eco/reference_values/slit_cleanup_up.json",
#         },
#     },
#     def_blade_down={
#         "args": [SmaractStreamdevice, "SARES23-LIC5"],
#         "kwargs": {
#             "offset_file": "/sf/bernina/config/eco/reference_values/slit_cleanup_down.json",
#         },
#     },
#     def_blade_left={
#         "args": [SmaractStreamdevice, "SARES23-LIC8"],
#         "kwargs": {
#             "offset_file": "/sf/bernina/config/eco/reference_values/slit_cleanup_left.json",
#         },
#     },
#     def_blade_right={
#         "args": [SmaractStreamdevice, "SARES23-LIC7"],
#         "kwargs": {
#             "offset_file": "/sf/bernina/config/eco/reference_values/slit_cleanup_right.json",
#         },
#     },
#     module_name="eco.xoptics.slits",
#     lazy=True,
# )

namespace.append_obj(
    "GasDetector",
    name="mon_und_gas",
    module_name="eco.xdiagnostics.intensity_monitors",
    lazy=True,
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
    pipeline_computation="SAROP21-PBPS103_proc",
    lazy=True,
)


namespace.append_obj(
    "RefLaser_Aramis",
    "SAROP21-OLAS134",
    module_name="eco.xoptics.reflaser",
    name="reflaser_beamline",
    lazy=True,
)

namespace.append_obj(
    "RefLaser_BerninaUSD",
    module_name="eco.xoptics.reflaser",
    name="reflaser",
    outpos_adjfs_path="/sf/bernina/config/eco/configuration/reflaser_usd_lastposition.json",
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


# namespace.append_obj(
#     "SolidTargetDetectorPBPS",
#     "SAROP21-PBPS133",
#     channel_xpos="SLAAR21-LTIM01-EVR0:CALCX",
#     channel_ypos="SLAAR21-LTIM01-EVR0:CALCY",
#     channel_intensity="SLAAR21-LTIM01-EVR0:CALCI",
#     diode_channels_raw={
#         "up": "SLAAR21-LSCP1-FNS:CH6:VAL_GET",
#         "down": "SLAAR21-LSCP1-FNS:CH7:VAL_GET",
#         "left": "SLAAR21-LSCP1-FNS:CH4:VAL_GET",
#         "right": "SLAAR21-LSCP1-FNS:CH5:VAL_GET",
#     },
#     calibration_records={
#         "intensity": "SLAAR21-LTIM01-EVR0:CALCI",
#         "xpos": "SLAAR21-LTIM01-EVR0:CALCX",
#         "ypos": "SLAAR21-LTIM01-EVR0:CALCY",
#     },
#     name="mon_opt_old",
#     module_name="eco.xdiagnostics.intensity_monitors",
#     lazy=True,
# )

namespace.append_obj(
    "SolidTargetDetectorPBPS",
    "SAROP21-PBPS133",
    use_calibration=False,
    # channel_xpos="SLAAR21-LTIM01-EVR0:CALCX",
    # channel_ypos="SLAAR21-LTIM01-EVR0:CALCY",
    # channel_intensity="SLAAR21-LTIM01-EVR0:CALCI",
    # diode_channels_raw={
    #     "up": "SLAAR21-LSCP1-FNS:CH6:VAL_GET",
    #     "down": "SLAAR21-LSCP1-FNS:CH7:VAL_GET",
    #     "left": "SLAAR21-LSCP1-FNS:CH4:VAL_GET",
    #     "right": "SLAAR21-LSCP1-FNS:CH5:VAL_GET",
    # },
    # calibration_records={
    #     "intensity": "SLAAR21-LTIM01-EVR0:CALCI",
    #     "xpos": "SLAAR21-LTIM01-EVR0:CALCX",
    #     "ypos": "SLAAR21-LTIM01-EVR0:CALCY",
    # },
    name="mon_opt",
    module_name="eco.xdiagnostics.intensity_monitors",
    pipeline_computation="SAROP21-PBPS133_proc",
    lazy=True,
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

namespace.append_obj(
    "Pprm",
    "SAROP11-PPRM066",
    "SAROP11-PPRM066",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_mirr_alv1",
    in_target=3,
    lazy=True,
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
    "AttenuatorAramis",
    "SAROP21-OATT135",
    shutter=xp,
    set_limits=[],
    module_name="eco.xoptics.attenuator_aramis",
    name="att",
    lazy=True,
)


namespace.append_obj(
    "SolidTargetDetectorBerninaUSD",
    "SARES23-LIC:MOT_12",
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

namespace.append_obj(
    "DownstreamDiagnostic",
    name="dsd_table",
    module_name="eco.xdiagnostics.dsd",
    lazy=True,
)

namespace.append_obj(
    "Pprm_dsd",
    pvname="SARES20-DSDPPRM",
    pvname_camera="SARES20-DSDPPRM",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_dsd",
    lazy=True,
)
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

## general components ##
namespace.append_obj(
    "CtaSequencer",
    "SAR-CCTA-ESB",
    0,
    name="seq",
    module_name="eco.timing.sequencer",
    lazy=True,
)
namespace.append_obj(
    "MasterEventSystem",
    "SIN-TIMAST-TMA",
    name="event_master",
    module_name="eco.timing.event_timing_new_new",
    # pv_eventset="SAR-CVME-TIFALL5:EvtSet",
    # lazy=False,
    lazy=True,
)
namespace.append_obj(
    "TimingSystem",
    pv_master="SIN-TIMAST-TMA",
    pv_pulse_id="SARES20-CVME-01-EVR0:RX-PULSEID",
    pv_eventset="SAR-CVME-TIFALL5:EvtSet",
    name="event_system",
    module_name="eco.timing.event_timing_new_new",
    lazy=True,
)

# namespace.append_obj('Daq', instrument= "bernina",pgroup= config_berninamesp["pgroup"], channels_JF=channels_JF, channels_BS=channels_BS,channels_BSCAM=channels_BSCAM,channels_CA=channels_CA,pulse_id_adj="SLAAR21-LTIM01-EVR0:RX-PULSEID",event_master=event_system.event_master,detectors_event_code=50,name='daq',module_name='eco.acquisition.daq_client')

# namespace.append_obj('Scans',data_base_dir="scan_data",scan_info_dir=f"/sf/bernina/data/{config_berninamesp['pgroup']}/res/scan_info",
# default_counters=[daq],checker=checker,scan_directories=True,run_table=run_table,elog=elog,
# module_name = "eco.acquisition.scan",name="scans")
namespace.append_obj(
    "ProfKbBernina",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_kb",
    pvname_mirror="SARES23-LIC:MOT_11",
    lazy=True,
)
namespace.append_obj(
    "TimetoolBerninaUSD",
    module_name="eco.timing.timing_diag",
    pvname_mirror="SARES23-LIC:MOT_11",
    andor_spectrometer="SLAAR11-LSPC-ALCOR1",
    name="tt_kb",
    lazy=True,
)
# namespace.append_obj(
#     "TimetoolSpatial",
#     module_name="eco.timing.timing_diag",
#     name="tt_spatial_dev",
#     lazy=True,
# )

namespace.append_obj(
    "HexapodSymmetrie",
    name="usd_table",
    module_name="eco.endstations.hexapod",
    offset=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    lazy=True,
)

namespace.append_obj(
    "EventReceiver",
    "SARES20-CVME-01-EVR0",
    event_master,
    n_pulsers=24,
    n_output_front=7,
    n_output_rear=16,
    name="evr",
    module_name="eco.timing.event_timing_new_new",
    lazy=True,
)
namespace.append_obj(
    "EventReceiver",
    "SLAAR-LTIM02-EVR0",
    event_master,
    n_pulsers=24,
    n_output_front=7,
    n_output_rear=16,
    name="evr_laser",
    module_name="eco.timing.event_timing_new_new",
    lazy=True,
)
namespace.append_obj(
    "EventReceiver",
    "SLAAR21-LTIM01-EVR0",
    event_master,
    n_pulsers=24,
    n_output_front=7,
    n_output_rear=16,
    name="evr_hutch_laser",
    module_name="eco.timing.event_timing_new_new",
    lazy=True,
    # lazy=False,
)
namespace.append_obj(
    "EventReceiver",
    "SGE-CPCW-72-EVR0",
    event_master,
    n_pulsers=16,
    n_output_front=16,
    n_output_rear=0,
    name="evr_camserver72",
    module_name="eco.timing.event_timing_new_new",
    lazy=True,
    # lazy=False,
)
namespace.append_obj(
    "EventReceiver",
    "SGE-CPCW-73-EVR0",
    event_master,
    n_pulsers=16,
    n_output_front=16,
    n_output_rear=0,
    name="evr_camserver73",
    module_name="eco.timing.event_timing_new_new",
    lazy=True,
    # lazy=False,
)
namespace.append_obj(
    "EventReceiver",
    "SGE-CPCW-74-EVR0",
    event_master,
    n_pulsers=16,
    n_output_front=16,
    n_output_rear=0,
    name="evr_camserver74",
    module_name="eco.timing.event_timing_new_new",
    lazy=True,
    # lazy=False,
)
namespace.append_obj(
    "EventReceiver",
    "SGE-CPCW-83-EVR0",
    event_master,
    n_pulsers=16,
    n_output_front=16,
    n_output_rear=0,
    name="evr_camserver83",
    module_name="eco.timing.event_timing_new_new",
    lazy=True,
    # lazy=False,
)
namespace.append_obj(
    "EventReceiver",
    "SGE-CPCW-84-EVR0",
    event_master,
    n_pulsers=16,
    n_output_front=16,
    n_output_rear=0,
    name="evr_camserver84",
    module_name="eco.timing.event_timing_new_new",
    lazy=True,
    # lazy=False,
)
namespace.append_obj(
    "EventReceiver",
    "SGE-CPCW-85-EVR0",
    event_master,
    n_pulsers=16,
    n_output_front=16,
    n_output_rear=0,
    name="evr_camserver85",
    module_name="eco.timing.event_timing_new_new",
    lazy=True,
    # lazy=False,
)
namespace.append_obj(
    "DigitizerKeysight",
    "SARES21-GES1",
    name="digitizer_keysight_user",
    module_name="eco.devices_general.digitizers",
    lazy=True,
)
namespace.append_obj(
    "DigitizerIoxos",
    "SARES20-LSCP9-FNS",
    name="digitizer_ioxos_user",
    module_name="eco.devices_general.digitizers",
    lazy=True,
)
namespace.append_obj(
    "DigitizerIoxos",
    "SLAAR21-LSCP1-FNS",
    name="digitizer_ioxos_laser",
    module_name="eco.devices_general.digitizers",
    lazy=True,
)

namespace.append_obj(
    "AxisPTZ",
    "bernina-cam-n",
    lazy=True,
    name="cam_north",
    module_name="eco.devices_general.cameras_ptz",
)
namespace.append_obj(
    "AxisPTZ",
    "bernina-cam-w",
    lazy=True,
    name="cam_west",
    module_name="eco.devices_general.cameras_ptz",
)
namespace.append_obj(
    "AxisPTZ",
    "bernina-cam-s",
    lazy=True,
    name="cam_south",
    module_name="eco.devices_general.cameras_ptz",
)
namespace.append_obj(
    "Xspect",
    name="xspect",
    lazy=True,
    module_name="eco.xdiagnostics.xspect",
)
namespace.append_obj(
    "SlitPosWidth",
    "SAROP21-OAPU138",
    name="slit_att",
    lazy=True,
    module_name="eco.xoptics.slits",
)
namespace.append_obj(
    "WagoAnalogInputs",
    "SARES20-CWAG-GPS01",
    lazy=True,
    name="analog_inputs",
    module_name="eco.devices_general.wago",
)
namespace.append_obj(
    "WagoAnalogOutputs",
    "SARES20-CWAG-GPS01",
    lazy=True,
    name="analog_outputs",
    module_name="eco.devices_general.wago",
)

# namespace.append_obj(
#     "AnalogInput",
#     "SARES20-CWAG-GPS01:ADC08",
#     lazy=True,
#     name="oxygen_sensor",
#     module_name="eco.devices_general.wago",
# )

namespace.append_obj(
    "GudeStrip",
    "SARES20-CPPS-01",
    lazy=True,
    name="powerstrip_gps",
    module_name="eco.devices_general.powersockets",
)
namespace.append_obj(
    "GudeStrip",
    "SARES20-CPPS-04",
    lazy=True,
    name="powerstrip_xrd",
    module_name="eco.devices_general.powersockets",
)
namespace.append_obj(
    "GudeStrip",
    "SARES20-CPPS-02",
    lazy=True,
    name="powerstrip_patch2",
    module_name="eco.devices_general.powersockets",
)

## diffractometers
namespace.append_obj(
    "AdjustableFS",
    "/photonics/home/gac-bernina/eco/configuration/config_JFs",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="config_JFs",
)

namespace.append_obj(
    "GPS",
    module_name="eco.endstations.bernina_diffractometers",
    name="gps",
    pvname="SARES22-GPS",
    configuration=config_bernina.gps_config(),
    pgroup_adj=config_bernina.pgroup,
    configsjf_adj=config_JFs,
    detectors=[
        # {"name": "det_fluo", "jf_id": "JF04T01V01"},
        # {"name": "det_vHamos", "jf_id": "JF05T01V01"},
    ],
    fina_hex_angle_offset="/sf/bernina/config/eco/reference_values/hex_pi_angle_offset.json",
    lazy=True,
)

namespace.append_obj(
    "StaeubliTx200",
    module_name="eco.endstations.bernina_robots",
    name="rob",
#    pshell_url="http://PC14742:8080/",
    pshell_url="http://saresb-robot:8080/",
    robot_config=config_bernina.robot_config,
    pgroup_adj=config_bernina.pgroup,
    jf_config=config_JFs,
    lazy=True,
)

namespace.append_obj(
    "XRDYou",
    module_name="eco.endstations.bernina_diffractometers",
    Id="SARES21-XRD",
    configuration=config_bernina.xrd_config(),
    detectors=[
        # {"name": "det_diff", "jf_id": "JF01T03V01"},
    ],
    pgroup_adj=config_bernina.pgroup,
    configsjf_adj=config_JFs,
    invert_kappa_ellbow=config_bernina.invert_kappa_ellbow._value,
    fina_hex_angle_offset="/sf/bernina/config/eco/reference_values/hex_pi_angle_offset.json",
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
    "KBMirrorBernina",
    "SAROP21-OKBV139",
    "SAROP21-OKBH140",
    module_name="eco.xoptics.kb_bernina",
    usd_table=usd_table,
    name="kb",
    diffractometer=gps,
    lazy=True,
)

### channelsfor daq ###
namespace.append_obj(
    "AdjustableFS",
    "/photonics/home/gac-bernina/eco/configuration/channels_JF",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_JF",
)
namespace.append_obj(
    "AdjustableFS",
    "/photonics/home/gac-bernina/eco/configuration/channTest of new scilog for Ovuka experimentels_BS",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_BS",
)
namespace.append_obj(
    "AdjustableFS",
    "/photonics/home/gac-bernina/eco/configuration/channels_BSCAM",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_BSCAM",
)
namespace.append_obj(
    "AdjustableFS",
    "/photonics/home/gac-bernina/eco/configuration/channels_CA",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_CA",
)
namespace.append_obj(
    "AdjustableFS",
    "/photonics/home/gac-bernina/eco/configuration/channels_CA_epicsdaq",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_CA_epicsdaq",
)

namespace.append_obj(
    "Att_usd",
    name="att_usd",
    module_name="eco.xoptics.att_usd",
    xp=xp,
    lazy=True,
)

namespace.append_obj(
    "Jungfrau",
    "JF13T01V01",
    name="det_invac",
    pgroup_adj=config_bernina.pgroup,
    module_name="eco.detector.jungfrau",
    config_adj=config_JFs,
    lazy=True,)

namespace.append_obj(
    "Jungfrau",
    "JF03T01V02",
    name="det_i0",
    pgroup_adj=config_bernina.pgroup,
    module_name="eco.detector.jungfrau",
    config_adj=config_JFs,
    lazy=True,
)

# namespace.append_obj(
#     "Jungfrau",
#     "JF04T01V01",
#     name="det_rowland",
#     pgroup_adj=config_bernina.pgroup,
#     module_name="eco.detector.jungfrau",
#     config_adj=config_JFs,
#     lazy=True,
# )

# namespace.append_obj(
#     "Jungfrau",
#     "JF14T01V01",
#     name="det_diff",
#     pgroup_adj=config_bernina.pgroup,
#     module_name="eco.detector.jungfrau",
#     config_adj=config_JFs,
#     lazy=True,
# )

# namespace.append_obj(
#     "DetectorRobot",
#     JF_detector_id="JF07T32V02",
#     JF_detector_name="det_diff",
#     pgroup_adj=config_bernina.pgroup,
#     config_adj=config_JFs,
#     module_name="eco.endstations.bernina_robot",
#     lazy=True,
#     name="robot",
# )

namespace.append_obj(
    "MpodModule",
    "SARES21-PS7071",
    [1, 2, 3, 4],
    ["ch1", "ch2", "ch3", "ch4"],
    module_string="LV_OMPV_1",
    name="power_LV_patch1",
    lazy=True,
    module_name="eco.devices_general.powersockets",
)

namespace.append_obj(
    "MpodModule",
    "SARES21-PS7071",
    [5, 6, 7, 8],
    ["ch1", "ch2", "ch3", "ch4"],
    module_string="LV_OMPV_1",
    name="power_LV_patch2",
    lazy=True,
    module_name="eco.devices_general.powersockets",
)

#
# namespace.append_obj(
#    "MpodModule",
#    "SARES21-CPCL-PS7071",
#    [1,2,3,4],
#    ['ch1','ch2','ch3','ch4'],
#    module_string='HV_EHS_3',
#    name="power_HV_patch1",
#    module_name="eco.devices_general.powersockets",
# )
#
# namespace.append_obj(
#    "MpodModule",
#    "SARES21-CPCL-PS7071",
#    [5,6,7,8],
#    ['ch1','ch2','ch3','ch4'],
#    module_string='HV_EHS_3',
#    name="power_HV_patch2",
#    module_name="eco.devices_general.powersockets",
# )

### draft new epics daq ###
namespace.append_obj(
    "EpicsDaq",
    channel_list=channels_CA_epicsdaq,
    name="daq_epics_local",
    module_name="eco.acquisition.epics_data",
    lazy=True,
)
### old epics daq ###
# namespace.append_obj(
#    "ChannelList",
#    name="epics_channel_list",
#    file_name="/sf/bernina/config/channel_lists/default_channel_list_epics",
#    module_name="eco.utilities.config",
# )

# namespace.append_obj(
#    "Epicstools",
#    name="epics_daq",
#    channel_list=epics_channel_list,
#    default_file_path=f"/sf/bernina/data/{config_berninamesp['pgroup']}/res/epics_daq/",
#    module_name="eco.acquisition.epics_data",
# )

namespace.append_obj(
    "Scans",
    name="scans_epics",
    module_name="eco.acquisition.scan",
    data_base_dir=f"{config_bernina.pgroup()}/scan_data",
    scan_info_dir=f"{daq_epics_local.default_file_path()}/{config_bernina.pgroup()}/scan_info",
    default_counters=[daq_epics_local],
    checker=None,
    scan_directories=True,
    run_table=None,
    lazy=True,
)
#
#
##### standard DAQ #######
namespace.append_obj(
    "Daq",
    instrument="bernina",
    pgroup=config_bernina.pgroup,
    channels_JF=channels_JF,
    channels_BS=channels_BS,
    channels_BSCAM=channels_BSCAM,
    channels_CA=channels_CA,
    config_JFs=config_JFs,
    pulse_id_adj="SLAAR21-LTIM01-EVR0:RX-PULSEID",
    event_master=event_master,
    detectors_event_code=50,
    rate_multiplicator="auto",
    name="daq",
    module_name="eco.acquisition.daq_client",
    lazy=True,
)
namespace.append_obj(
    "Daq",
    instrument="bernina",
    broker_address="http://sf-daq-1:10002",
    pgroup=config_bernina.pgroup,
    channels_JF=channels_JF,
    channels_BS=channels_BS,
    channels_BSCAM=channels_BSCAM,
    channels_CA=channels_CA,
    config_JFs=config_JFs,
    pulse_id_adj="SLAAR21-LTIM01-EVR0:RX-PULSEID",
    event_master=event_master,
    detectors_event_code=50,
    name="daq_dev",
    module_name="eco.acquisition.daq_client",
    lazy=True,
)

namespace.append_obj(
    "Run_Table2",
    name="run_table",
    module_name="eco.utilities.runtable",
    exp_id=config_bernina.pgroup._value,
    exp_path=f"/sf/bernina/data/{config_bernina.pgroup._value}/res/run_table/",
    devices="bernina",
    keydf_fname="/sf/bernina/config/src/python/gspread/gspread_keys.pkl",
    cred_fname="/sf/bernina/config/src/python/gspread/pandas_push",
    gsheet_key_path="/sf/bernina/config/eco/reference_values/run_table_gsheet_keys",
    lazy=True,
)


def _wait_for_tasks(scan, **kwargs):
    print("checking remaining tasks from previous scan ...")
    for task in scan.remaining_tasks:
        task.join()
    print("... done.")


def _append_namesace_status_to_scan(
    scan, daq=daq, namespace=namespace, append_status_info=True, **kwargs
):
    if not append_status_info:
        return
    namespace_status = namespace.get_status(base=None)
    stat = {"status_run_start": namespace_status}
    scan.status = stat


def _write_namespace_status_to_scan(
    scan, daq=daq, namespace=namespace, append_status_info=True, end_scan=True, **kwargs
):
    if not append_status_info:
        return
    if end_scan:
        namespace_status = namespace.get_status(base=None)
        scan.status["status_run_end"] = namespace_status
    if (not end_scan) and not (len(scan.values_done) == 1):
        return
    if hasattr(scan, "daq_run_number"):
        runno = scan.daq_run_number
    else:
        runno = daq.get_last_run_number()
    pgroup = daq.pgroup
    tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/tmp/stat_run{runno:04d}")
    tmpdir.mkdir(exist_ok=True, parents=True)
    try:
        tmpdir.chmod(0o775)
    except:
        pass

    statusfile = tmpdir / Path("status.json")
    if not statusfile.exists():
        with open(statusfile, "w") as f:
            json.dump(scan.status, f, sort_keys=True, cls=NumpyEncoder, indent=4)
    else:
        with open(statusfile, "r+") as f:
            f.seek(0)
            json.dump(scan.status, f, sort_keys=True, cls=NumpyEncoder, indent=4)
            f.truncate()
            print("Wrote status with seek truncate!")
    if not statusfile.group() == statusfile.parent.group():
        shutil.chown(statusfile, group=statusfile.parent.group())

    response = daq.append_aux(
        statusfile.resolve().as_posix(),
        pgroup=pgroup,
        run_number=runno,
    )
    print("####### transfer status #######")
    print(response.json())
    print("###############################")
    scan.scan_info["scan_parameters"]["status"] = "aux/status.json"


def _write_namespace_aliases_to_scan(scan, daq=daq, force=False, **kwargs):
    if force or (len(scan.values_done) == 1):
        namespace_aliases = namespace.alias.get_all()
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number
        else:
            runno = daq.get_last_run_number()
        pgroup = daq.pgroup
        tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/tmp/aliases_run{runno:04d}")
        tmpdir.mkdir(exist_ok=True, parents=True)
        try:
            tmpdir.chmod(0o775)
        except:
            pass
        aliasfile = tmpdir / Path("aliases.json")
        if not Path(aliasfile).exists():
            with open(aliasfile, "w") as f:
                json.dump(
                    namespace_aliases, f, sort_keys=True, cls=NumpyEncoder, indent=4
                )
        else:
            with open(aliasfile, "r+") as f:
                f.seek(0)
                json.dump(
                    namespace_aliases, f, sort_keys=True, cls=NumpyEncoder, indent=4
                )
                f.truncate()
        if not aliasfile.group() == aliasfile.parent.group():
            shutil.chown(aliasfile, group=aliasfile.parent.group())

        scan.remaining_tasks.append(
            Thread(
                target=daq.append_aux,
                args=[aliasfile.resolve().as_posix()],
                kwargs=dict(pgroup=pgroup, run_number=runno),
            )
        )
        # DEBUG
        print(
            f"Sending scan_info_rel.json in {Path(aliasfile).parent.stem} to run number {runno}."
        )
        scan.remaining_tasks[-1].start()
        # response = daq.append_aux(
        #     aliasfile.resolve().as_posix(),
        #     pgroup=pgroup,
        #     run_number=runno,
        # )
        print("####### transfer aliases started #######")
        # print(response.json())
        # print("################################")
        scan.scan_info["scan_parameters"]["aliases"] = "aux/aliases.json"


def _message_end_scan(scan, **kwargs):
    print(f"Finished run {scan.run_number}.")
    if hasattr(scan, "daq_run_number"):
        runno_daq_saved = scan.daq_run_number
        print(f"daq_run_number is run {runno_daq_saved}.")

    try:
        runno = daq.get_last_run_number()
        print(f"daq last run number is run {runno}.")
    except:
        pass

    try:
        e = pyttsx3.init()
        e.say(f"Finished run {scan.run_number}.")
        e.runAndWait()
        e.stop()
    except:
        print("Audio output failed.")


# def _copy_scan_info_to_raw(scan, daq=daq):
#     run_number = daq.get_last_run_number()
#     pgroup = daq.pgroup
#     print(f"Copying info file to run {run_number} to the raw directory of {pgroup}.")
#     response = daq.append_aux(
#         scan.scan_info_filename, pgroup=pgroup, run_number=run_number
#     )
#     print(f"Status: {response.json()['status']} Message: {response.json()['message']}")


def _create_general_run_info(scan, daq=daq, **kwargs):
    with open(scan.scan_info_filename, "r") as f:
        si = json.load(f)

    info = {}
    # general info, potentially automatically filled
    info["general"] = {}
    # individual data filled by daq/writers/user through api
    info["start"] = {}
    info["end"] = {}
    info["steps"] = []


def _copy_scan_info_to_raw(scan, daq=daq, **kwargs):
    t_start = time.time()

    scan.writeScanInfo()

    # get data that should come later from api or similar.
    run_directory = list(
        Path(f"/sf/bernina/data/{daq.pgroup}/raw").glob(f"run{scan.run_number:04d}*")
    )[0].as_posix()
    with open(scan.scan_info_filename, "r") as f:
        si = json.load(f)

    # correct some data in there (relative paths for now)
    from os.path import relpath

    newfiles = []
    for files in si["scan_files"]:
        newfiles.append([relpath(file, run_directory) for file in files])

    si["scan_files"] = newfiles

    # save temprary file and send then to raw
    if hasattr(scan, "daq_run_number"):
        runno = scan.daq_run_number
    else:
        runno = daq.get_last_run_number()
    pgroup = daq.pgroup
    tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/tmp/info_run{runno:04d}")
    tmpdir.mkdir(exist_ok=True, parents=True)
    try:
        tmpdir.chmod(0o775)
    except:
        pass
    scaninfofile = tmpdir / Path("scan_info_rel.json")
    if not Path(scaninfofile).exists():
        with open(scaninfofile, "w") as f:
            json.dump(si, f, sort_keys=True, cls=NumpyEncoder, indent=4)
    else:
        with open(scaninfofile, "r+") as f:
            f.seek(0)
            json.dump(si, f, sort_keys=True, cls=NumpyEncoder, indent=4)
            f.truncate()
    if not scaninfofile.group() == scaninfofile.parent.group():
        shutil.chown(scaninfofile, group=scaninfofile.parent.group())
    # print(f"Copying info file to run {runno} to the raw directory of {pgroup}.")

    scan.remaining_tasks.append(
        Thread(
            target=daq.append_aux,
            args=[scaninfofile.as_posix()],
            kwargs=dict(pgroup=pgroup, run_number=runno),
        )
    )
    # DEBUG
    print(
        f"Sending scan_info_rel.json in {Path(scaninfofile).parent.stem} to run number {runno}."
    )
    scan.remaining_tasks[-1].start()
    # response = daq.append_aux(scaninfofile.as_posix(), pgroup=pgroup, run_number=runno)
    # print(f"Status: {response.json()['status']} Message: {response.json()['message']}")
    # print(
    #     f"--> creating and copying file took{time.time()-t_start} s, presently adding to deadtime."
    # )


from eco.detector import Jungfrau


def _copy_selected_JF_pedestals_to_raw(
    scan, daq=daq, copy_selected_JF_pedestals_to_raw=True, **kwargs
):
    def copy_to_aux(daq, scan):
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number
        else:
            runno = daq.get_last_run_number()

        pgroup = daq.pgroup

        for jf_id in daq.channels["channels_JF"]():
            jf = Jungfrau(jf_id, name="noname", pgroup_adj=config_bernina.pgroup)
            print(
                f"Copying {jf_id} pedestal to run {runno} in the raw directory of {pgroup}."
            )
            response = daq.append_aux(
                jf.get_present_pedestal_filename_in_run(intempdir=True),
                pgroup=pgroup,
                run_number=runno,
            )
            print(
                f"Status: {response.json()['status']} Message: {response.json()['message']}"
            )
            print(
                f"Copying {jf_id} gainmap to run {runno} in the raw directory of {pgroup}."
            )

            response = daq.append_aux(
                jf.get_present_gain_filename_in_run(intempdir=True),
                pgroup=pgroup,
                run_number=runno,
            )
            print(
                f"Status: {response.json()['status']} Message: {response.json()['message']}"
            )

    if copy_selected_JF_pedestals_to_raw:
        scan.remaining_tasks.append(Thread(target=copy_to_aux, args=[daq, scan]))
        scan.remaining_tasks[-1].start()


def _increment_daq_run_number(scan, daq=daq, **kwargs):
    try:
        daq_last_run_number = daq.get_last_run_number()
        if int(scan.run_number) is int(daq_last_run_number) + 1:
            print("############ incremented ##########")
            daq_run_number = daq.get_next_run_number()
        else:
            daq_run_number = daq_last_run_number
        if int(scan.run_number) is not int(daq_run_number):
            print(
                f"Difference in run number between eco {int(scan.run_number)} and daq {int(daq_run_number)}: using run number {int(scan.run_number)}"
            )
            if int(scan.run_number) > int(daq_run_number):
                n = int(scan.run_number) - int(daq_run_number)
                print("Increasing daq run_number")
                for i in range(n):
                    rn = daq.get_next_run_number()
                    print(rn)
                    scan.daq_run_number = rn
        else:
            scan.daq_run_number = daq_run_number

    except Exception as e:
        print(e)


class Monitor:
    def __init__(self, pvname, start_immediately=True):
        self.data = {}
        self.print = False
        self.pv = PV(pvname)
        self.cb_index = None
        if start_immediately:
            self.start_callback()

    def start_callback(self):
        self.cb_index = self.pv.add_callback(self.append)

    def stop_callback(self):
        self.pv.remove_callback(self.cb_index)

    def append(self, pvname=None, value=None, timestamp=None, **kwargs):
        if not (pvname in self.data):
            self.data[pvname] = []
        ts_local = time.time()
        self.data[pvname].append(
            {"value": value, "timestamp": timestamp, "timestamp_local": ts_local}
        )
        if self.print:
            print(
                f"{pvname}:  {value};  time: {timestamp}; time_local: {ts_local}; diff: {ts_local-timestamp}"
            )


import traceback


def append_scan_monitors(scan, daq=daq, **kwargs):
    scan.monitors = {}
    for adj in scan.adjustables:
        try:
            tname = adj.alias.get_full_name()
        except Exception:
            tname = adj.name
            traceback.print_exc()
        try:
            scan.monitors[tname] = Monitor(adj.pvname)
        except Exception:
            print(f"Could not add CA monitor for {tname}")
            traceback.print_exc()
        try:
            rname = adj.readback.alias.get_full_name()
        except Exception:
            print("no readback configured")
            traceback.print_exc()
        try:
            scan.monitors[rname] = Monitor(adj.readback.pvname)
        except Exception:
            print(f"Could not add CA readback monitor for {tname}")
            traceback.print_exc()

    try:
        tname = daq.pulse_id.alias.get_full_name()
        scan.monitors[tname] = Monitor(daq.pulse_id.pvname)
    except Exception:
        print(f"Could not add daq.pulse_id monitor")
        traceback.print_exc()


def end_scan_monitors(scan, daq=daq, **kwargs):
    for tmon in scan.monitors:
        scan.monitors[tmon].stop_callback()

    monitor_result = {tmon: scan.monitors[tmon].data for tmon in scan.monitors}

    #######
    # get data that should come later from api or similar.
    run_directory = list(
        Path(f"/sf/bernina/data/{daq.pgroup}/raw").glob(f"run{scan.run_number:04d}*")
    )[0].as_posix()

    # correct some data in there (relative paths for now)
    from os.path import relpath

    # save temprary file and send then to raw
    if hasattr(scan, "daq_run_number"):
        runno = scan.daq_run_number
    else:
        runno = daq.get_last_run_number()
    pgroup = daq.pgroup
    tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/tmp/info_run{runno:04d}")
    tmpdir.mkdir(exist_ok=True, parents=True)
    try:
        tmpdir.chmod(0o775)
    except:
        pass
    scanmonitorfile = tmpdir / Path("scan_monitor.pkl")
    if not Path(scanmonitorfile).exists():
        with open(scanmonitorfile, "wb") as f:
            pickle.dump(monitor_result, f)

    print(f"Copying monitor file to run {runno} to the raw directory of {pgroup}.")
    response = daq.append_aux(
        scanmonitorfile.as_posix(), pgroup=pgroup, run_number=runno
    )
    print(f"Status: {response.json()['status']} Message: {response.json()['message']}")

    # scan.monitors = None


def _init_all(scan, append_status_info=True, **kwargs):
    if not append_status_info:
        return
    namespace.init_all(silent=False)


callbacks_start_scan = []
callbacks_start_scan.append(_init_all)
callbacks_start_scan.append(_wait_for_tasks)
callbacks_start_scan.append(_append_namesace_status_to_scan)
callbacks_start_scan.append(_increment_daq_run_number)
callbacks_start_scan.append(append_scan_monitors)
callbacks_end_step = []
callbacks_end_step.append(_copy_scan_info_to_raw)
callbacks_end_step.append(_write_namespace_aliases_to_scan)
callbacks_end_step.append(
    lambda scan, daq=daq, namespace=namespace, append_status_info=True, end_scan=True, **kwargs: _write_namespace_status_to_scan(
        scan,
        daq=daq,
        namespace=namespace,
        append_status_info=append_status_info,
        end_scan=False,
        **kwargs,
    )
)
callbacks_end_scan = []
callbacks_end_scan.append(_write_namespace_status_to_scan)
callbacks_end_scan.append(_copy_scan_info_to_raw)
callbacks_end_scan.append(
    lambda scan, daq=daq, force=True, **kwargs: _write_namespace_aliases_to_scan(
        scan, daq=daq, force=force, **kwargs
    )
)
callbacks_end_scan.append(_copy_selected_JF_pedestals_to_raw)
callbacks_end_scan.append(end_scan_monitors)
callbacks_end_scan.append(_message_end_scan)

# >>>> Extract for run_table and elog


# if self._run_table or self._elog:
def _create_metadata_structure_start_scan(
    scan, run_table=run_table, elog=elog, append_status_info=True, **kwargs
):
    runname = os.path.basename(scan.fina).split(".")[0]
    runno = int(runname.split("run")[1].split("_")[0])
    metadata = {
        "type": "scan",
        "name": runname.split("_", 1)[1],
        "scan_info_file": scan.scan_info_filename,
    }
    for n, adj in enumerate(scan.adjustables):
        nname = None
        nId = None
        if hasattr(adj, "Id"):
            nId = adj.Id
        if hasattr(adj, "name"):
            nname = adj.name

        metadata.update(
            {
                f"scan_motor_{n}": nname,
                f"from_motor_{n}": scan.values_todo[0][n],
                f"to_motor_{n}": scan.values_todo[-1][n],
                f"id_motor_{n}": nId,
            }
        )
    if np.mean(np.diff(scan.pulses_per_step)) < 1:
        pulses_per_step = scan.pulses_per_step[0]
    else:
        pulses_per_step = scan.pulses_per_step
    metadata.update(
        {
            "steps": len(scan.values_todo),
            "pulses_per_step": pulses_per_step,
            "counters": [daq.name for daq in scan.counterCallers],
        }
    )

    try:
        try:
            metadata.update({"scan_command": get_ipython().user_ns["In"][-1]})
        except:
            print("Count not retrieve ipython scan command!")

        message_string = f"#### Run {runno}"
        if metadata["name"]:
            message_string += f': {metadata["name"]}\n'
        else:
            message_string += "\n"

        if "scan_command" in metadata.keys():
            message_string += "`" + metadata["scan_command"] + "`\n"
        message_string += "`" + metadata["scan_info_file"] + "`\n"
        elog_ids = elog.post(
            message_string,
            Title=f'Run {runno}: {metadata["name"]}',
            text_encoding="markdown",
        )
        scan._elog_id = elog_ids[1]
        metadata.update({"elog_message_id": scan._elog_id})
        metadata.update(
            {"elog_post_link": scan._elog[1]._log._url + str(scan._elog_id)}
        )
    except:
        print("Elog posting failed with:")
        traceback.print_exc()
    if not append_status_info:
        return
    d = {}
    ## use values from status for run_table
    try:
        status = scan.status["status_run_start"]
        d = status["settings"]
        d.update(status["status"])
    except:
        print("Tranferring values from status to run_table did not work")
    t_start_rt = time.time()
    try:
        run_table.append_run(runno, metadata=metadata, d=d)
    except:
        print("WARNING: issue adding data to run table")
    print(f"RT appending: {time.time()-t_start_rt:.3f} s")


# <<<< Extract for run table and elog
callbacks_start_scan.append(_create_metadata_structure_start_scan)

namespace.append_obj(
    "CheckerCA",
    module_name="eco.acquisition.checkers",
    pvname="SLAAR21-LTIM01-EVR0:CALCI",
    thresholds=[0.2, 10],
    required_fraction=0.6,
    filepath_thresholds="/photonics/home/gac-bernina/eco/configuration/checker_thresholds_default",
    filepath_fraction="/photonics/home/gac-bernina/eco/configuration/checker_required_fraction_default",
    lazy=True,
    name="checker_mon_opt_ioxos",
)

namespace.append_obj(
    "CheckerBS",
    module_name="eco.acquisition.checkers",
    bs_channel="SAROP21-PBPS133:INTENSITY",
    thresholds=[0.2, 10],
    required_fraction=0.6,
    filepath_thresholds="/photonics/home/gac-bernina/eco/configuration/checker_thresholds_default",
    filepath_fraction="/photonics/home/gac-bernina/eco/configuration/checker_required_fraction_default",
    lazy=True,
    name="checker",
)

namespace.append_obj(
    "Scans",
    data_base_dir="scan_data",
    scan_info_dir=f"/sf/bernina/data/{config_bernina.pgroup()}/res/scan_info",
    default_counters=[daq],
    checker=checker,
    scan_directories=True,
    callbacks_start_scan=callbacks_start_scan,
    callbacks_end_step=callbacks_end_step,
    callbacks_end_scan=callbacks_end_scan,
    run_table=run_table,
    elog=elog,
    name="scans",
    module_name="eco.acquisition.scan",
    lazy=True,
)


#####################################################################################################
## more temporary devices will be outcoupled to temorary module.
# namespace.append_obj(
#    "RIXS",
#    lazy=True,
#    name="rixs",
#    module_name="eco.endstations.bernina_rixs",
# )

#### Beam pointing cameras for THz setups ####


# namespace.append_obj(
#    "CameraBasler",
#    pvname="SLAAR21-LCAM-C531",
#    lazy=True,
#    name="cam_NIR_position",
#    camserver_group=["Laser", "Bernina"],
#    module_name="eco.devices_general.cameras_swissfel",
# )
#
#
# namespace.append_obj(
#    "CameraBasler",
#    pvname="SLAAR21-LCAM-C511",
#    lazy=True,
#    name="cam_NIR_angle",
#    camserver_group=["Laser", "Bernina"],
#    module_name="eco.devices_general.cameras_swissfel",
# )

namespace.append_obj(
    "AxisPTZ",
    "bernina-cam-mobile1",
    lazy=True,
    name="cam_mob1",
    module_name="eco.devices_general.cameras_ptz",
)

# this is the large inline camera
namespace.append_obj(
    "BerninaInlineMicroscope",
    pvname_camera="SARES20-CAMS142-M3",
    lazy=True,
    name="samplecam_inline",
    module_name="eco.microscopes",
)

# namespace.append_obj(
#     "CameraBasler",
#     "SARES20-CAMS142-C1",
#     lazy=True,
#     name="samplecam_front",
#     module_name="eco.microscopes",
# )

# namespace.append_obj(
#    "MicroscopeMotorRecord",
#    pvname_camera="SARES20-CAMS142-C1",
#    lazy=True,
#    name="samplecam",
#    module_name="eco.microscopes",
#    pvname_zoom="SARES20-MF1:MOT_5",
# )


# from eco.devices_general.cameras_swissfel import FeturaMicroscope
# from eco.elements.assembly import Assembly
# class SpatialTimetool(Assembly):
#     def __init__(self, pvname_camera=None, pvname_base_zoom=None, pvname_target_stage = None, name=None):
#         super().__init__(name=name)
#         self._append(FeturaMicroscope, pvname_camera = pvname_camera, pvname_base_zoom=pvname_base_zoom, name = "camera", camserver_alias=name, is_display="recursive")
#         if pvname_target_stage:
#             self._append(MotorRecord, pvname = pvname_target_stage, name = "target_transl")
#         self._append(MotorRecord,'SARES23-USR:MOT_2', name='delaystage', is_setting=True)

# namespace.append_obj(
#     SpatialTimetool,
#     pvname_camera = "SARES20-CAMS142-M4",
#     pvname_base_zoom="SARES20-FETURA",
#     pvname_target_stage = "SARES20-MF1:MOT_8",
#     name="tt_spatial",
#     lazy=True,
# )

# namespace.append_obj(
#    "MicroscopeFeturaPlus",
#    "SARES20-PROF142-M1",
#    lazy=True,
#    name="samplecam_highres",
#    module_name="eco.microscopes",
# )

# namespace.append_obj(
#    "MicroscopeMotorRecord",
#    "SARES20-CAMS142-C1",
#    lazy=True,
#    pvname_zoom="SARES20-MF1:MOT_7",
#    name="samplecam_topview",
#    module_name="eco.microscopes",
# )

namespace.append_obj(
    "CameraBasler",
    "SARES20-CAMS142-M2",
    lazy=True,
    name="samplecam_sideview_45",
    module_name="eco.devices_general.cameras_swissfel",
)

namespace.append_obj(
    "CameraBasler",
    "SARES20-CAMS142-C1",
    lazy=True,
    name="samplecam_sideview",
    module_name="eco.devices_general.cameras_swissfel",
)

namespace.append_obj(
    "OxygenSensor",
    "SARES20-CWAG-GPS01:ADC08",
    lazy=True,
    name="oxygen_sensor",
    module_name="eco.devices_general.sensors_ai",
)

# namespace.append_obj(
#     "CameraBasler",
#     "SARES20-CAMS142-C2",
#     lazy=True,
#     name="samplecam_back_racks",
#     module_name="eco.devices_general.cameras_swissfel",
# )

# namespace.append_obj(
#     "CameraBasler",
#     "SARES20-CAMS142-C3",
#     lazy=True,
#     name="samplecam_back_door",
#     module_name="eco.devices_general.cameras_swissfel",
# )


# namespace.append_obj(
#    "CameraBasler",
#    "SARES20-CAMS142-C3",
#    lazy=True,
#    name="samplecam_xrd",
#    module_name="eco.devices_general.cameras_swissfel",
# )

# namespace.append_obj(
#     "PaseShifterAramis",
#     "SLAAR02-TSPL-EPL",
#     lazy=True,
#     name="phase_shifter",
#     module_name="eco.devices_general.timing",
# )


# will be split in permanent and temporary
namespace.append_obj(
    "LaserBernina",
    lazy=True,
    name="las",
    module_name="eco.loptics.bernina_laser",
    pvname="SLAAR21-LMOT",
)
namespace.append_obj(
    "PositionMonitors",
    lazy=True,
    name="las_pointing_monitors",
    module_name="eco.loptics.bernina_laser",
)

# namespace.append_obj(
#    "IncouplingCleanBernina",
#    lazy=True,
#    name="las_inc",
#    module_name="eco.loptics.bernina_laser",
# )


from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice
from ..loptics.bernina_laser import DelayTime


# namespace.append_obj(
#     "Organic_crystal_breadboard",
#     lazy=True,
#     name="ocb",
#     module_name="eco.endstations.bernina_sample_environments",
#     Id="SARES23",
# )

from ..epics.adjustable import AdjustablePv, AdjustablePvEnum


# class Double_Pulse_Pump(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)

#         ### dp smaract stages ####

#         self.motor_configuration = {
#             "delaystage_both": {
#                 "id": "SARES23-USR:MOT_15",
#             },
#             "delaystage_pulse2": {
#                 "id": "SARES23-USR:MOT_1",
#             },
#             "wp_both": {
#                 "id": "SARES23-USR:MOT_3",
#             },
#             "wp_pulse2": {
#                 "id": "SARES23-USR:MOT_2",
#             },
#         }
#         for name, config in self.motor_configuration.items():
#             self._append(
#                 SmaractRecord,
#                 pvname=config["id"],
#                 name=name,
#                 is_setting=True,
#             )
#         self._append(
#             DelayTime, self.delaystage_both, name="delay_both", is_setting=True
#         )
#         self._append(
#             DelayTime, self.delaystage_pulse2, name="delay_pulse2", is_setting=True
#         )


# namespace.append_obj(
#    Double_Pulse_Pump,
#    lazy=True,
#    name="pump",
# )


# ad hoc N2 jet readout
class N2jet(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)

        ### lakeshore temperatures ####
        self._append(
            AdjustablePv,
            pvsetname="SARES20-CRYO:TEMP-C_RBV",
            pvreadbackname="SARES20-CRYO:TEMP-C_RBV",
            accuracy=0.1,
            name="sample_temp",
            is_setting=False,
        )
        ### oxford jet readouts ####
        self._append(
            AdjustablePv,
            pvsetname="SARES20-OXCS:GasSetPoint",
            pvreadbackname="SARES20-OXCS:GasSetPoint",
            accuracy=0.1,
            name="gas_temp_setpoint",
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            pvsetname="SARES20-OXCS:GasTemp",
            pvreadbackname="SARES20-OXCS:GasTemp",
            accuracy=0.1,
            name="gas_temp",
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            pvsetname="SARES20-OXCS:GasFlow",
            pvreadbackname="SARES20-OXCS:GasFlow",
            accuracy=0.1,
            name="gas_flow",
            is_setting=False,
        )
        self._append(
            AdjustablePv,
            pvsetname="SARES20-OXCS:Remaining",
            pvreadbackname="SARES20-OXCS:Remaining",
            accuracy=0.1,
            name="gas_remaining",
            is_setting=False,
        )


# # ad hoc incoupling device
class Incoupling(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(SmaractRecord, "SARES23-USR:MOT_12", name="ver", is_setting=True)
        self._append(SmaractRecord, "SARES23-USR:MOT_13", name="hor", is_setting=True)
        # self._append(SmaractRecord, "SARES23-USR:MOT_11", name="x", is_setting=True)
        # self._append(
        #     MotorRecord,
        #     "SLAAR21-LMOT-M521:MOTOR_1",
        #     name="delaystage_eos",
        #     is_setting=True,
        # )
        # self._append(
        #     DelayTime,
        #     self.delaystage_eos,
        #     name="delay_eos",
        #     is_setting=False,
        #     is_display=True,
        # )
        # self._append(
        #     AdjustablePvEnum,
        #     "SLAAR21-LDIO-LAS6991:SET_BO02",
        #     name="eos_is_shut",
        #     is_setting=True,
        # )
        # self._append(
        #     DigitizerIoxosBoxcarChannel,
        #     "SARES20-LSCP9-FNS:CH1",
        #     name=f"signal_pol0",
        #     is_setting=True,
        # )
        # self._append(
        #     DigitizerIoxosBoxcarChannel,
        #     "SARES20-LSCP9-FNS:CH3",
        #     name=f"signal_pol1",
        #     is_setting=True,
        # )


namespace.append_obj(
    Incoupling,
    lazy=True,
    name="las_inc",
)


namespace.append_obj(
    "High_field_thz_chamber",
    name="thc",
    lazy=True,
    module_name="eco.endstations.bernina_sample_environments",
    illumination_mpod=[
        {
            "pvbase": "SARES21-PS7071",
            "channel_number": 5,
            "module_string": "LV_OMPV_1",
            "name": "illumination",
        }
    ],
    helium_control_valve={
        "pvbase": "SARES21-PS7071",
        "channel_number": 4,
        "module_string": "LV_OMPV_1",
        "name": "helium_control_valve",
        "pvname": "SARES20-CWAG-GPS01:DAC04",
    },
    # configuration=["ottifant"],
    configuration=["cube"],
)

#namespace.append_obj(
#    "Organic_crystal_breadboard",
#    lazy=True,
#    name="ocb",
#    delay_offset_detector=NamespaceComponent(namespace, "thc.delay_x_center"),
#    thc_x_adjustable=NamespaceComponent(namespace, "thc.x"),
#    module_name="eco.endstations.bernina_sample_environments",
#)
namespace.append_obj(
    "Organic_crystal_breadboard",
    lazy=True,
    name="ocb",
    delay_offset_detector=None,
    thc_x_adjustable=None,
    module_name="eco.endstations.bernina_sample_environments",
)

namespace.append_obj(
    "Electro_optic_sampling",
    lazy=True,
    name="eos",
    module_name="eco.endstations.bernina_sample_environments",
)
namespace.append_obj(
    "Electro_optic_sampling_new",
    lazy=True,
    name="eos_new",
    module_name="eco.endstations.bernina_sample_environments",
)
# class Sample_stages(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)
#         self._append(MotorRecord, "SARES20-MF1:MOT_11", name="x", is_setting=True)
#         self._append(MotorRecord, "SARES20-MF1:MOT_9", name="y", is_setting=True)

# namespace.append_obj(
#    Sample_stages,
#    lazy=True,
#    name="sample",
# )

# class LaserSteering(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)
#         self._append(SmaractRecord, "SARES23-USR:MOT_3", name="mirr1_pitch", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_4", name="mirr1_roll", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_14", name="mirr2_pitch", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_12", name="mirr2_roll", is_setting=True)

# class THzGeneration(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_16", name="par_x", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_8", name="mirr_x", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_7", name="mirr_z", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_18", name="mirr_ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_9", name="mirr_rz", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_15", name="polarizer", is_setting=True)


# class THzVirtualStages(Assembly):
#     def __init__(self, name=None, mx=None, mz=None, px=None, pz=None):
#         super().__init__(name=name)
#         self._mx = mx
#         self._mz = mz
#         self._px = px
#         self._pz = pz
#         self._append(
#             AdjustableFS,
#             "/photonics/home/gac-bernina/eco/configuration/p21145_mirr_x0",
#             name="offset_mirr_x",
#             default_value=0,
#             is_setting=True,
#         )
#         self._append(
#             AdjustableFS,
#             "/photonics/home/gac-bernina/eco/configuration/p21145_mirr_z0",
#             name="offset_mirr_z",
#             default_value=0,
#             is_setting=True,
#         )
#         self._append(
#             AdjustableFS,
#             "/photonics/home/gac-bernina/eco/configuration/p21145_par_x0",
#             name="offset_par_x",
#             default_value=0,
#             is_setting=True,
#         )
#         self._append(
#             AdjustableFS,
#             "/photonics/home/gac-bernina/eco/configuration/p21145_par_z0",
#             name="offset_par_z",
#             default_value=0,
#             is_setting=True,
#         )

#         def get_divergence(mx, px):
#             return px - self.offset_par_x()

#         def set_divergence(x):
#             mx = self.offset_mirr_x() + x
#             px = self.offset_par_x() + x
#             return mx, px

#         def get_focus_z(mx, pz):
#             return pz - self.offset_par_z()

#         def set_focus_z(z):
#             mz = self.offset_mirr_z() + z
#             pz = self.offset_par_z() + z
#             return mz, pz

#         self._append(
#             AdjustableVirtual,
#             [mx, px],
#             get_divergence,
#             set_divergence,
#             name="divergence_virtual",
#         )
#         self._append(
#             AdjustableVirtual,
#             [mz, pz],
#             get_focus_z,
#             set_focus_z,
#             name="focus_virtual",
#         )

#     def set_offsets_to_current_value(self):
#         self.offset_mirr_x.mv(self._mx())
#         self.offset_mirr_z.mv(self._mz())
#         self.offset_par_x.mv(self._px())
#         self.offset_par_z.mv(self._pz())


# class THz(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)
#         self._append(SmaractRecord, "SARES23-USR:MOT_6", name="par_x", is_setting=True)
#         self._append(MotorRecord, "SARES20-MF1:MOT_10", name="par_y", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_13", name="par_z", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_14", name="par_rx", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_15", name="par_ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_1", name="delaystage_thz", is_setting=True,)
#         self._append(DelayTime, self.delaystage_thz, name="delay_thz", is_setting=False, is_display=True,)
#         self._append(LaserSteering, name="ir_pointing", is_setting=False)
#         self._append(THzGeneration, name="generation", is_setting=False)

### Virtual stages ###
# self._append(
#     THzVirtualStages,
#     name="virtual_stages",
#     mx=self.generation.mirr_x,
#     mz=self.generation.mirr_z,
#     px=self.generation.par_x,
#     pz = self.par_z,
#     is_setting=False)


# namespace.append_obj(
#    THz,
#    lazy=True,
#    name="thz",
# )

# class THz_in_air(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)

#         self._append(SmaractRecord, "SARES23-USR:MOT_5", name="crystal_ROT", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_15", name="ir_1_z", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_13", name="ir_1_Ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_14", name="ir_1_Rx", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_10", name="ir_2_Rx", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_7", name="ir_2_Ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_9", name="para_2_x", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_3", name="thz_mir_x", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_1", name="thz_mir_z", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_8", name="thz_mir_Ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_2", name="thz_mir_Rz", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_6", name="focus_z", is_setting=True)
#         self._append(
#             MotorRecord,
#             "SARES20-MF1:MOT_4",
#             name="focus_y",
#             is_setting=True,
#             is_display=True,
#         )
#         self._append(SmaractRecord, "SARES23-USR:MOT_14", name="focus_x", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_13", name="focus_Rz", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_15", name="focus_Ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_11", name="focus_Rx", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_18", name="thz_wp", is_setting=True)
#         self._append(
#             SmaractRecord, "SARES23-LIC:MOT_16", name="delaystage_thz", is_setting=True
#         )
#         self._append(DelayTime, self.delaystage_thz, name="delay_thz", is_setting=True)
#         self._append(
#             MotorRecord,
#             "SLAAR21-LMOT-M521:MOTOR_1",
#             name="delaystage_800_pump",
#             is_setting=True,
#         )
#         self._append(
#             DelayTime, self.delaystage_800_pump, name="delay_800_pump", is_setting=True
#         )
#         self._append(
#             AdjustableFS,
#             "/photonics/home/gac-bernina/eco/configuration/combined_delta",
#             name="combined_delta",
#             default_value=0,
#             is_setting=True,
#         )
#         self.delay_thz = DelayTime(self.delaystage_thz, name="delay_thz")

#         self._append(
#             AdjustableVirtual,
#             [self.crystal_ROT, self.thz_wp],
#             self.thz_pol_get,
#             self.thz_pol_set,
#             name="thz_polarization",
#         )
#         # self.thz_polarization = AdjustableVirtual(
#         #     [self.crystal_ROT, self.thz_wp],
#         #     self.thz_pol_get,
#         #     self.thz_pol_set,
#         #     name="thz_polarization",
#         # )
#         self._append(
#             AdjustableVirtual,
#             [self.delay_thz, self.delay_800_pump],
#             self.delay_get,
#             self.delay_set,
#             name="combined_delay",
#         )

#         # self.combined_delay = AdjustableVirtual(
#         #     [self.delay_thz, self.delay_800_pump],
#         #     self.delay_get,
#         #     self.delay_set,
#         #     name="combined_delay",
#         # )

#     def thz_pol_set(self, val):
#         return 1.0 * val, 1.0 / 2 * val

#     def thz_pol_get(self, val, val2):
#         return 1.0 * val2

#     def delay_set(self, val):
#         return 1.0 * val + self.combined_delta(), 1.0 * val

#     def delay_get(self, val, val2):
#         return 1.0 * val2


# namespace.append_obj(
#    THz_in_air,
#    lazy=True,
#    name="thz",
# )


namespace.append_obj(
    "SmaractController",
    "SARES23-LIC:MOT_",
    lazy=True,
    name="smaract_ust",
    module_name="eco.motion.smaract",
)

namespace.append_obj(
    "SmaractController",
    "SARES23-USR:MOT_",
    lazy=True,
    name="smaract_user",
    module_name="eco.motion.smaract",
)


from ..devices_general.motors import MotorRecord
from ..loptics.bernina_laser import DelayTime
from ..microscopes import MicroscopeMotorRecord


# class JohannAnalyzer(Assembly):
#     def __init__(self, name=""):
#         super().__init__(name=name)
#         self._append(
#             MotorRecord,
#             "SARES20-MF1:MOT_3",
#             name="pitch",
#             is_setting=True,
#             is_display=True,
#         )
#         self._append(
#             MotorRecord,
#             "SARES20-MF1:MOT_4",
#             name="roll",
#             is_setting=True,
#             is_display=True,
#         )


# namespace.append_obj(JohannAnalyzer, name="analyzer", lazy=True)


# class GratingHolder(Assembly):
#     def __init__(self, name=""):
#         super().__init__(name=name)
#         self._append(y=True,
#             MotorRecord,
#             "SARES20-MF1:MOT_7",
#             name="vertical",
#             is_setting=True,
#             is_display=True,
#         )
#         self._append(
#             SmaractRecord,
#             "SARES23-USR:MOT_6",
#             name="horizontal",
#             is_setting=True,
#             is_display=True,
#         )


# namespace.append_obj(GratingHolder, name="grating_holder")


# ad hoc 2 pulse setup
# class Laser2pulse(Assembly):
#    def __init__(self, name=None):y=True,
#        super().__init__(name=name)
#        self._append(
#            SmaractStreamdevice,
#            "SARES23-ESB1",
#            name="pump_exp_delaystage",
#            is_setting=True,
#        )
#
#        self._append(
#            DelayTime,
#            self.pump_exp_delaystage,
#            name="pump_delay_exp",
#            is_setting=False,
#            is_display=True,
#            reset_current_value_to=False,
#        )
#        self._append(SmaractStreamdevice, "SARES23-ESB5", name="wp", is_setting=True)
#        self._append(
#            SmaractStreamdevice,
#            "SARES23-ESB4",
#            name="pump_2_delaystage",
#            is_setting=True,
#        )
#        self._append(
#            DelayTime,
#            self.pump_2_delaystage,
#            name="pump_2_delay",
#            is_setting=False,
#            is_display=True,
#            reset_current_value_to=False,
#        )
#        self._append(SmaractStreamdevice, "SARES23-ESB6", name="ratio", is_setting=True)
#        self._append(
#            SmaractStreamdevice, "SARES23-ESB17", name="rx_pump", is_setting=True
#        )
#        self._append(
#            SmaractStreamdevice, "SARES23-ESB18", name="ry_pump", is_setting=True
#        )
#
#
# namespace.append_obj(
#    Laser2pulse,
#    lazy=True,
#    name="laser2pulse",
# )


# from eco.xoptics import dcm_pathlength_compensation as dpc

# namespace._append(
#     "MotorRecord",
#     "SLAAR21-LMOT-M523:MOTOR_1",
#     name="delaystage_glob",
#     is_setting=True,
#     module_name="eco.devices_general.motors",
# )
# namespace.append(
#     "DelayTime",
#     delaystage_glob,
#     name="delay_glob",
#     is_setting=True,
#     module_name="eco.loptics.bernina_laser",
# )


namespace.append_obj(
    "SwissFel",
    name="fel",
    lazy=True,
    module_name="eco.fel.swissfel",
)
namespace.append_obj(
    "DoubleCrystalMono",
    pvname="SAROP21-ODCM098",
    fel=fel,
    las=las,
    undulator_deadband_eV=2.0,
    name="mono",
    lazy=True,
    module_name="eco.xoptics.dcm_new",
)

# namespace.append_obj(
#     "AramisDcmFeedback",
#     mono=mono,
#     xbpm=mon_opt,
#     name="mono_feedback",
#     lazy=True,
#     module_name="eco.xoptics.xopt_feedback",
# )


# namespace.append_obj(
#     "MonoTimecompensation",
#     las.delay_glob,
#     mono.mono_und_energy,
#     "/sf/bernina/config/eco/reference_values/dcm_reference_timing.json",
#     "/sf/bernina/config/eco/reference_values/dcm_reference_invert_delay.json",
#     lazy=True,
#     name="mono_und_time_corrected",
#     module_name="eco.xoptics.dcm_pathlength_compensation",
# )


# ad hoc interferometric timetool
# class TTinterferometrid(Assembly):
#    def __init__(self, name=None):
#        super().__init__(name=name)
#        self._append(MotorRecord, "SARES20-MF1:MOT_7", name="z_target", is_setting=True)
#        self._append(
#            MotorRecord, "SARES20-MF1:MOT_10", name="x_target", is_setting=True
#        )
#        self._append(
#            MotorRecord,
#            "SLAAR21-LMOT-M521:MOTOR_1",
#            name="delaystage",
#            is_setting=True
#            #            MotorRecord,"SLAAR21-LMOT-M521",name = ""
#            #               starting following commandline silently:
#            #           caqtdm -macro "P=SLAAR21-LMOT-M521:,M=MOTOR_1" motorx_more.ui
#        )
#        self._append(
#            DelayTime,
#            self.delaystage,
#            name="delay",
#            is_setting=True,
#            is_display=True,
#        )
#        self._append(
#            SmaractStreamdevice,
#            "SARES23-ESB18",
#            name="rot_BC",
#            accuracy=3e-3,
#            is_setting=True,
#        )
#        # self._append(
#        #     MotorRecord, "SARES20-MF1:MOT_15", name="zoom_microscope", is_setting=True
#        # )
#        self._append(
#            MicroscopeMotorRecord,
#            pvname_camera="SARES20-CAMS142-M1",
#            camserver_alias="tt_spatial",
#            pvname_zoom="SARES20-MF1:MOT_15",
#            is_setting=True,
#            is_display="recursive",
#            name="microscope",
#        )
#
#
# namespace.append_obj(
#    TTinterferometrid,
#    lazy=True,
#    name="exp",
# )


############## experiment specific #############


class Pumpdelay(Assembly):
    def __init__(
        self,
        delaystage_PV="SARES23-USR:MOT_2",
        name=None,
    ):
        super().__init__(name=name)

        self._append(SmaractRecord, delaystage_PV, name="delaystage", is_setting=True)
        self._append(DelayTime, self.delaystage, name="pdelay", is_setting=True)


namespace.append_obj(
    Pumpdelay,
    name="pumpdelay",
    lazy=True,
)


from eco.loptics.bernina_laser import Stage_LXT_Delay


# namespace.append_obj(
#     "StageLxtDelay",
#     las.delay_pump,
#     las,
#     lazy=True,
#     name="lxt",
#     direction=-1,
#     module_name="eco.loptics.bernina_laser",
# )

namespace.append_obj(
    "StageLxtDelay",
    ocb.delay_thz,
    las,
    lazy=True,
    name="lxt",
    direction=-1,
    module_name="eco.loptics.bernina_laser",
)

##combined delaystage with phase shifter motion##


#     def thz_pol_set(self, val):
#         return 1.0 * val, 1.0 / 2 * val

#     def thz_pol_get(self, val, val2):
#         return 1.0 * val2


# try to append pgroup folder to path !!!!! This caused eco to run in a timeout without error traceback !!!!!
try:
    import sys
    from ..utilities import TimeoutPath

    if TimeoutPath(f"/sf/bernina/data/{config_bernina.pgroup()}/res/").exists():
        pgroup_eco_path = TimeoutPath(
            f"/sf/bernina/data/{config_bernina.pgroup()}/res/eco"
        )
        pgroup_eco_path.mkdir(mode=0o775, exist_ok=True)
        try:
            pgroup_eco_path.chmod(mode=0o775)
        except:
            pass

        sys.path.append(pgroup_eco_path.as_posix())
    else:
        print(
            "Could not access experiment folder, could be due to more systematic file system failure!"
        )
except:
    print("Did not succeed to append an eco folder in current prgoup")


class Xspect_EH55(Assembly):
    def __init__(self, name="xspect_bernina"):
        super().__init__(name=name)
        self._append(
            MotorRecord, "SARES20-MF1:MOT_15", name="x_crystal", is_setting=True
        )
        self._append(
            MotorRecord, "SARES20-MF1:MOT_16", name="y_crystal", is_setting=True
        )
        self._append(
            SmaractRecord, "SARES23-USR:MOT_17", name="theta_crystal", is_setting=True
        )
        self._append(
            CameraBasler,
            "SARES20-CAMS142-M3",
            name="camera_bsss",
            is_display=False,
            is_setting=False,
        )


namespace.append_obj(Xspect_EH55, name="xspect_bernina", lazy=True)


############## BIG JJ SLIT #####################
namespace.append_obj(
    "SlitBladesGeneral",
    name="slit_cleanup_air",
    def_blade_up={
        "args": [MotorRecord, "SARES20-MF1:MOT_10"],
        "kwargs": {"is_psi_mforce": True},
    },
    def_blade_down={
        "args": [MotorRecord, "SARES20-MF1:MOT_9"],
        "kwargs": {"is_psi_mforce": True},
    },
    def_blade_left={
        "args": [MotorRecord, "SARES20-MF1:MOT_12"],
        "kwargs": {"is_psi_mforce": True},
    },
    def_blade_right={
        "args": [MotorRecord, "SARES20-MF1:MOT_11"],
        "kwargs": {"is_psi_mforce": True},
    },
    module_name="eco.xoptics.slits",
    lazy=True,
)

############## SMALL JJ SLIT #####################

# namespace.append_obj(
#     "SlitPosWidth",
#     pvname="SARES20-MF1:",
#     motornames={
#         "hpos": "MOT_2",
#         "vpos": "MOT_5",
#         "hgap": "MOT_3",
#         "vgap": "MOT_4",
#     },
#     name="slit_cleanup_air",
#     lazy=True,
#     module_name="eco.xoptics.slits",
# )


## N2 sample heater setup

from eco.devices_general.env_sensors import WagoSensor


class SampleHeaterJet(Assembly):
    def __init__(self, name="sampleheaterjet"):
        super().__init__(name=name)
        self._append(
            WagoSensor, pvbase="SARES20-CWAG-GPS01:TEMP-T9", name="sensor_sample"
        )
        self._append(
            WagoSensor, pvbase="SARES20-CWAG-GPS01:TEMP-T10", name="sensor_jet_mount"
        )
        self._append(
            WagoSensor, pvbase="SARES20-CWAG-GPS01:TEMP-T11", name="sensor_hexapod"
        )
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            channel_number=5,
            name="fan_hexapod_1",
        )
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            channel_number=6,
            name="fan_hexapod_2",
        )


# namespace.append_obj(SampleHeaterJet, name="heater_jet", lazy=True)


## sample illumination
from eco.devices_general.powersockets import MpodChannel


class IlluminatorsLasers(Assembly):
    def __init__(self, name="sample_illumination"):
        super().__init__(name=name)
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            channel_number=5,
            name="illumination_inline",
        )
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            channel_number=2,
            name="illumination_side",
        )
        # self._append(
        #    MpodChannel,
        #    pvbase="SARES21-CPCL-PS7071",
        #    channel_number=6,
        #    name="illumination_top",
        # )
        # self._append(
        #    MpodChannel,
        #    pvbase="SARES21-CPCL-PS7071",
        #    channel_number=4,
        #    name="flattening_laser",
        # )


namespace.append_obj(IlluminatorsLasers, name="sample_illumination", lazy=True)

## LIQUID jet setup

# from eco.devices_general.wago import AnalogOutput
# from eco.detector import Jungfrau
# from eco.timing.event_timing_new_new import EvrOutputsample
# from eco.devices_general.digitizers import DigitizerIoxosBoxcarChannel
# from eco.elements.adjustable import AdjustableVirtual
# import numpy as np


class LiquidJetSpectroscopy(Assembly):
    def __init__(self, pgroup_adj=None, config_JF_adj=None, name=None):
        super().__init__(name=name)
        self._append(
            MotorRecord,
            "SARES20-MF1:MOT_12",
            name="x",
            backlash_definition=True,
            is_setting=True,
        )
        self._append(
            MotorRecord,
            "SARES20-MF1:MOT_10",
            name="y",
            backlash_definition=True,
            is_setting=True,
        )
        self._append(
            MotorRecord,
            "SARES20-MF1:MOT_11",
            name="z",
            backlash_definition=True,
            is_setting=True,
        )
        # self._append(
        #     MotorRecord,y=True,
        #     "SARES20-MF1:MOT_3",
        #     name="x_analyzer",
        #     backlash_definition=True,
        #     is_setting=True,
        # )
        # self._append(
        #     MotorRecord,
        #     "SARES21-XRD:MOT_P_T",
        #     name="y_vhdet",
        #     is_setting=True,
        #
        self._append(
            Jungfrau,
            "JF04T01V01",
            name="det_em",
            pgroup_adj=pgroup_adj,
            config_adj=config_JF_adj,
        )


# namespace.append_obj(
#     LiquidJetSpectroscopy,
#     pgroup_adj=config_bernina.pgroup,
#     config_JF_adj=config_JFs,
#     name="liquidjet",
#     lazy=True,
# )


class Tapedrive(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            AdjustablePv, "KERNVARIABLES:DELAYBETWEENXFELANDLASER", name="delay"
        )
        self._append(SmaractRecord, "SARES23-USR:MOT_12", name="freespace_ver")
        self._append(SmaractRecord, "SARES23-USR:MOT_13", name="freespace_hor")

        self._append(MotorRecord, "SARES20-MF1:MOT_13", name="x_target_totem")
        self._append(MotorRecord, "SARES20-MF1:MOT_14", name="y_target_totem")

        self._append(AnalogOutput, "SARES20-CWAG-GPS01:DAC01", name="shutter1")
        self._append(AnalogOutput, "SARES20-CWAG-GPS01:DAC02", name="shutter2")
        self._append(AnalogOutput, "SARES20-CWAG-GPS01:DAC03", name="shutter3")
        self._append(AnalogOutput, "SARES20-CWAG-GPS01:DAC04", name="shutter4")

        self._append(
            EvrOutput,
            f"SARES20-CVME-01-EVR0:RearUniv0",
            pulsers=evr.pulsers,
            name=f"trigger_patch1_bnc16",
            is_setting=True,
            # is_display="recursive",
        )
        self._append(
            EvrOutput,
            f"SARES20-CVME-01-EVR0:RearUniv1",
            pulsers=evr.pulsers,
            name=f"trigger_patch2_bnc16",
            is_setting=True,
            # is_display="recursive",
        )

        self._append(
            Jungfrau,
            "JF07T32V01",
            config_adj=daq.config_JFs,
            pgroup_adj=config_bernina.pgroup,
            name="det_diff",
            is_setting=True,
            is_status=True,
            # is_display="recursive",
        )
        self._append(
            Jungfrau,
            "JF05T01V01",
            config_adj=daq.config_JFs,
            pgroup_adj=config_bernina.pgroup,
            name="det_spect",
            is_setting=True,
            is_status=True,
            # is_display="recursive",
        )
        self._append(
            Jungfrau,
            "JF03T01V01",
            config_adj=daq.config_JFs,
            pgroup_adj=config_bernina.pgroup,
            name="det_imon",
            is_setting=True,
            is_status=True,
            # is_display="recursive",
        )

        self._append(
            DigitizerIoxosBoxcarChannel, "SARES20-LSCP9-FNS:CH1", name="diode_1"
        )
        self._append(
            DigitizerIoxosBoxcarChannel, "SARES20-LSCP9-FNS:CH2", name="diode_2"
        )

        self._append(
            AdjustableFS,
            "/photonics/home/gac-bernina/eco/configuration/p20231_mono_und_offset",
            name="mono_und_calib",
            default_value=[[6500, 0], [7100, 0]],
            is_setting=True,
        )

        def en_set(en):
            ofs = np.array(self.mono_und_calib()).T
            fel_ofs = ofs[1][np.argmin(abs(ofs[0] - en))]
            return en, en / 1000 - fel_ofs

        def en_get(monoen, felen):
            return monoen

        self._append(
            AdjustableVirtual,
            [mono, fel.aramis_photon_energy_undulators],
            en_get,
            en_set,
            name="mono_und_energy",
        )

    def add_mono_und_calibration(self):
        mono_energy = mono.get_current_value()
        fel_offset = (
            mono.get_current_value() / 1000
            - fel.aramis_photon_energy_undulators.get_current_value()
        )
        self.mono_und_calib.mvr([[mono_energy, fel_offset]])


# namespace.append_obj(Tapedrive, name="tapedrive", lazy=True)


#### pgroup specific appending, might be temporary at this location ####

# namespace.append_obj("Xom", module_name="xom", name="xom", lazy=True)


# namespace.init_all()

############## maybe to be recycled ###################

# {
#     "args": [],
#     "name": "ocb",
#     "z_und": 142,
#     "desc": "LiNbO3 crystal breadboard",
#     "type": "eco.endstations.bernina_sample_environments:LiNbO3_crystal_breadboard",
#     "kwargs": {"Id": "SARES23"},
# },class LiquidJetSpectroscopy(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)
#         self._append(
#             MotorRecord,
#             "SARES20-MF1:MOT_2",
#             name="x_jet",
#             backlash_definition=True,
#             is_setting=True,
#         )
#         self._append(
#             MotorRecord,
#             "SARES20-MF1:MOT_4",
#             name="y_jet",
#             backlash_definition=True,
#             is_setting=True,
#         )
#         self._append(
#             MotorRecord,
#             "SARES20-MF1:MOT_6",
#             name="z_jet",
#             backlash_definition=True,
#             is_setting=True,
#         )
#         self._append(
#             MotorRecord,
#             "SARES20-MF1:MOT_3",
#             name="x_analyzer",
#             backlash_definition=True,
#             is_setting=True,
#         )
#         self._append(
#             MotorRecord,
#             "SARES21-XRD:MOT_P_T",
#             name="y_vhdet",
#             is_setting=True,
#         )
#         self._append(
#             Jungfrau, "JF03T01V02", name="det_i0", pgroup_adj=config_bernina.pgroup
#         )
#         self._append(
#             Jungfrau, "JF04T01V01", name="det_em", pgroup_adj=config_bernina.pgroup
#         )
#         self._append(
#             Jungfrau, "JF14T01V01", name="det_vhamos", pgroup_adj=config_bernina.pgroup
#         )
#         self._append(CameraBasler, "SARES20-CAMS142-M2", name="prof_pump")

# {
#     "args": [],
#     "name": "vonHamos",
#     "z_und": 142,
#     "desc": "Kern experiment, von Hamos vertical and horizontal stages ",
#     "type": "eco.devices_general.micos_stage:stage",
#     "kwargs": {
#         "vonHamos_horiz_pv": config["Kern"]["vonHamos_horiz"],
#         "vonHamos_vert_pv": config["Kern"]["vonHamos_vert"],
#     },
# },

# {
#     "name": "mono_old",
#     "args": ["SAROP21-ODCM098"],
#     "kwargs": {
#         "energy_sp": "SAROP21-ARAMIS:ENERGY_SP",
#         "energy_rb": "SAROP21-ARAMIS:ENERGY",
#     },
#     "z_und": 98,
#     "desc": "DCM Monochromator",
#     "type": "eco.xoptics.dcm:Double_Crystal_Mono",
# },


def pgroup2name(pgroup):
    tp = "/sf/bernina/exp/"
    d = Path(tp)
    dirs = [i for i in d.glob("*") if i.is_symlink()]
    names = [i.name for i in dirs]
    targets = [i.resolve().name for i in dirs]
    return names[targets.index(pgroup)]


def name2pgroups(name, beamline="bernina"):
    tp = f"/sf/{beamline}/exp/"
    d = Path(tp)
    dirs = [i for i in d.glob("*") if i.is_symlink()]
    names = [i.name for i in dirs]
    targets = [i.resolve().name for i in dirs]
    eq = [[i_n, i_p] for i_n, i_p in zip(names, targets) if name == i_n]
    ni = [
        [i_n, i_p]
        for i_n, i_p in zip(names, targets)
        if (not name == i_n) and (name in i_n)
    ]
    return eq + ni


def timetool_data_monitor(warning_threshold=1000, loopsleep=5):
    dir(bs_worker)
    tt_kb.spectrum_signal.stream.accumulate(do_accumulate=True)
    print("Monitoring timetool data ...")

    while True:

        eid_diff = int(
            event_system.pulse_id.get_current_value()
            - tt_kb.spectrum_signal.stream.eventIds[-1][-1]
        )
        if eid_diff > warning_threshold:
            message = f"Last timetool data {eid_diff} pulses ago!"
            print(message)
            try:
                e = pyttsx3.init()
                e.say(message)
                e.runAndWait()
                e.stop()
            except:
                pass
        time.sleep(loopsleep)
