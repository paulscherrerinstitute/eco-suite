from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent

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
