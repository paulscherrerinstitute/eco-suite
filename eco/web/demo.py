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
from eco.elements.adjustable import AdjustableGetSet, AdjustableMemory, AdjustableTrigger
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


class SimMotorRecord(Assembly):
    """Like eco.devices_general.motors.MotorRecord: an Assembly that is
    itself an Adjustable, with readback/speed/limit items and a hardware
    jog (JOGF/JOGR at JVEL, ending at the soft limits)."""

    def __init__(self, value=0.0, speed=1.0, jog_speed=0.2, limits=(-10, 10), unit="mm", name=None):
        super().__init__(name=name)
        self.status_collection.append(self, selection="settings", recursive=False)
        self._motor = SimMotor(value, speed=speed, limits=limits, unit=unit, name=f"{name}_motor")
        self.unit = unit
        self._jog = None  # (direction, stop event)
        self._append(DetectorGetter, self._motor.get_current_value, name="readback", is_display=True)
        self._append(AdjustableMemory, speed, name="speed", is_display=True)
        self._append(AdjustableMemory, jog_speed, name="jog_speed", is_display=False)
        self._append(AdjustableMemory, limits[0], name="limit_low", is_display=False)
        self._append(AdjustableMemory, limits[1], name="limit_high", is_display=False)

    def get_current_value(self):
        return self._motor.get_current_value()

    def set_target_value(self, value):
        self.jog_stop()
        self._motor.speed = self.speed.get_current_value()
        self._motor.limits = (self.limit_low.get_current_value(), self.limit_high.get_current_value())
        return self._motor.set_target_value(value)

    def stop(self):
        self.jog_stop()
        self._motor.stop()

    def jog(self, direction, start=True):
        if not start:
            return self.jog_stop()
        self.jog_stop()
        self._motor.stop()
        stop = threading.Event()
        self._jog = (direction, stop)
        motor = self._motor

        def run():
            dt = 0.05
            while not stop.is_set():
                low, high = self.limit_low.get_current_value(), self.limit_high.get_current_value()
                with motor._lock:
                    value = motor._value + (1 if direction > 0 else -1) * self.jog_speed.get_current_value() * dt
                    motor._value = min(max(value, low), high)
                    if motor._value in (low, high):
                        break
                time.sleep(dt)

        threading.Thread(target=run, daemon=True).start()

    def jog_stop(self):
        if self._jog is not None:
            self._jog[1].set()
            self._jog = None


class DetectorGetter:
    def __init__(self, getter, name=None):
        self.name = name
        self.alias = Alias(name)
        self._getter = getter

    def get_current_value(self):
        return self._getter()


class Stage(Assembly):
    """Two motor-record axes (value, ▲/▼, tweak and jog) and a detector to watch."""

    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(SimMotorRecord, 0.0, speed=1.0, jog_speed=0.2, limits=(-5, 5), unit="mm", name="x", is_display=True)
        self._append(SimMotorRecord, 0.0, speed=1.0, jog_speed=0.2, limits=(-5, 5), unit="mm", name="y", is_display=True)
        self._append(SimDetector, 1000.0, 20.0, unit="cts", name="diode", is_display=True)


class SimPTZCamera(Assembly):
    """Like eco.devices_general.cameras_ptz.AxisPTZ, without a camera: an
    MJPEG stream drawn with PIL (a pan/tilt grid, a few labelled targets and
    a clock) and the same click-to-center / area-zoom / home commands."""

    TARGETS = [(0, 0, "sample"), (-25, 5, "detector"), (30, -8, "laser in"), (-60, -20, "door"), (70, 12, "lamp")]

    def __init__(self, name=None):
        super().__init__(name=name)
        self._pos = {"pan": 0.0, "tilt": 0.0, "zoom": 1.0}
        self.rotate_display_180 = False
        for axis, unit in (("pan", "deg"), ("tilt", "deg"), ("zoom", "")):
            self._append(AdjustableGetSet, lambda a=axis: round(self._pos[a], 3),
                         lambda v, a=axis: self._pos.__setitem__(a, float(v)), name=axis, is_display=True)
        self._append(AdjustableTrigger, self.home, name="go_home", is_display=True, button_label="Home")

    def _fov(self, w, h):
        hfov = 60.0 / self._pos["zoom"]
        return hfov, hfov * h / w

    def home(self):
        self._pos.update(pan=0.0, tilt=0.0, zoom=1.0)

    def set_zoom(self, value):
        self._pos["zoom"] = min(max(float(value), 1.0), 30.0)

    def click_center(self, x, y, image_width, image_height):
        hfov, vfov = self._fov(image_width, image_height)
        self._pos["pan"] = min(max(self._pos["pan"] + (x / image_width - 0.5) * hfov, -170), 170)
        self._pos["tilt"] = min(max(self._pos["tilt"] - (y / image_height - 0.5) * vfov, -80), 30)

    def area_zoom(self, x, y, z, image_width, image_height):
        self.click_center(x, y, image_width, image_height)
        self.set_zoom(self._pos["zoom"] * z / 100)

    def zoom_to_rectangle(self, x0, y0, x1, y1, image_width, image_height):
        cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0), abs(y1 - y0)
        if w < 4 or h < 4:
            return self.click_center(cx, cy, image_width, image_height)
        return self.area_zoom(cx, cy, 100 * min(image_width / w, image_height / h), image_width, image_height)

    def iter_mjpeg_frames(self, resolution=None, compression=None, fps=None, stop_event=None, chunk_size=None):
        import io

        from PIL import Image, ImageDraw

        w, h = (int(v) for v in (resolution or "1280x720").split("x"))
        quality = max(10, 100 - int(compression or 50))
        period = 1.0 / (fps or 10)
        while stop_event is None or not stop_event.is_set():
            t = time.time()
            hfov, vfov = self._fov(w, h)
            pan, tilt = self._pos["pan"], self._pos["tilt"]
            X = lambda p: (p - pan) / hfov * w + w / 2
            Y = lambda q: (tilt - q) / vfov * h + h / 2
            img = Image.new("RGB", (w, h), (32, 44, 60))
            d = ImageDraw.Draw(img)
            step = 10 if hfov > 20 else (2 if hfov > 5 else 0.5)
            p = math.floor((pan - hfov) / step) * step
            while p <= pan + hfov:
                d.line([(X(p), 0), (X(p), h)], fill=(70, 90, 115))
                d.text((X(p) + 3, 3), f"{p:g}", fill=(150, 170, 200))
                p += step
            q = math.floor((tilt - vfov) / step) * step
            while q <= tilt + vfov:
                d.line([(0, Y(q)), (w, Y(q))], fill=(70, 90, 115))
                d.text((3, Y(q) + 2), f"{q:g}", fill=(150, 170, 200))
                q += step
            for tp, tt, label in self.TARGETS:
                r = 2.0 / hfov * w
                d.ellipse([X(tp) - r, Y(tt) - r, X(tp) + r, Y(tt) + r], outline=(255, 200, 60), width=3)
                d.text((X(tp) + r + 4, Y(tt) - 6), label, fill=(255, 220, 120))
            d.line([(w / 2 - 12, h / 2), (w / 2 + 12, h / 2)], fill=(255, 255, 255))
            d.line([(w / 2, h / 2 - 12), (w / 2, h / 2 + 12)], fill=(255, 255, 255))
            d.text((8, h - 18), f"{time.strftime('%H:%M:%S')}  pan {pan:.1f}  tilt {tilt:.1f}  zoom {self._pos['zoom']:.2f}",
                   fill=(255, 255, 255))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality)
            yield buf.getvalue()
            time.sleep(max(0.0, period - (time.time() - t)))


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


class Goniometer(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(SimMotor, 0.0, speed=5.0, limits=(-180, 180), unit="deg", name="omega", is_display=True)
        self._append(SimMotor, 0.0, speed=2.0, limits=(-45, 45), unit="deg", name="chi", is_display=True)
        self._append(SimMotor, 0.0, speed=5.0, limits=(-180, 180), unit="deg", name="phi", is_display=True)


class Cryostat(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(AdjustableMemory, 100.0, name="setpoint", is_display=True)
        self._append(SimDetector, 100.0, 0.05, unit="K", name="temperature", is_display=True)


class Sample(Assembly):
    """A nested device: the goniometer opens as its own view (is_display=True),
    the cryostat's items are shown inline (is_display="recursive")."""

    def __init__(self, name=None):
        super().__init__(name=name)
        for axis in "xyz":
            self._append(SimMotor, 0.0, speed=0.5, limits=(-10, 10), unit="mm", name=axis, is_display=True)
        self._append(Goniometer, name="gonio", is_display=True)
        self._append(Cryostat, name="cryo", is_display="recursive")


class SimScan:
    """The parts of eco.acquisition.scan.StepScan the queue view reads:
    steps done/to do, pause/resume at the next step, stop."""

    def __init__(self, positions):
        self._values_todo = list(positions)
        self._values_done = []
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_requested = False

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def is_paused(self):
        return not self._pause_event.is_set()

    def request_stop(self):
        self._stop_requested = True
        self._pause_event.set()


def demo_scan(motor, detector, start, stop, steps, step_time=0.5):
    """A queued 'scan' of a simulated motor: registers a SimScan with its
    task (as StepScan does), moves step by step, prints a line per step."""
    from eco.utilities import task_queue

    positions = [start + (stop - start) * i / (steps - 1) for i in range(steps)]
    scan = SimScan(positions)
    task = task_queue.current_task()
    if task is not None:
        task.scans.append(scan)
    print(f"scan {motor.name} {start} .. {stop}, {steps} steps")
    while scan._values_todo:
        while not scan._pause_event.wait(0.1):
            pass
        if scan._stop_requested:
            print("stopped on request")
            return
        value = scan._values_todo[0]
        motor.set_target_value(value).wait()
        time.sleep(step_time)
        print(f"step {len(scan._values_done) + 1:3d}/{steps}  {motor.name} = {motor.get_current_value():8.4f}"
              f"  {detector.name} = {detector.get_current_value():9.2f}")
        scan._values_done.append(scan._values_todo.pop(0))
    print("scan done")


def demo_queues(namespace):
    """A queue "default" with three scans waiting, paused (so they wait for
    Resume in the web client), and an empty queue "align"."""
    from eco.utilities.task_queue import TaskQueue

    stage = namespace.resolve_item("stage")
    default = TaskQueue("default", namespace={})
    default.pause()  # before the first append starts its worker
    for start, stop, steps in ((-1, 1, 40), (-0.5, 0.5, 30), (0, 2, 20)):
        default.append(demo_scan, stage.x, stage.diode, start, stop, steps, test=False,
                       task_description=f"x {start}..{stop}")
    return {"default": default, "align": TaskQueue("align", namespace={})}


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
            "sample": Sample,
            "stage": Stage,
            "cam_demo": SimPTZCamera,
            "lxt": lambda name=None: SimMotor(0.0, speed=2e-12, limits=(-1e-9, 1e-9), unit="s", name=name),
            "slow_device": SlowDevice,
            "broken": _broken,
        }
        for name in ("mono", "lxt", "stage"):
            self.init_name(name)
        for name in ("slits", "vacuum", "sample", "cam_demo", "slow_device", "broken"):
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
