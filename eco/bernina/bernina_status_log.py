import numpy as np

from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent
from eco.elements.assembly import Assembly
from eco.elements.adjustable import DummyAdjustable
from eco.elements.detector import DetectorGet
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


class DummyMexicanHat(Assembly):
    """Test fixture: two independent DummyAdjustables (x, y) plus a
    detector (imex) reading a live 2D Mexican-hat (Ricker wavelet) signal
    of their current values -- no real hardware, for testing grid-scan /
    live 2D counter-grid machinery (Stream/Array Grid support, live 2D
    plotting, 2D peak finding, ...) without needing real hardware.

    imex = (1 - r**2) * exp(-r**2 / 2), r = sqrt(x**2 + y**2): peaks at
    1.0 at the origin (x=y=0), crosses zero at r=1, and reaches its most
    negative value (~-0.44) around r=sqrt(3) -- stays within [-1, 1] for
    any x, y. A little Gaussian noise is added by default (see
    noise_amplitude) so it behaves like a real noisy detector rather than
    an exact deterministic function.
    """

    def __init__(self, name=None, limits=(-3, 3), noise_amplitude=0.02):
        super().__init__(name=name)
        self._noise_amplitude = noise_amplitude
        self._append(DummyAdjustable, name="x", limits=list(limits))
        self._append(DummyAdjustable, name="y", limits=list(limits))
        self._append(DetectorGet, self._get_imex, name="imex", is_setting=False)

    def _get_imex(self):
        x = self.x.get_current_value()
        y = self.y.get_current_value()
        r2 = x**2 + y**2
        value = (1 - r2) * np.exp(-r2 / 2)
        if self._noise_amplitude:
            value += np.random.normal(0, self._noise_amplitude)
        return float(value)


namespace.append_obj(DummyMexicanHat, name="dummy", lazy=True)

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
