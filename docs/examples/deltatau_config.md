# Delta Tau / PowerBrick motor configs

Every PowerBrick (Delta Tau PPMAC) motor controller boots from a **configuration**
— a small bundle of files that defines each axis' encoder, current limits, jog
speed, limit switches and so on. Getting that configuration wrong can *damage a
motor*, and until now it was applied by a single command-line script
(`ConfigMotorIOC.py`) whose host/config choices lived in hard-coded lookup tables.

The {py:mod}`eco.motion.deltatau_config` module turns that workflow into objects
you can script:

- **read** a config file into a plain dict, **edit** it, and **write it back**
  losslessly;
- **apply** a known config to a host with the bernina shortnames as keyword
  arguments;
- **query the live controller** and check it still matches the config you expect.

:::{warning}
Applying a wrong PowerBrick configuration can **destroy motors**.
{py:func}`~eco.motion.deltatau_config.apply` asks for the `deltatau` safety
password unless you pass `force=True`, and every example below that would touch
hardware uses `dry_run=True` so you can see the exact commands first.
:::

## A config file as a dict

The interesting per-motor settings live in a `.cfg` file as *template calls* like
`!motor(mot=1,dirCur=1000,JogSpeed=1.,servoSf=1024./5)`. Parse one and look at a
single axis:

```python
from eco.motion.deltatau_config import parse_cfg_file, CONFIGS

cfg = parse_cfg_file(CONFIGS["stageXYZ"].cfg_files()[0])
d = cfg.to_dict()

d["axes"][1]["motor"]
# {'mot': 1, 'dirCur': 1000, 'invDir': False, 'JogSpeed': 1.0, 'servoSf': '1024./5'}
d["axes"][1]["encoder"]
# {'enc': 1, 'posSf': '1./10'}
```

Note that `servoSf` came back as the *string* `'1024./5'`, not `204.8`. Delta Tau
config values are often arithmetic expressions, so they are preserved verbatim —
that is what makes the round-trip below exact.

## Round-trip: read → modify → write

`to_text()` reproduces the file **byte-for-byte**, so you can edit one field and
write the result back without disturbing anything else (comments, blank lines,
raw gpascii statements are all kept):

```python
cfg = parse_cfg_file("/sf/bernina/config/src/python/ConfigMotorIOC/stageXYZ/stageXYZ.cfg")

# bump the jog current of axis 1
cfg.set_axis(1, "motor", dirCur=1200)

open("/tmp/stageXYZ_edited.cfg", "w").write(cfg.to_text())
```

Only the line you touched is regenerated (`...,dirCur=1200,...`); everything else
is identical to the original.

## The config bundle and the bernina registries

A *bundle* is the set of files that make up one configuration:
`(base directory, startup add_device.cmd, other files)`. The
{py:class}`~eco.motion.deltatau_config.ConfigBundle` wraps that and round-trips to
a dict, so a config can be stored in JSON or edited programmatically:

```python
from eco.motion.deltatau_config import ConfigBundle

b = ConfigBundle(base="/my/configs/mysample", add_device="add_device.cmd", files=["*"])
b.to_dict()
# {'base': '/my/configs/mysample', 'add_device': 'add_device.cmd', 'files': ['*']}
ConfigBundle.from_dict(b.to_dict()) == b        # True
b.cfg_files()                                    # the .cfg file(s) in the bundle
```

The bernina hosts and named configs from the original CLI are available as plain
lookups:

```python
from eco.motion.deltatau_config import HOSTS, CONFIGS, resolve_host

sorted(CONFIGS)          # ['MForceExample', 'PowerBrickExample', 'europium',
                         #  'kappa', 'stage', 'stageXYZ']
resolve_host("GPS")      # ('SARES22-CPPM-GPS1', ('stage', 'kappa'))
CONFIGS["kappa"].add_device   # 'add_deviceKappa.cmd'
```

## Applying a config to a host

{py:func}`~eco.motion.deltatau_config.apply` copies the bundle to the host and
restarts the IOC — the same mechanism as the original script. The bernina
shortnames are just keyword arguments. **Always dry-run first** to see what will
happen:

```python
from eco.motion.deltatau_config import apply

apply(host="EXP1", config="stageXYZ", dry_run=True)
# host: SARES20-CPPM-EXP1, config: ('.../stageXYZ', 'add_deviceStageXYZ.cmd', '*')
# DRY-RUN scp -r ... stageXYZ/add_deviceStageXYZ.cmd root@SARES20-CPPM-EXP1:/tmp/add_device.cmd
# DRY-RUN scp -r ... stageXYZ/* root@SARES20-CPPM-EXP1:/tmp/
# DRY-RUN restart IOC on SARES20-CPPM-EXP1:50001 and wait for prompt
```

When it looks right, drop `dry_run` (you will be prompted for the `deltatau`
password), or pass `force=True` to skip the prompt in a script:

```python
apply(host="EXP1", config="stageXYZ")               # interactive password gate
apply(host="XRD", config="kappa", force=True)       # unattended
```

You can also hand `apply` a `ConfigBundle`, its dict, or the legacy
`(base, add_device, *files)` tuple instead of a shortname; and pass `ioc=...` to
reconfigure `shellbox` before the restart. Configs that a host does not allow are
rejected up front:

```python
apply(host="GPS", config="stageXYZ", dry_run=True)
# ValueError: config 'stageXYZ' not allowed on host 'GPS'; allowed: ('stage', 'kappa')
```

## Verifying the live controller

To confirm a running controller still matches the config you expect, read its
live parameters and diff them against a bundle:

```python
from eco.motion.deltatau_config import read_live, check

read_live("EXP1", axes=range(1, 4))
# {1: {'Motor[1].JogSpeed': 1.0, 'Motor[1].JogTa': ..., ...}, 2: {...}, 3: {...}}

ok, report = check("EXP1", "stageXYZ")
# config check: host=EXP1 config=stageXYZ -> OK
```

`check` returns `(ok, report)`; `ok` is `True` when no checkable parameter
mismatches. On a mismatch you get the offending axis/parameter and both values:

```
config check: host=EXP1 config=stageXYZ -> MISMATCH
  axis 1: JogSpeed: expected 1.0, live 2.0
```

:::{note}
The templates that expand `!motor(...)` into individual controller variables are
no longer shipped on the beamline, so only parameters with a *direct* live
variable can be checked automatically (`JogSpeed`, `JogTa`, `AbortTa`,
`InPosBand`, `HomeOffset` — see
{py:data}`~eco.motion.deltatau_config.verify.LIVE_MAP`). Everything else is
listed as *informational* rather than silently ignored, so the check is honest
about what it can and cannot confirm.
:::

## From the command line

The same operations are available as a module entry point, mirroring the original
script's examples:

```bash
# inspect what an apply would do
python -m eco.motion.deltatau_config --host EXP1 --cfg stageXYZ --dry-run

# apply for real (prompts for the deltatau password; -f to skip)
python -m eco.motion.deltatau_config --host XRD --cfg kappa -f

# set up shellbox + restart a specific IOC
python -m eco.motion.deltatau_config --host GPS --cfg stage --ioc /ioc/SARES22-CPPM-GPS1

# only verify the live controller against a config, apply nothing
python -m eco.motion.deltatau_config --host EXP1 --cfg stageXYZ --check
```

## Where this is heading

The diffractometer assemblies in `eco/endstations/bernina_diffractometers.py`
already know which PowerBrick host each stage lives on. The intended next step is
for them to call {py:func}`~eco.motion.deltatau_config.check` at startup to
confirm the controller is configured as expected — and offer to
{py:func}`~eco.motion.deltatau_config.apply` a known-good bundle if it is not.
That wiring is deliberately left as a reviewed follow-up given the motor-damage
risk.

## Related

- {doc}`../deltatau_servo` — what the servo parameters mean and how to tune them.
- {doc}`pipeline_offload` — upload your own code to the pipeline server.
- {doc}`listening_monitor` — collect a channel's updates in the client.
```
