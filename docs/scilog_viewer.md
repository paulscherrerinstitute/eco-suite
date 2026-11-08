# SciLog in the browser: `eco.start_scilog()`

SciLog's own web page is slow to navigate and loses formatting. This is a
second way to read the same logbooks: one logbook as a single flow, with an
outline and a search that tolerates typos. SciLog stays the database; this
page only reads it, with your own access rights.

```python
eco.start_scilog()          # prints the address and opens it where a browser can start
```

or without an eco session:

```bash
python -m eco.scilog_app    # --port 8798, --no-browser
```

On a console, forward the port from your own machine first,
`ssh -L 8798:localhost:8798 saresb-cons-05`, then open the printed address
there. It contains a one-time key; after the first visit a cookie keeps it.

## What it shows

- **One flow per logbook**, oldest at the top, the newest at the bottom
  where it opens, under day headers: *Today*, *Yesterday*, *Friday*, then
  dates. New and edited posts appear by themselves.
- **An outline** on the left: months, days, each post by its first line, and
  the headings inside posts. It follows where you are, and a click jumps
  there. `o` shows or hides it.
- **Search** (`/`): finds posts containing all your words, allowing a typo
  or two per word ("hexapd" finds "hexapod"); "quotes" for exact phrases.
  Each match has a preview. On a wide screen the results stay in a column on
  the right while ↑/↓ moves the logbook beside them to each match, with the
  words marked.
- **Images at the size they were given in SciLog**: never stretched beyond
  it, only shrunk to fit. A click shows them full size.
- **Interactive plots**: an HTML attachment is shown as a bar with
  **Show the interactive plot**. It runs in a sandbox, without access to the
  page or your login.
- **Links**: 🔗 on a post copies a link to it; **SciLog ↗** opens the
  logbook in SciLog itself. The logbook list (`l`) has the most recently
  active first, and the page reopens the last logbook you looked at.

## How it works

`eco.start_scilog()` starts a server as a separate process, so it never
competes with the eco session; it ends when the session ends. The server
reads SciLog as you: with your personal token, or as the beamline account
where that applies (see {doc}`ask_eco` for both). Your SciLog token never
reaches the browser. The page gets each post's HTML cleaned of anything
executable, and the server fetches images and attachments, keeping them in
a private cache. The server listens only on the machine itself, and the page
needs the key from the printed address.

```{admonition} Did you know?
:class: tip

Arrow keys in the search results move the logbook to each match in turn, so
you can read the matches in their context without leaving the search.
```

## Coming next

Writing posts (new and edited, with a robust editor, heading levels from
the outline, fixed image sizes set with the mouse), and search across all
the logbooks you can read.
