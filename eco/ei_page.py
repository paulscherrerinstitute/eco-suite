"""eco.ei_page -- a %ei answer as a page in the browser.

`%ei -b <question>` (or `%ei -b` for the last answer) writes one
self-contained HTML file and opens it: the answer, its numbered sources,
and below them each source in its context. Citations in the answer and
the numbers in the source list jump there.

A SciLog post is read from SciLog again when the page is made, as
the same account %ei indexes with (eco.logs_scilog.reader): as it looks
there, with its images and tables, between the posts written just before
and after it in its logbook. Without SciLog (no network, no token) the
page shows the indexed text instead. Every other source shows what
`%ei --open N` shows.

Pages go to ~/.eco/ei_pages (readable only by you: they hold SciLog
posts); the last KEEP are kept.
"""

import base64
import html
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from eco import logs_ask, logs_sources

PAGES_DIR = Path.home() / ".eco" / "ei_pages"
KEEP = 30
#: posts shown before and after a cited SciLog post
AROUND = 3
#: images larger than this are linked, not embedded
MAX_IMAGE_BYTES = 8_000_000

_TAGS = {
    "p", "br", "b", "strong", "i", "em", "u", "s", "sub", "sup", "span", "div", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
    "a", "img", "figure", "figcaption",
}
_ATTRIBUTES = {
    "a": {"href"},
    "img": {"title", "alt", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "col": {"span"},
    "ol": {"start"},
}
_IMG_RX = re.compile(r"<img\b([^>]*)>", re.I)
_ATTR_RX = re.compile(r'(\w+)="([^"]*)"')


def clean_html(text):
    """Untrusted HTML (a SciLog post, a model's answer) reduced to plain
    formatting: no scripts, styles, event handlers or foreign URL schemes.
    Without nh3, the text alone."""
    try:
        import nh3
    except ImportError:
        return f"<pre>{html.escape(logs_sources.html_to_text(text))}</pre>"
    return nh3.clean(text, tags=_TAGS, attributes=_ATTRIBUTES, url_schemes={"http", "https", "mailto"})


# -- SciLog ---------------------------------------------------------------------------


def _scilog_section(reader, hit, around):
    """[(post, cited)] read from SciLog, and the images they show."""
    _logbook, post_id = logs_ask.scilog_ids(hit.path)
    post = reader.post(post_id)
    posts = reader.around(post, around)
    files = {}
    for p in posts:
        for f in p.get("files") or ():
            if f.get("fileHash") and f.get("fileId"):
                files[f["fileHash"]] = f["fileId"]
    return [(p, p.get("id") == post_id) for p in posts], files


_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg"}


def _images(reader, file_ids, directory=None):
    """{file id: URL or None}, fetched in parallel: files in `directory`
    (each once, referenced relative to the page), or data URIs without one."""

    def one(file_id):
        try:
            data, ctype = reader.image(file_id)
        except Exception:
            return file_id, None
        ctype = ctype.split(";")[0].strip()
        if len(data) > MAX_IMAGE_BYTES or ctype not in _EXTENSIONS:
            return file_id, None
        if directory is None:
            return file_id, f"data:{ctype};base64,{base64.b64encode(data).decode()}"
        name = re.sub(r"[^A-Za-z0-9_-]", "_", file_id) + _EXTENSIONS[ctype]
        _write_private(Path(directory) / name, data)
        return file_id, f"{Path(directory).name}/{name}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(one, sorted(set(file_ids))))


def _post_html(post, files, images):
    body = clean_html(post.get("textcontent") or "")

    def img(m):
        attrs = dict(_ATTR_RX.findall(m.group(1)))
        uri = images.get(files.get(attrs.get("title", "")))
        if not uri:
            return '<span class="missing">[image not shown]</span>'
        size = "".join(f' {k}="{html.escape(attrs[k])}"' for k in ("width", "height") if k in attrs)
        return f'<img src="{uri}"{size} alt="">'

    return _IMG_RX.sub(img, body)


def _when(iso):
    import datetime

    try:
        t = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return str(iso or "")
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


# -- the page -------------------------------------------------------------------------


def _answer_html(ans, cited):
    """The model's answer as HTML, its citations linked to the sources."""
    import markdown

    from eco import ei

    body = clean_html(markdown.markdown(ans.text, extensions=["fenced_code", "tables"]))
    rx, numbers = _citation_rx(cited, ei._citation_forms)
    if rx is None:
        return body

    def link(m):
        n = next(numbers[i] for i, g in enumerate(m.groups()) if g is not None)
        return f'<a class="cite" href="#src-{n}">{m.group(0)}</a>'

    # only in text, not inside tags or code
    parts = re.split(r"(<pre>.*?</pre>|<code>.*?</code>|<[^>]+>)", body, flags=re.S)
    return "".join(p if p.startswith("<") else rx.sub(link, p) for p in parts)


def _citation_rx(cited, citation_forms):
    """One regex for every way the answer may cite a source, as it appears
    in HTML text, and the source number of each of its groups. A form
    starting with "[" but not closed ("[scilog 2026-05-08") runs to the
    end of the tag, which may hold bracketed parts: "[... [logbook]]"."""
    alternatives, numbers = [], []
    for n, h in enumerate(cited, 1):
        for f in citation_forms(h):
            if f.startswith("[") and not f.endswith("]"):
                pattern = re.escape(html.escape(f, quote=False)) + r"(?:[^\[\]<]|\[[^\[\]<]*\])*\]"
            else:
                tag = f if f.startswith("[") else f"[{f}]"
                pattern = re.escape(html.escape(tag, quote=False))
            alternatives.append((len(f), f"({pattern})", n))
    if not alternatives:
        return None, []
    alternatives.sort(key=lambda a: -a[0])  # the longest, most specific form first
    numbers = [n for _l, _p, n in alternatives]
    return re.compile("|".join(p for _l, p, _n in alternatives)), numbers


def render(ans, db_path=logs_ask.DEFAULT_DB, reader=None, around=AROUND, report=None, image_dir=None):
    """The page for `ans` (an eco.ei.Answer, also a search-only one), as
    HTML. reader: a logs_scilog Reader (default: logs_scilog.reader());
    False: no SciLog, the indexed text only. image_dir: where SciLog's
    images go, next to the page (default: embedded in it)."""
    from eco import ei

    cited = ans.cited
    scilog_hits = [h for h in cited if h.kind == "scilog" and logs_ask.scilog_ids(h.path)[1]]
    fetched, failure = {}, None
    if scilog_hits and reader is not False:
        try:
            if reader is None:
                from eco import logs_scilog

                reader = logs_scilog.reader(timeout=20)
            if report:
                report(f"eco.ei: reading {len(scilog_hits)} SciLog post(s) and their neighbours ...")
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(lambda h: _try(_scilog_section, reader, h, around), scilog_hits))
            files = {}
            for h, (result, exc) in zip(scilog_hits, results):
                if exc is None:
                    fetched[id(h)] = result
                    files.update(result[1])
                else:
                    failure = failure or exc
            if files and image_dir is not None:
                Path(image_dir).mkdir(mode=0o700, exist_ok=True)
            images = _images(reader, files.values(), image_dir) if files else {}
        except Exception as exc:
            failure, images = exc, {}
    else:
        images = {}

    out = [_HEAD.format(title=html.escape(ans.question[:80] or "eco.ei"))]
    out.append(f"<header><h1>{html.escape(ans.question)}</h1>")
    meta = f"{ans.model}, {ans.seconds:.1f} s" if ans.model else "search only (no model)"
    out.append(f'<p class="meta">%ei &middot; {html.escape(meta)} &middot; {time.strftime("%Y-%m-%d %H:%M")}</p></header>')
    if ans.model:
        out.append(f'<section class="answer">{_answer_html(ans, cited)}</section>')
    if failure is not None:
        out.append(
            f'<p class="note">SciLog could not be read ({html.escape(type(failure).__name__)}: '
            f"{html.escape(str(failure).splitlines()[0] if str(failure) else '')}); its posts show the indexed text.</p>"
        )
    if cited:
        out.append('<nav><h2>Sources</h2><ol class="sources">')
        for n, h in enumerate(cited, 1):
            out.append(f'<li><a href="#src-{n}">{html.escape(h.cite())}</a> {_first_line(h)}</li>')
        out.append("</ol></nav>")
    else:
        out.append(f'<p class="note">{html.escape(ans.text)}</p>')

    for n, h in enumerate(cited, 1):
        where = h.location()
        url = ei._url(where)
        link = f' <a class="where" href="{html.escape(url)}">{html.escape(where)}</a>' if url else ""
        out.append(f'<section class="source" id="src-{n}"><h2><span class="n">{n}</span> {html.escape(h.cite())}</h2>')
        out.append(f'<p class="meta">{html.escape(h.source)}{link}</p>')
        if id(h) in fetched:
            posts, files = fetched[id(h)]
            for p, is_cited in posts:
                author = html.escape(p.get("createdBy") or "")
                tags = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in p.get("tags") or ())
                out.append(
                    f'<article class="post{" cited" if is_cited else ""}">'
                    f'<p class="meta">{_when(p.get("createdAt"))} &middot; {author} {tags}</p>'
                    f"{_post_html(p, files, images)}</article>"
                )
        else:
            text = logs_ask.item_context(h, db_path=db_path)
            out.append(f"<pre>{html.escape(text)}</pre>")
        out.append('<p class="top"><a href="#">&uarr; top</a></p></section>')
    out.append("</main></body></html>")
    return "\n".join(out)


def _try(fn, *args):
    try:
        return fn(*args), None
    except Exception as exc:
        return None, exc


def _first_line(hit):
    first = hit.text.splitlines()[0] if hit.text else ""
    return f'<span class="first">{html.escape(first[:160])}</span>'


def write(ans, directory=None, **kwargs):
    """render() into a new file under ~/.eco/ei_pages (mode 600, SciLog's
    images in <name>_files/ next to it); returns its path. Older pages
    beyond the last KEEP are removed, with their images."""
    directory = Path(directory or PAGES_DIR)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass  # a filesystem without permissions (EOPNOTSUPP, e.g. a CIFS home): its mount decides
    slug = re.sub(r"[^a-z0-9]+", "-", ans.question.lower())[:40].strip("-") or "answer"
    path = directory / f"{time.strftime('%Y%m%d_%H%M%S')}_{slug}.html"
    text = render(ans, image_dir=path.with_name(path.stem + "_files"), **kwargs)
    _write_private(path, text.encode("utf-8"))
    for old in sorted(directory.glob("*.html"))[:-KEEP]:
        shutil.rmtree(old.with_name(old.stem + "_files"), ignore_errors=True)
        try:
            old.unlink()
        except OSError:
            pass
    return path


def _write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def can_open_browser():
    """A browser can be started here: a display, and not over ssh (a
    browser started on a console over a forwarded X11 display is slow)."""
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_page(path):
    """Open `path` in the browser if one can be started here; True if so."""
    if not can_open_browser():
        return False
    import webbrowser

    return webbrowser.open(Path(path).as_uri())


_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg: #ffffff; --fg: #1d1f23; --muted: #5d6470; --line: #d9dde3; --accent: #1f5fbf;
        --cited: #fff6d6; --cited-line: #d9a400; --code: #f3f4f6; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #16181c; --fg: #e3e5e8; --muted: #9aa1ab; --line: #343941; --accent: #7fb0ff;
          --cited: #2e2a1a; --cited-line: #c79a1c; --code: #23262c; }}
}}
body {{ background: var(--bg); color: var(--fg); margin: 0;
       font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 900px; margin: 0 auto; padding: 16px; }}
h1 {{ font-size: 1.35em; margin: 0.4em 0 0.1em; }}
h2 {{ font-size: 1.1em; margin: 1.6em 0 0.3em; }}
a {{ color: var(--accent); }}
.meta {{ color: var(--muted); font-size: 0.88em; margin: 0.2em 0 0.6em; word-break: break-all; }}
.note {{ border-left: 3px solid var(--cited-line); padding: 0.3em 0.8em; color: var(--muted); }}
pre, code {{ background: var(--code); border-radius: 4px; font: 13px/1.45 ui-monospace, monospace; }}
pre {{ padding: 0.7em; overflow-x: auto; white-space: pre-wrap; }}
code {{ padding: 0 0.2em; }}
.answer {{ border-bottom: 1px solid var(--line); padding-bottom: 0.8em; }}
.sources li {{ margin: 0.25em 0; }}
.sources .first {{ color: var(--muted); font-size: 0.9em; }}
.source {{ border-top: 1px solid var(--line); margin-top: 1.4em; }}
.source .n {{ display: inline-block; min-width: 1.6em; color: var(--muted); }}
.post {{ border: 1px solid var(--line); border-radius: 6px; padding: 0.4em 0.9em; margin: 0.6em 0; opacity: 0.8; }}
.post.cited {{ background: var(--cited); border-color: var(--cited-line); opacity: 1; }}
.post img {{ max-width: 100%; height: auto; }}
.post table {{ border-collapse: collapse; display: block; overflow-x: auto; }}
.post td, .post th {{ border: 1px solid var(--line); padding: 0.2em 0.5em; }}
.tag {{ border: 1px solid var(--line); border-radius: 3px; padding: 0 0.3em; margin-left: 0.3em; font-size: 0.85em; }}
.missing {{ color: var(--muted); font-style: italic; }}
.top {{ text-align: right; font-size: 0.85em; }}
a.cite {{ text-decoration: none; border-bottom: 1px dotted var(--accent); }}
:target {{ scroll-margin-top: 8px; }}
</style></head><body><main>"""
