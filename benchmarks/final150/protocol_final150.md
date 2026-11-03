> Sanitized public copy. Machine paths were replaced with <suite-dir> / <repo> placeholders. The original pre-run protocol SHA256 was `6B96E72E683BF5B9C895E452BA3FFD35DC2DA103D40E3AEE6E9F4E9283D9973E`.

# FINAL 150-TURN LONG-SESSION BENCHMARK — preregistered protocol

Separate benchmark. NOT V1/V2/scale/long-validation. Those artifacts
are never modified. Frozen BEFORE the first benchmark model call.
No semantic prompt/scoring/procedure edits after the run begins.
Local run on this Windows machine only. No Modal. Unattended:
no human approval clicks; the frozen auto policy decides.

PRODUCT BUILD: branch release/audit-fixes, HEAD
77ad19c58be43b41f9d2fcde9cc38f1fb5df9b99 (unmodified file copy).
No product changes, tuning, commits, push, or ZIP.

Reference SHAs: scale protocol 50611791…A7FA7; longval protocol
A5D5F62F…E1059. scale_test1.json CE659E62…06FF (read-only).

## 1. Goal

Compare Muse-direct vs Continuum+MSP over 150 continuous turns:
prompt/context tokens, cumulative tokens, cost, latency, recall
degradation, and the cost/effect of one Continuum Refresh Memory
at turn 100.

## 2. Configuration (recorded pre-run)

- Muse Code 1.4.14 (1.4.14-R7309.1); provider meta
- model muse-spark-1.3-contributor (host default, catalog-verified
  pre-run; no set_model calls either side)
- reasoning effort: host default (no set calls)
- approval mode promptUnmatched, both conditions; unattended auto
  policy = frozen scale §8 code (byte-identical copy): allow-once
  iff every referenced path resolves under the condition
  workspace root (workspaceRoot metadata excluded) or no path
  referenced; else deny; unclassifiable => deny. Every request
  and decision recorded. Permissions never broadened to finish.
- OS / Python: Windows 10.0.19045 / Python 3.12.10
- Same machine, sequential (direct first, then continuum).
- Derived-cost basis (not billing): input $0.10 / output $0.20
  per 1M tokens, labeled DERIVED.

## 3. Conditions and isolation

- DIRECT: raw MSP turns (msp.MSPHost + turn.stream_turn/summarize
  imported read-only from the product tree). One fresh sid for
  the whole leg; never resumed.
- CONTINUUM: file copy of the product tree at 77ad19c over HTTP
  (browser-equivalent path), port 8169. One fresh chat session.
  No projects registered (same as scale Test1/longval).
- Same NL user prompts turn-for-turn.
- Isolated environments under
  <suite-dir>/:
  final150-direct-ws/ and final150-continuum-copy/ (+ its data/).
  No real user data/projects/memories.

## 4. Frozen inputs

- Content turns 1..150: scale_test1.json turns n = 1..150 reused
  VERBATIM, in order (turns 101-150: 36 filler, 10 fact, 2 fileop,
  1 correction @118, 1 reference).
- Checkpoints 10/50/100: the 15 frozen probes reused VERBATIM.
- Checkpoints 125/150: 10 NEW probes (final150_probes.json, text
  frozen below), authored to mirror the frozen generator's own
  late-checkpoint pattern (cf. frozen P250: recent age 0, medium
  ~half back, old always F001, corrected mid-range, indirect
  paraphrase ~20-100 back; repeated question patterns and
  attribute reuse are established generator behavior):
  - T1-P125-recent (F028, age 0): "What is our planter soil?
    Answer briefly in one sentence." present [level]
  - T1-P125-medium (F016, age 60): "What is our tool brand?
    Answer briefly in one sentence." present [maroon]
  - T1-P125-old (F001, age 123): "What is our shed paint?
    Answer briefly in one sentence." present [charcoal]
  - T1-P125-corrected (F013, corr 83): "What is our current gate
    lock? Answer briefly in one sentence." corrected new hickory
    (retention retest of the P100-corrected pair)
  - T1-P125-indirect (F024, age 20): "What is our garden walkway
    made of? Answer briefly in one sentence." present [azure]
  - T1-P150-recent (F033, age 0): "What is our gate lock?
    Answer briefly in one sentence." present [leather]
  - T1-P150-medium (F019, age 70): "What is our hose length?
    Answer briefly in one sentence." present [walnut]
  - T1-P150-old (F001, age 148): "What is our shed paint?
    Answer briefly in one sentence." present [charcoal]
  - T1-P150-corrected (F006, corr 33): "What is our current tool
    brand? Answer briefly in one sentence." corrected new cork
    (retention retest of the P50-corrected pair)
  - T1-P150-indirect (F029, age 20): "How long is our garden
    hose? Answer briefly in one sentence." present [hammer]
- Deliberately avoided: lamp-timer probes (F010/F020/F030 give
  three unlinked values moss/granite/sparrow for one attribute)
  and bench-finish F027 (turn-120 text says beans; its frozen
  value field reflects the turn-153 correction outside this run).
- Scoring rules: frozen scale §6 present/corrected, executed by
  the byte-identical common-code copy. Gating: frozen scale §7
  (fact turn + correction turn must be clean, else INVALID /
  NOT TAUGHT). Probe timeout => scored 0 + flagged.

## 5. Refresh intervention (Continuum only, labeled)

At turn 100, AFTER the 5 normal probes:
1. Score the 5 probes (BEFORE).
2. Call the real product POST /api/memory/refresh once.
3. Record items/chars/tokens/prompt-tokens/cost/latency/mode/sid.
4. Immediately re-ask the SAME 5 probe texts (tags suffixed -R2).
5. Score them (AFTER) with identical rules/gating.
6. Continue normally to 150. No second refresh.
Direct receives no intervention. The 5 extra R2 turns are part
of the labeled intervention cost.

## 6. Procedure

One continuous session per condition (tag "fmain"). Content
turns T1-0001..T1-0150 in order; after turns 10/50/100/125/150
insert that checkpoint's 5 probes, then continue the SAME
session. Totals: direct 175 turns; continuum 175 + 1 refresh +
5 R2 = 181 records. Per-turn records mirror longval (usage,
latency, tools, approvals, integrity, delivery stats/sid for
Continuum). Client timeout 200 s, stop/cancel and continue, no
content retries ever. Integrity: done.turnId must equal started
or INVALID. One invocation per leg; interruption ends the leg at
a recorded boundary (abort only on operational inability, never
for bad recall). >30% prompt drops recorded as anomalies.

## 7. Fileop residue note

Frozen fileop prompts use relative bench-files-scale/ paths
(turns 24/49/74/99/124/149 form read/append chains). Any files
created outside the isolated workspaces are logged as residue
and removed after raw evidence is preserved. Synthetic content
only.

## 8. Analysis

Prompt tokens at 1/10/25/50/75/100/125/150 both conditions +
ratio + Continuum-added chars; growth/turn for 1-10/11-50/
51-100/101-150; cumulative prompt/cost thru 10/50/100/150;
recall at 10/50/100/125/150 by category; refresh BEFORE/AFTER
at 100 with token/cost/latency overhead; persistence of any
refresh effect at 125/150; totals + cost per correct recall;
approval counts (total/allow/deny/unclassifiable-deny);
failures (timeouts/cancels/empties/invalid/strays/delivery);
old-build context (preserved numbers only, never rerun).

## 9. Outputs (<suite-dir>/)

final150_protocol.md, final150_protocol.sha256,
final150_probes.json, val_common.py (byte copy), run_f150_direct.py,
run_f150_continuum.py, score_f150.py, raw_turns.jsonl, usage.csv,
recall_results.json, approval_log.json, final150_report.md
