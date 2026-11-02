# Browser smoke test (dev tool only)

Drives the real Continuum UI in headless Chromium. Not part of the product
runtime and not a user-facing dependency.

## Install (once, project-local env)

```powershell
cd continuum-public
python -m venv .venv
.venv\Scripts\activate
pip install -r dev/browser/requirements.txt
python -m playwright install chromium
```

## Run (from repo root, env activated)

```powershell
python dev/browser/smoke.py
```

Starts its own server on port 8137, or connect to a running one:

```powershell
python dev/browser/smoke.py --no-start --url http://127.0.0.1:8137/
```

## Exit codes

- `0` pass: connected chat turn streamed, views opened, no console errors.
- `2` degraded: UI loads but backend/Muse unavailable; frontend-only views
  captured and the gap is reported (chat/activation/approval skipped).
- `1` failure.

## Project-lifecycle smoke

```powershell
python dev/browser/smoke_projects.py
```

End-to-end NL project creation in Documents, workspace transition, decision
routing, switch away/back. Cleans up its temp project afterwards. Approvals
are auto-allowed only when the card references the temp project folder.

## Native-capability parity check

```powershell
python dev/browser/smoke_native.py
```

One ordinary file task phrased to match no project pattern: proves Muse
through Continuum acts with native file tools (no interception, no
registration side effects). Cleans up afterwards.

## Bookkeeping-order check

```powershell
python dev/browser/smoke_bookkeeping.py
```

NL project creation where Muse must act natively first: asserts from the
captured SSE that no `project-created` op ran pre-turn and that post-turn
bookkeeping registered/activated the folder Muse made. Cleans up afterwards.

## Approval-explanation check

```powershell
python dev/browser/smoke_approvals.py [--stub]
```

Live mode drives two real shell turns (mapped cmdlet + unmapped command),
asserts the explanation, byte-identical technical details, Allow-executes and
Deny-prevents. `--stub` replays canned approval frames through the real UI
when the machine lacks shell sandbox setup; it additionally asserts the
Allow/Deny payloads carry the original ids verbatim.

Environment limitation (recorded, not changed): native shell approvals
require one-time elevated `muse sandbox windows setup`, which has NOT been
run here. Until it is, live shell turns fail pre-approval with
`windows_elevated setup_required`; use `--stub` on this machine.

## Pro Mode check

```powershell
python dev/browser/smoke_promode.py
```

Normal/Pro toggle, raw rendering, read-only panels, native controls,
approval-mode selector and experimental-gated items. Canned SSE plus stubbed
`/api/pro/*` through the real UI; asserts posted payloads, confirm dialogs
(compact, allowAll, experimental profile) and byte identity. Defaults to port
8138; pass `--port/--url` to target your instance (the product server
defaults to 8137).

## Notes

- Screenshots and `summary.json` land in `dev/browser/shots/` (git-ignored).
- The smoke sends one benign chat turn, so a normal run appends to the real
  `data/sessions.json` and `data/usage.jsonl`, like any dev chat.
- If an approval card appears it is captured and reported, never auto-decided.
