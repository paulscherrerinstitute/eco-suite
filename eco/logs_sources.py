"""Turn the *other* records of what happened at the beamline into dated text
for eco.logs_ask: scilog posts, personal logs as text/Markdown/PDF files,
and scanned or photographed (also handwritten) notes via a pluggable OCR.

Everything here is a pure function of its inputs (plus optional imports
done at call time), so it can be tested without scilog, without PDFs on
disk and without an OCR engine:

  find_dates(text)            -> [(char_offset, epoch, matched_text)]
  dated_chunks(text, ...)     -> [(epoch | None, chunk_text)], each chunk
                                 dated by the last date written above it
  command_lines(text)         -> lines that look like eco commands
  html_to_text(html)          -> plain text of a scilog paragraph
  document_text(path, ocr=..) -> text of a .txt/.md/.log/.pdf/image file
  scilog_snippets(client, ..) -> scilog paragraph posts newer than a time

OCR is never bundled: pass `ocr=callable(PIL.Image) -> str` (printed text:
e.g. tesseract or rapidocr; handwriting needs a vision-language model --
VisionTranscriber asks any OpenAI-compatible one that accepts images).

PDFs: pypdf / pypdfium2 if installed, otherwise poppler's pdftotext /
pdftoppm command line tools (present on the beamline consoles).
"""
import base64
import datetime
import html.parser
import io
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".log", ".rst", ".text", ".csv"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

_MONTHS = {
    # English and German, full and abbreviated (beamline notebooks are both)
    "jan": 1, "january": 1, "januar": 1, "feb": 2, "february": 2, "februar": 2,
    "mar": 3, "march": 3, "mär": 3, "märz": 3, "maerz": 3, "apr": 4, "april": 4,
    "may": 5, "mai": 5, "jun": 6, "june": 6, "juni": 6, "jul": 7, "july": 7, "juli": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "okt": 10, "oktober": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    "dez": 12, "dezember": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))

_DATE_PATTERNS = [
    # 2026-10-29, 2026/10/29, 2026.10.29 (optionally with a time 14:05)
    (re.compile(r"\b(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d\d))?\b"), "ymd"),
    # 29.10.2026, 29.10.26, 29/10/2026 -- day first, the European convention
    (re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](20\d\d|\d\d)\b(?:,?\s+(\d{1,2}):(\d\d))?"), "dmy"),
    # 29 Oct 2026, 29. Oktober 2026
    (re.compile(rf"\b(\d{{1,2}})\.?\s+({_MONTH_RE})\.?\s+(20\d\d)\b", re.I), "d_mon_y"),
    # Oct 29, 2026 / October 29 2026
    (re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}}),?\s+(20\d\d)\b", re.I), "mon_d_y"),
]


def _epoch(y, m, d, hh=0, mm=0):
    try:
        return datetime.datetime(int(y), int(m), int(d), int(hh or 0), int(mm or 0)).timestamp()
    except (ValueError, OverflowError):
        return None


def find_dates(text):
    """Every date written in `text`, as (offset, epoch seconds, matched
    text), in order of appearance. Local time; dates that don't exist
    (31.02.) are ignored. Day-first for numeric dates (29/10/2026, 01.02.26
    is 1 February), which is what is written at PSI."""
    found = {}
    for rx, form in _DATE_PATTERNS:
        for m in rx.finditer(text):
            if any(start <= m.start() < end for start, (end, _t, _s) in found.items()):
                continue  # already covered by an earlier, more specific pattern
            g = m.groups()
            if form == "ymd":
                t = _epoch(g[0], g[1], g[2], g[3], g[4])
            elif form == "dmy":
                y = g[2] if len(g[2]) == 4 else "20" + g[2]
                t = _epoch(y, g[1], g[0], g[3], g[4])
            elif form == "d_mon_y":
                t = _epoch(g[2], _MONTHS[g[1].lower()], g[0])
            else:
                t = _epoch(g[2], _MONTHS[g[0].lower()], g[1])
            if t is not None:
                found[m.start()] = (m.end(), t, m.group(0))
    return [(start, t, s) for start, (_end, t, s) in sorted(found.items())]


def dated_chunks(text, default_t=None, max_chars=1200):
    """Split `text` into paragraphs and give each the last date written at
    or above it (a notebook's "29.10.2026" heading dates everything below
    it until the next date). Consecutive paragraphs with the same date are
    merged up to `max_chars`. Returns [(epoch or default_t, chunk)]."""
    chunks, cur_t, buf = [], default_t, []

    def flush():
        if buf:
            chunks.append((cur_t, "\n\n".join(buf)))
            buf.clear()

    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        dates = find_dates(para)
        if dates:
            flush()
            cur_t = dates[-1][1]
        if buf and sum(len(b) for b in buf) + len(para) > max_chars:
            flush()
        buf.append(para)
    flush()
    return chunks


_COMMAND_RE = re.compile(
    r"^\s*(?:>>>\s*|In \[\d+\]:\s*)?"  # optional prompt as copied from a console
    r"([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*"  # dotted name: mono, att.set_transmission
    r"(?:\(.*\)|\s*=\s*\S.*))\s*$"  # a call, or an assignment
)


def command_lines(text):
    """Lines of free text that look like eco commands (`mono.mv(8000)`,
    `>>> s.ascan(...)`, `In [3]: att.set_transmission(.1)`) -- what a
    personal log or a scilog post usually contains alongside prose."""
    out = []
    for line in text.splitlines():
        m = _COMMAND_RE.match(line)
        if not m:
            continue
        cmd = m.group(1).strip()
        head = cmd.split("(", 1)[0]
        if "=" in head and "." not in head.split("=", 1)[0]:
            continue  # "Energy = 8 keV" is prose; "mono.energy = 8000" is not
        out.append(cmd)
    return out


class _TextOnly(html.parser.HTMLParser):
    # paragraph-level tags become a blank line (= a paragraph for
    # dated_chunks), line-level ones a newline
    _PARAGRAPH = {"p", "div", "h1", "h2", "h3", "h4", "pre", "table"}
    _LINE = {"br", "li", "tr"}

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self._PARAGRAPH:
            self.parts.append("\n\n")
        elif tag in self._LINE:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html_text):
    p = _TextOnly()
    p.feed(html_text or "")
    text = "".join(p.parts)
    return re.sub(r"\n[ \t]*\n\s*", "\n\n", text).strip()


def document_text(path, ocr=None):
    """Plain text of one personal-log file. .txt/.md/.log/...: read as is.
    .pdf: its text layer (needs `pip install pypdf`); a scanned PDF without
    one is rendered page by page and OCRed if `ocr` is given (needs
    pypdfium2). Images: OCRed if `ocr` is given. Returns (text, note), note
    saying why the text is empty or partial."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return path.read_text(errors="replace"), ""
    if suffix == ".pdf":
        text = _pdf_text(path)
        if text is None:
            return "", "neither pypdf nor pdftotext available"
        if text.strip():
            return text, ""
        if ocr is None:
            return "", "PDF has no text layer and no OCR configured"
        pages = _pdf_page_images(path)
        if pages is None:
            return "", "scanned PDF needs pypdfium2 or pdftoppm for OCR"
        return "\n\n".join(ocr(page) for page in pages), "OCR"
    if suffix in IMAGE_SUFFIXES:
        if ocr is None:
            return "", "image and no OCR configured"
        from PIL import Image

        with Image.open(path) as img:
            return ocr(img), "OCR"
    return "", f"unsupported file type {suffix}"


def _pdf_text(path):
    """Text layer of a PDF (pages separated by blank lines), or None if
    there is no way to read it."""
    try:
        from pypdf import PdfReader
    except ImportError:
        if not shutil.which("pdftotext"):
            return None
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=120)
        return out.stdout.decode(errors="replace").replace("\f", "\n\n")
    return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)


def _pdf_page_images(path, dpi=150):
    """PIL images of a PDF's pages, or None if it can't be rendered."""
    try:
        import pypdfium2
    except ImportError:
        if not shutil.which("pdftoppm"):
            return None
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(path), f"{tmp}/page"], check=True, timeout=600)
            pages = []
            for png in sorted(Path(tmp).glob("page*.png"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0)):
                with Image.open(png) as img:
                    pages.append(img.copy())
            return pages
    return [page.render(scale=dpi / 72).to_pil() for page in pypdfium2.PdfDocument(str(path))]


TRANSCRIBE_PROMPT = (
    "Transcribe all handwritten and printed text in this image verbatim, line by line. "
    "Keep dates, numbers, units and anything that looks like code or a command exactly as written; "
    "write [illegible] where you cannot read something instead of guessing. "
    "Then, for every sketch, diagram or hand-drawn plot, add a line starting with 'FIGURE:' "
    "describing what it shows (axes and their labels, units, curve shapes, marked values). "
    "Do not add anything that is not in the image. If there is no text and no figure, answer "
    "'[no text]'."
)


class VisionTranscriber:
    """An `ocr=` callable, PIL.Image -> text, asking an OpenAI-compatible
    chat endpoint that accepts images (e.g. a Qwen-VL class model).
    Standard library plus Pillow. Phone and webcam pictures are scaled
    down to `max_side` pixels first; `options` go into the request body as
    they are (e.g. {"chat_template_kwargs": {"enable_thinking": False}})."""

    def __init__(
        self, base_url, model, api_key=None, prompt=TRANSCRIBE_PROMPT, max_side=1600,
        timeout=300, max_tokens=2000, options=None,
    ):
        self.base_url, self.model, self.api_key, self.prompt = base_url.rstrip("/"), model, api_key, prompt
        self.max_side, self.timeout, self.max_tokens = max_side, timeout, max_tokens
        self.options = dict(options or {})

    def data_url(self, image):
        img = image.convert("RGB")
        img.thumbnail((self.max_side, self.max_side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    def __call__(self, image):
        import urllib.request

        body = {
            "model": self.model, "temperature": 0, "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": self.prompt},
                {"type": "image_url", "image_url": {"url": self.data_url(image)}},
            ]}],
        }
        body.update(self.options)
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())["choices"][0]["message"].get("content") or ""


def to_pil(image):
    """PIL image from a PIL image, a numpy array (a camera frame: 2-D
    grey or HxWx3/4, any dtype -- scaled to 0..255) or image file bytes."""
    from PIL import Image

    if isinstance(image, Image.Image):
        return image
    if isinstance(image, (bytes, bytearray)):
        with Image.open(io.BytesIO(image)) as img:
            return img.copy()
    import numpy as np

    arr = np.asarray(image)
    if arr.dtype != np.uint8:
        arr = arr.astype(float)
        finite = arr[np.isfinite(arr)]
        lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
        arr = np.nan_to_num((arr - lo) / ((hi - lo) or 1.0) * 255).clip(0, 255).astype(np.uint8)
    if arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]
    return Image.fromarray(arr)


def scilog_snippets(client, after_iso=None, limit=500):
    """scilog paragraph posts created after `after_iso` (oldest first), as
    dicts {id, t, created_at, author, tags, text}. `client` is what
    eco.logs._scilog_client() returns (the `scilog` package's client);
    anything with the same get_snippets(where=, order=, limit=) works,
    which is how the tests fake it. Only "paragraph" snippets: the scilog
    client can't deserialize "task" snippets (see eco.logs.scilog)."""
    where = {"snippetType": "paragraph"}
    if after_iso:
        where["createdAt"] = {"gt": after_iso}
    out = []
    for s in client.get_snippets(where=where, order=["createdAt ASC"], limit=limit):
        created = getattr(s, "createdAt", None)
        if not created:
            continue
        try:
            t = datetime.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        out.append({
            "id": getattr(s, "id", None),
            "t": t,
            "created_at": created,
            "author": getattr(s, "createdBy", "") or "",
            "tags": list(getattr(s, "tags", None) or ()),
            "text": html_to_text(getattr(s, "textcontent", "") or ""),
        })
    return out

