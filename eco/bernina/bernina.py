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
namespace.append_obj(
    "BerninaInlineMicroscope",
    pvname_camera="SARES20-CAMS142-M3",
    lazy=True,
    name="inline_microscope",
    module_name="eco.microscopes",
)


# def __getattr__(*args, **kwargs)a
#     print("called getattr")
#     print(args, kwargs)
