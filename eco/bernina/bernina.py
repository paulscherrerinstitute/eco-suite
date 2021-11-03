from .config import components
from .config import config as config_berninamesp
from ..utilities.config import Namespace
from ..aliases import NamespaceCollection

from ..utilities.path_alias import PathAlias

path_aliases = PathAlias()

namespace = Namespace(
    name="bernina", root_module=__name__, alias_namespace=NamespaceCollection().bernina
)
namespace.alias_namespace.data = []

# Adding stuff that might be relevant for stuff configured below (e.g. config)


namespace.append_obj(
    "set_global_memory_dir",
    "~/eco/memory",
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
    pvname_mirror="SARES23-LIC11",
    lazy=True,
)
namespace.append_obj(
    "TimetoolBerninaUSD",
    module_name="eco.timing.timing_diag",
    pvname_mirror="SARES23-LIC11",
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
# namespace.append_obj(
#    "EventReceiver",
#    "SLAAR21-LTIM01-EVR0",
#    event_master,
#    n_pulsers=24,
#    n_output_front=7,
#    n_output_rear=16,
#    name="evr_hutch_laser",
#    module_name="eco.timing.event_timing_new_new",
#    lazy=True,
# )
# namespace.append_obj(
#    "EventReceiver",
#    "SGE-CPCW-72-EVR0",
#    event_master,
#    n_pulsers=16,
#    n_output_front=16,
#    n_output_rear=0,
#    name="evr_camserver72",
#    module_name="eco.timing.event_timing_new_new",
#    lazy=True,
# )
# namespace.append_obj(
#    "EventReceiver",
#    "SGE-CPCW-83-EVR0",
#    event_master,
#    n_pulsers=16,
#    n_output_front=16,
#    n_output_rear=0,
#    name="evr_camserver83",
#    module_name="eco.timing.event_timing_new_new",
#    lazy=True,
# )
# namespace.append_obj(
#    "EventReceiver",
#    "SGE-CPCW-84-EVR0",
#    event_master,
#    n_pulsers=16,
#    n_output_front=16,
#    n_output_rear=0,
#    name="evr_camserver84",
#    module_name="eco.timing.event_timing_new_new",
#    lazy=True,
# )
# namespace.append_obj(
#    "EventReceiver",
#    "SGE-CPCW-85-EVR0",
#    event_master,
#    n_pulsers=16,
#    n_output_front=16,
#    n_output_rear=0,
#    name="evr_camserver85",
#    module_name="eco.timing.event_timing_new_new",
#    lazy=True,
# )

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
    name="powerstrip_mobile",
    module_name="eco.devices_general.powersockets",
)
namespace.append_obj(
    "GPS",
    module_name="eco.endstations.bernina_diffractometers",
    name="gps",
    pvname="SARES22-GPS",
    configuration=config_berninamesp["gps_config"],
    fina_hex_angle_offset="~/eco/reference_values/hex_pi_angle_offset.json",
    lazy=True,
)
namespace.append_obj(
    "XRDYou",
    module_name="eco.endstations.bernina_diffractometers",
    Id="SARES21-XRD",
    configuration=config_berninamesp["xrd_config"],
    diff_detector={"jf_id": "JF01T03V01"},
    name="xrd",
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
    pgroup=config_berninamesp["pgroup"],
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


def _append_namesace_status_to_scan(scan):
    scan.scan_info["scan_parameters"]["namespace_status"] = namespace.get_status()


def _append_namespace_aliases_to_scan(scan):
    scan.scan_info["scan_parameters"]["namespace_aliases"] = namespace.alias.get_all()


callbacks_start_scan = [lambda scan: namespace.init_all()]
callbacks_start_scan.append(_append_namespace_aliases_to_scan)
callbacks_start_scan.append(_append_namesace_status_to_scan)


namespace.append_obj(
    "Scans",
    data_base_dir="scan_data",
    scan_info_dir=f"/sf/bernina/data/{config_berninamesp['pgroup']}/res/scan_info",
    default_counters=[daq],
    checker=checker,
    scan_directories=True,
    callbacks_start_scan=callbacks_start_scan,
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


namespace.append_obj(
    "CameraBasler",
    pvname="SLAAR21-LCAM-C531",
    lazy=True,
    name="cam_NIR_position",
    camserver_group=["Laser", "Bernina"],
    module_name="eco.devices_general.cameras_swissfel",
)


namespace.append_obj(
    "CameraBasler",
    pvname="SLAAR21-LCAM-C511",
    lazy=True,
    name="cam_NIR_angle",
    camserver_group=["Laser", "Bernina"],
    module_name="eco.devices_general.cameras_swissfel",
)

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

namespace.append_obj(
    "MicroscopeMotorRecord",
    pvname_camera="SARES20-CAMS142-C1",
    lazy=True,
    name="samplecam",
    module_name="eco.microscopes",
    pvname_zoom="SARES20-MF1:MOT_16",
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
namespace.append_obj(
    "IncouplingCleanBernina",
    lazy=False,
    name="las_inc",
    module_name="eco.loptics.bernina_laser",
)


from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice


# ad hoc incoupling device
class Incoupling(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            SmaractStreamdevice, "SARES23-ESB17", name="rx_pump", is_setting=True
        )
        self._append(
            SmaractStreamdevice, "SARES23-ESB18", name="ry_pump", is_setting=True
        )


# namespace.append_obj(
#    Incoupling,
#    lazy=True,
#    name="incoupling",
# )

from ..devices_general.motors import MotorRecord
from ..loptics.bernina_laser import DelayTime
from ..microscopes import MicroscopeMotorRecord

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
namespace.append_obj(
    "MonoTimecompensation",
    # laser2pulse.pump_delay_exp,
    las.delay_glob,
    mono,
    "/photonics/home/gac-bernina/eco/reference_values/dcm_reference_timing.json",
    "/photonics/home/gac-bernina/eco/reference_values/dcm_reference_invert_delay.json",
    lazy=True,
    name="mono_time_corrected",
    module_name="eco.xoptics.dcm_pathlength_compensation",
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

    if TimeoutPath(f'/sf/bernina/data/{config_berninamesp["pgroup"]}/res/').exists():
        pgroup_eco_path = TimeoutPath(
            f'/sf/bernina/data/{config_berninamesp["pgroup"]}/res/eco'
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
