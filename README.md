# Continuum — a memory in motion

Continuum is a local web frontend for **Muse Code** with persistent personal
memory and project context. The browser is only a different environment for
Muse Code: it streams Muse's exact text, tool activity, permission requests,
questions, errors and results, verbatim. There is no second agent and no
second model call interpreting Muse's output.

Path: browser UI → local Python app → Muse Code (`muse serve`, MSP stdio)
→ Muse Spark.

A local Markdown encyclopedia augments every turn (derived CORE + retrieved
entries). Muse maintains it through its own fenced blocks, parsed
deterministically — never silently rewritten, with provenance and
supersession instead of deletion.

## Requirements

- **Muse Code** (tested with 1.4.14) with a Meta account that has Muse access.
- **Python 3.10+** (3.12 tested). The product server is stdlib-only —
  no `pip install` needed to run Continuum itself.
- A modern browser (Chromium/Chrome/Edge/Firefox/Safari).
- Loopback networking (`127.0.0.1`). Continuum never listens on other
  interfaces and phones nothing home; all traffic is you → localhost →
  your Muse installation → Meta.

## Install Muse Code

Continuum does not bundle or silently install Muse. If it is missing, the
UI tells you so. Install it yourself with the official one-line installer
(docs: <https://dev.meta.ai/docs/muse-code/>):

macOS / Linux:

```sh
curl -fsSL https://dev.meta.ai/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://dev.meta.ai/install.ps1 | iex
```

Then confirm `muse --version` works in a new terminal.

## Meta login / auth

Auth belongs entirely to Muse. In a terminal, run `muse` once (or
`muse login`): on first run Muse Code asks you to trust the workspace and
prompts you to authenticate with a browser sign-in or an API key (reopen
with `/login` inside an interactive session; billing and full options:
<https://dev.meta.ai/docs/muse-code/auth>).

Continuum itself stores no Muse credentials — no keys, tokens or login
state in its files, `localStorage`, transcripts or memory. But note the
distinction: anything *you* type or paste into chat (including a secret)
is your content — it will appear in transcripts, and Muse may keep it in
memory if it judges it durable. Do not paste secrets into chat.

If the machine is not authenticated, Continuum shows a **Connect Meta**
banner linking to the official setup page instead of a cryptic failure.

## Python setup

No virtualenv needed for normal use — `server.py` uses the standard library
only. A venv is only required for the optional dev browser tests:

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r dev/browser/requirements.txt
python -m playwright install chromium
```

macOS / Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r dev/browser/requirements.txt
python -m playwright install chromium
```

## Start

From the repo root (`python` on Windows, `python3` on macOS/Linux):

```powershell
python server.py
```

then open <http://127.0.0.1:8137/> in your browser. Custom port:

```powershell
python server.py --port 8140
```

Stop with Ctrl-C in the terminal.

## First run

1. Continuum detects whether Muse Code is installed.
2. If installed but logged out, you get the Connect Meta banner; log in,
   press Recheck.
3. If everything is ready, the app opens straight into a new conversation.
4. `data/` starts empty and is created automatically: sessions, transcripts,
   memory index, usage log and attachment staging all appear on first use.
   Conversations begin with zero memories; Muse starts remembering what
   matters from the first turn (see Memory below).

## Interface language (EN/ES)

The quiet `ES | EN` switch next to Normal/Pro changes Continuum's chrome
only (menus, status, buttons, tooltips). It never translates Muse's replies,
your messages, memories, project files, transcripts or attachments, and it
never affects retrieval. Preference persists across reloads. Default: ES.

## Normal vs Pro

Both modes drive the **same** Muse agent with the same capabilities. Only
the presentation differs, and switching never changes model, effort,
permissions, session, project or memory.

- **Normal**: clean conversational UI, operational status
  (Thinking / Using a tool / Waiting for approval / Responding…), elapsed
  time, human-readable approval explanations with the byte-identical
  technical action one click away.
- **Pro**: the same agent plus raw/native telemetry and controls — exact
  tool names and arguments, session info, usage/context, tasks/todos,
  goals, skills, compact, approval modes, pending approvals, and gated
  experimental items (permission profiles, logs) where your Muse version
  supports them.

## Memory

You never type "remember this". After each turn, Muse may emit durable
facts; Continuum stores them as one Markdown file per entry under
`data/entries/`, with provenance (user-stated vs inferred), revision
history and supersession links. The UI toasts Remembered / Updated /
Superseded with undo. The Memory view and Memory Lab show the index,
connections, accessibility states (Active/Recent/Deep — reduced
accessibility, never deletion) and exactly which entries each answer used.

Entries and transcripts are local **plaintext** Markdown/JSON: readable,
editable, portable — and not encrypted. `data/entries/` plus
`data/transcripts.json` move cleanly to another machine, but the rest of
`data/` is machine-specific: the project registry holds absolute paths and
Muse session ids are meaningless on another computer. After copying,
re-register projects (or reset and start fresh). `data/index.json` and
`data/core.json` are derived caches and rebuild automatically.

## Projects

Each real project folder may hold a human-readable `.continuum.md` with
project-only context (state, decisions, pending work, key paths, failed
approaches). Continuum tracks an active project per conversation: switching
projects swaps context files without contaminating them, while global user
memory travels through the encyclopedia. Ask Muse to create projects in
normal language — it uses its native file tools; Continuum only registers
and activates what Muse made. Note the boundary: the active project
selects *context* (which `.continuum.md` is read, where project memories
go) — it is not a filesystem jail. Muse keeps its normal native ability
to work anywhere on your computer, subject to its own approval flow.
Credentials don't belong in `.continuum.md`.

## History

Every conversation keeps its transcript (`data/transcripts.json`, text plus
attachment metadata — never file contents). History cards show a
deterministic local title (no extra model call), project, date, turn count
and a preview; raw session ids stay behind a collapsed technical block
(auto-open in Pro). Resume continues the same Muse session. New Chat starts
a blank conversation in a fresh Muse session while keeping your project and
all memory.

## Attachments

The paperclip (or drag-and-drop onto the conversation) stages files
server-side — max 5 files, 10 MB each — and Muse reads them with its native
file tools during that turn. Chips show name/size with per-file remove;
selecting or removing never destroys your draft. Originals are never moved
or modified, and transcripts record only filename/type/size. Staged copies
live under `data/attachments/<chat>/` (portable with `data/`, delete them
with it when you reset).

## Model and reasoning controls (Pro)

The model list always comes from your live provider catalog at runtime
(`model/list`) — nothing is hardcoded. Switching models or reasoning effort
uses Muse's native session controls, is fully reversible, and never touches
memory, projects, sessions or transcripts. Pricing/availability details, if
shown, come from provider metadata only.

## Known limitation: Windows shell sandbox

Native shell approvals need a one-time elevated setup that this project
deliberately does not run for you:

```powershell
muse sandbox windows setup
```

Until you run it yourself (Administrator/UAC prompt), shell turns on
Windows fail pre-approval with a clear environment error instead of
executing. File reads/writes, memory, projects, chat and approvals relay
are unaffected. Non-shell approval coverage is exercised with
`python dev/browser/smoke_approvals.py --stub`.

## Uninstall / reset

- **Reset (forget everything Continuum owns)**: stop the server and
  delete the `data/` directory (or call `POST /api/reset`:
  `{"mode": "sample"}` reseeds the synthetic demo pack, any other body
  wipes clean). Reset clears all Continuum-owned local runtime/personal
  state: encyclopedia entries, derived index/CORE, operation log, usage
  log, chat sessions, transcripts, staged attachments, Muse session
  mappings, and the project registry (registered projects, workspace
  root, per-chat project mapping). A restart recreates empty state.
- **What reset does NOT delete**: `.continuum.md` files inside your own
  project folders. Those are human-readable project documents that live
  with your projects, not Continuum runtime state; edit or delete them
  yourself if you want them gone.
- **Uninstall**: delete the repo folder. Continuum writes nothing outside
  it except your own `.continuum.md` files, your staged attachments
  (inside `data/`), and whatever Muse itself stores in its own directories
  (`~/.local/share/muse` sessions). Remove the optional `.venv` and
  Playwright browsers (`playwright uninstall`) if you installed them.

## Repository layout

```text
server.py            # THE one process: UI + API + memory + MSP runtime
memory/              # encyclopedia, index, access, retrieval, projects, MSP client
web/                 # dependency-free frontend (chat + Pro panels + i18n)
data/                # runtime state (git-ignored, created on first run)
evals/               # frozen 48-probe harness + report + unit tests
dev/browser/         # Playwright dev smokes (not product runtime)
```

## Evals and tests (for contributors)

- Units: `python evals/test_*.py` (15 suites, stdlib + repo only).
- Frozen 48-probe memory comparison lives in `evals/` with its report;
  do not re-tune against it.
- Browser smokes: `python dev/browser/smoke_<area>.py [--no-start --url …]`
  (see `dev/browser/README.md`); screenshots land in `dev/browser/shots/`.
