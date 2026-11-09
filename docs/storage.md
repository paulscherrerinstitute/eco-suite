# Where is it stored? `%storage`

The beamline keeps a spreadsheet of what is stored where: the tab "Storage
location" lists the items, the tab "Storage map" the cabinets, drawers and
shelves with their rooms. `%storage` answers from it in the session:

```python
%storage hexapod          # items matching, typos forgiven
%storage BC6              # what is in cabinet BC6
%storage --check          # entries the storage map cannot place
%storage --url            # the spreadsheet, to edit it
```

```text
Accessoires PI Hexapod: BC6 (EH.002f, PSI standard cabinet, solid doors)
Symmetrie hexapod mounting accessories: BC10 (EH.055, PSI standard cabinet, solid doors)  -- can go to long term storage, hardly ever used
(storage sheet as of 2026-11-09 16:12)
```

From Python: `eco.utilities.storage_locations.where("hexapod")`,
`.contents("BC6")` and `.check()`. `%ei where are the oscilloscopes?`
answers from the same rows.

The spreadsheet stays the place to edit; this is a view of it. It is read
at most once an hour and kept in eco's index directory, so without network
the last copy is used (the output then says how old it is). `%storage
--refresh` reads it again at once. The cabinet references are matched
loosely: `BC 13`, `BC14 (FF)` and `bc13` all mean `BC13`, and `BD2 / BC5`
lists both.

`%storage --check` lists what the spreadsheet itself gets wrong: an item
whose location is not in the storage map (with the closest one that is), an
item without a location, an item listed twice.

```{admonition} Lock codes
:class: note

The storage map's column of lock codes is never read: the codes stay in the
spreadsheet, out of the cached copy, the terminal and `%ei`'s model input.
```

`ECO_STORAGE_SHEET` points it at another spreadsheet with the same two
tabs.
