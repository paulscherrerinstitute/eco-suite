from ..devices_general.motors import MotorRecord
from ..devices_general.adjustable import AdjustableVirtual
from ..aliases import Alias, append_object_to_object
from functools import partial


def addSlitRepr(Slitobj):
    def repr(self):
        s = f"pos ({self.hpos.get_value():6.3f},{self.vpos.get_value():6.3f}), gap ({self.hgap.get_value():6.3f},{self.vgap.get_value():6.3f})"
        return s
    Slitobj.__repr__ =  repr
    return Slitobj

@addSlitRepr
class SlitBlades:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.Id = pvname
        self.alias = Alias(name)
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_X1", name="right")
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_X2", name="left")
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_Y1", name="down")
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_Y2", name="up")
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_X", name="hpos_virt_mrec")
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_W", name="hgap_virt_mrec")
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_Y", name="vpos_virt_mrec")
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_H", name="vgap_virt_mrec")

        def getgap(xn, xp):
            return xp - xn

        def getpos(xn, xp):
            return (xn + xp) / 2

        def setwidth(x):
            return tuple([tx + self.hpos.get_value() for tx in [-x / 2, x / 2]])

        def setheight(x):
            return tuple([tx + self.vpos.get_value() for tx in [-x / 2, x / 2]])

        def sethpos(x):
            return tuple([tx + self.hgap.get_value() for tx in [-x / 2, x / 2]])

        def setvpos(x):
            return tuple([tx + self.vgap.get_value() for tx in [-x / 2, x / 2]])

        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.right, self.left],
            getgap,
            setwidth,
            set_current_value=True,
            name="hgap",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.down, self.up],
            getgap,
            setheight,
            set_current_value=True,
            name="vgap",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.right, self.left],
            getpos,
            sethpos,
            set_current_value=True,
            name="hpos",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.down, self.up],
            getpos,
            setvpos,
            set_current_value=True,
            name="vpos",
        )

    def __call__(self, *args):
        if len(args) == 0:
            return (
                self.hpos.get_value(),
                self.vpos.get_value(),
                self.hgap.get_value(),
                self.vgap.get_value(),
            )
        elif len(args) == 1:
            self.hgap.set_target(args[0])
            self.vgap.set_target(args[0])
        elif len(args) == 2:
            self.hgap.set_target(args[0])
            self.vgap.set_target(args[1])
        elif len(args) == 4:
            self.hpos.set_target(args[0])
            self.vpos.set_target(args[1])
            self.hgap.set_target(args[2])
            self.vgap.set_target(args[3])
        else:
            raise Exception("wrong number of input arguments!")

@addSlitRepr
class SlitPosWidth:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.Id = pvname
        self.alias = Alias(name)
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_X", name="hpos")
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_Y", name="vpos")
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_W", name="hgap")
        append_object_to_object(self, MotorRecord, pvname + ":MOTOR_H", name="vgap")

        def getblade(pos,gap,direction=1):
            return pos + direction*gap/2

        def setblade(bde,pos,gap,direction=1):
            delta = bde-getblade(pos,gap,direction=direction)
            ngap = gap + direction*delta
            npos = pos + direction*delta/2
            return npos,ngap

        def getpos(xn, xp):
            return (xn + xp) / 2

        def setwidth(x):
            return tuple([tx + self.hpos.get_value() for tx in [-x / 2, x / 2]])

        def setheight(x):
            return tuple([tx + self.vpos.get_value() for tx in [-x / 2, x / 2]])

        def sethpos(x):
            return tuple([tx + self.hgap.get_value() for tx in [-x / 2, x / 2]])

        def setvpos(x):
            return tuple([tx + self.vgap.get_value() for tw in [-x / 2, x / 2]])

        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.vpos, self.vgap],
            partial(getblade,direction=1),
            partial(setblade,direction=1),
            set_current_value=True,
            name="up"
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.vpos, self.vgap],
            partial(getblade,direction=-1),
            partial(setblade,direction=-1),
            set_current_value=True,
            name="down"
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.hpos, self.hgap],
            partial(getblade,direction=1),
            partial(setblade,direction=1),
            set_current_value=True,
            name="left"
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.hpos, self.hgap],
            partial(getblade,direction=-1),
            partial(setblade,direction=-1),
            set_current_value=True,
            name="right"
        )

    def __call__(self, *args):
        if len(args) == 0:
            return (
                self.hpos.get_value(),
                self.vpos.get_value(),
                self.hgap.get_value(),
                self.vgap.get_value(),
            )
        elif len(args) == 1:
            self.hgap.set_target(args[0])
            self.vgap.set_target(args[0])
        elif len(args) == 2:
            self.hgap.set_target(args[0])
            self.vgap.set_target(args[1])
        elif len(args) == 4:
            self.hpos.set_target(args[0])
            self.vpos.set_target(args[1])
            self.hgap.set_target(args[2])
            self.vgap.set_target(args[3])
        else:
            raise Exception("wrong number of input arguments!")

class SlitBlades_old:
    def __init__(self, Id, name=None, elog=None):
        self.Id = Id
        self.name = name
        self._x1 = MotorRecord(Id + ":MOTOR_X1")
        self._x2 = MotorRecord(Id + ":MOTOR_X2")
        self._y1 = MotorRecord(Id + ":MOTOR_Y1")
        self._y2 = MotorRecord(Id + ":MOTOR_Y2")

    def get_hg(self):
        return self._x2.get_value() - self._x1.get_value()

    def get_vg(self):
        return self._y2.get_value() - self._y1.get_value()

    def get_ho(self):
        return (self._x1.get_value() + self._x2.get_value()) / 2

    def get_vo(self):
        return (self._y1.get_value() + self._y2.get_value()) / 2

    def set_hg(self, value):
        ho = self.get_ho()
        c1 = self._x1.set_target(ho - value / 2)
        c2 = self._x2.set_target(ho + value / 2)
        return c1, c2

    def set_vg(self, value):
        vo = self.get_vo()
        c1 = self._y1.set_target(vo - value / 2)
        c2 = self._y2.set_target(vo + value / 2)
        return c1, c2

    def set_ho(self, value):
        hg = self.get_hg()
        c1 = self._x1.set_target(value - hg / 2)
        c2 = self._x2.set_target(value + hg / 2)
        return c1, c2

    def set_vo(self, value):
        vg = self.get_vg()
        c1 = self._y1.set_target(value - vg / 2)
        c2 = self._y2.set_target(value + vg / 2)
        return c1, c2

    def __call__(self, width, height):
        self.set_hg(width)
        self.set_vg(height)

    def __repr__(self):
        string1 = "gap: (%g,%g) mm" % (self.get_hg(), self.get_vg())
        string2 = "pos: (%g,%g) mm" % (self.get_ho(), self.get_vo())
        return "\n".join((string1, string2))


class SlitBladesJJ_old:
    def __init__(self, Id, name=None, elog=None):
        self.Id = Id
        self.name = name
        self._x1 = MotorRecord(Id + ":MOT2")
        self._x2 = MotorRecord(Id + ":MOT3")
        self._y1 = MotorRecord(Id + ":MOT4")
        self._y2 = MotorRecord(Id + ":MOT5")

    def get_hg(self):
        return -(self._x2.get_value() - self._x1.get_value())

    def get_vg(self):
        return -(self._y2.get_value() - self._y1.get_value())

    def get_ho(self):
        return (self._x1.get_value() + self._x2.get_value()) / 2

    def get_vo(self):
        return (self._y1.get_value() + self._y2.get_value()) / 2

    def set_hg(self, value):
        ho = self.get_ho()
        c1 = self._x1.set_target(ho + value / 2)
        c2 = self._x2.set_target(ho - value / 2)
        return c1, c2

    def set_vg(self, value):
        vo = self.get_vo()
        c1 = self._y1.set_target(vo + value / 2)
        c2 = self._y2.set_target(vo - value / 2)
        return c1, c2

    def set_ho(self, value):
        hg = self.get_hg()
        c1 = self._x1.set_target(-(-value - hg / 2))
        c2 = self._x2.set_target(-(-value + hg / 2))
        return c1, c2

    def set_vo(self, value):
        vg = self.get_vg()
        c1 = self._y1.set_target(value + vg / 2)
        c2 = self._y2.set_target(value - vg / 2)
        return c1, c2

    def __call__(self, width, height):
        self.set_hg(width)
        self.set_vg(height)

    def __repr__(self):
        string1 = "gap: (%g,%g) mm" % (self.get_hg(), self.get_vg())
        string2 = "pos: (%g,%g) mm" % (self.get_ho(), self.get_vo())
        return "\n".join((string1, string2))


class SlitFourBlades_old:
    def __init__(self, Id, name=None, elog=None):
        self.Id = Id
        self.name = name
        self._ax1 = MotorRecord(Id + ":MOTOR_AX1")
        self._ax2 = MotorRecord(Id + ":MOTOR_AX2")
        self._ay1 = MotorRecord(Id + ":MOTOR_AY1")
        self._ay2 = MotorRecord(Id + ":MOTOR_AY2")
        self._bx1 = MotorRecord(Id + ":MOTOR_BX1")
        self._bx2 = MotorRecord(Id + ":MOTOR_BX2")
        self._by1 = MotorRecord(Id + ":MOTOR_BY1")
        self._by2 = MotorRecord(Id + ":MOTOR_BY2")

    def get_hg(self):
        return self._ax2.get_value() - self._ax1.get_value()

    def get_vg(self):
        return self._ay2.get_value() - self._ay1.get_value()

    def get_ho(self):
        return (self._ax1.get_value() + self._ax2.get_value()) / 2

    def get_vo(self):
        return (self._ay1.get_value() + self._ay2.get_value()) / 2

    def set_hg(self, value):
        ho = self.get_ho()
        c1 = self._ax1.set_target(ho - value / 2)
        c2 = self._ax2.set_target(ho + value / 2)
        c3 = self._bx1.set_target(ho - value / 2)
        c4 = self._bx2.set_target(ho + value / 2)
        return c1, c2, c3, c4

    def set_vg(self, value):
        vo = self.get_vo()
        c1 = self._ay1.set_target(vo - value / 2)
        c2 = self._ay2.set_target(vo + value / 2)
        c3 = self._by1.set_target(vo - value / 2)
        c4 = self._by2.set_target(vo + value / 2)
        return c1, c2, c3, c4

    def set_ho(self, value):
        hg = self.get_hg()
        c1 = self._ax1.set_target(value - hg / 2)
        c2 = self._ax2.set_target(value + hg / 2)
        c3 = self._bx1.set_target(value - hg / 2)
        c4 = self._bx2.set_target(value + hg / 2)
        return c1, c2, c3, c4

    def set_vo(self, value):
        vg = self.get_vg()
        c1 = self._ay1.set_target(value - vg / 2)
        c2 = self._ay2.set_target(value + vg / 2)
        c3 = self._by1.set_target(value - vg / 2)
        c4 = self._by2.set_target(value + vg / 2)
        return c1, c2, c3, c4

    def __call__(self, width, height):
        self.set_hg(width)
        self.set_vg(height)

    def __str__(self):
        string1 = "gap: (%g,%g) mm" % (self.get_hg(), self.get_vg())
        string2 = "pos: (%g,%g) mm" % (self.get_ho(), self.get_vo())
        return "\n".join((string1, string2))

    def __repr__(self):
        return self.__str__()


class SlitPosWidth_old:
    def __init__(self, Id, name=None, elog=None):
        self.Id = Id
        self.name = name
        self._xoffs = MotorRecord(Id + ":MOTOR_X")
        self._yoffs = MotorRecord(Id + ":MOTOR_Y")
        self._xgap = MotorRecord(Id + ":MOTOR_W")
        self._ygap = MotorRecord(Id + ":MOTOR_H")

    def get_hg(self):
        return self._xgap.get_value()

    def get_vg(self):
        return self._ygap.get_value()

    def get_ho(self):
        return self._xoffs.get_value()

    def get_vo(self):
        return self._yoffs.get_value()

    def set_hg(self, value):
        c = self._xgap.set_target(value)
        return c

    def set_vg(self, value):
        c = self._ygap.set_target(value)
        return c

    def set_ho(self, value):
        c = self._xoffs.set_target(value)
        return c

    def set_vo(self, value):
        c = self._yoffs.set_target(value)
        return c

    def __call__(self, width, height):
        self.set_hg(width)
        self.set_vg(height)

    def __repr__(self):
        string1 = "gap: (%g,%g) mm" % (self.get_hg(), self.get_vg())
        string2 = "pos: (%g,%g) mm" % (self.get_ho(), self.get_vo())
        return "\n".join((string1, string2))
