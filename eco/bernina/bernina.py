# init()
import sys
from .config import components
from ..aliases import NamespaceCollection
from ..utilities.config import Component, Namespace


namespace = Namespace(
    name="bernina", root_module=__name__, alias_namespace=NamespaceCollection().bernina
)

# adding all stuff from the config components the "old" way of configuring.
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
