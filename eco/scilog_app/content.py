"""A SciLog post's HTML: made safe to show, prepared for the editor, and
turned back into what SciLog stores.

SciLog stores a post as CKEditor 5 HTML ("textcontent") plus a "files" list:

* an image is ``<figure class="image image_resized"><img src="" title="<fileHash>"
  width="1483" height="556" style="aspect-ratio:1483/556;"></figure>``, the
  file named by its fileHash in "files". SciLog's own editor moves the
  width an image was resized to out of the HTML into that file's
  ``style.width`` (its issue #896), so a viewer that reads only the HTML
  shows every image at full size;
* an attachment is ``<a class="fileLink" href="file:<fileHash>">name</a>``.

clean_post() is what the page shows: sanitized, images at ``img/<fileId>``
with their figure at the stored width, attachments at ``file/<fileId>``,
HTML attachments as plot placeholders. editor_html() is the same for the
editor (no placeholders). to_scilog() turns the editor's HTML back: every
image and attachment must resolve to a known file (otherwise the save is
refused, as SciLog's issue #900 asks), the width stays in the HTML *and*
goes into the file's style, so SciLog shows it too.

Sanitizing uses nh3 if installed, else bleach with its CSS sanitizer (the
console environments have bleach only); without either, the text alone.
"""

import html
import re
import uuid

TAGS = {
    "p", "br", "b", "strong", "i", "em", "u", "s", "sub", "sup", "span", "div", "hr", "mark",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
    "a", "img", "figure", "figcaption",
}
ATTRIBUTES = {
    "*": {"class", "style"},
    "a": {"href", "title", "target"},
    "img": {"title", "alt", "width", "height", "src"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "col": {"span"},
    "ol": {"start"},
}
#: CSS kept in style attributes: layout and colour, nothing that loads or positions
STYLE_PROPERTIES = {
    "width", "height", "max-width", "aspect-ratio", "text-align", "float", "color",
    "background-color", "margin-left", "margin-right", "font-weight", "font-style",
    "text-decoration", "vertical-align",
}
_STYLE_VALUE = re.compile(r"^[#%(),./\w\s-]{1,80}$")
_HIDDEN = re.compile(r"<(script|style|template|iframe|object)\b.*?</\1\s*>", re.I | re.S)
_STYLE_ATTR = re.compile(r'\sstyle="([^"]*)"')
_IMG = re.compile(r"<img\b([^>]*)>", re.I)
_FIGURE = re.compile(r"<figure\b([^>]*)>(.*?)</figure>", re.I | re.S)
_LINK = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_LOCAL = re.compile(r"^(?:\./|/)?(img|file)/([0-9a-f]{24})(?:\?.*)?$")
_WIDTH = re.compile(r"(?:^|;)\s*width:\s*([\d.]+(?:px|%))")


def _style(value):
    keep = []
    for decl in html.unescape(value).split(";"):
        prop, _, val = decl.partition(":")
        prop, val = prop.strip().lower(), val.strip()
        if prop in STYLE_PROPERTIES and _STYLE_VALUE.match(val) and "url" not in val.lower():
            keep.append(f"{prop}:{val}")
    return ";".join(keep)


def sanitize(text):
    """Untrusted HTML reduced to formatting: TAGS/ATTRIBUTES only, http(s)
    and mailto links (and SciLog's file: links), style limited to
    STYLE_PROPERTIES."""
    text = _HIDDEN.sub("", text or "")  # what is in them is not text to show
    schemes = {"http", "https", "mailto", "file"}
    try:
        import nh3

        cleaned = nh3.clean(text, tags=TAGS, attributes=ATTRIBUTES, url_schemes=schemes, link_rel=None)
    except ImportError:
        try:
            import bleach
        except ImportError:
            from eco import logs_sources

            return f"<pre>{html.escape(logs_sources.html_to_text(text))}</pre>"
        attributes = {tag: sorted(attrs | ATTRIBUTES["*"]) for tag, attrs in ATTRIBUTES.items() if tag != "*"}
        attributes["*"] = sorted(ATTRIBUTES["*"])
        try:  # without a CSS sanitizer bleach drops every style, image sizes included
            from bleach.css_sanitizer import CSSSanitizer

            css = {"css_sanitizer": CSSSanitizer(allowed_css_properties=STYLE_PROPERTIES)}
        except ImportError:
            css = {}
        cleaned = bleach.clean(text, tags=TAGS, attributes=attributes, protocols=schemes, strip=True,
                               strip_comments=True, **css)
    return _STYLE_ATTR.sub(lambda m: f' style="{html.escape(_style(m.group(1)))}"' if _style(m.group(1)) else "", cleaned)


def _attrs(text):
    return {k: html.unescape(v) for k, v in _ATTR.findall(text)}


def _attr_text(attrs):
    return "".join(f' {k}="{html.escape(v)}"' for k, v in attrs.items())


def _file_width(f):
    width = str(((f or {}).get("style") or {}).get("width") or "").strip()
    return width if re.match(r"^\d+(\.\d+)?(px|%)$", width) else ""


def _by_hash(files):
    return {f.get("fileHash"): f for f in files or () if f.get("fileHash") and f.get("fileId")}


def _show_images(body, by_hash, for_editor):
    """img title=<hash> -> src img/<fileId>; a figure without a width gets
    the one SciLog keeps in the file's style."""

    def img_tag(attrs, f):
        keep = {k: attrs[k] for k in ("width", "height", "style", "alt") if k in attrs}
        lazy = {} if for_editor else {"loading": "lazy"}
        return f'<img src="img/{f["fileId"]}"{_attr_text(keep)}{_attr_text(lazy)}>'

    def figure(m):
        fattrs, inner = _attrs(m.group(1)), m.group(2)
        imgs = list(_IMG.finditer(inner))
        if len(imgs) != 1:
            return m.group(0)
        attrs = _attrs(imgs[0].group(1))
        f = by_hash.get(attrs.get("title", ""))
        if not f:
            return m.group(0)
        width = _file_width(f)
        if width and not _WIDTH.search(fattrs.get("style", "")):
            fattrs["style"] = f"width:{width}" + (f";{fattrs['style']}" if fattrs.get("style") else "")
            fattrs["class"] = " ".join(dict.fromkeys((fattrs.get("class", "image") + " image_resized").split()))
        return f"<figure{_attr_text(fattrs)}>{inner[:imgs[0].start()]}{img_tag(attrs, f)}{inner[imgs[0].end():]}</figure>"

    body = _FIGURE.sub(figure, body)

    def loose(m):  # inline images, and any a figure did not take
        attrs = _attrs(m.group(1))
        if attrs.get("src", "").startswith("img/"):
            return m.group(0)
        f = by_hash.get(attrs.get("title", ""))
        if not f:
            return m.group(0) if attrs.get("src", "").startswith("http") else '<span class="missing">[image not found]</span>'
        return img_tag(attrs, f)

    return _IMG.sub(loose, body)


def clean_post(text, files, file_info=None):
    """`text` (a post's HTML) safe to show, images and attachments pointing
    at the local server. files: the post's "files" list. file_info:
    {fileId: content type} where known (an HTML attachment is told apart by
    it, or by its name)."""
    by_hash = _by_hash(files)
    body = _show_images(sanitize(text), by_hash, for_editor=False)

    def link(m):
        attrs, name = _attrs(m.group(1)), m.group(2)
        if not attrs.get("href", "").startswith("file:"):
            return m.group(0)
        f = by_hash.get(attrs["href"][5:])
        if not f:
            return f'<span class="missing">{name} (attachment not found)</span>'
        kind = (file_info or {}).get(f["fileId"]) or f.get("fileExtension") or ""
        plain = html.unescape(re.sub(r"<[^>]+>", "", name))
        if "html" in kind or plain.lower().endswith((".html", ".htm")):
            return (f'<span class="plot" data-file="{f["fileId"]}" data-name="{html.escape(plain)}">'
                    f'<a href="file/{f["fileId"]}" target="_blank" rel="noopener">{name}</a></span>')
        return f'<a class="attachment" href="file/{f["fileId"]}?download=1" target="_blank" rel="noopener">{name}</a>'

    return _LINK.sub(link, body)


def editor_html(text, files):
    """A post's HTML for the editor: like clean_post, but attachments stay
    plain links (``file/<fileId>``), so the editor keeps them as they are."""
    by_hash = _by_hash(files)
    body = _show_images(sanitize(text), by_hash, for_editor=True)

    def link(m):
        attrs, name = _attrs(m.group(1)), m.group(2)
        f = by_hash.get(attrs.get("href", "")[5:]) if attrs.get("href", "").startswith("file:") else None
        return f'<a href="file/{f["fileId"]}">{name}</a>' if f else m.group(0)

    return _LINK.sub(link, body)


class Unresolved(ValueError):
    """The HTML refers to images or attachments that are not (yet) files."""


def to_scilog(text, known):
    """(textcontent, files) as SciLog stores them, from the editor's HTML.
    known: {fileId: file container} -- the post's files and those uploaded
    while editing. Raises Unresolved for an image still being uploaded
    (data:/blob:) or one that is no known file, and for such an attachment."""
    files, problems = [], []
    if re.search(r'<img\b[^>]*\bsrc="(?:data|blob):', text or "", re.I):
        problems.append("an image is still being uploaded")  # checked before the sanitizer drops such sources
    body = sanitize(text)

    def container(file_id):
        f = known.get(file_id)
        if f is None:
            return None
        f = dict(f)
        f.setdefault("fileHash", str(uuid.uuid4()))
        return f

    def image(attrs, width_from):
        """The <img> SciLog stores, its file appended to `files`; None if it
        is not ours (an external image)."""
        src = attrs.get("src", "")
        if src.startswith(("data:", "blob:")):
            problems.append("an image is still being uploaded")
            return None
        local = _LOCAL.match(src)
        if not local:
            return None
        f = container(local.group(2))
        if f is None:
            problems.append(f"an image ({src}) is not a file of this post")
            return None
        width = _WIDTH.search(width_from)
        f["style"] = {"width": width.group(1) if width else "", "height": ""}
        files.append(f)
        attrs.update(src="", title=f["fileHash"])
        return f"<img{_attr_text(attrs)}>"

    def figure(m):
        fattrs, inner = _attrs(m.group(1)), m.group(2)
        imgs = list(_IMG.finditer(inner))
        if len(imgs) != 1:
            return m.group(0)
        tag = image(_attrs(imgs[0].group(1)), fattrs.get("style", ""))
        if tag is None:
            return m.group(0)
        return f"<figure{_attr_text(fattrs)}>{inner[:imgs[0].start()]}{tag}{inner[imgs[0].end():]}</figure>"

    def loose(m):
        attrs = _attrs(m.group(1))
        if attrs.get("src") == "" and attrs.get("title"):
            return m.group(0)  # done by figure()
        tag = image(attrs, attrs.get("style", ""))
        return m.group(0) if tag is None else tag

    def link(m):
        attrs, name = _attrs(m.group(1)), m.group(2)
        local = _LOCAL.match(attrs.get("href", ""))
        if not local:
            return m.group(0)
        f = container(local.group(2))
        if f is None:
            problems.append(f"the attachment {re.sub('<[^>]+>', '', name)!r} is not a file of this post")
            return m.group(0)
        files.append(f)
        return f'<a class="fileLink" target="_blank" href="file:{f["fileHash"]}">{name}</a>'

    body = _LINK.sub(link, _IMG.sub(loose, _FIGURE.sub(figure, body)))
    if problems:
        raise Unresolved("; ".join(dict.fromkeys(problems)))
    return body, files


_HEADING = re.compile(r"<(h[1-6])\b([^>]*)>(.*?)</\1>", re.I | re.S)


def set_heading(text, index, level):
    """`text` with its index-th heading (h1-h6, counted in order) turned into
    `level` ("h1".."h6", or "p" for normal text)."""
    if level not in {"h1", "h2", "h3", "h4", "h5", "h6", "p"}:
        raise ValueError(f"not a heading level: {level}")
    seen = -1

    def change(m):
        nonlocal seen
        seen += 1
        if seen != index:
            return m.group(0)
        return f"<{level}{m.group(2)}>{m.group(3)}</{level}>"

    out = _HEADING.sub(change, text)
    if seen < index:
        raise ValueError(f"the post has no heading number {index + 1}")
    return out
