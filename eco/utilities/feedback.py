from simple_pid import PID
from ..epics.detector import DetectorPvDataStream
from ..elements.assembly import Assembly
from threading import Thread
from time import sleep
from ..elements.adjustable import AdjustableFS

class Feedback_Timetool(Assembly):
    def __init__(self, name=None, pvname=None, control_adj = None, pid=[1, 0.01, 0], output_limits=(-100,100), setpoint=1060, calib_s_per_px=3e-15):
        super().__init__(name=name)
        self._append(DetectorPvDataStream, pvname, name="monitor")
        self.pid = PID(
            *pid,
            setpoint=0,
            output_limits=(output_limits[0]*abs(calib_s_per_px), output_limits[1]*abs(calib_s_per_px)),
            sample_time=10,
        )
        self.control_adj = control_adj
        self._append(
            AdjustableFS,
            "/photonics/home/gac-bernina/eco/configuration/tt_feedback_setpoint",
            default_value=setpoint,
            name="setpoint",
        )
        self._append(
            AdjustableFS,
            "/photonics/home/gac-bernina/eco/configuration/tt_feedback_calib_s_per_px",
            default_value=calib_s_per_px,
            name="calib_s_per_px",
        )
        self._running=False
    def stop(self):
        self._running = False
    def run_continuously(self):
        while(self._running):
            rb_val = self.monitor.get_current_value()
            set_val = self.pid(rb_val-self.setpoint())*self.calib_s_per_px()
            print(f"moving phase control adjustable by {set_val}")
            self.control_adj.mvr(set_val)
            sleep(60)


    def start_feedback(self):
        self._running=True
        self.feedback = Thread(target = self.run_continuously)




