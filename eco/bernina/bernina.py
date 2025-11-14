import json
from pathlib import Path
from threading import Thread
import traceback

import zmq
import eco
from eco.acquisition.scan import NumpyEncoder
from eco.devices_general.digitizers import DigitizerIoxosBoxcarChannel
from eco.devices_general.powersockets import MpodModule
from eco.devices_general.wago import AnalogOutput
from eco.elements.adjustable import AdjustableFS
from eco.elements.adjustable import AdjustableVirtual
from eco.elements.detector import DetectorGet
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
from eco.acquisition import counters


path_aliases = PathAlias()
sys.path.append("/sf/bernina/config/src/python/bernina_analysis")

namespace = Namespace(
    name="bernina",
    root_module=__name__,
    alias_namespace=NamespaceCollection().bernina,
    required_names_directory="/sf/bernina/config/eco/required_bernina_names.json",
)
namespace.alias_namespace.data = []

# Adding stuff that might be relevant for stuff configured below (e.g. config)
_config_bernina_dict = AdjustableFS(
    "/sf/bernina/config/eco/configuration/bernina_config.json",
    name="_config_bernina_dict",
)
from eco.elements.adj_obj import AdjustableObject, DetectorObject

namespace.append_obj(AdjustableObject, _config_bernina_dict, name="config_bernina")


counters.DEFAULT_STORAGE_DIR = (
    lambda: f"/sf/bernina/data/{config_bernina.pgroup.get_current_value()}/res/run_data/tmp"
)


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
    "DataHub",
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


# Adding all beamline components the "new" way


namespace.append_obj(
    "BerninaVacuum",
    name="vacuum",
    module_name="eco.endstations.bernina_vacuum",
    lazy=True,
)

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


## Old stuff that was still in config and might be needed
namespace.append_obj(
    "Pulsepick",
    Id="SAROP21-OPPI113",
    evronoff="SGE-CPCW-72-EVR0:FrontUnivOut15-Ena-SP",
    evrsrc="SGE-CPCW-72-EVR0:FrontUnivOut15-Src-SP",
    name="xp_old",
    module_name="eco.xoptics.pp",
    lazy=True,
)
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

from eco.devices_general.motors import SmaractStreamdevice, SmaractRecord

namespace.append_obj(
    "SlitBladesGeneral",
    name="slit_kb",
    def_blade_up={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_2"],
        "kwargs": {},
    },
    def_blade_down={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_1"],
        "kwargs": {},
    },
    def_blade_left={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_9"],
        "kwargs": {},
    },
    def_blade_right={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_4"],
        "kwargs": {},
    },
    module_name="eco.xoptics.slits",
    lazy=True,
)


namespace.append_obj(
    "SlitBladesGeneral",
    name="slit_cleanup",
    def_blade_up={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_6"],
        "kwargs": {},
    },
    def_blade_down={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_5"],
        "kwargs": {},
    },
    def_blade_left={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_8"],
        "kwargs": {},
    },
    def_blade_right={
        "args": [SmaractRecord, "SARES20-MCS1:MOT_7"],
        "kwargs": {},
    },
    module_name="eco.xoptics.slits",
    lazy=True,
)


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

namespace.append_obj(
    "DownstreamDiagnostic",
    name="dsd_table",
    module_name="eco.xdiagnostics.dsd",
    lazy=True,
)

namespace.append_obj(
    "Pprm_dsd",
    pvname="SARES20-DSDPPRM",
    pvname_camera="SARES20-PROF146-M1",
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


namespace.append_obj(
    "ProfKbBernina",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_kb",
    pvname_mirror="SARES20-MCS1:MOT_11",
    lazy=True,
)
namespace.append_obj(
    "TimetoolBerninaUSD",
    module_name="eco.timing.timing_diag",
    pvname_mirror="SARES20-MCS1:MOT_11",
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
    configuration=config_bernina.gps_config,
    pgroup_adj=config_bernina.pgroup,
    jf_config=config_JFs,
    fina_hex_angle_offset="/sf/bernina/config/eco/reference_values/hex_pi_angle_offset.json",
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
    configuration=config_bernina.xrd_config,
    pgroup_adj=config_bernina.pgroup,
    jf_config=config_JFs,
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
    usd_table=NamespaceComponent(namespace, "usd_table"),
    name="kb",
    diffractometer=NamespaceComponent(namespace, "xrd"),
    lazy=True,
)

namespace.append_obj(
    "Att_usd",
    name="att_usd",
    module_name="eco.xoptics.att_usd",
    xp=NamespaceComponent(namespace, "xp"),
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


##### standard DAQ #######


# TODO: need to check if the value property actually works here for the pgroup in the run table to make is dynamic!
def path_from_id(exp_id):
    path = f"/sf/bernina/data/{exp_id}/res/run_data/run_table/"
    path_old = f"/sf/bernina/data/{exp_id}/res/run_table/"
    if os.path.exists(path_old):
        path = path_old
    return path


def id_from_name(name):
    if name[0] == "p" and name[1:].isdigit():  # is pgroup
        return name
    else:
        return name2pgroups(name)[0][1]  # take the first one found


namespace.append_obj(
    "Runtable_Manager",
    name="run_table",
    module_name="eco.utilities.runtable_stripped",
    path_from_id=path_from_id,
    id_from_name=id_from_name,
    devices="eco.bernina",
    keydf_fname="/sf/bernina/config/src/python/gspread/gspread_keys.pkl",
    cred_fname="/sf/bernina/config/src/python/gspread/pandas_push",
    gsheet_key_path="/sf/bernina/config/eco/reference_values/run_table_gsheet_keys",
    parse=False,
    lazy=True,
)

namespace.append_obj(
    "Run_Table2",
    name="run_table_old",
    module_name="eco.utilities.runtable_stripped",
    exp_id=config_bernina.pgroup._value,
    # exp_path=f"/sf/bernina/data/{config_bernina.pgroup._value}/res/run_table/",
    exp_path=f"/sf/bernina/data/{config_bernina.pgroup._value}/res/run_data/run_table/",
    devices="eco.bernina",
    keydf_fname="/sf/bernina/config/src/python/gspread/gspread_keys.pkl",
    cred_fname="/sf/bernina/config/src/python/gspread/pandas_push",
    gsheet_key_path="/sf/bernina/config/eco/reference_values/run_table_gsheet_keys",
    parse=False,
    lazy=True,
)


namespace.append_obj(
    "Daq",
    instrument="bernina",
    pgroup=config_bernina.pgroup,
    channels_JF=channels_JF,
    channels_BS=channels_BS,
    channels_BSCAM=channels_BSCAM,
    channels_CA=channels_CA,
    config_JFs=config_JFs,
    # pulse_id_adj="SLAAR21-LTIM01-EVR0:RX-PULSEID",
    pulse_id_adj="SARES20-CVME-01-EVR0:RX-PULSEID",
    event_master=event_master,
    detectors_event_code=50,
    rate_multiplicator="auto",
    name="daq",
    namespace=namespace,
    checker=NamespaceComponent(namespace, "checker"),
    run_table=run_table,
    pulse_picker=NamespaceComponent(namespace, "xp"),
    elog=elog,
    module_name="eco.acquisition.daq_client",
    lazy=True,
)


namespace.append_obj(
    "Scans",
    # data_base_dir="scan_data",
    # scan_info_dir=f"/sf/bernina/data/{config_bernina.pgroup()}/res/scan_info",
    default_counters=[daq],
    callbacks_start_scan=[],
    callbacks_end_step=[],
    callbacks_end_scan=[],
    # elog=elog,
    name="scans",
    module_name="eco.acquisition.scan",
    lazy=True,
)

namespace.append_obj(
    "Scans",
    # data_base_dir="scan_data",
    # scan_info_dir=f"/sf/bernina/data/{config_bernina.pgroup()}/res/scan_info",
    default_counters=[],
    callbacks_start_scan=[],
    callbacks_end_step=[],
    callbacks_end_scan=[],
    name="scans_test",
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
    "MicroscopeMotorRecord",
    pvname_camera="SARES20-CAMS142-C1",  # GIC
    pvname_zoom="SARES20-MF1:MOT_14",
    lazy=True,
    name="jetcam_top",
    module_name="eco.microscopes",
)

namespace.append_obj(
    "CameraBasler",
    # pvname_camera="SARES20-CAMS142-M3", #THC
    "SARES20-CAMS142-M1",  # GIC
    lazy=True,
    name="jetcam_back",
    module_name="eco.microscopes",
)

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

# namespace.append_obj(
#     "CameraBasler",
#     "SARES20-CAMS142-M2",
#     lazy=True,
#     name="samplecam_sideview_45",
#     module_name="eco.devices_general.cameras_swissfel",
# )

# namespace.append_obj(
#     "CameraBasler",
#     # "SARES20-CAMS142-C1", # THC
#     "SARES20-CAMS142-M3",  # GIC
#     lazy=True,
#     name="samplecam_sideview",
#     module_name="eco.devices_general.cameras_swissfel",
# )

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


namespace.append_obj(
    "CameraBasler",
    "SARES20-CAMS142-C2",
    lazy=True,
    name="samplecam_xrd",
    module_name="eco.devices_general.cameras_swissfel",
)

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
#     "IncouplingCleanBernina",
#     lazy=True,
#     name="clic",
#     module_name="eco.loptics.bernina_laser",
# )
namespace.append_obj(
    "MidIR",
    lazy=True,
    name="midir",
    module_name="eco.loptics.bernina_laser",
)

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


from eco.devices_general.motors import ThorlabsPiezoRecord


# # ad hoc incoupling device
class Incoupling(Assembly):
    def __init__(self, delaystage_pump=None, name=None):
        super().__init__(name=name)
        # self._append(
        #     SmaractRecord, "SARES20-MCS2:MOT_13", name="thz_par2_x", is_setting=True
        # )
        # self._append(
        #     SmaractRecord, "SARES20-MCS2:MOT_16", name="thz_par2_z", is_setting=True
        # )
        # self._append(
        #     SmaractRecord, "SARES20-MCS2:MOT_14", name="thz_par2_ry", is_setting=True
        # )
        # self._append(
        #     SmaractRecord, "SARES20-MCS2:MOT_15", name="thz_par2_rx", is_setting=True
        # )
        self._append(
            SmaractRecord, "SARES20-MCS2:MOT_11", name="thz_par1_z", is_setting=True
        )
        self._append(
            SmaractRecord, "SARES20-MCS2:MOT_17", name="thz_par1_ry", is_setting=True
        )

        try:
            self.motor_configuration_thorlabs = {
                "thz_filter": {
                    "pvname": "SLAAR21-LMOT-ELL2",
                },
                "thz_crystal": {
                    "pvname": "SLAAR21-LMOT-ELL3",
                },
                "thz_waveplate": {
                    "pvname": "SLAAR21-LMOT-ELL5",
                },
                "block": {
                    "pvname": "SLAAR21-LMOT-ELL4",
                },
                "polarizer": {
                    "pvname": "SLAAR21-LMOT-ELL1",
                },
            }

            ### thorlabs piezo motors ###
            for name, config in self.motor_configuration_thorlabs.items():
                self._append(
                    ThorlabsPiezoRecord,
                    pvname=config["pvname"],
                    name=name,
                    is_setting=True,
                    accuracy=0.5,
                )
        except Exception as e:
            print(e)

        self._append(
            SmaractRecord, "SARES20-MCS2:MOT_18", name="opa_mirr2_ry", is_setting=True
        )
        self._append(
            SmaractRecord, "SARES20-MCS2:MOT_10", name="opa_mirr2_rx", is_setting=True
        )
        self._append(
            AnalogOutput,
            "SLAAR21-LDIO-LAS6991:DAC07_VOLTS",
            name="opa_mirr1_ry",
            is_setting=True,
        )
        self._append(
            AnalogOutput,
            "SLAAR21-LDIO-LAS6991:DAC08_VOLTS",
            name="opa_mirr1_rx",
            is_setting=True,
        )

        self._append(MotorRecord, "SARES20-XPS1:MOT_X", name="lens_x", is_setting=True)
        self._append(MotorRecord, "SARES20-XPS1:MOT_Y", name="lens_y", is_setting=True)
        self._append(MotorRecord, "SARES20-XPS1:MOT_Z", name="lens_z", is_setting=True)
        self._append(
            MotorRecord, "SARES20-MF1:MOT_13", name="eos_mirr", is_setting=True
        )

        self._append(
            AnalogOutput,
            "SLAAR21-LDIO-LAS6991:DAC06_VOLTS",
            name="eos_fb_rx",
            is_setting=True,
        )
        self._append(
            AnalogOutput,
            "SLAAR21-LDIO-LAS6991:DAC05_VOLTS",
            name="eos_fb_ry",
            is_setting=True,
        )

        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LCAM-C561:FIT2_REQUIRED.PROC",
            name="eos_fb_setpoint_rq",
            accuracy=1,
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LCAM-C561:FIT2_DEFAULT.PROC",
            name="eos_fb_setpoint_df",
            accuracy=1,
            is_setting=True,
        )
        self._append(
            AdjustablePv,
            pvsetname="SLAAR21-LTIM01-EVR0:CALCW.A",
            name="eos_fd_enable",
            accuracy=1,
            is_setting=True,
        )

        self._append(
            AdjustableVirtual,
            [self.thz_crystal, self.thz_waveplate],
            lambda c, w: c,
            lambda angle: [angle, angle / 2],
            name="thz_polarization",
            is_setting=False,
        )

        # self._append(
        #     AdjustableVirtual,
        #     [self.thz_par1_z, self.thz_par2_z],
        #     lambda z1, z2: z2,
        #     lambda z: [
        #         self.thz_par1_z.get_current_value()
        #         + (z - self.thz_par2_z.get_current_value()),
        #         z,
        #     ],
        #     name="thz_focus",
        #     is_setting=False,
        #     is_display=False,
        # )

        # self._append(
        #     delaystage_pump,
        #     name="delaystage_pump",
        #     is_setting=False,
        #     is_display=False,
        # )

        # self._append(
        #     AdjustableVirtual,
        #     [self.delaystage_pump, self.thz_par2_x],
        #     lambda d, x: x,
        #     lambda x: [
        #         self.delaystage_pump.get_current_value()
        #         + (x - self.thz_par2_x.get_current_value()) / 2,
        #         x,
        #     ],
        #     name="thz_par2_x_delaycomp",
        #     is_setting=False,
        #     is_display=False,
        # )

    # def thz_pol_set(self, val):
    #     return 1.0 * val, 1.0 / 2 * val

    # def thz_pol_get(self, val, val2):
    #     return 1.0 * val2


namespace.append_obj(
    Incoupling,
    delaystage_pump=NamespaceComponent(namespace, "las.delaystage_pump"),
    lazy=True,
    name="las_inc",
)


# namespace.append_obj(
#    "Organic_crystal_breadboard",
#    lazy=True,
#    name="ocb",
#    delay_offset_detector=NamespaceComponent(namespace, "thc.delay_x_center"),
#    thc_x_adjustable=NamespaceComponent(namespace, "thc.x"),
#    module_name="eco.endstations.bernina_sample_environments",
# )
# namespace.append_obj(
#     "Organic_crystal_breadboard",
#     lazy=True,
#     name="ocb",
#     delay_offset_detector=None,
#     thc_x_adjustable=None,
#     module_name="eco.endstations.bernina_sample_environments",
# )

# namespace.append_obj(
#     "Electro_optic_sampling",
#     lazy=True,
#     name="eos",
#     module_name="eco.endstations.bernina_sample_environments",
# )
# namespace.append_obj(
#     "Electro_optic_sampling_new",
#     lazy=True,
#     name="eos_new",
#     module_name="eco.endstations.bernina_sample_environments",
# )
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
#         self._append(
#             AdjustableVirtual,
#             [self.crystal_ROT, self.thz_wp],
#             self.thz_pol_get,
#             self.thz_pol_set,
#             name="",
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
#         self._append(SmaractRecord, ":MOT_18", name="thz_wp", is_setting=True)
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
#             name="",
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
    "SARES20-MCS1:MOT_",
    lazy=True,
    name="smaract_usd",
    module_name="eco.motion.smaract",
)
namespace.append_obj(
    "SmaractController",
    "SARES20-MCS2:MOT_",
    lazy=True,
    name="smaract_user1",
    module_name="eco.motion.smaract",
)
namespace.append_obj(
    "SmaractController",
    "SARES20-MCS3:MOT_",
    lazy=True,
    name="smaract_user2",
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

namespace.append_obj(
    MotorRecord,
    "SARES20-MF1:MOT_12",
    name="bsx",
)


class ConvergentBeamDiffraction(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            SmaractRecord,
            "SARES20-MCS3:MOT_1",
            preferred_home_direction="forward",
            name="sample_x",
            is_setting=True,
        )
        self._append(
            SmaractRecord,
            "SARES20-MCS3:MOT_2",
            preferred_home_direction="forward",
            name="sample_y",
            is_setting=True,
        )
        self._append(
            SmaractRecord,
            "SARES20-MCS3:MOT_3",
            preferred_home_direction="reverse",
            name="sample_z",
            is_setting=True,
        )
        self._append(
            DetectorGet, self._get_zmq_dataset, name="positions", is_display=False
        )
        # self._append(DetectorObject,self._positions, name='positions')

        self._append(
            SmaractRecord, "SARES20-MCS3:MOT_4", name="ublock_x", is_setting=True
        )
        self._append(
            MotorRecord, "SARES20-MF1:MOT_15", name="ublock_y", is_setting=True
        )
        self._append(
            SmaractRecord, "SARES20-MCS3:MOT_5", name="ublock_z", is_setting=True
        )
        self._append(
            SmaractRecord, "SARES20-MCS3:MOT_6", name="ublock_ry", is_setting=True
        )
        self._append(
            SmaractRecord, "SARES20-MCS3:MOT_7", name="ublock_rz", is_setting=True
        )

    def _get_zmq_dataset(self):
        # import zmq
        # import json
        # from pprint import pprint

        ATTRS = [
            "SlitU - left (float64, mm)",
            "SlitU - right (float64, mm)",
            "SlitU - up (float64, mm)",
            "SlitU - down (float64, mm)",
            "SlitD - left (int64, pm)",
            "SlitD - right (int64, pm)",
            "SlitD - up (int64, pm)",
            "SlitD - down (int64, pm)",
            "MLL - UP - X (float64, nm)",
            "MLL - UP - Y (float64, nm)",
            "MLL - UP - Z (float64, nm)",
            "MLL - UP - Pitch (float64, ndeg)",
            "MLL - UP - Roll (float64, ndeg)",
            "MLL - UP - Yaw (float64, ndeg)",
            "MLL - DOWN - X (float64, nm)",
            "MLL - DOWN - Y (float64, nm)",
            "MLL - DOWN - Z (float64, nm)",
            "MLL - DOWN - Pitch (float64, ndeg)",
            "MLL - DOWN - Roll (float64, ndeg)",
            "MLL - DOWN - Yaw (float64, ndeg)",
            "OSA - X (int64, pm)",
            "OSA - Y (int64, pm)",
            "OSA - Z (int64, pm)",
            "SAM - X (float64, mm)",
            "SAM - Y (float64, mm)",
            "SAM - Z (float64, mm)",
            "SAM - pitch (int64, ndeg)",
            "SAM - yaw (int64, ndeg)",
            "CONE - X (float64, mm)",
            "CONE - Y (float64, mm)",
            "CONE - Z (float64, mm)",
            "MIC - X (float64, mm)",
            "MIC - Y (int64, nm)",
            "MIC - Z (float64, mm)",
            "BSU - X (float64, mm)",
            "BSU - Y (float64, mm)",
            "BSU - Z (float64, mm)",
            "BSD - X (float64, mm)",
            "BSD - Y (float64, mm)",
            "BSD - Z (float64, mm)",
        ]

        HOST = (
            "129.129.243.102"  # Replace with the IP address of our server in BL network
        )

        socket = zmq.Context.instance().socket(zmq.SUB)
        socket.setsockopt(zmq.RCVTIMEO, 100)
        socket.setsockopt(zmq.LINGER, 0)
        socket.connect(f"tcp://{HOST}:50002")
        socket.setsockopt_string(zmq.SUBSCRIBE, "")
        while not socket.poll(timeout=100):
            pass

        positions = socket.recv()
        positions = json.loads(positions.decode()).split(";")

        data = {ATTRS[i]: positions[i] for i in range(len(ATTRS))}
        # pprint(data)
        return data


# namespace.append_obj(
#     ConvergentBeamDiffraction,
#     name="cbd",
#     lazy=True,
# )

# WHAT WAS THIS FOR? --> removed 1015-09-01 >>>>>>>>

# class Pumpdelay(Assembly):
#     def __init__(
#         self,
#         delaystage_PV="SARES23-USR:MOT_2",
#         name=None,
#     ):
#         super().__init__(name=name)

#         self._append(SmaractRecord, delaystage_PV, name="delaystage", is_setting=True)
#         self._append(DelayTime, self.delaystage, name="pdelay", is_setting=True)


# namespace.append_obj(
#     Pumpdelay,
#     name="pumpdelay",
#     lazy=True,
# )
# <<<<< WHAT WAS THIS FOR? --> removed 1015-09-01


from eco.loptics.bernina_laser import Stage_LXT_Delay

# OLD type lxt

namespace.append_obj(
    "StageLxtDelay",
    NamespaceComponent(namespace, "las.delay_pump"),
    NamespaceComponent(namespace, "las.xlt"),
    lazy=True,
    name="lxt",
    direction=-1,
    module_name="eco.loptics.bernina_laser",
)

# NEW type lxt

# namespace.append_obj(
#     "LxtCompStageDelay",
#     NamespaceComponent(namespace, "tt_kb.delay"),
#     NamespaceComponent(namespace, "las.xlt"),
#     feedback_enabled_adj=NamespaceComponent(namespace, "tt_kb.feedback_enabled"),
#     lazy=True,
#     name="lxt",
#     module_name="eco.loptics.bernina_laser",
# )

##combined delaystage with phase shifter motion##


#     def thz_pol_set(self, val):
#         return 1.0 * val, 1.0 / 2 * val

#     def thz_pol_get(self, val, val2):
#         return 1.0 * val2


# try to append pgroup folder to path !!!!! This caused eco to run in a timeout without error traceback !!!!!
# TODO  pgroup non dynamic here!
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
    name="slit_cleanup_sam",
    def_blade_up={
        "args": [MotorRecord, "SARES20-MF1:MOT_2"],
        "kwargs": {"is_psi_mforce": True},
    },
    def_blade_down={
        "args": [MotorRecord, "SARES20-MF1:MOT_3"],
        "kwargs": {"is_psi_mforce": True},
    },
    def_blade_left={
        "args": [MotorRecord, "SARES20-MF1:MOT_5"],
        "kwargs": {"is_psi_mforce": True},
    },
    def_blade_right={
        "args": [MotorRecord, "SARES20-MF1:MOT_4"],
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


namespace.append_obj(SampleHeaterJet, name="heater_jet", lazy=True)


## sample illumination
from eco.devices_general.powersockets import MpodChannel


class IlluminatorsLasers(Assembly):
    def __init__(self, name="sample_illumination"):
        super().__init__(name=name)
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            channel_number=4,
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
    def __init__(
        self, pgroup_adj=None, config_JF_adj=None, name=None, v_g=None, e2v=None
    ):
        super().__init__(name=name)
        self._v_g = v_g
        self._e2v = e2v
        self._append(
            MotorRecord,
            "SARES20-MF1:MOT_12",
            name="x",
            backlash_definition=True,
            is_setting=True,
        )
        self._append(
            MotorRecord,
            "SARES20-XPS1:MOT_JET_Y",
            name="y",
            backlash_definition=True,
            is_setting=True,
        )
        self._append(
            MotorRecord,
            "SARES20-MF1:MOT_13",
            name="z",
            backlash_definition=True,
            is_setting=True,
        )
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            channel_number=4,
            name="light",
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
            "JF03T01V02",
            name="det_jf",
            pgroup_adj=pgroup_adj,
            config_adj=config_JF_adj,
        )
        self._append(
            MpodChannel,
            pvbase="SARES21-PS7071",
            module_string="HV_EHS_3",
            channel_number=1,
            name="apd",
        )
        self._append(
            AdjustableFS,
            "/photonics/home/gac-bernina/eco/configuration/apd_voltage_calibration",
            name="apd_voltage_calibration",
            is_display=False,
            is_setting=True,
        )

        # Convert energy → voltage through calibration
        def ene2volt(energy):
            try:
                E, V = np.asarray(self.apd_voltage_calibration()).T
                return np.interp(energy, E, V)
            except:
                return np.nan

        # Getter: read the APD voltage and return it as the virtual value
        def get_voltage(apd_voltage):
            return apd_voltage

        # Setter: compute voltage from energy and set it
        def set_voltage(target_energy):
            voltage = ene2volt(target_energy)
            self.apd.voltage.set_target_value(voltage)
            return voltage

        # Create virtual adjustable:
        self._append(
            AdjustableVirtual,
            [self.apd.voltage],  # real adjustable(s)
            get_voltage,  # getter
            set_voltage,  # setter
            reset_current_value_to=False,
            name="ene2volt",
        )


namespace.append_obj(
    LiquidJetSpectroscopy,
    pgroup_adj=config_bernina.pgroup,
    config_JF_adj=config_JFs,
    name="jet",
    lazy=True,
)

from eco.detector import Jungfrau


class XrayWaveplate(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)

        self._append(
            EvrOutput,
            f"SARES20-CVME-01-EVR0:RearUniv2",
            pulsers=evr.pulsers,
            name=f"galvo_trigger",
            is_setting=True,
            # is_display="recursive",
        )

        self._append(
            AnalogOutput,
            "SARES20-CWAG-GPS01:DAC05",
            name="galvo_dc_voltage",
            is_display=True,
            is_setting=True,
        )

        # self._append(
        #    MpodChannel,
        #    pvbase = "SARES21-PS7071",
        #    channel_number=1,
        #    module_string= "HV_EHS_3_",
        #    name="diode_bias",
        #    is_display=True,
        # )

        self._append(
            Jungfrau,
            "JF01T03V01",
            config_adj=daq.config_JFs,
            pgroup_adj=config_bernina.pgroup,
            name="det_jf",
            is_setting=True,
            is_status=True,
            # is_display="recursive",
        )

        self._append(
            DigitizerIoxosBoxcarChannel, "SARES20-LSCP9-FNS:CH1", name="diode_side"
        )
        self._append(
            DigitizerIoxosBoxcarChannel, "SARES20-LSCP9-FNS:CH3", name="diode_bottom"
        )


namespace.append_obj(XrayWaveplate, name="xw", lazy=True)


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


def change_pgroup(searchstring="", config=config_bernina):
    """
    Change the pgroup of the bernina config.
    """
    gs = name2pgroups(searchstring)
    if len(gs) == 0:
        print("No pgroup found.")
    # elif len(gs) == 1:
    #     print(f"Found pgroup for {gs[0][0]} : {gs[0][1] }")
    #     print(f'(old pgroup: {config.pgroup})')
    #     if input('would you like to change? (y/n) ')=='y':
    #         config.pgroup = gs[0][1]
    #         print(f"Changed pgroup to {config.pgroup}")
    else:
        old_group = config.pgroup.get_current_value()
        try:
            print(f"Currently {pgroup2name(old_group)}: {old_group}")
        except:
            pass

        print(f"Found {len(gs)} pgroups:")
        for i, g in enumerate(gs):
            print(f"{i+1}: {g[0]} ({g[1]})")
        try:
            sel = int(input("Please select the pgroup to use: ")) - 1

            if sel < 0 or sel >= len(gs):
                raise ValueError("Invalid selection")

            config.pgroup.set_target_value(gs[sel][1]).wait()
            print(f"Changed pgroup from {old_group} to {gs[sel][1]}")

        except ValueError as e:
            print(f"Invalid selection: {e}")
            # traceback.print_exc()


from eco.utilities import linlog_intervals, roundto


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
