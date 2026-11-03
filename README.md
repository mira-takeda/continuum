# Continuum

Continuum is a local-first continuity and memory layer around Muse Code.

## What Continuum is

Continuum is a local web frontend for **Muse Code** with persistent personal
memory and project context. The browser is only a different environment for
Muse Code: it streams Muse's exact text, tool activity, permission requests,
questions, errors and results, verbatim. There is no second agent and no
second model call interpreting Muse's output.

Muse remains the agent with native tools. Continuum adds persistent memory,
project context, history and an accessible web interface.

## What problem it solves

- Continuity across sessions: new chats start fresh but remember you.
- Persistent global memory: identity, preferences, decisions, shared history.
- Project-scoped memory and context (`.continuum.md` per project).
- Corrections and supersession instead of silent overwriting.
- Project isolation: one project's facts don't leak into another.
- Continuity after app/server restart.
- An accessible web interface: Continuum provides a simple web interface
  for working with Muse Code, projects, files, memory and approvals
  without requiring users to be comfortable living in a terminal.

## Architecture

```text
Continuum Web UI → local Python bridge → Muse Code / MSP
→ Muse native tools → local computer
```

What each side does:

- **Muse / Meta**: agent runtime, reasoning, native file/tool execution,
  native approvals, model and reasoning controls, hosted model service.
- **Continuum**: persistent memory encyclopedia, global/project routing,
  project registry, `.continuum.md` context, provenance/supersession,
  session/history organization, attachments, onboarding integration,
  incremental context delivery, and the Normal/Pro web interface.

## Built during the Meta Global AI Developer Hackathon 2026

Continuum was built for the Meta Global AI Developer Hackathon
(period: Oct 26 – Nov 4, 2026; track: Push the Frontier). Only work
built during the event is presented as ours.

Meta provides (not our contribution):

- Muse Code
- Muse Spark / hosted model service
- MSP (Muse Server Protocol)
- native file/tool execution
- native approvals
- native model/reasoning controls

Continuum built during the hackathon:

- persistent memory encyclopedia
- global/project routing
- `.continuum.md` project context
- provenance/supersession
- project registry
- session/history UI
- Normal/Pro web UI
- attachments
- onboarding/runtime integration
- incremental context delivery
- manual experimental memory refresh
- EN/ES interface
- one-click launchers

Continuum is an independent project. Participation in the hackathon does
not imply Meta endorsement.

## Requirements

- **Muse Code** (tested with 1.4.14) with a Meta account that has Muse access.
- **Python 3.10+** (3.12 tested). `server.py` is stdlib-only.
- A modern browser. Loopback networking (`127.0.0.1`).

## Quick start

### 1. Easy (recommended)

No terminal needed:

- Windows: double-click `Start Continuum.bat`
- macOS: double-click `Start Continuum.command`

The launcher starts the local server and opens
<http://127.0.0.1:8137/>. If a Continuum server is already running
there, it just opens the browser.

First run: Continuum detects whether Muse Code is installed. If it is
missing, the UI explains how to install it with the official installer
(<https://dev.meta.ai/docs/muse-code/>). If Muse is installed but not
authenticated, a **Connect Meta** banner links to the official setup
flow instead of failing cryptically. Continuum stores no Muse
credentials; auth belongs entirely to Muse (`muse login`).

### 2. Terminal (technical users):

Windows PowerShell:

```powershell
git clone <PUBLIC_REPO_URL>
cd continuum
python server.py
```

macOS/Linux:

```sh
git clone <PUBLIC_REPO_URL>
cd continuum
python3 server.py
```

(`<PUBLIC_REPO_URL>` is a placeholder for the public GitHub URL; it
will be replaced with the real URL after the repository is created.)

Then open <http://127.0.0.1:8137/> in your browser.
Custom port: `python server.py --port 8140` (`python3` on macOS/Linux).

Stop with Ctrl-C. See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) for the
full non-technical guide.

## Benchmarks & Reproducibility

Controlled single-machine runs (single run each; not statistical proof):

- **Continuity V2** (fresh sessions, projects, restart): Continuum 10/10
  valid probes, Muse-direct 1/10, 0 invalid. Same model and config.
- **150-turn long session** (incremental delivery): prompt-token ratio
  Continuum/direct stayed ~1.07–1.14x from turn 1 to turn 150
  (1.085x at turn 150); sequence cost +8.1% DERIVED.
- **Long-session recall matched Muse-direct exactly** (15/25 both) and
  still degraded with context length in both conditions (5/5 → 2/5).
- **Refresh Memory** (experimental) completed correctly at turn 100
  (~1,029 injected tokens) but did not change the frozen probe score
  (3/5 before, 3/5 after).

No universal-superiority claims are made. Methodology, protocol hashes,
full numbers, caveats and reproduction steps:
[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md).

## Limitations

- AI/model outputs can still be wrong. Verify important information and
  review tool actions before approving them.
- Long-session reasoning and recall remain dependent on Muse/model
  behavior; Continuum does not claim to solve model-side degradation.
- Windows native shell execution may require Muse sandbox configuration
  (`muse sandbox windows setup`, run by you, elevated); until then,
  shell turns fail pre-approval with a clear environment error.
- Refresh Memory is experimental.
- Benchmark results are controlled single-machine runs, not universal
  claims.

## Privacy / Security

- Local-first: memories, transcripts, sessions and staged attachments
  live in `data/` on your machine as plaintext Markdown/JSON.
- Memories/transcripts/projects stay local unless Muse or connected
  services send information as part of normal model/tool operation.
- The API binds to loopback only and is defended by Host/Origin checks
  plus a per-process token — but other processes running as the same
  OS user are inside the local trust boundary. No claim of hostile
  local-process isolation.
- Anything you type or paste into chat (including a secret) is your
  content: it appears in transcripts and Muse may memorize it. Do not
  paste secrets. Continuum stores no Muse credentials anywhere.
- Reset (`POST /api/reset` or delete `data/`) clears all
  Continuum-owned local state but never deletes `.continuum.md` files
  inside your own project folders.

## License & Third-Party Services

Continuum source code is licensed under PolyForm Noncommercial 1.0.0.
Required Notice: Copyright 2026 Satoru Takeda.
Commercial use requires separate permission/license.

Muse Code, Muse Spark and Meta-hosted services are provided by Meta
and are subject to Meta's own applicable terms. Continuum does not
redistribute Meta model weights.

## Disclaimer

Continuum is experimental software. AI-generated output may be
inaccurate or incomplete. Verify important information and review tool
actions before approving them.

## Repository layout (contributors)

```text
server.py            # THE one process: UI + API + memory + MSP runtime
memory/              # encyclopedia, index, delivery, retrieval, projects, MSP client
web/                 # dependency-free frontend (chat + Pro panels + i18n)
docs/                # user guide + experiments
benchmarks/          # sanitized public reproduction assets
data/                # runtime state (git-ignored, created on first run)
evals/               # frozen 48-probe harness + report + unit tests
dev/browser/         # Playwright dev smokes (not product runtime)
```

Units: `python evals/test_*.py` (stdlib + repo only). Browser smokes:
`python dev/browser/smoke_<area>.py` (see `dev/browser/README.md`).
