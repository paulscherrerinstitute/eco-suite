from eco.pshell.client import PShellClient
from eco.elements.assembly import Assembly
from eco.elements.adjustable import AdjustableFS, AdjustableGetSet, value_property
from eco.devices_general.motors import PshellMotor
from eco.elements.detector import DetectorGet
from eco.elements.adj_obj import AdjustableObject, DetectorObject
from eco.devices_general.utilities import Changer
from threading import Thread
import time 
import numpy as np
import os
os.sys.path.insert(0, "/sf/bernina/config/src/python/bernina_urdf/")

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
            ["z_lin", "z_lin", "mm", 1],
            ["x", "x", "mm", 0], 
            ["y", "y", "mm", 0], 
            ["z", "z", "mm", 0], 
            ["rx", "rx", "deg", 0],
            ["ry", "ry", "deg", 0], 
            ["rz", "rz", "deg", 0], 
            ["gamma", "gamma", "deg", 0], 
            ["delta", "delta", "deg", 0],
            ["t_det", "r", "mm", 0], 
            ["j1", "j1", "deg", 1], 
            ["j2", "j2", "deg", 1], 
            ["j3", "j3", "deg", 1], 
            ["j4", "j4", "deg", 1], 
            ["j5", "j5", "deg", 1], 
            ["j6", "j6", "deg", 1],
            ]
        for [name, name_pshell, unit, setting] in motors:
            self._append(PshellMotor, robot=self, name=name, name_pshell=name_pshell, unit=unit, is_setting=setting, is_display=True)
        self._urdf = None
        try:
            import bernina_urdf
            self._urdf = bernina_urdf.models.Tx200_Ceiling()
            self._append(AdjustableFS, f'/sf/bernina/config/eco/reference_values/robot_auto_update_simulation.json', default_value=True, name="auto_update_simulation", is_setting=False)
            self._auto_update_simulation_thread = Thread(target=self._auto_updater_simulation)
            self._auto_update_simulation_thread.start()
        except:
            print("Loading bernina URDF robot model failed")

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

    def reset_motion(self):
        self._get_eval_result("robot.reset_motion()")

    def stop(self):
        try:
            self.z_lin.stop()
        except:
            print("Failed to stop linear axis")
        self._get_eval_result("robot.stop()")
        self.reset_motion()
        self._get_eval_result("robot.resume()")


    ######## Motion simulation ##########
    def simulate(self, **kwargs):
        """
        This method involves communication with the robot controller to interpolate
        the cartesian, spherical or joint motion depending on the passed keywords.
        By default with no specific keyword arguments, the stored commands on the robot 
        controller are simulated.

        If any of "x", "y", "z", "rx", "ry", "rz" are in the keyword arguments: 
            simulate cratesian motion using the helper function _simulate_cartesian_motion --> see docstring for details

        Else if any of "r", "gamma", "delta" are in the keyword arguments:
            simulate spherical motion using the helper function _simulate_spherical_motion --> see docstring for details

       Else if any of "j1" to "j6" are in the keyword arguments:
           simulate joint motion using the helper function _simulate_joint_motion --> see docstring for details

        coordinates = "joint":
            the coordinates in which the interpolated values are returned. 
            Options are "joint" (default) or "spherical" / "cartesian"

        plot = True:
            if plot = True, the interpolated motion will be shown in a browser window
            if plot = False, an array of the interpolated positions will be returned
        """
        if np.any([s in kwargs.keys() for s in ["x", "y", "z", "rx", "ry", "rz"]]):
            return self._simulate_cartesian_motion(**kwargs)
        elif np.any([s in kwargs.keys() for s in ["r", "gamma", "delta"]]):
            return self._simulate_spherical_motion(**kwargs)
        elif np.any([s in kwargs.keys() for s in ["j1", "j2", "j3", "j4", "j5", "j6"]]):
            return self._simulate_joint_motion(**kwargs)
        else:
            return self._simulate_stored_commands()

    def _simulate_stored_commands(self, plot=True):
        """        
        Simulated stored commands on the controller.
        """
        sim = np.array(self._get_eval_result(f"robot.simulate_stored_commands()"))
        lin = np.array([self.z_lin()]*len(sim))
        sim = np.vstack([lin,sim.T]).T
        if plot:
            if self._urdf is not None:
                self.auto_update_simulation(False)
                self._urdf.sim.move_trajectory(sim)
                res = ""
                while not res in ["y", "n"]:
                    res = input("Resume real time visualization of bernina robot in the hutch (y/n)?: ")
                if res == "y":
                    self.auto_update_simulation(True)
        else:
            return sim

    def _simulate_sphercial_motion(self, t_det=None, gamma=None, delta=None, coordinates="joint", plot=True):
        """        
        Simulated motion in the spherical coordinate system using a linear moteion movel command to         
        change the radius from point tcp_p_spherical[0] to tcp_p_spherical[1], followed        
        by a circular motion along the circle given by the start point tcp_p_spherical[1],         
        the intermediate point tcp_p_spherical[2] and the target tcp_p_spherical[3].        
        
        simulate: True or False        
        coordinates: "joint" or "cartesian"        
        
        If simulate = True, an array of interpolated positions in either joint or cartesian        
        coordinates is returned. Setting coordinates only has an effect, when the motion is simulated.        
        """
        sim = np.array(self._get_eval_result(f"robot.move_spherical(r={t_det}, gamma={gamma}, delta={delta}, simulate=True, coordinates='{coordinates}')"))
        lin = np.array([self.z_lin()]*len(sim))
        sim = np.vstack([lin,sim.T]).T
        if plot:
            if self._urdf is not None:
                self.auto_update_simulation(False)
                self._urdf.sim.move_trajectory(sim)
                res = ""
                while not res in ["y", "n"]:
                    res = input("Resume real time visualization of bernina robot in the hutch (y/n)?: ")
                if res == "y":
                    self.auto_update_simulation(True)
        else:
            return sim


    def _simulate_joint_motion(self, j1=None, j2=None, j3=None, j4=None, j5=None, j6=None, plot=True):
        """
        Simulated motion in the joint coordinate system.
        """
        sim = np.array(self._get_eval_result(f"robot.move_joint(j1={j1}, j2={j2}, j3={j3}, j4={j4}, j5={j5}, j6={j6}, simulate=True"))
        lin = np.array([self.z_lin()]*11)
        sim = np.vstack([lin,sim.T]).T
        if plot:
            if self._urdf is not None:
                self.auto_update_simulation(False)
                self._urdf.sim.move_trajectory(sim)
                res = ""
                while not res in ["y", "n"]:
                    res = input("Resume real time visualization of bernina robot in the hutch (y/n)?: ")
                if res == "y":
                    self.auto_update_simulation(True)
        else:
            return sim



    def _simulate_cartesian_motion(self, x=None, y=None, z=None, rx=None, ry=None, rz=None, coordinates="joint", plot=True):
        """        
        Simulated motion in the cartesian coordinate system using a linear motion movel command to
        move from point tcp_p_spherical[0] to tcp_p_spherical[1].        
        
        coordinates: "joint" or "cartesian"        
        
        An array of interpolated positions in either joint or cartesian        
        coordinates is returned. Setting coordinates only has an effect, when the motion is simulated.        
        """
        sim = np.array(self._get_eval_result(f"robot.move_cartesian(x={x}, y={y}, z={z}, rx={rx}, ry={ry}, rz={rz}, simulate=True, coordinates='{coordinates}')"))
        lin = np.array([self.z_lin()]*11)
        sim = np.vstack([lin,sim.T]).T
        if plot:
            if self._urdf is not None:
                self.auto_update_simulation(False)
                self._urdf.sim.move_trajectory(sim)
                res = ""
                while not res in ["y", "n"]:
                    res = input("Resume real time visualization of bernina robot in the hutch (y/n)?: ")
                if res == "y":
                    self.auto_update_simulation(True)
        else:
            return sim
    
    def simulate_current_pos(self):
        js = np.array([self._cache["pos"][k] for k in ["z_lin", "j1", "j2", "j3", "j4", "j5", "j6"]])
        if np.any([j is None for j in js]):
            raise RobotError("Some of the joint positions are None, check if the connection between server and robot is lost")
        self._urdf.sim.pos = js
        self._urdf.sim._ensure_vis_running()
        self._urdf.sim.vis.step(0)

    def show(self):
        self._urdf.sim.show()

    ######## Helper functions ##########
    def _auto_updater_simulation(self):#
        while(True):
            js = np.array([self._cache["pos"][k] for k in ["z_lin", "j1", "j2", "j3", "j4", "j5", "j6"]])
            if np.any([j is None for j in js]):
                time.sleep(1)
                continue
            if self.auto_update_simulation():
                if not np.all(js.round(3) == self._urdf.sim.pos.round(3)):
                    self._urdf.sim.pos = js
                    if self._urdf.sim._vis_running():
                        self._urdf.sim.vis.step(0)
            time.sleep(.05)

    def _get_on_poll_info(self):
        cfg = self._get_eval_result("robot.on_poll_info()")
        self._info_fields = cfg["info"]
        self._config_fields = cfg["config"]

    def _do_update(self):
        self._get_eval_result("robot.doUpdate()")

    def _on_event(self, name, value):
        if name == "polling":
            self._cache = value
        elif name == "reset_motion":
            print(value)
        elif name == "stop":
            print(value)
        elif name == "motion":
            print(value)
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
