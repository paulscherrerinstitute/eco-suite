"""Generic "hold to move" jog for any eco Adjustable.

If the adjustable exposes a native jog() (currently MotorRecord, via its
JOGF/JOGR motor-record fields - see eco.devices_general.motors.jog), that
is used directly for true continuous hardware motion driven by the IOC
itself. Otherwise this falls back to a software jog: repeatedly nudging
set_target_value() by step_size while held, using the same held/
accelerating action mechanism as tweak_action.py.

This is what lets a single manual-control box drive motors, virtual
adjustables, PV-backed knobs, etc. all through the same joystick, without
each device class needing to implement anything special.
"""

from .tweak_action import _HeldAction


class Jogger:
    def __init__(self, step_size=1.0, interval=0.12, growth=0.85, min_interval=0.03):
        self.step_size = step_size
        self.interval = interval
        self.growth = growth
        self.min_interval = min_interval
        self._software_action = None
        self._adjustable = None

    @staticmethod
    def _has_native_jog(adjustable):
        return callable(getattr(adjustable, "jog", None))

    def start(self, adjustable, direction):
        """Begin jogging `adjustable` in `direction` (+1 or -1). No-op if
        already jogging something (call stop() first)."""
        if self._adjustable is not None:
            return
        direction = 1 if direction > 0 else -1

        if self._has_native_jog(adjustable):
            adjustable.jog(direction, start=True)
        else:

            def _step():
                adjustable.set_target_value(
                    adjustable.get_current_value() + direction * self.step_size
                )

            self._software_action = _HeldAction(
                _step, self.interval, self.growth, self.min_interval
            )
            self._software_action.start()

        self._adjustable = adjustable

    def stop(self):
        if self._adjustable is None:
            return
        if self._has_native_jog(self._adjustable):
            if callable(getattr(self._adjustable, "jog_stop", None)):
                self._adjustable.jog_stop()
            else:
                self._adjustable.jog(1, start=False)
                self._adjustable.jog(-1, start=False)
        elif self._software_action is not None:
            self._software_action.stop()
            self._software_action = None
        self._adjustable = None

    @property
    def is_jogging(self):
        return self._adjustable is not None
