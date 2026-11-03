# Continuum experiments

What was measured, how, what was observed, and how to rerun it.
All live benchmarks below compare **Muse Code direct** against
**Continuum + Muse Code** with the same model, effort, approval
mode, prompts and machine. Nothing here is statistical proof:
every live run is a single controlled run.

Related: the in-repo 48-probe memory-architecture comparison
(S0/S1/S2, mock-keyword judge — not an LLM judge) lives in
`evals/` with its own [report](../evals/report.md).

## 1. Purpose and methodology

Each benchmark isolates one question: cross-session continuity
(V2), long-session cost growth (scale), post-fix validation
(longval), or the full 150-turn picture plus one Refresh Memory
intervention (final150). Drivers replay frozen synthetic prompts
turn-by-turn, record native usage (prompt/output/total tokens,
model-reported where available), latency, tool/approval events and
terminal states, then deterministic scorers grade frozen probes.

## 2. Model configuration (all live runs)

- Provider Meta, model `muse-spark-1.3-contributor` (host default;
  verified in the provider catalog before each run; no `set_model`
  calls in either condition).
- Reasoning effort: host default (no set calls).
- Approval mode promptUnmatched; unattended runs answer approvals
  deterministically: allow-once iff workspace-confined, else deny.
- Muse Code 1.4.14, Windows 10.0.19045, Python 3.12.10, one machine,
  sequential legs.
- Derived-cost basis (NOT billing): input $0.10 / output $0.20 per
  1M tokens, always labeled DERIVED.

## 3. Preregistration and deterministic scoring

Every benchmark froze its protocol (prompts, facts, probes, expected
answers, scoring, stopping rules) to disk with a SHA256 **before the
first model call**; no semantic edits after the start. Scoring is
binary 1/0 per probe by frozen substring rules (`present`,
`corrected`), executed by code — no LLM judge. Setup gating: a
recall probe is scoreable only if its teaching turn completed
cleanly, else INVALID / NOT TAUGHT (never silently rescored).
Timeouts and operational failures are recorded separately from
memory failures, and content failures are never retried.

## 4. Isolation requirements

Fresh synthetic workspaces and data per condition; no real user
projects, memories, transcripts or data. Continuum legs run an
unmodified file copy of the tested HEAD with an empty `data/`.
Project names are checked absent before use. File-operation
prompts are confined by the approval policy; residue sweeps after
each run removed only content- and time-validated synthetic files.

## 5. Commands / steps to reproduce

Common setup: check out this repo, install and authenticate Muse
Code, keep a loopback port free. Continuum legs need a pristine
tree copy (no `.git`, `data/`, caches) at the protocol's HEAD —
see [benchmarks/README.md](../benchmarks/README.md) for the exact
copy commands. Then, from each suite directory:

```sh
# v2 — Part A cost + Part B continuity (protocol: benchmarks/v2/)
python policy_selftest.py     # model-free policy check
python run_direct2.py         # live leg, ~25 turns
python run_continuum2.py      # live leg, needs the tree copy
python score2.py              # per_turn_usage_v2.csv + continuity_results_v2.json

# scale — long session (Test1) and memory load (Test2)
python gen_scale.py           # regenerates frozen inputs byte-identically
python scale_selftest.py      # model-free harness check
python run_scale_direct.py test1
python run_scale_continuum.py test1
python run_scale_direct.py test2 1   # …2, 3, R likewise, both drivers
python score_scale.py

# longval — 100-turn post-fix validation
python run_val_direct.py
python run_val_continuum.py
python score_val.py

# final150 — single unattended runner (both legs + scoring + report)
python run_final150.py --check   # model-free: HEAD, hashes, model, machinery
python run_final150.py           # the full run (~350 turns, hours)
```

The 48-probe harness: `python evals/run_eval.py [--system all|S0|S1|S2]`.

## 6. Expected output files

Per suite directory: `raw_*.jsonl` (every turn, prompt, reply,
usage, approvals, terminal state), `usage.csv`-style per-turn
tables, `recall_results.json`-style gated scores, approval logs
where applicable, and a `*_report.md`. Compare against §8; run to
run, expect small numeric shifts (model variance) with the same
shapes.

## 7. Protocol hashes (originals, pre-run)

| Benchmark | Protocol SHA256 |
|---|---|
| v2 | `380ACE23AA788C93868AD862BC0358C70FD8829517B97B814AF35B091B1F1133` |
| scale | `50611791204E377D434BC0EC15FAABD888072D2C352BCD5AFBE84D61A71E1FA7` |
| longval | `A5D5F62F5F5B92BD3577846376523F373D3CC05973F4EB79AA058789835E1059` |
| final150 | `6B96E72E683BF5B9C895E452BA3FFD35DC2DA103D40E3AEE6E9F4E9283D9973E` |

`benchmarks/` carries sanitized path-copies of these protocols plus
frozen inputs (`scale_test1.json` `CE659E62…06FF`,
`scale_test2.json` `E846D256…6BC32B7` — reproducible from
`gen_scale.py`).

## 8. Original observed results

### A. Cross-session/product benchmark V2 (HEAD d7c85a2)

Continuum **10/10** valid, Muse-direct **1/10** valid, 0 invalid
probes. Tested dimensions: global preference (B1), project memory
(B2), supersession/correction (B3), project isolation (B4),
cross-project global memory (B5), explicit A+B lookup (B6),
process/server restart (B7). Every Continuum probe traced to a
durable artifact (project `.continuum.md` records with preserved
supersession history, one global encyclopedia entry). Part A cost
(9 paired turns): +23.5% tokens, +23.6% derived cost, +4.5%
summed latency; one Continuum turn timed out after correct
out-of-workspace denials (explicit failure, session recovered).
Do NOT read 10/10 vs 1/10 as universal superiority: one
controlled run, and direct-Muse persistence varies run to run
(see E).

### B. Long-session scaling diagnosis (HEAD d7c85a2)

At scale turn 250, Continuum prompt tokens reached 551,636 vs
76,208 direct (7.24x) with identical recall. Diagnosis (read-only):
per-turn injection was small and bounded, but every injected block
was retained in native MSP history while a fresh block was injected
each turn — ~511k of 551k tokens (~99% of the gap) were accumulated
prior blocks. No transcript double-send, no project file in that
test, no runaway entry growth. This motivated the incremental
delivery fix (bootstrap once + deltas).

### C. Incremental-delivery validation (HEAD fe2408c, 100 turns)

Turn 100: direct 54,412 vs Continuum 55,233 prompt tokens
(ratio 1.015x, converging from 1.077x). Cumulative derived cost
through 100: direct $0.4397, Continuum $0.4525 (+2.9%). Recall
11/15 in BOTH conditions (5/5, 3/5, 3/5). Exactly 1 bootstrap,
deltas flat (~185 chars mean), 0 failures.

### D. Final 150-turn benchmark (HEAD 77ad19c)

Prompt-token ratio Continuum/direct: turn 10: 1.135x, 50: 1.088x,
100: 1.076x, 125: 1.092x, 150: 1.085x — flat, no divergence.
Sequence cost through 150: direct $0.7300, Continuum $0.7890
(+8.1% DERIVED). Recall identical both sides: 10: 5/5, 50: 3/5,
100: 3/5, 125: 2/5, 150: 2/5 (15/25 each) — long-session recall
matched direct and still degraded with context in both.
Refresh Memory at turn 100 (labeled intervention): before 3/5,
after 3/5 (identical replies), 10 items, 4,117 chars, ~1,029
injected tokens, $0.0059, 29.6s — completed correctly with no
demonstrated recall benefit on this probe set; experimental.
Zero timeouts/cancels/empties/invalids; zero approval events;
one bootstrap + flat deltas across 181 continuum turns.

### E. Run-to-run native Muse persistence (observation)

Muse-direct persistence varied between runs: in the V1 diagnostic
run it created native workspace memory notes and recalled
substantially more (7/10 continuity); in V2 it created no
workspace files and scored 1/10 (its single hit came from
Muse-internal memory of unknown store). Native global memory
files also appeared mid-run in long sessions. This variability
is reported as an observation about run-to-run behavior, not a
claim about all Muse behavior — and it is why every benchmark
above records what Muse actually did rather than assuming zero
or full memory.

## 9. Important caveats

- Single runs on one machine/provider snapshot; model behavior
  drifts over time. Shapes replicate better than exact digits.
- Synthetic short probes, substring judges: controlled
  architecture tests, not real-world memory benchmarks.
- Continuum legs add server pre-turn latency (time-to-first-token
  overhead is the stable latency signal).
- Never merge scores across runs (V1 diagnostic vs V2; old vs new
  builds are historical context, not paired comparisons).

## 10. Diagnostic vs public runs

- **Diagnostic only** (kept as evidence, never cited as results):
  V1 comparison (approval-blind driver), supersession scorer
  post-hoc analysis (kept separate from the frozen scorer).
- **Public benchmark runs**: V2 continuity, scale long-session +
  memory load, longval 100-turn validation, final150. Protocols,
  inputs, drivers and scorers ship under `benchmarks/`; raw logs
  stay out of the repo (size + machine-specific paths).
