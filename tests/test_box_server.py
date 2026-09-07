"""Tests for eco.manual_control.box_server: the background service that
holds a console's connection to the physical control box.

Uses the Flask test client (no HTTP socket) against a real BoxListener on
loopback, exactly like tests/test_manual_control_box.py's own link tests -
the box side is real, only the socket the test client talks to is faked by
Flask.
"""

import threading
import time

import pytest

flask = pytest.importorskip("flask")

from eco.manual_control.box_server import BoxServerState, create_box_app
from eco.manual_control.demo import build_fake_beamline
from eco.manual_control.remote.box_link import BoxListener
from eco.manual_control.remote.client import RemoteControlClient
from eco.utilities.config import Namespace

TOKEN = "box-server-test-token"


def _listener(port, token=TOKEN):
    return BoxListener(port=port, token=token, bind="127.0.0.1")


def _next_request(listener, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        request = listener.pending()
        if request is not None:
            return request
        time.sleep(0.02)
    return None


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _fresh_namespace():
    from eco.elements.adjustable import DummyAdjustable

    ns = Namespace(name="bernina_test")
    ns.append_obj(DummyAdjustable, name="theta", module_name=None)
    return ns


def test_health_before_any_connection_reports_idle():
    ns = _fresh_namespace()
    state = BoxServerState(ns, "127.0.0.1", 8760, TOKEN, None)
    app = create_box_app(ns, state)
    resp = app.test_client().get("/health")
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["state"] == "idle"
    assert body["connected"] is False
    assert body["namespace"] == "bernina_test"
    assert body["box"] == "127.0.0.1:8760"


def test_health_reflects_a_real_accepted_connection():
    listener = _listener(8759)
    ns = _fresh_namespace()
    state = BoxServerState(ns, "127.0.0.1", 8759, TOKEN, None)
    app = create_box_app(ns, state)
    client = app.test_client()
    try:
        resp = client.get("/health")
        assert resp.get_json()["state"] == "idle"

        state.connect()  # what main() does at startup
        assert _wait(lambda: state._session() is not None)
        assert _wait(lambda: state._session().state == "waiting for the operator")
        assert client.get("/health").get_json()["state"] == "waiting for the operator"

        request = _next_request(listener)
        assert request is not None
        box_client = RemoteControlClient(request.accept()).start()
        assert _wait(lambda: bool(box_client.entries))
        assert _wait(lambda: state.connected)

        body = client.get("/health").get_json()
        assert body["state"] == "connected"
        assert body["connected"] is True
    finally:
        ns.stop_eco_control_box()
        listener.close()


def test_admin_disconnect_releases_the_box_and_it_is_reported():
    """The safety button: /admin/disconnect must actually drop the link, and
    the box side must notice (fall back to waiting), not just the console."""
    listener = _listener(8758)
    ns = _fresh_namespace()
    state = BoxServerState(ns, "127.0.0.1", 8758, TOKEN, None)
    app = create_box_app(ns, state)
    client = app.test_client()
    try:
        state.connect()
        request = _next_request(listener)
        box_client = RemoteControlClient(request.accept()).start()
        assert _wait(lambda: bool(box_client.entries))
        assert _wait(lambda: state.connected)

        resp = client.post("/admin/disconnect")
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["connected"] is False
        assert "disconnect" in body["message"]

        # the box side must see this too, not just the console bookkeeping
        assert _wait(lambda: not box_client.connected), \
            "the box was not told the session disconnected"

        # health afterwards still reflects "not connected", not a crash
        assert client.get("/health").get_json()["connected"] is False
    finally:
        ns.stop_eco_control_box()
        listener.close()


def test_admin_reconnect_offers_a_new_session_that_the_box_can_accept():
    listener = _listener(8757)
    ns = _fresh_namespace()
    state = BoxServerState(ns, "127.0.0.1", 8757, TOKEN, None)
    app = create_box_app(ns, state)
    client = app.test_client()
    try:
        # nothing connected yet - /admin/reconnect must offer a session
        resp = client.post("/admin/reconnect")
        assert resp.get_json()["state"] in ("dialling", "waiting for the operator")

        request = _next_request(listener)
        assert request is not None, "/admin/reconnect did not call the box"
        box_client = RemoteControlClient(request.accept()).start()
        assert _wait(lambda: bool(box_client.entries))
        assert _wait(lambda: state.connected)
    finally:
        ns.stop_eco_control_box()
        listener.close()


def test_admin_reconnect_is_idempotent_while_already_connecting():
    """Calling reconnect twice in a row must not open two competing sessions
    - Namespace.start_eco_control_box() is already idempotent; this checks
    box_server does not bypass that."""
    listener = _listener(8756)
    ns = _fresh_namespace()
    state = BoxServerState(ns, "127.0.0.1", 8756, TOKEN, None)
    app = create_box_app(ns, state)
    client = app.test_client()
    try:
        client.post("/admin/reconnect")
        first_session = ns._control_box_server
        client.post("/admin/reconnect")
        assert ns._control_box_server is first_session
    finally:
        ns.stop_eco_control_box()
        listener.close()


def test_admin_restart_disconnects_then_execs(monkeypatch):
    """Mirrors test_status_server.py's restart test: the listening socket
    must be closed before os.execv (werkzeug marks it inheritable), and -
    unlike the plain status server - the box must be told to disconnect
    first, so a restarted process does not leave the box thinking it is
    still connected to a session that is about to vanish."""
    import types

    from eco.manual_control import box_server as box_server_mod

    listener = _listener(8755)
    ns = _fresh_namespace()
    state = BoxServerState(ns, "127.0.0.1", 8755, TOKEN, None)
    holder = {}
    app = create_box_app(ns, state, wsgi_server_holder=holder)
    client = app.test_client()
    try:
        state.connect()
        request = _next_request(listener)
        box_client = RemoteControlClient(request.accept()).start()
        assert _wait(lambda: bool(box_client.entries))
        assert _wait(lambda: state.connected)

        closed = []
        execd = []
        holder["server"] = types.SimpleNamespace(server_close=lambda: closed.append(True))
        monkeypatch.setattr(box_server_mod.os, "execv", lambda exe, argv: execd.append(argv))
        monkeypatch.setattr(box_server_mod.time, "sleep", lambda s: None)

        resp = client.post("/admin/restart", json={"delay": 0})
        assert resp.status_code == 202

        assert _wait(lambda: bool(execd), timeout=5)
        assert closed == [True]
        assert _wait(lambda: not box_client.connected), \
            "restart must disconnect the box before re-exec'ing"
    finally:
        ns.stop_eco_control_box()
        listener.close()


def test_health_survives_no_control_box_attribute_yet():
    """A namespace that has never called start/stop_eco_control_box (the
    attribute genuinely does not exist) must not crash /health."""
    ns = _fresh_namespace()
    assert not hasattr(ns, "_control_box_server")
    state = BoxServerState(ns, "127.0.0.1", 8754, TOKEN, None)
    app = create_box_app(ns, state)
    body = app.test_client().get("/health").get_json()
    assert body["state"] == "idle"
    assert body["connected"] is False


def test_restart_argv_reconstructs_module_invocation(monkeypatch):
    """Same reasoning/test shape as status_server's equivalent: running the
    __main__.py file directly breaks relative imports, so a re-exec must use
    `-m eco.manual_control.box_server`, not sys.argv[0] verbatim."""
    import sys
    import types

    from eco.manual_control import box_server as box_server_mod

    fake_main = types.ModuleType("__main__")
    fake_main.__spec__ = types.SimpleNamespace(name="eco.manual_control.box_server",
                                                parent="eco.manual_control.box_server")
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    monkeypatch.setattr(sys, "argv", ["box_server.py", "-s", "bernina"])
    argv = box_server_mod._restart_argv()
    assert argv[:3] == [sys.executable, "-m", "eco.manual_control.box_server"]
    assert argv[3:] == ["-s", "bernina"]
