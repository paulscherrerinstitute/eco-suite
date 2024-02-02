from eco.utilities.feedback import Feedback
from eco import Assembly
from eco.elements.adjustable import AdjustableFS
import numpy as np
import time


class AramisDcmFeedback(Assembly):
    def __init__(
        self,
        mono,
        xbpm,
        mon_samples_path="/sf/bernina/config/eco/configuration/mono_feedback_samples.json",
        mon_samples_default=400,
        setpoint_ver_path="/sf/bernina/config/eco/configuration/mono_feedback_setpoint_ver.json",
        setpoint_ver_default=0,
        pid_ver_path="/sf/bernina/config/eco/configuration/mono_feedback_pid_ver.json",
        pid_ver_default=[0, -0.1, 0],
        output_limits_ver_path="/sf/bernina/config/eco/configuration/mono_feedback_output_limits_ver.json",
        output_limits_ver_default=[45, 55],
        running_ver_path="/sf/bernina/config/eco/configuration/mono_feedback_running_ver.json",
        sample_time_ver_path="/sf/bernina/config/eco/configuration/mono_feedback_sample_time_ver.json",
        setpoint_hor_path="/sf/bernina/config/eco/configuration/mono_feedback_setpoint_hor.json",
        setpoint_hor_default=0,
        pid_hor_path="/sf/bernina/config/eco/configuration/mono_feedback_pid_hor.json",
        pid_hor_default=[0, -0.1, 0],
        output_limits_hor_path="/sf/bernina/config/eco/configuration/mono_feedback_output_limits_hor.json",
        output_limits_hor_default=[45, 55],
        running_hor_path="/sf/bernina/config/eco/configuration/mono_feedback_running_hor.json",
        sample_time_hor_path="/sf/bernina/config/eco/configuration/mono_feedback_sample_time_hor.json",
        name="mono_feedback",
    ):
        super().__init__(name=name)
        self.mono = mono
        self.xbpm = xbpm
        self._append(
            AdjustableFS,
            mon_samples_path,
            default_value=mon_samples_default,
            name="mon_samples",
        )

        self._append(
            Feedback,
            name="hor",
            foo_detector=self.get_xpos,
            setpoint=setpoint_hor_path,
            pid=pid_hor_path,
            control_adj=self.mono.roll1_piezo,
            output_limits=output_limits_hor_path,
            running_path=None,
            sample_time=sample_time_hor_path,
            callback_start_feedback=lambda: self.mono.roll1.parked.set_target_value(
                1
            ).wait(),
            callback_stop_feedback=None,
            callback_start_control=None,
            callback_stop_control=lambda: time.sleep(1),
            is_display="recursive",
            is_setting=True,
        )

        self._append(
            Feedback,
            name="ver",
            foo_detector=self.get_ypos,
            setpoint=setpoint_ver_path,
            pid=pid_ver_path,
            control_adj=self.mono.pitch2_piezo,
            output_limits=output_limits_ver_path,
            running_path=None,
            sample_time=sample_time_ver_path,
            callback_start_feedback=lambda: self.mono.pitch2.parked.set_target_value(
                1
            ).wait(),
            callback_stop_feedback=None,
            callback_start_control=None,
            callback_stop_control=lambda: time.sleep(1),
            is_display="recursive",
            is_setting=True,
        )

    def get_ypos(self):
        i, x, y = self.get_monitor_values()
        if 0.8 < np.mean((0.2 < i) & (i < 10)):
            return np.median(y)
        else:
            return self.get_ypos()

    def get_xpos(self):
        i, x, y = self.get_monitor_values()
        if 0.8 < np.mean((0.2 < i) & (i < 10)):
            return np.median(x)
        else:
            return self.get_xpos()

    def get_monitor_values(self):
        acquisitions = [
            td.acquire(samples=self.mon_samples.get_current_value())
            for td in [
                self.xbpm.intensity,
                self.xbpm.xpos,
                self.xbpm.ypos,
            ]
        ]
        output = [np.asarray(tac.wait()) for tac in acquisitions]
        return output
