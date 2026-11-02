"""eco's web app: a fourth front-end (a PWA -- installable web page, made for
phones and tablets, fine on a laptop) next to the terminal, the Qt desktop
and the Jupyter/Voila one.

Started with a session (`eco web -s bernina`), or from a running one::

    eco.start_web()               # this session's `namespace`, read-only
    eco.start_web(writes=True)    # allow writes from the browser
    eco.stop_web()

or without one (`eco web --no-console`, `python -m eco.web -s bernina`), or
without any beamline, against a simulated namespace::

    python -m eco.web --demo --writes

It prints a URL with an access token, for ``http://localhost:<port>``. From
elsewhere, forward the port over SSH first (``ssh -L 8097:localhost:8097
<host>``). Design, protocol and status: ``web_frontend/`` at the repo root.
"""
import atexit
import errno

from eco.web.gateway import DEFAULT_PORT

_gateway = None
_atexit_registered = False


def start_web(namespace=None, port=DEFAULT_PORT, writes=False, host="127.0.0.1",
              token=None, poll_interval=1.0, quiet=False):
    """Serve `namespace` (default: the calling session's `namespace`) to
    the web client. Returns the running gateway (``.url``, ``.stop()``).
    One per session: calling it again returns the running one."""
    global _gateway, _atexit_registered
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
    try:
        _gateway = WebGateway(SessionBackend(namespace), host=host, port=port, token=token,
                              writes=writes, poll_interval=poll_interval).start()
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        raise RuntimeError(
            f"port {port} is already in use on this host (another eco web, perhaps of "
            f"another account?) -- start it on another one, e.g. start_web(port={port + 1})"
        ) from None
    # the gateway thread is a daemon: without this, leaving the session
    # would end it without stopping a jog a browser is holding
    if not _atexit_registered:
        atexit.register(stop_web)
        _atexit_registered = True
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
