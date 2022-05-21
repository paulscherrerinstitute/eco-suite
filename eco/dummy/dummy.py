from eco.elements.adjustable import AdjustableFS
from eco.motion.smaract import SmaractController

# from .config import components
# from .config import config as config_berninamesp
from ..utilities.config import Namespace

# from ..aliases import NamespaceCollection
import pyttsx3

from ..utilities.path_alias import PathAlias
import sys, os
from IPython import get_ipython


# path_aliases = PathAlias()
# sys.path.append("/sf/bernina/config/src/python/bernina_analysis")

# namespace = Namespace(
#     name="bernina", root_module=__name__, alias_namespace=NamespaceCollection().bernina
# )
# namespace.alias_namespace.data = []

# # Adding stuff that might be relevant for stuff configured below (e.g. config)

# _config_bernina_dict = AdjustableFS(
#     "/sf/bernina/config/eco/configuration/bernina_config.json",
#     name="_config_bernina_dict",
# )
# from eco.elements.adj_obj import AdjustableObject

# namespace.append_obj(AdjustableObject, _config_bernina_dict, name="config_bernina")
