"""Scale scoring: usage CSVs + gated results + analysis."""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scale_common as common

BENCH = os.path.dirname(os.path.abspath(__file__))
T1 = json.load(open(os.path.join(BENCH, "scale_test1.json"), encoding="utf-8"))
T2 = json.load(open(os.path.join(BENCH, "scale_test2.json"), encoding="utf-8"))
recs = [json.loads(x) for x in open(os.path.join(BENCH, "raw_turns.jsonl"),
                                    encoding="utf-8")]
turns = [r for r in recs if "tag" in r]
markers = [r for r in recs if r.get("marker")]

USAGE_KEYS = ["condition", "test", "tag", "kind", "session", "prompt_tokens",
              "output_tokens", "total_tokens", "cost_derived_usd",
              "first_visible_ms", "turn_latency_ms", "model", "terminal",
              "error", "approvals", "questions", "tool_events", "timeout",
              "cancelled", "invalid", "md_count", "agents", "ts"]


def rows_for(test):
    rows = []
    for r in turns:
        if r.get("test") != test:
            continue
        u = r.get("usage") or {}
        rows.append({
            "condition": r["condition"], "test": test, "tag": r["tag"],
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


def get(cond, test, tag):
    return next((r for r in turns if r["condition"] == cond
                 and r.get("test") == test and r["tag"] == tag), None)


def setup_ok(rec):
    return bool(rec and rec.get("terminal") == "completed"
                and (rec.get("reply") or "").strip()
                and not rec.get("invalid") and not rec.get("timeout"))


# ---- gating maps ----
T1_CORR_TURN = {}  # fid -> correction turn (<= after handled per probe)
for cturn, c in T1["corrections"].items():
    T1_CORR_TURN[c["target"]] = int(cturn)


def t1_gate(probe):
    tags = [f"T1-{T1['facts'][probe['ref']]['turn']:04d}"]
    if probe["kind"] == "corrected":
        tags.append(f"T1-{T1_CORR_TURN[probe['ref']]:04d}")
    return tags


T2_BATCH_SEQ = {b["batch"]: b["seq"] for b in T2["teaching"]}
T2_FID_BATCH = {}
for b in T2["teaching"]:
    for fid in b["fids"]:
        if b["kind"] in ("facts", "globals"):
            T2_FID_BATCH[fid] = b["batch"]


def t2_gate(probe):
    tags = []
    for fid in probe["fids"]:
        tags.append(f"T2-teach-{T2_BATCH_SEQ[T2_FID_BATCH[fid]]:03d}")
    if probe["kind"] == "corrected":
        f = T2["facts"][probe["fids"][0]]
        proj = f["project"]
        tags.append(f"T2-teach-{T2_BATCH_SEQ[f'BY-{proj}']:03d}")
        if probe["id"].endswith("-Z") or probe["id"].endswith("-corrZ"):
            tags.append(f"T2-teach-{T2_BATCH_SEQ[f'BZ-{proj}']:03d}")
    return tags


def score_probe(cond, test, probe, gate_tags, restart_needed=False):
    rec = get(cond, test, probe["id"])
    if rec is None:
        return {"score": "MISSING", "reason": "turn not recorded"}
    if restart_needed:
        ok = any(m.get("condition") == cond and m.get("test") == test
                 and m.get("ok") for m in markers
                 if "restart" in (m.get("marker") or ""))
        if not ok:
            return {"score": "INVALID", "reason": "restart-failed",
                    "reply": rec["reply"], "terminal": rec["terminal"]}
    bad = [t for t in gate_tags if not setup_ok(get(cond, test, t))]
    if bad:
        return {"score": "INVALID", "reason": f"NOT-TAUGHT: {bad}",
                "reply": rec["reply"], "terminal": rec["terminal"]}
    if rec.get("invalid"):
        return {"score": "INVALID", "reason": rec["invalid"],
                "reply": rec["reply"], "terminal": rec["terminal"]}
    if rec.get("terminal") == "cancelled" and not rec.get("timeout"):
        return {"score": "INVALID", "reason": "spontaneous-cancel",
                "reply": rec["reply"], "terminal": rec["terminal"]}
    rule = probe.get("expected") or probe.get("rule")
    sc = common.score_rule(rule, rec["reply"])
    o = {"score": sc, "reply": rec["reply"], "terminal": rec["terminal"],
         "error": rec.get("error"), "kind": probe.get("kind")}
    if rec.get("timeout"):
        o["flag"] = "timeout"
    if rec.get("terminal") == "completed" and not (rec.get("reply") or "") \
            .strip():
        o["flag"] = "empty-completed"
    return o


def interval_stats(cond, test, lo, hi):
    sel = [r for r in turns if r["condition"] == cond and r.get("test") == test
           and r.get("kind") in ("fact", "correction", "fileop",
                                 "reference", "filler")
           and (r.get("usage") or {}).get("prompt_tokens") is not None]
    if test == "test1":
        sel = [r for r in sel
               if lo <= int(r["tag"].split("-")[1]) <= hi]
    pt = [(r["usage"] or {})["prompt_tokens"] for r in sel]
    lat = [r["turn_latency_ms"] for r in sel
           if r.get("turn_latency_ms") is not None]
    return {"n": len(sel),
            "mean_prompt": round(statistics.mean(pt), 1) if pt else None,
            "mean_latency_ms": round(statistics.mean(lat), 1) if lat else None}


def compaction_flags(cond, test):
    sel = sorted([r for r in turns if r["condition"] == cond
                  and r.get("test") == test and r.get("kind") != "probe"
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


# ============================ TEST 1 ============================
common.write_csv(os.path.join(BENCH, "long_session_usage.csv"),
                 rows_for("test1"), USAGE_KEYS)
res1 = {}
for cond in ("direct", "continuum"):
    res1[cond] = {}
    for p in T1["probes"]:
        res1[cond][p["id"]] = score_probe(cond, "test1", p, t1_gate(p))
    valid = [v for v in res1[cond].values() if v["score"] != "INVALID"
             and v["score"] != "MISSING"]
    res1[cond]["TOTAL"] = \
        f"{sum(v['score'] for v in valid)}/{len(valid)} valid"
    res1[cond]["_invalid"] = [k for k, v in res1[cond].items()
                              if isinstance(v, dict)
                              and v["score"] == "INVALID"]
    res1[cond]["_by_checkpoint"] = {}
    for cp in (10, 50, 100, 250):
        ps = [v for k, v in res1[cond].items()
              if k.startswith(f"T1-P{cp}-") and v["score"] != "INVALID"
              and v["score"] != "MISSING"]
        res1[cond]["_by_checkpoint"][str(cp)] = \
            f"{sum(v['score'] for v in ps)}/{len(ps)}"
    res1[cond]["_by_kind"] = {}
    for kind in ("recent", "medium", "old", "corrected", "indirect"):
        ps = [v for v in res1[cond].values()
              if isinstance(v, dict) and v.get("kind") == kind
              and v["score"] not in ("INVALID", "MISSING")]
        res1[cond]["_by_kind"][kind] = \
            f"{sum(v['score'] for v in ps)}/{len(ps)}" if ps else "n/a"
    res1[cond]["_intervals"] = {}
    for lo, hi in ((1, 10), (11, 50), (51, 100), (101, 250)):
        res1[cond]["_intervals"][f"{lo}-{hi}"] = interval_stats(
            cond, "test1", lo, hi)
    sel = [r for r in turns if r["condition"] == cond
           and r.get("test") == "test1"]
    res1[cond]["_cumulative"] = {
        "prompt": sum((r["usage"] or {}).get("prompt_tokens") or 0
                      for r in sel),
        "total": sum((r["usage"] or {}).get("total_tokens") or 0
                     for r in sel),
        "cost": round(sum((r["usage"] or {}).get("cost_derived_usd") or 0
                          for r in sel), 6),
        "n_turns": len(sel)}
    res1[cond]["_compaction_flags"] = compaction_flags(cond, "test1")
common.save_json(os.path.join(BENCH, "long_session_results.json"), res1)

# ============================ TEST 2 ============================
common.write_csv(os.path.join(BENCH, "memory_load_usage.csv"),
                 rows_for("test2"), USAGE_KEYS)
res2 = {}
for cond in ("direct", "continuum"):
    res2[cond] = {}
    for p in T2["probes"]:
        lvl = p["level"]
        res2[cond][p["id"]] = score_probe(
            cond, "test2", p, t2_gate(p), restart_needed=(lvl == "R"))
    for scope, ids in (
            ("TOTAL", [k for k in res2[cond] if not k.startswith("_")]),
            ("L1", [k for k in res2[cond] if k.startswith("T2-L1-")]),
            ("L2", [k for k in res2[cond] if k.startswith("T2-L2-")]),
            ("L3", [k for k in res2[cond] if k.startswith("T2-L3-")]),
            ("R", [k for k in res2[cond] if k.startswith("T2-R-")])):
        ps = [res2[cond][k] for k in ids
              if res2[cond][k]["score"] not in ("INVALID", "MISSING")]
        res2[cond][scope if scope == "TOTAL" else "_" + scope] = \
            f"{sum(v['score'] for v in ps)}/{len(ps)}" if scope == "TOTAL" \
            else f"{sum(v['score'] for v in ps)}/{len(ps)} valid"
    res2[cond]["_invalid"] = [k for k, v in res2[cond].items()
                              if isinstance(v, dict)
                              and v["score"] == "INVALID"]
    res2[cond]["_by_kind"] = {}
    for kind in ("global", "project-local", "oldest", "newest", "corrected",
                 "conflicting", "compare", "ambiguous"):
        ps = [v for v in res2[cond].values()
              if isinstance(v, dict) and v.get("kind") == kind
              and v["score"] not in ("INVALID", "MISSING")]
        res2[cond]["_by_kind"][kind] = \
            f"{sum(v['score'] for v in ps)}/{len(ps)}" if ps else "n/a"
    # probe cost/prompt means per level
    res2[cond]["_probe_means"] = {}
    for lvl in (1, 2, 3, "R"):
        sel = [r for r in turns if r["condition"] == cond
               and r.get("test") == "test2" and r.get("kind") == "probe"
               and r.get("level") == lvl
               and (r.get("usage") or {}).get("prompt_tokens") is not None]
        pt = [(r["usage"] or {})["prompt_tokens"] for r in sel]
        co = [(r["usage"] or {}).get("cost_derived_usd") or 0 for r in sel]
        res2[cond]["_probe_means"][str(lvl)] = {
            "n": len(sel),
            "mean_prompt": round(statistics.mean(pt), 1) if pt else None,
            "mean_cost": round(statistics.mean(co), 6) if co else None}
    sel = [r for r in turns if r["condition"] == cond
           and r.get("test") == "test2"]
    res2[cond]["_cumulative"] = {
        "prompt": sum((r["usage"] or {}).get("prompt_tokens") or 0
                      for r in sel),
        "total": sum((r["usage"] or {}).get("total_tokens") or 0
                     for r in sel),
        "cost": round(sum((r["usage"] or {}).get("cost_derived_usd") or 0
                          for r in sel), 6),
        "n_turns": len(sel)}
common.save_json(os.path.join(BENCH, "memory_load_results.json"), res2)

print("== TEST1 ==")
for cond in ("direct", "continuum"):
    print(f"{cond}: {res1[cond]['TOTAL']} invalid={res1[cond]['_invalid']}")
    print(f"  checkpoints={res1[cond]['_by_checkpoint']}")
    print(f"  kinds={res1[cond]['_by_kind']}")
print("== TEST2 ==")
for cond in ("direct", "continuum"):
    print(f"{cond}: {res2[cond]['TOTAL']} invalid={res2[cond]['_invalid']}")
    print(f"  L1={res2[cond]['_L1']} L2={res2[cond]['_L2']} "
          f"L3={res2[cond]['_L3']} R={res2[cond]['_R']}")
    print(f"  kinds={res2[cond]['_by_kind']}")
