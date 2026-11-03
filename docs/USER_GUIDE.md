# Continuum User Guide

Continuum is a local web app that gives Muse Code a persistent memory:
it remembers you, your projects and your shared history across chats
and restarts. Muse remains the agent doing the work; Continuum adds
continuity, memory, project context and an accessible interface.

## Getting started

You need two things: **Muse Code** (the agent) and **Python 3.10+**
(runs Continuum itself; 3.12 tested). A modern browser completes
the setup.

## First launch

Start Continuum (see below). On first launch it checks its own
requirements and tells you plainly what is missing, if anything.

## Installing / connecting Muse Code

Continuum does not bundle Muse and never installs it silently.

- If Muse Code is missing, the app shows setup guidance pointing to
  the official installer (<https://dev.meta.ai/docs/muse-code/>).
- If Muse is installed but you have never logged in, a **Connect
  Meta** banner appears. Log in with `muse login` (your terminal or
  the official flow), then press Recheck in Continuum.
- Authentication belongs entirely to Muse. Continuum never stores
  your Muse credentials.

## Starting Continuum

- **Windows:** double-click `Start Continuum.bat`
- **macOS:** double-click `Start Continuum.command`

Your browser opens at <http://127.0.0.1:8137/>. If Continuum is
already running, the launcher just opens the browser window.

## Normal Mode

Normal Mode is the default simplified interface: a clean conversation
with a small operational status (Thinking, Using a tool, Waiting for
approval, Responding) and elapsed time. Approvals show a short
human-readable explanation with the exact technical action one click
away. Nothing about Normal reduces what Muse can do.

## Pro Mode

Pro Mode is an optional advanced interface for the same agent: exact
tool names and arguments, session information, usage/context figures,
tasks, goals, skills, approval modes and model controls. Use the
Normal/Pro toggle at any time.

## New Chat

**New Chat** clears the visible conversation and starts a genuinely
new Muse session. It does not delete History, memories or the active
project: the previous chat stays in History and global memory keeps
working.

## History

Every conversation is listed with a short automatic title, date,
project, turn count and preview. Opening one restores the full
transcript and continues the same Muse session.

## Projects

A project is one of your real folders, registered in Continuum. The
active project drives which project context is loaded; switching
projects swaps that context without mixing projects. Ask Muse in
normal language to create or organize projects — Muse uses its own
file tools, and Continuum registers and activates the result.

## Global memory vs project memory

- **Global memory** (the encyclopedia) travels with you everywhere:
  who you are, preferences, people, durable cross-project knowledge.
- **Project memory** (`.continuum.md` in the project folder) holds
  only that project's state, decisions, pending work and conventions.

You never manage files by hand and never type "remember this": Muse
maintains both automatically, and the UI confirms with Remembered /
Updated / Superseded notices (with undo). Corrections replace old
values while preserving history.

## Attachments

Use the paperclip (or drag files onto the conversation) to attach up
to 5 files (10 MB each) to a message. Muse reads them during that
turn. Your originals are never moved or modified.

## Tool approvals

When Muse wants to act on your computer (files, commands), Continuum
shows what it wants to do and asks you to **Allow** or **Deny**. The
original action is always shown unchanged. Review before allowing;
when in doubt, deny.

## Refresh Memory (Experimental)

In Memory Lab you will find **Refresh Memory · Experimental**. It
sends one compact snapshot of your current memory into the ongoing
Muse session (same chat, same history), then returns to normal
behavior. It never deletes or rewrites stored memories. It is
experimental: use it when Muse seems to have lost track of something
it should know. Automatic refresh is not implemented.

## Models and reasoning controls

In Pro Mode you can see and change the active model and reasoning
effort using your provider's live catalog and Muse's native session
controls. Changes are reversible and never touch memory, projects or
history.

## Feature status

Stable / current:

- Persistent global memory
- Project-scoped memory/context
- Provenance and supersession
- Normal Mode
- Pro Mode
- History
- Attachments
- One-click launchers
- EN/ES interface

Experimental:

- Refresh Memory

Notes:

- Normal Mode is the default simplified interface.
- Pro Mode is an optional advanced interface exposing more
  Muse/session/model/approval diagnostics and controls.
- Switching Normal/Pro does not itself change memory, project,
  model, reasoning, permissions or session state.
- Refresh Memory is experimental. In the frozen 150-turn benchmark
  it completed correctly but did not improve the tested recall
  score (3/5 before, 3/5 after).
- Automatic memory refresh is not implemented.
- Continuum does not currently claim to solve model-side
  long-session recall degradation.

## Restarting / stopping Continuum

Close the browser tab any time; your state is saved continuously.
To stop the server, use Ctrl-C in its terminal window (or close the
launcher window). Start it again the same way — History, projects
and memory resume.

## Where Continuum stores local data

Everything lives in the `data/` folder next to the app: memory
entries, transcripts, sessions, attachments and indexes, as
readable local files. Your `.continuum.md` project files live
inside your own project folders. To move machines, copy `data/`
(entries and transcripts travel; re-register projects, whose paths
are machine-specific). To forget everything Continuum owns, stop
the server and delete `data/` (your `.continuum.md` files stay).

## Windows notes

File operations, memory, chat and approvals work normally. Native
*shell command* execution additionally needs Muse's one-time
elevated sandbox setup (`muse sandbox windows setup`, which you
must run yourself as Administrator); until then, shell turns fail
before approval with a clear message instead of executing.

## macOS notes

Double-click `Start Continuum.command` (first launch may ask for
permission to run it — allow it in System Settings if needed).
Install Muse with the official `install.sh` one-liner if missing.

## Troubleshooting

- **Blank page / cannot connect:** is the server terminal still
  running? Restart the launcher.
- **Connect Meta banner:** run `muse login`, then Recheck.
- **Muse missing:** follow the in-app install guidance, then
  restart Continuum.
- **An action looks wrong:** deny the approval, explain the problem
  to Muse in chat, and continue.
- **Something remembered incorrectly:** correct Muse in normal
  language — corrections supersede the old value with history kept.

## Privacy and important limitations

Your data stays on your machine as plaintext files unless Muse or
connected services send information as part of normal model/tool
operation. Other programs running as the same computer user could
in principle reach the local service; Continuum does not isolate
you from hostile local software. AI output can be wrong — verify
important information. Benchmark and evaluation claims in this
project are controlled single-machine runs; see
[EXPERIMENTS.md](EXPERIMENTS.md) for honest numbers and caveats.
