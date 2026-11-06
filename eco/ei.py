"""eco.ei -- ask eco. `%ei how do I do a mesh scan with two motors?`

Answers questions about using eco from what eco knows about itself and
about past beamtimes, through a language model:

  - eco's docs (docs/*.md) and its API (class/function signatures and
    docstrings, read from the source without importing it),
  - the command history of every eco console (kernel logs), each command
    marked ok / warning / failed by what followed it,
  - SciLog posts and personal notes, once indexed (`%ei --scilog`,
    `%ei --notes PATH`),
  - the machine's elog (elog-gfa.psi.ch: SwissFEL commissioning and
    Diagnostics by default, config "elog_logbooks"), so a question like
    "what happened to the machine since yesterday?" gets that span's
    entries in order (eco.logs_elog),
  - the running session's namespace: which devices exist and of which
    class, without initialising anything lazy,
  - escape (escape-fel), the analysis framework: its API from the
    installed package, its docs and example notebooks from an escape
    checkout (config "sources"). A question about analysis is recognised
    by its words (config "topics") and then gets mostly escape context,
    plus the primer file ~/.eco/ei_primer_escape.md if it exists.

The search part is eco.logs_ask (a local SQLite full-text index under
~/.eco). The best matches of each kind, plus the namespace listing, go to
the model as context; the model is told to answer only from them and to
cite them. Nothing an answer contains is ever executed: `%ei --paste`
puts its code at the next prompt, to read and edit first.

The model is any OpenAI-compatible endpoint. Default: PSI's ai.psi.ch,
configured in ~/.eco/ei.json (written on first use, see DEFAULT_CONFIG).
The API key is looked up in this order: $ECO_EI_API_KEY, ~/.eco/ei_api_key
(keep it mode 600), the gopass entry "ai/psi-api-key", and finally the
key VS Code's Continue extension uses for the same endpoint
(~/.continue/config.yaml). Questions, and the matching history/log
excerpts, are sent to that endpoint -- nothing else leaves the machine.

    %ei how do I set the attenuator transmission?
    %ei and only if the shutter is open?    # follow-up, same conversation
    %ei -n ...      new conversation        %ei -s ...   search only, no model
    %ei -b ...      answer in the browser   %ei -b       the last answer there
    %ei -m openai/gpt-oss-120b ...          another model (kept)
    %ei --paste     last answer's code to the next prompt
    %ei --open 2    source [2] of the last answer in its context (the
                    commands around it, the neighbouring logbook posts, ...)
    %ei --bad att   commands like `att...` that gave warnings/errors
    %ei --examples PATH   index notebooks (code cells) as usage examples
    %ei --index | --rebuild | --scilog [pgroup] | --notes PATH | --models | --config
    %ei --elog [--rebuild]   update the elog entries now (or all again)

Every answer ends with the sources it cites, numbered, each with where to
find it: the elog entry's or SciLog logbook's address, or file:line.

SciLog (all logbooks your PSI groups can read with a personal token --
see eco.logs_scilog for getting it from the browser -- or, without one
and always on gac-bernina, what the beamline account can read):

    %ei --scilog-token                   who reads; asks for a token if none
    %ei --scilog-logbooks [--location bernina] [--pgroup p23618] [--name kern]
    %ei --scilog-index    [same filters | --all] [--rebuild]   index them (new posts only,
                                         or all again); no filter: the last choice
    %ei --scilog-search TEXT             SciLog's own search, live

Notes on paper -- photos, scanned PDFs, a webcam picture -- are transcribed
by the (vision) model once and then searched like everything else. Only
with a model connected:

    %ei --scan FILE_OR_DIR ...   images / PDFs / text files
    %ei --scan-image VAR         an image already in the session, e.g. a
                                 camera frame (numpy array) or PIL image
    %ei --webcam                 take a picture with a webcam attached to
                                 the machine eco runs on (Qt dialog)

Scanned and captured pictures are kept in ~/.eco/ei_scans (private).

From Python: `eco.ei.ask("...")`, `eco.ei.assistant()`, `.scan(paths)`,
`.scan_image(img)`.
"""
import contextlib
import datetime
import json
import os
import re
import shlex
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from eco import logs_ask, logs_sources

CONFIG_PATH = Path.home() / ".eco" / "ei.json"
KEY_PATH = Path.home() / ".eco" / "ei_api_key"
GOPASS_KEY = "ai/psi-api-key"

DEFAULT_CONFIG = {
    "base_url": "https://ai.psi.ch/api",
    "model": "Qwen3.8-27B",
    # sent with every request to that model, as they are
    "model_options": {
        "Qwen3.8-27B": {"chat_template_kwargs": {"enable_thinking": False}},
        "openai/gpt-oss-120b": {"reasoning_effort": "low"},
    },
    # reads images (photos, scans, webcam) for indexing; must accept images
    "vision_model": "Qwen3.8-27B",
    # where scanned and captured pictures are kept (mode 700)
    "scan_dir": "~/.eco/ei_scans",
    "max_tokens": 1500,
    # share of questions (0..1) that get a small animation instead of the
    # spinner while the model thinks (terminal only); 0: always the spinner
    "wait_animations": 0.25,
    # how a finished answer is shown in a terminal: "auto" renders it with
    # glow (https://github.com/charmbracelet/glow) when glow is found, "plain"
    # leaves the streamed text; "glow_style" "dark" or "light" ("auto": from
    # $COLORFGBG, else dark)
    "render": "auto",
    "glow_style": "auto",
    # SciLog logbooks indexed (and kept up to date) before questions until
    # `%ei --scilog-index` chooses others; null: SciLog only when chosen
    "scilog_default": {"location": "bernina"},
    # elog-gfa.psi.ch logbooks indexed (and kept up to date) before
    # questions; [] or null: none
    "elog_logbooks": ["SwissFEL commissioning", "SwissFEL Diagnostics"],
    "timeout": 180,
    # how many search hits of each kind go to the model
    # (recent: logbook entries of the time span a question names)
    "hits": {"command": 10, "note": 5, "elog": 3, "doc": 6, "api": 8, "example": 3, "recent": 25},
    # what is indexed besides eco itself. packages: their API, found
    # without importing them. repos: checkouts of those packages, the
    # first existing one is used; its docs/*.md and examples/**/*.ipynb
    # (plus notebooks inside the package) are indexed with the label.
    # example_dirs: more notebooks, {label: [paths]}.
    "sources": {
        "packages": ["escape"],
        "repos": {
            "escape: ": [
                "~/escape-fel",
                "~/mypy/escape-fel",
                "/sf/bernina/config/personal/lemke_h/escape-fel",
            ],
        },
        "example_dirs": {},
    },
    # a question with one of these words (or a follow-up to one) is about
    # that topic: its context comes mainly from items carrying the label
    # (docs, examples) or the module prefix (api), and the primer file is
    # added to the instructions
    "topics": {
        "escape": {
            "words": [
                "escape", "esc", "analysis", "analyse", "analyze", "analysing", "analyzing",
                "notebook", "binning", "histogram", "dask", "hdf5", "h5", "jungfrau",
                "pump probe", "pump-probe", "correlation", "exafs", "wavefront",
                "load run", "load the run", "run data", "raw data", "dataset",
            ],
            "label": "escape: ",
            "module": "escape.",
            "primer": "~/.eco/ei_primer_escape.md",
        },
    },
    # previous question/answer pairs kept for follow-up questions
    "history_turns": 3,
    # rebuild the index at most this often (seconds; incremental, cheap)
    "reindex_interval": 600,
}

SYSTEM_PROMPT = """\
You are the help assistant of eco, the Python experiment-control system of \
the Bernina beamline at SwissFEL (PSI). Users work in an IPython session in \
which the instrument's devices are top-level names (listed under "Namespace" \
below) and ask how to do things.

eco basics -- trust the context below over this summary where they differ:
- Everything is a Detector (has get_current_value()) or an Adjustable (also \
has set_target_value(value), which returns a Changer with .wait()/.stop()). \
Adjustables have spec-style shortcuts: obj.mv(v) (move and wait), \
obj.mvr(dv) (relative), obj.umv(v) (with progress), obj.wm() (where/read), \
obj.tweak(step).
- A device is an Assembly: a tree of Adjustables, Detectors and \
sub-Assemblies (`dev.child.grandchild`). dev.status(), dev.settings(), \
dev.get_tree(); typing the name shows its status.
- Scans are calls on `scans`, e.g. scans.ascan(adj, start, end, n_intervals, \
N_pulses), scans.dscan(...) (relative), scans.scan((adj, start, end, n), ...) \
for meshes and co-moving axes. queue=... appends a scan to a task queue \
(%queue magic) instead of running it.
- Namespace entries marked [lazy] are not initialised yet: they initialise on \
first use, which is fine to suggest.
- Data analysis is done with escape (package escape-fel, `import escape`), \
usually in a Jupyter notebook, not with eco devices. Context items tagged \
[api escape...], [doc escape: ...] and [example escape: ...] show its API and \
real usage; write analysis code in the style of those examples.

Rules:
- Answer from the context below: eco docs, eco API signatures, and commands \
users actually ran (marked ok / WARNING / FAILED with what came back), \
logbook posts and notes. Prefer commands that worked. If the relevant \
commands often gave warnings or failed, say so and quote the message.
- Never invent device names, attributes, PVs or functions. Use names from the \
Namespace list and the context. If the context does not answer the question, \
say so plainly and say what to look at (e.g. `dev.get_tree()`, `help(...)`).
- Be brief: a short explanation and one runnable Python block. Cite the \
context items you used by their [tag].
- [elog ...] items are the accelerator's logbook (SwissFEL commissioning: \
operation, shift handovers, problems and fixes). For a question about a time \
span ("since yesterday", "last week"), summarise what the logbook entries of \
that span report, in order, with their times and tags; no code needed.
- The user runs the code themselves. Warn when a suggestion moves hardware \
or starts an acquisition.
"""


# -- configuration ---------------------------------------------------------------


def load_config(path=None):
    """DEFAULT_CONFIG updated by ~/.eco/ei.json (created with the defaults
    on first use, so there is a file to edit)."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = Path(path or CONFIG_PATH)
    if path.exists():
        try:
            user = json.loads(path.read_text())
        except ValueError as exc:
            print(f"eco.ei: ignoring {path}: {exc}")
            user = {}
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    else:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        except OSError:
            pass
    return cfg


def _continue_key(base_url):
    """apiKey of a model with this apiBase in VS Code Continue's config."""
    path = Path.home() / ".continue" / "config.yaml"
    if not path.exists():
        return None
    try:
        import yaml

        models = (yaml.safe_load(path.read_text()) or {}).get("models") or []
    except Exception:
        return None
    for m in models:
        if str(m.get("apiBase", "")).rstrip("/") == base_url.rstrip("/") and m.get("apiKey"):
            return m["apiKey"]
    return None


def _gopass_key():
    import contextlib
    import io

    try:
        from eco.utilities.secrets_gopass import get_gopass_password

        with contextlib.redirect_stdout(io.StringIO()):  # it prints its failures
            return get_gopass_password(GOPASS_KEY, timeout=5)
    except Exception:
        return None


def api_key(base_url=DEFAULT_CONFIG["base_url"], gopass=True):
    """(key, where it came from), or (None, None). gopass=False skips the
    gopass lookup (a subprocess, up to seconds) -- what a GUI thread may
    afford: environment and small files only."""
    key = os.environ.get("ECO_EI_API_KEY")
    if key:
        return key.strip(), "$ECO_EI_API_KEY"
    if KEY_PATH.exists():
        mode = KEY_PATH.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            print(f"eco.ei: {KEY_PATH} is readable by others -- `chmod 600 {KEY_PATH}`")
        key = KEY_PATH.read_text().strip()
        if key:
            return key, str(KEY_PATH)
    key = _gopass_key() if gopass else None
    if key:
        return key.strip(), f"gopass {GOPASS_KEY}"
    key = _continue_key(base_url)
    if key:
        return key, "~/.continue/config.yaml"
    return None, None


NO_KEY_HELP = f"""eco.ei: no API key for {{base_url}}.
Get one at https://ai.psi.ch (Settings -> Account -> API keys), then either
  export ECO_EI_API_KEY=sk-...
or store it, readable only by you:
  (umask 077; echo sk-... > {KEY_PATH})
Until then %ei only searches (docs, API, history) without a model.
Then `%ei --key` to pick it up in this session."""

#: shown before the first (long) indexing when the answer will be a search
SEARCH_HELP = """eco.ei: without the model, %ei is a search, e.g.
  %ei attenuator transmission          the best matches in the docs, eco's source, the
                                       command history and the logbooks, numbered, with
                                       where each one is
  %ei --open 2                         match [2] in its context (the commands around it,
                                       the neighbouring logbook entries, the docs section)
  %ei what happened since yesterday?   the logbook entries of that time span, in order
  %ei --bad att                        commands like `att...` that gave warnings or errors
  %ei -s ...                           search only, also once there is a key"""

#: a search before the first index was built
NOT_INDEXED = "eco.ei: no search index yet -- `%ei --index` builds it whenever you like."

#: what a question without a key prints after the first time
NO_KEY_SHORT = "(search only -- no API key for the model; `%ei` says how to add one)"

#: a missing key is looked up again after this many seconds at the
#: earliest (the gopass lookup alone can take seconds)
KEY_RETRY_S = 300


NO_MODEL_FOR_SCAN = (
    "eco.ei: reading notes from pictures needs the model (an API key) -- `%ei` says how to add one."
)


class NoModel(RuntimeError):
    """Raised by Assistant.scan*/transcriber() without a connected model."""


#: seconds between progress lines of a long first read
PROGRESS_S = 10


def _every(seconds, report, template):
    """progress(*values) that reports `template` at most every `seconds`
    (and not for something done sooner)."""
    last = [time.time()]

    def progress(*values):
        if time.time() - last[0] >= seconds:
            last[0] = time.time()
            report(template.format(*values))

    return progress


def enabled():
    """False when ECO_NO_EI=1: no %ei magic, no Ask menu."""
    return os.environ.get("ECO_NO_EI", "").strip().lower() not in ("1", "true", "yes", "on")


# -- questions about a time span -------------------------------------------------

_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "ten": 10, "few": 3, "couple of": 2, "couple": 2}
_UNITS = {"hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400, "shift": 8 * 3600}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_SPAN_RX = re.compile(
    r"\b(?:last|past|previous)\s+(?:(\d+|" + "|".join(_NUMBERS) + r")\s+)?(hour|day|week|month|shift)s?\b"
)
_TIME_WORDS_RX = re.compile(
    r"\b(?:(?:in|over|during|for|since|from)\s+)?(?:the\s+)?(?:(?:last|past|previous)\s+(?:\w+\s+)?"
    r"(?:hours?|days?|weeks?|months?|shifts?|night)|today|tonight|yesterday|this\s+(?:morning|week)"
    r"|recently|recent|lately|latest|or\s+so|" + "|".join(_WEEKDAYS) + r")\b",
    re.I,
)


def time_window(question, now=None):
    """(since, until) in epoch seconds for the time span a question names --
    "last day", "past 3 hours", "yesterday", "since Monday", "last night",
    "this week", "recently" -- or None. until is None for "up to now"."""
    now = time.time() if now is None else now
    q = " " + question.lower() + " "
    midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    stretch = 1.5 if re.search(r"\bor so\b", q) else 1.0
    m = _SPAN_RX.search(q)
    if m:
        n = m.group(1) or "1"
        n = int(n) if n.isdigit() else _NUMBERS[n]
        return now - n * _UNITS[m.group(2)] * stretch, None
    if re.search(r"\b(last|past) night\b|\btonight\b", q):
        return midnight - 6 * 3600, None
    if re.search(r"\bsince yesterday\b", q):
        return midnight - 86400, None
    if re.search(r"\byesterday\b", q):
        return midnight - 86400, midnight
    if re.search(r"\btoday\b|\bthis morning\b", q):
        return midnight, None
    if re.search(r"\bthis week\b", q):
        return midnight - time.localtime(now).tm_wday * 86400, None
    for i, day in enumerate(_WEEKDAYS):
        if re.search(rf"\bsince {day}\b", q):
            back = (time.localtime(now).tm_wday - i) % 7
            return midnight - back * 86400, None
    if re.search(r"\b(recent|recently|lately|latest)\b", q):
        return now - 2 * 86400, None
    return None


# -- the running session ---------------------------------------------------------


def _words(text):
    """Lowercase word pieces of identifiers and module paths:
    "AttenuatorAramis" -> attenuator, aramis; "eco.xoptics.att" -> xoptics, att."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text))
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1}


def _kind_of(obj):
    t = type(obj)  # not obj.__class__: that would resolve a lazy Proxy
    if t.__name__ == "Proxy":
        return "lazy"
    if hasattr(t, "set_target_value"):
        return "adjustable"
    if hasattr(t, "_append"):
        return "assembly"
    if hasattr(t, "get_current_value"):
        return "detector"
    return t.__name__


def _children(obj, limit=60):
    """`child: Class (kind)` for the components appended to an initialised
    Assembly, read from its __dict__ (no property is evaluated)."""
    out = []
    try:
        items = list(vars(obj).items())
    except TypeError:
        return out
    for name, child in items:
        if name.startswith("_") or "alias" not in getattr(child, "__dict__", {}):
            continue
        out.append(f"{name}: {type(child).__name__} ({_kind_of(child)})")
        if len(out) >= limit:
            out.append("...")
            break
    return out


class NamespaceInfo:
    """What the model is told about the session's devices. Reads only the
    Namespace's own bookkeeping; never initialises a lazy component."""

    def __init__(self, namespace):
        self.namespace = namespace

    def entries(self):
        """[(name, class name, module, state)] for every top-level name."""
        ns = self.namespace
        if ns is None:
            return []
        info = getattr(ns, "_factory_info", {}) or {}
        try:
            lazy, failed = set(ns.lazy_names), set(ns.failed_names)
            names = sorted(ns.all_names)
        except Exception:
            return []
        out = []
        for name in names:
            fi = info.get(name, {})
            factory = fi.get("obj_factory")
            cls = factory if isinstance(factory, str) else getattr(factory, "__name__", "")
            module = fi.get("module_name") or getattr(factory, "__module__", "") or ""
            state = "lazy" if name in lazy else "failed" if name in failed else ""
            out.append((name, cls, module, state))
        return out

    def expansions(self, question):
        """Namespace names whose class or module matches a word of the
        question ("attenuator" -> att), to search the history for them too."""
        qwords = {w for w in _words(question) if len(w) >= 4}
        found = []
        for name, cls, module, _state in self.entries():
            words = _words(cls) | _words(module.split(".")[-1]) | _words(name)
            if any(q.startswith(w) or w.startswith(q) for q in qwords for w in words if len(w) >= 4):
                found.append(name)
        return found[:12]

    def named(self, question):
        """[(name, class name)] of top-level names the question spells out
        ("how to see the fel energy" -> fel)."""
        tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question))
        return [(name, cls) for name, cls, _module, _state in self.entries() if len(name) >= 3 and name in tokens]

    def text(self, question="", max_lines=400):
        entries = self.entries()
        if not entries:
            return ""
        lines = [
            f"{name}: {cls or '?'}" + (f" [{state}]" if state else "")
            for name, cls, _module, state in entries
        ]
        if len(lines) > max_lines:
            keep = set(self.expansions(question))
            lines = [ln for ln in lines if ln.split(":")[0] in keep] + [f"... {len(entries)} names in total"]
        out = ["Namespace (top-level names: class [state]):", *lines]
        # children of initialised devices the question names
        initialized = getattr(self.namespace, "initialized_items", {}) or {}
        tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)) | set(self.expansions(question))
        for name in sorted(tokens & set(initialized))[:4]:
            children = _children(initialized[name])
            if children:
                out += [f"\nComponents of {name}:", *children]
        return "\n".join(out)


# -- the assistant -----------------------------------------------------------------


_CODE_RX = re.compile(r"```(?:python|py|ipython)?[ \t]*\n(.*?)```", re.S)


@dataclass
class Answer:
    question: str
    text: str
    hits: list = field(default_factory=list)
    model: str = ""
    seconds: float = 0.0

    @property
    def code(self):
        """The answer's Python code blocks, joined."""
        return "\n\n".join(b.strip() for b in _CODE_RX.findall(self.text))

    @property
    def cited(self):
        """The items the answer cites by their [tag], in the order given;
        every item for a search-only answer (it lists them all)."""
        if not self.model:
            return list(self.hits)
        out = []
        for h in self.hits:
            if any(form in self.text for form in _citation_forms(h)) and all(h is not o for o in out):
                out.append(h)
        return out

    def sources(self):
        """How many items of each kind the answer was given, e.g.
        "10 commands, 6 doc, 8 api"."""
        counts = {}
        for h in self.hits:
            counts[h.kind] = counts.get(h.kind, 0) + 1
        return ", ".join(f"{n} {kind}" for kind, n in counts.items()) or "nothing found"

    def __str__(self):
        return self.text

    def __repr__(self):
        return f"<eco.ei.Answer {self.model or 'search only'}, {len(self.text)} chars, {self.sources()}: {self.question[:60]!r}>"


def _citation_forms(hit):
    """How an answer may cite a hit: its full [tag], or the shorter forms
    models write -- [doc scanning.md], [scilog 2026-05-08]."""
    tag = hit.cite()
    forms = [tag[1:-1]]
    if hit.kind in ("doc", "api", "example"):
        forms.append(hit.context)
        if hit.kind == "doc":
            forms.append(f"[doc {hit.context.split(' > ')[0]}]")
    if hit.kind in ("scilog", "note", "note_command") and hit.t:
        day = time.strftime("%Y-%m-%d", time.localtime(hit.t))
        forms.append(f"[{tag[1:].split()[0]} {day}")  # "[scilog 2026-05-08", "[note 2026-05-08"
    if hit.kind == "elog" and hit.t:
        # to the minute: the machine logbook has a dozen entries a day
        forms.append(time.strftime("[elog %Y-%m-%d %H:%M", time.localtime(hit.t)))
    return forms


class Assistant:
    """One conversation with the model about one session's namespace.
    Thread-safe enough for one question at a time from any thread (the
    desktop runs ask() on a worker)."""

    def __init__(self, namespace=None, config=None, backend=None, db_path=logs_ask.DEFAULT_DB):
        self.config = config if config is not None else load_config()
        self.namespace = NamespaceInfo(namespace)
        self.db_path = db_path
        self.history = []  # [(question, answer text)]
        self.last = None
        self.shown = None  # the last answer or listing, for --open
        self._backend = backend
        self._no_key_until = 0.0  # don't look for a missing key again before
        self._key_note_shown = False
        self._indexed_at = 0.0
        # None: not asked yet; False: the first (long) indexing was declined
        # for this session; True: go ahead
        self._index_choice = None
        self._scilog_failed = False
        self._elog_failed = False
        self._index_lock = threading.Lock()

    # model ---------------------------------------------------------------

    @property
    def model(self):
        return self.config["model"]

    def set_model(self, model):
        self.config["model"] = model
        if isinstance(self._backend, logs_ask.OpenAICompatibleBackend):
            self._backend = None

    def backend(self, recheck=False, gopass=True):
        """The configured backend, or None without an API key. A missing
        key is looked for again only every KEY_RETRY_S (or with recheck).
        gopass=False: see api_key (a miss is then not remembered)."""
        if self._backend is None:
            if not recheck and time.time() < self._no_key_until:
                return None
            cfg = self.config
            key, _where = api_key(cfg["base_url"]) if gopass else api_key(cfg["base_url"], gopass=False)
            if key is None:
                if gopass:
                    self._no_key_until = time.time() + KEY_RETRY_S
                return None
            self._backend = logs_ask.OpenAICompatibleBackend(
                cfg["base_url"],
                cfg["model"],
                api_key=key,
                timeout=cfg["timeout"],
                max_tokens=cfg["max_tokens"],
                options=cfg.get("model_options", {}).get(cfg["model"], {}),
            )
        return self._backend

    def model_available(self):
        """True once a backend is set up, without looking anything up --
        for enabling GUI actions (call backend() on a worker first)."""
        return self._backend is not None

    # notes on paper ----------------------------------------------------------

    def transcriber(self):
        """A logs_sources.VisionTranscriber on the configured endpoint, or
        NoModel without a key."""
        backend = self.backend()
        if backend is None:
            raise NoModel(NO_MODEL_FOR_SCAN)
        cfg = self.config
        model = cfg.get("vision_model") or cfg["model"]
        return logs_sources.VisionTranscriber(
            cfg["base_url"], model, api_key=getattr(backend, "api_key", None),
            timeout=cfg["timeout"], options=cfg.get("model_options", {}).get(model, {}),
        )

    def scan(self, paths, report=print):
        """Transcribe (images, scanned PDFs) or read (text, PDFs with
        text) the given files or directories and add them to the index,
        dated by the dates written in them. Files already indexed and
        unchanged are skipped. Returns {"files_indexed", "items", "notes",
        "transcripts": [text per transcribed picture/page]}."""
        transcribe = self.transcriber()
        transcripts = []

        def ocr(image):
            text = transcribe(image)
            transcripts.append(text)
            if report:
                report(f"eco.ei: transcribed a picture ({len(text)} characters)")
            return text

        result = logs_ask.index_documents(paths, db_path=self.db_path, ocr=ocr)
        result["transcripts"] = transcripts
        return result

    def scan_dir(self):
        path = Path(self.config.get("scan_dir") or "~/.eco/ei_scans").expanduser()
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)  # pictures of notes are private
        except OSError:
            pass
        return path

    def scan_image(self, image, label="image", report=print):
        """Keep `image` (PIL image, numpy array e.g. a camera frame, or
        image file bytes) as a PNG in the scan directory, transcribe it and
        add it to the index. Returns (path, transcript)."""
        self.transcriber()  # NoModel before anything is written
        img = logs_sources.to_pil(image)
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", label)[:40] or "image"
        path = self.scan_dir() / f"{time.strftime('%Y%m%d_%H%M%S')}_{name}.png"
        img.save(path)
        result = self.scan([path], report=report)
        if str(path) in result["notes"] and not result["transcripts"]:
            raise RuntimeError(f"transcribing {path.name} failed: {result['notes'][str(path)]}")
        return path, "\n".join(result["transcripts"])

    # index ---------------------------------------------------------------

    def index_sources(self):
        """build_index() keyword arguments for eco plus the configured
        packages, repos and example dirs (those that exist)."""
        import importlib.util

        src = self.config.get("sources", {})
        topics = self.config.get("topics", {}).values()
        src_dirs, doc_dirs, example_dirs = [logs_ask.SRC_DIR], [logs_ask.DOCS_DIR], []
        for package in src.get("packages", []):
            try:
                spec = importlib.util.find_spec(package)  # a top-level package: not imported
            except (ImportError, ValueError):
                spec = None
            if spec is not None and spec.submodule_search_locations:
                pkg_dir = Path(list(spec.submodule_search_locations)[0])
                src_dirs.append(pkg_dir)
                # notebooks shipped inside the package, under its topic's label
                label = next((t.get("label", "") for t in topics if t.get("module") == package + "."), "")
                example_dirs.append((pkg_dir, label))
        for label, candidates in src.get("repos", {}).items():
            repo = next((Path(c).expanduser() for c in candidates if _readable_dir(Path(c).expanduser())), None)
            if repo is not None:
                doc_dirs.append((repo / "docs", label))
                example_dirs.append((repo / "examples", label))
        for label, paths in src.get("example_dirs", {}).items():
            example_dirs += [(Path(p).expanduser(), label) for p in paths]
        return {"src_dirs": src_dirs, "doc_dirs": doc_dirs, "example_dirs": example_dirs}

    #: share of the best doc/api/example matches that must belong to a
    #: topic for a question without any of its words to count as about it
    TOPIC_VOTE = 0.6

    def topic(self, question, follow_up=True):
        """Name of the configured topic the question (or, for a
        follow-up, the previous question) is about, or None: one of the
        topic's words, else -- unless it names a device of the session --
        a majority of the best doc/api/example matches carrying its label
        or module."""
        topics = self.config.get("topics", {})
        texts = [question] + ([self.history[-1][0]] if follow_up and self.history else [])
        for text in texts:
            low = " " + " ".join(re.findall(r"[a-z0-9-]+", text.lower())) + " "
            for name, t in topics.items():
                if any(f" {w.lower()} " in low for w in t.get("words", [])):
                    return name
        if self.namespace.named(question):
            return None  # "the fel energy" is about `fel`, whatever "energy" finds
        try:
            hits = logs_ask.search(" ".join(texts), k=10, db_path=self.db_path, kinds=("doc", "api", "example"))
        except Exception:
            return None
        for name, t in topics.items():
            prefixes = tuple(p for p in (t.get("label"), t.get("module")) if p)
            if hits and prefixes and sum(h.context.startswith(prefixes) for h in hits) >= self.TOPIC_VOTE * len(hits):
                return name
        return None

    def primer(self, topic):
        path = (self.config.get("topics", {}).get(topic) or {}).get("primer")
        if not path:
            return ""
        path = Path(path).expanduser()
        try:
            return path.read_text().strip()
        except OSError:
            return ""

    def first_index_steps(self):
        """The long first-time parts of the next refresh_index():
        [(what, rough seconds)], empty once everything is indexed."""
        from eco import logs_elog, logs_scilog

        exists = Path(self.db_path).exists()
        steps = [] if exists else [("docs, eco source and command history", 90)]
        try:
            filters = (logs_scilog.load_selection(self.db_path) if exists else None) or self.config.get("scilog_default")
            if filters and not self._scilog_failed and not (exists and logs_scilog.indexed_logbooks(self.db_path)):
                steps.append(("SciLog", 20))
            known = logs_elog.indexed_logbooks(self.db_path) if exists else {}
            fresh = [lb for lb in self.config.get("elog_logbooks") or [] if lb not in known]
            if fresh and not self._elog_failed:
                steps.append((f"the elog {', '.join(fresh)}", 30))
        except Exception:
            pass  # an unreadable index: refresh_index reports it
        return steps

    def refresh_index(self, force=False, report=print):
        """Incremental logs_ask.build_index(), at most every
        reindex_interval seconds unless forced. After the first indexing
        was declined (ask(confirm=...)), only what is indexed already is
        updated, until forced."""
        with self._index_lock:
            if force:
                self._index_choice = True
            elif time.time() - self._indexed_at < self.config["reindex_interval"]:
                return None
            first_time = self._index_choice is not False
            fresh = not Path(self.db_path).exists()
            if fresh and not first_time:
                return None
            if fresh and report:
                report("eco.ei: building the search index (docs, eco source, command history) -- the first time takes a while ...")
            t0 = time.time()
            result = logs_ask.build_index(db_path=self.db_path, **self.index_sources())
            self._indexed_at = time.time()
            if report and (fresh or force):
                report(f"eco.ei: index has {result['items']} items ({result['files_indexed']} files updated, {time.time() - t0:.1f} s)")
            self._refresh_scilog(report, first_time=first_time)
            self._refresh_elog(report, first_time=first_time)
            return result

    def rebuild_index(self, report=print):
        """The index from scratch: removed, then built again with SciLog and
        the elog (minutes). Notes added with --notes/--scan need adding again."""
        with self._index_lock:
            for suffix in ("", "-journal", "-wal", "-shm"):
                path = Path(str(self.db_path) + suffix)
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            self._scilog_failed = self._elog_failed = False
        if report:
            report(f"eco.ei: {self.db_path} removed, building it again ...")
        result = self.refresh_index(force=True, report=report)
        if report:
            report("eco.ei: done; notes added with `%ei --notes` or `--scan` need adding again")
        return result

    def _refresh_scilog(self, report, first_time=True):
        """New posts of the SciLog logbooks chosen last time (by default
        config["scilog_default"], Bernina's), and away with those of
        logbooks no longer readable. One failure report per session; the
        existing index is searched regardless."""
        from eco import logs_scilog

        filters = logs_scilog.load_selection(self.db_path) or self.config.get("scilog_default")
        if not filters:
            return
        try:
            first = not logs_scilog.indexed_logbooks(self.db_path)
            if first and not first_time:
                return
            reader = logs_scilog.reader(timeout=10)
            if first and report:
                report(f"eco.ei: indexing SciLog {filters} as {reader.identity} -- the first time reads every post, a minute or more; Ctrl-C stops it ...")
            r = logs_scilog.update(
                reader, filters, db_path=self.db_path, report=None,
                progress=_every(PROGRESS_S, report, "eco.ei: SciLog {0}/{1} logbooks read, {2} posts ...") if first and report else None,
            )
        except logs_scilog.TokenError:
            if report and not self._scilog_failed:
                report("eco.ei: SciLog is not searched: no SciLog token here (`%ei --scilog-token` tells how to add one)")
            self._scilog_failed = True
            return
        except Exception as exc:
            if report and not self._scilog_failed:
                report(f"eco.ei: SciLog not updated ({type(exc).__name__}: {str(exc).splitlines()[0]})")
            self._scilog_failed = True
            return
        if first and report:
            report(f"eco.ei: {r['new_posts']} SciLog posts from {r['logbooks']} logbooks indexed")
        self._scilog_failed = False
        if report and r["dropped"]:
            report(f"eco.ei: {r['dropped']} SciLog posts removed: {r['identity']} can no longer read their logbooks")

    def _refresh_elog(self, report, rebuild=False, first_time=True):
        """New entries of config["elog_logbooks"] (first_time=False: only
        of those indexed already). One failure report per session; the
        existing index is searched regardless."""
        from eco import logs_elog

        logbooks = self.config.get("elog_logbooks") or []
        try:
            known = logs_elog.indexed_logbooks(self.db_path)
            if not first_time:
                logbooks = [lb for lb in logbooks if lb in known]
            if not logbooks and not known:
                return None
            fresh = [lb for lb in logbooks if lb not in known]
            if (fresh or rebuild) and report:
                report(f"eco.ei: reading the elog {', '.join(fresh or logbooks)} -- all entries, ~30 s for SwissFEL commissioning ...")
            r = logs_elog.update(
                logbooks, db_path=self.db_path, report=report if (fresh or rebuild) else None, rebuild=rebuild
            )
        except Exception as exc:
            if report and not self._elog_failed:
                report(f"eco.ei: elog not updated ({type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''})")
            self._elog_failed = True
            return None
        self._elog_failed = False
        return r

    def retrieve(self, question, follow_up=True):
        """Best hits per kind, for the question plus (for a follow-up) the
        previous question, plus namespace names its words point to."""
        query = question
        if follow_up and self.history:
            query += " " + self.history[-1][0]
        expansions = self.namespace.expansions(question)
        if expansions:
            query += " " + " ".join(expansions)
        quota = self.config["hits"]
        topic = self.topic(question, follow_up=follow_up)
        t = self.config.get("topics", {}).get(topic) or {}
        label, module = t.get("label"), t.get("module")
        # (kinds, how many, context prefix, excluded prefix)
        if topic:
            groups = [
                (("example",), 6, label, None),
                (("api",), 8, module, None),
                (("doc",), 5, label, None),
                (("api",), 2, None, module),
                (("doc",), 2, None, label),
                (("command",), 4, None, None),
                (("scilog", "note", "note_command"), 3, None, None),
            ]
        else:
            # eco questions: the topic packages' API, docs and examples
            # only through the example quota
            module_out = [t["module"] for t in self.config.get("topics", {}).values() if t.get("module")]
            groups = [
                (("command",), quota.get("command", 10), None, None),
                (("scilog", "note", "note_command"), quota.get("note", 5), None, None),
                (("elog",), quota.get("elog", 3), None, None),
                (("doc",), quota.get("doc", 6), None, None),
                (("api",), quota.get("api", 8), "eco." if module_out else None, None),
                (("example",), quota.get("example", 3), None, None),
            ]
        hits = self.recent_hits(question) if not topic else []
        if not topic:
            # the class of each device the question names, with its components
            for _name, cls in self.namespace.named(question)[:3]:
                if cls:
                    found = logs_ask.search(cls, k=10, db_path=self.db_path, kinds=("api",), context_prefix="eco.")
                    hits += [h for h in found if h.context.endswith("." + cls) and h not in hits][:1]
        for kinds, k, prefix, exclude in groups:
            if k:
                found = logs_ask.search(
                    query, k=k, db_path=self.db_path, kinds=kinds, context_prefix=prefix, exclude_prefix=exclude
                )
                if kinds == ("command",):
                    found.sort(key=lambda h: logs_ask._STATUS_ORDER[h.status])
                hits += [h for h in found if h not in hits]
        return hits

    def recent_hits(self, question, now=None):
        """The logbook entries (elog, SciLog) of the time span the question
        names, oldest first; [] if it names none. More than the "recent"
        quota: those with more of the question's other words, then the
        newest, entries with a text before title-only ones."""
        window = time_window(question, now=now)
        if not window:
            return []
        since, until = window
        found = logs_ask.recent(since, until, limit=1000, db_path=self.db_path)
        words = logs_ask._query_words(_TIME_WORDS_RX.sub(" ", question))
        found.sort(key=lambda h: (-logs_ask._coverage(words, h.text, h.context), "\n" not in h.text.strip(), -h.t))
        chosen = found[: self.config["hits"].get("recent", 25)]
        return sorted(chosen, key=lambda h: h.t)

    # asking --------------------------------------------------------------

    def messages(self, question, hits):
        context = []
        for h in hits:
            if h.kind == "command":
                flag = f" -- {h.flags[:200]}" if h.flags else ""
                out = f"\n    -> {h.context[:200]}" if h.context else ""
                context.append(f"{h.cite()} ({logs_ask._status_label(h)}{flag}) >>> {h.text[:400]}{out}")
            elif h.kind == "note_command":
                context.append(f"{h.cite()} (from notes, outcome unknown) >>> {h.text[:400]}")
            elif h.kind == "api":
                context.append(f"{h.cite()} {h.text[:1500 if 'components: ' in h.text else 700]}")
            elif h.kind == "example":
                context.append(f"{h.cite()}\n```python\n{h.text[:1200]}\n```")
            else:
                context.append(f"{h.cite()}\n{h.text[:1200]}")
        ns_text = self.namespace.text(question)
        user = (
            time.strftime("Now: %a %Y-%m-%d %H:%M\n\n")
            + (ns_text + "\n\n" if ns_text else "")
            + "Context:\n\n"
            + ("\n\n".join(context) if context else "(nothing found)")
            + f"\n\nQuestion: {question}"
        )
        system = SYSTEM_PROMPT
        topic = self.topic(question)
        primer = self.primer(topic) if topic else ""
        if primer:
            system += f"\n{topic} primer (written by its maintainers, authoritative):\n{primer}\n"
        msgs = [{"role": "system", "content": system}]
        for q, a in self.history[-self.config["history_turns"]:]:
            msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        msgs.append({"role": "user", "content": user})
        return msgs

    def ask(self, question, on_delta=None, stop=None, new=False, search_only=False, report=print, on_waiting=None,
            confirm=None, on_phase=None):
        """Answer `question`. on_delta(text) receives the answer as it
        streams; on_waiting() is called just before the model is asked,
        on_phase(what) as each step starts ("updating the index", ...).
        search_only (or no API key): the search hits, no model.

        When the answer will be a search and the first, long indexing is
        due, the search syntax is reported first, and confirm(steps) --
        steps as from first_index_steps() -- decides whether to index now
        (once per session; without confirm it goes ahead)."""
        question = question.strip()
        if new:
            self.history = []
        t0 = time.time()
        # the key first: without one the answer is a search, and the user may
        # rather not wait for the first indexing just to try %ei
        backend = None if search_only else self.backend()
        explained = False
        steps = self.first_index_steps() if backend is None and self._index_choice is None else []
        if steps:
            if report:
                if not search_only:
                    report(NO_KEY_HELP.format(base_url=self.config["base_url"]))
                    self._key_note_shown = explained = True
                report(SEARCH_HELP)
            if confirm is not None:
                self._index_choice = bool(confirm(steps))
                if not self._index_choice and report and Path(self.db_path).exists():
                    report("eco.ei: not indexed now -- `%ei --index` builds it whenever you like; "
                           "until then %ei searches what is indexed already.")
        if on_phase:
            on_phase("updating the index")
        try:
            self.refresh_index(report=report)
        except Exception as exc:
            if report:
                report(f"eco.ei: index update failed ({type(exc).__name__}: {exc}); searching the existing index")
        if self._index_choice is False and not Path(self.db_path).exists():
            self.shown = Answer(question, NOT_INDEXED, [], "", time.time() - t0)
            return self.shown
        if on_phase:
            on_phase("searching")
        hits = self.retrieve(question, follow_up=not search_only)
        if backend is None:
            if not search_only and report and not explained:
                # the full how-to once per session, then a reminder
                report(NO_KEY_SHORT if self._key_note_shown else NO_KEY_HELP.format(base_url=self.config["base_url"]))
                self._key_note_shown = True
            text = _listing(hits) or "Nothing in the docs, the eco source, the command history, logbooks or notes matches that."
            # not self.last: that stays the last model answer, for --paste
            self.shown = Answer(question, text, hits, "", time.time() - t0)
            return self.shown
        msgs = self.messages(question, hits)
        if on_phase:
            on_phase(f"asking {self.model}")
        if on_waiting:
            on_waiting()
        text = backend.chat(msgs, on_delta=on_delta, stop=stop)
        self.history.append((question, text))
        self.last = self.shown = Answer(question, text, hits, self.model, time.time() - t0)
        return self.last

    def reset(self):
        self.history = []


def _readable_dir(path):
    try:
        return path.is_dir() and os.access(path, os.R_OK | os.X_OK)
    except OSError:
        return False


def _listing(hits, links=False):
    lines = []
    for n, h in enumerate(hits, 1):
        first = h.text.splitlines()[0][:160] if h.text else ""
        bad = "" if h.ok else f"({logs_ask._status_label(h).lower()}: {h.flags[:80]}) "
        where = h.location()
        lines.append(f"[{n}] {h.cite()} {bad}{first}" + (f"\n      {_shown(where, links)}" if where else ""))
    if lines:
        lines.append("-- `%ei --open N` shows one of them in its context")
    return "\n".join(lines)


def source_list(ans, markdown=False, links=False):
    """The answer's cited sources, numbered as `%ei --open N` takes them,
    each with where it is; markdown=True for the desktop panel (links),
    links=True for terminal hyperlinks."""
    cited = ans.cited
    if not cited:
        return ""
    lines = []
    for n, h in enumerate(cited, 1):
        where = h.location()
        if markdown:
            url = _url(where)
            lines.append(f"{n}. {h.cite()}" + (f" -- [{where}]({url})" if url else ""))
        else:
            lines.append(f"[{n}] {h.cite()}" + (f"\n      {_shown(where, links)}" if where else ""))
    if markdown:
        return "**Sources**\n\n" + "\n".join(lines)
    return "-- sources (`%ei --open N` shows one in its context):\n" + "\n".join(lines)


def _url(where):
    """A location as a URL: SciLog's as it is, file:line as file://host/path
    (no line: file URLs have none). The host is where eco runs; a terminal
    elsewhere opens the same path locally, which at PSI is mostly the same
    file (NFS)."""
    if not where or where.startswith("http"):
        return where
    import socket
    from urllib.parse import quote

    path = re.sub(r":\d+$", "", where)
    return f"file://{socket.gethostname()}{quote(path)}"


def terminal_links():
    """Whether printed locations become clickable terminal hyperlinks
    (OSC 8: gnome-terminal and other VTE terminals, kitty, WezTerm, iTerm2,
    Windows Terminal, VS Code's terminal; others show the plain text).
    Not in a Jupyter/qtconsole kernel, nor with ECO_NO_HYPERLINKS=1."""
    import sys

    if os.environ.get("ECO_NO_HYPERLINKS") or os.environ.get("TERM", "dumb") == "dumb":
        return False
    if type(sys.stdout).__module__.startswith("ipykernel"):
        return False
    try:
        return sys.__stdout__.isatty()
    except Exception:
        return False


def _shown(where, links):
    """A location, as an OSC 8 terminal hyperlink when `links`."""
    url = _url(where) if links else ""
    return f"\x1b]8;;{url}\x1b\\{where}\x1b]8;;\x1b\\" if url else where


_assistants = {}


def assistant(namespace=None):
    """The session's Assistant for this namespace (one per namespace)."""
    key = id(namespace)
    if key not in _assistants:
        _assistants[key] = Assistant(namespace)
    return _assistants[key]


def ask(question, namespace=None, **kwargs):
    """eco.ei.ask("...") -> Answer (printing it streamed)."""
    return _print_answer(assistant(namespace), question, **kwargs)


def _print_answer(asst, question, **kwargs):
    import sys

    from eco import ei_wait

    out = sys.stdout
    # one display for the whole wait: the index update and the search come
    # before the model, and on a console they take seconds too
    waiting = []
    if ei_wait.in_terminal(out):
        animation = ei_wait.pick(asst.config.get("wait_animations", 0))
        waiting.append(ei_wait.Waiting(out, label="getting ready", animation=animation, tips=True).start())

    def paused():
        return waiting[0].paused() if waiting else contextlib.nullcontext()

    def on_phase(what):
        if waiting:
            waiting[0].label = what

    def report(text):
        with paused():
            print(text)

    streamed = []
    # with glow the answer is shown once complete, rendered; meanwhile its
    # last lines are a preview in the wait display
    rendering = bool(waiting) and asst.config.get("render", "auto") == "auto" and glow_path() is not None

    def on_delta(piece):
        streamed.append(piece)
        if rendering and waiting:
            waiting[0].label = f"{asst.model} is writing"
            waiting[0].preview = "".join(streamed).rstrip().split("\n")[-PREVIEW_LINES:]
            return
        if waiting:
            waiting.pop().stop()
        out.write(piece)
        out.flush()

    def confirm(steps):
        what = ", ".join(f"{name} ~{seconds} s" for name, seconds in steps)
        with paused():
            try:
                reply = input(f"eco.ei: build the search index now ({what})? [Y/n] ")
            except EOFError:
                return False
        return reply.strip().lower() not in ("n", "no")

    if ei_wait.in_terminal(out) and sys.stdin.isatty():
        kwargs.setdefault("confirm", confirm)
    kwargs.setdefault("report", report)
    try:
        try:
            ans = asst.ask(question, on_delta=on_delta, on_phase=on_phase, **kwargs)
        finally:
            if waiting:  # erased before anything else is printed
                waiting.pop().stop()
    except KeyboardInterrupt:
        if rendering and streamed:
            print("".join(streamed))
        print("\neco.ei: interrupted")
        return None
    except Exception as exc:
        print(f"\neco.ei: {asst.model} at {asst.config['base_url']} failed: {type(exc).__name__}: {exc}")
        return None
    if ans.model:
        rendered = _render(asst.config, out, ans.text) if rendering else False
        print(f"{'' if rendered else chr(10)}\n-- {ans.model}, {ans.seconds:.1f} s; context: {ans.sources()}")
        sources = source_list(ans, links=terminal_links())
        if sources:
            print(sources)
        if ans.code:
            print("-- `%ei --paste` puts the code at the next prompt")
    else:
        print(_listing(ans.hits, links=terminal_links()) if ans.hits else ans.text)
    # nothing for IPython to echo as Out[]: the answer is already printed and
    # stays available as eco.ei.assistant(namespace).last (and through %ei --paste)
    return None


#: where glow may be when it is not on PATH (the SwissFEL consoles)
GLOW_PLACES = ("/sf/daq/bin/glow",)


def glow_path():
    """The glow executable, or None."""
    import shutil

    return shutil.which("glow") or next((p for p in GLOW_PLACES if os.access(p, os.X_OK)), None)


def _glow_style(config):
    style = config.get("glow_style", "auto")
    if style in ("dark", "light"):
        return style
    # COLORFGBG="15;0": foreground;background, a light background is 7 or 15
    bg = os.environ.get("COLORFGBG", "").split(";")[-1]
    return "light" if bg in ("7", "15") else "dark"


def glow(text, width, config=None):
    """`text` (Markdown) rendered by glow for a terminal `width` wide, or None."""
    import subprocess

    exe = glow_path()
    if exe is None:
        return None
    env = dict(os.environ, CLICOLOR_FORCE="1")  # colours also into a pipe
    try:
        r = subprocess.run(
            [exe, "-s", _glow_style(config or {}), "-w", str(max(40, min(width, 120))), "-"],
            input=text, capture_output=True, text=True, timeout=10, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and r.stdout.strip() else None


#: lines of the answer shown while it is being written (with glow)
PREVIEW_LINES = 3


def _render(config, out, text):
    """Print `text` rendered by glow; the plain text if glow fails. True if
    rendered."""
    import shutil

    rendered = glow(text, shutil.get_terminal_size().columns, config)
    out.write(rendered.rstrip("\n") + "\n" if rendered else text)
    out.flush()
    return rendered is not None


def _show_in_browser(asst):
    """The last answer (or search listing) as a page: written, opened if a
    browser can be started here, and its path printed."""
    import sys

    from eco import ei_page

    if asst.shown is None:
        print("eco.ei: nothing to show yet -- `%ei -b <question>`")
        return None
    from eco import ei_wait

    waiting = None
    if ei_wait.in_terminal(sys.stdout):
        waiting = ei_wait.Waiting(sys.stdout, label="making the page, with the SciLog posts it cites", tips=True).start()
    try:
        path = ei_page.write(asst.shown, db_path=asst.db_path, report=None)
    except Exception as exc:
        if waiting:
            waiting.stop()
        print(f"eco.ei: the page failed: {type(exc).__name__}: {exc}")
        return None
    if waiting:
        waiting.stop()
    opened = ei_page.open_page(path)
    where = _shown(str(path), terminal_links())
    print(f"eco.ei: {'opened ' if opened else 'page: '}{where}")
    return path


# -- %ei -----------------------------------------------------------------------------


def _shell_namespace(shell):
    ns = shell.user_ns.get("namespace") if shell is not None else None
    return ns if hasattr(ns, "lazy_names") else None


def ei_magic(line, cell=None):
    """%ei <question> -- ask eco (see `eco.ei` for everything it can do)."""
    from IPython import get_ipython

    shell = get_ipython()
    asst = assistant(_shell_namespace(shell))
    text = (line + ("\n" + cell if cell else "")).strip()
    try:
        args = shlex.split(text.split("\n")[0]) if text.startswith("-") else []
    except ValueError:
        args = []
    opt = args[0] if args else ""
    rest = text[len(opt):].strip() if opt else text

    if not text:
        _key, where = api_key(asst.config["base_url"])
        print(_status(asst, where))
        if where is None:
            print(NO_KEY_HELP.format(base_url=asst.config["base_url"]))
        return None
    if opt in ("-h", "--help"):
        print(__doc__)
        return None
    if opt in ("--scan", "--scan-image", "--webcam"):
        if asst.backend() is None:
            print(NO_MODEL_FOR_SCAN)
            return None
        try:
            if opt == "--scan":
                result = asst.scan(shlex.split(rest) or ["."])
                print(f"eco.ei: {result['files_indexed']} files indexed, {len(result['transcripts'])} pictures transcribed")
                for path, note in result["notes"].items():
                    if note != "OCR":
                        print(f"  {path}: {note}")
                for text in result["transcripts"][:3]:
                    print("---\n" + text.strip()[:600])
            elif opt == "--scan-image":
                if rest not in shell.user_ns:
                    print(f"eco.ei: no variable {rest!r} -- e.g. `img = cam.get_image()` first, then `%ei --scan-image img`")
                    return None
                path, text = asst.scan_image(shell.user_ns[rest], label=rest)
                print(f"eco.ei: {path} indexed:\n{text.strip()}")
            else:
                from eco.widgets.ei_qt import run_webcam_dialog

                run_webcam_dialog(asst)
        except Exception as exc:
            print(f"eco.ei: {opt} failed: {type(exc).__name__}: {exc}")
        return None
    if opt == "--key":
        if asst.backend(recheck=True) is None:
            print(NO_KEY_HELP.format(base_url=asst.config["base_url"]))
        else:
            print(f"eco.ei: API key found, answers come from {asst.model}")
        return None
    if opt == "--paste":
        if asst.last is None or not asst.last.code:
            print("eco.ei: the last answer has no code")
        else:
            shell.set_next_input(asst.last.code)
        return None
    if opt == "--models":
        backend = asst.backend()
        if backend is None:
            print(NO_KEY_HELP.format(base_url=asst.config["base_url"]))
            return None
        for m in backend.models():
            print(("* " if m == asst.model else "  ") + m)
        return None
    if opt == "--config":
        print(f"{CONFIG_PATH}:\n{json.dumps(asst.config, indent=2)}")
        return None
    if opt == "--index":
        asst.refresh_index(force=True)
        return None
    if opt == "--rebuild":
        asst.rebuild_index()
        return None
    if opt == "--scilog":
        # one pgroup's logbooks, as whoever reads SciLog here
        _scilog_magic(asst, "--scilog-index", _scilog_default(asst, rest))
        return None
    if opt == "--elog":
        r = asst._refresh_elog(print, rebuild="--rebuild" in rest)
        if r:
            print(f"eco.ei: elog read as {r['identity']}: {r['new_entries']} entries from {r['logbooks']} logbooks, {r['items']} items in the index")
        return None
    if opt == "--open":
        shown = asst.shown
        cited = shown.cited if shown else []
        try:
            hit = cited[int(rest) - 1]
        except (ValueError, IndexError):
            print(f"eco.ei: %ei --open N, N from 1 to {len(cited)} (the numbers under the last answer)" if cited else "eco.ei: no sources to open yet")
            return None
        text, where = logs_ask.item_context(hit, db_path=asst.db_path), hit.location()
        print(text.replace(where, _shown(where, terminal_links()), 1) if where else text)
        return None
    if opt == "--examples":
        # for this session; add it to "sources" in ~/.eco/ei.json to keep it
        asst.config.setdefault("sources", {}).setdefault("example_dirs", {}).setdefault("", []).append(rest)
        asst.refresh_index(force=True)
        return None
    if opt == "--notes":
        result = logs_ask.index_documents(rest, db_path=asst.db_path)
        print(f"eco.ei: {result['files_indexed']} files indexed, {result['items']} items in total")
        for path, note in result["notes"].items():
            print(f"  {path}: {note}")
        return None
    if opt.startswith("--scilog-"):
        _scilog_magic(asst, opt, rest)
        return None
    if opt == "--bad":
        rows = logs_ask.command_outcomes(rest or None, db_path=asst.db_path, min_runs=2, limit=20)
        for r in rows:
            if r["warning"] + r["error"]:
                print(f"{r['warning'] + r['error']:4d}/{r['runs']:<4d} {r['command'][:90]}\n          {r['last_flag'][:110]}")
        return None
    if opt in ("-m", "--model"):
        model, _, rest = rest.partition(" ")
        asst.set_model(model)
        print(f"eco.ei: model {model}")
        if not rest.strip():
            return None
        return _print_answer(asst, rest)
    if opt in ("-n", "--new"):
        return _print_answer(asst, rest, new=True)
    if opt in ("-s", "--search"):
        return _print_answer(asst, rest, search_only=True)
    if opt in ("-b", "--browser"):
        if rest:
            _print_answer(asst, rest)
        _show_in_browser(asst)
        return None
    return _print_answer(asst, text)


def _scilog_default(asst, pgroup=""):
    """--scilog's choice: that pgroup, else the last choice, else Bernina."""
    from eco import logs_scilog as ls

    if pgroup:
        return f"--pgroup {pgroup}"
    return "" if ls.load_selection(asst.db_path) else "--location bernina"


def _scilog_magic(asst, opt, rest):
    """--scilog-token / -logbooks / -index / -search: SciLog read with a
    personal token, or as the beamline account (eco.logs_scilog)."""
    from eco import logs_scilog as ls

    if opt == "--scilog-token":
        value, where = ls.token()
        shared = ls._shared_account()
        if shared:
            try:
                reader = ls.reader()
                print(f"eco.ei: {shared} is a shared account, so no personal token here: SciLog is read as {reader.identity}; {len(reader.logbooks())} logbooks readable")
            except Exception as exc:
                print(f"eco.ei: {shared} reads SciLog as the beamline account, which failed: {type(exc).__name__}: {exc}")
            return
        if value is None or rest == "new":
            import getpass

            print(ls.TOKEN_STEPS + "\n  3. paste it here (it is not shown):")
            try:
                value = getpass.getpass("SciLog token: ")
            except (EOFError, KeyboardInterrupt):
                print("eco.ei: no token entered")
                return
            try:
                where = ls.save_token(value)
            except ValueError as exc:
                print(f"eco.ei: {exc}")
                return
        try:
            n = len(ls.Reader(value).logbooks())
        except ls.TokenError as exc:
            print(f"eco.ei: {exc}")
            return
        except Exception as exc:
            print(f"eco.ei: SciLog not reachable: {type(exc).__name__}: {exc}")
            return
        print(f"eco.ei: SciLog token from {where}: {ls.describe(value)}; {n} logbooks readable")
        return
    try:
        reader = ls.reader()
        if opt == "--scilog-search":
            if not rest:
                print("eco.ei: %ei --scilog-search TEXT")
                return
            names = {lb["id"]: lb["name"] for lb in reader.logbooks()}
            hits = reader.search(rest, limit=20)
            for h in hits:
                when = datetime.datetime.fromtimestamp(h["t"]).strftime("%Y-%m-%d %H:%M")
                text = " ".join(h["text"].split())
                print(f"{when}  {names.get(h['logbook'], h['logbook'])}  {h['author']}\n    {text[:220]}")
            print(f"-- {len(hits)} posts" + (" (the newest 20)" if len(hits) == 20 else ""))
            return
        filters = ls.parse_filters(rest)
        if opt == "--scilog-logbooks":
            filters.pop("all", None)
            lbs = reader.logbooks(**filters)
            for lb in lbs:
                print(f"{lb['touched'][:10]}  {lb['location']:<16} {lb['pgroup']:<7} {lb['name']}" + ("  (private)" if lb["private"] else ""))
            print(f"-- {len(lbs)} logbooks; `%ei --scilog-index` with the same filters indexes them")
            return
        if opt == "--scilog-index":
            if not {k for k in filters if k != "rebuild"}:
                last = ls.load_selection(asst.db_path)
                if not last:
                    print("eco.ei: which logbooks? e.g. `%ei --scilog-index --location bernina`, or `--all`")
                    return
                print(f"eco.ei: indexing the last choice: {last}")
                filters = {**last, **filters}
            what = "all posts again" if filters.get("rebuild") else "posts newer than the last time"
            print(f"eco.ei: reading SciLog as {reader.identity}; fetching {what} ...")
            result = ls.update(reader, filters, db_path=asst.db_path)
            ls.save_selection({k: v for k, v in filters.items() if k != "rebuild"}, asst.db_path)
            print(f"eco.ei: {result['new_posts']} new SciLog posts from {result['logbooks']} logbooks; index has {result['items']} items")
            if result["dropped"]:
                print(f"eco.ei: {result['dropped']} posts removed: their logbooks are no longer readable")
            return
        print(f"eco.ei: unknown option {opt} (--scilog-token, --scilog-logbooks, --scilog-index, --scilog-search)")
    except ls.TokenError as exc:
        print(f"eco.ei: {exc}")
    except ValueError as exc:
        print(f"eco.ei: {exc}")
    except Exception as exc:
        print(f"eco.ei: SciLog failed: {type(exc).__name__}: {exc}")


def _status(asst, key_where):
    return (
        f"eco.ei: model {asst.model} at {asst.config['base_url']}, "
        f"API key {'from ' + key_where if key_where else 'MISSING -- search only, `%ei --key` once you have one'}; "
        f"{len(asst.history)} earlier question(s) in this conversation.\n"
        "%ei <question> | -n new | -s search only | -b browser | -m MODEL | --paste | --bad TEXT | "
        "--scan FILES | --scan-image VAR | --webcam | --key | --index | --rebuild | --scilog [pgroup] | --notes PATH | "
        "--scilog-token | --scilog-logbooks | --scilog-index | --scilog-search TEXT | "
        "--examples PATH | --models | --config | --help"
    )


_QUESTION_LINE_RX = re.compile(r"^%ei(\s.*)?$", re.S)


def _question_mark_transform(lines):
    """IPython reads a line ending in `?` as a help request for its last word
    (`%ei how to see the fel energy?` -> help on `energy`) before the magic
    runs. Turn a one-line %ei into a plain call first, so questions can end
    in a question mark."""
    if len(lines) != 1:
        return lines
    line = lines[0].rstrip("\n")
    if not line.rstrip().endswith("?") or not _QUESTION_LINE_RX.match(line):
        return lines
    return [f"get_ipython().run_line_magic('ei', {line[3:].strip()!r})\n"]


def register_magic(shell):
    """Register %ei / %%ei on an IPython shell (idempotent). Nothing with
    ECO_NO_EI=1. Cheap: no network, no index, no key lookup until the
    first question."""
    if shell is None or not enabled():
        return
    shell.register_magic_function(ei_magic, magic_kind="line_cell", magic_name="ei")
    cleanup = getattr(shell, "input_transformers_cleanup", None)
    if cleanup is not None and _question_mark_transform not in cleanup:
        cleanup.append(_question_mark_transform)
