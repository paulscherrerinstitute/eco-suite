# Tab completion on lazy devices

Most components in the Bernina namespace are **lazy**: they are only built
(with real EPICS connections) the first time you use them. Tab completion has
to look inside a device to list its children, so without care, pressing Tab
would quietly build devices you never meant to touch.

## Two Tabs to initialize

If the device you are completing into has not been initialized yet, the first
Tab does nothing except tell you so:

```text
In [1]: prepump.<TAB>
eco: 'prepump' is not initialized yet -- press Tab again to initialize it and complete.
In [1]: prepump.<TAB>
        check  gp  isolate_all  line1_usd  line2_lic  pump_down  roots_pump  show  vent  ...
```

The second Tab initializes it and completes as usual. This works at any depth
(`bernina.something.<TAB>`) and still counts if you kept typing between the
two Tabs.

Only the one component named in the expression is ever initialized, never
its siblings that happen to share a prefix. Completing into a device that is
already initialized works on the first Tab, as before.

## What makes completion work on eco objects

You normally don't need to know this, but if completion breaks after an
IPython upgrade, these are the three settings involved. eco sets them in every
session it starts (terminal, desktop console, `start_console()`):

1. `Completer.use_jedi = False`. jedi resolves every candidate to inspect it,
   which on a lazy namespace means initializing everything with a matching
   prefix. It also returns no matches at all on eco's dynamic objects.
2. eco's lazy-proxy class is allow-listed in IPython's `guarded_eval` policy.
   Without it, completing two levels deep (`prepump.line1_usd.<TAB>`) silently
   returns nothing.
3. The two-Tab gate described above.
