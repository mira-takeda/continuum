"""POST-HOC supersession re-analysis. NOT preregistered, NOT the frozen scorer.

Reads evals/results.json + evals/tasks/corrections.json read-only and prints:
  1. recomputed FROZEN sup values (sanity: must match results.json exactly);
  2. CORRECTED sup values (citation-stripped + correction-marker aware,
     reusing exactly the frozen mock_judge marker list — no new tuning);
  3. the manual audit verdict per probe (explicit table below).

Frozen files are never modified. Run:
  python evals/posthoc_supersede.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Frozen marker list copied from run_eval.py mock_judge (do not extend here;
# extending it post-hoc to reach a nicer number would be outcome engineering).
MARKERS = ["not ", "n't ", "previously", "correction", "instead", "old ",
           "dropped", "no longer", "misremembered", "wrong", "changed",
           "moved to", "now "]


def frozen_sup(answer, new, old):
    a = (answer or "").lower()
    return 1 if (new.lower() in a and old.lower() not in a) else 0


def corrected_sup(answer, new, old):
    a = re.sub(r"\[[^\]]*\]", "", answer or "").lower()
    if new.lower() not in a:
        return 0
    if old.lower() not in a:
        return 1
    return 1 if any(m in a for m in MARKERS) else 0


# Human audit annotations: (verdict, reason). These are human judgments from
# reading history + answer, NOT an automatic measurement and NOT a third
# "manual scorer" - do not present counts over them as a score.
MANUAL = {
    # corr-1: "Casa Linda, not Casa Loma" — textbook corrective answer.
    ("S1", "corr-1"): ("scorer-false-negative", "corrective 'not X' phrasing"),
    # Caveat: the S2 answer opens with an incorrect uncertainty statement
    # ("I don't have a restaurant choice recorded") before giving the
    # correct updated answer.
    ("S2", "corr-1"): ("scorer-false-negative",
                       "corrective 'not X' phrasing; opens with incorrect uncertainty"),
    # corr-2: probe asks "what did we DROP?" — naming PyTorch is required.
    ("S1", "corr-2"): ("scorer-false-negative", "probe demands naming old value"),
    ("S2", "corr-2"): ("scorer-false-negative", "probe demands naming old value"),
    # corr-3: "not lactose intolerant anymore" — must name it to negate it.
    ("S1", "corr-3"): ("scorer-false-negative", "negation must name the condition"),
    ("S2", "corr-3"): ("scorer-false-negative", "negation must name the condition"),
    ("S1", "corr-4"): ("pass", "clean new-value answer"),
    ("S2", "corr-4"): ("pass", "clean new-value answer"),
    ("S1", "corr-5"): ("pass", "clean new-value answer"),
    # corr-5 S2: "yuki" only inside the citation token of the correction entry.
    ("S2", "corr-5"): ("scorer-false-negative", "old value only in citation id"),
    # corr-6: history says "born Bilbao, GREW UP in Madrid" — Madrid is true.
    ("S1", "corr-6"): ("scorer-false-negative", "old value restated as still-true fact"),
    ("S2", "corr-6"): ("scorer-false-negative", "old value restated as still-true fact"),
    ("S1", "corr-7"): ("pass", "clean new-value answer"),
    ("S2", "corr-7"): ("pass", "clean new-value answer"),
    # corr-8: "wet mixing failed/turned muddy" — old method cited as failed.
    ("S1", "corr-8"): ("scorer-false-negative", "old value cited only as failed"),
    ("S2", "corr-8"): ("scorer-false-negative", "old value cited only as failed"),
}


def main():
    tasks = json.load(open(os.path.join(ROOT, "evals", "tasks", "corrections.json"),
                           encoding="utf-8"))
    res = json.load(open(os.path.join(ROOT, "evals", "results.json"),
                         encoding="utf-8"))
    sup = {p["probe"]: p["supersede"] for p in tasks["probes"]}
    probes = [(p["probe"], p["supersede"]["new"], p["supersede"]["old"])
              for p in tasks["probes"]]
    print("POST-HOC supersession analysis (frozen results.json untouched)\n")
    mismatch = 0
    for sys in ("S1", "S2"):
        recs = [r for r in res["results"][sys] if r.get("category") == "corrections"]
        assert len(recs) == 8, sys
        rows = []
        for i, r in enumerate(recs):
            _, new, old = probes[i]
            f = frozen_sup(r["answer"], new, old)
            if f != r["supersede_ok"]:
                mismatch += 1
                print(f"MISMATCH {sys} {r['id']}: recomputed={f} "
                      f"frozen={r['supersede_ok']}")
            c = corrected_sup(r["answer"], new, old)
            v, reason = MANUAL[(sys, r["id"])]
            rows.append((r["id"], r["score"], r["supersede_ok"], c, v, reason))
        print(f"== {sys}: frozen sup "
              f"{sum(x[2] for x in rows)}/8 | corrected sup "
              f"{sum(x[3] for x in rows)}/8")
        for pid, score, f, c, v, reason in rows:
            print(f"  {pid}: score={score} frozen_sup={f} corrected_sup={c} "
                  f"manual={v} ({reason})")
        print()
    if mismatch:
        print(f"FROZEN MISMATCHES: {mismatch} (investigate before trusting this)")
        return 1
    print("frozen recomputation matches results.json on all 16 records.")
    print("No substantive supersession failures were found in manual review:")
    print("all 16 answers give the correct updated value. Caveat: S2 corr-1")
    print("opens with an incorrect uncertainty statement before the right answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
