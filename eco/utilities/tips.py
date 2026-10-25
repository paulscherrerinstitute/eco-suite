""""Did you know?" tips in the session.

The tips are the Markdown files in ``docs/did_you_know/`` (one per feature,
``<date>-<slug>.md`` with a small front matter, see that folder's
``index.md``) -- the same files the Sphinx docs show. Here they are:

- shown in a small Qt window by ``eco.tip()`` (eco.widgets.tip_qt), or
  printed in the terminal where there is no display. The session start
  (``eco/startup_inline.py``) only prints links to the docs site and its
  random-tip page.

Links to other docs pages (``{doc}`../scanning``` in a tip) point at the
docs site, ``ECO_DOCS_URL`` (default: the server on saresb-cons-04).

The tips are read from the checkout this package lives in. An eco installed
without its ``docs/`` folder has no tips; everything here then quietly does
nothing.
"""
import os
import random
import re
import shutil
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

DOCS_URL = os.environ.get("ECO_DOCS_URL", "http://saresb-cons-04.psi.ch:8095/")

TIPS_DIR = Path(__file__).resolve().parents[2] / "docs" / "did_you_know"

_DOC_ROLE = re.compile(r"\{doc\}`([^`]+)`")


@dataclass
class Tip:
    name: str  # file name without .md, e.g. "2026-09-12-ellipsis-picker"
    title: str
    body: str  # Markdown, without front matter and title
    added: str = ""
    tags: list = field(default_factory=list)
    details: str = ""  # Sphinx docname of the full page, or ""

    @property
    def url(self):
        """This tip's own page on the docs site."""
        return doc_url(f"did_you_know/{self.name}")

    @property
    def details_url(self):
        return doc_url(self.details) if self.details else None


def doc_url(docname):
    """URL of a docs page given as a Sphinx docname, as written in a tip
    (``../scanning``, ``examples/deltatau_config``)."""
    docname = docname.strip()
    while docname.startswith("../"):
        docname = docname[3:]
    return DOCS_URL.rstrip("/") + "/" + docname + ".html"


def _doc_label(docname):
    return docname.rstrip("/").split("/")[-1].replace("_", " ")


def _parse_front_matter(text):
    """(dict, rest). Only the simple `key: value` / `key: [a, b]` lines the
    tips use -- no YAML dependency."""
    meta = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end < 0:
        return meta, text
    for line in text[3:end].strip().splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        meta[key.strip()] = value
    return meta, text[end + 4 :].lstrip("\n")


def parse_tip(path):
    path = Path(path)
    meta, rest = _parse_front_matter(path.read_text())
    title, body = path.stem, rest
    lines = rest.splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        body = "\n".join(lines[1:]).strip("\n")
    return Tip(
        name=path.stem,
        title=title,
        body=body,
        added=str(meta.get("added", "")),
        tags=meta.get("tags") or [],
        details=meta.get("details") or "",
    )


def load_tips():
    """All tips, oldest first; [] if this eco has no docs folder."""
    if not TIPS_DIR.is_dir():
        return []
    return [parse_tip(p) for p in sorted(TIPS_DIR.glob("20*.md"))]


def random_tip(exclude=None):
    """A random tip, other than `exclude` (a Tip or name) if possible."""
    tips = load_tips()
    if not tips:
        return None
    exclude = getattr(exclude, "name", exclude)
    others = [t for t in tips if t.name != exclude] or tips
    return random.choice(others)


def get_tip(name):
    """The tip whose file name contains `name` (e.g. "ellipsis-picker")."""
    for t in load_tips():
        if name in t.name:
            return t
    raise KeyError(f"no tip matching {name!r}")


class TipHistory:
    """The tips shown in this session, in order, and where in that list we
    are -- shared by ``eco.tip()`` (printed or in the window) and the window's
    Back / Next buttons, like a browser history.

    `next()` first walks forward through tips already shown (after going
    back); at the end it picks a new random one, preferring tips not seen
    yet in this session. `back()` returns to the previous one."""

    def __init__(self):
        self.names = []
        self.pos = -1

    @property
    def current(self):
        return self._get(self.names[self.pos]) if self.pos >= 0 else None

    @property
    def can_back(self):
        return self.pos > 0

    def show(self, tip):
        """Record `tip` as shown now (forward history is kept, as `tip`
        goes after everything already seen)."""
        if tip is None:
            return None
        self.names.append(tip.name)
        self.pos = len(self.names) - 1
        return tip

    def next(self):
        if self.pos < len(self.names) - 1:
            self.pos += 1
            return self.current
        tips = load_tips()
        if not tips:
            return None
        seen = set(self.names)
        unseen = [t for t in tips if t.name not in seen]
        current = self.names[self.pos] if self.pos >= 0 else None
        pool = unseen or [t for t in tips if t.name != current] or tips
        return self.show(random.choice(pool))

    def back(self):
        if self.can_back:
            self.pos -= 1
        return self.current

    def position(self):
        """(1-based position, number of tips shown so far)."""
        return self.pos + 1, len(self.names)

    @staticmethod
    def _get(name):
        for t in load_tips():
            if t.name == name:
                return t
        return None


history = TipHistory()


# -- rendering -----------------------------------------------------------------


def markdown_for_links(body):
    """`body` with {doc} roles turned into plain Markdown links to the docs
    site -- what the Qt window renders."""
    return _DOC_ROLE.sub(lambda m: f"[{_doc_label(m.group(1))}]({doc_url(m.group(1))})", body)


def as_text(tip, width=None, color=None):
    """`tip` for a terminal: wrapped prose, code blocks indented as they
    are, links as URLs."""
    if width is None:
        width = min(88, shutil.get_terminal_size((80, 20)).columns) - 2
    if color is None:
        color = sys.stdout.isatty()
    bold, dim, reset = ("\033[1m", "\033[2m", "\033[0m") if color else ("", "", "")

    body = _DOC_ROLE.sub(lambda m: doc_url(m.group(1)), tip.body)
    body = re.sub(r"\*\*(.+?)\*\*", r"\1", body)  # **bold** -> bold
    out = [f"{bold}{tip.title}{reset}", ""]
    paragraph = []

    def flush():
        if paragraph:
            text = " ".join(paragraph)
            out.extend(textwrap.wrap(text, width, break_long_words=False, break_on_hyphens=False))
            out.append("")
            paragraph.clear()

    in_code = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            flush()
            in_code = not in_code
            if not in_code:
                out.append("")
            continue
        if in_code:
            out.append(f"  {dim}{line}{reset}" if line else "")
        elif not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
    flush()
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def print_tip(tip=None):
    """Print `tip` (default: the next one in this session's history, see
    TipHistory) in the terminal, framed as a "Did you know?" with a pointer
    to the window version."""
    tip = history.show(tip) if tip else history.next()
    if tip is None:
        return None
    color = sys.stdout.isatty()
    accent, reset = ("\033[36m", "\033[0m") if color else ("", "")
    print(f"{accent}Did you know?{reset}")
    print(textwrap.indent(as_text(tip, color=color), "  "))
    hint = "eco.tip() shows more tips in a window"
    print(f"{accent}-> {hint}{reset}\n")
    return tip


def tip(name=None, window=None):
    """Show a "Did you know?" tip: in a small Qt window if there is a
    display, else printed here. `name` picks one (part of its file name,
    e.g. "picker"); `window=True/False` forces the choice.

    The window opens on the tip last shown in this session (e.g. the one
    printed without a window), so its Back / Next buttons continue that history.
    Printing without a window moves on to the next tip.

    Returns the window handle (``.next()``, ``.back()``, ``.stop()``) or the
    printed Tip.
    """
    chosen = get_tip(name) if name else None
    if window is None:
        from eco.widgets.tweak_recorder import display_available

        window = display_available()
    if not window:
        return print_tip(chosen)
    from eco.widgets.tip_qt import show_tip_window

    if chosen is not None:
        history.show(chosen)
    elif history.current is None:
        history.next()
    return show_tip_window()


def docs(page=None):
    """Open the eco documentation (ECO_DOCS_URL) in the web browser, or a
    given page of it (``docs("scanning")``); without a display, print the
    URL instead. Returns the URL."""
    url = doc_url(page) if page else DOCS_URL
    from eco.widgets.tweak_recorder import display_available

    if display_available():
        import webbrowser

        if webbrowser.open(url):
            print(f"opened {url}")
            return url
    print(url)
    return url
