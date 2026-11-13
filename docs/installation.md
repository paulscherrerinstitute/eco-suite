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

Defaults — scope, IPython profile, lazy initialisation, which subcommand runs
by default — do not need to be typed every time. They can be set once in an
`.ecorc` file, which is looked up as `$ECORC`, then `./.ecorc`, then
`~/.ecorc`; any setting it provides can still be overridden on the command
line, e.g. `eco -s alvra` or `eco --no-lazy`. With an `.ecorc` in place, a bare
`eco` just works:

```ini
[eco]
command = console
scope = bernina
profile = eco
lazy = true
```

`--set-rcfile [PATH]` writes exactly what you typed on that command line into
such a file (`~/.ecorc` by default) and exits instead of launching, e.g.
`eco -s alvra --set-rcfile`.

Besides the default `console` subcommand (the IPython shell), three others
start a different front end:

```bash
eco desktop -s bernina      # Qt workbench: embedded console + a device browser panel
eco jupyterlab -s bernina   # JupyterLab, plus a real jupyter console with the scope preloaded
eco webapp -s bernina       # serve the eco dashboard notebook as a read-only Voila page
```

`desktop`/`jupyterlab`/`webapp` are optional — they are not required to use
eco, only installed on demand (e.g. `conda install jupyterlab` / `voila`, or
`pip install eco[gui]` for the desktop UI's Qt console).

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
exists / is the recommended entry point alongside `eco jupyterlab`, and
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

### On a laptop, without the beamline network

The core install is enough to work with the simulated devices (`sim`, see
{doc}`simulated_targets`) and run the tests, with no connection at all once
installed:

```bash
uv venv --python 3.12 ~/eco-venv          # or python -m venv
uv pip install --python ~/eco-venv/bin/python -e ".[dev]"
```

Without the beamline file system the `bernina` scope cannot start, so make a
bare `eco` start without an instrument, with an `~/.ecorc` of

```ini
[eco]
scope =
```

Such a session has `sim` and a plain `scans` ready. In Python of your own:

```python
from eco.devices_general.simulated_signals import SimulatedSignals
from eco.acquisition.scan import Scans

sim = SimulatedSignals()
scans = Scans()
scans.ascan(sim.peak.x, -1, 2, 20, 0.05, counters=[sim.peak.signal])
scans.adaptive_scan([sim.peak2d.x, -2, 2, 0.1], [sim.peak2d.y, -2, 2, 0.1],
                    exposure_def=0.05, feedback=sim.peak2d.signal, max_points=60)
```

Works offline: scans and adaptive scans on `sim`, peak analysis, escape files
and `arr.mesh`, the text charts (`plot="text"`), `sim.oscillation_tt.shots()`.
Needs the beamline: real devices (EPICS), the DAQ, the archiver and anything
beam-synchronous (`bsread`, PSI's `datahub`). Qt windows need the `gui` extra.

Run the tests one file at a time (see the developer notes): tests that need a
package the install doesn't have (Qt, flask, dash, `bsread`, `datahub`, ...)
are skipped.

```bash
for f in tests/test_*.py; do ~/eco-venv/bin/python -m pytest -q "$f"; done
```

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
