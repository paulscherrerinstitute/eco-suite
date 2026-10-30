"""eco's web front-end (PWA) -- in development, not wired into eco yet.

Nothing in eco imports this package; it is started explicitly::

    from eco.web import start_web
    gw = start_web()              # this session's `namespace`, read-only
    gw = start_web(writes=True)   # allow writes from the browser
    stop_web()

or, without any beamline, against a simulated namespace::

    python -m eco.web --demo

It prints a URL with an access token, for ``http://localhost:<port>``. From
elsewhere, forward the port over SSH first (``ssh -L 8097:localhost:8097
<host>``). Plan, protocol and status: ``web_frontend/`` at the repo root.
"""
from eco.web.gateway import DEFAULT_PORT

_gateway = None


def start_web(namespace=None, port=DEFAULT_PORT, writes=False, host="127.0.0.1",
              token=None, poll_interval=1.0, quiet=False):
    """Serve `namespace` (default: the calling session's `namespace`) to
    the web client. Returns the running gateway (``.url``, ``.stop()``).
    One per session: calling it again returns the running one."""
    global _gateway
    from eco.web.backends import SessionBackend, calling_namespace
    from eco.web.gateway import WebGateway

    if _gateway is not None and _gateway.running:
        if not quiet:
            print(f"eco web already running: {_gateway.url}")
        return _gateway
    if namespace is None:
        namespace = calling_namespace()
    if namespace is None:
        raise RuntimeError("no namespace given and none found in this session")
    _gateway = WebGateway(SessionBackend(namespace), host=host, port=port, token=token,
                          writes=writes, poll_interval=poll_interval).start()
    if not quiet:
        print(connection_help(_gateway))
    return _gateway


def stop_web():
    global _gateway
    if _gateway is not None:
        _gateway.stop()
        _gateway = None


def connection_help(gateway):
    import socket

    mode = "writes ENABLED" if gateway.writes else "read-only"
    return (
        f"eco web ({mode}) on this host, port {gateway.port}:\n"
        f"  {gateway.url}\n"
        f"from another machine, forward the port first:\n"
        f"  ssh -L {gateway.port}:localhost:{gateway.port} {socket.gethostname()}"
    )
