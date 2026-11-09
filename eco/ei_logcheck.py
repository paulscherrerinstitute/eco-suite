"""eco.ei_logcheck -- does the code in the logbooks still fit eco?

Logbook posts (SciLog, elog, notes) and the command history keep code from
whatever version of eco was running when they were written: a method that
was renamed since, a keyword that went away, a component that moved. Copied
from a post, or picked up by %ei as an example, such a line fails today.

This module finds the lines of code in logbook text and checks each with
eco.ei_check (read, never run):

* code_lines(text, roots) -- the lines of a post that are code: Python that
  parses and uses a top-level name of the instrument (`scans.ascan(...)`,
  `mono.theta.mv(...)`); prose and other people's code are left alone;
* problems_of(text, ...) -- what does not fit, for one post or command
  (%ei puts this next to a logbook hit, so the model does not copy it);
* check_index(...) -- every logbook item of the search index, for a report
  (`%ei --check-logs`): which outdated calls occur, how often, how
  recently, and where.

Outside a session the instrument's names come from its definition
(ei_check.static_namespace()); in a session from the live Namespace, and
objects built already are checked live.
"""

import ast
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from eco import ei_check, logs_ask

#: the logbook kinds of the index; commands are checked only where asked
LOG_KINDS = ("scilog", "elog", "note", "note_command")
#: names a logbook line may start with besides the instrument's own
EXTRA_ROOTS = {"eco"}
#: longer lines are not code someone typed
MAX_LINE = 400

_PROMPT_RX = re.compile(r"^\s*(?:>>>|\.\.\.|In \[\d+\]:|\[\d+\]:)\s?")
_START_RX = re.compile(r"^[A-Za-z_][\w.]*\s*[.(\[=]")


def _roots(tree):
    """Root names of the attribute chains and calls of a parsed line."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Call)):
            target = node.func if isinstance(node, ast.Call) else node
            ch = ei_check._chain(target)
            if ch and (ch[1] or isinstance(node, ast.Call)):
                out.add(ch[0])
    return out


def code_lines(text, roots):
    """[(line number, code)] of `text` that are code using one of `roots`
    (a set of top-level names). A statement spread over several lines
    (open brackets) is joined."""
    lines = (text or "").splitlines()
    out, i = [], 0
    while i < len(lines):
        line = _PROMPT_RX.sub("", lines[i]).strip()
        start = i
        i += 1
        if not line or len(line) > MAX_LINE:
            continue
        if not _START_RX.match(line):
            # prose: the names it mentions ("then use las.wp_att"), as chains
            out += [(start + 1, chain) for chain in _prose_chains(line, roots)]
            continue
        code = line
        # an open bracket: take the following lines until it closes
        while i < len(lines) and i - start < 15 and code.count("(") + code.count("[") > code.count(")") + code.count("]"):
            code += "\n" + _PROMPT_RX.sub("", lines[i]).rstrip()
            i += 1
        try:
            tree = ast.parse(code)
        except (SyntaxError, ValueError):
            out += [(start + 1, chain) for chain in _prose_chains(code, roots)]
            continue
        if _roots(tree) & roots:
            out.append((start + 1, code))
    return out


_CHAIN_RX = re.compile(r"(?<![\w./@:-])([A-Za-z_]\w*)((?:\.[A-Za-z_]\w*)+)(?![\w@/-]|\.\w)")
#: endings that make a dotted word an address or a file name
_NOT_CODE_RX = re.compile(r"\.(ch|com|org|net|de|json|py|h5|txt|png|jpg|pdf|csv|log|md|html?)$", re.I)


def _prose_chains(text, roots):
    """`root.attr...` chains named in a line of prose."""
    out = []
    for m in _CHAIN_RX.finditer(text):
        chain = m.group(0)
        if m.group(1) in roots and not _NOT_CODE_RX.search(chain) and chain not in out:
            out.append(chain)
    return out


class Checker:
    """Checks logbook lines against eco; caches per distinct line, since
    the same commands recur in hundreds of posts."""

    def __init__(self, symbols, namespace=None, user_ns=None):
        self.symbols = symbols
        self.namespace = list(namespace) if namespace else ei_check.static_namespace()
        self.user_ns = user_ns
        self.roots = {name for name, *_ in self.namespace} | EXTRA_ROOTS
        self._cache = {}

    def check_line(self, code):
        if code not in self._cache:
            try:
                problems = ei_check.check(code, self.symbols, self.namespace, None)
            except Exception:
                problems = []  # a check that fails claims nothing
            if re.fullmatch(r"[\w.]+", code):
                problems = [p for p in problems if not self._cut_off(p)]
            self._cache[code] = problems
        return self._cache[code]

    def _cut_off(self, problem):
        """A bare name in prose that begins a real member's name: cut off
        in a table cell or a narrow column (`mono.mono_und_energy_ti`),
        not wrong."""
        m = re.match(r"(\w+) has no (\w+)", problem.message)
        if not m:
            return False
        cls, attr = m.groups()
        return any(n != attr and n.startswith(attr) for n in self.symbols.members(cls))

    def problems_of(self, text):
        """[(code, [Problem])] for the lines of `text` that do not fit."""
        out = []
        for _n, code in code_lines(text, self.roots):
            problems = self.check_line(code)
            if problems:
                out.append((code, problems))
        return out

    def command_problems(self, command):
        """[Problem] of one command of the history (it is all code)."""
        return self.check_line(command) if _roots_ok(command, self.roots) else []


def _roots_ok(code, roots):
    try:
        return bool(_roots(ast.parse(ei_check._python_only(code) or "")) & roots)
    except (SyntaxError, ValueError):
        return False


_IT_HAS_RX = re.compile(r" \(it has: [^)]*\)")


def short(message):
    """A problem's message without the long "(it has: ...)" member list."""
    return _IT_HAS_RX.sub("", message)


def annotation(problems):
    """The note %ei puts next to a logbook hit whose code is outdated."""
    shown = []
    for p in problems[:3]:
        shown.append(f"`{p.code}` {short(p.message)}")
    more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
    return "code check: this does not fit eco today -- " + "; ".join(shown) + more + ". Do not copy it."


# -- the report over the whole index ---------------------------------------------------------


@dataclass
class Finding:
    """One problem, with every logbook item it occurs in."""

    code: str
    message: str
    items: list = field(default_factory=list)  # [(t, cite, location)]

    @property
    def newest(self):
        return max((t for t, _c, _l in self.items), default=0)

    def __str__(self):
        when = time.strftime("%Y-%m-%d", time.localtime(self.newest)) if self.newest else "undated"
        return f"`{self.code}` -- {self.message}  ({len(self.items)}x, last {when})"


def check_index(symbols, db_path=logs_ask.DEFAULT_DB, kinds=LOG_KINDS, since=None, namespace=None,
                progress=None):
    """[Finding] over the logbook items of the index, most frequent first.
    since: epoch seconds, only items written after it. progress(done, total)
    is called now and then."""
    kinds = tuple(kinds)
    con = logs_ask._connect(db_path)
    where = f"kind IN ({','.join('?' * len(kinds))})"
    params = list(kinds)
    if since is not None:
        where += " AND CAST(t AS REAL) >= ?"
        params.append(since)
    rows = con.execute(f"SELECT text, context, kind, status, flags, t, source, path FROM items WHERE {where}",
                       params).fetchall()
    con.close()
    checker = Checker(symbols, namespace)
    found = {}
    for i, (text, context, kind, status, flags, t, source, path) in enumerate(rows):
        if progress and i % 2000 == 0:
            progress(i, len(rows))
        if kind == "note_command":
            pairs = [(text, checker.command_problems(text))]
        else:
            pairs = checker.problems_of(text)
        if not any(problems for _c, problems in pairs):
            continue
        hit = logs_ask.Hit(kind, text, context, status, flags, float(t or 0), source, 0.0, path or "")
        for _code, problems in pairs:
            for p in problems:
                key = (p.code, short(p.message))
                f = found.setdefault(key, Finding(p.code, short(p.message)))
                f.items.append((hit.t, hit.cite(), logs_ask.item_location(hit)))
    if progress:
        progress(len(rows), len(rows))
    return sorted(found.values(), key=lambda f: (-len(f.items), -f.newest))


def by_name(findings):
    """{object: [Finding]}: the findings grouped by what they call on
    (`scans`, `mono.theta`), for an overview."""
    out = defaultdict(list)
    for f in findings:
        out[f.code.rpartition(".")[0] or f.code].append(f)
    return dict(out)


def report(findings, limit=25, links=False):
    """Text for the terminal."""
    if not findings:
        return "eco.ei: the code in the indexed logbooks fits eco as it is today."
    total = sum(len(f.items) for f in findings)
    out = [f"eco.ei: {len(findings)} kinds of outdated or wrong code in {total} logbook lines "
           "(checked against eco's source, not run):", ""]
    for f in findings[:limit]:
        out.append(f"  {f}")
        t, cite, location = max(f.items)
        out.append(f"      newest: {cite}" + (f"  {location}" if links and location else ""))
    if len(findings) > limit:
        out.append(f"  ... and {len(findings) - limit} more (`eco.ei_logcheck.check_index(...)` returns all)")
    return "\n".join(out)
