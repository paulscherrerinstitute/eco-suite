"""Command-line front end, mirroring the examples of the original
``ConfigMotorIOC.py`` but built on the object API of this package.

Examples::

    python -m eco.motion.deltatau_config --host EXP1 --cfg stageXYZ
    python -m eco.motion.deltatau_config --host XRD  --cfg kappa -f
    python -m eco.motion.deltatau_config --host GPS  --cfg stage --ioc /ioc/SARES22-CPPM-GPS1
    python -m eco.motion.deltatau_config --host EXP1 --cfg stageXYZ --dry-run
    python -m eco.motion.deltatau_config --host EXP1 --cfg stageXYZ --check   # verify only
"""

from __future__ import annotations

import argparse

from .bundle import CONFIGS, HOSTS
from .deploy import apply


def _build_parser() -> argparse.ArgumentParser:
    hosts = "|".join(HOSTS)
    configs = "|".join(CONFIGS)
    p = argparse.ArgumentParser(
        prog="eco.motion.deltatau_config",
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            f"known hosts:   {hosts}\n"
            f"known configs: {configs}\n\n"
            ">>> A WRONG CONFIGURATION CAN DESTROY MOTORS <<<"
        ),
    )
    p.add_argument("--host", required=True, help="short name or full hostname")
    p.add_argument("-c", "--cfg", default=None, help="config shortname or tuple")
    p.add_argument("--ioc", default=None, help="ioc path to (re)configure in shellbox")
    p.add_argument("-p", "--port", type=int, default=50001, help="ioc console port")
    p.add_argument("-v", "--verbose", type=int, default=1, help="verbosity bits")
    p.add_argument("-f", "--force", action="store_true", help="skip safety password")
    p.add_argument("--no-restart", dest="restart", action="store_false",
                   help="copy files but do not restart the IOC")
    p.add_argument("--dry-run", action="store_true",
                   help="print the command sequence without executing")
    p.add_argument("--check", action="store_true",
                   help="only verify the live config against --cfg (no apply)")
    return p


def _maybe_tuple(cfg):
    """Accept the legacy tuple-literal form passed as a string on the CLI."""
    if cfg is None or cfg in CONFIGS:
        return cfg
    if cfg.strip().startswith("("):
        import ast

        return ast.literal_eval(cfg)
    return cfg


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = _maybe_tuple(args.cfg)

    if args.check:
        from .verify import check

        ok, _ = check(args.host, cfg)
        return 0 if ok else 1

    return apply(
        host=args.host,
        config=cfg,
        ioc=args.ioc,
        port=args.port,
        force=args.force,
        verbose=args.verbose,
        restart=args.restart,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
