"""Offline tests for the manual-control thin client, in particular the
adaptation to the PSI 'Motor Control Unit' box (Pi 3B, 7" 800x480 panel,
MCP3008 joystick ADC, Ethernet/PoE link).

No Pi, no EPICS, no display: the ADC is faked, gpiozero is expected to be
absent (the backend must degrade), and the GUI tests are skipped unless a
display is available.
"""

import sys
import threading
import time
import types

import pytest

from eco.manual_control.demo import build_fake_beamline
from eco.manual_control.remote.client import RemoteControlClient
from eco.manual_control.remote.pi_hardware import (
    HardwareInput,
    Mcp3008Adc,
    config_for,
)
from eco.manual_control.remote.server import RemoteControlServer
from eco.manual_control.remote.transport import socketpair_transports

TOKEN = "test-token"


class FakeSpiDev:
    """spidev stand-in for an MCP3008: channel -> 10-bit count."""

    max_speed_hz = 0

    def __init__(self):
        self.values = {0: 1023, 1: 512, 2: 512}  # sw released, y/x centred

    def open(self, bus, device):
        self.bus, self.device = bus, device

    def xfer2(self, msg):
        value = self.values[(msg[1] >> 4) - 8]
        return [0, (value >> 8) & 3, value & 0xFF]

    def close(self):
        pass


@pytest.fixture
def fake_spi(monkeypatch):
    spi = FakeSpiDev()
    monkeypatch.setitem(sys.modules, "spidev", types.SimpleNamespace(SpiDev=lambda: spi))
    return spi


def _serve(token=None):
    srv_tr, cli_tr = socketpair_transports()
    RemoteControlServer(
        build_fake_beamline(), srv_tr, root_name="beamline",
        step_sizes=[0.001, 0.01, 0.1, 1, 10], token=token,
    ).start()
    return cli_tr


def _wait(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_token_required_client_gets_no_state():
    client = RemoteControlClient(_serve(token=TOKEN), token="wrong").start()
    assert not _wait(lambda: bool(client.entries), timeout=0.6)


def test_token_accepted_client_gets_tree():
    client = RemoteControlClient(_serve(token=TOKEN), token=TOKEN).start()
    assert _wait(lambda: bool(client.entries))
    assert client.path_names == ["beamline"]


def test_no_token_configured_still_works():
    client = RemoteControlClient(_serve()).start()
    assert _wait(lambda: bool(client.entries))


def test_psi_box_preset_matches_the_box_wiring():
    cfg = config_for("psi-mcu-box")
    assert (cfg["enc_a"], cfg["enc_b"], cfg["enc_sw"]) == (5, 6, 13)
    assert cfg["adc"] == "mcp3008"
    assert (cfg["joy_x_chan"], cfg["joy_y_chan"]) == (2, 1)  # VRX=CH2, VRY=CH1
    assert cfg["joy_sw_chan"] == 0 and cfg["joy_sw"] is None  # switch on the ADC
    assert cfg["extra_sw"] == 26


def test_unknown_preset_rejected():
    with pytest.raises(ValueError):
        config_for("nope")


def test_mcp3008_scaling(fake_spi):
    adc = Mcp3008Adc()
    fake_spi.values[2] = 1023
    assert adc.read_frac(2) == pytest.approx(1.0)
    fake_spi.values[2] = 0
    assert adc.read_frac(2) == 0.0
    fake_spi.values[2] = 512
    assert adc.read_frac(2) == pytest.approx(0.5, abs=0.01)


def test_hardware_input_degrades_without_gpio(fake_spi):
    # gpiozero cannot find a pin factory off-Pi -> GUI-only mode, no crash
    hw = HardwareInput(RemoteControlClient(_serve()), preset="psi-mcu-box")
    assert hw.available is False


def test_joystick_loop_drives_jog_navigate_and_button(fake_spi):
    events = []

    class Recorder:
        jog_start = lambda self, d: events.append(("jog_start", d))  # noqa: E731
        jog_stop = lambda self: events.append(("jog_stop",))  # noqa: E731
        encoder_rotate = lambda self, d: events.append(("rotate", d))  # noqa: E731
        encoder_short_press = lambda self: events.append(("ok",))  # noqa: E731
        encoder_long_press = lambda self: events.append(("disarm",))  # noqa: E731

    hw = HardwareInput.__new__(HardwareInput)
    hw.client = Recorder()
    hw.cfg = config_for("psi-mcu-box")
    hw.on_activity = lambda: None
    hw._stop = threading.Event()
    hw._jog_dir = 0
    hw._nav_latch = 0
    hw._joy_sw_down = False
    hw.adc = Mcp3008Adc()
    threading.Thread(target=hw._joystick_loop, daemon=True).start()
    try:
        fake_spi.values[1] = 1023  # stick up -> jog +1
        assert _wait(lambda: ("jog_start", 1) in events)
        fake_spi.values[1] = 512  # centred -> stop
        assert _wait(lambda: ("jog_stop",) in events)
        fake_spi.values[2] = 1023  # stick right -> exactly one detent
        assert _wait(lambda: ("rotate", 1) in events)
        time.sleep(0.15)
        assert [e[0] for e in events].count("rotate") == 1
        fake_spi.values[0] = 0  # stick button (active low) -> one OK
        assert _wait(lambda: ("ok",) in events)
        time.sleep(0.15)
        assert [e[0] for e in events].count("ok") == 1
    finally:
        hw.close()


@pytest.mark.parametrize(
    "size,expect_landscape", [((800, 480), True), ((480, 320), False)]
)
def test_gui_fits_the_panel_exactly(size, expect_landscape):
    tk = pytest.importorskip("tkinter")
    from eco.manual_control.mock_gui import ManualControlApp

    try:
        probe = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    probe.destroy()

    client = RemoteControlClient(_serve()).start()
    assert _wait(lambda: bool(client.entries))
    app = ManualControlApp(client, mock=False, screen_size=size, font_scale=1.4)
    try:
        app.update_idletasks()
        app.update()
        assert (app.device.winfo_width(), app.device.winfo_height()) == size
        assert app.landscape is expect_landscape
    finally:
        app.destroy()
