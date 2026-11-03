> Sanitized public copy. Machine paths were replaced with <suite-dir> / <repo> placeholders. The original pre-run protocol SHA256 was `50611791204E377D434BC0EC15FAABD888072D2C352BCD5AFBE84D61A71E1FA7`.

# SCALE / LONG-HORIZON BENCHMARK — preregistered protocol

Separate benchmark. NOT V1 or V2. V1/V2 artifacts are never touched.
Frozen BEFORE any scale benchmark prompt was sent. No semantic
prompt/scoring changes after the run begins.

PRODUCT BUILD: branch release/audit-fixes, HEAD d7c85a2
(unmodified copies). No product changes, tuning, commits, or push.

## 1. Configuration (recorded pre-run)

- Muse Code 1.4.14 (1.4.14-R7309.1); provider meta
- model muse-spark-1.3-contributor (host default; verified present in
  catalog; no set_model calls in either condition)
- reasoning effort: host default (no set calls)
- approval mode promptUnmatched, both conditions
- OS / Python: Windows 10.0.19045 / Python 3.12.10
- `muse usage`: "No subscription allowance (no_allowance)" — same
  metering state under which V2 ran successfully; serving works via
  the contributor entitlement. Derived-cost basis (not billing):
  input $0.10 / output $0.20 per 1M tokens, labeled DERIVED.
- Same machine for both conditions, runs sequential (no contention).

## 2. Conditions and isolation

- DIRECT: raw MSP turns (msp.MSPHost + turn.stream_turn/summarize).
  No Continuum prompts/memory/projects/server. Fresh sid per
  required session boundary, never resumed.
- CONTINUUM: frozen server copy over HTTP (browser-equivalent).
  Fresh chat session per required boundary. Project activation via
  the product's own register/activate API where specified.
- Same NL user prompts turn-for-turn; activation asymmetry is the
  logged experimental variable.
- Test 1 and Test 2 use SEPARATE isolated environments:
  scale-test1/{direct-ws,continuum-copy,data} and
  scale-test2/{...}. No real user data/projects/memories anywhere.

## 3. Frozen artifacts (hashed here, immutable after)

- gen_scale.py (seeds 20261102 / 77031; re-running reproduces bytes)
- scale_test1.json SHA256:
  CE659E62FA99C13F21E19FD749ED9457DB5662B0C6A0A9AD8827662E932706FF
  (250 content turns: 53 facts, 8 corrections, 10 fileops,
  8 references, 171 fillers; 20 checkpoint probes)
- scale_test2.json SHA256:
  E846D2561757CF6EE987C33CA648A78C0563E0433204FB865102164FE6BC32B7
  (250 facts: 5 projects x45 + 25 globals; 60 teaching turns:
  50 fact batches + 5 Y-correction + 5 Z-correction; 48 probes:
  12 per level + 12 restart)
- Value pools are pairwise substring-collision-free (asserted by
  the generator), disjoint across tests, and exclude V1/V2 words.

## 4. TEST 1 — long single session

One continuous session per condition. Content turns T1-0001..T1-0250
in frozen order. After content turns 10/50/100/250 insert the 5
frozen probes for that checkpoint (recent/medium/old/corrected/
indirect; probe ids T1-P10-*, ...), then continue the SAME session.
Total 270 turns/condition. File ops use bench-files-scale/.

## 5. TEST 2 — durable memory load

- 5 projects (Acorn/Birch/Cedar/Dogwood/Elm), 45 facts each; 6
  conflict attributes (distinct value per project); 25 globals;
  15 X->Y corrections + 5 X->Y->Z corrections (frozen selection).
- Teaching in frozen batch order (project-pure or global-pure
  turns): L1 = 5 turns (25 facts), L2 = 20 turns (+75 facts),
  L3 = 35 turns (+150 facts). One teaching session per level.
- At each level: END the teaching session; run the 12 frozen level
  probes in FRESH sessions (one session per probe); then resume
  teaching in a NEW session for the next level.
- Continuum: register all 5 projects up front; activate the batch's
  project before each project teaching turn; no activation for
  global batches. Probes activate the probe's frozen project field
  (single-project probes) or none (global/ambiguous/compare).
- Direct: identical messages, never activates.
- After L3 probes: FULL process restart (host/server stopped,
  confirmed gone), then 12 frozen restart probes in fresh sessions
  (Continuum re-registers? No — registry persists in data/;
  re-activate per probe as specified. Direct: fresh host/sessions).
- Total 108 turns/condition (60 teaching + 36 level probes + 12
  restart probes).

## 6. Value matcher and scoring (binary 1/0, deterministic)

Matcher (case-insensitive): a value MATCHES a reply iff the full
value string occurs OR every whitespace-separated token of the
value occurs. (Single-word values behave exactly like V1
substring.) ABSENT = no match.

- present: 1 iff every word in `words` matches.
- corrected: 1 iff `new` matches AND NOT ("not {new}" or
  "n't {new}" present, checked on the full new string).
  Mentioning `old` as superseded is allowed.
- present_absent: 1 iff every `present` matches AND every
  `absent` is absent.
- ab: 1 iff `va` matches within 80 chars AFTER the first
  occurrence of project name `a` (lowercased) AND `vb` within 80
  chars after first `b`. Token-relaxed: full value or all tokens
  inside the window.
- clarify: 1 iff reply contains "?" AND every `absent` candidate
  is absent.

## 7. Setup gating (scoreable vs INVALID)

A probe is scoreable ONLY if every teaching/content turn covering
its referenced fact(s) completed (terminal completed AND non-empty
reply AND no invalid flag AND no timeout). Otherwise INVALID /
NOT TAUGHT (excluded from numerator+denominator, listed).
- Test 1 probes: gated on the referenced fact turn + (for
  corrected) the correction turn, per frozen refs.
- Test 2 probes: gated on the teaching turn(s) containing their
  fids (frozen batch map) + corrections where applicable
  (Y-probes need the Y batch; Z-probes need Y and Z batches).
- Restart probes additionally require the restart marker ok.
Timeout on a PROBE turn: scored 0 AND flagged `timeout` (not a
clean recall miss). Timeout on a TEACHING turn: dependents INVALID.

## 8. Approval policy (identical both conditions)

Never silently wait. On approval/requested (or /request): record
approvalId/toolName/300-char subject, then decide:
- ALLOW ONCE iff every filesystem path referenced in the
  command/subject-target resolves under the condition's workspace
  root (normalizing JSON escapes and `\\?\` prefixes), or no path
  is referenced. The echoed workspaceRoot METADATA field itself is
  context, not a target, and is excluded from the check.
- Otherwise DENY (abort/reject choice); if the preferred side has
  no choice, take choices[0]; if no choices, flag INVALID.
On userInput/questions: record; answer first option per question
(or empty free text). Approval-RPC failure or unanswered-approval
end state => turn OPERATIONAL INVALID (not a recall result).

## 9. Turn integrity and records

Per turn: condition/test/session/turn-tag, prompt, raw reply,
started.turnId, done.turnId/terminal/reason/durationMs,
approvalIds+decisions, questions count, terminal/error,
timeout/cancelled flags, invalid reason, integrity_alert,
usage (V2-identical fields), first_visible_ms, turn_latency_ms,
tool-event count, md_count + agents_present (workspace snapshot),
ts. Integrity rule: done.turnId MUST equal started.turnId or the
turn is INVALID. Client timeout 200 s; on timeout the driver
stops/cancels that turn (recorded) and continues; no retries on
content, ever. Model string recorded per turn; mismatches flagged.

Workspace snapshots: per-turn md_count/agents_present; full
recursive file listing at Test1 checkpoints and Test2 level
boundaries (before teaching, after teaching, after probes).
Native-persistence observation (direct): first-appearance
turn/session of .agents/ or *.md notes, what triggered it if
determinable, reads, survival across sessions/restart. Observed
only — never encouraged or suppressed.

## 10. Resumability (no-retry preserving)

Drivers append to raw_turns.jsonl and SKIP already-recorded turn
tags (a timed-out/failed turn is recorded once and never resent).
Test 2 levels run as separate invocations; if interrupted
mid-level, remaining batches of that level continue in a new
teaching session (recorded). Test 1 SHOULD complete in one driver
invocation per condition (single session cannot span processes);
interruption ends Test 1 at a recorded boundary (see §11).

## 11. Stopping rules

Abort a condition ONLY on operational inability: host/server
won't start (after 1 identical restart attempt), 5 consecutive
transport failures, or Test 1 session death (record exact
boundary; score probes so far). NEVER for bad recall. Suspected
compaction (prompt-token drop >30% turn-over-turn with session
continuity) is recorded as an observed anomaly, not a stop.
Report every boundary exactly.

## 12. Analysis (measured curves, no extrapolation of primaries)

Test 1 per condition: mean prompt tok/turn for 1-10/11-50/51-100/
101-250; prompt tokens at checkpoints; cumulative prompt/total
tokens; cumulative cost; mean latency by interval; recall by age
bucket (recent/medium/old/corrected/indirect across checkpoints).
Test 2 per condition per level: mean prompt tok/probe, recall,
cost/probe; restart recall. Overheads = (continuum-direct)/direct
at each checkpoint/level + trend (grows/shrinks/stable); cost and
latency per correct recall. Report tables per the request.

## 13. Outputs (<suite-dir>/)

scale_protocol.md, scale_protocol.sha256, raw_turns.jsonl,
long_session_usage.csv, long_session_results.json,
memory_load_usage.csv, memory_load_results.json, scale_report.md,
plot data CSVs (+ static PNGs only if matplotlib is already
available — no new product/UI work).
