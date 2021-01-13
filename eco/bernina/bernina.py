# init()
import sys
from .config import components
from importlib import import_module
from lazy_object_proxy import Proxy
from ..aliases import NamespaceCollection
from ..utilities.config import Component


class Namespace(object):
    def __init__(self, name=None, root_module=None, alias_namespace=None):
        self.name = name
        self.lazy_items = {}
        self.initialized_items = {}
        self.root_module = root_module
        self.alias_namespace = alias_namespace

    @property
    def initialized_names(self):
        return set(self.initialized_items.keys())

    @property
    def lazy_names(self):
        return set(self.lazy_items.keys())

    @property
    def all_names(self):
        return self.initialized_names | self.lazy_names

    def append_obj(
        self, obj_factory, *args, lazy=False, name=None, module_name=None, **kwargs
    ):
        if lazy:

            def init_local():
                if module_name:
                    obj_maker = getattr(import_module(module_name), obj_factory)
                else:
                    obj_maker = obj_factory
                try:
                    obj_initialized = obj_maker(*args, name=name, **kwargs)
                except TypeError:
                    obj_initialized = obj_maker(*args, **kwargs)

                self.initialized_items[name] = self.lazy_items.pop(name)
                if self.alias_namespace and hasattr(obj_initialized, "alias"):
                    for ta in obj_initialized.alias.get_all():
                        try:
                            self.alias_namespace.update(
                                ta["alias"], ta["channel"], ta["channeltype"]
                            )
                        except:
                            print(f'could not init alias {ta["alias"]}')
                else:
                    print(f"object {name} has no alias!")
                return obj_initialized

            obj_lazy = Proxy(init_local)
            self.lazy_items[name] = obj_lazy
            if self.root_module:
                sys.modules[self.root_module].__dict__[name] = obj_lazy
            return obj_lazy

        else:
            if module_name:
                obj_maker = getattr(import_module(module_name), obj_factory)
            else:
                obj_maker = obj_factory
            try:
                obj = obj_maker(*args, name=name, **kwargs)
            except TypeError:
                obj = obj_maker(*args, **kwargs)
            self.initialized_items[name] = obj
            if self.root_module:
                sys.modules[self.root_module].__dict__[name] = obj
            if self.alias_namespace and hasattr(obj, "alias"):
                for ta in obj.alias.get_all():
                    try:
                        self.alias_namespace.update(
                            ta["alias"], ta["channel"], ta["channeltype"]
                        )
                    except:
                        print(f'could not init alias {ta["alias"]}')
            else:
                print(f"object {name} has no alias!")
            return obj

    def get_obj(self, name):
        if name in self.lazy_names:
            return self.lazy_items[name]
        elif name in self.initialized_names:
            return self.initialized_items[name]
        else:
            raise Exception("Name is not initialized!")

    def append_obj_from_config(self, cnf, lazy=False):
        module_name, obj_factory = cnf["type"].split(":")
        args = []
        for targ in cnf["args"]:
            if isinstance(targ, Component):
                args.append(self.get_obj(targ.name))
            else:
                args.append(targ)
        kwargs = {}
        for tk, tv in cnf["kwargs"].items():
            if isinstance(tv, Component):
                kwargs[tk] = self.get_obj(tv.name)
            else:
                kwargs[tk] = tv
        if "lazy" in cnf.keys():
            lazy = cnf["lazy"]

        self.append_obj(
            obj_factory,
            *args,
            lazy=lazy,
            name=cnf["name"],
            module_name=module_name,
            **kwargs,
        )


namespace = Namespace(
    name="bernina", root_module=__name__, alias_namespace=NamespaceCollection().bernina
)

namespace.append_obj(
    "AxisPTZ",
    "bernina-cam-n",
    lazy=True,
    name="cam_north",
    module_name="eco.devices_general.cameras_ptz",
)

cn = cam_north

# {
# "name": "elog",
# "type": "eco.utilities.elog:Elog",
# "args": ["https://elog-gfa.psi.ch/Bernina"],
# "kwargs": {
# "screenshot_directory": "/tmp",
# },
# },


# @init_obj(lazy=is_globally_lazy)
# def init_specific_device(*args, **kwargs):
# from whatever import something

# return something(*args, **kwargs)


# specific_name = init_specific_device(name=specific_name)
