# Installation

## From the beamline environment

At SwissFEL, eco is normally already available inside the beamline conda
environments. The `eco` command launches an interactive session for a given
instrument:

```bash
eco -s bernina
```

Run `eco --help` for the full list of options.

This opens an IPython session with the scope imported and `pylab`-style
plotting ready to go — equivalent to
`ipython --profile=eco --no-banner -i -c "run <eco>/startup_inline.py -l -s bernina"`.

Defaults — scope, IPython profile, lazy initialisation — do not need to be
typed every time. They can be set once in an `.ecorc` file, which is looked up
as `$ECORC`, then `./.ecorc`, then `~/.ecorc`; any setting it provides can
still be overridden on the command line, e.g. `eco -s alvra` or
`eco --no-lazy`. With an `.ecorc` in place, a bare `eco` just works:

```ini
[eco]
scope = bernina
profile = eco
lazy = true
```

Besides the default IPython shell, `--ui` can start a browser-based front end
instead:

```bash
eco --ui lab -s bernina     # open the eco dashboard notebook in JupyterLab
eco --ui voila -s bernina   # serve it as a live Voila widget dashboard
```

`lab`/`voila` are optional — they are not required to use eco, only installed
on demand (e.g. `conda install jupyterlab` / `voila`).

**Optional — manual import.** If you are working from an uninstalled source
checkout (see *From source* below), or want to import eco directly inside a
notebook instead of going through the launcher, the equivalent is:

```python
%matplotlib widget
import sys; sys.path.insert(0, "/sf/bernina/code/gac-bernina/eco/")
from eco import bernina
from eco.bernina import *
```

<!-- TODO(eco docs): confirm whether a beamline-specific launcher script
(e.g. one opening JupyterLab directly in the default environment) still
exists / is the recommended entry point alongside `eco --ui lab`, and
document it here if so. -->

`bernina` is the instrument object (a lazy {doc}`Namespace <concepts>` — its
components initialise only when you touch them). From there, `bernina.status()`,
`bernina.get_tree(level=-1)` and `bernina.namespace.all_names` are good first
commands.

## With conda

eco is published on [anaconda.org](https://anaconda.org/paulscherrerinstitute/eco):

```bash
conda install -c paulscherrerinstitute eco
```

:::{note}
This packaging is currently being reworked and has not yet been re-verified
end to end. Until it has, prefer *From source* below.
:::

## From source

For development, install eco in editable mode from a checkout:

```bash
git clone https://github.com/paulscherrerinstitute/eco.git
cd eco
pip install -e .
```

<!-- TODO(eco docs): PSI staff developing on-site normally check out from the
internal Gitea (git@gitea.psi.ch:Bernina/eco.git) instead of the GitHub
mirror above; confirm the mirror's sync policy and, if it can lag, add an
explicit internal-clone instruction here. -->

On a beamline machine, install into the existing conda environment without
letting pip touch its (conda-managed) dependencies:

```bash
pip install -e . --no-deps
```

eco's core dependencies (numpy, scipy, EPICS, …) install normally with
`pip install -e .`; a further stack of hardware, beam-synchronous and PSI-only
libraries (`pyepics`, `bsread`, the PSI `datahub` package, `cam_server`, …) is
grouped into optional extras (`eco[gui]`, `eco[sheets]`, `eco[hardware]`,
`eco[lab]`, `eco[voila]`) and, for the PSI-internal/git-only packages,
`eco[psi]`. Several of these are only reachable inside the PSI network or a
PSI conda channel, so a full installation is generally done on a beamline
machine rather than a laptop.

----

## Development

The sections above are for *using* eco. The following is only relevant if you
are working on eco itself.

### Building this documentation

The documentation is built with [Sphinx](https://www.sphinx-doc.org):

```bash
pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
```

Open `docs/_build/html/index.html` in a browser. The same configuration is used
by [Read the Docs](https://readthedocs.org) via the `.readthedocs.yaml` file in
the repository root.
