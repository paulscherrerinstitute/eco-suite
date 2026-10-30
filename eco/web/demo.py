"""A simulated namespace for developing and trying the web client without a
beamline: motors that take time to move, noisy detectors, valves, an enum
selector, a trigger, an SVG panel, a component that initializes slowly and
one that fails. Pure Python, no EPICS.

    python -m eco.web --demo            # serve it (read-only)
    python -m eco.web --demo --writes   # with writes
"""
import enum
import math
import random
import threading
import time

from eco.aliases.aliases import Alias
from eco.elements.adjustable import AdjustableMemory, AdjustableTrigger
from eco.elements.assembly import Assembly


class _Changer:
    def __init__(self, done):
        self._done = done
        self.stopped = False

    def wait(self):
        self._done.wait()

    def stop(self):
        self.stopped = True


class SimMotor:
    """Moves linearly at `speed` units/s; stoppable; soft limits."""

    def __init__(self, value=0.0, speed=1.0, limits=(-100, 100), unit="mm", name=None):
        self.name = name
        self.alias = Alias(name)
        self.unit = unit
        self.speed = speed
        self.limits = limits
        self._value = float(value)
        self._lock = threading.Lock()
        self._changer = None

    def get_current_value(self):
        return round(self._value, 6)

    def set_target_value(self, value):
        value = float(value)
        low, high = self.limits
        if not low <= value <= high:
            raise ValueError(f"{self.name}: {value} outside limits {self.limits}")
        if self._changer is not None:
            self._changer.stop()
        done = threading.Event()
        changer = self._changer = _Changer(done)

        def run():
            dt = 0.05
            while not changer.stopped:
                with self._lock:
                    delta = value - self._value
                    step = math.copysign(min(abs(delta), self.speed * dt), delta)
                    self._value += step
                    if abs(value - self._value) < 1e-9:
                        break
                time.sleep(dt)
            done.set()

        threading.Thread(target=run, daemon=True).start()
        return changer

    def stop(self):
        if self._changer is not None:
            self._changer.stop()


class SimDetector:
    def __init__(self, center=1.0, noise=0.05, unit=None, log=False, name=None):
        self.name = name
        self.alias = Alias(name)
        self.unit = unit
        self.center = center
        self.noise = noise
        self.log = log

    def get_current_value(self):
        if self.log:
            return self.center * 10 ** random.gauss(0, self.noise)
        return self.center + random.gauss(0, self.noise)


class ValveState(enum.Enum):
    closed = 0
    open = 1


class SimValve:
    def __init__(self, open=True, name=None):
        self.name = name
        self.alias = Alias(name)
        self._state = ValveState.open if open else ValveState.closed

    def get_current_value(self):
        return self._state

    def set_target_value(self, value):
        if isinstance(value, str):
            value = ValveState[value]
        elif not isinstance(value, ValveState):
            value = ValveState(int(value))
        done = threading.Event()

        def run():
            time.sleep(0.8)
            self._state = value
            done.set()

        threading.Thread(target=run, daemon=True).start()
        return _Changer(done)


class Mono(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(SimMotor, 7.0, speed=0.2, limits=(4, 20), unit="keV", name="energy", is_display=True)
        self._append(SimMotor, 15.0, speed=0.5, limits=(5, 40), unit="deg", name="bragg", is_display=True)
        self._append(SimDetector, 0.0, 0.002, unit="urad", name="pitch_rbv", is_display=True)
        self._append(AdjustableMemory, 3, name="harmonic", is_display=True)
        self._append(AdjustableTrigger, lambda: time.sleep(1), name="home_all", is_display=True)


class Slits(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(SimMotor, 1.0, speed=0.5, limits=(0, 10), name="hgap", is_display=True)
        self._append(SimMotor, 1.0, speed=0.5, limits=(0, 10), name="vgap", is_display=True)
        self._append(SimMotor, 0.0, speed=0.5, limits=(-5, 5), name="hpos", is_display=True)
        self._append(SimMotor, 0.0, speed=0.5, limits=(-5, 5), name="vpos", is_display=True)
        self._append(SimMotor, 0.0, speed=0.2, limits=(-5, 5), name="blade_offset", is_display=False)


class Vacuum(Assembly):
    """A short vacuum section with an SVG panel (like PrepumpSystem/Beamline)."""

    def __init__(self, name=None):
        super().__init__(name=name)
        for i, open_ in enumerate([True, True, False], start=1):
            self._append(SimValve, open_, name=f"valve{i}", is_display=True)
        for i, center in enumerate([1e-7, 3e-6, 2e-2], start=1):
            self._append(SimDetector, center, 0.1, unit="mbar", log=True, name=f"gauge{i}", is_display=True)

    def _widget_svg_panel(self, path=None, live=True):
        from eco.utilities.tempfiles import user_temp_svg_path

        parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 160" font-family="sans-serif">',
                 '<line x1="20" y1="80" x2="620" y2="80" stroke="#888" stroke-width="6"/>']
        for i in range(3):
            x = 100 + i * 200
            valve = getattr(self, f"valve{i + 1}")
            gauge = getattr(self, f"gauge{i + 1}")
            color = "#888"
            pressure = ""
            if live:
                color = "#2e9d4a" if valve.get_current_value() is ValveState.open else "#d93025"
                pressure = f"{gauge.get_current_value():.1e}"
            parts.append(f'<g style="cursor:pointer" onclick="// eco: valve{i + 1}"><desc>valve{i + 1}</desc>'
                         f'<polygon points="{x - 20},60 {x + 20},100 {x + 20},60 {x - 20},100" fill="{color}" stroke="#333"/>'
                         f'<text x="{x}" y="125" text-anchor="middle" font-size="13">valve{i + 1}</text></g>')
            gx = x + 90
            parts.append(f'<g style="cursor:pointer" onclick="// eco: gauge{i + 1}"><desc>gauge{i + 1}</desc>'
                         f'<circle cx="{gx}" cy="45" r="14" fill="#fff" stroke="#333"/>'
                         f'<text x="{gx}" y="20" text-anchor="middle" font-size="12">{pressure}</text>'
                         f'<line x1="{gx}" y1="59" x2="{gx}" y2="80" stroke="#333"/></g>')
        parts.append("</svg>")
        path = path or user_temp_svg_path("eco_web_demo", self.name)
        with open(path, "w") as f:
            f.write("".join(parts))
        return path


class SlowDevice(Assembly):
    def __init__(self, name=None):
        time.sleep(2.0)
        super().__init__(name=name)
        self._append(SimDetector, 42.0, 0.5, name="reading", is_display=True)


def _broken(name=None):
    raise ConnectionError("simulated: IOC not reachable")


class DemoNamespace:
    """Just the parts of eco.utilities.config.Namespace the web backend uses."""

    name = "demo"

    def __init__(self):
        self.initialized_items = {}
        self.lazy_items = {}
        self.failed_items = {}
        self.failed_items_excpetion = {}
        self._factories = {
            "mono": Mono,
            "slits": Slits,
            "vacuum": Vacuum,
            "lxt": lambda name=None: SimMotor(0.0, speed=2e-12, limits=(-1e-9, 1e-9), unit="s", name=name),
            "slow_device": SlowDevice,
            "broken": _broken,
        }
        for name in ("mono", "lxt"):
            self.init_name(name)
        for name in ("slits", "vacuum", "slow_device", "broken"):
            self.lazy_items[name] = name
        self.init_name("broken")

    @property
    def initialized_names(self):
        return set(self.initialized_items)

    @property
    def lazy_names(self):
        return set(self.lazy_items)

    @property
    def failed_names(self):
        return set(self.failed_items)

    @property
    def all_names(self):
        return self.initialized_names | self.lazy_names | self.failed_names

    def resolve_item(self, name):
        for items in (self.lazy_items, self.failed_items, self.initialized_items):
            if name in items:
                return items[name]
        return None

    def init_name(self, name, verbose=True, raise_errors=False, quiet=False):
        self.lazy_items.pop(name, None)
        try:
            self.initialized_items[name] = self._factories[name](name=name)
        except Exception as exc:
            self.failed_items[name] = None
            self.failed_items_excpetion[name] = exc
            if raise_errors:
                raise
