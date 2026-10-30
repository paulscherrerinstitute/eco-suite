"""eco.ei -- ask eco. `%ei how do I do a mesh scan with two motors?`

Answers questions about using eco from what eco knows about itself and
about past beamtimes, through a language model:

  - eco's docs (docs/*.md) and its API (class/function signatures and
    docstrings, read from the source without importing it),
  - the command history of every eco console (kernel logs), each command
    marked ok / warning / failed by what followed it,
  - SciLog posts and personal notes, once indexed (`%ei --scilog`,
    `%ei --notes PATH`),
  - the running session's namespace: which devices exist and of which
    class, without initialising anything lazy.

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
    %ei -m openai/gpt-oss-120b ...          another model (kept)
    %ei --paste     last answer's code to the next prompt
    %ei --bad att   commands like `att...` that gave warnings/errors
    %ei --index | --scilog [pgroup] | --notes PATH | --models | --config

From Python: `eco.ei.ask("...")`, `eco.ei.assistant()`.
"""
import json
import os
import re
import shlex
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from eco import logs_ask

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
    "max_tokens": 1500,
    "timeout": 180,
    # how many search hits of each kind go to the model
    "hits": {"command": 10, "note": 5, "doc": 6, "api": 8},
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


def api_key(base_url=DEFAULT_CONFIG["base_url"]):
    """(key, where it came from), or (None, None)."""
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
    key = _gopass_key()
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
Until then `%ei -s ...` searches without a model."""


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

    def sources(self):
        """How many items of each kind the answer was given, e.g.
        "10 commands, 6 doc, 8 api"."""
        counts = {}
        for h in self.hits:
            counts[h.kind] = counts.get(h.kind, 0) + 1
        return ", ".join(f"{n} {kind}" for kind, n in counts.items()) or "nothing found"

    def __str__(self):
        return self.text


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
        self._backend = backend
        self._indexed_at = 0.0
        self._index_lock = threading.Lock()

    # model ---------------------------------------------------------------

    @property
    def model(self):
        return self.config["model"]

    def set_model(self, model):
        self.config["model"] = model
        if isinstance(self._backend, logs_ask.OpenAICompatibleBackend):
            self._backend = None

    def backend(self):
        """The configured backend, or None without an API key."""
        if self._backend is None:
            cfg = self.config
            key, _where = api_key(cfg["base_url"])
            if key is None:
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

    # index ---------------------------------------------------------------

    def refresh_index(self, force=False, report=print):
        """Incremental logs_ask.build_index(), at most every
        reindex_interval seconds unless forced."""
        with self._index_lock:
            if not force and time.time() - self._indexed_at < self.config["reindex_interval"]:
                return None
            fresh = not Path(self.db_path).exists()
            if fresh and report:
                report("eco.ei: building the search index (docs, eco source, command history) -- the first time takes a while ...")
            t0 = time.time()
            result = logs_ask.build_index(db_path=self.db_path)
            self._indexed_at = time.time()
            if report and (fresh or force):
                report(f"eco.ei: index has {result['items']} items ({result['files_indexed']} files updated, {time.time() - t0:.1f} s)")
            return result

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
        groups = [
            (("command",), quota.get("command", 10)),
            (("scilog", "note", "note_command"), quota.get("note", 5)),
            (("doc",), quota.get("doc", 6)),
            (("api",), quota.get("api", 8)),
        ]
        hits = []
        for kinds, k in groups:
            if k:
                found = logs_ask.search(query, k=k, db_path=self.db_path, kinds=kinds)
                if kinds == ("command",):
                    found.sort(key=lambda h: logs_ask._STATUS_ORDER[h.status])
                hits += found
        return hits

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
                context.append(f"{h.cite()} {h.text[:700]}")
            else:
                context.append(f"{h.cite()}\n{h.text[:1200]}")
        ns_text = self.namespace.text(question)
        user = (
            (ns_text + "\n\n" if ns_text else "")
            + "Context:\n\n"
            + ("\n\n".join(context) if context else "(nothing found)")
            + f"\n\nQuestion: {question}"
        )
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        for q, a in self.history[-self.config["history_turns"]:]:
            msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        msgs.append({"role": "user", "content": user})
        return msgs

    def ask(self, question, on_delta=None, stop=None, new=False, search_only=False, report=print):
        """Answer `question`. on_delta(text) receives the answer as it
        streams. search_only (or no API key): the search hits, no model."""
        question = question.strip()
        if new:
            self.history = []
        try:
            self.refresh_index(report=report)
        except Exception as exc:
            if report:
                report(f"eco.ei: index update failed ({type(exc).__name__}: {exc}); searching the existing index")
        t0 = time.time()
        hits = self.retrieve(question, follow_up=not search_only)
        backend = None if search_only else self.backend()
        if backend is None:
            if not search_only and report:
                report(NO_KEY_HELP.format(base_url=self.config["base_url"]))
            text = _listing(hits) or "Nothing in the docs, the eco source, the command history, logbooks or notes matches that."
            # not self.last: that stays the last model answer, for --paste
            return Answer(question, text, hits, "", time.time() - t0)
        text = backend.chat(self.messages(question, hits), on_delta=on_delta, stop=stop)
        self.history.append((question, text))
        self.last = Answer(question, text, hits, self.model, time.time() - t0)
        return self.last

    def reset(self):
        self.history = []


def _listing(hits):
    lines = []
    for h in hits:
        first = h.text.splitlines()[0][:160] if h.text else ""
        bad = "" if h.ok else f"({logs_ask._status_label(h).lower()}: {h.flags[:80]}) "
        lines.append(f"{h.cite()} {bad}{first}")
    return "\n".join(lines)


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

    out = sys.stdout

    def on_delta(piece):
        out.write(piece)
        out.flush()

    try:
        ans = asst.ask(question, on_delta=on_delta, **kwargs)
    except KeyboardInterrupt:
        print("\neco.ei: interrupted")
        return None
    except Exception as exc:
        print(f"\neco.ei: {asst.model} at {asst.config['base_url']} failed: {type(exc).__name__}: {exc}")
        return None
    if ans.model:
        print(f"\n\n-- {ans.model}, {ans.seconds:.1f} s; context: {ans.sources()}")
        if ans.code:
            print("-- `%ei --paste` puts the code at the next prompt")
    else:
        print(ans.text)
    return ans


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
        return None
    if opt in ("-h", "--help"):
        print(__doc__)
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
    if opt == "--scilog":
        pgroup = rest or None
        result = logs_ask.index_scilog(pgroup=pgroup, db_path=asst.db_path)
        print(f"eco.ei: {result['new_posts']} new SciLog posts indexed ({pgroup or 'default pgroup'})")
        return None
    if opt == "--notes":
        result = logs_ask.index_documents(rest, db_path=asst.db_path)
        print(f"eco.ei: {result['files_indexed']} files indexed, {result['items']} items in total")
        for path, note in result["notes"].items():
            print(f"  {path}: {note}")
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
    return _print_answer(asst, text)


def _status(asst, key_where):
    return (
        f"eco.ei: model {asst.model} at {asst.config['base_url']}, "
        f"API key {'from ' + key_where if key_where else 'MISSING'}; "
        f"{len(asst.history)} earlier question(s) in this conversation.\n"
        "%ei <question> | -n new | -s search only | -m MODEL | --paste | --bad TEXT | "
        "--index | --scilog [pgroup] | --notes PATH | --models | --config | --help"
    )


def register_magic(shell):
    """Register %ei / %%ei on an IPython shell (idempotent)."""
    if shell is None:
        return
    shell.register_magic_function(ei_magic, magic_kind="line_cell", magic_name="ei")
