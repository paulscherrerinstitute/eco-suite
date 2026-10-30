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

## Where the answers come from

| Source | What it contributes |
|---|---|
| Command history | Every command typed in an eco console (`~/.eco/kernel_logs`), marked ok, warning or failed by what came back. The model is told to prefer commands that worked and to mention the ones that often gave warnings. |
| eco docs | These pages, one item per section. |
| eco API | Class and function signatures with the first paragraph of their docstrings, read from the source. Nothing is imported for this. |
| Your namespace | Which top-level devices exist and their classes, plus the components of an initialized device the question names. Lazy devices are listed without being initialized. |
| escape | The API of the installed escape package, and the docs and example notebooks of an escape checkout. |
| Notebooks | Code cells of more notebooks, after `%ei --examples PATH`. |
| SciLog, notes | Only after `%ei --scilog [pgroup]` or `%ei --notes PATH` has indexed them. |

The search index lives in `~/.eco/history_index.sqlite`. It is updated
before a question at most every ten minutes, and only files that changed are
read again.

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

## Follow-up questions

`%ei` keeps the conversation going, so a short follow-up works:

```python
%ei how do I do a mesh scan with two co-moving motors and a third one stepped?
%ei and how do I put that in a queue instead of running it?
%ei -n ...            # a new conversation
```

## All options

| Command | Does |
|---|---|
| `%ei QUESTION` | Ask (a follow-up to the previous question). |
| `%%ei` | Ask a question longer than one line. |
| `%ei -n QUESTION` | Start a new conversation. |
| `%ei -s WORDS` | Show only the matching history, docs and API entries, without asking the model. |
| `%ei -m MODEL [QUESTION]` | Switch the model (kept for the session). |
| `%ei --paste` | Put the last answer's code at the next prompt. |
| `%ei --bad TEXT` | Commands containing TEXT that gave warnings or errors, worst first. |
| `%ei --index` | Update the search index now. |
| `%ei --scilog [pgroup]` | Index SciLog posts (new ones since the last time). |
| `%ei --notes PATH` | Index a folder of notes (.txt, .md, .pdf). |
| `%ei --examples PATH` | Index notebooks (code cells) as usage examples, for this session. |
| `%ei --key` | Look for the API key again, after adding one. |
| `%ei --models` | Models the server offers. |
| `%ei` | Model, where the API key came from, and the options. |

From Python: `eco.ei.ask("...")`.

## In eco desktop

The **Ask** menu opens the same assistant as a panel (Ctrl+Shift+A), with a
model choice and a **Search only** switch. **To Console** puts the answer's
code at the console prompt, without running it. The menu also updates the
index and indexes SciLog or a notes folder.

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
In eco desktop, the Ask panel switches to **Search only** by itself. After
adding a key, `%ei --key` picks it up without restarting.

`ECO_NO_EI=1` switches the feature off completely: no `%ei` magic and no
**Ask** menu.

**What leaves the machine:** your question, the matching history, docs and
log excerpts, and the list of namespace names go to the model server. The
server is PSI's own, but think before indexing notes you would not paste
into it yourself.
