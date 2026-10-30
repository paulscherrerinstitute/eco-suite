"""eco.logs_ask -- "how did we do X?" and "did that work?" over everything
recorded about past beamtimes. A prototype.

Sources, all in one SQLite FTS5 full-text index (standard library only):

  kernel logs   every command typed in an eco console, with what came back
                and an outcome: "ok", "warning" or "error" (see below)
  docs/*.md     eco's own documentation, split by heading
  api           eco's classes and functions: signature and first
                docstring paragraph, read from the source with `ast`
                (nothing is imported)
  scilog        paragraph posts of a pgroup's logbook (index_scilog)
  documents     personal logs as .txt/.md/.pdf, scanned or photographed
                notes via a pluggable OCR (index_documents); every chunk is
                dated by the date written above it, and command-looking
                lines in it are indexed as commands of that date

Warnings: a command's outcome is "warning" when a `warning` record, or
output matching WARNING_PATTERNS (timeouts, "not connected", deprecation,
out of limits, ...), follows it before the next command. command_outcomes()
aggregates that per command shape (numbers masked), i.e. "which commands
tend to give bad results, and what did they say".

stage 2 (optional): ask() hands the top hits to a language model as context
and asks it to answer from them, citing them. Any object with a
.complete(prompt) -> str method is a backend; LlamaCppBackend (local GGUF
model, `pip install llama-cpp-python`, offline) and OpenAICompatibleBackend
(any OpenAI-style chat server) are provided, importing nothing until used.

Safety, by construction: this module only reads and returns text. Nothing
it returns is executed. No network except scilog (only when index_scilog is
called) and an explicitly configured OpenAICompatibleBackend.

    import eco.logs_ask as ask
    ask.build_index()                         # kernel logs + docs, incremental
    ask.index_scilog(pgroup="p21234")         # optional
    ask.index_documents("~/notes")            # optional; ocr=... for images
    ask.ask("how do I do a mesh scan?")
    ask.command_outcomes("att.set_transmission")
"""
import ast
import json
import re
import time
import sqlite3
import warnings
from dataclasses import dataclass
from pathlib import Path

from eco import logs_sources
from eco.widgets import kernel_registry

DEFAULT_DB = Path.home() / ".eco" / "history_index.sqlite"
DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
SRC_DIR = Path(__file__).resolve().parent
_SRC_SKIP = ("textual test", "__pycache__")
_MAX_OUTCOME = 400  # chars of result/error/output kept per command
_SCHEMA_VERSION = 2

#: output (results, printed text, logged warnings) matching one of these
#: marks the command it followed as "warning". Case-insensitive. Extend it
#: from the most frequent warning/stderr lines in real kernel logs.
WARNING_PATTERNS = [
    r"\bwarn(ing)?s?\b",
    r"deprecat",
    r"time[ d]?out",
    r"not connected|disconnected|no connection",
    r"\bfail(ed|ure|s)?\b",
    r"could not|cannot|can't|unable to",
    r"out of (range|limits?)|limit (switch|reached|violation)|soft ?limit",
    r"\binvalid\b",
    r"\bnan\b",
]
_WARN_RX = re.compile("|".join(f"(?:{p})" for p in WARNING_PATTERNS), re.I)

_STATUS_ORDER = {"ok": 0, "warning": 1, "error": 2}


@dataclass
class Hit:
    kind: str  # "command" | "doc" | "api" | "scilog" | "note" | "note_command"
    text: str  # the command, or the text chunk
    context: str  # command: what came back; doc: "file > heading"; scilog/note: author/file
    status: str  # "ok" | "warning" | "error" (commands); "ok" otherwise
    flags: str  # command: the warning/error text that set its status
    t: float  # when (0 if unknown)
    source: str  # session label, doc path, "scilog <pgroup>", note file
    score: float

    @property
    def ok(self):
        return self.status == "ok"

    def cite(self):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.t)) if self.t else "undated"
        if self.kind == "command":
            return f"[{when} {self.source}]"
        if self.kind == "doc":
            return f"[doc {self.context}]"
        if self.kind == "api":
            return f"[api {self.context}]"
        if self.kind == "scilog":
            return f"[scilog {when} {self.context}]"
        return f"[note {when} {Path(self.source).name}]"


def _connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    if con.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
        # prototype: an index is a cache of files that are still on disk,
        # so an old layout is simply rebuilt
        con.executescript("DROP TABLE IF EXISTS items; DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS meta;")
        con.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
    con.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, size INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS items USING fts5("
        "text, context, kind UNINDEXED, status UNINDEXED, flags UNINDEXED, t UNINDEXED,"
        " source UNINDEXED, path UNINDEXED)"
    )
    return con


# -- kernel logs ----------------------------------------------------------------


def classify_output(event, rec):
    """(status, flag_text) for one record that followed a command."""
    if event == "error":
        return "error", f"{rec.get('ename', '')}: {rec.get('evalue', '')}"
    if event == "warning":
        return "warning", f"{rec.get('category', 'Warning')}: {rec.get('text', '')}"
    text = str(rec.get("text", "") or "")
    m = _WARN_RX.search(text)
    if m:
        line = next((ln for ln in text.splitlines() if _WARN_RX.search(ln)), text)
        return "warning", line.strip()[:200]
    return "ok", ""


#: commands not indexed: the assistant's own questions (their printed
#: answers would otherwise come back as "what that command returned")
_SKIP_COMMANDS = re.compile(r"^(%%?ei\b|get_ipython\(\)\.run_(line|cell)_magic\(['\"]ei['\"])")


def _command_episodes(path):
    """(code, outcome, status, flags, t, session_label) per input/
    widget_control record of one kernel log: outcome and status come from
    everything up to the next input."""
    session = path.stem
    episodes, cur = [], None
    for line in path.read_text(errors="replace").splitlines():
        try:
            rec = json.loads(line)
            event, t = rec.get("event"), float(rec.get("t"))
        except Exception:
            continue
        if event in ("session_start", "session_start_subprocess"):
            session = f"{rec.get('kind', '')}:{rec.get('label', '')}"
        elif event in ("input", "widget_control"):
            if cur:
                episodes.append(cur)
            code = str(rec.get("code", "")).strip()
            cur = [code, "", "ok", "", t, session] if code and not _SKIP_COMMANDS.match(code) else None
        elif cur is not None and event not in ("session_end",):
            status, flag = classify_output(event, rec)
            if _STATUS_ORDER[status] > _STATUS_ORDER[cur[2]]:
                cur[2], cur[3] = status, flag
            piece = flag if event in ("error", "warning") else str(rec.get("text", "") or "")
            if len(cur[1]) < _MAX_OUTCOME:
                cur[1] = (cur[1] + "\n" + piece).strip()[:_MAX_OUTCOME]
    if cur:
        episodes.append(cur)
    return episodes


def _doc_chunks(path):
    """(heading path, chunk text) per markdown section."""
    chunks, title, heading, buf = [], path.stem, "", []
    text = path.read_text(errors="replace")
    if text.startswith("---\n"):  # YAML front matter (docs/did_you_know)
        end = text.find("\n---\n", 4)
        text = text[end + 5 :] if end > 0 else text
    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            if "".join(buf).strip():
                chunks.append((f"{path.name} > {heading or title}", "\n".join(buf).strip()))
            buf = []
            if len(m.group(1)) == 1:
                title = m.group(2)
            heading = m.group(2)
        else:
            buf.append(line)
    if "".join(buf).strip():
        chunks.append((f"{path.name} > {heading or title}", "\n".join(buf).strip()))
    return chunks


def _first_paragraph(doc, limit=600):
    doc = (doc or "").strip()
    return doc.split("\n\n")[0].strip()[:limit]


def _signature(node):
    """`(a, b=1, *args, c=None, **kw)` of a def, defaults abbreviated."""
    try:
        sig = ast.unparse(node.args)
    except Exception:
        return "(...)"
    return f"({sig[:200]})"


def _api_chunks(path, src_root=SRC_DIR):
    """(qualified name, text) per public class and function of one source
    file: signature plus first docstring paragraph; a class also lists its
    public methods, so "what can `att` do" finds them."""
    try:
        module = "eco." + ".".join(path.relative_to(src_root).with_suffix("").parts)
    except ValueError:
        module = path.stem
    module = module.removesuffix(".__init__")
    with warnings.catch_warnings():  # old files' invalid escape sequences
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(path.read_text(errors="replace"))
    chunks = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            bases = ", ".join(ast.unparse(b) for b in node.bases)[:120]
            methods, init_sig = [], ""
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name == "__init__":
                    init_sig = _signature(item).replace("(self, ", "(").replace("(self)", "()")
                elif not item.name.startswith("_") or item.name.startswith("_widget_"):
                    methods.append(item.name)
                    doc = _first_paragraph(ast.get_docstring(item))
                    if doc:
                        sig = _signature(item).replace("(self, ", "(").replace("(self)", "()")
                        chunks.append((f"{module}.{node.name}.{item.name}", f"{node.name}.{item.name}{sig}: {doc}"))
            text = f"class {node.name}({bases}){init_sig and ' ' + node.name + init_sig}: {_first_paragraph(ast.get_docstring(node))}"
            if methods:
                text += "\nmethods: " + ", ".join(methods)[:800]
            chunks.append((f"{module}.{node.name}", text))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            doc = _first_paragraph(ast.get_docstring(node))
            if doc:
                chunks.append((f"{module}.{node.name}", f"{node.name}{_signature(node)}: {doc}"))
    return chunks


def _index_files(con, sources, rows_for):
    """Shared incremental loop: (re)index each (path, what) whose mtime/size
    changed; rows_for(path, what) -> rows. Returns (n_indexed, notes)."""
    known = {p: (m, s) for p, m, s in con.execute("SELECT path, mtime, size FROM files")}
    n, notes = 0, {}
    with con:
        for path, what in sources:
            try:
                st = path.stat()
            except OSError:
                continue
            if known.get(str(path)) == (st.st_mtime, st.st_size):
                continue
            try:
                rows, note = rows_for(path, what)
            except Exception as exc:
                notes[str(path)] = f"{type(exc).__name__}: {exc}"
                continue  # no files row written: retried next time
            if note:
                notes[str(path)] = note
            con.execute("DELETE FROM items WHERE path = ?", (str(path),))
            con.executemany("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)", rows)
            con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?)", (str(path), st.st_mtime, st.st_size))
            n += 1
    return n, notes


def build_index(db_path=DEFAULT_DB, log_dir=None, doc_dirs=(DOCS_DIR,), src_dirs=(SRC_DIR,)):
    """Add new/changed kernel logs, docs and eco source files (api) to the
    index; unchanged files (same mtime and size) are skipped, so re-running
    is cheap. Returns {"files_indexed": n, "items": total}."""
    con = _connect(db_path)
    sources = [(p, "log") for p in kernel_registry.find_all_logs(log_dir)]
    for d in doc_dirs:
        if d and Path(d).exists():
            sources += [(p, "doc") for p in sorted(Path(d).rglob("*.md")) if "_build" not in p.parts]
    for d in src_dirs:
        if d and Path(d).exists():
            sources += [
                (p, "api") for p in sorted(Path(d).rglob("*.py"))
                if not any(skip in p.parts for skip in _SRC_SKIP)
            ]

    def rows_for(path, what):
        if what == "log":
            return [
                (code, out, "command", status, flags, t, sess, str(path))
                for code, out, status, flags, t, sess in _command_episodes(path)
            ], ""
        if what == "api":
            return [(text, where, "api", "ok", "", 0.0, str(path), str(path)) for where, text in _api_chunks(path)], ""
        return [(text, where, "doc", "ok", "", 0.0, str(path), str(path)) for where, text in _doc_chunks(path)], ""

    n, _notes = _index_files(con, sources, rows_for)
    total = con.execute("SELECT count(*) FROM items").fetchone()[0]
    con.close()
    return {"files_indexed": n, "items": total}


# -- personal logs, scans, scilog -----------------------------------------------


def index_documents(paths, db_path=DEFAULT_DB, ocr=None):
    """Index personal logs: files or directories (recursively) of
    .txt/.md/.log/.pdf, and images if `ocr` (PIL.Image -> str) is given.
    Each chunk is dated by the date written above it (logs_sources.
    dated_chunks; file mtime if none), and every command-looking line in it
    becomes a "note_command" item of that date, so it is found like a typed
    command. Incremental like build_index. Returns {"files_indexed", "items",
    "notes": {path: why skipped/partial}}."""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    files = []
    for p in paths:
        p = Path(p).expanduser()
        cand = sorted(p.rglob("*")) if p.is_dir() else [p]
        files += [
            f for f in cand
            if f.is_file() and f.suffix.lower() in (logs_sources.TEXT_SUFFIXES | logs_sources.IMAGE_SUFFIXES | {".pdf"})
        ]
    con = _connect(db_path)

    def rows_for(path, _what):
        text, note = logs_sources.document_text(path, ocr=ocr)
        if not text.strip():
            raise ValueError(note or "no text")
        rows = []
        for t, chunk in logs_sources.dated_chunks(text, default_t=path.stat().st_mtime):
            rows.append((chunk, path.name, "note", "ok", "", t or 0.0, str(path), str(path)))
            for cmd in logs_sources.command_lines(chunk):
                rows.append((cmd, chunk[:300], "note_command", "ok", "", t or 0.0, str(path), str(path)))
        return rows, note

    n, notes = _index_files(con, [(f, "document") for f in files], rows_for)
    total = con.execute("SELECT count(*) FROM items").fetchone()[0]
    con.close()
    return {"files_indexed": n, "items": total, "notes": notes}


def index_scilog(pgroup=None, db_path=DEFAULT_DB, client=None, limit=500):
    """Fetch scilog paragraph posts newer than the last fetch for this
    pgroup and index them (dated by their creation time; command-looking
    lines also as "note_command"). `client` defaults to eco's configured
    scilog client (eco.logs._scilog_client). Returns {"new_posts", "items"}."""
    if client is None:
        from eco.logs import _scilog_client

        client = _scilog_client(pgroup=pgroup)
    key = f"scilog_last:{pgroup}"
    con = _connect(db_path)
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    posts = logs_sources.scilog_snippets(client, after_iso=row[0] if row else None, limit=limit)
    with con:
        for p in posts:
            path = f"scilog:{pgroup}:{p['id']}"
            con.execute("DELETE FROM items WHERE path = ?", (path,))
            context = p["author"] + (f" [{', '.join(p['tags'])}]" if p["tags"] else "")
            source = f"scilog {pgroup}"
            con.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)", (p["text"], context, "scilog", "ok", "", p["t"], source, path))
            for cmd in logs_sources.command_lines(p["text"]):
                con.execute(
                    "INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                    (cmd, p["text"][:300], "note_command", "ok", "", p["t"], source, path),
                )
        if posts:
            con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, posts[-1]["created_at"]))
    total = con.execute("SELECT count(*) FROM items").fetchone()[0]
    con.close()
    return {"new_posts": len(posts), "items": total}


# -- querying -----------------------------------------------------------------------


#: question words that match everything and rank nothing
STOPWORDS = frozenset(
    "a an and are as at be by can could do does did for from has have how i if in into is it its me my "
    "of on or our should so that the their them then there these this to use used using was we were "
    "what when where which who why will with would you your please want need eco".split()
)


def _fts_query(text):
    # same split as FTS5's unicode61 tokenizer: "att.set_transmission(0.1)"
    # -> att, set, transmission, 0, 1
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1 and w not in STOPWORDS]
    return " OR ".join(f'"{w}"*' for w in dict.fromkeys(words))


ALL_KINDS = ("command", "doc", "api", "scilog", "note", "note_command")


def search(query, k=10, db_path=DEFAULT_DB, kinds=ALL_KINDS, only_ok=False, since=None, until=None):
    """Best-matching index items for `query` (plain words, no syntax
    needed), optionally only between `since`/`until` (epoch seconds).
    Repeated identical items are collapsed to their latest occurrence."""
    q = _fts_query(query)
    if not q:
        return []
    kinds = tuple(kinds)
    con = _connect(db_path)
    rows = con.execute(
        "SELECT text, context, kind, status, flags, t, source, bm25(items) FROM items"
        f" WHERE items MATCH ? AND kind IN ({','.join('?' * len(kinds))}) ORDER BY bm25(items) LIMIT ?",
        (q, *kinds, k * 10),
    ).fetchall()
    con.close()
    hits, seen = [], set()
    for text, context, kind, status, flags, t, source, score in rows:
        t = float(t or 0)
        if kind not in kinds or (only_ok and status != "ok"):
            continue
        if (since is not None and t < since) or (until is not None and t > until):
            continue
        key = (kind, text)
        if key in seen:
            continue
        seen.add(key)
        hits.append(Hit(kind, text, context, status, flags, t, source, -float(score)))
        if len(hits) >= k:
            break
    return hits


def command_shape(code):
    """`att.set_transmission(0.1)` and `att.set_transmission(0.36)` are the
    same command for statistics: numbers -> #, string literals -> '…'."""
    code = re.sub(r"(['\"]).*?\1", "'…'", code)
    return re.sub(r"(?<![\w.])-?\d+(\.\d*)?(e-?\d+)?", "#", code).strip()


def command_outcomes(query=None, db_path=DEFAULT_DB, min_runs=1, limit=30):
    """Per command shape: runs, warnings, errors, last run, and the most
    recent warning/error text -- sorted worst first (share of bad runs).
    `query` narrows to commands containing that text. The answer to "which
    commands gave a bad result, and what did they say"."""
    con = _connect(db_path)
    sql = "SELECT text, status, flags, t FROM items WHERE kind = 'command'"
    rows = con.execute(sql).fetchall()
    con.close()
    stats = {}
    for text, status, flags, t in rows:
        if query and query.lower() not in text.lower():
            continue
        s = stats.setdefault(command_shape(text), {"runs": 0, "warning": 0, "error": 0, "last_t": 0.0, "last_flag": ""})
        s["runs"] += 1
        if status in ("warning", "error"):
            s[status] += 1
            if float(t) >= s.get("last_bad_t", 0):
                s["last_bad_t"], s["last_flag"] = float(t), flags
        s["last_t"] = max(s["last_t"], float(t))
    out = [
        {"command": shape, **{k: v for k, v in s.items() if k != "last_bad_t"}}
        for shape, s in stats.items() if s["runs"] >= min_runs
    ]
    out.sort(key=lambda r: (-(r["warning"] + r["error"]) / r["runs"], -r["runs"]))
    return out[:limit]


PROMPT = """You help users of "eco", the Python control system of the Bernina beamline \
at SwissFEL. Answer the question using ONLY the context below: commands \
users actually ran (each marked ok / WARNING / FAILED, with what came back), \
commands found in logbooks and personal notes, and excerpts of the eco docs. \
Prefer commands marked ok. If the commands relevant to the question often \
gave warnings or failed, say so and quote the warning. Give runnable Python \
where you can, and cite the context items you used by their [tag]. If the \
context does not answer the question, say so -- do not invent device names, \
PVs or functions.

Context:
{context}

Question: {question}
Answer:"""


def _status_label(h):
    return {"ok": "ok", "warning": "WARNING", "error": "FAILED"}[h.status]


def build_prompt(question, hits):
    lines = []
    for h in hits:
        if h.kind == "command":
            flag = f" -- {h.flags}" if h.flags else ""
            lines.append(f"{h.cite()} ({_status_label(h)}{flag}) >>> {h.text}\n    -> {h.context[:200]}")
        elif h.kind == "note_command":
            lines.append(f"{h.cite()} (from notes, outcome unknown) >>> {h.text}")
        else:
            lines.append(f"{h.cite()}\n{h.text[:800]}")
    return PROMPT.format(context="\n\n".join(lines), question=question)


def ask(question, backend=None, k=8, db_path=DEFAULT_DB, since=None, until=None):
    """Answer `question` from the index. Without a backend: the top hits as
    text (stage 1 -- useful on its own). With one: the model's answer,
    followed by the hits it was given, so every claim can be checked."""
    hits = search(question, k=k, db_path=db_path, since=since, until=until)
    hits.sort(key=lambda h: _STATUS_ORDER[h.status])  # stable: what worked first, rank kept within
    if not hits:
        return "Nothing in the command history, logbooks, notes or docs matches that."
    listing = "\n".join(
        f"{h.cite()} {'' if h.ok else f'({_status_label(h).lower()}: {h.flags[:80]}) '}{h.text.splitlines()[0][:160]}"
        for h in hits
    )
    if backend is None:
        return listing
    answer = backend.complete(build_prompt(question, hits))
    return f"{answer.strip()}\n\n-- based on:\n{listing}"


# -- model backends ----------------------------------------------------------------


class LlamaCppBackend:
    """Local, offline model: `pip install llama-cpp-python` and a GGUF file
    (a small instruction-tuned model is enough). Loaded on first use."""

    def __init__(self, model_path, n_ctx=8192, n_threads=None, max_tokens=600):
        self.model_path, self.n_ctx, self.n_threads, self.max_tokens = str(model_path), n_ctx, n_threads, max_tokens
        self._llm = None

    def complete(self, prompt):
        if self._llm is None:
            from llama_cpp import Llama

            self._llm = Llama(model_path=self.model_path, n_ctx=self.n_ctx, n_threads=self.n_threads, verbose=False)
        out = self._llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}], max_tokens=self.max_tokens, temperature=0.1
        )
        return out["choices"][0]["message"]["content"]


class OpenAICompatibleBackend:
    """Any OpenAI-compatible chat endpoint (llama.cpp's llama-server, vLLM,
    an institute-hosted service). Standard library only.

    `options` go into every request body as they are, e.g.
    {"chat_template_kwargs": {"enable_thinking": False}} for a Qwen model
    on vLLM, or {"reasoning_effort": "low"}. A reasoning model's thinking
    ("reasoning"/"reasoning_content" deltas) is dropped, only the answer
    is returned."""

    def __init__(self, base_url, model, api_key=None, timeout=120, max_tokens=1500, options=None):
        self.base_url, self.model, self.api_key = base_url.rstrip("/"), model, api_key
        self.timeout, self.max_tokens, self.options = timeout, max_tokens, dict(options or {})

    def _request(self, path, body=None):
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers={"Content-Type": "application/json"})
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        return urllib.request.urlopen(req, timeout=self.timeout)

    def models(self):
        """Model ids the endpoint offers."""
        with self._request("/models") as r:
            return [m["id"] for m in json.loads(r.read()).get("data", [])]

    def chat(self, messages, on_delta=None, stop=None):
        """The answer to `messages` ([{"role", "content"}, ...]). With
        `on_delta`, the answer is streamed and on_delta(text) is called
        per piece as it arrives. `stop` (a threading.Event) ends a stream
        early; the text so far is returned."""
        body = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens, "temperature": 0.1}
        body.update(self.options)
        if on_delta is None:
            with self._request("/chat/completions", body) as r:
                return json.loads(r.read())["choices"][0]["message"].get("content") or ""
        body["stream"] = True
        parts = []
        with self._request("/chat/completions", body) as r:
            for raw in r:
                if stop is not None and stop.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    choices = json.loads(data).get("choices") or [{}]
                except ValueError:
                    continue
                piece = (choices[0].get("delta") or {}).get("content")
                if piece:
                    parts.append(piece)
                    on_delta(piece)
        return "".join(parts)

    def complete(self, prompt):
        return self.chat([{"role": "user", "content": prompt}])
