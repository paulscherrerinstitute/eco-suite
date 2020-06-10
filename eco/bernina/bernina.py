# init()
import sys
from .config import config



from ..utilities.runtable import Run_Table
def init(pgroup, alias_namespaces, instances):
    run_table = Run_Table(pgroup, alias_namespaces.bernina, instances) 
    return run_table
