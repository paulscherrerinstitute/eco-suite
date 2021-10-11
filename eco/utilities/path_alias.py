from pathlib import Path


class PathAlias:
    def __init__(self, linkpath="/sf/bernina/config/exp/"):
        self.linkpath = linkpath

    @property
    def _tp_list(self):
        po = []
        for tp in Path(self.linkpath).glob("*"):
            if tp.is_symlink():
                tt = tp.resolve()
                if [tp.name for tp in list(tt.parents)[:2]] == ["data", "sf_bernina"]:
                    po.append([tt.name, tp.name])
        return po

    @property
    def aliases(self):
        return {p: a for p, a in self._tp_list}

    @property
    def pgroups(self):
        return {a: p for p, a in self._tp_list}

    def get_alias(self, pgroup):
        return {p: a for p, a in self._tp_list}.get(pgroup)

    def get_pgroup(self, alias):
        return {a: p for p, a in self._tp_list}.get(alias)
