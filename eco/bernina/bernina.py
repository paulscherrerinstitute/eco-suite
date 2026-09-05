import json
from pathlib import Path
from threading import Thread
import traceback

import zmq
import eco
from eco.acquisition.scan import NumpyEncoder
from eco.devices_general.digitizers import DigitizerIoxosBoxcarChannel
from eco.devices_general.pipelines_swissfel import Pipeline
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
    required_names_directory="/sf/bernina/code/gac-bernina/eco_cnf_bernina/required_bernina_names.json",
)
namespace.alias_namespace.data = []
namespace._show_svg = str(Path(__file__).parent / "beamline_interact.svg")


def show():
    namespace.show(backend="window")


# Adding stuff that might be relevant for stuff configured below (e.g. config)
_config_bernina_dict = AdjustableFS(
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/bernina_config.json",
    name="_config_bernina_dict",
)
from eco.elements.adj_obj import AdjustableObject, DetectorObject

namespace.append_obj(AdjustableObject, _config_bernina_dict, name="config_bernina")


counters.DEFAULT_STORAGE_DIR = (
    lambda: f"/sf/bernina/data/{config_bernina.pgroup.get_current_value()}/res/run_data/tmp"
)


from . import bernina_status_log
eco.defaults.ELOG = elog
eco.defaults.ARCHIVER = archiver
from . import bernina_env


# adding all stuff from the config components the "old" way of configuring.
# whatever is added, it is available by the configured name in this module
# afterwards, and can be used immediately, e.g. as input argument for the next thing.

for tk in components:
    namespace.append_obj_from_config(tk, lazy=True)


# Adding all beamline components the "new" way

namespace.append_obj(
    "make_bernina_beamline",
    name="beamline",
    module_name="eco.xoptics.beamline_bernina",
    lazy=True,
)


from . import bernina_vacuum

from . import bernina_event_timing





# First real trial of the generalized beamline-view prototype (see
# eco.elements.beamline_view / Assembly.mark_beamline) on the live namespace,
# in parallel with the untouched eco.xoptics.beamline_assembly.Beamline draft
# (eco.xoptics.beamline_bernina.make_bernina_front_end/make_bernina_experiment_
# hutch) -- z_source/kind values below are taken straight from those modules
# so they agree. Every "fel"-tagged component below also carries an
# organisational subtype -- "front_end" (SARFE10, up to the end-of-front-end
# shutter), "optics" (SAROP21 Bernina optics hutch), or "hutch" (the
# experiment hutch itself, see further down) -- navigable by prefix via
# mark_beamline's path/subtype doc. Purely additive bookkeeping:
# mark_beamline() never touches EPICS or constructs anything, so nothing
# here changes unless namespace.beamline (or .beamline_view(...)) is
# actually used. Try e.g.:
#   namespace.beamline.fel             # every "fel" position, any subtype
#   namespace.beamline.fel.front_end   # just this subtype
#   namespace.beamline.fel.optics
#   namespace.beamline.fel.hutch
#   namespace.beamline.vacuum          # the vacuum system, same 3 subtypes,
#                                       # unfolding into each section's real
#                                       # valve/gauge/pump devices


# The whole "fel"/"front_end" group (pshut_und, slit_und, mon_und, pshut_fe,
# att_fe, prof_fe) is delegated to bernina_front_end.py, which imports
# `namespace` back and self-registers - only needs to come after
# `namespace = Namespace(...)` above; see that module's docstring for why
# that's not a circular import.
from . import bernina_front_end  # noqa: F401
from . import bernina_optics_hutch

from . import bernina_beamline_hutch
from . import bernina_laser
from . import bernina_hutch_devices






# Alarm-overview panels mirroring the caqtdm "Alarms overview" launcher entry
# (S_charts.json -> alarms_caqtdm -> alarms.ui) and its two Papamoll pump-laser
# "Expert" sub-panels. See eco/devices_general/alarms/README.md.
namespace.append_obj(
    "BerninaAlarmsOverview",
    lazy=True,
    name="alarms",
    module_name="eco.devices_general.alarms",
)
namespace.append_obj(
    "PapamollAlarms",
    "26l_dean_1um_35fs",
    lazy=True,
    name="papamoll_alarms_35fs",
    module_name="eco.devices_general.alarms",
)
namespace.append_obj(
    "PapamollAlarms",
    "26h_orr_510nm_100fs",
    lazy=True,
    name="papamoll_alarms_100fs",
    module_name="eco.devices_general.alarms",
)




namespace.append_obj(
    "AdjustableFS",
    # "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/config_JFs.json",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/config_JFs.json",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="config_JFs",
)




### channelsfor daq ###
namespace.append_obj(
    "AdjustableFS",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/channels_JF.json",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_JF",
)
namespace.append_obj(
    "AdjustableFS",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/channels_BS.json",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_BS",
)
namespace.append_obj(
    "AdjustableFS",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/channels_BSCAM.json",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_BSCAM",
)
namespace.append_obj(
    "AdjustableFS",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/channels_CA.json",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_CA",
)

# namespace.append_obj(
#     "MpodModule",
#     "SARES21-PS7071",
#     [1, 2, 3, 4],
#     ["ch1", "ch2", "ch3", "ch4"],
#     module_string="LV_OMPV_1",
#     name="power_LV_patch1",
#     lazy=True,
#     module_name="eco.devices_general.powersockets",
# )

# namespace.append_obj(
#     "MpodModule",
#     "SARES21-PS7071",
#     [5, 6, 7, 8],
#     ["ch1", "ch2", "ch3", "ch4"],
#     module_string="LV_OMPV_1",
#     name="power_LV_patch2",
#     lazy=True,
#     module_name="eco.devices_general.powersockets",
# )

# new MPOD implementation


from eco.loptics.bernina_laser import Stage_LXT_Delay

# namespace.append_obj(
#     "NEW_MpodModule",
#     "SARES20-MPD1",
#     [0, 1, 2, 3],
#     ["ch1", "ch2", "ch3", "ch4"],
#     module_string='1',
#     name="power_LV_patch1",
#     lazy=True,
#     module_name="eco.devices_general.powersockets",
# )

# namespace.append_obj(
#     "NEW_MpodModule",
#     "SARES21-MPD1",
#     [4, 5, 6, 7],
#     ["ch4", "ch5", "ch6", "ch7"],
#     module_string='1',
#     name="power_LV_patch2",
#     lazy=True,
#     module_name="eco.devices_general.powersockets",
# )

# namespace.append_obj(
#     "CheckerCA",
#     module_name="eco.acquisition.checkers",
#     pvname="SLAAR21-LTIM01-EVR0:CALCI",
#     thresholds=[0.2, 10],
#     required_fraction=0.6,
#     filepath_thresholds="/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/default_checker_thresholds.json",
#     filepath_fraction="/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/default_checker_thresholds_fraction.json",
#     lazy=True,
#     name="checker_mon_opt_ioxos",
# )

namespace.append_obj(
    "CheckerBS",
    module_name="eco.acquisition.checkers",
    bs_channel="SAROP21-PBPS133:INTENSITY",
    thresholds=[0.2, 10],
    required_fraction=0.6,
    filepath_thresholds="/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/default_checker_thresholds.json",
    filepath_fraction="/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/default_checker_thresholds_fraction.json",
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
    gsheet_key_path="/sf/bernina/code/gac-bernina/eco_cnf_bernina/reference_values/run_table_gsheet_keys",
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
    gsheet_key_path="/sf/bernina/code/gac-bernina/eco_cnf_bernina/reference_values/run_table_gsheet_keys",
    parse=False,
    lazy=True,
)


# Optional: take run status from a long-running eco.status_server process
# instead of initializing and reading *this* session's namespace at every
# scan start (see eco/status_server/README.md). Off unless the environment
# variable is set, e.g.
#   ECO_STATUS_SERVER=http://saresb-cons-04:8091 scripts/eco-dev -s bernina
# An env var rather than a key in the shared bernina config JSON on purpose:
# which host (if any) runs a status server is a per-session choice, and that
# file is read by every session at the beamline.
_status_server = os.environ.get("ECO_STATUS_SERVER") or None
if _status_server:
    print(f"daq: taking run status from status server {_status_server}")

namespace.append_obj(
    "Daq",
    instrument="bernina",
    status_server=_status_server,
    pgroup=NamespaceComponent(namespace, "config_bernina.pgroup"),
    channels_JF=NamespaceComponent(namespace, "channels_JF"),
    channels_BS=NamespaceComponent(namespace, "channels_BS"),
    channels_BSCAM=NamespaceComponent(namespace, "channels_BSCAM"),
    channels_CA=NamespaceComponent(namespace, "channels_CA"),
    config_JFs=NamespaceComponent(namespace, "config_JFs"),
    # pulse_id_adj="SLAAR21-LTIM01-EVR0:RX-PULSEID",
    pulse_id_adj="SARES20-CVME-01-EVR0:RX-PULSEID",
    event_master=NamespaceComponent(namespace, "event_master"),
    detectors_event_code=50,
    rate_multiplicator="auto",
    name="daq",
    namespace=namespace,
    checker=NamespaceComponent(namespace, "checker"),
    run_table=NamespaceComponent(namespace, "run_table"),
    pulse_picker=NamespaceComponent(namespace, "xp"),
    elog=NamespaceComponent(namespace, "elog"),
    module_name="eco.acquisition.daq_client",
    lazy=True,
)


namespace.append_obj(
    "Scans",
    # data_base_dir="scan_data",
    # scan_info_dir=f"/sf/bernina/data/{config_bernina.pgroup()}/res/scan_info",
    default_counters=[daq],
    # default_counters=[NamespaceComponent(namespace,"daq")],
    # default_counters=NamespaceComponent(namespace,"daq"),
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
namespace.append_obj(
    "RIXS",
    lazy=True,
    name="rixs",
    jf_id="JF14T01V01",
    config_jf_adj=config_JFs,
    pgroup_adj=config_bernina.pgroup,
    module_name="eco.endstations.bernina_rixs",
)

namespace.append_obj(
    "SaxsSpectrometer",
    lazy=True,
    name="xspec_gc",
    config_jf_adj=config_JFs,
    pgroup_adj=config_bernina.pgroup,
    module_name="eco.bernina.bernina_exp",
)


# namespace.append_obj(
#     "CameraBasler",
#     pvname="SARES20-CAMS142-M1",
#     lazy=True,
#     name="samplecam",
#     camserver_group=["Laser", "Bernina"],
#     module_name="eco.devices_general.cameras_swissfel",
# )

# namespace.append_obj(
#     "CameraBasler",
#     pvname="SARES20-CAMS142-C1",
#     lazy=True,
#     name="gccam",
#     camserver_group=["Laser", "Bernina"],
#     module_name="eco.devices_general.cameras_swissfel",
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
#     "MicroscopeMotorRecord",
#     "SARES20-CAMS142-C1",
#     lazy=True,
#     pvname_zoom="SARES20-MF1:MOT_14",
#     name="samplecam_below",
#     module_name="eco.microscopes",
# )

# this is the large inline camera
namespace.append_obj(
    "BerninaInlineMicroscope",
    # pvname_camera="SARES20-CAMS142-M3", #THC
    pvname_camera="SARES20-CAMS142-M3",  # GIC
    lazy=True,
    name="samplecam_microscope",
    module_name="eco.microscopes",
)


namespace.append_obj(
    "CameraBasler",
    "SARES20-CAMS142-M2",
    lazy=True,
    name="samplecam_top",
    module_name="eco.devices_general.cameras_swissfel",
)

namespace.append_obj(
    "CameraBasler",
    "SARES20-CAMS142-M1",
    lazy=True,
    name="samplecam_sideview_90",
    module_name="eco.devices_general.cameras_swissfel",
)

# namespace.append_obj(
#     "CameraBasler",
#     # "SARES20-CAMS142-C1", # THC
#     "SARES20-CAMS142-M3",  # GIC
#     lazy=True,
#     name="samplecam_sideview",
#     module_name="eco.devices_general.cameras_swissfel",
# )



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
#     "PaseShifterAramis",
#     "SLAAR02-TSPL-EPL",
#     lazy=True,
#     name="phase_shifter",
#     module_name="eco.devices_general.timing",
# )


# will be split in permanent and temporary

# OLD type lxt

# namespace.append_obj(
#     "StageLxtDelay",
#     NamespaceComponent(namespace, "las.delay_nopa"),
#     NamespaceComponent(namespace, "las.xlt"),
#     lazy=True,
#     name="lxt",
#     direction=-1,
#     module_name="eco.loptics.bernina_laser",
# )

# NEW type lxt






from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice
from ..loptics.bernina_laser import DelayTime


class VonHamos(Assembly):
    def __init__(self, config_jf_adj, pgroup_adj, name="vhamos"):
        super().__init__(name=name)
        self._append(
            Jungfrau,
            jf_id="JF04T01V01",
            name="detector",
            config_adj=config_jf_adj,
            pgroup_adj=pgroup_adj,
            event_master=NamespaceComponent(namespace, "event_master"),
            detectors_event_code=50,
        )
        self._append(
            MotorRecord, "SARES20-XPS1:MOT_1", name="slit_hor", is_setting=True
        )
        self._append(
            MotorRecord, "SARES20-XPS1:MOT_2", name="slit_ver", is_setting=True
        )


namespace.append_obj(
    VonHamos,
    lazy=True,
    name="vhamos",
    config_jf_adj=config_JFs,
    pgroup_adj=config_bernina.pgroup,
)


# namespace.append_obj(
#     "Organic_crystal_breadboard",
#     lazy=True,
#     name="ocb",
#     module_name="eco.endstations.bernina_sample_environments",
#     Id="SARES23",
# )

from ..epics_utils.adjustable import AdjustablePv, AdjustablePvEnum

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



from ..devices_general.motors import MotorRecord
from ..loptics.bernina_laser import DelayTime
from ..microscopes import MicroscopeMotorRecord


namespace.append_obj(
    "SwissFel",
    name="fel",
    lazy=True,
    module_name="eco.fel.swissfel",
)
namespace.append_obj(
    "DoubleCrystalMono",
    pvname="SAROP21-ODCM098",
    fel=NamespaceComponent(namespace, "fel"),
    delay_time=NamespaceComponent(namespace, "las.delay_monochromator_extension"),
    timing_feedback_enabled=NamespaceComponent(namespace, "tt_kb.feedback_enabled"),
    undulator_deadband_eV=0.5,
    name="mono",
    lazy=True,
    module_name="eco.xoptics.dcm_new",
)
namespace.mark_beamline(
    "mono", types=("fel", "optics"), z_source=98.0, kind="mono",
    description="Si(111) double-crystal monochromator",
)


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

# namespace.append_obj(
#     MotorRecord,
#     "SARES20-MF1:MOT_12",
#     name="bsx",
# )

# namespace.append_obj(
#     "LinearFresnelZonePlate",
#     name="fzp",
#     module_name="eco.bernina.bernina_exp",
#     lazy=True,
# )


# namespace.append_obj(
#     "TimetoolSpatial",
#     module_name="eco.timing.timing_diag",
#     name="tt_spatial_dev",
#     lazy=True,
# )


# class ConvergentBeamDiffraction(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)
#         self._append(
#             SmaractRecord,
#             "SARES20-MCS3:MOT_1",
#             preferred_home_direction="forward",
#             name="sample_x",
#             is_setting=True,
#         )
#         self._append(
#             SmaractRecord,
#             "SARES20-MCS3:MOT_2",
#             preferred_home_direction="forward",
#             name="sample_y",
#             is_setting=True,
#         )
#         self._append(
#             SmaractRecord,
#             "SARES20-MCS3:MOT_3",
#             preferred_home_direction="reverse",
#             name="sample_z",
#             is_setting=True,
#         )
#         self._append(
#             DetectorGet, self._get_zmq_dataset, name="positions", is_display=False
#         )
#         # self._append(DetectorObject,self._positions, name='positions')

#         self._append(
#             SmaractRecord, "SARES20-MCS3:MOT_4", name="ublock_x", is_setting=True
#         )
#         self._append(
#             MotorRecord, "SARES20-MF1:MOT_15", name="ublock_y", is_setting=True
#         )
#         self._append(
#             SmaractRecord, "SARES20-MCS3:MOT_5", name="ublock_z", is_setting=True
#         )
#         self._append(
#             SmaractRecord, "SARES20-MCS3:MOT_6", name="ublock_ry", is_setting=True
#         )
#         self._append(
#             SmaractRecord, "SARES20-MCS3:MOT_7", name="ublock_rz", is_setting=True
#         )

#     def _get_zmq_dataset(self):
#         # import zmq
#         # import json
#         # from pprint import pprint

#         ATTRS = [
#             "SlitU - left (float64, mm)",
#             "SlitU - right (float64, mm)",
#             "SlitU - up (float64, mm)",
#             "SlitU - down (float64, mm)",
#             "SlitD - left (int64, pm)",
#             "SlitD - right (int64, pm)",
#             "SlitD - up (int64, pm)",
#             "SlitD - down (int64, pm)",
#             "MLL - UP - X (float64, nm)",
#             "MLL - UP - Y (float64, nm)",
#             "MLL - UP - Z (float64, nm)",
#             "MLL - UP - Pitch (float64, ndeg)",
#             "MLL - UP - Roll (float64, ndeg)",
#             "MLL - UP - Yaw (float64, ndeg)",
#             "MLL - DOWN - X (float64, nm)",
#             "MLL - DOWN - Y (float64, nm)",
#             "MLL - DOWN - Z (float64, nm)",
#             "MLL - DOWN - Pitch (float64, ndeg)",
#             "MLL - DOWN - Roll (float64, ndeg)",
#             "MLL - DOWN - Yaw (float64, ndeg)",
#             "OSA - X (int64, pm)",
#             "OSA - Y (int64, pm)",
#             "OSA - Z (int64, pm)",
#             "SAM - X (float64, mm)",
#             "SAM - Y (float64, mm)",
#             "SAM - Z (float64, mm)",
#             "SAM - pitch (int64, ndeg)",
#             "SAM - yaw (int64, ndeg)",
#             "CONE - X (float64, mm)",
#             "CONE - Y (float64, mm)",
#             "CONE - Z (float64, mm)",
#             "MIC - X (float64, mm)",
#             "MIC - Y (int64, nm)",
#             "MIC - Z (float64, mm)",
#             "BSU - X (float64, mm)",
#             "BSU - Y (float64, mm)",
#             "BSU - Z (float64, mm)",
#             "BSD - X (float64, mm)",
#             "BSD - Y (float64, mm)",
#             "BSD - Z (float64, mm)",
#         ]

#         HOST = (
#             "129.129.243.102"  # Replace with the IP address of our server in BL network
#         )

#         socket = zmq.Context.instance().socket(zmq.SUB)
#         socket.setsockopt(zmq.RCVTIMEO, 100)
#         socket.setsockopt(zmq.LINGER, 0)
#         socket.connect(f"tcp://{HOST}:50002")
#         socket.setsockopt_string(zmq.SUBSCRIBE, "")
#         while not socket.poll(timeout=100):
#             pass

#         positions = socket.recv()
#         positions = json.loads(positions.decode()).split(";")

#         data = {ATTRS[i]: positions[i] for i in range(len(ATTRS))}
#         # pprint(data)
#         return data


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
    from ..utilities.datafiles import ensure_dir

    if TimeoutPath(f"/sf/bernina/data/{config_bernina.pgroup()}/res/").exists():
        pgroup_eco_path = TimeoutPath(
            f"/sf/bernina/data/{config_bernina.pgroup()}/res/eco"
        )
        # ensure_dir, not mkdir(mode=0o775): the mode argument is masked by
        # the umask (0022 here), so this used to land as 0o755 -- see
        # eco.utilities.datafiles.
        ensure_dir(pgroup_eco_path)

        sys.path.append(pgroup_eco_path.as_posix())
    else:
        print(
            "Could not access experiment folder, could be due to more systematic file system failure!"
        )
except:
    print("Did not succeed to append an eco folder in current pgroup")


# class Xspect_EH55(Assembly):
#     def __init__(self, name="xspect_bernina"):
#         super().__init__(name=name)
#         self._append(
#             MotorRecord, "SARES20-MF1:MOT_15", name="x_crystal", is_setting=True
#         )
#         self._append(
#             MotorRecord, "SARES20-MF1:MOT_16", name="y_crystal", is_setting=True
#         )
#         self._append(
#             SmaractRecord, "SARES23-USR:MOT_17", name="theta_crystal", is_setting=True
#         )
#         self._append(
#             CameraBasler,
#             "SARES20-CAMS142-M3",
#             name="camera_bsss",
#             is_display=False,
#             is_setting=False,
#         )


# namespace.append_obj(Xspect_EH55, name="xspect_bernina", lazy=True)


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

# namespace.append_obj(IlluminatorsLasers, name="sample_illumination", lazy=True)

## LIQUID jet setup

# from eco.devices_general.wago import AnalogOutput
# from eco.detector import Jungfrau
# from eco.timing.event_timing_new_new import EvrOutputsample
# from eco.devices_general.digitizers import DigitizerIoxosBoxcarChannel
# from eco.elements.adjustable import AdjustableVirtual
# import numpy as np


namespace.append_obj(
    "LiquidJetSpectroscopy",
    pgroup_adj=config_bernina.pgroup,
    config_JF_adj=config_JFs,
    name="jet",
    module_name="eco.bernina.bernina_exp",
    lazy=True,
)

namespace.append_obj(
    "TimetoolBerninaDSD",
    name="tt_opt",
    module_name="eco.timing.timing_diag",
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
            event_master=NamespaceComponent(namespace, "event_master"),
            detectors_event_code=50,
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
            event_master=NamespaceComponent(namespace, "event_master"),
            detectors_event_code=50,
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
            event_master=NamespaceComponent(namespace, "event_master"),
            detectors_event_code=50,
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
            event_master=NamespaceComponent(namespace, "event_master"),
            detectors_event_code=50,
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
            "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configurationmono_und_offset.json",
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


# The physical handheld manual-control box (Raspberry Pi pendant). Lazy: it
# only opens a listening socket when you actually call .start(). Its class
# docstring is the full manual - manual_control_box.manual().
namespace.append_obj(
    "ControlBox",
    name="manual_control_box",
    module_name="eco.manual_control.control_box",
    lazy=True,
)
