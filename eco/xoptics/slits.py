from ..devices_general.motors import MotorRecord
from ..devices_general.adjustable import AdjustableVirtual
from ..aliases import Alias,append_object_to_object

class SlitBlades:
    def __init__(self, pvname, name=None, elog=None):
        self.name = name
        self.Id = pvname
        self.alias = Alias(name)
        append_object_to_object(self,MotorRecord,pvname + ":MOTOR_X1",name='right')
        append_object_to_object(self,MotorRecord,pvname + ":MOTOR_X2",name='left')
        append_object_to_object(self,MotorRecord,pvname + ":MOTOR_Y1",name='down')
        append_object_to_object(self,MotorRecord,pvname + ":MOTOR_Y2",name='up')
    
        def getgap(xn,xp): return xp-xn
        def getpos(xn,xp): return (xn+xp)/2
        def setwidth(x):
            return tuple([tx + self.hpos.get_current_value() for tx in [-x/2,x/2]])
        def setheight(x):
            return tuple([tx + self.vpos.get_current_value() for tx in [-x/2,x/2]])
        def sethpos(x):
            return tuple([tx + self.hgap.get_current_value() for tx in [-x/2,x/2]])
        def setvpos(x):
            return tuple([tx + self.vgap.get_current_value() for tw in [-x/2,x/2]])

        append_object_to_object(self,AdjustableVirtual,[self.right,self.left],getgap,setwidth,set_current_value=True,name='hgap')
        append_object_to_object(self,AdjustableVirtual,[self.down,self.up],getgap,setheight,set_current_value=True,name='vgap')
        append_object_to_object(self,AdjustableVirtual,[self.right,self.left],getpos,sethpos,set_current_value=True,name='hpos')
        append_object_to_object(self,AdjustableVirtual,[self.down,self.up],getpos,setvpos,set_current_value=True,name='vpos')

    def  __call__(self,*args):
        if len(args)==0:
            return self.hpos.get_current_value(),self.vpos.get_current_value(),self.hgap.get_current_value(),self.vgap.get_current_value()
        elif len(args)==1:
            self.hgap.changeTo(args[0])
            self.vgap.changeTo(args[0])
        elif len(args)==2:
            self.hgap.changeTo(args[0])
            self.vgap.changeTo(args[1])
        elif len(args)==4:
            self.hpos.changeTo(args[0])
            self.vpos.changeTo(args[1])
            self.hgap.changeTo(args[2])
            self.vgap.changeTo(args[3])
        else:
            raise Exception('wrong number of input arguments!')




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
        c1 = self._x1.changeTo(ho - value / 2)
        c2 = self._x2.changeTo(ho + value / 2)
        return c1, c2

    def set_vg(self, value):
        vo = self.get_vo()
        c1 = self._y1.changeTo(vo - value / 2)
        c2 = self._y2.changeTo(vo + value / 2)
        return c1, c2

    def set_ho(self, value):
        hg = self.get_hg()
        c1 = self._x1.changeTo(value - hg / 2)
        c2 = self._x2.changeTo(value + hg / 2)
        return c1, c2

    def set_vo(self, value):
        vg = self.get_vg()
        c1 = self._y1.changeTo(value - vg / 2)
        c2 = self._y2.changeTo(value + vg / 2)
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
        c1 = self._x1.changeTo(ho + value / 2)
        c2 = self._x2.changeTo(ho - value / 2)
        return c1, c2

    def set_vg(self, value):
        vo = self.get_vo()
        c1 = self._y1.changeTo(vo + value / 2)
        c2 = self._y2.changeTo(vo - value / 2)
        return c1, c2

    def set_ho(self, value):
        hg = self.get_hg()
        c1 = self._x1.changeTo(-(-value - hg / 2))
        c2 = self._x2.changeTo(-(-value + hg / 2))
        return c1, c2

    def set_vo(self, value):
        vg = self.get_vg()
        c1 = self._y1.changeTo(value + vg / 2)
        c2 = self._y2.changeTo(value - vg / 2)
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
        c1 = self._ax1.changeTo(ho - value / 2)
        c2 = self._ax2.changeTo(ho + value / 2)
        c3 = self._bx1.changeTo(ho - value / 2)
        c4 = self._bx2.changeTo(ho + value / 2)
        return c1, c2, c3, c4

    def set_vg(self, value):
        vo = self.get_vo()
        c1 = self._ay1.changeTo(vo - value / 2)
        c2 = self._ay2.changeTo(vo + value / 2)
        c3 = self._by1.changeTo(vo - value / 2)
        c4 = self._by2.changeTo(vo + value / 2)
        return c1, c2, c3, c4

    def set_ho(self, value):
        hg = self.get_hg()
        c1 = self._ax1.changeTo(value - hg / 2)
        c2 = self._ax2.changeTo(value + hg / 2)
        c3 = self._bx1.changeTo(value - hg / 2)
        c4 = self._bx2.changeTo(value + hg / 2)
        return c1, c2, c3, c4

    def set_vo(self, value):
        vg = self.get_vg()
        c1 = self._ay1.changeTo(value - vg / 2)
        c2 = self._ay2.changeTo(value + vg / 2)
        c3 = self._by1.changeTo(value - vg / 2)
        c4 = self._by2.changeTo(value + vg / 2)
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
        c = self._xgap.changeTo(value)
        return c

    def set_vg(self, value):
        c = self._ygap.changeTo(value)
        return c

    def set_ho(self, value):
        c = self._xoffs.changeTo(value)
        return c

    def set_vo(self, value):
        c = self._yoffs.changeTo(value)
        return c

    def __call__(self, width, height):
        self.set_hg(width)
        self.set_vg(height)

    def __repr__(self):
        string1 = "gap: (%g,%g) mm" % (self.get_hg(), self.get_vg())
        string2 = "pos: (%g,%g) mm" % (self.get_ho(), self.get_vo())
        return "\n".join((string1, string2))
