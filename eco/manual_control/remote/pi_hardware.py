"""Physical input backend for the handheld pendant: reads the rotary
encoder (GPIO quadrature + push) and the analog thumbstick (ADS1115 ADC
over I2C) and drives the same RemoteControlClient input methods that the
on-screen widgets call. Touch and physical controls therefore stay in
sync.

Degrades gracefully: the Pi-only libraries (gpiozero, adafruit ADS1x15)
are imported lazily inside setup(). If they are missing - i.e. you are not
on the Pi - HardwareInput just reports itself unavailable and the GUI
keeps working via touch/keyboard. So the same pi_app runs on a laptop for
testing and on the real device unchanged.

Default wiring (BCM numbering; all on pins the 4" SPI display leaves free -
VERIFY against your display's overlay before soldering):
    encoder A / B / push   : GPIO 5 / 6 / 13
    thumbstick push button : GPIO 19
    thumbstick X / Y       : ADS1115 A0 / A1  (I2C, shared bus w/ PiSugar)
"""

import threading
import time

DEFAULT_CONFIG = {
    "enc_a": 5,
    "enc_b": 6,
    "enc_sw": 13,
    "joy_sw": 19,
    "joy_x_chan": 0,
    "joy_y_chan": 1,
    "hold_time": 0.6,  # encoder long-press -> disarm
    "deadzone": 0.28,  # fraction of half-range before an axis acts
    "poll_hz": 30,
    "invert_y": False,
    "invert_x": False,
}


class HardwareInput:
    def __init__(self, client, config=None, on_activity=None):
        self.client = client
        self.cfg = dict(DEFAULT_CONFIG, **(config or {}))
        self.on_activity = on_activity or (lambda: None)
        self.available = False
        self._stop = threading.Event()
        self._jog_dir = 0  # current joystick-driven jog direction
        self._nav_latch = 0  # current joystick-x navigate latch
        try:
            self._setup()
            self.available = True
            print("hardware input active (encoder + analog joystick)")
        except Exception as exc:
            print(f"hardware input unavailable, GUI-only mode: {exc}")

    def _setup(self):
        from gpiozero import Button, RotaryEncoder  # Pi only

        enc = RotaryEncoder(self.cfg["enc_a"], self.cfg["enc_b"], max_steps=0)
        enc.when_rotated_clockwise = lambda: self._rotate(1)
        enc.when_rotated_counter_clockwise = lambda: self._rotate(-1)

        sw = Button(self.cfg["enc_sw"], hold_time=self.cfg["hold_time"])
        # short press fires on release only if it was not a hold
        sw.when_released = self._on_encoder_release
        sw.when_held = self._on_encoder_hold
        self._held = False

        jbtn = Button(self.cfg["joy_sw"])
        jbtn.when_pressed = lambda: (self.on_activity(), self.client.encoder_short_press())

        self._enc, self._sw, self._jbtn = enc, sw, jbtn

        self._ax, self._ay = self._make_adc()
        t = threading.Thread(target=self._joystick_loop, daemon=True)
        t.start()

    def _make_adc(self):
        import adafruit_ads1x15.ads1115 as ADS  # Pi only
        import board
        import busio
        from adafruit_ads1x15.analog_in import AnalogIn

        i2c = busio.I2C(board.SCL, board.SDA)
        ads = ADS.ADS1115(i2c)
        chans = [ADS.P0, ADS.P1, ADS.P2, ADS.P3]
        ax = AnalogIn(ads, chans[self.cfg["joy_x_chan"]])
        ay = AnalogIn(ads, chans[self.cfg["joy_y_chan"]])
        return ax, ay

    def _rotate(self, direction):
        self.on_activity()
        self.client.encoder_rotate(direction)

    def _on_encoder_hold(self):
        self._held = True
        self.on_activity()
        self.client.encoder_long_press()  # disarm

    def _on_encoder_release(self):
        self.on_activity()
        if self._held:
            self._held = False
        else:
            self.client.encoder_short_press()

    @staticmethod
    def _norm(analog_in):
        # ADS1115 single-ended 0..~26400 (gain 1); center ~ half. Return -1..1.
        return (analog_in.value / 32767.0) * 2.0 - 1.0

    def _joystick_loop(self):
        period = 1.0 / self.cfg["poll_hz"]
        dz = self.cfg["deadzone"]
        while not self._stop.wait(period):
            try:
                x = self._norm(self._ax) * (-1 if self.cfg["invert_x"] else 1)
                y = self._norm(self._ay) * (-1 if self.cfg["invert_y"] else 1)
            except Exception:
                continue

            # Y axis -> jog the armed target (up = increase). Start/stop on
            # crossing the deadzone, like the on-screen joystick.
            jog_dir = 0 if abs(y) < dz else (1 if y > 0 else -1)
            if jog_dir != self._jog_dir:
                self.on_activity()
                if self._jog_dir != 0:
                    self.client.jog_stop()
                if jog_dir != 0:
                    self.client.jog_start(jog_dir)
                self._jog_dir = jog_dir

            # X axis -> discrete navigate (one detent per push past deadzone,
            # re-arm only after returning near center).
            if abs(x) < dz * 0.6:
                self._nav_latch = 0
            elif self._nav_latch == 0:
                self.on_activity()
                nav = 1 if x > 0 else -1
                self.client.encoder_rotate(nav)
                self._nav_latch = nav

    def close(self):
        self._stop.set()
