from epics import PV
from ..devices_general.adjustable import PvEnum
from time import sleep

class Hexapod_PI:
    def __init__(self, Id):
        self.Id = Id
        self.x, self.y, self.z = [
            ValueRdback(self.id + f":SET-POSI-{i}", self.id + f":POSI-{i}")
            for i in "XYZ"
        ]
        self.dx, self.dy, self.dz = [
            ValueRdback(self.id + f":SET-POSI-{i}", self.id + f":POSI-{i}")
            for i in "UVW"
        ]
        self._piv_x, self._piv_y, self._piv_z = [
            ValueRdback(self.id + f":SET-PIVOT-{i}", self.id + f":PIVOT-R-{i}")
            for i in "RST"
        ]


class HexapodSymmetrie:
    def __init__(self,pv_master='SARES20-HEXSYM',name='hex_usd',offset=[0,0,0,0,0,0]):
        self.name = name
        self.offset = offset
        self.pvname = pv_master
        self.coordinate_switch = PvEnum(f'{self.pvname}:MOVE#PARAM:CM',name='hex_usd_coordinate_switch')
        self.pvs_setpos = {
                'x':PV(f'{self.pvname}:MOVE#PARAM:X.VAL'),
                'y':PV(f'{self.pvname}:MOVE#PARAM:Y.VAL'),
                'z':PV(f'{self.pvname}:MOVE#PARAM:Z.VAL'),
                'rx':PV(f'{self.pvname}:MOVE#PARAM:RX.VAL'),
                'ry':PV(f'{self.pvname}:MOVE#PARAM:RY.VAL'),
                'rz':PV(f'{self.pvname}:MOVE#PARAM:RZ.VAL'),
                }
        self.pvs_getpos = {
                'x':PV(f'{self.pvname}:POSMACH:X'),
                'y':PV(f'{self.pvname}:POSMACH:Y'),
                'z':PV(f'{self.pvname}:POSMACH:Z'),
                'rx':PV(f'{self.pvname}:POSMACH:RX'),
                'ry':PV(f'{self.pvname}:POSMACH:RY'),
                'rz':PV(f'{self.pvname}:POSMACH:RZ'),
                }
        self._ctrl_pv = PV(f'{self.pvname}:STATE#PANEL:SET.VAL')

    def set_coordinates(self,x,y,z,rx,ry,rz):
        self.pvs_setpos['x'].put(x)
        self.pvs_setpos['y'].put(y)
        self.pvs_setpos['z'].put(z)
        self.pvs_setpos['rx'].put(rx)
        self.pvs_setpos['ry'].put(ry)
        self.pvs_setpos['rz'].put(rz)
    
    def get_coordinates(self):
        x = self.pvs_getpos['x'].get()
        y = self.pvs_getpos['y'].get()
        z = self.pvs_getpos['z'].get()
        rx = self.pvs_getpos['rx'].get()
        ry = self.pvs_getpos['ry'].get()
        rz = self.pvs_getpos['rz'].get()
        return x,y,z,rx,ry,rz

    def set_control_on(self):
        self._ctrl_pv.put(3)

    def set_control_off(self):
        self._ctrl_pv.put(4)

    def get_control_state(self):
        stat = self._ctrl_pv.get()
        if stat==3:
            return 'control on'
        elif stat==4:
            return 'control on'
        elif stat==2:
            return 'stopped'
        elif stat==11:
            return 'moving'

    def move_to_coordinates(self,x,y,z,rx,ry,rz,precision=[.001,.001,.001,.001,.001,.001],coordinate_type='absolute',relative_to_eco_offset=True):
        self.coordinate_switch.set_target_value(coordinate_type).wait()
        if relative_to_eco_offset:
            x = x+self.offset[0]
            y = y+self.offset[1]
            z = z+self.offset[2]
            rx = rx+self.offset[3]
            ry = ry+self.offset[4]
            rz = rz+self.offset[5]
        self.set_coordinates(x,y,z,rx,ry,rz)
        sleep(.1)
        self.start_move(target=(x,y,z,rx,ry,rz),precision=precision,coordinate_type=coordinate_type)

    def start_move(self,target=None,precision=[.001,.001,.001,.001,.001,.001],coordinate_type='absolute'):
        print('Starting to move... stop with Ctrl-C')
        self.set_control_on()
        sleep(0.2)
        self._ctrl_pv.put(11) #this starts moving!
        while 1:
            try:
                if target:
                    coo = self.get_coordinates()
                    if all([abs(ctarg-cnow)<cprec for ctarg,cnow,cprec in zip(target,coo,precision)]):
                        self.stop_move()
                        print('Target position reached')
                        break
                sleep(.01) 
            except KeyboardInterrupt:
                self.stop_move()
                print('Motion stopped')
                break
        self.set_control_off()
        sleep(0.05)

    def stop_move(self):
        self._ctrl_pv.put(2)

