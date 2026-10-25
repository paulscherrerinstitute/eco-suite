# Did you know?

Short tips on eco features, newest first. Each one gives the main usage and
what you gain from it, with a link to the full documentation where there is
one. The collection so far covers features added since May 2026.

In an eco session, one tip is printed at startup (`ECO_NO_TIP=1` turns that
off), and `eco.tip()` opens a small window where **Back** / **Next** go
through the tips you have seen in this session and then on to new random
ones. Without a display, `eco.tip()` prints the next tip instead.

```{toctree}
:glob:
:reversed:
:maxdepth: 1

20*
```

## Adding a tip

One Markdown file per tip in this folder, named
`<YYYY-MM-DD>-<short-slug>.md`. The date is when the feature landed, so the
list above sorts itself. The format:

````markdown
---
added: 2026-09-12
tags: [shell, history]
details: command_history
---

# A one-line title that states the gain

One to three sentences: what it does for you and how you use it.

```python
the_main_call()
```

More: {doc}`../command_history`
````

- **Keep it short**: around 80 words of prose plus one code block of one to
  three lines. It should fit a terminal screen as a startup tip.
- **The title states the benefit**, not the implementation ("Tab never
  initializes a device behind your back", not "lazy completion gate").
- **`details`** is the Sphinx document name (relative to `docs/`, without
  `.md`) of the full page, or empty if there is none. For a feature
  that needs more than a tip, write that page and link it.
- **`tags`** are free-form lowercase words (`scans`, `ui`, `shell`, `daq`, ...)
  for filtering.
- The front matter is plain YAML, so a tool such as a startup "tip of the day"
  can read the files without Sphinx.
