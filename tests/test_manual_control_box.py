"""Offline tests for the manual-control thin client, in particular the
adaptation to the PSI 'Motor Control Unit' box (Pi 3B, 7" 800x480 panel,
MCP3008 joystick ADC, Ethernet/PoE link).

No Pi, no EPICS, no display: the ADC is faked, gpiozero is expected to be
absent (the backend must degrade), and the GUI tests are skipped unless a
display is available.
"""

import os
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
    """A box-side transport wired to a server, without any network."""
    srv_tr, cli_tr = socketpair_transports()
    RemoteControlServer(
        build_fake_beamline(), srv_tr, root_name="beamline",
        step_sizes=[0.001, 0.01, 0.1, 1, 10], token=token,
    ).start()
    return cli_tr


def _box_listener(port, token=TOKEN):
    """A listening box; the caller plays the operator via .pending()."""
    from eco.manual_control.remote.box_link import BoxListener

    return BoxListener(port=port, token=token, bind="127.0.0.1")


def _next_request(listener, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        request = listener.pending()
        if request is not None:
            return request
        time.sleep(0.02)
    return None


def _wait(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_caller_with_a_bad_token_never_reaches_the_operator():
    """The box refuses the wrong token itself - the operator is not bothered."""
    from eco.manual_control.remote.serve import connect_to_box

    listener = _box_listener(8769)
    try:
        session = connect_to_box(build_fake_beamline(), root_name="b",
                                 host="127.0.0.1", port=8769, token="wrong",
                                 token_file=None)
        assert _wait(lambda: session.state == "closed")
        assert "token" in (session.reason or "")
        assert _next_request(listener, timeout=0.5) is None
    finally:
        listener.close()


def test_accepted_session_drives_the_box():
    """Mode 1: nothing connected, a session calls, the operator accepts."""
    from eco.manual_control.remote.serve import connect_to_box

    listener = _box_listener(8768)
    try:
        session = connect_to_box(build_fake_beamline(), root_name="beamline",
                                 host="127.0.0.1", port=8768, token=TOKEN,
                                 token_file=None)
        request = _next_request(listener)
        assert request is not None
        assert session.state == "waiting for the operator"
        assert "@" in request.who and request.namespace == "beamline"
        client = RemoteControlClient(request.accept()).start()
        assert _wait(lambda: bool(client.entries))
        assert _wait(lambda: session.connected)
    finally:
        listener.close()


def test_rejected_session_closes_and_leaves_the_current_one_alone():
    """Mode 2: the operator keeps the session already driving the box."""
    from eco.manual_control.remote.serve import connect_to_box

    listener = _box_listener(8767)
    try:
        first = connect_to_box(build_fake_beamline(), root_name="first",
                               host="127.0.0.1", port=8767, token=TOKEN, token_file=None)
        client = RemoteControlClient(_next_request(listener).accept()).start()
        assert _wait(lambda: first.connected)

        second = connect_to_box(build_fake_beamline(), root_name="second",
                                host="127.0.0.1", port=8767, token=TOKEN, token_file=None)
        _next_request(listener).reject("the box stayed with the current session")
        assert _wait(lambda: second.state == "closed")
        assert "stayed with" in (second.reason or "")
        assert first.connected, "declining must not disturb the current session"
        assert client.connected
    finally:
        listener.close()


def test_takeover_closes_the_displaced_session_with_a_reason():
    """Mode 2: the operator hands the box over; the old session closes itself."""
    from eco.manual_control.remote.box_link import say_bye
    from eco.manual_control.remote.serve import connect_to_box

    listener = _box_listener(8766)
    try:
        first = connect_to_box(build_fake_beamline(), root_name="first",
                               host="127.0.0.1", port=8766, token=TOKEN, token_file=None)
        first_client = RemoteControlClient(_next_request(listener).accept()).start()
        assert _wait(lambda: first.connected)

        second = connect_to_box(build_fake_beamline(), root_name="second",
                                host="127.0.0.1", port=8766, token=TOKEN, token_file=None)
        request = _next_request(listener)
        say_bye(first_client.tr, f"the box was handed to {request.who}")  # what the GUI does
        second_client = RemoteControlClient(request.accept()).start()

        assert _wait(lambda: bool(second_client.entries))
        assert _wait(lambda: second.connected)
        assert _wait(lambda: not first.connected), "displaced session stayed open"
        assert "handed to" in (first.reason or "")
    finally:
        listener.close()


def test_psi_box_jog_axis_is_inverted(fake_spi):
    """The jog axis is flipped relative to the raw ADC reading.

    On the real box the stick jogged the wrong way, so the preset inverts Y:
    a rising Y reading must now produce a negative jog. (Which physical
    direction that is depends on how the stick is mounted - the point is
    that it is the opposite of the raw axis.)
    """
    events = []

    class Recorder:
        jog_start = lambda self, d: events.append(("jog_start", d))  # noqa: E731
        jog_stop = lambda self: events.append(("jog_stop",))  # noqa: E731
        encoder_rotate = lambda self, d: None  # noqa: E731
        encoder_short_press = lambda self: None  # noqa: E731
        encoder_long_press = lambda self: None  # noqa: E731

    cfg = config_for("psi-mcu-box")
    assert cfg["invert_y"] is True, "the jog axis must stay inverted for this box"

    hw = HardwareInput.__new__(HardwareInput)
    hw.client = Recorder()
    hw.cfg = cfg
    hw.on_activity = lambda: None
    hw._stop = threading.Event()
    hw._jog_dir = 0
    hw._nav_latch = 0
    hw._joy_sw_down = False
    hw.adc = Mcp3008Adc()
    threading.Thread(target=hw._joystick_loop, daemon=True).start()
    try:
        fake_spi.values[cfg["joy_y_chan"]] = 1023  # raw reading rises
        assert _wait(lambda: events)
        assert events[0] == ("jog_start", -1), f"jog axis not inverted: {events[0]}"
    finally:
        hw.close()


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


def test_encoder_and_joystick_fail_independently(fake_spi):
    """A dead ADC must not take the encoder down with it, or vice versa.

    On the real box both were dead at once because a single exception in
    setup killed every physical control; the two are separate hardware
    (GPIO vs SPI) and must degrade separately.
    """
    # gpiozero finds no pin factory off-Pi, but the (faked) SPI ADC works
    hw = HardwareInput(RemoteControlClient(_serve()), preset="psi-mcu-box")
    try:
        assert hw.encoder_ok is False
        assert hw.encoder_error is not None
        assert hw.joystick_ok is True
        assert hw.available is True  # half the controls still work
    finally:
        hw.close()


def test_hardware_input_degrades_when_nothing_is_available():
    """No gpiozero pin factory and no ADC -> GUI-only mode, no crash."""
    hw = HardwareInput(RemoteControlClient(_serve()), preset="psi-mcu-box",
                       config={"adc": "none"})
    try:
        assert hw.available is False
        assert hw.encoder_ok is False and hw.joystick_ok is False
        assert hw.joystick_error is not None
    finally:
        hw.close()


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
        fake_spi.values[1] = 1023  # rising Y -> jog -1 (the preset inverts Y)
        assert _wait(lambda: ("jog_start", -1) in events)
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


def test_namespace_control_box_survives_an_unreachable_box_host():
    from eco.utilities.config import Namespace

    ns = Namespace(name="ns_no_box")
    try:
        ns.start_eco_control_box(port=8794, bind="127.0.0.1", token=TOKEN,
                                 token_file=None, box_host="pi@no-such-box.invalid")
    finally:
        ns.stop_eco_control_box()


def _namespace_with_control_box(name="bernina"):
    import sys
    import types

    from eco.utilities.config import Namespace

    mod_name = f"fake_instrument_{name}"
    mod = types.ModuleType(mod_name)
    sys.modules[mod_name] = mod
    ns = Namespace(name=name, root_module=mod_name)
    ns.append_obj("ControlBox", name="manual_control_box",
                  module_name="eco.manual_control.control_box", lazy=True)
    return ns, mod


def test_control_box_manual_covers_the_operational_basics():
    """The docstring is the manual - keep it complete."""
    from eco.manual_control.control_box import ControlBox

    manual = ControlBox.__doc__
    for topic in ["ssh gac-bernina@ecobox", "/etc/eco-control-box.token",
                  "waiting screen", "pin factory", "python3-lgpio",
                  "invert_x", "eco-control-box", "--probe"]:
        assert topic in manual, f"manual does not mention {topic!r}"


def test_namespace_start_eco_control_box_offers_itself_to_the_box():
    """bernina.namespace.start_eco_control_box() -> the box asks the operator."""
    from eco.elements.adjustable import DummyAdjustable
    from eco.utilities.config import Namespace

    listener = _box_listener(8765)
    ns = Namespace(name="ns_offer")
    ns.append_obj(DummyAdjustable, name="theta", module_name=None)
    try:
        session = ns.start_eco_control_box(box_host="127.0.0.1", port=8765,
                                           token=TOKEN, token_file=None)
        request = _next_request(listener)
        assert request is not None and request.namespace == "ns_offer"
        client = RemoteControlClient(request.accept()).start()
        assert _wait(lambda: bool(client.entries))
        assert {e.name for e in client.entries} == {"theta"}
        # idempotent: a second call returns the same session
        assert ns.start_eco_control_box(box_host="127.0.0.1", port=8765) is session
    finally:
        ns.stop_eco_control_box()
        listener.close()


def test_session_notices_the_box_going_away():
    """If the box vanishes, the session must close itself, not look connected."""
    listener = _box_listener(8764)
    from eco.manual_control.remote.serve import connect_to_box

    try:
        session = connect_to_box(build_fake_beamline(), root_name="beamline",
                                 host="127.0.0.1", port=8764, token=TOKEN, token_file=None)
        client = RemoteControlClient(_next_request(listener).accept()).start()
        assert _wait(lambda: session.connected)
        client.tr.close()  # the box app dies / is restarted
        assert _wait(lambda: not session.connected), "session did not notice the box leaving"
    finally:
        listener.close()


def test_box_serves_a_new_session_after_the_previous_one_ended():
    """The box keeps listening, so the next session can simply call again."""
    from eco.manual_control.remote.serve import connect_to_box

    listener = _box_listener(8763)
    try:
        first = connect_to_box(build_fake_beamline(), root_name="first",
                               host="127.0.0.1", port=8763, token=TOKEN, token_file=None)
        first_client = RemoteControlClient(_next_request(listener).accept()).start()
        assert _wait(lambda: first.connected)
        first.stop()

        second = connect_to_box(build_fake_beamline(), root_name="second",
                                host="127.0.0.1", port=8763, token=TOKEN, token_file=None)
        second_client = RemoteControlClient(_next_request(listener).accept()).start()
        assert _wait(lambda: bool(second_client.entries))
        assert _wait(lambda: second.connected)
    finally:
        listener.close()


def test_a_silent_caller_cannot_block_the_box():
    """A caller that connects and says nothing must not lock everyone out."""
    import socket

    from eco.manual_control.remote.serve import connect_to_box

    listener = _box_listener(8762)
    mute = socket.create_connection(("127.0.0.1", 8762))  # connects, sends no hello
    try:
        session = connect_to_box(build_fake_beamline(), root_name="real",
                                 host="127.0.0.1", port=8762, token=TOKEN, token_file=None)
        request = _next_request(listener)
        assert request is not None, "a silent caller blocked the listener"
        assert request.namespace == "real"
        client = RemoteControlClient(request.accept()).start()
        assert _wait(lambda: bool(client.entries))
        assert _wait(lambda: session.connected)
    finally:
        mute.close()
        listener.close()


def test_control_box_component_offers_the_namespace_it_lives_in():
    """bernina.manual_control_box.start() with no arguments offers bernina."""
    from eco.elements.adjustable import DummyAdjustable

    listener = _box_listener(8761)
    ns, mod = _namespace_with_control_box(name="bernina")
    ns.append_obj(DummyAdjustable, name="theta", module_name=None)
    box = mod.manual_control_box
    box.host, box.port, box.token_file = "127.0.0.1", 8761, None
    try:
        box.start(token=TOKEN)
        # start() returns at once; the dialling happens in the background
        assert _wait(lambda: box.state == "waiting for the operator")
        request = _next_request(listener)
        assert request is not None and request.namespace == "bernina"
        client = RemoteControlClient(request.accept()).start()
        assert _wait(lambda: bool(client.entries))
        assert _wait(lambda: box.is_connected)
        assert "theta" in {e.name for e in client.entries}
        assert box.start() is box.server  # idempotent
    finally:
        box.stop()
        listener.close()


# --- slots, menu, step memory, memories -------------------------------------

def _box(tmp_path=None, **kwargs):
    from eco.manual_control.box import ManualControlBox
    from eco.manual_control.step_store import StepStore

    kwargs.setdefault("step_sizes", [0.001, 0.01, 0.1, 1, 10])
    kwargs.setdefault("step_store", StepStore(path=None))
    return ManualControlBox(build_fake_beamline(), root_name="bernina", **kwargs)


def _arm(box, *path):
    """Walk to a leaf by name and arm it."""
    for name in path:
        names = [e.name for e in box.entries]
        box.set_cursor(names.index(name))
        box.activate_cursor()


def test_several_axes_stay_armed_each_with_its_own_step():
    box = _box()
    _arm(box, "mono", "theta")
    box.breadcrumb_jump(0)
    _arm(box, "attenuator")
    assert [s.name for s in box.slots] == ["theta", "attenuator"]
    assert box.active_slot == 1 and box.target_name() == "attenuator"

    box.step_up()  # only the active slot changes
    steps = {s.name: s.step_size for s in box.slots}
    assert steps["attenuator"] > steps["theta"]

    box.select_slot(0)
    assert box.target_name() == "theta"
    assert box.step_size == steps["theta"], "step size must follow the slot"


def test_arming_more_than_max_slots_replaces_the_active_one():
    box = _box(max_slots=2)
    _arm(box, "mono", "theta")
    _arm(box, "two_theta")
    _arm(box, "energy")
    assert len(box.slots) == 2
    assert [s.name for s in box.slots] == ["theta", "energy"]


def test_menu_is_reachable_and_switches_the_stick_axis():
    box = _box()
    _arm(box, "mono", "theta")
    box.breadcrumb_jump(0)
    _arm(box, "attenuator")

    box.toggle_menu()
    assert box.in_menu and box.path_names[-1] == "menu"
    labels = [e.name for e in box.entries]
    assert any(l.startswith("step size:") for l in labels)
    assert any(l.startswith("stick axis:") for l in labels)

    box.set_cursor([i for i, l in enumerate(labels) if l.startswith("stick axis")][0])
    box.activate_cursor()
    assert [e.name for e in box.entries][0].endswith("back")
    box.set_cursor(1)  # first slot
    box.activate_cursor()
    assert box.target_name() == "theta"
    assert not box.in_menu, "choosing a slot returns to the tree"


def test_menu_long_press_goes_back_one_level_not_disarm():
    box = _box()
    _arm(box, "mono", "theta")
    box.toggle_menu()
    box.set_cursor(0)
    box.activate_cursor()  # into 'step size'
    assert len(box.path_names) >= 3
    box.encoder_long_press()
    assert box.in_menu and box.path_names[-1] == "menu"
    assert box.slots, "backing out of a submenu must not disarm"


def test_motion_mode_switches_between_jog_and_step():
    from eco.manual_control.box import MOTION_JOG, MOTION_STEP

    box = _box()
    _arm(box, "mono", "theta")
    assert box.active.motion == MOTION_JOG
    box.toggle_motion()
    assert box.active.motion == MOTION_STEP


def test_step_size_is_remembered_per_adjustable(tmp_path):
    from eco.manual_control.step_store import StepStore

    store = StepStore(path=str(tmp_path / "steps.json"))
    box = _box(step_store=store)
    _arm(box, "mono", "theta")
    box.step_up()
    chosen = box.step_size

    # a fresh box (new session) must come back with the same step
    again = _box(step_store=StepStore(path=str(tmp_path / "steps.json")))
    _arm(again, "mono", "theta")
    assert again.step_size == chosen


def test_first_step_size_comes_from_the_objects_own_tweak():
    from eco.manual_control.step_store import initial_step_size, tweak_step_size

    class FakeTweak:
        step_sizes = [0.025]

    class Adj:
        _tweak_instance = FakeTweak()

    assert tweak_step_size(Adj()) == 0.025
    assert initial_step_size(Adj(), "k", None, [0.1, 1, 10]) == 0.025
    # nothing stored and no tweak -> middle of the list
    assert initial_step_size(object(), "k", None, [0.1, 1, 10]) == 1


def test_memories_can_be_listed_recalled_and_saved_from_the_box():
    """The box only renders; the recall/save happen in the eco session."""
    recalled, saved = [], []

    class FakeMemory:
        def memories(self):
            return [{"date": "2026-09-05 11:02", "message": "aligned"},
                    {"date": "2026-09-05 12:40", "message": "beam centred"}]

        def __call__(self, index=None, **kwargs):
            recalled.append(index)

        def memorize(self, message=None, **kwargs):
            saved.append(message)

    box = _box()
    _arm(box, "mono", "theta")
    box.active.obj.memory = FakeMemory()

    assert box.has_memories()
    labels = [label for _, label in box.list_memories()]
    assert "aligned" in labels[0] and "beam centred" in labels[1]

    box.toggle_menu()
    names = [e.name for e in box.entries]
    box.set_cursor(names.index("memories"))
    box.activate_cursor()
    entries = [e.name for e in box.entries]
    assert any("aligned" in e for e in entries)

    # recalling moves hardware, so it must take a confirmation step
    box.set_cursor([i for i, e in enumerate(entries) if "aligned" in e][0])
    box.activate_cursor()
    assert recalled == [], "recall must not fire before confirmation"
    confirm = [e.name for e in box.entries]
    box.set_cursor([i for i, e in enumerate(confirm) if e.startswith("YES")][0])
    box.activate_cursor()
    assert recalled == [0]

    box.toggle_menu()
    names = [e.name for e in box.entries]
    box.set_cursor(names.index("memories"))
    box.activate_cursor()
    entries = [e.name for e in box.entries]
    box.set_cursor([i for i, e in enumerate(entries) if e.startswith("save")][0])
    box.activate_cursor()
    assert saved and "control box" in saved[0]


def test_menu_survives_an_adjustable_with_a_broken_memory():
    class Exploding:
        def memories(self):
            raise RuntimeError("memory dir unreadable")

    box = _box()
    _arm(box, "mono", "theta")
    box.active.obj.memory = Exploding()
    box.toggle_menu()
    names = [e.name for e in box.entries]
    box.set_cursor(names.index("memories"))
    box.activate_cursor()
    assert any("cannot read memories" in e.name for e in box.entries)


def test_control_box_manual_mentions_the_background_service():
    from eco.manual_control.control_box import ControlBox

    manual = ControlBox.__doc__
    for topic in ["eco-box-server", "eco-dev box-server", "box_server"]:
        assert topic in manual, f"manual does not mention {topic!r}"
