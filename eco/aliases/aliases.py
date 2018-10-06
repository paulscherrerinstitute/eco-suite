import os
from pathlib import Path
import json


class Alias:
    def __init__(self, alias, channel=None, channeltype=None):
        self.alias = alias
        self.channel = channel
        self.channeltype = channeltype
        self.children = []

    def append(self, subalias):
        assert type(subalias) is Alias, "You can only append aliases to aliases!"
        assert not (
            subalias.alias in [tc.alias for tc in self.children]
        ), f"Alias {subalias.alias} exists already!"
        self.children.append(subalias)

    def get_all(self):
        aa = []
        if self.channel:
            ta = {}
            ta["alias"] = self.alias
            ta["channel"] = self.channel
            if self.channeltype:
                ta["channeltype"] = self.channeltype
            aa.append(ta)
        if self.children:
            for tc in self.children:
                taa = tc.get_all()
                for ta in taa:
                    ta["alias"] = self.alias + ta["alias"]
                    aa.append(ta)

    def add_children(self, *args):
        self.children.append(find_aliases(args))


def find_aliases(*args):
    o = []
    for obj in args:
        if hasattr(obj, "alias"):
            o.append(obj.alias)
    return tuple(o)


class Namespace:
    def __init__(self, namespace_file=None):
        path = Path(namespace_file)
        assert path.suffix == ".json", "file has no json extension"
        self._path = path
        self.name = path.stem
        self.data = None

    def read_file(self):
        with self._path.open("r") as fp:
            self.data = json.load(fp)
            self._modified = False

    @property
    def aliases(self):
        if not self.data:
            self.read_file()
        return [td["alias"] for td in self.data]

    @property
    def channels(self):
        if not self.data:
            self.read_file()
        return [td["channel"] for td in self.data]

    def update(self, alias, channel, channeltype):
        assert not alias in self.aliases, "Duplicate alias {alias} found!"
        assert not channel in self.channels, "Duplicate channel {channel} found!"
        self.data.append(
            {"alias": alias, "channel": channel, "channeltype": channeltype}
        )
        self._modified = True

    def store(self):
        if self._modified:
            with self._path.open("w") as fp:
                json.dump(self.data, fp)
                self._modified = False


class NamespaceCollection:
    def __init__(self, alias_path=None):
        if not alias_path:
            alias_path = os.path.abspath(__file__)
            self._namespace_path = Path(alias_path).parent / "namespaces"
        else:
            self._namespace_path = Path(alias_path)
        for nsf in self._namespace_path.glob("*.json"):
            # self.__dict__[nsf.stem] = property(lambda:Namespace(nsf))
            self.__dict__[nsf.stem] = Namespace(nsf)
