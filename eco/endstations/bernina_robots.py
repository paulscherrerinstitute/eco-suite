from eco.pshell.client import PShellClient
from eco.elements.assembly import Assembly
from eco.elements.adjustable import AdjustableVirtual, AdjustableGetSet, value_property
from eco.devices_general.motors import PshellMotor
from eco.elements.detector import DetectorGet
from eco.elements.adj_obj import AdjustableObject, DetectorObject
from eco.devices_general.utilities import Changer
import time 
import numpy as np

class RobotError(Exception):
    pass

class StaeubliTx200(Assembly):
    def __init__(
        self,
        name=None,
        pshell_url=None,
    ):
        """Robot arm at SwissFEL Bernina.\
        """
        super().__init__(name=name)
        self.pc = PShellClient(pshell_url)
        self.pc.start_sse_event_loop_task(None, self._on_event)
        self._cache = {}
        self._info_fields={}
        self._config_fields={}
        self._do_update()
        self._get_on_poll_info()
        self._append(DetectorGet, self._get_info, cache_get_seconds =None, name='_info', is_setting=False, is_display=False)
        self._append(DetectorObject, self._info, name='info', is_display='recursive', is_setting=False)
        self._append(AdjustableGetSet, 
                     self._get_config, 
                     self._set_config, 
                     cache_get_seconds =None, 
                     precision=0, 
                     check_interval=None, 
                     name='_config', 
                     is_setting=False, 
                     is_display=False)
        self._append(AdjustableObject, self._config, name='config',is_setting=True, is_display='recursive')

        # appending pshell motors
        motors = [
            ["x", "x", "mm"], 
            ["y", "y", "mm"], 
            ["z", "z", "mm"], 
            ["rx", "rx", "deg"],
            ["ry", "ry", "deg"], 
            ["rz", "rz", "deg"], 
            ["gamma", "gamma", "deg"], 
            ["delta", "delta", "deg"],
            ["t_det", "r", "mm"], 
            ["j1", "j1", "deg"], 
            ["j2", "j2", "deg"], 
            ["j3", "j3", "deg"], 
            ["j4", "j4", "deg"], 
            ["j5", "j5", "deg"], 
            ["j6", "j6", "deg"],
            ]
        for [name, name_pshell, unit] in motors:
            self._append(PshellMotor, robot=self, name=name, name_pshell=name_pshell, unit=unit, is_setting=True, is_display=True)

    def _get_info(self):
        return {k: v for k, v in self._cache.items() if k in self._info_fields}
    
    def _set_config(self, fields):
        changed_item = [[k, v] for k, v in fields.items() if v != self._cache[k]]
        print(changed_item)
        if len(changed_item) > 1:
            raise RobotError("Changing multiple fields at once")
        k, v = changed_item[0]
        method = self._config_fields[k]
        if type(v) != str:
            v = str(v)
        else:
            v = "'" + v + "'"
        cmd = method["cmd"]+"(" + v + "," + method["def_kwargs"] + ")"
        print(cmd)
        return self._set_eval_cmd(cmd)

    def _get_config(self):
        return {k: v for k, v in self._cache.items() if k in self._config_fields.keys()}
   
    def gui(self):
        cmd = ["caqtdm","/sf/bernina/config/src/caqtdm/robot/robot.ui"]
        return self._run_cmd(" ".join(cmd))
    ######## Motion simulation ##########

    def simulate_sphercial_motion(self, t_det=None, gamma=None, delta=None, coordinates="joint"):
        """        
        Simulated motion in the spherical coordinate system using a linear moteion movel command to         
        change the radius from point tcp_p_spherical[0] to tcp_p_spherical[1], followed        
        by a circular motion along the circle given by the start point tcp_p_spherical[1],         
        the intermediate point tcp_p_spherical[2] and the target tcp_p_spherical[3].        
        
        frame: string, defaults to self.config.frame
        The frame defines the cartesian coordinate system and origin. Must exist on the controller. 
        
        sync: True or False, defaults to False  
        If true, no further commands are accepted until the motion is finished.                
        
        simulate: True or False        
        coordinates: "joint" or "cartesian"        
        
        If simulate = True, an array of interpolated positions in either joint or cartesian        
        coordinates is returned. Setting coordinates only has an effect, when the motion is simulated.        
        """
        sim = self._get_eval_result(f"robot.move_spherical(r={t_det}, gamma={gamma}, delta={delta}, simulate=True, coordinates='{coordinates}')")
        return np.array(sim)
    
    def simulate_cartesian_motion(self, x=None, y=None, z=None, rx=None, ry=None, rz=None, coordinates="joint"):
        """        
        Simulated motion in the cartesian coordinate system using a linear motion movel command to
        move from point tcp_p_spherical[0] to tcp_p_spherical[1].        
        
        frame: string        
        The frame defines the cartesian coordinate system and origin. Must exist on the controller.                
        
        sync: True or False        
        If true, no further commands are accepted until the motion is finished.                
        
        simulate: True or False        
        coordinates: "joint" or "cartesian"        
        
        If simulate = True, an array of interpolated positions in either joint or cartesian        
        coordinates is returned. Setting coordinates only has an effect, when the motion is simulated.        
        """
        sim = self._get_eval_result(f"robot.move_cartesian(x={x}, y={y}, z={z}, rx={rx}, ry={ry}, rz={rz}, simulate=True, coordinates='{coordinates}')")
        return np.array(sim)

    ######## Helper functions ##########
    def _get_on_poll_info(self):
        cfg = self._get_eval_result("robot.on_poll_info()")
        self._info_fields = cfg["info"]
        self._config_fields = cfg["config"]

    def _do_update(self):
        self._get_eval_result("robot.doUpdate()")

    def _on_event(self, name, value):
        if name == "polling":
            self._cache = value
        else:
            self._check_disconnect_event(name, value)

    def _as_bool(self, s):
        return True if s=='true' else False if s=='false' else None
    
    def _get_eval_result(self, cmd, update_value_time=0.05, timeout=120):
        cid = self.pc.start_eval(f"{cmd}&")
        t_start = time.time()
        while(True):
            time.sleep(update_value_time)
            res = self.pc.get_result(cid)
            if res["status"] == "failed":
                raise RobotError(res["exception"])
                break
            elif res["status"] == "completed":
                val = res["return"]
                break
            elif (time.time() - t_start) > timeout:
                raise RobotError(
                    f"evaluation timeout reached"
                )     
        return val

    def _set_eval_cmd(self, cmd, hold=False, update_value_time=0.05, timeout=120):
        return Changer(
            target=cmd,
            changer=lambda cmd: self._get_eval_result(cmd, update_value_time, timeout),
            hold=hold,
        )

    def _check_disconnect_event(self, name, value):
        if name == "shell":
            if type(value) != str:
                pass
            if "Update error" in value:
                for k, v in self._cache.items():
                    if type(v) == dict:
                        for k2 in self._cache[k].keys():
                            self._cache[k][k2]=None
                    else:
                        self._cache[k]=None
                    self._cache["connected"]=False
                raise RobotError(value)
