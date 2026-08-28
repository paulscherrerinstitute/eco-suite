"""A minimal lazy namespace of controller Assemblies.

`LazyControllers` groups several controller-box Assemblies under one object,
each built only on first attribute access -- the same tab-completion-safe
`lazy_object_proxy` technique `eco.utilities.config.Namespace.append_obj`
uses for top-level namespace names (`eco.utilities.config.Proxy`, reused
directly here rather than reimplemented), scaled down: no `required_names`
file, no background-init/threading, no `sys.modules` publishing -- this is a
nested grouping *inside* one namespace name, not a namespace in its own
right.

Register one `LazyControllers` instance as a single lazy top-level name in
the real Bernina namespace, e.g.::

    namespace.append_obj(build_bernina_controllers, lazy=True, name="controllers")

so the whole branch is exactly one entry in `namespace.all_names`, and can be
excluded from a default startup like any other component, via
`namespace.select_required_names()` / `namespace.required_names([...])`
(see `eco.utilities.config.Namespace`) -- e.g. leaving `"controllers"` out of
the selected list keeps `bernina.controllers` lazy-but-present rather than
built during `init_all(required_only=True)`.
"""

from eco.elements.assembly import Assembly
from eco.utilities.config import Proxy


class LazyControllers(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._controller_names = []

    def add(self, factory, *args, name, **kwargs):
        """Register one controller under `name`. Nothing runs yet --
        `factory(*args, **kwargs, name=name)` is only called the first time
        an attribute of `getattr(self, name)` is actually used."""

        def _build():
            obj = factory(*args, name=name, **kwargs)
            self.__dict__[name] = obj
            self.alias.append(obj.alias)
            self.status_collection.append(obj)
            return obj

        self.__dict__[name] = Proxy(_build)
        self._controller_names.append(name)

    def __dir__(self):
        return list(super().__dir__()) + self._controller_names
