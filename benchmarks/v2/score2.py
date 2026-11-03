"""V2 scoring: usage CSV + setup-gated continuity. Reads raw_results_v2.jsonl."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common2 as common

BENCH = os.path.dirname(os.path.abspath(__file__))
recs = [json.loads(x) for x in open(os.path.join(BENCH, "raw_results_v2.jsonl"),
                                    encoding="utf-8")]
turns = [r for r in recs if "turn" in r]
markers = [r for r in recs if r.get("marker")]

# per-turn usage rows
rows = []
for r in turns:
    u = r.get("usage") or {}
    rows.append({
        "condition": r["condition"], "phase": r["phase"], "turn": r["turn"],
        "session": r.get("session"), "prompt_tokens": u.get("prompt_tokens"),
        "output_tokens": u.get("output_tokens"),
        "total_tokens": u.get("total_tokens"),
        "cost_derived_usd": u.get("cost_derived_usd"),
        "first_visible_ms": r.get("first_visible_ms"),
        "turn_latency_ms": r.get("turn_latency_ms"),
        "model": u.get("model"), "terminal": r.get("terminal"),
        "error": r.get("error"), "approvals": len(r.get("approvals") or []),
        "timeout": bool(r.get("timeout")),
        "cancelled": bool(r.get("cancelled")),
        "invalid": r.get("invalid"), "ts": r.get("ts")})
common.write_csv(os.path.join(BENCH, "per_turn_usage_v2.csv"), rows)
print(f"usage rows: {len(rows)}")

# setup gating
SETUP_OF = {
    "B1": ["B1teach"],
    "B2color": ["B2setup1", "B2setup2"],
    "B2face": ["B2setup1", "B2setup2"],
    "B3": ["B2setup1", "B2setup2", "B3corr"],
    "B4": ["B2setup1", "B2setup2", "B3corr"],
    "B5": ["B1teach"],
    "B6": ["B2setup1", "B2setup2", "B3corr", "B4setup"],
    "B7a": ["B1teach"],
    "B7b": ["B2setup1", "B2setup2", "B3corr"],
    "B7c": ["B4setup"],
}
PROBES = {"B1": "B1", "B2": ["B2color", "B2face"], "B3": "B3", "B4": "B4",
          "B5": "B5", "B6": "B6", "B7a": "B7a", "B7b": "B7b", "B7c": "B7c"}


def get(cond, phase, tag):
    return next((r for r in turns if r["condition"] == cond
                 and r["phase"] == phase and r["turn"] == tag), None)


def setup_ok(rec):
    return bool(rec and rec.get("terminal") == "completed"
                and (rec.get("reply") or "").strip()
                and not rec.get("invalid") and not rec.get("timeout"))


out = {}
for cond in ("direct", "continuum"):
    out[cond] = {}
    restart_ok = any(m.get("condition") == cond and m.get("ok")
                     for m in markers)
    out[cond]["_restart_ok"] = restart_ok
    for tag, keys in PROBES.items():
        rec = get(cond, "B", tag)
        assert rec, (cond, tag)
        keys = [keys] if isinstance(keys, str) else keys
        for k in keys:
            deps = SETUP_OF[k]
            if k.startswith("B7") and not restart_ok:
                out[cond][k] = {"score": "INVALID",
                                "reason": "restart-failed",
                                "reply": rec["reply"],
                                "terminal": rec["terminal"]}
                continue
            bad = [d for d in deps
                   if not setup_ok(get(cond, "B", d))]
            if bad:
                out[cond][k] = {"score": "INVALID",
                                "reason": f"NOT-TAUGHT: {bad}",
                                "reply": rec["reply"],
                                "terminal": rec["terminal"]}
                continue
            if rec.get("invalid"):
                out[cond][k] = {"score": "INVALID",
                                "reason": rec["invalid"],
                                "reply": rec["reply"],
                                "terminal": rec["terminal"]}
                continue
            if rec.get("terminal") == "cancelled" and not rec.get("timeout"):
                out[cond][k] = {"score": "INVALID",
                                "reason": "spontaneous-cancel",
                                "reply": rec["reply"],
                                "terminal": rec["terminal"]}
                continue
            sc = common.SCORERS[k](rec["reply"])
            o = {"score": sc, "reply": rec["reply"],
                 "terminal": rec["terminal"], "error": rec.get("error")}
            if rec.get("timeout"):
                o["flag"] = "timeout-scored-0" if sc == 0 else \
                    "timeout-but-rule-matched"
            if rec.get("terminal") == "completed" and not (
                    rec.get("reply") or "").strip():
                o["flag"] = "empty-completed"
            out[cond][k] = o
    valid = [v for k, v in out[cond].items()
             if not k.startswith("_") and v["score"] != "INVALID"]
    tot = sum(v["score"] for v in valid)
    out[cond]["TOTAL"] = f"{tot}/{len(valid)} valid"
    out[cond]["_invalid"] = [k for k, v in out[cond].items()
                             if not k.startswith("_") and k != "TOTAL"
                             and v["score"] == "INVALID"]
common.save_json(os.path.join(BENCH, "continuity_results_v2.json"), out)
for cond in ("direct", "continuum"):
    print(f"== {cond}: {out[cond]['TOTAL']} invalid={out[cond]['_invalid']}")
    for k, v in out[cond].items():
        if k.startswith("_") or k == "TOTAL":
            continue
        print(f"  {k}: {v['score']} term={v.get('terminal')} "
              f"reason={v.get('reason', '')} flag={v.get('flag', '')} "
              f"reply={str(v.get('reply'))[:90]!r}")
