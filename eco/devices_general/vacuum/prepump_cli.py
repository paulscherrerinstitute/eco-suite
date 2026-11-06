"""Command line for the Bernina prepump system.

This is what the buttons of the caQtDM vacuum panel run (through a small
wrapper script that sets up the Python environment), so the panel uses the
very same :class:`~.prepump.PrepumpSystem` logic as an eco session::

    python -m eco.devices_general.vacuum.prepump_cli status
    python -m eco.devices_general.vacuum.prepump_cli pump_down 3
    python -m eco.devices_general.vacuum.prepump_cli vent line3 --delay 5
    python -m eco.devices_general.vacuum.prepump_cli isolate 3
    python -m eco.devices_general.vacuum.prepump_cli isolate_all
    python -m eco.devices_general.vacuum.prepump_cli abort

A line is given by its name (``line1_usd``), a unique part of it (``usd``)
or its number (``1`` .. ``5``).

Only :mod:`eco.devices_general.vacuum.prepump` is imported, not the Bernina
namespace, so a button press costs a few seconds, not a full eco start.

Stopping a running ``pump_down``/``vent``:

* Ctrl-C, SIGTERM or SIGHUP -- closing caQtDM's process window sends SIGTERM
  (and kills 500 ms later);
* ``abort`` from anywhere (another console, another account, the panel's
  Abort button): it creates ``<lock_file>.abort``, which the running sequence
  polls.

Either way the sequence's own abort handling runs: a pump-down closes the
line's pump valve again and restores the other lines, a vent closes the vent
valve. Only one sequence runs at a time, across processes and consoles (see
``PrepumpSystem.lock_file``).
"""

import argparse
import os
import signal
import sys
import threading
import time


def _now():
    return time.strftime("%H:%M:%S")


def _say(*args):
    print(f"[{_now()}]", *args, flush=True)


def _fmt(p):
    try:
        return f"{float(p):.2e} mbar"
    except (TypeError, ValueError):
        return "n/a"


def _resolve_line(system, key):
    names = list(system._line_names)
    if key.isdigit():
        i = int(key)
        if 1 <= i <= len(names):
            return names[i - 1]
    if key in names:
        return key
    hits = [n for n in names if key.lower() in n.lower()]
    if len(hits) == 1:
        return hits[0]
    raise SystemExit(f"unknown line {key!r}; lines are: {', '.join(names)} (or 1..{len(names)})")


def _valve_state(line, role):
    valve = getattr(line, role, None)
    if valve is None:
        return "not connected"
    try:
        state = valve.get_current_value()
        return getattr(state, "name", str(state))
    except Exception as e:
        return f"? ({e})"


def print_status(system):
    try:
        roots = system.roots_pump.get_current_value()
        roots = getattr(roots, "name", roots)
    except Exception:
        roots = "?"
    _say(f"Gp {_fmt(system.gp_pressure())}   Roots pump: {roots}")
    running = system.running()
    _say(f"running: {running or 'nothing'}")
    for i, name in enumerate(system._line_names, 1):
        line = getattr(system, name)
        try:
            state = system.line_state(line)
        except Exception:
            state = "?"
        _say(f"{i} {name:11s} G {_fmt(line.pressure()):14s} {state:9s}"
             f" pump valve {_valve_state(line, 'valve_prevac'):13s}"
             f" vent valve {_valve_state(line, 'valve_vent')}")


def _abort_file(system):
    return f"{system.lock_file}.abort" if system.lock_file else None


def _remove(path):
    try:
        os.remove(path)
    except (FileNotFoundError, TypeError):
        pass


def request_abort(system):
    running = system.running()
    path = _abort_file(system)
    if not running or not path:
        _say("nothing is running")
        return 0
    with open(path, "w") as f:
        f.write(f"abort requested {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    try:
        os.chmod(path, 0o664)
    except OSError:
        pass
    _say(f"abort requested for: {running}")
    for _ in range(40):
        time.sleep(0.25)
        if not system.running():
            _say("stopped")
            return 0
    _say("still running after 10 s -- check its window / the valves")
    return 1


def _watch(system, done, every=5.0):
    """Poll the abort file and print the sequence's progress line."""
    path = _abort_file(system)
    last, t_print = None, 0.0
    while not done.wait(0.25):
        if path and os.path.exists(path):
            _remove(path)
            _say("abort requested")
            os.kill(os.getpid(), signal.SIGINT)  # interrupts the sleep, too
            return
        activity = system._activity
        if activity and (activity != last or time.time() - t_print > 30) and time.time() - t_print > every:
            _say(activity)
            last, t_print = activity, time.time()


def run_sequence(system, what, line, delay, timeout):
    running = system.running()
    if running:
        _say(f"busy, not starting: {running}")
        return 2
    if delay > 0:
        _say(f"{what} {line} starts in {delay:.0f} s -- close this window (Ctrl-C) to cancel")
        time.sleep(delay)
    _remove(_abort_file(system))
    done = threading.Event()
    watcher = threading.Thread(target=_watch, args=(system, done), daemon=True)
    watcher.start()
    try:
        ok = getattr(system, what)(line, timeout=timeout)
    except KeyboardInterrupt:
        _say(f"{what} {line}: aborted (its valve closed again, see above)")
        return 130
    except RuntimeError as e:  # busy (lost the race), missing valves
        _say(str(e))
        return 2
    finally:
        done.set()
    _say(f"{what} {line}: {'done' if ok else 'FAILED (timeout)'}")
    return 0 if ok else 1


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="prepump", description="Bernina prepump system (same logic as eco's `prepump`)."
    )
    ap.add_argument("command", choices=["status", "pump_down", "vent", "isolate", "isolate_all", "abort"])
    ap.add_argument("line", nargs="?", help="line name, part of it, or number")
    ap.add_argument("--delay", type=float, default=5.0,
                    help="seconds to wait (and allow cancelling) before pump_down/vent starts [5]")
    ap.add_argument("--timeout", type=float, default=700.0, help="pump_down/vent timeout [s, 700]")
    args = ap.parse_args(argv)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _raise_interrupt)

    from eco.devices_general.vacuum.prepump import make_bernina_prepump_system

    system = make_bernina_prepump_system()

    try:
        if args.command == "status":
            print_status(system)
            return 0
        if args.command == "abort":
            return request_abort(system)
        if args.command == "isolate_all":
            system.isolate_all()
            _say("all lines isolated (pump and vent valves closed)")
            return 0
        if not args.line:
            ap.error(f"{args.command} needs a line")
        line = _resolve_line(system, args.line)
        if args.command == "isolate":
            getattr(system, line).isolate()
            _say(f"{line} isolated (pump and vent valves closed)")
            return 0
        print_status(system)
        return run_sequence(system, args.command, line, args.delay, args.timeout)
    except KeyboardInterrupt:
        _say("cancelled")
        return 130


if __name__ == "__main__":
    sys.exit(main())
