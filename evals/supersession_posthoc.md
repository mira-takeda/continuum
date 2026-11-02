# Supersession metric: post-hoc audit (NOT preregistered)

Status: **post-hoc analysis**. The frozen scores in `results.json`
(harness `a906024`) are preserved exactly and remain the preregistered
result. This document only explains why the frozen supersession metric
under-reports both systems, and what a corrected reading looks like.
No product behavior was changed based on this audit.

Regenerate the numbers (read-only, never touches frozen files):

```sh
python evals/posthoc_supersede.py
```

## The scoring defect

Frozen rule (`run_eval.py`):

```python
sup = 1 if (new in answer and old not in answer) else 0
```

It requires the new value present **and the old value textually absent**.
That punishes fully correct corrective answers:

- `"We settled on Casa Linda, not Casa Loma"` → sup 0 (old named to negate it).
- `"We chose JAX and dropped PyTorch"` → sup 0, even though probe corr-2
  explicitly asks *"what did we drop?"* — fully answering the probe
  guarantees sup 0. The probe and the metric contradict each other.
- `"You are not lactose intolerant anymore"` → sup 0: key `new="not"` /
  `old="lactose intolerant"` demands "not" present while "lactose
  intolerant" is absent, which the natural correct answer cannot satisfy
  (it must name the condition to negate it).
- Citation contamination (S2 corr-5): the answer text is clean
  (`"Your sister's name is Aiko"`) but the old value `yuki` appears
  inside the citation token `[…-not-yuki-correc]` — the system cited
  exactly the correction entry and was punished for it. This one also
  zeroes the overall probe score, not just sup.
- Still-true restatement (corr-6, both systems): history says
  *"born in Bilbao, grew up in Madrid"* — Madrid is TRUE as the
  grown-up place, and both answers repeat exactly that. Both sup and
  the overall score are 0 for a fully correct answer.

Note the irony: the overall judge (`mock_judge`) already forgives stale
mentions next to correction markers ("not ", "dropped", …) — only the
sup metric lacks that forgiveness.

## Per-probe audit (all 8 corrections probes × S1/S2)

| id | probe | S1 answer (short) | S1 score/sup | S2 answer (short) | S2 score/sup | verdict |
|----|-------|-------------------|--------------|-------------------|--------------|---------|
| corr-1 | restaurant? | "Casa Linda, not Casa Loma" | 1.0 / 0 | "…Casa Linda, not Casa Loma" (hedged open) | 1.0 / 0 | scorer false negative ×2 |
| corr-2 | framework + dropped? | "chose JAX and dropped PyTorch" | 1.0 / 0 | same | 1.0 / 0 | scorer false negative ×2 (probe contradicts metric) |
| corr-3 | lactose intolerant? | "not lactose intolerant anymore" | 1.0 / 0 | same | 1.0 / 0 | scorer false negative ×2 (key unsatisfiable) |
| corr-4 | dentist? | "on Friday" | 1.0 / 1 | same | 1.0 / 1 | pass ×2 |
| corr-5 | sister's name? | "Aiko" | 1.0 / 1 | "Aiko" + citation containing "yuki" | 0.0 / 0 | S1 pass; S2 scorer false negative (citation) |
| corr-6 | born where? | "born Bilbao, grew up Madrid" | 0.0 / 0 | same | 0.0 / 0 | scorer false negative ×2 (true restatement; score also 0) |
| corr-7 | deep work? | "at night" | 1.0 / 1 | same | 1.0 / 1 | pass ×2 |
| corr-8 | mix stems? | "dry — wet mixing failed" | 1.0 / 0 | "dry — wet mixing turned muddy" | 1.0 / 0 | scorer false negative ×2 (old cited as failed) |

Full answer texts are in `results.json` (`results.S1/S2`, category
`corrections`); histories in `tasks/corrections.json`.

Observation (not a failure): S2 corr-1 opens with a hedge
("I don't have a restaurant choice recorded") before giving the right
answer. Retrieval still surfaced the correct entry; sup does not test
hedging, so this stays a scorer false negative.

## Corrected reading

Corrected scorer (`posthoc_supersede.py`, separate file): strip
`[…]` citations, then require new-value present and old-value absent
**unless a correction marker** from the frozen `mock_judge` list
("not ", "dropped", …) is present. The marker list is reused exactly —
not extended post-hoc to reach a nicer number.

| system | frozen sup (preregistered) | corrected sup (post-hoc) | manual correct |
|--------|---------------------------|--------------------------|----------------|
| S1 | 3/8 = 0.375 | 6/8 = 0.75 | 8/8 |
| S2 | 2/8 = 0.25 | 6/8 = 0.75 | 8/8 |

The frozen S1-over-S2 sup gap (one probe: corr-5 citation
contamination) is itself an artifact; under the corrected reading both
systems tie at 6/8 mechanical and 8/8 manual.

Two probes (corr-6, corr-8) defeat even the corrected mechanical
scorer: no substring rule can tell "Madrid as superseded birthplace"
from "Madrid as current grown-up place", or "wet" as current method
from "wet" as failed method. Only history-aware human reading resolves
them — which is why the manual column exists and why it is labeled
manual.

## Genuine remaining failures

None. All 11 frozen sup failures (5 S1 + 6 S2) are scorer artifacts.
Ambiguous: none.

## What this benchmark is (and is not)

- The 48 probes are **synthetic and short** (2-turn scripted histories,
  single-fact probes).
- The frozen judge is **mock-keywords / substring based**, not an LLM
  judge (`judge: "mock-keywords"` on every record).
- This is a **controlled architecture test** (no-memory vs flat-injected
  vs encyclopedia under one shared runtime), not a comprehensive
  real-world memory benchmark. Its value is the comparison under
  identical conditions — context growth, citation behavior, retrieval
  precision — not the absolute percentages.
