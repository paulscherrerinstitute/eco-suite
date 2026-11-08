"""A SciLog post's HTML made safe to show, with its images and attachments
pointed at the local server.

SciLog stores a post as CKEditor 5 HTML ("textcontent"):

* an image is ``<figure class="image image_resized"><img src="" title="<fileHash>"
  width="1483" height="556" style="aspect-ratio:1483/556;"></figure>``, the
  file named by its fileHash in the post's "files" list;
* an attachment is ``<a class="fileLink" href="file:<fileHash>">name</a>``.

clean_post() keeps that formatting (figure classes, sizes, colours,
alignment) but drops scripts, event handlers and foreign URL schemes, and
rewrites images to ``img/<fileId>`` and attachments to ``file/<fileId>``.
An HTML attachment becomes a placeholder the page turns into a sandboxed
plot on click. Uses nh3 if installed, else bleach (the console
environments have bleach only); without either, the text alone.
"""

import html
import re

TAGS = {
    "p", "br", "b", "strong", "i", "em", "u", "s", "sub", "sup", "span", "div", "hr", "mark",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
    "a", "img", "figure", "figcaption",
}
ATTRIBUTES = {
    "*": {"class", "style"},
    "a": {"href", "title"},
    "img": {"title", "alt", "width", "height"},
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
_FILE_LINK = re.compile(r'<a\b([^>]*\bhref="file:([\w-]+)"[^>]*)>(.*?)</a>', re.I | re.S)
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')


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
    and mailto links, style limited to STYLE_PROPERTIES."""
    text = _HIDDEN.sub("", text)  # what is in them is not text to show
    try:
        import nh3

        cleaned = nh3.clean(text, tags=TAGS, attributes=ATTRIBUTES, url_schemes={"http", "https", "mailto", "file"},
                            link_rel=None)
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
        cleaned = bleach.clean(text, tags=TAGS, attributes=attributes, protocols={"http", "https", "mailto", "file"},
                               strip=True, strip_comments=True, **css)
    return _STYLE_ATTR.sub(lambda m: f' style="{html.escape(_style(m.group(1)))}"' if _style(m.group(1)) else "", cleaned)


def clean_post(text, files, file_info=None):
    """`text` (a post's HTML) safe to show, images and attachments pointing
    at the local server. files: the post's "files" list. file_info:
    {fileId: content type} where known (an HTML attachment is told apart by
    it, or by its name)."""
    by_hash = {f.get("fileHash"): f for f in files or () if f.get("fileHash") and f.get("fileId")}
    body = sanitize(text or "")

    def img(m):
        attrs = dict(_ATTR.findall(m.group(1)))
        f = by_hash.get(attrs.get("title", ""))
        if not f:
            return '<span class="missing">[image not found]</span>'
        keep = "".join(f' {k}="{attrs[k]}"' for k in ("width", "height", "style") if k in attrs)
        return f'<img src="img/{f["fileId"]}"{keep} loading="lazy" alt="">'

    def link(m):
        f = by_hash.get(m.group(2))
        name = m.group(3)
        if not f:
            return f'<span class="missing">{name} (attachment not found)</span>'
        kind = (file_info or {}).get(f["fileId"]) or f.get("fileExtension") or ""
        plain = html.unescape(re.sub(r"<[^>]+>", "", name))
        if "html" in kind or plain.lower().endswith((".html", ".htm")):
            return (f'<span class="plot" data-file="{f["fileId"]}" data-name="{html.escape(plain)}">'
                    f'<a href="file/{f["fileId"]}" target="_blank" rel="noopener">{name}</a></span>')
        return f'<a class="attachment" href="file/{f["fileId"]}?download=1" target="_blank" rel="noopener">{name}</a>'

    return _FILE_LINK.sub(link, _IMG.sub(img, body))
