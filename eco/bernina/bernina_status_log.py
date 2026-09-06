from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent
import eco

namespace.append_obj(
    "RunData",
    NamespaceComponent(namespace,'config_bernina.pgroup'),
    name="runs",
    load_kwargs={
        "checknstore_parsing_result": "/sf/bernina/data/{pgroup}/res",
        # "checknstore_parsing_result": "/sf/bernina/data/{pgroup}/scratch",
        "load_dap_data": True,
        "lazyEscArrays": True,
        "exclude_from_files": ["PVDATA"],
    },
    module_name="eco.acquisition.scan_data",
)

namespace.append_obj(
    "StatusData",
    NamespaceComponent(namespace,'config_bernina.pgroup'),
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
    pgroup_adj=NamespaceComponent(namespace,'config_bernina.pgroup'),
    name="scilog",
    module_name="eco.utilities.elog_scilog",
    lazy=True,
)

namespace.append_obj(
    "ElogsMultiplexer",
    NamespaceComponent(namespace,"scilog"),
    NamespaceComponent(namespace,"elog_gfa"),
    name="elog",
    module_name="eco.utilities.elog",
    lazy=True,
)

namespace.append_obj(
    "DummyAdjustable",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="dummy_adjustable",
)

namespace.append_obj(
    "set_global_memory_dir",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/memory",
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

namespace.append_obj(
    "get_strip_chart_function",
    name="strip_chart",
    module_name="eco.dbase.strip_chart",
    lazy=True,
)
