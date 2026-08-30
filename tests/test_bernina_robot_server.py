"""Server-level tests for eco.bernina_robot_server.

Runs the whole app against a simulated controller, through Flask's test
client, and asserts the pshell-compatible contract that eco's
StaeubliTx200 / PshellMotor depend on. If these pass, pointing
`bernina.rob`'s pshell_url at this server needs no eco-side change.
"""

import json
import threading
import time

import pytest

from eco.bernina_robot_server import RobotServerApp, RobotServerConfig, create_app


@pytest.fixture(scope="module")
def app():
    config = RobotServerConfig(simulated=True, epics_enabled=False,
                              polling_interval=0.05, env_polling_interval=0.1)
    server_app = RobotServerApp(config).start()
    yield server_app
    server_app.stop()


@pytest.fixture
def client(app):
    return create_app(app).test_client()


def submit(client, statement, timeout=5.0):
    """The eval/poll cycle StaeubliTx200.get_eval_result performs."""
    command_id = int(client.get(f"/evalAsync/{statement}").data)
    start = time.time()
    while True:
        result = client.get(f"/result/{command_id}").get_json()
        if result["status"] != "running":
            return result
        if time.time() - start > timeout:
            raise AssertionError(f"timeout on {statement}")
        time.sleep(0.02)


# ------------------------------------------------- pshell-compatible surface

class TestPshellContract:
    def test_version_is_plain_text(self, client):
        assert client.get("/version").data.decode()

    def test_state_is_a_json_string(self, client):
        assert client.get("/state").get_json() in (
            "Ready", "Busy", "Fault", "Initializing")

    def test_devices_returns_the_pshell_five_tuple(self, client):
        rows = client.get("/devices").get_json()
        assert all(len(row) == 5 for row in rows)
        assert rows[0][0] == "robot"

    def test_evalAsync_returns_an_integer_id(self, client):
        assert int(client.get("/evalAsync/robot.doUpdate()").data) > 0

    def test_result_carries_the_pshell_field_names(self, client):
        result = submit(client, "robot.on_poll_info()")
        assert set(result) >= {"id", "status", "return", "exception"}
        assert result["status"] == "completed"

    def test_unknown_command_id_is_a_404_not_a_crash(self, client):
        assert client.get("/result/999999").status_code == 404

    def test_failed_command_reports_the_exception(self, client):
        result = submit(client, "robot.no_such_method()")
        assert result["status"] == "failed"
        assert "AttributeError" in result["exception"]

    def test_blocking_eval_returns_text(self, client):
        assert client.get("/eval/robot.get_frame()").data.decode() == "f_4mRad"

    def test_config_endpoint(self, client):
        assert client.get("/config").get_json()["robot_port"] == 1234


class TestEvalNamespace:
    def test_robot_is_reachable(self, client):
        assert submit(client, "robot.get_tool()")["return"] == "t_JF01T03"

    def test_poll_info_shape_drives_the_eco_config_object(self, client):
        info = submit(client, "robot.on_poll_info()")["return"]
        assert set(info) == {"info", "config"}
        # eco renders each as cmd(<value>, <def_kwargs>)
        for spec in info["config"].values():
            assert set(spec) == {"cmd", "def_kwargs"}
            assert spec["cmd"].startswith("robot.")
        assert "frame" in info["config"] and "tool" in info["config"]

    def test_motors_are_reachable_by_bare_axis_name(self, client):
        # PshellMotor sends `<name>.moveAsync(<value>)` and `<name>.stop()`
        for axis in ("gamma", "delta", "r", "x", "j1", "z_lin"):
            result = submit(client, f"{axis}.get_readback()")
            assert result["status"] == "completed"
            assert isinstance(result["return"], float)

    def test_motion_script_functions_exist(self, client):
        for name in ("move_home", "move_park", "tweak_x", "tweak_y"):
            assert submit(client, f"callable({name})")["return"] is True

    def test_kwargs_splatting_works(self, client):
        # eco literally sends: robot.general_motion(**{'gamma': 20})
        result = submit(client, "robot.cart2sph(**{'x': 0.0, 'y': 0.0, 'z': 1000.0})")
        assert result["return"]["r"] == pytest.approx(1000.0)

    def test_dangerous_builtins_are_not_reachable(self, client):
        for statement in ("__import__('os').system('true')",
                          "open('/etc/passwd').read()"):
            assert submit(client, statement)["status"] == "failed"


class TestBusyGating:
    def test_foreground_command_sets_busy_and_blocks_a_second(self, client):
        command_id = int(client.get("/evalAsync/robot.cancellation.sleep(5)").data)
        time.sleep(0.3)
        try:
            assert client.get("/state").get_json() == "Busy"
            second = submit(client, "robot.doUpdate()")
            assert second["status"] == "failed"
            assert "busy" in second["exception"]
        finally:
            client.get("/eval/:abort")
        for _ in range(100):
            if client.get(f"/result/{command_id}").get_json()["status"] != "running":
                break
            time.sleep(0.02)
        assert client.get(f"/result/{command_id}").get_json()["status"] == "aborted"

    def test_background_commands_run_while_busy(self, client):
        command_id = int(client.get("/evalAsync/robot.cancellation.sleep(5)").data)
        time.sleep(0.3)
        try:
            assert submit(client, "robot.doUpdate()&")["status"] == "completed"
        finally:
            client.get("/eval/:abort")
            time.sleep(0.3)

    def test_abort_is_prompt(self, client):
        int(client.get("/evalAsync/robot.cancellation.sleep(30)").data)
        time.sleep(0.2)
        start = time.time()
        client.get("/eval/:abort")
        while client.get("/state").get_json() == "Busy" and time.time() - start < 5:
            time.sleep(0.02)
        assert time.time() - start < 2.0
        assert client.get("/state").get_json() == "Ready"


class TestEvents:
    def test_polling_payload_has_what_eco_caches(self, app):
        subscriber = app.events.subscribe("test", ["polling"])
        try:
            frame = subscriber.queue.get(timeout=5)
        finally:
            app.events.unsubscribe(subscriber)
        payload = json.loads(frame.split("data: ", 1)[1])
        assert set(payload) >= {"pos", "mode", "status", "frame", "tool",
                                "powered", "speed", "connected"}
        assert set(payload["pos"]) >= {"x", "gamma", "j1", "z_lin"}

    def test_sse_framing(self, app):
        subscriber = app.events.subscribe("test", ["motion"])
        try:
            app.events.publish("motion", "hello")
            frame = subscriber.queue.get(timeout=2)
        finally:
            app.events.unsubscribe(subscriber)
        assert frame.startswith("event: motion\ndata: ")
        assert frame.endswith("\n\n")

    def test_state_event_carries_the_server_state_not_the_robot_state(self, app):
        subscriber = app.events.subscribe("test", ["state"])
        try:
            subscriber.queue.get(timeout=2)          # replayed current state
            app.set_state("Busy")
            frame = subscriber.queue.get(timeout=2)
            app.set_state("Ready")
        finally:
            app.events.unsubscribe(subscriber)
        assert json.loads(frame.split("data: ", 1)[1]) == "Busy"

    def test_a_slow_subscriber_never_blocks_the_publisher(self, app):
        subscriber = app.events.subscribe("test", ["motion"])
        try:
            start = time.time()
            for i in range(5000):            # far beyond the queue bound
                app.events.publish("motion", i)
            assert time.time() - start < 5.0
            assert subscriber.dropped > 0
        finally:
            app.events.unsubscribe(subscriber)

    def test_new_subscriber_gets_the_last_value_immediately(self, app):
        app.events.publish("motion", "latest")
        subscriber = app.events.subscribe("late", ["motion"])
        try:
            frame = subscriber.queue.get(timeout=1)
        finally:
            app.events.unsubscribe(subscriber)
        assert "latest" in frame


class TestStructuredApi:
    def test_status(self, client):
        status = client.get("/api/status").get_json()
        assert status["robot"]["simulated"] is True
        assert len(status["motors"]) == 16

    def test_health(self, client):
        assert client.get("/api/health").get_json()["healthy"] is True

    def test_positions(self, client):
        positions = client.get("/api/positions").get_json()
        assert set(positions) >= {"x", "y", "z", "gamma", "delta", "r",
                                  "j1", "z_lin"}

    def test_move_without_eval(self, client):
        result = client.post("/api/move", json={"gamma": 40.0, "wait": True})
        assert result.get_json()["status"] == "completed"

    def test_move_rejects_an_empty_body(self, client):
        assert client.post("/api/move", json={}).status_code == 400

    def test_subscriber_stats(self, client):
        assert isinstance(client.get("/api/subscribers").get_json(), list)


class TestConcurrency:
    def test_polling_and_commands_share_the_link_safely(self, app):
        """The poller runs continuously while commands hammer the link."""
        errors = []

        def worker():
            try:
                for _ in range(40):
                    app.robot.get_cartesian_pos()
                    app.robot.get_joint_pos()
                    app.robot.update()
            except Exception as exc:       # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert not errors
        # Every reply matched its request: a desync would have raised.
        assert app.robot.connected

    def test_transaction_serialises_a_multi_call_sequence(self, app):
        """A move's scratch-point writes must not interleave with the poller."""
        robot = app.robot
        seen_interleaved = []

        original = robot.link.transport.request
        depth = {"in_transaction": False}

        def spy(line, timeout):
            if "tcp_p_spherical[0]" in line:
                depth["in_transaction"] = True
            if depth["in_transaction"] and "get_status_fast" in line:
                seen_interleaved.append(line)
            if "movec" in line or "movel(tcp_p_spherical[3]" in line:
                depth["in_transaction"] = False
            return original(line, timeout)

        robot.link.transport.request = spy
        try:
            for _ in range(5):
                robot.move_spherical(r=2500, gamma=55, delta=8)
        finally:
            robot.link.transport.request = original
        assert seen_interleaved == [], (
            "the poller slipped into a motion's scratch-point sequence")
