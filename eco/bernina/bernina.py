from eco.elements.adjustable import AdjustableFS
from eco.motion.smaract import SmaractController
from .config import components
from .config import config as config_berninamesp
from ..utilities.config import Namespace
from ..aliases import NamespaceCollection
import pyttsx3

from ..utilities.path_alias import PathAlias
import sys,os
from IPython import get_ipython


path_aliases = PathAlias()
sys.path.append("/sf/bernina/config/src/python/bernina_analysis")

namespace = Namespace(
    name="bernina", root_module=__name__, alias_namespace=NamespaceCollection().bernina
)
namespace.alias_namespace.data = []

# Adding stuff that might be relevant for stuff configured below (e.g. config)

_config_bernina_dict = AdjustableFS('/sf/bernina/config/eco/configuration/bernina_config.json',name='_config_bernina_dict')
from eco.elements.adj_obj import AdjustableObject
namespace.append_obj(AdjustableObject,_config_bernina_dict,name='config_bernina')

namespace.append_obj(
    "DummyAdjustable", module_name="eco.elements.adjustable", name="dummy_adjustable"
)
namespace.append_obj(
    "set_global_memory_dir",
    "/sf/bernina/config/eco/memory",
    module_name="eco.elements.memory",
    name="path_memory",
)

namespace.append_obj(
    "DataApi",
    name="archiver",
    module_name="eco.dbase.archiver",
    pv_pulse_id="SARES20-CVME-01-EVR0:RX-PULSEID",
    add_to_cnf=True,
    lazy=False,
)

namespace.append_obj(
    "BerninaEnv",
    name="env_log",
    module_name="eco.fel.atmosphere",
    lazy=True,
)
namespace.append_obj(
    "AdjustableFS",
    "/photonics/home/gac-bernina/eco/configuration/run_table_channels_CA",
    name="_env_channels_ca",
    module_name="eco.elements.adjustable",
)
namespace.append_obj(
    "Run_Table2",
    name= "run_table",
    module_name= "eco.utilities.runtable",
    exp_id = config_bernina.pgroup.value,
    exp_path = f"/sf/bernina/data/{config_bernina.pgroup.value}/res/run_table/",
    devices = "bernina",
    keydf_fname = "/sf/bernina/config/src/python/gspread/gspread_keys.pkl",
    cred_fname = "/sf/bernina/config/src/python/gspread/pandas_push",
    gsheet_key_path = "/sf/bernina/config/eco/reference_values/run_table_gsheet_keys",
    lazy=True
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
)
namespace.append_obj(
    "SlitBlades",
    "SAROP21-OAPU102",
    name="slit_mono",
    module_name="eco.xoptics.slits",
)

from eco.devices_general.motors import SmaractStreamdevice, SmaractRecord

namespace.append_obj(
    "SlitBladesGeneral",
    name="slit_kb",
    def_blade_up={
        "args": [SmaractRecord, "SARES23:LIC2"],
        "kwargs": {},
    },
    def_blade_down={
        "args": [SmaractRecord, "SARES23:LIC1"],
        "kwargs": {},
    },
    def_blade_left={
        "args": [SmaractRecord, "SARES23:LIC9"],
        "kwargs": {},
    },
    def_blade_right={
        "args": [SmaractRecord, "SARES23:LIC4"],
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
        "args": [SmaractRecord, "SARES23:LIC6"],
        "kwargs": {},
    },
    def_blade_down={
        "args": [SmaractRecord, "SARES23:LIC5"],
        "kwargs": {},
    },
    def_blade_left={
        "args": [SmaractRecord, "SARES23:LIC8"],
        "kwargs": {},
    },
    def_blade_right={
        "args": [SmaractRecord, "SARES23:LIC7"],
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
    "RefLaser_Aramis",
    "SAROP21-OLAS136",
    module_name="eco.xoptics.reflaser_new",
    name="reflaser",
    lazy=True,
)
namespace.append_obj(
    "SolidTargetDetectorPBPS_assi",
    "SAROP21-PBPS133",
    pvname_fedigitizerchannels=dict(
        up="SAROP21-CVME-PBPS1:Lnk9Ch0",
        down="SAROP21-CVME-PBPS1:Lnk9Ch12",
        left="SAROP21-CVME-PBPS1:Lnk9Ch15",
        right="SAROP21-CVME-PBPS1:Lnk9Ch13",
    ),
    name="mon_opt_dev",
    module_name="eco.xdiagnostics.intensity_monitors",
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
namespace.append_obj(
    "SolidTargetDetectorPBPS_new_assembly",
    pvname="SARES20-DSDPBPS",
    module_name="eco.xdiagnostics.intensity_monitors",
    name="mon_dsd",
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
    # lazy=False,
    lazy=True,
)
# namespace.append_obj("TimingSystem",pv_master="SIN-TIMAST-TMA",pv_pulse_id="SARES20-CVME-01-EVR0:RX-PULSEID",name='event_system',module_name = "eco.timing.event_timing_new_new",lazy=True)

# namespace.append_obj('Daq', instrument= "bernina",pgroup= config_berninamesp["pgroup"], channels_JF=channels_JF, channels_BS=channels_BS,channels_BSCAM=channels_BSCAM,channels_CA=channels_CA,pulse_id_adj="SLAAR21-LTIM01-EVR0:RX-PULSEID",event_master=event_system.event_master,detectors_event_code=50,name='daq',module_name='eco.acquisition.daq_client')

# namespace.append_obj('Scans',data_base_dir="scan_data",scan_info_dir=f"/sf/bernina/data/{config_berninamesp['pgroup']}/res/scan_info",
# default_counters=[daq],checker=checker,scan_directories=True,run_table=run_table,elog=elog,
# module_name = "eco.acquisition.scan",name="scans")
namespace.append_obj(
    "ProfKbBernina",
    module_name="eco.xdiagnostics.profile_monitors",
    name="prof_kb",
    pvname_mirror="SARES23:LIC11",
    lazy=True,
)
namespace.append_obj(
    "TimetoolBerninaUSD",
    module_name="eco.timing.timing_diag",
    pvname_mirror="SARES23:LIC11",
    name="tt_kb",
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
    "WagoAnalogInputs",
    "SARES20-CWAG-GPS01",
    lazy=True,
    name="analog_inputs",
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
namespace.append_obj(
    "GPS",
    module_name="eco.endstations.bernina_diffractometers",
    name="gps",
    pvname="SARES22-GPS",
    configuration=config_bernina.gps_config(),
    fina_hex_angle_offset="/sf/bernina/config/eco/reference_values/hex_pi_angle_offset.json",
    lazy=True,
)
namespace.append_obj(
    "XRDYou",
    module_name="eco.endstations.bernina_diffractometers",
    Id="SARES21-XRD",
    configuration=config_bernina.xrd_config(),
    diff_detector={"jf_id": "JF01T03V01"},
    invert_kappa_ellbow = config_bernina.invert_kappa_ellbow.value,
    name="xrd",
    lazy=True,
)
namespace.append_obj(
    "KBMirrorBernina_new",
    "SAROP21-OKBV139",
    "SAROP21-OKBH140",
    module_name="eco.xoptics.kb_bernina",
    usd_table=usd_table,
    name="kb",
    diffractometer=xrd,
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
    "/photonics/home/gac-bernina/eco/configuration/config_JFs",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="config_JFs",
)
namespace.append_obj(
    "AdjustableFS",
    "/photonics/home/gac-bernina/eco/configuration/channels_BS",
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
    module_name = "eco.xoptics.att_usd",
    xp=xp,
)


### draft new epics daq ###
# namespace.append_obj(
#    "EpicsDaq",
#    default_file_path=f"/sf/bernina/data/{config_berninamesp['pgroup']}/res/epics_daq/",
#    channels_list=channels_CA_epicsdaq,
#    name="daq_epics_local",
#    module_name="eco.acquisition.epics_data",
#    lazy=True,
# )
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

# namespace.append_obj(
#    "Scans",
#    name="scans_epics",
#    module_name="eco.acquisition.scan",
#    data_base_dir="scan_data",
#    scan_info_dir=f"/sf/bernina/data/{config_berninamesp['pgroup']}/res/scan_info",
#    default_counters=[epics_daq],
#    checker=checker_epics,
#    scan_directories=True,
#    run_table=run_table,
# )
#
#
##### standard DAQ #######
namespace.append_obj(
    "Daq",
    instrument="bernina",
    pgroup=config_bernina.pgroup(),
    channels_JF=channels_JF,
    channels_BS=channels_BS,
    channels_BSCAM=channels_BSCAM,
    channels_CA=channels_CA,
    config_JFs=config_JFs,
    pulse_id_adj="SLAAR21-LTIM01-EVR0:RX-PULSEID",
    event_master=event_master,
    detectors_event_code=50,
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


def _append_namesace_status_to_scan(scan):
    scan.scan_info["scan_parameters"]["namespace_status"] = namespace.get_status(
        base=None
    )


def _append_namespace_aliases_to_scan(scan):
    scan.scan_info["scan_parameters"]["namespace_aliases"] = namespace.alias.get_all()


def _message_end_scan(scan):
    e = pyttsx3.init()
    e.say(f"Finished run {scan.run_number}.")
    e.runAndWait()
    e.stop()


def _copy_scan_info_to_raw(scan, daq=daq):
    run_number = daq.get_last_run_number()
    pgroup = daq.pgroup
    print(f"Copying info file to run {run_number} to the raw directory of {pgroup}.")
    response = daq.append_aux(
        scan.scan_info_filename, pgroup=pgroup, run_number=run_number
    )
    print(f"Status: {response.json()['status']} Message: {response.json()['message']}")


def _increment_daq_run_number(scan, daq=daq):
    try:
        daq_last_run_number = daq.get_last_run_number()
        if int(scan.run_number) is int(daq_last_run_number) + 1:
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
    except Exception as e:
        print(e)


callbacks_start_scan = []
callbacks_start_scan = [lambda scan: namespace.init_all()]
callbacks_start_scan.append(_append_namespace_aliases_to_scan)
callbacks_start_scan.append(_append_namesace_status_to_scan)
callbacks_start_scan.append(_increment_daq_run_number)
callbacks_end_scan = [_message_end_scan]
callbacks_end_scan.append(_copy_scan_info_to_raw)


# >>>> Extract for run_table and elog


# if self._run_table or self._elog:
def _create_metadata_structure_start_scan(scan, run_table=run_table, elog=elog):
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
    metadata.update(
        {
            "steps": len(scan.values_todo),
            "pulses_per_step": scan.pulses_per_step,
            "counters": [daq.name for daq in scan.counterCallers],
        }
    )

    try:
        try:
            metadata.update({"scan_command": get_ipython().user_ns["In"][-1]})
        except:
            print("Count not retrieve ipython scan command!")

        message_string = f'Acquisition run {runno}: {metadata["name"]}\n'
        if "scan_command" in metadata.keys():
            message_string += metadata["scan_command"] + "\n"
        message_string += metadata["scan_info_file"] + "\n"
        scan._elog_id = elog.post(
            message_string, Title=f'Run {runno}: {metadata["name"]}'
        )
        metadata.update({"elog_message_id": scan._elog_id})
        metadata.update(
            {"elog_post_link": scan._elog._log._url + str(scan._elog_id)}
        )
    except:
        print("elog posting failed")
    try:
        run_table.append_run(runno, metadata=metadata)
    except:
        print("WARNING: issue adding data to run table")

# <<<< Extract for run table and elog
callbacks_start_scan.append(_create_metadata_structure_start_scan)


namespace.append_obj(
    "Scans",
    data_base_dir="scan_data",
    scan_info_dir=f"/sf/bernina/data/{config_bernina.pgroup()}/res/scan_info",
    default_counters=[daq],
    checker=checker,
    scan_directories=True,
    callbacks_start_scan=callbacks_start_scan,
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

namespace.append_obj(
    "BerninaInlineMicroscope",
    pvname_camera="SARES20-CAMS142-M3",
    lazy=True,
    name="samplecam_inline",
    module_name="eco.microscopes",
)

# namespace.append_obj(
#    "MicroscopeMotorRecord",
#    pvname_camera="SARES20-CAMS142-C1",
#    lazy=True,
#    name="samplecam",
#    module_name="eco.microscopes",
#    pvname_zoom="SARES20-MF1:MOT_16",
# )

namespace.append_obj(
    "MicroscopeMotorRecord",
    "SARES20-CAMS142-C1",
    lazy=True,
    pvname_zoom="SARES20-MF1:MOT_16",
    name="samplecam_microscope",
    module_name="eco.microscopes",
)

namespace.append_obj(
    "CameraBasler",
    "SARES20-CAMS142-C2",
    lazy=True,
    name="samplecam_sideview",
    module_name="eco.devices_general.cameras_swissfel",
)

namespace.append_obj(
    "CameraBasler",
    "SARES20-CAMS142-C3",
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
# namespace.append_obj(
# "Laser_Exp",
# lazy=True,
# name="las",
# module_name="eco.loptics.bernina_experiment",
# Id="SLAAR21-LMOT",
# smar_config=config_berninamesp["las_smar_config"],
# )

# new version
namespace.append_obj(
    "LaserBernina",
    "SLAAR21-LMOT",
    lazy=True,
    name="las",
    module_name="eco.loptics.bernina_laser",
)
# namespace.append_obj(
#    "IncouplingCleanBernina",
#    lazy=False,
#    name="las_inc",
#    module_name="eco.loptics.bernina_laser",
# )


from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice


# namespace.append_obj(
#    "Organic_crystal_breadboard",
#    lazy=True,
#    name="ocb",
#    module_name="eco.endstations.bernina_sample_environments",
#    Id="SARES23",
# )

from ..epics.adjustable import AdjustablePv

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


namespace.append_obj(
    N2jet,
    lazy=True,
    name="jet",
)

# ad hoc incoupling device
class Incoupling(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(SmaractRecord, "SARES23:LIC13", name="mirr_table_tilt", is_setting=True)
        self._append(SmaractRecord, "SARES23:LIC14", name="mirr_table_roll", is_setting=True)
        # self._append(SmaractRecord, "SARES23:ESB16", name="tilt", is_setting=True)
        # self._append(SmaractRecord, "SARES23:ESB16", name="tilt", is_setting=True)
        # self._append(SmaractRecord, "SARES23:ESB17", name="rotation", is_setting=True)


namespace.append_obj(
    Incoupling,
    lazy=True,
    name="las_inc",
)


namespace.append_obj(
    "SmaractController",
    "SARES23:LIC",
    lazy=True,
    name="smaract_ust",
    module_name="eco.motion.smaract"
)

namespace.append_obj(
    "SmaractController",
    "SARES23:ESB",
    lazy=True,
    name="smaract_user",
    module_name="eco.motion.smaract"
)


from ..devices_general.motors import MotorRecord
from ..loptics.bernina_laser import DelayTime
from ..microscopes import MicroscopeMotorRecord


class JohannAnalyzer(Assembly):
    def __init__(self,name=''):
        super().__init__(name=name)
        self._append(MotorRecord,"SARES20-MF1:MOT_3",name='pitch', is_setting=True, is_status=True)
        self._append(MotorRecord,"SARES20-MF1:MOT_4",name='roll', is_setting=True, is_status=True)

namespace.append_obj(JohannAnalyzer,name='analyzer')

# ad hoc 2 pulse setup
# class Laser2pulse(Assembly):
#    def __init__(self, name=None):
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
#            is_status=True,
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
#            is_status=True,
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
# namespace.append_obj(
#    "MonoTimecompensation",
#    # laser2pulse.pump_delay_exp,
#    las.delay_glob,
#    mono,
#    "/sf/bernina/config/eco/reference_values/dcm_reference_timing.json",
#    "/sf/bernina/config/eco/reference_values/dcm_reference_invert_delay.json",
#    lazy=True,
#    name="mono_time_corrected",
#    module_name="eco.xoptics.dcm_pathlength_compensation",
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
#            is_status=True,
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
#            is_status="recursive",
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

# try to append pgroup folder to path !!!!! This caused eco to run in a timeout without error traceback !!!!!
try:

    import sys
    from ..utilities import TimeoutPath

    if TimeoutPath(f'/sf/bernina/data/{config_bernina.pgroup()}/res/').exists():
        pgroup_eco_path = TimeoutPath(
            f'/sf/bernina/data/{config_bernina.pgroup()}/res/eco'
        )
        pgroup_eco_path.mkdir(mode=775, exist_ok=True)
        sys.path.append(pgroup_eco_path.as_posix())
    else:
        print(
            "Could not access experiment folder, could be due to more systematic file system failure!"
        )
except:
    print("Did not succed to append an eco folder in current prgoup")


#### pgroup specific appending, might be temporary at this location ####

# namespace.append_obj("Xom", module_name="xom", name="xom", lazy=True)


############## maybe to be recycled ###################

# {
#     "args": [],
#     "name": "ocb",
#     "z_und": 142,
#     "desc": "LiNbO3 crystal breadboard",
#     "type": "eco.endstations.bernina_sample_environments:LiNbO3_crystal_breadboard",
#     "kwargs": {"Id": "SARES23"},
# },
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


