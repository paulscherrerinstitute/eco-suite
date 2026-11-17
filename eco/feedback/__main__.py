"""Command line of eco.feedback (under development).

    python -m eco.feedback serve SETUP.py [--host H] [--port P] [--api-key K]
    python -m eco.feedback demo {eco,matrix,busy} [--host H] [--port P]
    python -m eco.feedback panel URL [--api-key K] [--port P]

``serve`` runs ``SETUP.py``, which must define ``manager`` (a
FeedbackManager) or ``build_manager()`` returning one -- that is where the
eco devices are created; see ``eco/feedback/examples/``. ``panel`` starts
Voila on the control-panel notebook for a running server.

The server binds 127.0.0.1 by default; reach it through ``ssh -L``.
"""

from __future__ import annotations

import argparse
import os
import runpy
import subprocess
import sys
from pathlib import Path

NOTEBOOK = Path(__file__).resolve().parent / "control_panel.ipynb"


def _load_setup(path):
    ns = runpy.run_path(str(path), run_name="__feedback_setup__")
    if "build_manager" in ns:
        return ns["build_manager"]()
    if "manager" in ns:
        return ns["manager"]
    raise SystemExit(f"{path} defines neither `manager` nor `build_manager()`")


def voila_panel(url, api_key=None, port=8866, open_browser=False):
    """Start Voila on the control-panel notebook for the server at ``url``;
    returns the ``Popen``. Stop it with ``.terminate()``."""
    env = dict(os.environ, ECO_FEEDBACK_URL=url)
    if api_key:
        env["ECO_FEEDBACK_API_KEY"] = api_key
    # make this checkout importable in the voila kernel too
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(p for p in (root, env.get("PYTHONPATH")) if p)
    if sys.prefix != sys.base_prefix:
        # a venv on top of the beamline env: voila's templates live in the base
        base = os.path.join(sys.base_prefix, "share", "jupyter")
        env["JUPYTER_PATH"] = os.pathsep.join(p for p in (env.get("JUPYTER_PATH"), base) if p)
    cmd = [sys.executable, "-m", "voila", str(NOTEBOOK), f"--port={port}"]
    if not open_browser:
        cmd.append("--no-browser")
    return subprocess.Popen(cmd, env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m eco.feedback",
                                     description="eco feedback server (under development)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="serve the manager built by a setup script")
    p.add_argument("setup", type=Path)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8010)
    p.add_argument("--api-key", default=os.environ.get("ECO_FEEDBACK_API_KEY"))

    p = sub.add_parser("demo", help="serve a no-hardware demo")
    p.add_argument("name", choices=["eco", "matrix", "busy"], nargs="?", default="eco")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=None)

    p = sub.add_parser("panel", help="Voila control panel for a running server")
    p.add_argument("url")
    p.add_argument("--api-key", default=os.environ.get("ECO_FEEDBACK_API_KEY"))
    p.add_argument("--port", type=int, default=8866)

    args = parser.parse_args(argv)

    if args.command == "panel":
        proc = voila_panel(args.url, args.api_key, args.port)
        try:
            return proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            return 0

    from .server import serve

    if args.command == "serve":
        manager = _load_setup(args.setup)
        print(f"eco feedback: {len(manager.loops)} loop(s) on http://{args.host}:{args.port}")
        serve(manager, host=args.host, port=args.port, api_key=args.api_key)
    else:
        from .demo import DEMOS

        build, port, key = DEMOS[args.name]
        port = args.port or port
        manager = build()
        print(f"eco feedback demo {args.name!r} on http://{args.host}:{port}"
              + (f" (API key {key!r})" if key else ""))
        serve(manager, host=args.host, port=port, api_key=key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
