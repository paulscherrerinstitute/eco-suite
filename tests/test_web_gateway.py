"""eco.web.gateway end to end: a real tornado server on an ephemeral port,
token handling, static files, the SVG endpoint and the websocket."""
import asyncio
import json

import pytest

tornado = pytest.importorskip("tornado")
from tornado.httpclient import AsyncHTTPClient, HTTPClientError, HTTPRequest
from tornado.websocket import websocket_connect

from eco.web.backends import SessionBackend
from eco.web.demo import DemoNamespace
from eco.web.gateway import COOKIE, WebGateway

TOKEN = "test-token"


@pytest.fixture(scope="module")
def gateway():
    gw = WebGateway(SessionBackend(DemoNamespace()), port=0, token=TOKEN).start()
    yield gw
    gw.stop()


def fetch(gateway, path, **kwargs):
    async def go():
        client = AsyncHTTPClient()
        request = HTTPRequest(f"http://127.0.0.1:{gateway.port}{path}", follow_redirects=False, **kwargs)
        try:
            return await client.fetch(request, raise_error=False)
        finally:
            client.close()

    return asyncio.run(go())


def test_index_requires_the_token(gateway):
    assert fetch(gateway, "/").code == 403
    assert fetch(gateway, "/?token=wrong").code == 403


def test_token_in_url_sets_cookie_and_redirects(gateway):
    response = fetch(gateway, f"/?token={TOKEN}")
    assert response.code == 302 and response.headers["Location"] == "/"
    assert f"{COOKIE}={TOKEN}" in response.headers["Set-Cookie"]
    assert "HttpOnly" in response.headers["Set-Cookie"]
    page = fetch(gateway, "/", headers={"Cookie": f"{COOKIE}={TOKEN}"})
    assert page.code == 200 and b"/static/app.js" in page.body


def test_app_shell_is_served(gateway):
    for path, kind in [("/sw.js", "javascript"), ("/manifest.webmanifest", "manifest"),
                       ("/icon.svg", "svg"), ("/static/app.js", "javascript"), ("/static/style.css", "css")]:
        response = fetch(gateway, path)
        assert response.code == 200, path
        assert kind in response.headers["Content-Type"], path


def test_svg_endpoint(gateway):
    assert fetch(gateway, "/api/svg?name=vacuum").code == 403
    response = fetch(gateway, f"/api/svg?name=vacuum&token={TOKEN}")
    assert response.code == 200 and response.body.startswith(b"<svg")
    assert response.headers["X-Eco-Prefix"] == "vacuum"
    assert fetch(gateway, f"/api/svg?name=mono&token={TOKEN}").code == 404


def test_websocket_rejects_without_token(gateway):
    async def go():
        ws = await websocket_connect(f"ws://127.0.0.1:{gateway.port}/ws")
        message = await ws.read_message()
        return message, ws.close_code

    message, code = asyncio.run(go())
    assert message is None and code == 4403


def test_websocket_round_trip_and_values(gateway):
    async def go():
        request = HTTPRequest(f"ws://127.0.0.1:{gateway.port}/ws", headers={"Cookie": f"{COOKIE}={TOKEN}"})
        ws = await websocket_connect(request)

        async def call(i, op, **args):
            await ws.write_message(json.dumps({"v": 0, "id": i, "op": op, "args": args}))
            while True:
                message = json.loads(await asyncio.wait_for(ws.read_message(), 5))
                if message.get("re") == i:
                    return message

        hello = await call(1, "hello")
        await call(2, "describe", name="mono")
        await call(3, "subscribe", names=["mono.energy"])
        while True:
            message = json.loads(await asyncio.wait_for(ws.read_message(), 5))
            if message.get("ev") == "values" and "mono.energy" in message["data"]:
                break
        ws.close()
        return hello, message

    hello, values = asyncio.run(go())
    assert hello["ok"] and hello["data"]["writes"] is False
    assert values["data"]["mono.energy"]["num"] == 7.0


def test_stop_closes_client_connections():
    gw = WebGateway(SessionBackend(DemoNamespace()), port=0, token=TOKEN).start()

    async def go():
        request = HTTPRequest(f"ws://127.0.0.1:{gw.port}/ws", headers={"Cookie": f"{COOKIE}={TOKEN}"})
        ws = await websocket_connect(request)
        await ws.write_message(json.dumps({"v": 0, "id": 1, "op": "ping"}))
        reply = json.loads(await asyncio.wait_for(ws.read_message(), 5))
        await asyncio.get_running_loop().run_in_executor(None, gw.stop)
        closed = await asyncio.wait_for(ws.read_message(), 5)
        return reply, closed, ws.close_code

    reply, closed, code = asyncio.run(go())
    assert reply["ok"] and reply["data"] == {}
    assert closed is None and code == 1001
    assert not gw.running


def test_persistent_token_is_created_private_and_reused(tmp_path):
    import os
    import stat

    from eco.web.gateway import persistent_token

    path = tmp_path / "sub" / "web_token"
    first = persistent_token(str(path))
    assert len(first) >= 20 and persistent_token(str(path)) == first
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    path.unlink()
    assert persistent_token(str(path)) != first  # deleting the file invalidates old clients
