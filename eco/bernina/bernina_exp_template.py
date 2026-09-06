# This is only a template for creating experiment-specific files -- it is
# copied into the current pgroup's res/eco/ folder (as bernina_exp.py) the
# first time that folder is empty, then imported and left alone; edit the
# per-pgroup copy, not this one.
from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent
from eco.elements.assembly import Assembly

class MyExp(Assembly):
    """A simple experiment with a timing master and a few devices."""

    def __init__(self, adj_from_bernina, det_from_bernina, name=None):
        super().__init__( name=name)
        self._append(adj_from_bernina,name="test_adjustable", is_display=True, is_setting=True)
        self._append(det_from_bernina,name="pulse_id", is_display=True, is_setting=False)

namespace.append_obj(MyExp,
                 NamespaceComponent(namespace,"dummy_adjustable"),  # replace with a real adjustable name
                 NamespaceComponent(namespace,"daq.pulse_id"),
                 name="my_exp_object_1",
                 lazy=True,  # lazy=True: only resolves the components above once "my_exp_object_1" is actually touched
                 )



