"""Physical input backend for the control box: reads the rotary encoder
(GPIO quadrature + push) and the analog joystick (through an ADC) and
drives the same RemoteControlClient input methods that the on-screen
widgets call. Touch and physical controls therefore stay in sync.

Two ADC backends, because two different boxes exist:
    - MCP3008 over SPI  - the PSI "Motor Control Unit" box (Pi 3B, 7"
      official touchscreen, PoE), see PRESETS["psi-mcu-box"].
    - ADS1115 over I2C  - the Pi Zero 2 W battery pendant design.
Pick one with config["adc"], or leave it "auto" to try both.

Degrades gracefully: the Pi-only libraries (gpiozero, spidev, adafruit
ADS1x15) are imported lazily inside setup(). If they are missing - i.e.
you are not on the Pi - HardwareInput just reports itself unavailable and
the GUI keeps working via touch/keyboard. So the same pi_app runs on a
laptop for testing and on the real device unchanged.

Default wiring (BCM numbering) is the pendant design; the PSI box preset
overrides it to match the wiring in that box's build report:
    encoder A / B / push   : GPIO 5 / 6 / 13   (same in both)
    joystick push button   : ADC channel 0     (GPIO 19 in the pendant)
    joystick X / Y         : ADC channel 2 / 1 (A0 / A1 in the pendant)
    extra momentary button : GPIO 26           (absent in the pendant)
"""

import threading
import time

DEFAULT_CONFIG = {
    "enc_a": 5,
    "enc_b": 6,
    "enc_sw": 13,
    "joy_sw": 19,  # GPIO of the stick's push button (None if it is on the ADC)
    "joy_sw_chan": None,  # ADC channel of the stick button (PSI box: 0)
    "joy_sw_active_low": True,  # pressed pulls the ADC reading low
    "joy_sw_threshold": 0.5,  # fraction of full scale counted as "pressed"
    "extra_sw": None,  # GPIO of an extra momentary button (PSI box: 26)
    "extra_sw_action": "disarm",  # "disarm" or "ok"
    "joy_x_chan": 0,
    "joy_y_chan": 1,
    "adc": "auto",  # "auto" | "mcp3008" | "ads1115" | "none"
    "spi_bus": 0,
    "spi_dev": 0,
    "spi_hz": 1000000,
    "hold_time": 0.6,  # encoder long-press -> disarm
    "deadzone": 0.28,  # fraction of half-range before an axis acts
    "poll_hz": 30,
    "invert_y": False,
    "invert_x": False,
}

# Wiring of the existing PSI SwissFEL "Motor Control Unit" box (2018 build:
# Pi 3B + official 7" touchscreen + MCP3008 on SPI0 + KY-023 joystick +
# KY-040 encoder + a separate momentary button, powered over PoE).
# ADC channels per that box's schematic: CH0 = stick switch, CH1 = VRY,
# CH2 = VRX. The 7" display talks DSI + I2C touch, so SPI0 is free.
PRESETS = {
    "psi-mcu-box": {
        "adc": "mcp3008",
        "enc_a": 5,
        "enc_b": 6,
        "enc_sw": 13,
        "joy_sw": None,
        "joy_sw_chan": 0,
        "joy_x_chan": 2,
        "joy_y_chan": 1,
        "extra_sw": 26,
    },
    "pendant": {},  # the Pi Zero 2 W design = plain defaults (ADS1115)
}


def config_for(preset=None, **overrides):
    """Full config dict for a named preset plus ad-hoc overrides."""
    cfg = dict(DEFAULT_CONFIG)
    if preset:
        try:
            cfg.update(PRESETS[preset])
        except KeyError:
            raise ValueError(f"unknown preset {preset!r}; have {sorted(PRESETS)}")
    cfg.update(overrides)
    return cfg


class Mcp3008Adc:
    """10-bit SPI ADC (the PSI box). Channel reads return 0.0 .. 1.0."""

    full_scale = 1023.0

    def __init__(self, bus=0, device=0, max_speed_hz=1000000):
        import spidev  # Pi only

        self._spi = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = max_speed_hz
        self._lock = threading.Lock()

    def read_frac(self, channel):
        with self._lock:
            r = self._spi.xfer2([1, (8 + channel) << 4, 0])
        return (((r[1] & 3) << 8) + r[2]) / self.full_scale

    def close(self):
        try:
            self._spi.close()
        except Exception:
            pass


class Ads1115Adc:
    """16-bit I2C ADC (the battery pendant). Channel reads return 0.0 .. 1.0."""

    full_scale = 32767.0

    def __init__(self, **_):
        import adafruit_ads1x15.ads1115 as ADS  # Pi only
        import board
        import busio
        from adafruit_ads1x15.analog_in import AnalogIn

        i2c = busio.I2C(board.SCL, board.SDA)
        ads = ADS.ADS1115(i2c)
        pins = [ADS.P0, ADS.P1, ADS.P2, ADS.P3]
        self._chans = {i: AnalogIn(ads, pin) for i, pin in enumerate(pins)}

    def read_frac(self, channel):
        return self._chans[channel].value / self.full_scale

    def close(self):
        pass


def make_adc(cfg):
    """Build the configured ADC, or try both when cfg['adc'] == 'auto'."""
    kind = cfg.get("adc", "auto")
    builders = {
        "mcp3008": lambda: Mcp3008Adc(cfg["spi_bus"], cfg["spi_dev"], cfg["spi_hz"]),
        "ads1115": lambda: Ads1115Adc(),
    }
    if kind == "none":
        return None
    if kind != "auto":
        return builders[kind]()
    errors = []
    for name in ("mcp3008", "ads1115"):
        try:
            adc = builders[name]()
            print(f"ADC autodetected: {name}")
            return adc
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("no ADC found (" + "; ".join(errors) + ")")


class HardwareInput:
    def __init__(self, client, config=None, on_activity=None, preset=None):
        self.client = client
        self.cfg = config_for(preset, **(config or {}))
        self.on_activity = on_activity or (lambda: None)
        self.available = False
        self.adc = None
        self._stop = threading.Event()
        self._jog_dir = 0  # current joystick-driven jog direction
        self._nav_latch = 0  # current joystick-x navigate latch
        self._joy_sw_down = False  # debounce state of an ADC-read stick button
        try:
            self._setup()
            self.available = True
            print(f"hardware input active (encoder + joystick, adc={self.cfg['adc']})")
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
        self._buttons = [sw]
        self._enc = enc

        # Stick button: a GPIO on the pendant, an ADC channel on the PSI box
        # (that box wires the switch through the MCP3008, not to a GPIO).
        if self.cfg["joy_sw"] is not None:
            jbtn = Button(self.cfg["joy_sw"])
            jbtn.when_pressed = self._on_ok
            self._buttons.append(jbtn)

        if self.cfg["extra_sw"] is not None:
            action = self._on_extra_disarm if self.cfg["extra_sw_action"] == "disarm" else self._on_ok
            ebtn = Button(self.cfg["extra_sw"])
            ebtn.when_pressed = action
            self._buttons.append(ebtn)

        self.adc = make_adc(self.cfg)
        threading.Thread(target=self._joystick_loop, daemon=True).start()

    # --- event helpers ---
    def _rotate(self, direction):
        self.on_activity()
        self.client.encoder_rotate(direction)

    def _on_ok(self):
        self.on_activity()
        self.client.encoder_short_press()

    def _on_extra_disarm(self):
        self.on_activity()
        self.client.encoder_long_press()

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

    # --- joystick ---
    def _axis(self, channel):
        """Read one axis as -1 .. +1 (centre = 0)."""
        return self.adc.read_frac(channel) * 2.0 - 1.0

    def _poll_joy_button(self):
        chan = self.cfg["joy_sw_chan"]
        if chan is None:
            return
        frac = self.adc.read_frac(chan)
        down = frac < self.cfg["joy_sw_threshold"] if self.cfg["joy_sw_active_low"] \
            else frac > self.cfg["joy_sw_threshold"]
        if down and not self._joy_sw_down:
            self._on_ok()
        self._joy_sw_down = down

    def _joystick_loop(self):
        period = 1.0 / self.cfg["poll_hz"]
        dz = self.cfg["deadzone"]
        while not self._stop.wait(period):
            try:
                x = self._axis(self.cfg["joy_x_chan"]) * (-1 if self.cfg["invert_x"] else 1)
                y = self._axis(self.cfg["joy_y_chan"]) * (-1 if self.cfg["invert_y"] else 1)
                self._poll_joy_button()
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
        if self.adc is not None:
            self.adc.close()
