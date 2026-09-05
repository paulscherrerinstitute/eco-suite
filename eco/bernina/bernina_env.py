from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent

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
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/run_table_channels_CA.json",
    name="_env_channels_ca",
    module_name="eco.elements.adjustable",
    lazy=True,
)