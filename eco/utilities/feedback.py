from pathlib import Path
from simple_pid import PID

from eco.elements.adjustable import AdjustableMemory
from ..epics.detector import DetectorPvDataStream
from ..elements.assembly import Assembly
from threading import Thread
from time import sleep
from ..elements.adjustable import AdjustableFS


class Feedback_Timetool(Assembly):
    def __init__(
        self,
        name=None,
        pvname=None,
        control_adj=None,
        pid=[1, 0.01, 0],
        output_limits=(-100, 100),
        setpoint=1060,
        calib_s_per_px=3e-15,
    ):
        super().__init__(name=name)
        self._append(DetectorPvDataStream, pvname, name="monitor")
        self.pid = PID(
            *pid,
            setpoint=0,
            output_limits=(
                output_limits[0] * abs(calib_s_per_px),
                output_limits[1] * abs(calib_s_per_px),
            ),
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
        self._running = False

    def stop(self):
        self._running = False

    def run_continuously(self):
        while self._running:
            rb_val = self.monitor.get_current_value()
            set_val = self.pid(rb_val - self.setpoint()) * self.calib_s_per_px()
            print(f"moving phase control adjustable by {set_val}")
            self.control_adj.mvr(set_val)
            sleep(60)

    def start_feedback(self):
        self._running = True
        self.feedback = Thread(target=self.run_continuously)


class Feedback(Assembly):
    def __init__(
        self,
        name=None,
        foo_detector=None,
        setpoint=None,
        pid=None,
        control_adj=None,
        output_limits=None,
        running_path=None,
        sample_time=None,
        callback_start_control=None,
        callback_stop_control=None,
    ):
        super().__init__(name=name)
        self.foo_detector = foo_detector
        self.control_adj = control_adj
        self.callback_start_control = callback_start_control
        self.callback_stop_control = callback_stop_control

        if type(setpoint) is str or isinstance(setpoint, Path):
            self._append(
                AdjustableFS,
                setpoint,
                name="setpoint",
            )
        else:
            self._append(
                AdjustableMemory,
                setpoint,
                name="setpoint",
            )

        if type(pid) is str or isinstance(pid, Path):
            self._append(
                AdjustableFS,
                pid,
                name="pid",
            )
        else:
            self._append(
                AdjustableMemory,
                pid,
                name="pid",
            )
        if type(sample_time) is str or isinstance(sample_time, Path):
            self._append(
                AdjustableFS,
                sample_time,
                name="sample_time",
            )
        else:
            self._append(
                AdjustableMemory,
                sample_time,
                name="sample_time",
            )

        if type(output_limits) is str or isinstance(output_limits, Path):
            self._append(
                AdjustableFS,
                output_limits,
                name="output_limits",
            )
        else:
            self._append(
                AdjustableMemory,
                output_limits,
                name="output_limits",
            )

        if running_path:
            self._append(AdjustableFS, running_path, "running")
        else:
            self._append(AdjustableMemory, False, running_path, "running")

    def create_new_pid(self, start_control_output=None):
        if start_control_output is None:
            start_control_output = self.control_adj.get_current_value()
        self.pid = PID(
            *self.pid.get_current_value(),
            setpoint=self.setpoint.get_current_value(),
            output_limits=self.output_limits.get_current_value(),
            starting_output=start_control_output,
            sample_time=self.sample_time.get_current_value(),
        )

    def stop(self):
        self.running.set_target_value(False)

    def run_continuously(self):
        while self.running.get_current_value():
            valcurr = self.foo_detector()
            set_val = self.pid(valcurr)
            if self.callback_start_control:
                self.callback_start_control()
            self.control_adj.set_target_value(set_val).wait()
            if self.callback_stop_control:
                self.callback_stop_control()

    def start_feedback(self):
        self.running.set_target_value(True).wait()
        self.feedback = Thread(target=self.run_continuously)


class FeedbackContextManager(object):
    def __init__(self, pid, method):
        self.pid = pid

    def __enter__(self):
        return self.pid

    def __exit__(self, type, value, traceback):
        self.pid.running.set_taret_value(False).wait()


# vs = []
# mono.roll1.parked(1)
# mono.pitch2.parked(1)
# px = PID(Kp=0, Ki=1, Kd=0, setpoint=0, output_limits=(30,70), starting_output=mono.roll1_piezo.get_current_value())
# py = PID(Kp=0, Ki=.3, Kd=0, setpoint=0, output_limits=(30,70), starting_output=mono.pitch2_piezo.get_current_value())
# vx,vy,vi = [mean(tts.wait()) for tts in  [ts.acquire(samples=100) for ts in [mon_opt_new.xpos,mon_opt_new.ypos,mon_opt_new.intensity]]]
# Ngood = 0
# while True:
#     if vi>0.8:
#         Ngood+=1
#     else:
#         Ngood = 0
#     if Ngood > 1:
#         cx = px(vx)
#         cy = py(vy)
#         mono.roll1_piezo(cx)
#         mono.pitch2_piezo(cy)
#         res = dict(time=time.time(),cx=cx,vx=vx,cy=cy,vy=vy)
#         print(px.components, py.components)
#         vs.append(res)
#     time.sleep(1)
#     vx,vy,vi = [mean(tts.wait()) for tts in  [ts.acquire(samples=100) for ts in [mon_opt_new.xpos,mon_opt_new.ypos,mon_opt_new.intensity]]]
