# Asking eco: `%ei`

Plenty of what you need at the beamline has been done before: someone has
already typed the right command in a console, written a doc section or a
docstring, or left a SciLog post. `%ei` takes your question, finds the parts
of all that which match it, and has a language model answer from them. The
answer names the devices of *your* session and gives a code block to run.

```python
%ei how do I set the attenuator transmission to 10%?
```

```text
You have several attenuators in the namespace (`att`, `att_fe`, `att_usd`).
The standard way is to use the `set_transmission` method ...

    att.set_transmission(0.1)

This moves hardware ... [api AttenuatorAramis]

-- Qwen3.8-27B, 7.7 s; context: 10 command, 6 doc, 8 api
```

Nothing in an answer is run for you. `%ei --paste` puts the answer's code at
the next prompt, where you can read and edit it first.

## Sources: where an answer comes from

Every answer ends with the sources it cites, numbered, each with where to
find it:

```text
-- sources (`%ei --open N` shows one in its context):
[1] [api eco.xoptics.attenuator_aramis.AttenuatorAramis]
      /sf/bernina/.../eco/xoptics/attenuator_aramis.py:57
[2] [2026-10-14 09:12 console:bernina · 091204]
      /photonics/home/.../kernel_logs/20261014_091204_console_3f2a.jsonl:118
[3] [scilog 2026-10-13 10:02 henrik.lemke@psi.ch [26j_kern_p23618 (p23618)]]
      https://scilog.psi.ch/logbooks/6703.../dashboard
[4] [elog 2026-11-02 08:02 Graf, Streich, Holz [SwissFEL commissioning] [Problem, ...]]
      https://elog-gfa.psi.ch/SwissFEL+commissioning/43530
```

`%ei --open 2` shows that source in its context: for a command, the commands
typed before and after it in that session with what came back; for a SciLog
post, the whole post between its neighbours in the logbook; for docs and
code, the section or definition. A search-only listing (`%ei -s`) is
numbered the same way. An elog entry links to itself; SciLog has no
address for a single post, so a post links to its logbook, and its date and
author find it there. In eco desktop
the sources are links under the answer.

In a terminal the locations are clickable too (Ctrl+click in gnome-terminal):
eco prints them as terminal hyperlinks, which gnome-terminal and other VTE
terminals, kitty, WezTerm, iTerm2, Windows Terminal and VS Code's terminal
understand; other terminals show the plain text. A file opens in the
desktop's default application for it, at the start of the file (a file link
has no line). In a session opened over SSH the link opens the same path on
your own machine, which works for files on shared storage (`/sf`, home
directories). `ECO_NO_HYPERLINKS=1` prints plain text.

## Where the answers come from

| Source | What it contributes |
|---|---|
| Command history | Every command typed in an eco console (`~/.eco/kernel_logs`), marked ok, warning or failed by what came back. The model is told to prefer commands that worked and to mention the ones that often gave warnings. |
| eco docs | These pages, one item per section. |
| eco API | Class and function signatures with the first paragraph of their docstrings, read from the source. Nothing is imported for this. |
| Your namespace | Which top-level devices exist and their classes, plus the components of an initialized device the question names. Lazy devices are listed without being initialized. |
| escape | The API of the installed escape package, and the docs and example notebooks of an escape checkout. |
| Notebooks | Code cells of more notebooks, after `%ei --examples PATH`. |
| SciLog | Bernina's logbooks by default, or those chosen with `%ei --scilog-index ...`, as far as the account can read them. |
| Machine elog | SwissFEL commissioning and SwissFEL Diagnostics on elog-gfa.psi.ch: operation, shift handovers, problems. |
| Notes | After `%ei --notes PATH` or `%ei --scan ...` has indexed them. |

The search index lives in `~/.eco/history_index.sqlite`. It is updated
before a question at most every ten minutes, and only files that changed are
read again. `%ei --rebuild` removes it and builds it again from scratch,
with SciLog and the elog (a few minutes); notes added with `--notes` or
`--scan` then need adding again.

Where `~/.eco` is on a network share mounted by the desktop or over SMB, as
on gac-bernina, the index goes to `~/.cache/eco` instead, or to a per-user
directory in `/tmp` if that is on a share too. The database cannot be used
reliably on such a share: it fails with "unable to open database file". An
index already on the share is copied over the first time. `$ECO_INDEX_DIR`
sets the directory explicitly.

## Analysis questions: escape

Data analysis is done with escape, not with eco devices. `%ei` recognizes an
analysis question either by its words ("escape", "analysis", "binning",
"pump-probe", ...) or because most of the best matches come from escape.
The context then comes mainly from escape's API, docs and example notebooks:

```python
%ei how do I load a run and bin a detector by the delay?
```

The answer is only as good as escape's examples: the model copies their
style, and their mistakes. A short primer in `~/.eco/ei_primer_escape.md`
(core objects and idioms, written by someone who knows escape) is added to
the model's instructions for every escape question.

Where the escape docs and examples are read from is set under `"sources"` in
`~/.eco/ei.json`. The first existing checkout from the list is used.

## Notes on paper: photos, scans, webcam

Shift notes, notebook pages and whiteboards can be added to what `%ei`
searches. The model reads each picture once (handwriting included), and the
text is stored in the index, dated by the date written on the note and with
its command lines picked out. After that the note is found like everything
else, also without a key. This needs a model connected, so it is only
offered when there is one.

```python
%ei --scan ~/photos/shift_2026-10-14/      # pictures, PDFs (also scanned), text files
img = cam_north.get_image()                # any image in the session: a camera frame, a PIL image
%ei --scan-image img
%ei --webcam                               # take pictures with a webcam
```

In eco desktop: **Ask → Add Notes from Pictures/PDFs...** and **Add Notes
from Webcam...**, or **Scan...** and **Webcam...** in the Ask panel.

The webcam is the one attached to the computer eco runs on. In a session
opened over SSH, that is the console, not your laptop. To add a note from
your own computer, take a photo and scan the file instead. Pictures are
kept in `~/.eco/ei_scans`, readable only by you, and they are sent to the
model server to be read. A hand-drawn plot is described (axes, units, marked
values), but its data points are not read off.

## SciLog

`%ei` reads SciLog as one of two accounts:

- **as yourself**, with a personal token: every logbook your PSI groups can
  read, across pgroups and beamlines;
- **as the beamline account** (`swissfelaramis-bernina`), when there is no
  personal token, and always on the shared gac-bernina account. This needs
  no token: it logs in with the password eco's SciLog client already uses
  (gopass `elog/scilog-password`, or `~/.scilog_psi`).

Either way, `%ei` reads only what that account can read *now*. Every update
lists the readable logbooks again and removes indexed posts of logbooks the
account has lost access to.

SciLog is searched by default: before a question (at most every ten
minutes) `%ei` fetches the new posts of Bernina's logbooks -- the first
time all of them, about 20 s -- or of the logbooks last chosen with
`%ei --scilog-index`. Without a token and without the beamline password it
says so once per session and searches the rest. `"scilog_default": null`
in `~/.eco/ei.json` switches the default off.

Personal accounts log in through the browser only, so `%ei` uses the token
SciLog gives the browser after that login:

1. log in at <https://scilog.psi.ch>;
2. in the browser's developer console (F12): `copy(localStorage.id_token)`;
3. in eco: `%ei --scilog-token`, and paste it at the prompt. It is not shown,
   not stored in the command history, and kept in `~/.eco/scilog_token`,
   readable only by you. `$ECO_SCILOG_TOKEN` works too.

The token lasts about two weeks; then `%ei --scilog-token new` with a fresh
one. Only ever on your own account: under a shared account (gac-bernina)
everyone using it would read SciLog as you, so eco refuses to store one
there.

```python
%ei --scilog-token                            # who reads SciLog here
%ei --scilog-logbooks --location bernina      # what that account can read there
%ei --scilog-index --location bernina         # all of it, ~15 s for 40 000 posts
%ei --scilog-index                            # later: only what is new, < 1 s
%ei --scilog-search tapedrive trigger         # SciLog's own search, live
%ei what trigger settings did we use for the tapedrive?
```

Indexed posts are kept in the local index, so searching them needs no
network, and an expired token loses nothing already indexed. An update
fetches only posts newer than the last one, so a post edited or deleted
later keeps its indexed version until `%ei --scilog-index --rebuild` reads
the chosen logbooks again completely (~15 s for Bernina's).

Indexed posts are searched together with the history and the docs; the
ones that match a question go to the model with it, like every other
excerpt.

## The machine's elog

The accelerator is still logged in the classic elog at
<https://elog-gfa.psi.ch>, and `%ei` searches its SwissFEL commissioning
and SwissFEL Diagnostics logbooks along with everything else. They are
most useful for what happened to the machine lately:

```python
%ei what happened to the machine over the last day or so?
%ei any Athos problems since Friday?
%ei what did the operators change last night?
```

A question that names a time span -- "last day", "past 3 hours",
"yesterday", "since Monday", "last night", "this week", "recently" -- gets
the logbook entries of that span (elog and SciLog, up to 25, those with
more of the question's other words first) in time order, and the model is
told the current time. The answer goes through them in order, citing each
entry, and the sources link to the entries themselves.

The first question reads both logbooks completely, ~42 000 entries in
about 30 s; after that, each update (at most every ten minutes, before a
question) fetches only the days since the last one, in about a second.
An entry edited more than a day after it was written keeps its indexed
version until `%ei --elog --rebuild`. Which logbooks are read is set by
`"elog_logbooks"` in `~/.eco/ei.json` (logbook names as on the elog's
tabs, `[]` for none).

Both logbooks can be read without logging in. A logbook that asks for a
login is read as the beamline account gac-bernina, with the elog password
eco's elog client uses (gopass `elog/elog-password`, or `~/.elog_psi`).

elog-gfa.psi.ch sends its certificate without the intermediate one, which
browsers fetch by themselves and Python does not. eco does the same as the
browsers: it downloads the intermediate certificate from the address the
server's certificate gives, keeps it only if a root that certifi trusts
signed it, and stores the completed bundle in `~/.eco/ca/`. Certificate
checking stays on.

## Follow-up questions

`%ei` keeps the conversation going, so a short follow-up works:

```python
%ei how do I do a mesh scan with two co-moving motors and a third one stepped?
%ei and how do I put that in a queue instead of running it?
%ei -n ...            # a new conversation
```

Ask the way you would ask a person, question mark included: unlike other
IPython lines, `%ei ...?` is not taken as a request for help on its last
word. The answer is printed rather than returned, so nothing is echoed
afterwards; `%ei --paste` puts its code at the prompt, and
`eco.ei.assistant(namespace).last` holds the whole answer with the items it
was given (`.hits`).

While the model thinks, a terminal shows a spinner with the seconds
waited -- and now and then, at random, a small animation instead (a
chicken working on the chicken-and-egg question, an electron lasing
through an undulator, a coffee brewing, a cat after a laser dot, a scan
finding its peak). `"wait_animations"` in `~/.eco/ei.json` is the share of
questions that get one (0.25; 0 for always the spinner). Jupyter, qtconsole
and output that is not a terminal get neither.

If [glow](https://github.com/charmbracelet/glow) is installed (on the
SwissFEL consoles it is, as `/sf/daq/bin/glow`), a finished answer is shown
rendered: headings, bold, lists and highlighted code instead of Markdown
symbols. While the model writes, the wait display shows the last lines of
the answer; when it is complete, the whole answer is printed rendered. In `~/.eco/ei.json`, `"render": "plain"` switches this off, and
`"glow_style": "light"` suits a terminal with a light background; the
default, `"auto"`, picks light or dark from `$COLORFGBG` and otherwise dark.

## In the browser

`%ei -b` shows the answer as a page in the browser, `%ei -b QUESTION` asks
and shows it there:

```text
%ei -b what trigger settings did we use for the tapedrive?
%ei -b                      # the last answer, or the last search listing
```

The page has the answer, the numbered sources and, below them, every
source in its context. The citations in the answer and the numbers in the
source list link there.

A SciLog source is read from SciLog again while the page is made: as it
looks in SciLog, with its tables and images, between the three posts written
before and the three after it in the same logbook. The cited post is
highlighted. It is read as the account `%ei` indexes SciLog with (your token,
or the beamline account). Without SciLog (no network, no token), the page
shows the indexed text, as `%ei --open N` does; so does every other kind of
source. The machine's elog entries link to the elog.

The pages are kept in `~/.eco/ei_pages`, readable only by you because they
hold SciLog posts. SciLog's images are saved next to each page, and the last
30 pages are kept. A browser is started only on a machine with a display
and not over ssh; otherwise `%ei -b` prints where the page is.

```{admonition} Did you know?
:class: tip

A search without a model works with `-b` too: `%ei -s tapedrive` and then
`%ei -b` puts every match on one page, each SciLog post between its
neighbours.
```

## All options

| Command | Does |
|---|---|
| `%ei QUESTION` | Ask (a follow-up to the previous question). |
| `%%ei` | Ask a question longer than one line. |
| `%ei -n QUESTION` | Start a new conversation. |
| `%ei -b [QUESTION]` | Ask and show the answer in the browser, with each SciLog source between its neighbouring posts; without a question, the last answer or listing. |
| `%ei -s WORDS` | Show only the matching history, docs and API entries, without asking the model. |
| `%ei -m MODEL [QUESTION]` | Switch the model (kept for the session). |
| `%ei --paste` | Put the last answer's code at the next prompt. |
| `%ei --open N` | Source N of the last answer (or listing) in its context. |
| `%ei --bad TEXT` | Commands containing TEXT that gave warnings or errors, worst first. |
| `%ei --index` | Update the search index now. |
| `%ei --rebuild` | Remove the search index and build it again from scratch (minutes). |
| `%ei --scilog [pgroup]` | Index the logbooks of one pgroup (no pgroup: the last choice, else Bernina's). |
| `%ei --scilog-token [new]` | Who reads SciLog here: your token (and until when), or the beamline account; asks for a token if neither works. |
| `%ei --scilog-logbooks FILTERS` | The logbooks that account can read; filters `--location`, `--pgroup`, `--name`. |
| `%ei --scilog-index FILTERS` | Index those logbooks' new posts (`--all` for every one; no filter: the last choice; `--rebuild`: all posts again). |
| `%ei --scilog-search TEXT` | SciLog's own search over everything you can read, live. |
| `%ei --elog [--rebuild]` | Update the elog entries now (`--rebuild`: all of them again). |
| `%ei --notes PATH` | Index a folder of notes (.txt, .md, .pdf). |
| `%ei --examples PATH` | Index notebooks (code cells) as usage examples, for this session. |
| `%ei --scan PATH ...` | Add notes from pictures, PDFs and text files (needs the model). |
| `%ei --scan-image VAR` | Add a note from an image in the session (needs the model). |
| `%ei --webcam` | Add notes from webcam pictures (needs the model). |
| `%ei --key` | Look for the API key again, after adding one. |
| `%ei --models` | Models the server offers. |
| `%ei` | Model, where the API key came from, and the options. |

From Python: `eco.ei.ask("...")`.

## In eco desktop

The **Ask** menu opens the same assistant as a panel (Ctrl+Shift+A), with a
model choice and a **Search only** switch. **To Console** puts the answer's
code at the console prompt, without running it. The menu also updates the
index, updates SciLog (the last choice of logbooks, else Bernina's) and
indexes a notes folder.

## Model and API key

The default is Qwen3.8-27B on PSI's `ai.psi.ch`, an OpenAI-compatible
service. `openai/gpt-oss-120b` is faster, but in tests it wrote longer
answers and was more inclined to guess names. The settings are in
`~/.eco/ei.json`, which is written with the defaults on first use.

You need your own API key from <https://ai.psi.ch> (Settings → Account).
eco looks for it in this order:

1. the environment variable `ECO_EI_API_KEY`;
2. the file `~/.eco/ei_api_key`, which should be readable only by you:
   `(umask 077; echo sk-... > ~/.eco/ei_api_key)`;
3. the gopass entry `ai/psi-api-key`;
4. the key the VS Code extension Continue uses for the same server
   (`~/.continue/config.yaml`).

## Without a key

Not everyone has a key, so the model is optional. Without one, `%ei` still
searches the docs, the API and the command history and lists what matches.
It explains once how to add a key, and after that only notes "search only".

The search needs the index, and building it the first time takes a while:
about 1.5 minutes for the docs, the source and the command history on a
console, plus about 20 s for SciLog and 30 s for the machine's elog. So when
the answer will be a search and that first indexing is still to do, `%ei`
first shows how to search and asks:

```text
eco.ei: without the model, %ei is a search, e.g.
  %ei attenuator transmission          the best matches in the docs, eco's source, the
                                       command history and the logbooks, numbered, with
                                       where each one is
  %ei --open 2                         match [2] in its context (the commands around it,
                                       the neighbouring logbook entries, the docs section)
  %ei what happened since yesterday?   the logbook entries of that time span, in order
  %ei --bad att                        commands like `att...` that gave warnings or errors
  %ei -s ...                           search only, also once there is a key
eco.ei: build the search index now (docs, eco source and command history ~90 s)? [Y/n]
```

Enter builds it, and the search syntax stays on screen while it does. `n`
skips it for the session; `%ei --index` builds it whenever you like. It asks
only in a terminal; eco desktop's Ask panel indexes as before. With a key,
nothing is asked.
In eco desktop, the Ask panel switches to **Search only** by itself. After
adding a key, `%ei --key` picks it up without restarting.

`ECO_NO_EI=1` switches the feature off completely: no `%ei` magic and no
**Ask** menu.

**What leaves the machine:** your question, the matching history, docs and
log excerpts (SciLog posts and elog entries included), and the list of namespace names go to the model server. The
server is PSI's own, but think before indexing notes you would not paste
into it yourself.

```{admonition} Did you know?
:class: tip

`%ei what happened to the machine since yesterday?` goes through the
machine's elog entries of that span, in order, each linked to its entry.
And now and then something other than the spinner keeps you company while
the model thinks.
```
