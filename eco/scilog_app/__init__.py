"""eco.scilog_app -- read (and, later, write) SciLog in the browser.

    scilog.viewer()                         # in a session: the pgroup's logbook
    from eco.scilog_app import start; start()   # anywhere else: a separate server process
    python -m eco.scilog_app [--port 8798]  # on its own, without a session

Nothing in eco imports this package except scilog.viewer()/widget(), and
only when they are called.

The page shows a logbook as one flow, oldest at the top and the newest at
the bottom, under day headers ("Today", "Yesterday", "Friday", ...), with:

* an outline of months, days, posts and their headings to jump to;
* fuzzy search (typos allowed) with previews; a result jumps to the post in
  its context and marks the matches;
* permalinks to a post (``#lb=<logbook>&post=<id>``) and the logbook's
  SciLog address;
* images at the size they were given in SciLog, never stretched beyond it;
* HTML attachments (interactive plots) inline, in a sandbox, started with a
  click;
* new and edited posts appearing by themselves.

The server runs as a process of its own, so it never competes with the eco
session for its interpreter; it ends with the session that started it.
SciLog is read with your own token (eco.logs_scilog.reader: your personal
token, or the beamline account where that applies). On a console, forward
the port first: ``ssh -L 8798:localhost:8798 <console>``.
"""

import os
import secrets
import socket
import collections
import subprocess
import sys
import threading
import time

DEFAULT_PORT = 8798
#: the server's token travels in the environment: a command line is
#: readable by every account on the host (ps)
TOKEN_ENV = "ECO_SCILOG_APP_TOKEN"

_process = None
_url = None
#: the server's last output lines (log()), read off its pipe so it never fills up
_output = collections.deque(maxlen=200)


def start(port=DEFAULT_PORT, open_browser=None, timeout=60, logbook=None):
    """Start the server as a separate process (once per session) and print
    its address; opens a browser where one can be started (a display, not
    over ssh). The process ends when this session does. `logbook` (an id)
    makes the address open that logbook."""
    global _process, _url
    if _process is None or _process.poll() is not None:
        token = secrets.token_urlsafe(24)
        env = dict(os.environ, **{TOKEN_ENV: token})
        env["PYTHONPATH"] = os.pathsep.join(p for p in [os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                                         env.get("PYTHONPATH", "")] if p)
        _process = subprocess.Popen(
            [sys.executable, "-m", "eco.scilog_app", "--port", str(port), "--no-browser",
             "--parent", str(os.getpid()), "--quiet"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True,  # Ctrl-C in the session does not reach it
        )
        line, t0 = "", time.time()
        while time.time() - t0 < timeout:
            line = _process.stdout.readline()
            if line.startswith("eco scilog: http") or not line:
                break
        if not line.startswith("eco scilog: http"):
            rest = line + (_process.stdout.read() if _process.poll() is not None else "")
            _process.kill()
            _process = None
            raise RuntimeError(f"eco scilog server did not start: {rest.strip()[-500:]}")
        _url = line.split(": ", 1)[1].strip()
        threading.Thread(target=_drain, args=(_process,), name="eco-scilog-output", daemon=True).start()
    url = f"{_url.split('#', 1)[0]}#lb={logbook}" if logbook else _url
    print(f"eco scilog: {url}")
    if os.environ.get("SSH_CONNECTION"):
        print(f"  from your machine: ssh -L {port}:localhost:{port} {socket.gethostname()}, then open the address")
    from eco.ei_page import can_open_browser

    if open_browser or (open_browser is None and can_open_browser()):
        import webbrowser

        webbrowser.open(url)
    return url


def _drain(process):
    for line in process.stdout:
        _output.append(line.rstrip("\n"))


def log():
    """The server's last output lines (warnings, errors)."""
    return "\n".join(_output)


def stop():
    global _process, _url
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(5)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = _url = None
