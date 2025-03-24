from eco.elements.adjustable import AdjustableVirtual, AdjustableFS
from scipy.spatial.transform import Rotation
from eco import Assembly


class CartCooRotated(Assembly):
    def __init__(self,
                 x_adj=AdjustableFS('./delme_x',default_value=0,name='x'),
                 y_adj=AdjustableFS('./delme_y',default_value=0,name='y'),
                 z_adj=AdjustableFS('./delme_z',default_value=0,name='z'),
                 euler_seq = 'xyz',
                 euler_angles_deg = [0,0,0],
                 file_rotation='./delme_rotation',
                 names_rotated_axes = ['xp','yp','zp'],
                 change_simultaneously = True,
                 reset_current_value_to = False,
                 check_limits= False,
                 append_aliases=False,
                 name=None):
        
        super().__init__(name=name)
        self._append(AdjustableFS,
                     file_rotation, 
                     default_value={'euler_sequence':euler_seq, 'euler_angles_deg':euler_angles_deg}, 
                     name='rotdef')
        # self._x = x_adj
        # self._y = y_adj
        # self._z = z_adj
        self._append(x_adj,name='_x',is_setting=True,is_display=False)
        self._append(y_adj,name='_y',is_setting=True,is_display=False)
        self._append(z_adj,name='_z',is_setting=True,is_display=False)


        self._append(AdjustableVirtual,
                     [self._x,self._y,self._z],
                     lambda x,y,z: self.get_rotated_coo(x,y,z)[0],
                     lambda xp: self.get_base_coo(xp=xp),
                     change_simultaneously=change_simultaneously,
                     reset_current_value_to=reset_current_value_to,
                     check_limits=check_limits,
                     append_aliases=append_aliases,
                     is_status=True,
                     is_setting=False,
                     is_display=True,
                     name=names_rotated_axes[0])
        self._append(AdjustableVirtual,
                     [self._x,self._y,self._z],
                     lambda x,y,z: self.get_rotated_coo(x,y,z)[1],
                     lambda yp: self.get_base_coo(yp=yp),
                     change_simultaneously=change_simultaneously,
                     reset_current_value_to=reset_current_value_to,
                     check_limits=check_limits,
                     append_aliases=append_aliases,
                     is_status=True,
                     is_setting=False,
                     is_display=True,                     
                     name=names_rotated_axes[1])
        self._append(AdjustableVirtual,
                     [self._x,self._y,self._z],
                     lambda x,y,z: self.get_rotated_coo(x,y,z)[2],
                     lambda zp: self.get_base_coo(zp=zp),
                     change_simultaneously=change_simultaneously,
                     reset_current_value_to=reset_current_value_to,
                     check_limits=check_limits,
                     append_aliases=append_aliases,
                     is_status=True,
                     is_setting=False,
                     is_display=True,
                     name=names_rotated_axes[2])
        self._adjs_rotated_axes = [self.__dict__[tname] for tname in names_rotated_axes]


    @property
    def rotation(self):
        euler = self.rotdef.get_current_value()
        return Rotation.from_euler(euler['euler_sequence'],euler['euler_angles_deg'],degrees=True)

    def get_rotated_coo(self,x=None,y=None,z=None):
        if x is None:
            x = self._x.get_current_value()
        if y is None:
            y = self._y.get_current_value()
        if z is None:
            z = self._z.get_current_value()
        return tuple(self.rotation.inv().apply([x,y,z]))

    def get_base_coo(self,xp=None,yp=None,zp=None):
        if xp is None:
            xp = self._adjs_rotated_axes[0].get_current_value()
        if yp is None:
            yp = self._adjs_rotated_axes[1].get_current_value()
        if zp is None:
            zp = self._adjs_rotated_axes[2].get_current_value()
        
        return tuple(self.rotation.apply([xp,yp,zp]))
    
    
    

        
