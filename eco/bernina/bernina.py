from .config import components
from .config import config as config_berninamesp
from ..utilities.config import Namespace
from ..aliases import NamespaceCollection


namespace = Namespace(
    name="bernina", root_module=__name__, alias_namespace=NamespaceCollection().bernina
)
namespace.alias_namespace.data = []

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
    "CTA_sequencer",
    "SAR-CCTA-ESB",
    name="seq",
    module_name="eco.timing.event_timing",
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
    lazy=True,
)
namespace.append_obj(
    "TimetoolBerninaUSD",
    module_name="eco.timing.timing_diag",
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
    "XRDYou",
    module_name="eco.endstations.bernina_diffractometers",
    Id="SARES21-XRD",
    configuration=config_berninamesp["xrd_config"],
    diff_detector={"jf_id": "JF01T03V01"},
    name="xrd_you",
    lazy=True,
)
# {
#     "args": [],
#     "name": "xrd",
#     "z_und": 142,
#     "desc": "Xray diffractometer",
#     "type": "eco.endstations.bernina_diffractometers:XRD",
#     "kwargs": {
#         "Id": "SARES21-XRD",
#         "configuration": config["xrd_config"],
#         "diff_detector": {"jf_id": "JF01T03V01"},
#     },
# },
namespace.append_obj(
    "Daq",
    instrument="bernina",
    pgroup=config_berninamesp["pgroup"],
    channels_JF=channels_JF,
    channels_BS=channels_BS,
    channels_BSCAM=channels_BSCAM,
    channels_CA=channels_CA,
    pulse_id_adj="SLAAR21-LTIM01-EVR0:RX-PULSEID",
    event_master=event_master,
    detectors_event_code=50,
    name="daq",
    module_name="eco.acquisition.daq_client",
    lazy=True,
)
namespace.append_obj(
    "Scans",
    data_base_dir="scan_data",
    scan_info_dir=f"/sf/bernina/data/{config_berninamesp['pgroup']}/res/scan_info",
    default_counters=[daq],
    checker=checker,
    scan_directories=True,
    run_table=run_table,
    elog=elog,
    name="scans",
    module_name="eco.acquisition.scan",
    lazy=True,
)

#####################################################################################################
## more temporary devices will be outcoupled to temorary module.

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

namespace.append_obj(
    "PaseShifterAramis",
    "SLAAR02-TSPL-EPL",
    lazy=True,
    name="phase_shifter",
    module_name="eco.devices_general.timing",
)

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

from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice


# ad hoc incoupling device
class Incoupling(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            SmaractStreamdevice, "SARES23-ESB6", name="rx_pump", is_setting=True
        )
        self._append(
            SmaractStreamdevice, "SARES23-ESB17", name="ry_pump", is_setting=True
        )


namespace.append_obj(
    Incoupling,
    lazy=True,
    name="incoupling",
)

# incoupling = Incoupling(name="incoupling")
# {
#     "args": [],
#     "name": "cams_qioptiq",
#     "z_und": 142,
#     "desc": "Qioptic sample viewer in Bernina hutch",
#     "type": "eco.endstations.bernina_cameras:Qioptiq",
#     "kwargs": {
#         "bshost": "sf-daqsync-01.psi.ch",
#         "bsport": 11149,
#         "zoomstage_pv": config["cams_qioptiq"]["zoomstage_pv"],
#         "camera_pv": config["cams_qioptiq"]["camera_pv"],
#     },
# },


# def __getattr__(*args, **kwargs)a
#     print("called getattr")
#     print(args, kwargs)
