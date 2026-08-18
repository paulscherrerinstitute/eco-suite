"""Quickly query the *present* configuration of a live Delta Tau host and diff it
against an expected config.

This is the piece that makes the module useful as a **checker** (the eventual
``bernina_diffractometers`` use case): read a curated set of live gpascii
variables per axis and compare them with what a bundle's ``.cfg`` says they
should be.

Honest limitation
-----------------
The ``!motor(...)`` / ``!encoder_*(...)`` template calls in a ``.cfg`` are expanded
into many gpascii statements by ``gpasciiCommander`` templates that are no longer
shipped on disk. We therefore cannot in general map every template argument to a
live variable. :data:`LIVE_MAP` lists only the arguments that *do* have a direct,
well-known live variable; everything else is reported as ``informational`` so the
comparison is transparent about what it can and cannot check.

>>> from eco.motion.deltatau_config import read_live, check, CONFIGS
>>> read_live("SARES20-CPPM-EXP1", axes=range(1, 4))          # {axis: {var: val}}
>>> check("SARES20-CPPM-EXP1", CONFIGS["stageXYZ"])            # -> report
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Union

from .bundle import ConfigBundle, resolve_config, resolve_host
from .cfgfile import parse_cfg_file

# template-arg -> live gpascii variable format string (``{}`` = axis index).
# Only arguments with a direct, well-known live equivalent are listed.
LIVE_MAP: Dict[str, str] = {
    "JogSpeed": "Motor[{}].JogSpeed",
    "JogTa": "Motor[{}].JogTa",
    "AbortTa": "Motor[{}].AbortTa",
    "InPosBand": "Motor[{}].InPosBand",
    "HomeOffset": "Motor[{}].HomeOffset",
}


def _pbcom(host: str):
    """Return a cached :class:`PowerBrickComm` for ``host`` (lazy import so the
    module imports fine without paramiko / on non-SSH machines)."""
    from eco.devices_general.powerbrick import get_power_brick_comm

    full_host, _ = resolve_host(host)
    return get_power_brick_comm(full_host)


def read_live(
    host: str,
    axes: Iterable[int] = range(1, 9),
    variables: Optional[Iterable[str]] = None,
) -> Dict[int, Dict[str, Any]]:
    """Read live gpascii variables for ``axes`` from ``host``.

    ``variables`` are format strings with a single ``{}`` for the axis index;
    defaults to the :data:`LIVE_MAP` values (the checkable motor parameters).
    Returns ``{axis: {variable_name: value}}``.
    """
    if variables is None:
        variables = list(LIVE_MAP.values())
    com = _pbcom(host)
    out: Dict[int, Dict[str, Any]] = {}
    for axis in axes:
        axis_vals: Dict[str, Any] = {}
        for var in variables:
            name = var.format(axis)
            axis_vals[name] = com.get_parameter(name)
        out[axis] = axis_vals
    return out


def expected_from_config(config: Union[str, ConfigBundle, tuple, dict]) -> Dict[int, Dict[str, Any]]:
    """Parse the bundle's ``.cfg`` file(s) into the expected per-axis motor dict.

    Returns ``{axis: {template_arg: value}}`` from the ``!motor`` calls.
    """
    bundle = resolve_config(config)
    expected: Dict[int, Dict[str, Any]] = {}
    for cfg_path in bundle.cfg_files():
        axes = parse_cfg_file(cfg_path).to_dict()["axes"]
        for axis, roles in axes.items():
            if "motor" in roles:
                expected.setdefault(axis, {}).update(roles["motor"])
    return expected


def diff(
    expected: Dict[int, Dict[str, Any]],
    live: Dict[int, Dict[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    """Compare expected (template args) vs. live readback.

    Returns ``{axis: {"mismatch": {arg: (expected, live)},
                        "ok": [arg, ...],
                        "informational": [arg, ...]}}`` where ``informational``
    are expected args with no entry in :data:`LIVE_MAP` (cannot be checked).
    """
    result: Dict[int, Dict[str, Any]] = {}
    for axis, exp_args in expected.items():
        mismatch: Dict[str, Any] = {}
        ok = []
        informational = []
        live_axis = live.get(axis, {})
        for arg, exp_val in exp_args.items():
            if arg not in LIVE_MAP:
                informational.append(arg)
                continue
            live_name = LIVE_MAP[arg].format(axis)
            live_val = live_axis.get(live_name)
            if _values_equal(exp_val, live_val):
                ok.append(arg)
            else:
                mismatch[arg] = (exp_val, live_val)
        result[axis] = {"mismatch": mismatch, "ok": ok, "informational": informational}
    return result


def _values_equal(expected: Any, live: Any) -> bool:
    """Compare an expected value (possibly an expression string like ``1024./5``)
    with a live numeric readback, tolerant of int/float and float rounding."""
    try:
        exp_num = float(expected) if not isinstance(expected, str) else float(eval(expected, {}, {}))
        return abs(exp_num - float(live)) <= 1e-6 * max(1.0, abs(exp_num))
    except (TypeError, ValueError, SyntaxError, NameError):
        return str(expected) == str(live)


def check(host: str, config: Union[str, ConfigBundle, tuple, dict], verbose: bool = True):
    """Convenience checker: read live, diff against ``config``, print a report and
    return ``(ok: bool, diff_result: dict)``.

    ``ok`` is ``True`` when there are no mismatches on checkable parameters.
    """
    expected = expected_from_config(config)
    live = read_live(host, axes=sorted(expected))
    result = diff(expected, live)
    ok = all(not r["mismatch"] for r in result.values())
    if verbose:
        _print_report(host, config, result, ok)
    return ok, result


def _print_report(host, config, result, ok):
    cfg_name = config if isinstance(config, str) else "<bundle>"
    print(f"config check: host={host} config={cfg_name} -> {'OK' if ok else 'MISMATCH'}")
    for axis in sorted(result):
        r = result[axis]
        if r["mismatch"]:
            for arg, (exp, got) in r["mismatch"].items():
                print(f"  axis {axis}: {arg}: expected {exp!r}, live {got!r}")
        if r["informational"]:
            print(f"  axis {axis}: not checkable (no live variable): {r['informational']}")
