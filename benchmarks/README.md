# Public benchmark reproduction assets

Sanitized, machine-independent copies of the protocols, frozen inputs,
drivers, scorers and selftests behind the numbers in
[docs/EXPERIMENTS.md](../docs/EXPERIMENTS.md). Absolute developer paths
were replaced with repo-relative resolution; synthetic content is
byte-identical to the frozen originals (hashes below and in each
sanitized protocol header).

## What is included, and what is not

| Suite | Contents | Needs live model |
|---|---|---|
| `v2/` | protocol, frozen policy, both drivers, scorer, policy selftest | yes for legs |
| `scale/` | protocol, generator, frozen inputs, policy, both drivers, scorer, selftest | yes for legs |
| `longval/` | protocol, frozen inputs, policy, both drivers, scorer | yes for legs |
| `final150/` | protocol, probes, inputs, policy, both drivers, single runner, scorer | yes for legs |

Included: protocols (sanitized paths), frozen synthetic inputs,
approval-policy code, benchmark drivers, deterministic scorers,
model-free selftests, the seeded input generator.

NOT included (deliberately): raw turn logs and usage CSVs (bulky,
machine-specific), server tree copies, benchmark workspaces, leg
logs, local transcripts, credentials (there never were any in these
files — verified by scan; the only "password" strings are the frozen
synthetic vocabulary pair "password book").

The in-repo 48-probe memory comparison (`evals/run_eval.py`,
S0/S1/S2) is separate and documented in `evals/report.md`.

## Requirements for a live rerun

- This repository checked out; commands run from the suite directory.
- Muse Code installed and authenticated, same provider/model path the
  protocol names (original runs: Meta `muse-spark-1.3-contributor`,
  host defaults, approval mode promptUnmatched).
- Python 3.10+ (stdlib only for harnesses).
- A free loopback port per suite (v2: 8161; scale/longval/final150:
  see driver `PORT`).
- The drivers were developed and run on Windows; paths are
  `os.path`-relative but shell tool approval behavior is
  platform-dependent (see the Windows sandbox note in EXPERIMENTS.md).
- Live reruns consume model calls and take tens of minutes to hours.

## Model-free checks (no model calls)

From each suite directory unless noted:

```sh
# v2 approval policy behavior
python policy_selftest.py
# scale: regenerate frozen inputs byte-identically, then selftest
python gen_scale.py        # rewrites scale_test1/2.json deterministically
python scale_selftest.py
# final150: full precondition + machinery check
python run_final150.py --check
```

Expected frozen input hashes: `scale_test1.json`
`CE659E62FA99C13F21E19FD749ED9457DB5662B0C6A0A9AD8827662E932706FF`,
`scale_test2.json`
`E846D2561757CF6EE987C33CA648A78C0563E0433204FB865102164FE6BC32B7`.

## Live reruns (each step appends evidence; legs are single-session)

Continuum legs need a pristine copy of this repo tree (no `.git`,
`data/`, caches) at the HEAD the protocol names. From the repo root,
e.g. on Windows:

```powershell
robocopy . benchmarks\v2\continuum-copy-v2 /E /XD .git data __pycache__ .venv /XF *.pyc
```

(Replace `v2\continuum-copy-v2` with the copy dir each driver names:
scale `scale-test1\continuum-copy` / `scale-test2\…`, longval
`lv-continuum-copy`, final150 `final150-continuum-copy`. Check out the
protocol's HEAD first if you want the exact original build.)

Then, from the suite directory:

```sh
# v2 (Part A cost + Part B continuity)
python run_direct2.py
python run_continuum2.py
python score2.py
# scale Test1 (long session) — Test2 legs take <1|2|3|R> args, see protocol
python run_scale_direct.py test1
python run_scale_continuum.py test1
python score_scale.py
# longval (100-turn validation)
python run_val_direct.py
python run_val_continuum.py
python score_val.py
# final150 (single unattended runner does both legs + scoring + report)
python run_final150.py
```

Scorers write CSV/JSON results next to the drivers (`usage.csv` +
`recall_results.json` for longval/final150, `long_session_*` +
`memory_load_*` for scale, `per_turn_usage_v2.csv` +
`continuity_results_v2.json` for v2). Compare against the observed
numbers in [docs/EXPERIMENTS.md](../docs/EXPERIMENTS.md); run-to-run
model variance applies — see the caveats there.
