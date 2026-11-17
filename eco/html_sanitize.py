"""Untrusted HTML (a SciLog post, a model's answer) reduced to plain
formatting: no scripts, event handlers or foreign URL schemes. Uses nh3 if
installed, else bleach with its CSS sanitizer (the console environments have
bleach only); without either, the text alone.

The same rules as the SciLog viewer's (scilog_viewer.content.sanitize), kept
here so that %ei's page does not need that package.
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


