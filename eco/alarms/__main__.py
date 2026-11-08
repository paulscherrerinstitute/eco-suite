"""Command line: ``python -m eco.alarms <command>``.

    monitor -s bernina [--force]   run the alarm monitor standalone
    oncall                         who is on call now
    upcoming [--hours 48]          shift timeline
    create-sheet [--title T]       create a new shift-plan sheet and use it
    state                          the running monitor's summary
"""

import argparse
import logging
import signal
import sys
import time


def _system(scope):
    import importlib

    mod = importlib.import_module(f"eco.{scope}.{scope}")
    ns = mod.namespace
    return ns.get_obj("alarms")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m eco.alarms")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("monitor")
    m.add_argument("-s", "--scope", default="bernina")
    m.add_argument("--force", action="store_true", help="start even if another monitor's heartbeat is fresh")
    m.add_argument("--period", type=float, default=2.0)
    sub.add_parser("oncall")
    u = sub.add_parser("upcoming")
    u.add_argument("--hours", type=float, default=48)
    c = sub.add_parser("create-sheet")
    c.add_argument("--title", default="bernina_shift_plan")
    c.add_argument("--no-example", action="store_true")
    sub.add_parser("state")
    a = ap.parse_args(argv)

    if a.cmd == "monitor":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        from eco.alarms.monitor import AlarmMonitor

        system = _system(a.scope)
        mon = AlarmMonitor(system, period=a.period).start(force=a.force)
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        try:
            while not stop:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        mon.stop()
        return 0
    if a.cmd in ("oncall", "upcoming"):
        from eco.alarms import shifts

        plan = shifts.load_plan()
        if a.cmd == "oncall":
            print(plan.on_call())
        else:
            for t0, t1, oc in plan.timeline(hours=a.hours):
                print(f"{t0:%a %d.%m %H:%M} - {t1:%a %H:%M}  on call: {', '.join(oc.primary) or 'NOBODY':30s} backup: {', '.join(oc.backup)}")
        for p in plan.problems:
            print("!", p)
        return 0
    if a.cmd == "create-sheet":
        from eco.alarms import shifts
        from eco.alarms.store import SYSTEM

        shs = shifts.create_shift_sheet(title=a.title, example=not a.no_example)
        SYSTEM.update(lambda d: d.update(shift_sheet_key=shs.id))
        print(f"created {shs.url}\nkey {shs.id} stored as the shift plan")
        return 0
    if a.cmd == "state":
        from eco.alarms.store import STATE

        doc = STATE.read()
        m = doc.get("monitor", {})
        if not m:
            print("no monitor state")
            return 1
        print(f"monitor {m.get('host')} pid {m.get('pid')}, heartbeat {time.time() - m.get('heartbeat', 0):.0f} s ago, "
              f"armed {m.get('armed')}, backend {m.get('backend')}")
        print("on call:", doc.get("oncall"))
        raised = {k: v for k, v in doc.get("alarms", {}).items() if v.get("state") in ("active", "acked", "clearing")}
        for k, v in raised.items():
            print(f"  {v['state']:9s} {k}  value {v.get('last_value')}")
        for p in m.get("problems", []):
            print("!", p)
        return 0


if __name__ == "__main__":
    sys.exit(main())
