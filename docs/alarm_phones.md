# Getting Bernina alarms on your phone

Bernina can wake you up when something goes wrong during your shift, for
example when the laser loses synchronisation in the middle of the night. The
alarms arrive through the **Pushover** app. This page explains how to set it
up. It takes about ten minutes.

## What you get

- **Only during your shifts.** Who is on call comes from the Bernina shift
  plan. Outside your shifts you get nothing, or at most a quiet notice if you
  ask for that.
- **Critical alarms repeat** every minute until somebody on shift taps
  *Acknowledge* in the app. If nobody does within 10 minutes, the backup gets
  the alarm too.
- **Through silent mode only if you want that.** It is a setting on your phone
  (see step 4), and it applies only during your shifts, because outside them
  eco sends you no critical alarms.

## 1. Install Pushover

Install **Pushover Notifications** (by Pushover, LLC) from the App Store or
Google Play, open it and create an account. A personal email address is fine.

The app is free for 30 days. After that, Pushover asks for a one-off payment
of about $5 (per platform, inside the app). There is no subscription. You pay
it, not the beamline. If that is a problem, talk to the beamline staff.

## 2. Send us your user key

In the app (or after logging in at https://pushover.net), your **user key** is
shown at the top: 30 letters and digits, starting with `u`. Send it to the
Bernina staff together with your name as it appears in the shift plan.

If you have an eco session, you can register the key yourself:

```python
alarms.set_phone_key("Your Name", "u...")
```

The user key only lets eco send you messages. It gives nobody access to your
phone or your account.

## 3. Test it

Ask the staff to send a test message, or do it yourself in eco:

```python
alarms.send_test("Your Name")                  # a normal message
alarms.send_test("Your Name", critical=True)   # a real alarm, repeats until you acknowledge it
```

The critical test keeps repeating until you open it and tap *Acknowledge*.

## 4. Optional: let critical alarms through silent mode

Without this setting, a critical alarm is a loud, repeating notification, but
a phone on silent or Do Not Disturb stays quiet.

- **iPhone:** in the Pushover app open *Settings* and turn on *Critical
  Alerts*. Allow it when iOS asks.
- **Android:** in the Pushover app's settings, set emergency-priority messages
  to play as an alarm (alarm volume), and allow Pushover to override Do Not
  Disturb when Android asks.

Send yourself a critical test with the phone on silent to check it works.

## When an alarm comes in

- Open it and tap **Acknowledge**. That stops the repeats for everybody, and
  the others on shift see who took it.
- Then look at the alarm on a console: `alarms` in an eco session shows what is
  raised and since when.
- When the value is back in range, you get a short "cleared" message.

## Questions

Ask the Bernina beamline staff. Details for eco users are in
[Alarms and phone paging](alarms.md).
