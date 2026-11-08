"""eco alarm system: timed alarms on any Detector, a shift plan, and phone
paging. See docs/alarms.md.

* :mod:`eco.elements.alarm` -- `Alarm`, `AlarmGroup`, `AlarmPanel`
* :mod:`eco.alarms.system` -- `AlarmSystem`, the namespace object ``alarms``
* :mod:`eco.alarms.tracker` -- the timed state machine
* :mod:`eco.alarms.shifts` -- the shift plan (Google Sheet)
* :mod:`eco.alarms.notify` -- sending (Pushover)
* :mod:`eco.alarms.monitor` -- the process that pages people
* :mod:`eco.alarms.store` -- shared JSON files
"""
