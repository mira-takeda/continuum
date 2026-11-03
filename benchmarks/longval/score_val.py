"""Longval scoring: usage.csv + gated recall + delivery tallies."""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import val_common as common

BENCH = os.path.dirname(os.path.abspath(__file__))
T1 = json.load(open(os.path.join(BENCH, "scale_test1.json"), encoding="utf-8"))
recs = [json.loads(x) for x in open(os.path.join(BENCH, "raw_turns.jsonl"),
                                    encoding="utf-8")]
turns = [r for r in recs if "tag" in r]
markers = [r for r in recs if r.get("marker")]
TEST = "longval1"
CPS = (10, 50, 100)

USAGE_KEYS = ["condition", "test", "tag", "kind", "session", "prompt_tokens",
              "output_tokens", "total_tokens", "cost_derived_usd",
              "first_visible_ms", "turn_latency_ms", "model", "terminal",
              "error", "approvals", "questions", "tool_events", "timeout",
              "cancelled", "invalid", "md_count", "agents", "ts"]


def rows_for():
    rows = []
    for r in turns:
        if r.get("test") != TEST:
            continue
        u = r.get("usage") or {}
        rows.append({
            "condition": r["condition"], "test": TEST, "tag": r["tag"],
            "kind": r.get("kind"), "session": r.get("session"),
            "prompt_tokens": u.get("prompt_tokens"),
            "output_tokens": u.get("output_tokens"),
            "total_tokens": u.get("total_tokens"),
            "cost_derived_usd": u.get("cost_derived_usd"),
            "first_visible_ms": r.get("first_visible_ms"),
            "turn_latency_ms": r.get("turn_latency_ms"),
            "model": u.get("model"), "terminal": r.get("terminal"),
            "error": r.get("error"),
            "approvals": len(r.get("approvals") or []),
            "questions": r.get("questions_answered", 0),
            "tool_events": r.get("tool_events", 0),
            "timeout": bool(r.get("timeout")),
            "cancelled": bool(r.get("cancelled")),
            "invalid": r.get("invalid"),
            "md_count": r.get("md_count"), "agents": r.get("agents_present"),
            "ts": r.get("ts")})
    rows.sort(key=lambda r: (r["condition"], r["tag"]))
    return rows


def get(cond, tag):
    return next((r for r in turns if r["condition"] == cond
                 and r.get("test") == TEST and r["tag"] == tag), None)


def setup_ok(rec):
    return bool(rec and rec.get("terminal") == "completed"
                and (rec.get("reply") or "").strip()
                and not rec.get("invalid") and not rec.get("timeout"))


T1_CORR_TURN = {}
for cturn, c in T1["corrections"].items():
    T1_CORR_TURN[c["target"]] = int(cturn)


def t1_gate(probe):
    tags = [f"T1-{T1['facts'][probe['ref']]['turn']:04d}"]
    if probe["kind"] == "corrected":
        tags.append(f"T1-{T1_CORR_TURN[probe['ref']]:04d}")
    return tags


def score_probe(cond, probe, gate_tags):
    rec = get(cond, probe["id"])
    if rec is None:
        return {"score": "MISSING", "reason": "turn not recorded"}
    bad = [t for t in gate_tags if not setup_ok(get(cond, t))]
    if bad:
        return {"score": "INVALID", "reason": f"NOT-TAUGHT: {bad}",
                "reply": rec["reply"], "terminal": rec["terminal"]}
    if rec.get("invalid"):
        return {"score": "INVALID", "reason": rec["invalid"],
                "reply": rec["reply"], "terminal": rec["terminal"]}
    if rec.get("terminal") == "cancelled" and not rec.get("timeout"):
        return {"score": "INVALID", "reason": "spontaneous-cancel",
                "reply": rec["reply"], "terminal": rec["terminal"]}
    sc = common.score_rule(probe["expected"], rec["reply"])
    o = {"score": sc, "reply": rec["reply"], "terminal": rec["terminal"],
         "error": rec.get("error"), "kind": probe.get("kind")}
    if rec.get("timeout"):
        o["flag"] = "timeout"
    if rec.get("terminal") == "completed" and not (rec.get("reply") or "") \
            .strip():
        o["flag"] = "empty-completed"
    return o


def interval_stats(cond, lo, hi):
    sel = [r for r in turns if r["condition"] == cond and r.get("test") == TEST
           and r.get("kind") in ("fact", "correction", "fileop",
                                 "reference", "filler")
           and (r.get("usage") or {}).get("prompt_tokens") is not None
           and lo <= int(r["tag"].split("-")[1]) <= hi]
    pt = [(r["usage"] or {})["prompt_tokens"] for r in sel]
    lat = [r["turn_latency_ms"] for r in sel
           if r.get("turn_latency_ms") is not None]
    return {"n": len(sel),
            "mean_prompt": round(statistics.mean(pt), 1) if pt else None,
            "mean_latency_ms": round(statistics.mean(lat), 1) if lat else None}


def compaction_flags(cond):
    sel = sorted([r for r in turns if r["condition"] == cond
                  and r.get("test") == TEST and r.get("kind") != "probe"
                  and (r.get("usage") or {}).get("prompt_tokens") is not None],
                 key=lambda r: r["tag"])
    out = []
    for a, b in zip(sel, sel[1:]):
        pa = a["usage"]["prompt_tokens"]
        pb = b["usage"]["prompt_tokens"]
        if pa > 0 and (pa - pb) / pa > 0.30:
            out.append({"after": a["tag"], "at": b["tag"],
                        "from": pa, "to": pb})
    return out


def delivery_tally(cond):
    sel = sorted([r for r in turns if r["condition"] == cond
                  and r.get("test") == TEST], key=lambda r: r["tag"])
    modes = {}
    added = []
    bootstraps = []
    fallbacks = 0
    sids = set()
    for r in sel:
        d = r.get("delivery") or {}
        m = d.get("mode", "none")
        modes[m] = modes.get(m, 0) + 1
        if isinstance(d.get("added_chars"), int):
            added.append(d["added_chars"])
        if m == "bootstrap":
            bootstraps.append(r["tag"])
        fallbacks += len(r.get("delivery_fallbacks") or [])
        if r.get("native_sid"):
            sids.add(r["native_sid"])
    return {"modes": modes, "bootstraps": bootstraps,
            "bootstrap_count": len(bootstraps),
            "mean_added_chars": round(statistics.mean(added), 1) if added else None,
            "max_added_chars": max(added) if added else None,
            "fallback_events": fallbacks, "native_sids": sorted(sids)}


common.write_csv(os.path.join(BENCH, "usage.csv"), rows_for(), USAGE_KEYS)
res = {}
for cond in ("direct", "continuum"):
    res[cond] = {}
    for p in T1["probes"]:
        if p["after"] > 100:
            continue
        res[cond][p["id"]] = score_probe(cond, p, t1_gate(p))
    valid = [v for v in res[cond].values() if v["score"] != "INVALID"
             and v["score"] != "MISSING"]
    res[cond]["TOTAL"] = \
        f"{sum(v['score'] for v in valid)}/{len(valid)} valid"
    res[cond]["_invalid"] = [k for k, v in res[cond].items()
                             if isinstance(v, dict)
                             and v["score"] == "INVALID"]
    res[cond]["_by_checkpoint"] = {}
    for cp in CPS:
        ps = [v for k, v in res[cond].items()
              if k.startswith(f"T1-P{cp}-") and v["score"] != "INVALID"
              and v["score"] != "MISSING"]
        res[cond]["_by_checkpoint"][str(cp)] = \
            f"{sum(v['score'] for v in ps)}/{len(ps)}"
    res[cond]["_by_kind"] = {}
    for kind in ("recent", "medium", "old", "corrected", "indirect"):
        ps = [v for v in res[cond].values()
              if isinstance(v, dict) and v.get("kind") == kind
              and v["score"] not in ("INVALID", "MISSING")]
        res[cond]["_by_kind"][kind] = \
            f"{sum(v['score'] for v in ps)}/{len(ps)}" if ps else "n/a"
    res[cond]["_intervals"] = {}
    for lo, hi in ((1, 10), (11, 50), (51, 100)):
        res[cond]["_intervals"][f"{lo}-{hi}"] = interval_stats(cond, lo, hi)
    sel = [r for r in turns if r["condition"] == cond
           and r.get("test") == TEST]
    res[cond]["_cumulative"] = {
        "prompt": sum((r["usage"] or {}).get("prompt_tokens") or 0
                      for r in sel),
        "total": sum((r["usage"] or {}).get("total_tokens") or 0
                     for r in sel),
        "cost": round(sum((r["usage"] or {}).get("cost_derived_usd") or 0
                          for r in sel), 6),
        "n_turns": len(sel)}
    res[cond]["_compaction_flags"] = compaction_flags(cond)
    if cond == "continuum":
        res[cond]["_delivery"] = delivery_tally(cond)
common.save_json(os.path.join(BENCH, "recall_results.json"), res)

for cond in ("direct", "continuum"):
    print(f"{cond}: {res[cond]['TOTAL']} invalid={res[cond]['_invalid']}")
    print(f"  checkpoints={res[cond]['_by_checkpoint']}")
    print(f"  kinds={res[cond]['_by_kind']}")
    if cond == "continuum":
        print(f"  delivery={res[cond]['_delivery']}")
