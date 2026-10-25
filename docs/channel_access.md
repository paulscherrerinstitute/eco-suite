# Channel access: monitored reads and `None` diagnostics

pyepics never raises when a read fails. It returns `None`, and that `None`
then crashes something further along, typically a scan several steps in
(`int(None)`, `100 / None`). This happened repeatedly, for a different PV each
time. eco now prevents it in general rather than one PV at a time.

## Reads come from monitors

PVs are now created with `auto_monitor=True` by default. A monitored,
connected PV's value is pushed into a local cache, so a read is a dictionary
lookup: about 30× faster (0.017 ms vs 0.54 ms), and with no network round trip
that could time out and return `None`.

Monitoring everything is cheap because almost nothing updates fast. Of ~7 700
Bernina status channels, 48 (0.6 %) produce 87 % of all updates. So eco
**learns and demotes** the fast ones:

- A background sweeper measures per-channel update rates.
- A channel above 10 Hz for several consecutive sweeps gets its monitor
  dropped. It is not demoted if someone holds a callback on it (a recording, a
  `Monitor`).
- The learned list is saved to `~/.eco/ca_fast_channels.json`, so the next
  session never subscribes to those channels at all.

On a full Bernina namespace this costs about 8 % of one core.

Reads of channels that have worked before are also **retried** at one central
place, so a transient drop is absorbed for every caller. During the
acquisition window of a scan step the sweeper leaves subscriptions alone and
reads get extra retries.

## Looking at it

```python
from eco.epics_utils import ca_tuning

ca_tuning.monitor_report()
# {'auto_monitor_default': True, 'max_rate_hz': 10.0, 'monitored': 7074,
#  'known_fast': [...], 'n_known_fast': 68, 'state_file': '...', 'in_sensitive_period': False}

ca_tuning.forget_channel("SOME:PV")    # a demoted channel became slow again
ca_tuning.clear_fast_channels()        # forget the whole learned list
```

## When a `None` still happens

Every read that returns `None` is logged as a warning, at most once per PV per
minute. The warning names the PV, says whether it ever returned a value in this
session and how long ago, how many cached channels are currently disconnected,
and how many failed reads were swallowed since the last message. If you see a
`None`-related crash, look for that warning first: it usually identifies the
cause.

## For device developers

- Create PVs with `ca_tuning.make_pv(pvname, ...)` instead of
  `PV(..., auto_monitor=False)`, so the policy applies.
- Wrap timing-critical sections in
  `with ca_tuning.sensitive_period("what this is"):`.
- To revert the whole policy, set `ca_tuning.AUTO_MONITOR_DEFAULT = False`.
