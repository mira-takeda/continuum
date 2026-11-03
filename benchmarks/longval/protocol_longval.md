> Sanitized public copy. Machine paths were replaced with <suite-dir> / <repo> placeholders. The original pre-run protocol SHA256 was `A5D5F62F5F5B92BD3577846376523F373D3CC05973F4EB79AA058789835E1059`.

# LONG-SESSION VALIDATION — delivery-fix protocol (preregistered)

Separate validation. NOT V1, V2, or the scale benchmark. Those
artifacts are never modified. Frozen BEFORE the first validation
model call. No prompt/scoring/procedure edits after the run begins.

PRODUCT BUILD: branch release/audit-fixes, HEAD
fe2408cd4e375605b8e3945ed39ff4f2dfa5c169 (unmodified file copy).
No product changes, tuning, commits, push, or ZIP.

Reference scale protocol SHA:
50611791204E377D434BC0EC15FAABD888072D2C352BCD5AFBE84D61A71E1FA7

## 1. Goal

Validate that the session-aware bootstrap + incremental-delta fix
(commit fe2408c) removes the long-session token-growth pathology
without reducing recall:

1. Does Continuum prompt-token growth stay near Muse-direct instead
   of diverging as the session gets longer?
2. Does continuity/recall remain intact?
3. Any new lifecycle, timeout, cancellation, or delivery-state bugs?

## 2. Configuration (recorded pre-run)

- Muse Code 1.4.14 (1.4.14-R7309.1); provider meta
- model muse-spark-1.3-contributor (host default; verified present
  in providerCatalog 2026-11-02; no set_model calls either side)
- reasoning effort: host default (catalog default max; no set calls)
- approval mode promptUnmatched, both conditions
- OS / Python: Windows 10.0.19045 / Python 3.12.10
- `muse usage` (CLI): "not logged in: run `muse login` first";
  MSP providerCatalog + turns serve via the contributor
  entitlement (same metering state as the scale run, which
  recorded no_allowance with working serving).
- Same machine, runs sequential (direct first, then continuum).
- Derived-cost basis (not billing): input $0.10 / output $0.20
  per 1M tokens, labeled DERIVED. Same as scale §1.

## 3. Conditions and isolation

- DIRECT: raw MSP turns (msp.MSPHost + turn.stream_turn/summarize
  imported read-only from the product tree; turn.py/msp.py are
  byte-identical to d7c85a2, untouched by fe2408c).
  Fresh sid; never resumed.
- CONTINUUM: file copy of the product tree at fe2408c over HTTP
  (browser-equivalent path), port 8163. Fresh chat session.
- Same NL user prompts turn-for-turn (frozen scale Test1 text).
- Isolated environments under <suite-dir>/:
  lv-direct-ws/ and lv-continuum-copy/ (+ its data/).
  No real user data/projects/memories.
- NO projects registered/activated (same as scale Test1).

## 4. Frozen inputs (read-only; hashed here)

- scale_test1.json SHA256:
  CE659E62FA99C13F21E19FD749ED9457DB5662B0C6A0A9AD8827662E932706FF
- Content turns reused VERBATIM: n = 1..100 (first 100 of 250).
- Probes reused VERBATIM: the 15 probes with after = 10/50/100
  (recent/medium/old/corrected/indirect per checkpoint) with
  their frozen expected rules.
- No other turns, no rewording, no added probes.
- Approval policy + matcher + scoring: byte copy of frozen
  scale_common.py (SHA256 recorded in the report; verified
  identical before the run).

## 5. Procedure

One continuous session per condition (session tag "lvmain").
Content turns T1-0001..T1-0100 in frozen order. After content
turns 10/50/100 insert the 5 frozen probes for that checkpoint,
then continue the SAME session. Total 115 turns/condition.
File ops use bench-files-scale/ (frozen texts; turns 24/49/74/99).

Per turn record (both conditions): turn number/tag, prompt, raw
reply, started.turnId, done.turnId/terminal/reason/durationMs,
approvalIds+decisions, questions count, terminal/error,
timeout/cancelled flags, invalid reason, integrity_alert,
usage (prompt/output/total tokens, model, derived cost),
first_visible_ms, turn_latency_ms, tool-event count,
md_count + agents_present, ts.

Continuum additionally records per turn: native sid, started
delivery stats (mode/added_chars/new_entries/superseded),
delivery-fallback events if any. Bootstrap count, repeated
bootstraps, and compaction-heuristic observations are tallied
from these records.

## 6. Scoring (binary 1/0, deterministic, no LLM judge)

Matcher and rules are the frozen scale §6 semantics, executed by
the byte-identical common-code copy:
- present: 1 iff every word matches.
- corrected: 1 iff `new` matches AND NOT ("not {new}"/"n't {new}").
Setup gating per frozen scale §7: a probe is scoreable ONLY if
its referenced fact turn (+ correction turn for corrected
probes) completed with non-empty reply, no invalid, no timeout;
else INVALID / NOT TAUGHT. Probe timeout => scored 0 + flagged.
Empty completed turn => flagged, scored by rule (0 unless the
rule passes on empty text, which it cannot for present/corrected).

## 7. Approval policy (identical both conditions)

Frozen scale §8, executed by the identical code copy. Never
silently wait; allow-once iff confined under the condition
workspace root (workspaceRoot metadata excluded); else deny;
questions answered with first option. Approval-RPC failure or
unanswered-approval end state => turn OPERATIONAL INVALID.

## 8. Turn integrity, timeouts, stopping

- Client timeout 200 s; on timeout the driver stops/cancels that
  turn (recorded) and continues. No retries on content, ever.
- Integrity: done.turnId MUST equal started.turnId or INVALID.
- One driver invocation per condition (single session cannot
  span processes). Interruption ends the leg at a recorded
  boundary; a leg that cannot complete in one invocation is
  reported with its exact boundary per scale §11 (abort only on
  operational inability: host/server won't start after 1
  identical restart, 5 consecutive transport failures, or
  session death; NEVER for bad recall).
- Suspected compaction (prompt-token drop >30% turn-over-turn
  with session continuity) is recorded as an observed anomaly.

## 9. Fileop residue note

Frozen fileop prompts use relative bench-files-scale/ paths. In
the scale run these resolved outside the Continuum copy (3 tiny
synthetic strays, since removed). If this validation recreates
equivalent synthetic files (note.txt/todo.txt), they are logged
as residue of THIS run and removed after raw evidence is
preserved. Content is frozen synthetic text only.

## 10. Analysis (measured, no extrapolation of primaries)

Prompt tokens at turns 1/10/25/50/75/100 both conditions +
ratio + Continuum-added chars that turn; mean prompt growth per
turn for 1-10/11-50/51-100; cumulative prompt tokens/cost
through 10/50/100; recall at 10/50/100 by category; bootstrap
count; mean delta chars; compaction/reset events;
timeouts/cancels/empties/errors; old-build comparison below.

Historical baseline (preserved scale numbers, NOT rerun):
old Continuum @ d7c85a2 prompt tokens: turn 10 = 42,513;
turn 50 = 104,799; turn 100 = 209,135.
Old scale recall: turn 10: 5/5; turn 50: 3/5; turn 100: 3/5
(both conditions combined per the task brief; per-condition
values taken from preserved results at report time for context).

## 11. Pass-review signals (observations, not targets)

Report whether: (A) no recurrence of accelerating injection;
(B) no repeated full bootstrap in an uninterrupted session;
(C) recall not materially worse than the preserved run;
(D) no new lifecycle/reliability regression;
(E) deltas bounded as memory grows. No decision made here.

## 12. Outputs (<suite-dir>/)

long_validation_protocol.md, long_validation_protocol.sha256,
val_common.py (byte copy), run_val_direct.py, run_val_continuum.py,
score_val.py, raw_turns.jsonl, usage.csv, recall_results.json,
long_validation_report.md
