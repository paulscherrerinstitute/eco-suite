from eco.pshell.client import PShellClient
from eco.elements.assembly import Assembly
from eco.elements.adjustable import AdjustableVirtual, AdjustableGetSet, value_property
from eco.devices_general.motors import PshellMotor
from eco.elements.detector import DetectorGet
from eco.elements.adj_obj import AdjustableObject, DetectorObject
from eco.devices_general.utilities import Changer

import time 

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
        self.pc.start_sse_event_loop_task(None, self.on_event)
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
        motors = ["x", "y", "z", "rx", "ry", "rz", "gamma", "delta"]
        for motor in motors:
            self._append(PshellMotor, robot=self, name=motor, name_pshell=motor, is_setting=True, is_display=True)
        self._append(PshellMotor, robot=self, name="t_det", name_pshell="r", is_setting=True, is_display=True)
    
    def _get_info(self):
        return {k: v for k, v in self._cache.items() if k in self._info_fields}
    
    def _set_config(self, fields):
        changed_item = [[k, v] for k, v in fields.items() if v != self._cache[k]]
        print(changed_item)
        if len(changed_item) > 1:
            raise Exception("Changing multiple fields at once")
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
    
    def _get_on_poll_info(self):
        cfg = self._get_eval_result("robot.on_poll_info()")
        self._info_fields = cfg["info"]
        self._config_fields = cfg["config"]

    def _do_update(self):
        self._get_eval_result("robot.doUpdate()")

    def on_event(self, name, value):
        if name == "polling":
            self._cache = value

    ######## Helper functions ##########
    def _as_bool(self, s):
        return True if s=='true' else False if s=='false' else None
    
    def _get_eval_result(self, cmd, update_value_time=0.05, timeout=120):
        cid = self.pc.start_eval(f"{cmd}&")
        t_start = time.time()
        while(True):
            time.sleep(update_value_time)
            res = self.pc.get_result(cid)
            if res["status"] == "failed":
                raise Exception(res["exception"])
                break
            elif res["status"] == "completed":
                val = res["return"]
                break
            elif (time.time() - t_start) > timeout:
                raise Exception(
                    f"evaluation timeout reached"
                )     
        return val

    def _set_eval_cmd(self, cmd, hold=False, update_value_time=0.05, timeout=120):
        return Changer(
            target=cmd,
            changer=lambda cmd: self._get_eval_result(cmd, update_value_time, timeout),
            hold=hold,
        )


