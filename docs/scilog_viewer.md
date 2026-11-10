# SciLog in the browser: `scilog.viewer()`

SciLog's own web page is slow to navigate and loses formatting. This is a
second way to read and write the same logbooks: one logbook as a single
flow, with an outline, a search that tolerates typos, and an editor that
keeps image sizes and your drafts. SciLog stays the database, with your own
access rights.

```python
scilog.viewer()             # the pgroup's logbook; prints the address and opens it where a browser can start
scilog.widget()             # the same
scilog.viewer.stop()        # end the viewer's server; scilog.viewer.log(): its last output
```

outside the Bernina namespace (any session, a script), the package is only
loaded when imported:

```python
from eco.scilog_app import start
start()                     # or start(logbook="<id>")
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

`scilog.viewer()` (or `start()`) starts a server as a separate process, so it never
competes with the eco session; it ends when the session ends. Your SciLog
token never reaches the browser.

### Whose SciLog access

The page's **Read SciLog as …** window (the badge in the bar opens it again)
offers what is possible where the viewer runs:

- **the beamline account** (swissfelaramis-bernina, where its password is
  readable): reads this beamline's logbooks; what you write is saved as the
  beamline account, and the editor says so;
- **your kept token** (~/.eco/scilog_token, readable only by you): never on a
  shared account;
- **your own token, lent**: pasted in (or, in the Android app, taken over from
  SciLog's own login). On a personal account you can keep it; on a shared
  account (gac-bernina) it is never written to disk: the viewer's server holds
  it in memory for at most 8 hours and forgets it when the eco session ends.

On gac-bernina the window comes first, before anything is read. A personal
PSI account cannot log in to SciLog with a username and password from a
program (PSI's single sign-on only works in a browser), hence the token.

### Getting your SciLog token

SciLog's own page keeps the token in the browser after you log in (local
storage, `id_token`). The viewer's token window shows these steps for the
device you are on.

**On a computer** (Chrome, Edge, Firefox, Safari):

1. Open <https://scilog.psi.ch> in a new tab and log in with your PSI
   account, until you see your logbooks.
2. In *that SciLog tab*, open the developer tools: `F12` (Mac `⌥⌘I`; Safari
   first needs Settings → Advanced → "Show features for web developers").
3. Open the **Console** tab.
4. Type `copy(localStorage.id_token)` and press Enter. Nothing seems to happen:
   the token is now on the clipboard. Type it rather than paste it; browsers
   refuse pasting into the console until you type `allow pasting`.
5. Paste it into the viewer's token window.

Without the console: developer tools → **Application** (Firefox: **Storage**)
→ Local storage → `https://scilog.psi.ch` → `id_token`, copy its value.

**On an Android phone:** in the Android app, tap **Log in to SciLog here**:
the token is taken over by itself. In Chrome, a bookmark reads it (phones
have no developer tools):

1. Make a bookmark (⋮ → ☆), edit it (⋮ → Bookmarks → ⋮ → Edit): name
   `SciLog token`, address
   `javascript:void(prompt('SciLog token (select all, copy):',localStorage.id_token))`.
2. Open <https://scilog.psi.ch> and log in.
3. In the SciLog tab, tap the address bar, type `SciLog token` and tap the
   bookmark among the suggestions (from the bookmark list it does not run).
4. Copy the token from the box that appears.

Firefox for Android does not run such bookmarks.

**On an iPhone or iPad** (Safari): the same bookmark (Share → Add Bookmark,
then edit its address), opened from the bookmarks while SciLog is shown.

**If it does not work:**

- *"null" or "undefined"*: it ran on the wrong page (the viewer, or SciLog
  before the login). Run it in the SciLog tab once your logbooks are shown.
- *"not accepted" or "expired"*: a token lasts 14 days from the login and
  cannot be renewed. Log out of SciLog, log in again, copy the new one.
- *The bookmark starts a web search*: its address must begin with
  `javascript:`; some phones drop that when pasting.
- Keep the token to yourself: for up to 14 days it lets anyone read and
  write SciLog as you. Never paste it into a chat, an e-mail or a logbook. The page gets each post's HTML cleaned of anything
executable, and the server fetches images and attachments, keeping them in
a private cache. The server listens only on the machine itself, and the page
needs the key from the printed address.

```{admonition} Did you know?
:class: tip

Arrow keys in the search results move the logbook to each match in turn, so
you can read the matches in their context without leaving the search.
In the editor, an image's size menu sets it to a fixed 300, 500 or 800 px.
```

## Writing

**✚ New post** (`n`) and **✏️ Edit** on a post open the editor in a dialog,
so it never takes reading space; ⤢ makes it full screen. It is CKEditor 5,
the editor SciLog itself uses, in the same version, so a post looks the same
in SciLog and here.

- **Images**: paste, drop or 🖼 upload them; each goes to SciLog at once, not
  only when you save. Drag an image's corner, or choose 300, 500 or 800 px
  in its toolbar: the size is in pixels and stays fixed, here and in SciLog,
  only ever shrunk to fit a narrow screen. The toolbar also places it left,
  centred, right or with text around it.
- **📎 Attach** adds any file as a link; an HTML file (a plot exported with
  plotly or bokeh) is shown as an interactive plot.
- **Your text is kept** in this browser after every change. If the browser
  closes or the dialog is closed without saving, the next time the editor
  opens it offers to restore the draft.
- **Saving** (`Ctrl+Enter`) is refused while an image is still uploading or
  refers to something that is not a file of the post, so nothing is saved
  with broken images. An edit is refused if someone changed the post in the
  meantime; your version stays in the editor.
- **Headings from the outline**: hover a heading in the outline of a post you
  may edit and click its level to turn it into a heading, subheading, minor
  heading or normal text.

You can edit what SciLog lets you edit: your own posts, and those whose
groups include one of yours, as whoever the viewer reads SciLog as (see above).

Writing is switched on per logbook in `~/.eco/scilog_app.json`:

```json
{"write_logbooks": ["68afe9fe6f90f96e7370f9b3"]}
```

The default is only the test logbook above (p22891); `"all"` allows every
logbook you can write to.

## Views and links

The **view** menu next to the search shows all posts, only your own, or those
with one tag; the choice is kept per logbook. 🔗 copies a link to a post in
its context, ⧉ shows the post alone and copies a link to that.

## Not yet

Search across all your logbooks, and SciLog's comments and replies (they are
not shown).
