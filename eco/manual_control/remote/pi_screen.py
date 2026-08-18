"""Idle screen-power management for the pendant: the display backlight is
roughly half the device's power draw, so blanking it after a period of no
input dramatically extends battery life; any activity wakes it instantly.

Eco-free and self-detecting: uses a sysfs backlight if the display driver
exposes one, else a GPIO PWM backlight pin if configured, else becomes a
no-op (so it runs harmlessly off-Pi / on displays without brightness
control).
"""

import glob
import os
import threading
import time


class ScreenPower:
    def __init__(self, timeout=60.0, gpio_pin=None):
        self.timeout = float(timeout)
        self._last = time.time()
        self._blanked = False
        self._lock = threading.Lock()
        self._backend = self._detect_backend(gpio_pin)
        self._stop = threading.Event()
        if self.timeout > 0:
            threading.Thread(target=self._loop, daemon=True).start()
        print(f"screen power management: backend={self.backend}, timeout={self.timeout}s")

    def _detect_backend(self, gpio_pin):
        for base in glob.glob("/sys/class/backlight/*"):
            bright = os.path.join(base, "brightness")
            if os.access(bright, os.W_OK):
                try:
                    maxv = int(open(os.path.join(base, "max_brightness")).read())
                except Exception:
                    maxv = 255
                return ("sysfs", bright, maxv)
        if gpio_pin is not None:
            try:
                from gpiozero import PWMLED

                led = PWMLED(gpio_pin)
                led.value = 1.0
                return ("gpio", led, None)
            except Exception:
                pass
        return ("noop", None, None)

    @property
    def backend(self):
        return self._backend[0]

    def _set(self, on):
        kind = self._backend[0]
        if kind == "sysfs":
            _, path, maxv = self._backend
            try:
                with open(path, "w") as f:
                    f.write(str(maxv if on else 0))
            except Exception:
                pass
        elif kind == "gpio":
            self._backend[1].value = 1.0 if on else 0.0

    def wake(self, *args):
        with self._lock:
            self._last = time.time()
            if self._blanked:
                self._blanked = False
                self._set(True)

    def _loop(self):
        while not self._stop.wait(1.0):
            with self._lock:
                if not self._blanked and (time.time() - self._last) > self.timeout:
                    self._blanked = True
                    self._set(False)

    def close(self):
        self._stop.set()
        self._set(True)  # leave the screen on when we exit
