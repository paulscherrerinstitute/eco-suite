from ..devices_general.adjustable import AdjustableVirtual
from ..aliases import Alias, append_object_to_object
from functools import partial
from ..devices_general.smaract import SmarActRecord


def addSlitRepr(Slitobj):
    def repr(self):
        s = f"pos ({self.hpos.get_current_value():6.3f},{self.vpos.get_current_value():6.3f}), gap ({self.hgap.get_current_value():6.3f},{self.vgap.get_current_value():6.3f})"
        # s = f"{self.hgap.get_current_value()}"
        return s

    Slitobj.__repr__ = repr
    return Slitobj


@addSlitRepr
class Upstream_diagnostic_slits:
    def __init__(
        self, pvname, name=None, right=None, left=None, up=None, down=None, elog=None
    ):
        self.name = name
        self.Id = pvname
        self.alias = Alias(name)
        self.right = right
        self.left = left
        self.down = down
        self.up = up
        append_object_to_object(self, SmarActRecord, pvname + right, name="right")
        append_object_to_object(self, SmarActRecord, pvname + left, name="left")
        append_object_to_object(self, SmarActRecord, pvname + down, name="down")
        append_object_to_object(self, SmarActRecord, pvname + up, name="up")

        def getgap(xn, xp):
            return xp - xn

        def getpos(xn, xp):
            return (xn + xp) / 2

        def setwidth(x):
            return tuple([tx + self.hpos.get_current_value() for tx in [-x / 2, x / 2]])

        def setheight(x):
            return tuple([tx + self.vpos.get_current_value() for tx in [-x / 2, x / 2]])

        def sethpos(x):
            hpos0 = self.hpos.get_current_value()
            diff_hpos = x - hpos0
            return tuple(
                [
                    self.right.get_current_value() + diff_hpos,
                    self.left.get_current_value() + diff_hpos,
                ]
            )

        def setvpos(x):
            vpos0 = self.vpos.get_current_value()
            diff_vpos = x - vpos0
            return tuple(
                [
                    self.up.get_current_value() + diff_vpos,
                    self.down.get_current_value() + diff_vpos,
                ]
            )

        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.left, self.right],
            getgap,
            setwidth,
            reset_current_value_to=False,
            name="hgap",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.up, self.down],
            getgap,
            setheight,
            reset_current_value_to=False,
            name="vgap",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.right, self.left],
            getpos,
            sethpos,
            reset_current_value_to=False,
            name="hpos",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.up, self.down],
            getpos,
            setvpos,
            reset_current_value_to=False,
            name="vpos",
        )

    def __call__(self, *args):

        if len(args) == 0:
            return (
                self.hpos.get_current_value(),
                self.vpos.get_current_value(),
                self.hgap.get_current_value(),
                self.vgap.get_current_value(),
            )
        elif len(args) == 1:
            self.hgap.set_target_value(args[0])
            self.vgap.set_target_value(args[0])
        elif len(args) == 2:
            self.hgap.set_target_value(args[0])
            self.vgap.set_target_value(args[1])
        elif len(args) == 4:
            self.hpos.set_target_value(args[0])
            self.vpos.set_target_value(args[1])
            self.hgap.set_target_value(args[2])
            self.vgap.set_target_value(args[3])
        else:
            raise Exception("wrong number of input arguments!")
