"""eco.web.client_session: the protocol, without a transport -- requests
answered off the caller's thread, coalesced values, writes only when
enabled, Stop during a move."""
import queue
import time

import pytest

from eco.web.backends import SessionBackend
from eco.web.client_session import ClientSession
from eco.web.demo import DemoNamespace


class Client:
    def __init__(self, writes=False):
        self.messages = queue.Queue()
        self.session = ClientSession(SessionBackend(DemoNamespace()), self.messages.put,
                                     writes=writes, poll_interval=0.05)
        self.next_id = 0
        self.backlog = []  # events that arrived while waiting for a reply

    def call(self, op, timeout=5.0, **args):
        self.next_id += 1
        request_id = self.next_id
        self.session.handle({"v": 0, "id": request_id, "op": op, "args": args})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.messages.get(timeout=timeout)
            if message.get("re") == request_id:
                return message
            if "ev" in message:
                self.backlog.append(message)
        raise AssertionError(f"no reply to {op}")

    def events(self, kind, seconds=1.0):
        found = [m["data"] for m in self.backlog if m["ev"] == kind]
        self.backlog = [m for m in self.backlog if m["ev"] != kind]
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=0.05)
            except queue.Empty:
                continue
            if message.get("ev") == kind:
                found.append(message["data"])
        return found

    def values(self, name, seconds=2.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            event = self.session.flush()
            if event and name in event["data"]:
                return event["data"][name]
            time.sleep(0.02)
        raise AssertionError(f"no value for {name}")


@pytest.fixture
def client():
    c = Client()
    yield c
    c.session.close()


@pytest.fixture
def writer():
    c = Client(writes=True)
    yield c
    c.session.close()


def test_hello_and_names(client):
    hello = client.call("hello", client_version="t")
    assert hello["ok"] and hello["data"]["writes"] is False and hello["data"]["label"] == "demo"
    names = client.call("names")["data"]
    assert "mono" in names["initialized"]


def test_unknown_op_and_bad_arguments_are_errors(client):
    reply = client.call("frobnicate")
    assert not reply["ok"] and reply["error"]["type"] == "ProtocolError"
    reply = client.call("describe", nonsense=1)
    assert not reply["ok"] and reply["error"]["type"] == "ProtocolError"


def test_subscribe_resolves_names_not_seen_yet(client):
    # a pinned item after a page reload: never described in this session
    assert client.call("subscribe", names=["mono.energy"])["ok"]
    assert client.values("mono.energy")["num"] == 7.0
    reply = client.call("subscribe", names=["mono.nothing"])
    assert not reply["ok"] and reply["error"]["type"] == "NotFound"


def test_search_finds_readable_items_of_initialized_components(client):
    hits = client.call("search", query="energy")["data"]["hits"]
    assert {"name": "mono.energy", "kind": "adjustable"} in hits
    names = [h["name"] for h in client.call("search", query="mono pitch")["data"]["hits"]]
    assert names == ["mono.pitch_rbv"]
    everything = [h["name"] for h in client.call("search", query="", limit=500)["data"]["hits"]]
    assert "lxt" in everything and not any(n.startswith("vacuum.") for n in everything)  # lazy: not searched
    assert client.call("subscribe", names=["mono.pitch_rbv"])["ok"]  # a hit can be watched at once


def test_details_reuse_the_grids_item_menu(client):
    rows = dict(client.call("details", name="mono.energy")["data"]["rows"])
    assert rows["name"] == "mono.energy" and rows["kind"] == "Adjustable"
    assert rows["unit"] == "keV" and "SimMotor" in rows["class"]


def test_values_arrive_coalesced_after_subscribe(client):
    client.call("describe", name="mono")
    assert client.call("subscribe", names=["mono.energy", "mono.pitch_rbv"])["ok"]
    value = client.values("mono.energy")
    assert value["num"] == 7.0 and value["state"] == "polled" and value["t"] > 0
    time.sleep(0.3)  # several polls at 0.05 s ...
    event = client.session.flush()
    assert event["ev"] == "values" and "now" in event
    assert set(event["data"]) <= {"mono.energy", "mono.pitch_rbv"}  # ... one entry each


def test_unsubscribed_items_stop_reporting(client):
    client.call("describe", name="mono")
    client.call("subscribe", names=["mono.pitch_rbv"])
    client.values("mono.pitch_rbv")
    client.call("unsubscribe", names=["mono.pitch_rbv"])
    time.sleep(0.2)
    client.session.flush()
    time.sleep(0.3)
    event = client.session.flush()
    assert event is None or "mono.pitch_rbv" not in event["data"]


def test_writes_are_refused_by_default(client):
    client.call("describe", name="mono")
    for op, args in [("set", {"value": 8}), ("step", {"sign": 1, "step": 0.1}), ("stop", {}), ("trigger", {})]:
        name = "mono.home_all" if op == "trigger" else "mono.energy"
        reply = client.call(op, name=name, **args)
        assert not reply["ok"] and reply["error"]["type"] == "WritesDisabled", op
    rows = client.call("describe", name="mono")["data"]["rows"]
    assert not any(r["writable"] for r in rows)


def test_set_reports_moving_then_done(writer):
    writer.call("describe", name="lxt")
    writer.call("subscribe", names=["lxt"])
    writer.values("lxt")
    assert writer.call("set", name="lxt", value="1e-13")["ok"]
    states = [e["state"] for e in writer.events("write", seconds=1.0)]
    assert states[:2] == ["busy", "moving"] and states[-1] == "idle"


def test_set_errors_come_back_as_events(writer):
    writer.call("describe", name="mono")
    writer.call("subscribe", names=["mono.energy"])
    writer.values("mono.energy")
    writer.call("set", name="mono.energy", value=99)  # outside the simulated limits
    errors = [e for e in writer.events("write", seconds=0.5) if e["state"] == "error"]
    assert errors and "outside limits" in errors[0]["message"]


def test_stop_during_a_move(writer):
    writer.call("describe", name="slits")
    writer.call("subscribe", names=["slits.hgap"])
    writer.values("slits.hgap")
    writer.call("set", name="slits.hgap", value=9.0)  # 16 s at 0.5 mm/s
    time.sleep(0.3)
    assert writer.call("stop", name="slits.hgap")["ok"]
    states = [e["state"] for e in writer.events("write", seconds=1.0)]
    assert states[-1] == "idle"
    assert writer.session._objects["slits.hgap"].get_current_value() < 2.0


def test_step_and_enum_set(writer):
    writer.call("describe", name="vacuum")
    writer.call("subscribe", names=["vacuum.valve3"])
    assert writer.values("vacuum.valve3")["text"] == "closed"
    writer.call("set", name="vacuum.valve3", value="open")
    writer.events("write", seconds=1.2)
    assert writer.session._objects["vacuum.valve3"].get_current_value().name == "open"

    writer.call("describe", name="mono")
    step = writer.call("step_size", name="mono.harmonic")["data"]["step"]
    assert step == 1
    writer.call("step", name="mono.harmonic", sign=1, step=step)
    writer.events("write", seconds=0.3)
    assert writer.session._objects["mono.harmonic"].get_current_value() == 4


def test_writes_can_be_limited_to_names():
    c = Client(writes={"mono.harmonic"})
    try:
        rows = {r["name"]: r["writable"] for r in c.call("describe", name="mono")["data"]["rows"]}
        assert rows["mono.harmonic"] and not rows["mono.energy"]
        assert c.call("set", name="mono.energy", value=8)["error"]["type"] == "WritesDisabled"
    finally:
        c.session.close()


def test_drill_down_and_move_in_a_nested_device(writer):
    rows = writer.call("describe", name="sample")["data"]["rows"]
    assert any(r["name"] == "sample.gonio" and r["kind"] == "assembly" for r in rows)
    inner = writer.call("describe", name="sample.gonio")["data"]  # a row name of the parent view
    assert [r["name"] for r in inner["rows"]] == ["sample.gonio.omega", "sample.gonio.chi", "sample.gonio.phi"]
    writer.call("subscribe", names=["sample.gonio.omega"])
    writer.values("sample.gonio.omega")
    writer.call("set", name="sample.gonio.omega", value=1.0)
    assert [e["state"] for e in writer.events("write", seconds=1.0)][-1] == "idle"
    assert writer.session._objects["sample.gonio.omega"].get_current_value() == 1.0


# -- jog -------------------------------------------------------------------------------


def _x(client):
    client.call("describe", name="stage")
    return client.session._objects["stage.x"]


def test_jog_moves_while_confirmed_and_stops_on_release(writer):
    x = _x(writer)
    start = x.get_current_value()
    assert writer.call("jog", name="stage.x", direction=1)["ok"]
    for _ in range(4):
        time.sleep(0.2)
        writer.call("jog", name="stage.x", direction=1)  # the held button repeats
    assert x._jog is not None and x.get_current_value() > start
    assert writer.call("jog", name="stage.x", direction=1, start=False)["ok"]
    assert x._jog is None
    states = [e["state"] for e in writer.events("write", 0.3) if e["name"] == "stage.x"]
    assert states[0] == "moving" and states[-1] == "idle"


def test_jog_stops_without_confirmation(writer, monkeypatch):
    # the phone locked or the tunnel died: no release ever arrives
    from eco.web import client_session

    monkeypatch.setattr(client_session, "JOG_DEADMAN", 0.3)
    x = _x(writer)
    writer.call("jog", name="stage.x", direction=-1)
    assert x._jog is not None
    time.sleep(0.7)
    assert x._jog is None
    events = [e for e in writer.events("write", 0.2) if e["name"] == "stage.x"]
    assert "no signal" in events[-1]["message"]


def test_closing_the_session_stops_a_jog(writer):
    x = _x(writer)
    writer.call("jog", name="stage.x", direction=1)
    writer.session.close()
    assert x._jog is None


def test_stop_ends_a_jog(writer):
    x = _x(writer)
    writer.call("jog", name="stage.x", direction=1)
    writer.call("stop", name="stage.x")
    assert x._jog is None


def test_jog_needs_writes_and_a_hardware_jog(client, writer):
    x = _x(client)
    reply = client.call("jog", name="stage.x", direction=1)
    assert not reply["ok"] and reply["error"]["type"] == "WritesDisabled" and x._jog is None
    reply = writer.call("jog", name="mono.energy", direction=1)
    assert not reply["ok"] and "no hardware jog" in reply["error"]["message"]


def test_jog_goes_through_the_access_and_claim_gates(writer, monkeypatch):
    from eco.devices_general import utilities

    def deny(parent):
        raise PermissionError("blocked by a queue task")

    monkeypatch.setattr(utilities, "_claim_gate", deny)
    x = _x(writer)
    reply = writer.call("jog", name="stage.x", direction=1)
    assert not reply["ok"] and "queue task" in reply["error"]["message"] and x._jog is None


def test_jog_speed(writer):
    x = _x(writer)
    assert writer.call("jog_speed", name="stage.x", value=0.8)["ok"]
    deadline = time.monotonic() + 2
    while x.jog_speed.get_current_value() != 0.8 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert x.jog_speed.get_current_value() == 0.8
    assert not writer.call("jog_speed", name="stage.x", value=0)["ok"]
