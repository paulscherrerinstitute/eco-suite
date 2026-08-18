# eco — Experiment Control

```
                       ___ _______
                      / -_) __/ _ \
 Experiment Control   \__/\__/\___/
```

**eco** is a Python-based control environment for experiments, developed and
used at SwissFEL, PSI. It is used both as:

- a **library** of experimental devices for higher-level Python applications
  or GUIs, and
- an **interactive command-line interface**, e.g. from an IPython/Jupyter
  shell or notebook.

eco follows an object-oriented approach: every device is represented as a
Python object with a small, predictable interface, so devices can be freely
combined in generic control/acquisition routines and analysed with the
scientific Python ecosystem. For a general introduction to object-oriented
Python, see e.g. this [short introduction](https://realpython.com/python3-object-oriented-programming/).

## Documentation

The full documentation — installation, core concepts, and worked examples
(listening monitors, archiver data and strip charts, pipeline offload, motor
configuration) — lives in [docs/](docs/) and is built with
[Sphinx](https://www.sphinx-doc.org), configured to build on
[Read the Docs](https://readthedocs.org) via [.readthedocs.yaml](.readthedocs.yaml).

Build it locally:

```bash
pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
```

## Installation

```bash
conda install -c paulscherrerinstitute eco
```

or, for development, in editable mode from a checkout:

```bash
git clone https://github.com/paulscherrerinstitute/eco.git
cd eco
pip install -e .
```

See [Installation](docs/installation.md) for beamline-specific setup (the
`eco` launcher, `.ecorc` defaults) and the full dependency picture.

## Creating a new device

New devices are implemented as a subclass of `Assembly`, which provides
naming, aliasing, and shell representation:

```python
from eco.elements.assembly import Assembly

class MyDevice(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(MySubObject, name="my_sub_object", is_setting=True, is_status=True)
```

`is_setting=True` marks the child as a *setting* of the assembly (shown by
`.settings()` and captured when settings are saved); `is_status=True` marks it
as contributing to the assembly's `.status()`. See
[Representing real devices — the Assembly](docs/concepts.md) in the full docs
for the rest of the model (Adjustable, Detector, Namespace) and a
from-scratch, runnable example of each.
