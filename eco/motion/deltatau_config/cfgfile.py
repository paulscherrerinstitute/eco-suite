"""Parse a Delta Tau / PowerBrick ``.cfg`` file into a structured, editable form
and serialise it back *losslessly*.

Why this exists
---------------
The real per-motor configuration of a PowerBrick lives in a ``.cfg`` file that is
consumed by ``gpasciiCommander``. Most of the interesting lines are *template
calls* such as::

    !encoder_inc(enc=1,posSf=1./10)
    !motor(mot=1,dirCur=1000,invDir=False,JogSpeed=1.,servoSf=1024./5)

interleaved with raw gpascii statements (``Motor[4].pLimits=...``,
``disable plc 12`` ...), ``//`` comments and blank lines.

We want to *read* such a file, *inspect / modify* individual axes as a plain
Python dict, and *write it back*. Round-trip fidelity matters: a config that
silently changes when re-serialised is dangerous (a wrong PowerBrick config can
damage motors), so every source line is kept verbatim and only lines the caller
explicitly edits are regenerated.

Minimal example
---------------
>>> from eco.motion.deltatau_config.cfgfile import parse_cfg
>>> cfg = parse_cfg("!encoder_inc(enc=1,posSf=1./10)\\n"
...                 "!motor(mot=1,dirCur=1000,JogSpeed=1.)\\n")
>>> cfg.to_dict()["axes"][1]["motor"]["dirCur"]
1000
>>> cfg.to_text()  # byte-identical to the input
'!encoder_inc(enc=1,posSf=1./10)\\n!motor(mot=1,dirCur=1000,JogSpeed=1.)\\n'
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# A template call looks like ``!name(arg=val, arg2=val2, ...)`` possibly with a
# trailing comment. We capture the name and the raw argument string; arguments
# are parsed separately so we can keep Delta Tau expressions (``1024./5``) intact.
_TEMPLATE_RE = re.compile(r"^\s*!\s*(?P<name>\w+)\s*\((?P<args>.*)\)\s*$")

# Which keyword inside a template call carries the axis index. ``!motor`` uses
# ``mot=``, encoder templates use ``enc=``. ``holding_current`` addresses many
# motors at once (m1=, m2=, ...) so it is treated as a non-axis (global) entry.
_AXIS_KEY_BY_ROLE = {"motor": "mot", "encoder": "enc"}


def _classify_role(template_name: str) -> Optional[str]:
    """Map a template name to a coarse role used for grouping.

    ``motor`` -> ``"motor"``; anything starting with ``encoder`` -> ``"encoder"``;
    everything else -> ``None`` (kept only as a raw ordered entry).
    """
    if template_name == "motor":
        return "motor"
    if template_name.startswith("encoder"):
        return "encoder"
    return None


def _parse_args(argstr: str) -> "Dict[str, Any]":
    """Parse ``k=v, k2=v2`` into a dict, preserving un-evaluatable expressions.

    Values that are plain Python literals (``1000``, ``False``, ``[0, 1000]``) are
    returned as their evaluated value; Delta Tau arithmetic expressions
    (``1024./5``, ``102.4*1``) cannot be ``literal_eval``-ed and are kept as the
    verbatim source string so a round-trip never loses precision or intent.
    """
    args: "Dict[str, Any]" = {}
    for key, raw in _split_kwargs(argstr):
        try:
            args[key] = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            args[key] = raw  # keep expression verbatim, e.g. "1024./5"
    return args


def _split_kwargs(argstr: str):
    """Yield ``(key, raw_value)`` pairs, respecting brackets so that commas inside
    ``[...]`` / ``(...)`` (e.g. ``m1=[0,1000]``) do not split an argument."""
    depth = 0
    token = ""
    tokens: List[str] = []
    for ch in argstr:
        if ch in "([{":
            depth += 1
            token += ch
        elif ch in ")]}":
            depth -= 1
            token += ch
        elif ch == "," and depth == 0:
            tokens.append(token)
            token = ""
        else:
            token += ch
    if token.strip():
        tokens.append(token)
    for tok in tokens:
        if "=" not in tok:
            continue
        key, _, val = tok.partition("=")
        yield key.strip(), val.strip()


def _format_value(value: Any) -> str:
    """Render a parsed argument value back to source text.

    Strings are emitted verbatim (they are preserved expressions like ``1024./5``);
    everything else uses ``repr`` so ``False``/lists/ints round-trip exactly.
    """
    if isinstance(value, str):
        return value
    return repr(value)


def _format_template(name: str, args: "Dict[str, Any]") -> str:
    """Rebuild a ``!name(k=v,...)`` line from a role's argument dict."""
    inner = ",".join(f"{k}={_format_value(v)}" for k, v in args.items())
    return f"!{name}({inner})"


@dataclass
class CfgLine:
    """One physical line of the source file, classified but kept verbatim.

    ``text`` is the exact original line (without the trailing newline). When a
    template call is edited via :meth:`CfgFile.set_axis`, its ``text`` is
    regenerated from ``template``/``args`` and ``dirty`` is set.
    """

    text: str
    kind: str  # "template" | "comment" | "blank" | "raw"
    template: Optional[str] = None
    args: "Dict[str, Any]" = field(default_factory=dict)
    dirty: bool = False

    def render(self) -> str:
        if self.dirty and self.kind == "template":
            return _format_template(self.template, self.args)
        return self.text


@dataclass
class CfgFile:
    """A parsed ``.cfg`` file: an ordered list of :class:`CfgLine` plus helpers to
    view/modify it as an axis-keyed dict and serialise back to text."""

    lines: List[CfgLine] = field(default_factory=list)

    # --- construction -----------------------------------------------------
    @classmethod
    def from_text(cls, text: str) -> "CfgFile":
        lines: List[CfgLine] = []
        for raw in text.split("\n"):
            lines.append(_classify_line(raw))
        # ``"a\nb\n".split("\n")`` yields a trailing "" for the final newline;
        # keep it so ``to_text`` reproduces the original byte-for-byte.
        return cls(lines=lines)

    # --- serialise --------------------------------------------------------
    def to_text(self) -> str:
        return "\n".join(line.render() for line in self.lines)

    # --- dict view --------------------------------------------------------
    def to_dict(self) -> "Dict[str, Any]":
        """Return an axis-grouped view of the config.

        ``{"axes": {n: {"motor": {...}, "encoder": {...}}},
           "globals": [{"template": ..., "args": {...}}],
           "raw": [str, ...]}``

        ``axes`` groups template calls by their ``mot=``/``enc=`` index so a caller
        can read/compare a single motor. ``globals`` holds template calls with no
        single axis (e.g. ``holding_current``). ``raw`` lists non-template gpascii
        statements for completeness. The full ordered source is always available
        via :meth:`to_text`; this view is for inspection and comparison.
        """
        axes: "Dict[int, Dict[str, Any]]" = {}
        globals_: List[Dict[str, Any]] = []
        raw: List[str] = []
        for line in self.lines:
            if line.kind == "template":
                role = _classify_role(line.template)
                axis_key = _AXIS_KEY_BY_ROLE.get(role) if role else None
                if role and axis_key in line.args:
                    try:
                        n = int(line.args[axis_key])
                    except (TypeError, ValueError):
                        globals_.append({"template": line.template, "args": dict(line.args)})
                        continue
                    axes.setdefault(n, {})[role] = dict(line.args)
                else:
                    globals_.append({"template": line.template, "args": dict(line.args)})
            elif line.kind == "raw":
                raw.append(line.text)
        return {"axes": axes, "globals": globals_, "raw": raw}

    # --- modify -----------------------------------------------------------
    def set_axis(self, axis: int, role: str, **args: Any) -> None:
        """Update the arguments of the ``role`` (``"motor"``/``"encoder"``)
        template call for ``axis``. Only the given keys are changed; the line is
        regenerated on :meth:`to_text`. Raises ``KeyError`` if no such call exists.
        """
        axis_key = _AXIS_KEY_BY_ROLE[role]
        for line in self.lines:
            if (
                line.kind == "template"
                and _classify_role(line.template) == role
                and str(line.args.get(axis_key)) == str(axis)
            ):
                line.args.update(args)
                line.dirty = True
                return
        raise KeyError(f"no {role} template found for axis {axis}")


def _classify_line(raw: str) -> CfgLine:
    stripped = raw.strip()
    if stripped == "":
        return CfgLine(text=raw, kind="blank")
    if stripped.startswith("//") or stripped.startswith("#") and not stripped[1:2].isdigit():
        # ``//`` is a comment; ``#`` is ambiguous in gpascii (``#1..16hmz`` is a
        # command, ``# note`` is a comment). Treat ``#<space/letter>`` as comment,
        # ``#<digit>`` as a raw command.
        return CfgLine(text=raw, kind="comment")
    m = _TEMPLATE_RE.match(raw)
    if m:
        return CfgLine(
            text=raw,
            kind="template",
            template=m.group("name"),
            args=_parse_args(m.group("args")),
        )
    return CfgLine(text=raw, kind="raw")


def parse_cfg(text: str) -> CfgFile:
    """Parse ``.cfg`` source text into a :class:`CfgFile`."""
    return CfgFile.from_text(text)


def parse_cfg_file(path: str) -> CfgFile:
    """Parse a ``.cfg`` file from disk."""
    with open(path, "r") as fh:
        return parse_cfg(fh.read())
