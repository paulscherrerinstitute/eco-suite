"""Driver-level tests for eco.robots (no hardware, no server).

Everything runs against eco.robots.simulation.SimulatedController, which
answers the real VAL3 line protocol. The framing assertions in
TestVal3Protocol are transcribed from captured traffic with the live Bernina
controller at 129.129.243.106:1234, so they pin the wire format rather than
just the port's self-consistency.
"""

import math

import pytest

from eco.robots import (
    BerninaRobot,
    MemoryValue,
    SimulatedController,
    Val3Error,
    Val3Link,
    Val3ProtocolError,
    cart2sph,
    connect_bernina_robot,
    sph2cart,
)
from eco.robots.protocol import BaseTransport, CancellationToken


@pytest.fixture
def robot():
    r = connect_bernina_robot(simulated=True)
    r.setup()
    r.update()
    return r


# --------------------------------------------------------------- protocol

class ScriptedTransport(BaseTransport):
    """Replays canned replies so exact framing can be asserted."""

    simulated = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self._connected = True

    def connect(self):
        self._connected = True

    def close(self):
        self._connected = False

    @property
    def connected(self):
        return self._connected

    def request(self, line, timeout):
        self.sent.append(line)
        return self.replies.pop(0)


class TestVal3Protocol:
    def test_request_framing_matches_the_controller(self):
        # Captured live: TX '000 get_status_fast None\n'
        transport = ScriptedTransport(["000 1|2|3|"])
        link = Val3Link(transport)
        assert link.execute("get_status_fast", "None") == ["1", "2", "3", ""]
        assert transport.sent == ["000 get_status_fast None\n"]

    def test_arguments_join_with_space_then_pipes(self):
        transport = ScriptedTransport(["000 ok"])
        link = Val3Link(transport)
        link.execute("get_movel_interpolation", 0.5, "joint", 0, 1)
        assert transport.sent == ["000 get_movel_interpolation 0.5|joint|0|1\n"]

    def test_message_ids_increment_and_wrap_at_1000(self):
        transport = ScriptedTransport(["%03d x" % (i % 1000) for i in range(1002)])
        link = Val3Link(transport)
        for _ in range(1002):
            link.call("noop")
        assert transport.sent[0].startswith("000 ")
        assert transport.sent[999].startswith("999 ")
        assert transport.sent[1000].startswith("000 ")   # wrapped

    def test_star_separator_raises_the_controller_error(self):
        link = Val3Link(ScriptedTransport(["000*bad symbol"]))
        with pytest.raises(Val3Error, match="bad symbol"):
            link.call("eval nonsense")

    def test_id_mismatch_is_reported_not_a_NameError(self):
        # The original referenced an undefined `start` here, so every desync
        # surfaced as NameError instead of a diagnosable protocol error.
        link = Val3Link(ScriptedTransport(["099 stale"]), retries=0)
        with pytest.raises(Val3ProtocolError, match="does not match request id"):
            link.call("get_var x")

    def test_short_reply_is_rejected(self):
        link = Val3Link(ScriptedTransport(["00"]), retries=0)
        with pytest.raises(Val3ProtocolError, match="too short"):
            link.call("get_var x")

    def test_oversized_frame_is_refused_before_sending(self):
        transport = ScriptedTransport([])
        link = Val3Link(transport)
        with pytest.raises(Val3ProtocolError, match="frame limit"):
            link.call("eval " + "x" * 200)
        assert transport.sent == []

    def test_too_many_parameters_refused(self):
        link = Val3Link(ScriptedTransport([]))
        with pytest.raises(Val3ProtocolError, match="exceeds the limit"):
            link.execute("cmd", *range(21))

    def test_padded_fields_are_stripped(self):
        # The controller pads to fixed width: '105   ', '-0.46  '
        link = Val3Link(ScriptedTransport(["000 20.8   |26.8   |-8.8   |"
                                           "-0.46  |0.46  |0     |"]))
        assert link.get_trsf("f_4mRad.trsf") == [20.8, 26.8, -8.8, -0.46, 0.46, 0.0]

    def test_empty_eval_reply_is_success(self):
        link = Val3Link(ScriptedTransport(["000 "]))
        link.evaluate("tcp_b=isPowered()")     # must not raise


class TestCancellation:
    def test_sleep_is_interruptible(self):
        import threading, time
        token = CancellationToken()
        threading.Timer(0.1, token.cancel).start()
        start = time.time()
        with pytest.raises(Exception):
            token.sleep(10)
        assert time.time() - start < 2.0

    def test_call_refuses_once_cancelled(self):
        link = Val3Link(ScriptedTransport(["000 x"]))
        link.cancellation.cancel()
        with pytest.raises(Exception):
            link.call("noop")


# ------------------------------------------------------------- kinematics

class TestKinematics:
    @pytest.mark.parametrize("gamma", [-170, -95, -90, -45, 0, 45, 90, 95, 170])
    @pytest.mark.parametrize("delta", [-89, -45, 0, 45, 89])
    def test_round_trip_preserves_position(self, gamma, delta):
        cart = sph2cart(r=1500.0, gamma=float(gamma), delta=float(delta))
        back = cart2sph(**cart)
        assert back["r"] == pytest.approx(1500.0, abs=1e-9)
        forward = sph2cart(**back)
        for axis in ("x", "y", "z"):
            assert forward[axis] == pytest.approx(cart[axis], abs=1e-6)

    def test_axis_conventions(self):
        # gamma is the horizontal angle in the x/z plane, delta the vertical.
        assert sph2cart(r=1000, gamma=0, delta=0)["z"] == pytest.approx(1000)
        assert sph2cart(r=1000, gamma=90, delta=0)["x"] == pytest.approx(1000)
        assert sph2cart(r=1000, gamma=0, delta=90)["y"] == pytest.approx(1000)

    def test_pole_does_not_raise(self):
        # The original hit `math domain error` / ZeroDivisionError here.
        for gamma in range(-180, 181, 15):
            for delta in (-90.0, -89.99, 89.99, 90.0):
                sph2cart(r=1000.0, gamma=float(gamma), delta=delta)

    def test_delta_pole_snaps_rz_to_gamma(self):
        assert sph2cart(r=1000, gamma=33.0, delta=90.0)["rz"] == pytest.approx(33.0)

    def test_gamma_undefined_on_the_y_axis_needs_a_fallback(self):
        with pytest.raises(ValueError, match="ry_fallback"):
            cart2sph(x=0.0, y=100.0, z=0.0)
        assert cart2sph(x=0.0, y=100.0, z=0.0,
                        ry_fallback=42.0)["gamma"] == pytest.approx(42.0)

    def test_rz_stays_within_a_half_turn(self):
        for gamma in range(-180, 181, 5):
            rz = sph2cart(r=1000.0, gamma=float(gamma), delta=30.0)["rz"]
            assert -180.0 <= rz <= 180.0


# ------------------------------------------------------------------ driver

class TestDriver:
    def test_status_poll_decodes_every_axis(self, robot):
        assert set(robot.joint_pos) == {"j1", "j2", "j3", "j4", "j5", "j6"}
        assert set(robot.cartesian_pos) == {"x", "y", "z", "rx", "ry", "rz"}
        assert set(robot.spherical_pos) == {"r", "gamma", "delta"}
        assert set(robot.linear_axis_pos) == {"z_lin"}

    def test_working_mode_decoding(self, robot):
        robot._update_working_mode(1, 6)
        assert (robot.working_mode, robot.status) == ("manual", "hold")
        robot._update_working_mode(4, 0)
        assert (robot.working_mode, robot.status) == ("remote", "programmed")
        robot._update_working_mode(99, 0)
        assert robot.working_mode == "invalid"

    def test_state_machine(self, robot):
        robot.settled, robot.empty, robot.current_task = True, True, None
        robot._update_state()
        assert str(robot.state) == "Ready"
        robot.settled = False
        robot._update_state()
        assert str(robot.state) == "Busy"
        robot.settled, robot.empty = True, False
        robot._update_state()
        assert str(robot.state) == "Paused"

    def test_manual_jog_discards_queued_motions(self, robot):
        seen = []
        robot.add_listener(lambda k, v: seen.append(k))
        robot.on_change_status("joint", "hold")
        assert "reset_motion" in seen

    def test_setup_enables_all_motor_groups(self, robot):
        assert sorted(robot.motors) == sorted(
            ["x", "y", "z", "rx", "ry", "rz", "r", "gamma", "delta",
             "j1", "j2", "j3", "j4", "j5", "j6", "z_lin"])

    def test_disabling_a_group_clears_its_destination(self, robot):
        robot.set_motors_enabled(False, ["cartesian"])
        assert robot.motor_groups["cartesian"] == {}
        # The original assigned [] here while treating it as a dict elsewhere.
        assert robot.cartesian_destination is None

    def test_is_in_points_handles_unknown_distances(self, robot):
        robot.get_distance_to_pnts = lambda *p: [None, -1.0, 1.0]
        assert robot.is_in_points("a", "b", "c", tolerance=5) == [None, None, True]

    def test_take_returns_the_broadcast_payload(self, robot):
        payload = robot.take()
        assert "pos" in payload and "mode" in payload and "robot_state" in payload


# ------------------------------------------------------------------ motors

class TestPseudoMotors:
    def test_single_axis_write_keeps_the_other_setpoints_live(self, robot):
        robot.last_remote_motion = "spherical"
        for m in robot.motor_groups["spherical"].values():
            m.initialize()
        robot.motors["gamma"].move(robot.spherical_pos["gamma"] + 20)
        target = robot.motors["gamma"].target_pos
        assert target["gamma"] == pytest.approx(robot.spherical_pos["gamma"] + 20)

    def test_second_same_system_write_merges_into_one_move(self, robot):
        seen = []
        robot.add_listener(lambda k, v: seen.append((k, str(v))))
        robot.last_remote_motion = "spherical"
        for m in robot.motor_groups["spherical"].values():
            m.initialize()
        base = dict(robot.spherical_pos)
        robot.motors["gamma"].move(base["gamma"] + 20)
        robot.motors["delta"].move(base["delta"] + 20)
        assert any("Combining 2 motions" in v for k, v in seen if k == "motion")

    def test_merge_does_not_lose_the_setpoints_being_merged(self, robot):
        # Regression: using the full reset_motion() here re-seeds every
        # setpoint from the readback and destroys the merge in progress.
        robot.last_remote_motion = "spherical"
        for m in robot.motor_groups["spherical"].values():
            m.initialize()
        base = dict(robot.spherical_pos)
        robot.motors["gamma"].move(base["gamma"] + 20)
        robot.motors["delta"].move(base["delta"] + 20)
        merged = robot.motors["delta"].target_pos
        assert merged["gamma"] == pytest.approx(base["gamma"] + 20)
        assert merged["delta"] == pytest.approx(base["delta"] + 20)

    def test_switching_coordinate_system_announces_itself(self, robot):
        seen = []
        robot.add_listener(lambda k, v: seen.append((k, str(v))))
        robot.last_remote_motion = "spherical"
        robot.motors["x"].move(robot.cartesian_pos["x"] + 10)
        assert any("changed coordinate system" in v for k, v in seen
                   if k == "motion")

    def test_motors_use_their_own_robot_not_a_global(self, robot):
        other = connect_bernina_robot(simulated=True)
        other.setup(); other.update()
        assert robot.motors["gamma"].robot is robot
        assert other.motors["gamma"].robot is other


# ------------------------------------------------------------------ motion

class TestMotion:
    def test_spherical_move_is_a_radial_leg_plus_an_arc(self, robot):
        robot.link.transport.log.clear()
        robot.move_spherical(r=2500, gamma=60, delta=10)
        issued = [a[0] for c, a in robot.link.transport.log
                  if c == "eval" and "move" in a[0]]
        assert any("movel(tcp_p_spherical[1]" in s for s in issued)
        assert any("movec(tcp_p_spherical[2], tcp_p_spherical[3]" in s
                   for s in issued)

    def test_simulate_returns_interpolated_poses_and_does_not_move(self, robot):
        robot.link.transport.log.clear()
        poses = robot.move_cartesian(x=2000, simulate=True)
        assert len(poses) == 11 and len(poses[0]) == 6
        assert not any("movel(" in a[0] for c, a in robot.link.transport.log
                       if c == "eval")

    def test_simulation_is_not_blocked_by_the_remote_whitelist(self, robot):
        # `& (~simulate)` in the original was always truthy, so simulating a
        # move in remote mode ran the safety check and returned False.
        robot.working_mode = "remote"
        assert robot.move_cartesian(x=2000, simulate=True) is not False

    def test_general_motion_rejects_mixed_coordinate_systems(self, robot):
        seen = []
        robot.add_listener(lambda k, v: seen.append(str(v)))
        assert robot.general_motion(x=1.0, gamma=2.0) is None
        assert any("No unique coordinate system" in s for s in seen)

    def test_general_motion_picks_the_right_system(self, robot):
        assert robot._dispatch_motion({"gamma": 1})[0] == "spherical"
        assert robot._dispatch_motion({"x": 1})[0] == "cartesian"
        assert robot._dispatch_motion({"j4": 1})[0] == "joint"


# ------------------------------------------------------------ remote safety

class TestRemoteSafety:
    def test_remote_motion_is_refused_without_a_recording(self, robot):
        robot.working_mode = "remote"
        assert robot.move_spherical(r=2600, gamma=61, delta=11) is False

    def test_refusal_tells_the_operator_what_to_do(self, robot):
        seen = []
        robot.add_listener(lambda k, v: seen.append(str(v)))
        robot.working_mode = "remote"
        robot.move_spherical(r=2600, gamma=61, delta=11)
        assert any("record_motion" in s for s in seen)

    def test_override_permits_everything(self, robot):
        robot.working_mode = "remote"
        robot.set_override_remote_safety(True)
        assert robot.move_spherical(r=2600, gamma=61, delta=11) is not False

    def test_recording_then_replaying_is_allowed(self, robot):
        robot.working_mode = "manual"
        robot.record_motion(gamma=70, delta=15, r=2700)
        path = robot.remote_allowed_recorded()["spherical"]["pos"][-1]
        start = dict(zip(["r", "gamma", "delta"], path[0]))
        end = dict(zip(["r", "gamma", "delta"], path[-1]))
        assert robot.remote_allowed(start, end, robot.frame_trsf,
                                    robot.tool_trsf, "spherical") is True

    def test_a_target_off_the_recorded_path_is_still_refused(self, robot):
        robot.working_mode = "manual"
        robot.record_motion(gamma=70, delta=15, r=2700)
        path = robot.remote_allowed_recorded()["spherical"]["pos"][-1]
        start = dict(zip(["r", "gamma", "delta"], path[0]))
        far = dict(start, gamma=start["gamma"] + 40)
        assert robot.remote_allowed(start, far, robot.frame_trsf,
                                    robot.tool_trsf, "spherical") is False

    def test_a_recording_taken_in_another_frame_does_not_count(self, robot):
        robot.working_mode = "manual"
        robot.record_motion(gamma=70, delta=15, r=2700)
        path = robot.remote_allowed_recorded()["spherical"]["pos"][-1]
        start = dict(zip(["r", "gamma", "delta"], path[0]))
        end = dict(zip(["r", "gamma", "delta"], path[-1]))
        assert robot.remote_allowed(start, end, [999.0] * 6,
                                    robot.tool_trsf, "spherical") is False

    def test_frame_orientation_is_compared_too(self, robot):
        # The original passed three motion tolerances for six frame
        # components, so a frame differing only in rx/ry/rz still matched.
        robot.working_mode = "manual"
        robot.record_motion(gamma=70, delta=15, r=2700)
        path = robot.remote_allowed_recorded()["spherical"]["pos"][-1]
        start = dict(zip(["r", "gamma", "delta"], path[0]))
        end = dict(zip(["r", "gamma", "delta"], path[-1]))
        rotated = list(robot.frame_trsf[:3]) + [90.0, 0.0, 0.0]
        assert robot.remote_allowed(start, end, rotated,
                                    robot.tool_trsf, "spherical") is False

    def test_joint_recordings_work(self, robot):
        # The original passed frame_trsf=None for joint motions and then
        # subtracted it from an array: TypeError with a recording present,
        # and an unconditional refusal without one.
        robot.working_mode = "manual"
        robot.record_motion(j1=robot.joint_pos["j1"] - 8)
        path = robot.remote_allowed_recorded()["joint"]["pos"][-1]
        axes = ["j1", "j2", "j3", "j4", "j5", "j6"]
        assert robot.remote_allowed(dict(zip(axes, path[0])),
                                    dict(zip(axes, path[-1])),
                                    None, None, "joint") is True

    def test_recordings_do_not_leak_between_coordinate_systems(self, robot):
        # The original aliased one dict across all three systems, so a
        # spherical recording also registered as cartesian and joint.
        robot.working_mode = "manual"
        robot.record_motion(gamma=70)
        counts = {k: len(v["pos"])
                  for k, v in robot.remote_allowed_recorded().items()}
        assert counts == {"spherical": 1, "cartesian": 0, "joint": 0}

    def test_reset_recorded_motions_by_index(self, robot):
        # The original wrote `.pop[index]`, a TypeError on every call.
        robot.working_mode = "manual"
        robot.record_motion(gamma=70)
        robot.reset_recorded_motions(index=0, motion="spherical")
        assert robot.remote_allowed_recorded()["spherical"]["pos"] == []

    def test_reset_recorded_motions_clears_everything(self, robot):
        robot.working_mode = "manual"
        robot.record_motion(gamma=70)
        robot.reset_recorded_motions()
        assert all(v["pos"] == []
                   for v in robot.remote_allowed_recorded().values())


# ------------------------------------------------------------- persistence

class TestPersistence:
    def test_simulated_robots_do_not_touch_the_shared_files(self):
        r = connect_bernina_robot(simulated=True)
        assert isinstance(r.frame, MemoryValue)

    def test_write_on_change_only(self, tmp_path):
        from eco.robots.persistence import JsonValue
        value = JsonValue(name="frame", default_value="f_4mRad",
                          base_path=tmp_path)
        assert value.set_if_changed("f_4mRad") is False   # no rewrite
        assert value.set_if_changed("f_2mRad") is True

    def test_writes_are_atomic(self, tmp_path):
        from eco.robots.persistence import JsonValue
        value = JsonValue(name="v", default_value=1, base_path=tmp_path)
        value.write_value({"big": list(range(1000))})
        # No temp files left behind, and the file parses.
        assert value.get_current_value()["big"][-1] == 999
        assert [p.name for p in tmp_path.iterdir()] == ["v"]
