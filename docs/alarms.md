# Alarms and phone paging

Overnight, nobody watches the screens. An alarm in eco is a threshold on any
detector that, once it has been out of range long enough, wakes up whoever is
on shift on their phone and keeps doing so until somebody acknowledges it.

## One alarm, no hardware

```python
import time
from eco.elements.alarm import Alarm
from eco.elements.detector import DetectorGet

a = Alarm(DetectorGet(lambda: time.time() % 60), name="seconds",
          max=30, delay=5, clear_delay=5)
a          # value, range, state, how long, how often
```

`a` reads the detector and turns the reading into a *state*:

| state | meaning |
|---|---|
| `ok` | in range |
| `pending` | out of range, but not yet for `delay` seconds |
| `ACTIVE` | raised and nobody acknowledged it, so phones are being paged |
| `acked` | raised, acknowledged (by whom is shown) |
| `clearing` | back in range, waiting `clear_delay` before it clears |
| `inhibited` | an alarm listed in `inhibit_by` is not ok (e.g. no beam) |
| `shelved` | silenced for a while (`a.shelve(60)`) |
| `disabled` | switched off |
| `unknown` | value unreadable (an alarm only with `unknown_is_alarm=True`) |

`delay` and `clear_delay` keep a noisy value from paging people for a single
bad reading, or from paging again every time it flickers across the limit.
The table also counts how often the alarm was raised and how long it spent in
alarm.

## Every parameter is settable

Each parameter is an adjustable on the alarm:

```python
a.max(25)                  # or a.max.set_target_value(25)
a.delay(30)
a.level("critical")        # display | warning | critical
a.inhibit_by(["alarms.overview.swissfel.Electron_Beam"])
a.settings()               # all of them
a.reset_settings()         # back to the defaults from the code
```

Changes are stored in the shared alarm settings, so they apply in every
session and in the monitor that pages people.

`level` decides who hears about a raised alarm:

- `display`: only shown. This is the default, so nothing pages anyone until
  somebody decides it should.
- `warning`: one normal push message to whoever is on shift.
- `critical`: an emergency message to whoever is on shift, repeated every
  minute until someone acknowledges it on the phone. If nobody does within 10
  minutes, the backup is paged too, and after 20 minutes the escalation
  contacts.

## The `alarms` object

In a Bernina session, `alarms` holds the overview panel modelled on the caQtDM
alarm overview (`alarms.overview`) and any alarms people added
(`alarms.custom`):

```python
alarms                     # armed?, monitor ok?, who is on call, all alarms
alarms.active()            # only what needs attention
alarms.show(live=True)     # tile panel, click a tile to inspect it

alarms.add(mon_kb.intensity, min=0.5, level="critical", delay=30)  # any detector
alarms.remove("mon_kb.intensity")

alarms.ack()               # acknowledge everything raised
alarms.shelve("mon_kb.intensity", 120)   # silence for two hours
alarms.events()            # what was raised, cleared, acknowledged, sent
```

## Who gets paged: the shift plan

The plan is a Google Sheet next to the run tables (`alarms.shift_sheet()`
prints its link). It has three tabs:

- **Shifts**: one row per shift (date, from, to, on call, backup). A shift
  whose end is before its start runs into the next day.
- **Deviations**: individual times that differ from the plan: `off` (not
  available, even if listed in Shifts), `on` (additionally on call), `backup`.
- **People**: what each person gets while *not* on shift (`none`, `quiet` or
  `normal`) and whether they are an escalation contact.

```python
alarms.oncall()            # now
alarms.upcoming(48)        # timeline
alarms.take_shift(hours=8)               # you, from now, on top of the sheet
alarms.leave_shift("Ben", hours=2)
```

eco reads the sheet every two minutes and keeps the last copy it read, so a
Google outage does not leave the monitor without a plan.

## Phones

Paging goes through [Pushover](https://pushover.net). Each person installs the
Pushover app (a one-off licence of about $5 per platform after a 30-day trial)
and registers their *user key* once:

```python
alarms.set_phone_key("Henrik", "u...")   # name as in the shift sheet
alarms.people()                          # who has a key, preferences
alarms.send_test("Henrik")               # a normal test message
alarms.send_test("Henrik", critical=True)  # the real thing, repeats until acknowledged
```

To have critical alarms sound even when the phone is set to silent, turn this
on in the Pushover app. It is your choice, per phone:

- iPhone: Settings, *Critical Alerts*.
- Android: the app's alarm setting ("play as alarm"), and allow Pushover to
  override Do Not Disturb.

eco sends emergency priority only to whoever is on shift (or escalated to),
so with the setting on, the phone only gets through silent mode during your
shifts.

Phone keys are kept in eco's alarm directory, not in the sheet, so editing the
sheet can never send alarms to an unknown phone.

## Arming, and the monitor

One process, the *monitor*, evaluates the alarms and sends the messages. It
runs inside the status server (`ECO_ALARM_MONITOR=1`), or on its own with
`python -m eco.alarms monitor -s bernina`. Displays show its state when it
runs; without it they evaluate locally and say that nobody is being paged.

Nothing is sent until the system is armed:

```python
alarms.arm()
alarms.disarm()
alarms.configure()                        # escalation time, repeat interval, sounds, ...
alarms.configure(heartbeat_url="https://hc-ping.com/<uuid>")
```

If the console or the monitor dies, it stops paging, and nobody would notice.
A heartbeat URL (for example healthchecks.io, which can itself alert through
Pushover) raises the alarm from outside when the pings stop.

## Files

Everything shared lives in `/sf/bernina/code/gac-bernina/eco_cnf_bernina/alarms`
(`ECO_ALARM_DIR` overrides it): `settings.json` (parameter overrides, shelving,
acknowledgements), `custom_alarms.json`, `system.json` (armed, shift sheet,
escalation), `people.json` (phone keys), `state.json` (written by the
monitor), `events.jsonl` (history).
