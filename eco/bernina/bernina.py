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

#####################################################################################################
## more temporary devices will be outcoupled to temorary module.

namespace.append_obj(
    "AxisPTZ",
    "bernina-cam-mobile",
    lazy=True,
    name="cam_mob",
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
namespace.append_obj(
    "Laser_Exp",
    lazy=True,
    name="las",
    module_name="eco.loptics.bernina_experiment",
    Id="SLAAR21-LMOT",
    smar_config=config_berninamesp["las_smar_config"],
)


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
