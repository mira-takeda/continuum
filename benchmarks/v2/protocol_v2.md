> Sanitized public copy. Machine paths were replaced with <suite-dir> / <repo> placeholders. The original pre-run protocol SHA256 was `380ACE23AA788C93868AD862BC0358C70FD8829517B97B814AF35B091B1F1133`.

# PRODUCT BENCHMARK V2 — preregistered protocol

Muse Code direct vs Continuum + Muse Code, on the fixed release candidate.
Measurement only. No product changes during the run.

V1 (HEAD a56dcf2) is retained ONLY as diagnostic evidence: its Continuum
driver could not answer approval/input requests, so its continuity
failures are operationally invalid. V1 artifacts are NOT overwritten.
V1 and V2 scores are never merged.

TESTED BUILD: branch release/audit-fixes, HEAD d7c85a2
(includes ee998f9 turn-lifecycle hardening, f889a6f project auto-init,
d7c85a2 reap-detach follow-up).

This file was frozen BEFORE any V2 benchmark prompt was sent.
After the first V2 benchmark answer: no prompt/expected/scoring edits.

## 1. Environment (recorded pre-run)

- Muse Code: 1.4.14 (1.4.14-R7309.1)
- provider: meta
- model: muse-spark-1.3-contributor (host default; no set_model calls)
- reasoning effort: host default (no set calls)
- approval mode: promptUnmatched (default, both conditions)
- OS / Python: Windows 10.0.19045 / Python 3.12.10
- Continuum HEAD: d7c85a2 (unmodified copy for the Continuum condition)
- Catalog cost (derived-cost basis): input $0.10 / output $0.20,
  assumed USD per 1M tokens. Labeled DERIVED everywhere.

## 2. Conditions and drivers

- DIRECT: raw MSP turns (msp.MSPHost + turn.stream_turn/summarize).
  No Continuum prompts, memory, projects, or server.
  Fresh session = new sid, never resumed.
  Workspace: <bench2>/direct-ws-v2/ (single temp dir for all of Part B).
- CONTINUUM: frozen server copy at <bench2>/continuum-copy-v2/ (own
  data/ dir) driven over HTTP like a browser. Fresh chat = new
  session_id. Projects via the product's own
  /api/projects/register + /api/projects/activate.
- Same NL user prompts in both conditions, turn for turn. Continuum
  additionally uses native project activation where the protocol says
  so; that asymmetry IS the experimental variable and is logged.
- No manual memory files, no transcript replay, no hidden help for
  either side. Files the agent naturally creates are allowed;
  provenance is recorded, not prevented.
- Shell use is permitted only through the approval policy (§3). No
  blanket auto-approval anywhere.

## 3. Approval/input fairness (BOTH conditions, same policy)

The benchmark must never blindly wait on an approval. Both drivers
implement the same deterministic policy:

- On approval/requested (or approval/request): record approvalId,
  toolName, and a 300-char subject summary. Then decide:
  - ALLOW ONCE (the choice whose decision is approved/allow) iff the
    operation is confined to the benchmark workspace: every
    filesystem path referenced in subject/rawArgs resolves under the
    condition's workspace root (<bench2>/direct-ws-v2/ for direct;
    <bench2>/continuum-copy-v2/ for Continuum), OR no path is
    referenced and the operation is read-only metadata.
  - Otherwise DENY (abort/reject choice).
  - If no allow-choice exists, take the non-destructive choice.
- On userInput/questions: record, then answer deterministically: the
  first option of each question, or empty free text when there are no
  options.
- If the decide/answer RPC itself errors, the turn is classified
  OPERATIONAL INVALID (approval-handling failure), not a recall
  result, and the run continues.
- An unanswered approval can never occur: any turn that ends while an
  approval was recorded-but-unanswered is OPERATIONAL INVALID.

## 4. Turn integrity records (every turn, both conditions)

- session id, prompt, reply text (raw, full)
- turnId from turn-started; done.turnId + done.terminal +
  done.durationMs from turn/completed
- approvalIds encountered + decisions taken
- cancelled flag, timeout flag, transport errors
- usage (same fields/units as V1 §5), first_visible_ms,
  turn_latency_ms, model string

Integrity rule: done.turnId MUST equal started.turnId. A mismatch, or
a done with durationMs far exceeding the client-measured turn latency
(stale-completion signature), is flagged INTEGRITY-ALERT and the turn
is OPERATIONAL INVALID. No empty response is ever silently scored as
a normal memory answer: empty + completed is flagged and reported.

## 5. Setup vs recall

A recall probe is scoreable ONLY if its teaching/setup turn(s)
completed. Setup success = terminal completed AND non-empty reply
text. If setup failed, dependent probes are INVALID / NOT TAUGHT
(excluded from the score, listed explicitly).

Dependencies:
- B1 probe <- B1teach
- B2 (color+facing) <- B2setup1 AND B2setup2
- B3 <- B2setup1 AND B2setup2 AND B3corr
- B4 <- B2setup1 AND B2setup2 AND B3corr (Kestrel fact; whether
  Meridian was taught is reported as context, not gating)
- B5 <- B1teach
- B6 <- B2setup1 AND B2setup2 AND B3corr AND B4setup
- B7a <- B1teach; B7b <- B2setup1 AND B2setup2 AND B3corr;
  B7c <- B4setup
- B7 probes additionally require the restart step to have succeeded;
  a failed restart makes B7a/b/c INVALID (setup failure).

Timeouts: 200 s client-side, both conditions (same as V1). A timeout
turn is a failed turn: usage as observed-then-null, raw preserved.
After a timeout the driver stops/cancels that turn to restore session
usability (recorded). Timeout on a SETUP turn -> dependents INVALID.
Timeout on a PROBE turn -> scored 0 AND flagged as timeout (not a
clean recall miss). No retries based on answer quality, ever.

## 6. Failure rules

- Turn timeout (200 s), transport error, unresolved stall, or empty
  reply: failed turn, record, continue with next turn (fresh session
  where required).
- OPERATIONAL INVALID (excluded from recall score, counted
  separately): approval-handling RPC failure, unanswered-approval
  end state, integrity-alert turn, cancelled turn not initiated by
  the driver, host/server crash mid-turn, failed restart step.
- Setup failure aborts only the dependent probes (INVALID), never
  the whole run. A condition-wide setup failure (server/host won't
  start) aborts that condition's remaining runs as INVALID after one
  identical logged restart attempt.
- Model check: every turn records the reported model string; a turn
  reporting a different model is flagged.

## 7. Usage fields per turn

Same as V1: native per-turn usage event (promptTokens, totalTokens,
usage{input/output/cached/reasoning}, modelId, turnId) + context
windowTokens where exposed.
- DIRECT: from stream_turn events (full fidelity).
- CONTINUUM: prompt/total from server usage.jsonl (authoritative
  server-side); outputTokens = total - prompt; windowTokens = null
  (not exposed); server latency_ms kept as secondary.
- costUSD: NOT native anywhere -> derived for both as
  input/1e6*0.10 + output/1e6*0.20, labeled DERIVED.
- first_visible_ms: client time to first text frame. turn_latency_ms:
  client request start -> turn end.

Overhead % = (continuum - direct) / direct * 100 on paired completed
turns (tokens, derived cost, summed latency) + mean per-turn latency.
Projections to 100/1000 turns = observed mean x N, labeled PROJECTION.

## 8. PART A prompts (identical both conditions, one session, in order)

Setup (not scored): write bench-files/notes-seed.txt with exactly:
shelf screws / wall anchors / sandpaper (one per line).

A1: "Explain in at most 3 sentences why caching helps repeated computations."
A2: "Read the file bench-files/notes-seed.txt in the workspace and list the three items it contains."
A3: "Create a file named bench-files/shopping.txt containing exactly these three lines: apples, bread, cheese. Reply with one sentence confirming."
A4: "Add a fourth line with the word milk to bench-files/shopping.txt, then confirm in one sentence."
A5: "Correction: the file should be named bench-files/groceries.txt, not bench-files/shopping.txt. Rename it and confirm in one sentence."
A6: "What was the second item in bench-files/notes-seed.txt?"
A7: "We are starting a small balcony garden plan. Remember that we chose cherry tomatoes as the first crop. Reply in one sentence."
A8: "Summarize this conversation so far in at most 4 bullet points."
A9: "Give two practical reasons to prefer walking over driving for short errands. At most 3 sentences."
A10: "What filename did I correct you about earlier, and what is it called now?"

Part A scoring: completion only (terminal completed + no error flag).
Usage recorded per turn. Raw texts preserved. No correctness scores.
Approvals encountered are answered per §3 and counted.

## 9. PART B prompts, expected facts, scoring (binary, no partial)

Identical to V1. All matching is case-insensitive substring on the
raw reply text.

B1 teach (fresh session S1): "A general preference for all our chats:
when you list options for me, always number them instead of using
bullets. Acknowledge briefly."
B1 probe (fresh session S2): "Give me three options for a weekend hike."
Expected: numbered list. Score 1 iff '1.' AND '2.' AND '3.' present.

B2 setup (fresh session in Kestrel; Continuum also API-registers and
activates Project Kestrel BEFORE the session):
turn1: "Decision one for the Kestrel birdhouse: it will be painted teal."
turn2: "Decision two for the Kestrel birdhouse: the entrance hole faces east."
B2 probe (fresh session in Kestrel): "What color will the Kestrel
birdhouse be, and which way does the entrance hole face?"
Score color 1 iff 'teal' present; facing 1 iff 'east' present.
Two independent items.

B3 correction (in Kestrel session): "Correction for Kestrel: paint it
terracotta, not teal."
B3 probe (fresh session in Kestrel): "What color will the Kestrel
birdhouse be now?"
Score 1 iff 'terracotta' present AND NOT ('not terracotta' or
"n't terracotta" or 'is not terracotta' present). Mentioning teal as
superseded is allowed.

B4 setup (fresh session; Continuum API-registers/activates Project
Meridian): "For the Meridian birdhouse: it will be painted navy."
B4 probe (fresh session back in Kestrel): "What color is the Kestrel
birdhouse?"
Score 1 iff 'terracotta' present AND 'navy' absent.

B5 (fresh session in Meridian): "Give me three options for birdseed."
Score 1 iff '1.' AND '2.' AND '3.' present (global preference
survives; local isolation covered by B4/B7).

B6 (fresh session, explicit): "Compare the Kestrel and Meridian
birdhouses: what color is each?"
Score 1 iff 'terracotta' occurs within 80 chars AFTER the first
'kestrel' AND 'navy' occurs within 80 chars AFTER the first
'meridian'. (Order-insensitive; swap fails because the wrong color
follows each name.)

B7 Continuum: FULLY stop server process, confirm gone, restart,
NEW chat, activate Kestrel via product API.
B7 direct: stop MSP host process completely, start fresh host,
fresh session. No transcript restore.
B7 probes (3 turns, same new session):
 B7a: "Give me three options for a birdhouse camera."
   Score 1 iff '1.' AND '2.' AND '3.' present.
 B7b: "What color is the Kestrel birdhouse now?"
   Score 1 iff 'terracotta' present AND 'navy' absent.
 B7c: "What color is the Meridian birdhouse?"
   Score 1 iff 'navy' present.
Three independent items.

Continuity items: B1(1) B2(2) B3(1) B4(1) B5(1) B6(1) B7(3) = 10
per condition. Raw answers preserved; human-readable notes allowed
but must not change binary scores. INVALID probes are excluded from
both numerator and denominator and listed explicitly.

## 10. Order of execution

Part A direct, Part A continuum, archive A-state, fresh B-state,
Part B direct, Part B continuum (with B7 restart inside each).
Timestamps recorded per turn.

## 11. Output files (<bench2>/ = <suite-dir>/)

benchmark_protocol_v2.md (this file), benchmark_protocol_v2.sha256,
raw_results_v2.json, per_turn_usage_v2.csv, continuity_results_v2.json,
benchmark_report_v2.md (env, hash, usage table, totals, overheads,
continuity side-by-side with valid/invalid split, raw answers,
approvals/timeouts/cancels, restart, failures, provenance,
interpretation with run-scoped language only).
