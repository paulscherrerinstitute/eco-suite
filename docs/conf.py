"""Sphinx configuration for the eco documentation.

Loosely modelled on the escape-fel docs so the two read consistently.
The docs mix reStructuredText (index/api) and Markdown (concepts, examples,
installation) via the MyST parser.
"""

import os
import sys

# Make the eco package importable for autodoc even when the docs are built
# without a full `pip install .` (e.g. locally with `make html`).
sys.path.insert(0, os.path.abspath(".."))

# -- Project information -----------------------------------------------------

project = "eco"
author = "Paul Scherrer Institute"
copyright = "2026, Paul Scherrer Institute"

# Kept intentionally light; bump alongside eco/setup.py when it matters.
release = "0.0.2"

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",       # NumPy / Google style docstrings
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_parser",               # Markdown support
    "sphinx_copybutton",         # copy button on code blocks
    "sphinx_design",             # grids / cards / tabs used on the landing page
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Accept both .rst and .md sources.
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# -- Autodoc -----------------------------------------------------------------

autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    "special-members": "__init__",
}

# eco pulls in a large stack of hardware / beamline / streaming libraries that
# are not (and need not be) installed on the docs builder. Mock them so that
# importing eco for autodoc does not fail or, worse, try to open live EPICS /
# beam-synchronous connections. Add to this list whenever autodoc trips over a
# new third-party import.
autodoc_mock_imports = [
    "epics",
    "pyepics",
    "caproto",
    "serial",
    "bsread",
    "datahub",
    "cbor2",
    "cam_server",
    "cam_server_client",
    "cachebox",
    "pcaspy",
    "slic",
    "scilog",
    "elog",
    "PyQt5",
    "pyqtgraph",
    "ipywidgets",
    "textual",
    "rich",
    "colorama",
    "tqdm",
    "requests",
    "zmq",
    "h5py",
    "pandas",
    "scipy",
    "matplotlib",
    # escape (SwissFEL's data-analysis package) transitively pulls in
    # dask -> xarray, whose xarray/core/types.py defines a module-level
    # `pd.Timestamp | datetime.datetime | ...` type alias evaluated eagerly
    # at import time -- with `pandas` mocked above, that `|` on a mock
    # object raises TypeError and takes down autodoc for every module in
    # eco that imports escape transitively (eco.elements.assembly among
    # them, via eco.acquisition.scan_data). Mocking escape itself avoids
    # ever reaching xarray's import at all.
    "escape",
]

# -- MyST (Markdown) ---------------------------------------------------------

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
]
# "linkify" (auto-linking bare URLs) needs the optional linkify-it-py package.
# Enable it only when available so a local build without it still succeeds;
# Read the Docs installs it via docs/requirements.txt (myst-parser[linkify]).
try:
    import linkify_it  # noqa: F401

    myst_enable_extensions.append("linkify")
except ImportError:
    pass
myst_heading_anchors = 3

# -- Intersphinx -------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "pandas": ("https://pandas.pydata.org/docs/", None),
}

# -- HTML output -------------------------------------------------------------

html_theme = "furo"
html_title = "eco"
html_static_path = ["_static"]

# SwissFEL/escape blue, so eco and escape docs feel related.
html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#0071bc",
        "color-brand-content": "#0071bc",
    },
    "dark_css_variables": {
        "color-brand-primary": "#4aa8e0",
        "color-brand-content": "#4aa8e0",
    },
}


# -- "Did you know?" random-tip page -----------------------------------------

def _write_random_tip_page(app, exception):
    """Write did_you_know/random.html: one random tip with no sidebar, for a
    small browser window. The page itself is _templates/random_tip.html; the
    list of tips is filled in here because it changes with every new file."""
    if exception is not None or app.builder.format != "html":
        return
    import json

    tips = [
        {
            "page": docname.split("/", 1)[1] + app.builder.out_suffix,
            "title": app.env.titles[docname].astext(),
        }
        for docname in sorted(app.env.found_docs)
        if docname.startswith("did_you_know/20")
    ]
    with open(os.path.join(app.confdir, "_templates", "random_tip.html")) as f:
        page = f.read().replace("__TIPS__", json.dumps(tips, indent=1))
    out_dir = os.path.join(app.outdir, "did_you_know")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "random.html"), "w") as f:
        f.write(page)


def setup(app):
    app.connect("build-finished", _write_random_tip_page)
