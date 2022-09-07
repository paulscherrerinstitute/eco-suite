from time import sleep
from ..devices_general.motors import MotorRecord
from ..elements.adjustable import AdjustableVirtual
from ..aliases import Alias, append_object_to_object
from functools import partial
from ..elements.assembly import Assembly


def addSlitRepr(Slitobj):
    def repr(self):
        s = f"pos ({self.hpos.get_current_value():6.3f},{self.vpos.get_current_value():6.3f}), gap ({self.hgap.get_current_value():6.3f},{self.vgap.get_current_value():6.3f})"
        return s

    Slitobj.__repr__ = repr
    return Slitobj


def addSlitCall(Slitobj):
    def call(self, *args):
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

    Slitobj.__call__ = call
    return Slitobj


@addSlitRepr
@addSlitCall
class JJSlitUnd(Assembly):
    def __init__(self, pvname="SARFE10-OAPU044", name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(
            MotorRecord,
            self.pvname + ":MOTOR_AX1",
            is_setting=True,
            is_display=False,
            name="_blade_ax1",
        )
        self._append(
            MotorRecord,
            self.pvname + ":MOTOR_AX2",
            is_setting=True,
            is_display=False,
            name="_blade_ax2",
        )
        self._append(
            MotorRecord,
            self.pvname + ":MOTOR_AY1",
            is_setting=True,
            is_display=False,
            name="_blade_ay1",
        )
        self._append(
            MotorRecord,
            self.pvname + ":MOTOR_AY2",
            is_setting=True,
            is_display=False,
            name="_blade_ay2",
        )
        self._append(
            MotorRecord,
            self.pvname + ":MOTOR_BX1",
            is_setting=True,
            is_display=False,
            name="_blade_bx1",
        )
        self._append(
            MotorRecord,
            self.pvname + ":MOTOR_BX2",
            is_setting=True,
            is_display=False,
            name="_blade_bx2",
        )
        self._append(
            MotorRecord,
            self.pvname + ":MOTOR_BY1",
            is_setting=True,
            is_display=False,
            name="_blade_by1",
        )
        self._append(
            MotorRecord,
            self.pvname + ":MOTOR_BY2",
            is_setting=True,
            is_display=False,
            name="_blade_by2",
        )
        self._append(
            AdjustableVirtual,
            [self._blade_ax1, self._blade_ax2],
            lambda x1, x2: (x1 + x2) / 2,
            lambda pos: [
                (pos - self._pos_ax()) + tb
                for tb in [self._blade_ax1(), self._blade_ax2()]
            ],
            is_setting=True,
            is_display=False,
            name="_pos_ax",
        )
        self._append(
            AdjustableVirtual,
            [self._blade_ay1, self._blade_ay2],
            lambda x1, x2: (x1 + x2) / 2,
            lambda pos: [
                (pos - self._pos_ay()) + tb
                for tb in [self._blade_ay1(), self._blade_ay2()]
            ],
            is_setting=True,
            is_display=False,
            name="_pos_ay",
        )
        self._append(
            AdjustableVirtual,
            [self._blade_bx1, self._blade_bx2],
            lambda x1, x2: (x1 + x2) / 2,
            lambda pos: [
                (pos - self._pos_bx()) + tb
                for tb in [self._blade_bx1(), self._blade_bx2()]
            ],
            is_setting=True,
            is_display=False,
            name="_pos_bx",
        )
        self._append(
            AdjustableVirtual,
            [self._blade_by1, self._blade_by2],
            lambda x1, x2: (x1 + x2) / 2,
            lambda pos: [
                (pos - self._pos_by()) + tb
                for tb in [self._blade_by1(), self._blade_by2()]
            ],
            is_setting=True,
            is_display=False,
            name="_pos_by",
        )

        self._append(
            AdjustableVirtual,
            [self._blade_ax1, self._blade_ax2],
            lambda x1, x2: (x2 - x1),
            lambda gap: [(sign * gap / 2 + self._pos_ax()) for sign in [-1, 1]],
            is_setting=True,
            is_display=False,
            name="_gap_ax",
        )
        self._append(
            AdjustableVirtual,
            [self._blade_ay1, self._blade_ay2],
            lambda x1, x2: (x2 - x1),
            lambda gap: [(sign * gap / 2 + self._pos_ay()) for sign in [-1, 1]],
            is_setting=True,
            is_display=False,
            name="_gap_ay",
        )
        self._append(
            AdjustableVirtual,
            [self._blade_bx1, self._blade_bx2],
            lambda x1, x2: (x2 - x1),
            lambda gap: [(sign * gap / 2 + self._pos_bx()) for sign in [-1, 1]],
            is_setting=True,
            is_display=False,
            name="_gap_bx",
        )
        self._append(
            AdjustableVirtual,
            [self._blade_by1, self._blade_by2],
            lambda x1, x2: (x2 - x1),
            lambda gap: [(sign * gap / 2 + self._pos_by()) for sign in [-1, 1]],
            is_setting=True,
            is_display=False,
            name="_gap_by",
        )
        self._append(
            AdjustableVirtual,
            [self._pos_ax, self._pos_bx],
            lambda a, b: a,
            lambda v: (v, v),
            is_setting=False,
            is_display=True,
            name="hpos",
        )
        self._append(
            AdjustableVirtual,
            [self._gap_ax, self._gap_bx],
            lambda a, b: a,
            lambda v: (v, v),
            is_setting=False,
            is_display=True,
            name="hgap",
        )
        self._append(
            AdjustableVirtual,
            [self._pos_ay, self._pos_by],
            lambda a, b: a,
            lambda v: (v, v),
            is_setting=False,
            is_display=True,
            name="vpos",
        )
        self._append(
            AdjustableVirtual,
            [self._gap_ay, self._gap_by],
            lambda a, b: a,
            lambda v: (v, v),
            is_setting=False,
            is_display=True,
            name="vgap",
        )

    def gui(self):
        self._run_cmd(
            'caqtdm -macro "NAME=OAPU044_JJXRAY,P=SARFE10-OAPU044" /sf/photo/config/qt/OAPU044.ui'
        )


@addSlitRepr
class SlitBlades(Assembly):
    def __init__(self, pvname, name=None, elog=None):
        super().__init__(name=name)
        self.Id = pvname
        self._append(MotorRecord, pvname + ":MOTOR_X1", name="right", is_setting=True)
        self._append(MotorRecord, pvname + ":MOTOR_X2", name="left", is_setting=True)
        self._append(MotorRecord, pvname + ":MOTOR_Y1", name="down", is_setting=True)
        self._append(MotorRecord, pvname + ":MOTOR_Y2", name="up", is_setting=True)
        self._append(MotorRecord, pvname + ":MOTOR_X", name="hpos_virt_mrec")
        self._append(MotorRecord, pvname + ":MOTOR_W", name="hgap_virt_mrec")
        self._append(MotorRecord, pvname + ":MOTOR_Y", name="vpos_virt_mrec")
        self._append(MotorRecord, pvname + ":MOTOR_H", name="vgap_virt_mrec")

        def getgap(xn, xp):
            return xp - xn

        def getpos(xn, xp):
            return (xn + xp) / 2

        def setwidth(x):
            return tuple([tx + self.hpos.get_current_value() for tx in [-x / 2, x / 2]])

        def setheight(x):
            return tuple([tx + self.vpos.get_current_value() for tx in [-x / 2, x / 2]])

        def sethpos(x):
            return tuple(
                [x + tx * self.hgap.get_current_value() for tx in [-1 / 2, +1 / 2]]
            )

        def setvpos(x):
            return tuple(
                [x + tx * self.vgap.get_current_value() for tx in [-1 / 2, 1 / 2]]
            )

        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.right, self.left],
            getgap,
            setwidth,
            reset_current_value_to=True,
            name="hgap",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.down, self.up],
            getgap,
            setheight,
            reset_current_value_to=True,
            name="vgap",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.right, self.left],
            getpos,
            sethpos,
            reset_current_value_to=True,
            name="hpos",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.down, self.up],
            getpos,
            setvpos,
            reset_current_value_to=True,
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


@addSlitRepr
class SlitPosWidth(Assembly):
    def __init__(self, pvname, name=None, elog=None):
        super().__init__(name=name)

        self.pvname = pvname

        self._append(MotorRecord, pvname + ":MOTOR_X", name="hpos")
        self._append(MotorRecord, pvname + ":MOTOR_Y", name="vpos")
        self._append(MotorRecord, pvname + ":MOTOR_W", name="hgap")
        self._append(MotorRecord, pvname + ":MOTOR_H", name="vgap")

        def getblade(pos, gap, direction=1):
            return pos + direction * gap / 2

        def setblade(bde, pos, gap, direction=1):
            delta = bde - getblade(pos, gap, direction=direction)
            ngap = gap + direction * delta
            npos = pos + direction * delta / 2
            return npos, ngap

        def getpos(xn, xp):
            return (xn + xp) / 2

        def setwidth(x):
            return tuple([tx + self.hpos.get_current_value() for tx in [-x / 2, x / 2]])

        def setheight(x):
            return tuple([tx + self.vpos.get_current_value() for tx in [-x / 2, x / 2]])

        def sethpos(x):
            return tuple(
                [x + tx * self.hgap.get_current_value() for tx in [-1 / 2, +1 / 2]]
            )

        def setvpos(x):
            return tuple(
                [x + tx * self.vgap.get_current_value() for tx in [-1 / 2, 1 / 2]]
            )

        self._append(
            AdjustableVirtual,
            [self.vpos, self.vgap],
            partial(getblade, direction=1),
            partial(setblade, direction=1),
            reset_current_value_to=True,
            name="up",
        )
        self._append(
            AdjustableVirtual,
            [self.vpos, self.vgap],
            partial(getblade, direction=-1),
            partial(setblade, direction=-1),
            reset_current_value_to=True,
            name="down",
        )
        self._append(
            AdjustableVirtual,
            [self.hpos, self.hgap],
            partial(getblade, direction=1),
            partial(setblade, direction=1),
            reset_current_value_to=True,
            name="left",
        )
        self._append(
            AdjustableVirtual,
            [self.hpos, self.hgap],
            partial(getblade, direction=-1),
            partial(setblade, direction=-1),
            reset_current_value_to=True,
            name="right",
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


@addSlitRepr
class SlitBlades_JJ:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.Id = pvname
        self.alias = Alias(name)
        append_object_to_object(self, MotorRecord, pvname + ":MOT_1", name="right")
        append_object_to_object(self, MotorRecord, pvname + ":MOT_2", name="left")
        append_object_to_object(self, MotorRecord, pvname + ":MOT_4", name="down")
        append_object_to_object(self, MotorRecord, pvname + ":MOT_3", name="up")

        def getgap(xn, xp):
            return xp - xn

        def getpos(xn, xp):
            return (xn + xp) / 2

        def setwidth(x):
            return tuple([tx + self.hpos.get_current_value() for tx in [-x / 2, x / 2]])

        def setheight(x):
            return tuple([tx + self.vpos.get_current_value() for tx in [-x / 2, x / 2]])

        def sethpos(x):
            return tuple([tx + self.hgap.get_current_value() for tx in [-x / 2, x / 2]])

        def setvpos(x):
            return tuple([tx + self.vgap.get_current_value() for tx in [-x / 2, x / 2]])

        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.right, self.left],
            getgap,
            setwidth,
            reset_current_value_to=True,
            name="hgap",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.down, self.up],
            getgap,
            setheight,
            reset_current_value_to=True,
            name="vgap",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.right, self.left],
            getpos,
            sethpos,
            reset_current_value_to=True,
            name="hpos",
        )
        append_object_to_object(
            self,
            AdjustableVirtual,
            [self.down, self.up],
            getpos,
            setvpos,
            reset_current_value_to=True,
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


class SlitBlades_old:
    def __init__(self, Id, name=None, elog=None):
        self.Id = Id
        self.name = name
        self._x1 = MotorRecord(Id + ":MOTOR_X1")
        self._x2 = MotorRecord(Id + ":MOTOR_X2")
        self._y1 = MotorRecord(Id + ":MOTOR_Y1")
        self._y2 = MotorRecord(Id + ":MOTOR_Y2")

    def get_hg(self):
        return self._x2.get_current_value() - self._x1.get_current_value()

    def get_vg(self):
        return self._y2.get_current_value() - self._y1.get_current_value()

    def get_ho(self):
        return (self._x1.get_current_value() + self._x2.get_current_value()) / 2

    def get_vo(self):
        return (self._y1.get_current_value() + self._y2.get_current_value()) / 2

    def set_hg(self, value):
        ho = self.get_ho()
        c1 = self._x1.set_target_value(ho - value / 2)
        c2 = self._x2.set_target_value(ho + value / 2)
        return c1, c2

    def set_vg(self, value):
        vo = self.get_vo()
        c1 = self._y1.set_target_value(vo - value / 2)
        c2 = self._y2.set_target_value(vo + value / 2)
        return c1, c2

    def set_ho(self, value):
        hg = self.get_hg()
        c1 = self._x1.set_target_value(value - hg / 2)
        c2 = self._x2.set_target_value(value + hg / 2)
        return c1, c2

    def set_vo(self, value):
        vg = self.get_vg()
        c1 = self._y1.set_target_value(value - vg / 2)
        c2 = self._y2.set_target_value(value + vg / 2)
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
        return -(self._x2.get_current_value() - self._x1.get_current_value())

    def get_vg(self):
        return -(self._y2.get_current_value() - self._y1.get_current_value())

    def get_ho(self):
        return (self._x1.get_current_value() + self._x2.get_current_value()) / 2

    def get_vo(self):
        return (self._y1.get_current_value() + self._y2.get_current_value()) / 2

    def set_hg(self, value):
        ho = self.get_ho()
        c1 = self._x1.set_target_value(ho + value / 2)
        c2 = self._x2.set_target_value(ho - value / 2)
        return c1, c2

    def set_vg(self, value):
        vo = self.get_vo()
        c1 = self._y1.set_target_value(vo + value / 2)
        c2 = self._y2.set_target_value(vo - value / 2)
        return c1, c2

    def set_ho(self, value):
        hg = self.get_hg()
        c1 = self._x1.set_target_value(-(-value - hg / 2))
        c2 = self._x2.set_target_value(-(-value + hg / 2))
        return c1, c2

    def set_vo(self, value):
        vg = self.get_vg()
        c1 = self._y1.set_target_value(value + vg / 2)
        c2 = self._y2.set_target_value(value - vg / 2)
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
        return self._ax2.get_current_value() - self._ax1.get_current_value()

    def get_vg(self):
        return self._ay2.get_current_value() - self._ay1.get_current_value()

    def get_ho(self):
        return (self._ax1.get_current_value() + self._ax2.get_current_value()) / 2

    def get_vo(self):
        return (self._ay1.get_current_value() + self._ay2.get_current_value()) / 2

    def set_hg(self, value):
        ho = self.get_ho()
        c1 = self._ax1.set_target_value(ho - value / 2)
        c2 = self._ax2.set_target_value(ho + value / 2)
        c3 = self._bx1.set_target_value(ho - value / 2)
        c4 = self._bx2.set_target_value(ho + value / 2)
        return c1, c2, c3, c4

    def set_vg(self, value):
        vo = self.get_vo()
        c1 = self._ay1.set_target_value(vo - value / 2)
        c2 = self._ay2.set_target_value(vo + value / 2)
        c3 = self._by1.set_target_value(vo - value / 2)
        c4 = self._by2.set_target_value(vo + value / 2)
        return c1, c2, c3, c4

    def set_ho(self, value):
        hg = self.get_hg()
        c1 = self._ax1.set_target_value(value - hg / 2)
        c2 = self._ax2.set_target_value(value + hg / 2)
        c3 = self._bx1.set_target_value(value - hg / 2)
        c4 = self._bx2.set_target_value(value + hg / 2)
        return c1, c2, c3, c4

    def set_vo(self, value):
        vg = self.get_vg()
        c1 = self._ay1.set_target_value(value - vg / 2)
        c2 = self._ay2.set_target_value(value + vg / 2)
        c3 = self._by1.set_target_value(value - vg / 2)
        c4 = self._by2.set_target_value(value + vg / 2)
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
        return self._xgap.get_current_value()

    def get_vg(self):
        return self._ygap.get_current_value()

    def get_ho(self):
        return self._xoffs.get_current_value()

    def get_vo(self):
        return self._yoffs.get_current_value()

    def set_hg(self, value):
        c = self._xgap.set_target_value(value)
        return c

    def set_vg(self, value):
        c = self._ygap.set_target_value(value)
        return c

    def set_ho(self, value):
        c = self._xoffs.set_target_value(value)
        return c

    def set_vo(self, value):
        c = self._yoffs.set_target_value(value)
        return c

    def __call__(self, width, height):
        self.set_hg(width)
        self.set_vg(height)

    def __repr__(self):
        string1 = "gap: (%g,%g) mm" % (self.get_hg(), self.get_vg())
        string2 = "pos: (%g,%g) mm" % (self.get_ho(), self.get_vo())
        return "\n".join((string1, string2))


class SlitBladeStages:
    def __init__(
        self,
        up=None,
        down=None,
        left=None,
        right=None,
    ):
        self.up = up
        self.down = down
        self.left = left
        self.right = right

    def getwidth(self):
        return self.left.get_current_value() - self.right.get_current_value()

    def getheight(self):
        return self.up.get_current_value() - self.down.get_current_value()

    def gethpos(self):
        return (self.left.get_current_value() + self.right.get_current_value()) / 2

    def getvpos(self):
        return (self.up.get_current_value() + self.down.get_current_value()) / 2

    def setwidth(self, value):
        pos = self.gethpos()
        self.left.set_target_value(pos + value / 2)
        self.right.set_target_value(pos - value / 2)

    def setheight(self, value):
        pos = self.getvpos()
        self.up.set_target_value(pos + value / 2)
        self.down.set_target_value(pos - value / 2)

    def sethpos(self, value):
        gap = self.getwidth()
        self.left.set_target_value(value + gap / 2)
        self.right.set_target_value(value - gap / 2)

    def setvpos(self, value):
        gap = self.getheigth()
        self.up.set_target_value(value + gap / 2)
        self.down.set_target_value(value - gap / 2)


@addSlitRepr
class SlitBladesGeneral(Assembly):
    def __init__(
        self,
        def_blade_up={"args": [], "kwargs": {}},
        def_blade_down={"args": [], "kwargs": {}},
        def_blade_left={"args": [], "kwargs": {}},
        def_blade_right={"args": [], "kwargs": {}},
        name=None,
        elog=None,
    ):
        super().__init__(name=name)
        self._append(
            *def_blade_up["args"],
            **def_blade_up["kwargs"],
            name="up",
            is_setting=True,
            is_display=False,
        )
        self._append(
            *def_blade_down["args"],
            **def_blade_down["kwargs"],
            name="down",
            is_setting=True,
            is_display=False,
        )
        self._append(
            *def_blade_left["args"],
            **def_blade_left["kwargs"],
            name="left",
            is_setting=True,
            is_display=False,
        )
        self._append(
            *def_blade_right["args"],
            **def_blade_right["kwargs"],
            name="right",
            is_setting=True,
            is_display=False,
        )
        self.blade_motors = [self.up, self.down, self.left, self.right]

        def getgap(xn, xp):
            return xp - xn

        def getpos(xn, xp):
            return (xn + xp) / 2

        def setwidth(x):
            return tuple([tx + self.hpos.get_current_value() for tx in [-x / 2, x / 2]])

        def setheight(x):
            return tuple([tx + self.vpos.get_current_value() for tx in [-x / 2, x / 2]])

        def sethpos(x):
            return tuple([x + tx for tx in [-self.hgap.get_current_value()/2, self.hgap.get_current_value()/2]])

        def setvpos(x):
            return tuple([x + tx for tx in [-self.vgap.get_current_value()/2, self.vgap.get_current_value()/2]])

        self._append(
            AdjustableVirtual,
            [self.right, self.left],
            getgap,
            setwidth,
            reset_current_value_to=True,
            name="hgap",
        )
        self._append(
            AdjustableVirtual,
            [self.down, self.up],
            getgap,
            setheight,
            reset_current_value_to=True,
            name="vgap",
        )
        self._append(
            AdjustableVirtual,
            [self.right, self.left],
            getpos,
            sethpos,
            reset_current_value_to=True,
            name="hpos",
        )
        self._append(
            AdjustableVirtual,
            [self.down, self.up],
            getpos,
            setvpos,
            reset_current_value_to=True,
            name="vpos",
        )

    def _apply_on_all_blades(self, method_name, *args, **kwargs):
        out = []
        for blade in self.blade_motors:
            if method_name in blade.__dict__.keys():
                out.append(blade.__dict__[method_name](*args, **kwargs))
            else:
                out.append(blade.__getattribute__(method_name)(*args, **kwargs))
        return out

    def home_all_blades(self):
        self._apply_on_all_blades("home")

    def init_all_blades(self):
        self._apply_on_all_blades("stage_type", 1)
        sleep(0.5)
        self._apply_on_all_blades("sensor_type", 0)
        sleep(0.5)
        self._apply_on_all_blades("calibrate_sensor", 1)
        sleep(3)
        self._apply_on_all_blades("home_forward", 1)
        homed = 0
        while not homed:
            homed = all(self._apply_on_all_blades("is_homed"))
            sleep(0.1)

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
