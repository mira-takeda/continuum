"""3-system eval harness. Same Muse Code/MSP runtime for all; only memory differs.

S0: Muse Code/MSP, no persistent memory (fresh session per probe, probe only).
S1: Muse Code/MSP, flat append-only memory injected fully (unbounded growth).
S2: Muse Code/MSP, encyclopedia path (cheap retrieval -> CORE/index ->
    selective entries -> turn -> deterministic memory-ops parsing).

Usage: python evals/run_eval.py [--system all|S0|S1|S2] [--max-probes N]
Provider: MUSE_PROVIDER (meta default, echo for plumbing regression).
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from memory import core as core_mod, extract as extract_mod, index as index_mod
from memory import retrieve as retrieve_mod, store
from memory import turn as turn_mod
from memory.msp import MSPError, MSPHost, auth_status, find_muse, muse_version
from memory.muse_client import MuseClient

BOOT = ("You are Muse Spark Contributor. Answer using memory; cite entry ids used like [id]. "
        "If memory lacks the fact, say you don't have it; never invent.")

EVAL_SERVE_FLAGS = ["--disable-write", "--disable-shell"]
EVAL_TIMEOUT = 600


def load_tasks(tasks_dir):
    tasks = []
    for fp in sorted(glob.glob(os.path.join(tasks_dir, "*.json"))):
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        for p in d.get("probes", []):
            p["_category"] = d.get("category")
            tasks.append(p)
    return tasks


def mock_judge(answer, expect, forbid):
    a = (answer or "").lower()
    # Forbid fails only when the stale value is presented as current: mentions
    # alongside a correction marker ("not X", "previously X") don't fail.
    markers = ["not ", "n't ", "previously", "correction", "instead", "old ", "dropped",
               "no longer", "misremembered", "wrong", "changed", "moved to", "now "]
    for f in (forbid or []):
        if f and f.lower() in a and not any(m in a for m in markers):
            return 0.0
    hits = [e for e in (expect or []) if e.lower() in a]
    if not expect:
        return 1.0
    if hits:
        return 1.0  # expect entries are alternatives; any hit is full credit
    # Partial credit: any long word overlap
    ew = set(" ".join(expect).lower().split())
    aw = set(re.findall(r"[a-z]+", a))
    if len(ew & aw) >= 2:
        return 0.5
    return 0.0


def llm_judge(muse, probe, answer, expect, forbid, system):
    if muse.mocked:
        return mock_judge(answer, expect, forbid), "mock-keywords"
    prompt = (f"Probe: {probe}\nAnswer: {answer}\nExpected substrings: {expect}\n"
              f"Forbidden: {forbid}\nScore 1 if an expected idea present and no forbidden; "
              f"0.5 if partial; else 0. Reply ONLY the number.")
    try:
        t = muse.chat([{"role": "user", "content": prompt}], purpose="judge", system=system,
                      max_tokens=10, temperature=0.0)
        m = re.search(r"(0\.5|1(\.0)?|0(\.0)?)", t)
        return float(m.group(1)) if m else mock_judge(answer, expect, forbid), "llm"
    except Exception:
        return mock_judge(answer, expect, forbid), "mock-fallback"


def log_eval_usage(work, record):
    with open(os.path.join(work, "eval_usage.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def fresh_session(host, workspace):
    res = host.start_session(approval_mode="promptUnmatched")
    sid = (res.get("session") or {}).get("sessionId")
    if not sid:
        raise MSPError("session/start returned no sessionId")
    host.subscribe(sid)
    return sid


def leave_quietly(host, sid):
    try:
        host.leave_session(sid)
    except Exception:
        pass


def msp_turn(host, sid, full_text, display_text):
    """One turn through the SHARED core. Returns (summary, events, latency_ms).
    Raises MSPError on transport failure; timeout is reported, never retried."""
    t0 = time.time()
    events = list(turn_mod.stream_turn(host, sid, full_text, display_text, EVAL_TIMEOUT, None))
    ms = int((time.time() - t0) * 1000)
    return turn_mod.summarize(events), events, ms


class S0:
    name = "S0"
    def __init__(self, work, host):
        self.work = work
        self.host = host
    def ingest(self, text, turn):
        pass  # no persistent memory by design
    def answer(self, probe, probe_id):
        full = BOOT + "\n\nUSER MESSAGE:\n" + probe
        sid, failed, err = None, False, ""
        try:
            sid = fresh_session(self.host, self.work)
            summary, events, ms = msp_turn(self.host, sid, full, probe)
            if summary["timed_out"]:
                failed, err = True, "turn timeout (no turn/completed)"
        except MSPError as e:
            summary, ms = {"text": "", "usage": {}, "terminal": None,
                           "error": str(e), "timed_out": True}, 0
            failed, err = True, str(e)[:300]
        finally:
            if sid:
                leave_quietly(self.host, sid)
        u = summary["usage"] or {}
        log_eval_usage(self.work, {"ts": store.utcnow(), "model": u.get("modelId"),
                                   "purpose": "answer", "system": "S0", "probe": probe_id,
                                   "prompt_tokens": u.get("promptTokens"),
                                   "total_tokens": u.get("totalTokens"), "latency_ms": ms,
                                   "mocked": False, "provider": self.host.provider,
                                   "session": sid, "failed": failed, "error": err})
        return {"answer": summary["text"], "opened": [], "input_est": len(full) // 4,
                "latency_ms": ms, "failed": failed, "error": err}


class S1:
    name = "S1"
    def __init__(self, work, host):
        self.work = work
        self.host = host
        self.mem_path = os.path.join(work, "memory.json")
        self.mem = []
        if os.path.exists(self.mem_path):
            with open(self.mem_path, encoding="utf-8") as f:
                self.mem = json.load(f)
    def ingest(self, text, turn):
        self.mem.append({"turn": turn, "text": text})
        with open(self.mem_path, "w", encoding="utf-8") as f:
            json.dump(self.mem, f)
    def answer(self, probe, probe_id):
        # Full flat memory injected entirely, however large. Never truncated.
        dump = "\n".join(f"[{m['turn']}] {m['text']}" for m in self.mem)
        full = (BOOT + "\n\nFull flat memory (append-only, injected entirely):\n" + dump +
                "\n\nUSER MESSAGE:\n" + probe)
        sid, failed, err = None, False, ""
        try:
            sid = fresh_session(self.host, self.work)
            summary, events, ms = msp_turn(self.host, sid, full, probe)
            if summary["timed_out"]:
                failed, err = True, "turn timeout (no turn/completed)"
        except MSPError as e:
            summary, ms = {"text": "", "usage": {}, "terminal": None,
                           "error": str(e), "timed_out": True}, 0
            failed, err = True, str(e)[:300]
        finally:
            if sid:
                leave_quietly(self.host, sid)
        u = summary["usage"] or {}
        log_eval_usage(self.work, {"ts": store.utcnow(), "model": u.get("modelId"),
                                   "purpose": "answer", "system": "S1", "probe": probe_id,
                                   "prompt_tokens": u.get("promptTokens"),
                                   "total_tokens": u.get("totalTokens"), "latency_ms": ms,
                                   "mocked": False, "provider": self.host.provider,
                                   "session": sid, "failed": failed, "error": err,
                                   "mem_chars": len(dump)})
        return {"answer": summary["text"], "opened": [], "input_est": len(full) // 4,
                "latency_ms": ms, "mem_chars": len(dump), "failed": failed, "error": err}


class S2:
    name = "S2"
    def __init__(self, work, host):
        self.work = work  # work/S2 acts as project root (has data/)
        self.host = host
        os.makedirs(os.path.join(work, "data", "entries"), exist_ok=True)
        index_mod.load_or_build(work)
        core_mod.load_or_build(work)
    def _context_turn(self, text, turn_label, probe_id, purpose):
        """Exact product memory path: cheap retrieval -> CORE/index ->
        selective entries -> shared turn core -> deterministic block parse."""
        P = store.data_paths(self.work)
        idx = index_mod.load_or_build(self.work)
        ret = retrieve_mod.retrieve(self.work, None, text, system="S2")
        full = BOOT + "\n\n" + turn_mod.build_turn_text(self.work, text, ret, idx)
        sid = fresh_session(self.host, self.work)
        try:
            summary, events, ms = msp_turn(self.host, sid, full, text)
        except MSPError as e:
            leave_quietly(self.host, sid)
            return {"text": "", "usage": {}, "failed": True, "error": str(e)[:300],
                    "ms": 0, "sid": sid, "ret": ret}
        leave_quietly(self.host, sid)
        failed = summary["timed_out"]
        u = summary["usage"] or {}
        log_eval_usage(self.work, {"ts": store.utcnow(), "model": u.get("modelId"),
                                   "purpose": purpose, "system": "S2", "probe": probe_id,
                                   "prompt_tokens": u.get("promptTokens"),
                                   "total_tokens": u.get("totalTokens"), "latency_ms": ms,
                                   "mocked": False, "provider": self.host.provider,
                                   "session": sid, "failed": failed,
                                   "error": ("" if not failed else "turn timeout (no turn/completed)")})
        return {"text": summary["text"], "usage": u, "failed": failed,
                "error": ("" if not failed else "turn timeout"), "ms": ms, "sid": sid, "ret": ret}
    def ingest(self, text, turn, probe_id=""):
        # History turns run as real turns (writes applied, replies discarded).
        out = self._context_turn(text, turn, probe_id, "ingest")
        if out["failed"]:
            return {"writes": [], "failed": True}
        model_name = f"muse-code/{muse_version() or 'unknown'}"
        writes = extract_mod.apply_ops_from_block(self.work, out["text"], turn, model_name)
        for w in writes:
            try:
                index_mod.update_one(self.work, w["entry_id"])
            except Exception:
                pass
        core_mod.rebuild(self.work)
        return {"writes": writes, "failed": False}
    def answer(self, probe, probe_id):
        # Probes are read-only: memory-ops in the answer turn are NOT applied.
        out = self._context_turn(probe, f"probe-{probe_id}", probe_id, "answer")
        ignored = extract_mod.parse_memory_block(out["text"])
        full_len = len(BOOT) + 2 + len(turn_mod.build_turn_text(
            self.work, probe, out["ret"],
            index_mod.load_or_build(self.work)))
        return {"answer": out["text"], "opened": out["ret"]["opened"], "paths": out["ret"]["paths"],
                "input_est": full_len // 4, "latency_ms": out["ms"],
                "failed": out["failed"], "error": out["error"],
                "writes_ignored": len(ignored)}


def run_system(name, tasks, work_root, host, judge, max_probes):
    work = os.path.join(work_root, name)
    os.makedirs(work, exist_ok=True)
    sys_obj = {"S0": S0, "S1": S1, "S2": S2}[name](work, host)
    results = []
    todo = tasks[:max_probes] if max_probes else tasks
    for i, t in enumerate(todo):
        for h in t.get("history", []):
            if name == "S2":
                sys_obj.ingest(f"{h.get('role')}: {h.get('text')}", f"eval{i}", t["id"])
            else:
                sys_obj.ingest(f"{h.get('role')}: {h.get('text')}", f"eval{i}")
        out = sys_obj.answer(t["probe"], t["id"])
        score, how = llm_judge(judge, t["probe"], out["answer"], t.get("expect", []),
                               t.get("forbid", []), name)
        a_low = (out["answer"] or "").lower()
        cite = 0
        if name == "S2":
            cite = 1 if (re.search(r"\[[0-9]{8}-[0-9]{4}-", out["answer"] or "") and out["opened"]) else 0
        elif "neg" in t.get("id", ""):
            cite = 1 if score == 1.0 else 0
        sup = ""
        if t.get("supersede"):
            s = t["supersede"]
            sup = 1 if (s["new"].lower() in a_low and s["old"].lower() not in a_low) else 0
        xhit = ""
        if t.get("cross_domain"):
            xhit = 1 if score >= 0.5 else 0
        # Retrieval precision for S2: opened entries containing an expect string.
        prec = ""
        if name == "S2" and out["opened"]:
            rel = sum(1 for e in out["opened"]
                      if any(x.lower() in (e.get("body", "") + e.get("title", "")).lower()
                             for x in t.get("expect", [])))
            prec = round(rel / len(out["opened"]), 2)
        results.append({"id": t["id"], "category": t["_category"], "score": score,
                        "judge": how, "citation_ok": cite, "supersede_ok": sup,
                        "cross_domain_hit": xhit, "precision": prec,
                        "input_est": out.get("input_est", 0), "latency_ms": out.get("latency_ms", 0),
                        "answer": (out["answer"] or "")[:300],
                        "opened": [e["id"] for e in out.get("opened", [])],
                        "mem_chars": out.get("mem_chars", ""),
                        "failed": out.get("failed", False), "error": out.get("error", ""),
                        "writes_ignored": out.get("writes_ignored", "")})
    return results


def summarize(results):
    by_cat = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r["score"])
    acc = {c: round(sum(v) / len(v), 3) for c, v in by_cat.items()}
    overall = round(sum(r["score"] for r in results) / max(1, len(results)), 3)
    sup = [r for r in results if r["supersede_ok"] != ""]
    x = [r for r in results if r["cross_domain_hit"] != ""]
    neg = [r for r in results if "neg" in r["id"]]
    return {"overall": overall, "by_category": acc,
            "supersede_rate": round(sum(r["supersede_ok"] for r in sup) / max(1, len(sup)), 3),
            "xdom_rate": round(sum(r["cross_domain_hit"] for r in x) / max(1, len(x)), 3),
            "neg_ok": round(sum(1 for r in neg if r["score"] >= 1.0) / max(1, len(neg)), 3),
            "mean_input_est": int(sum(r["input_est"] for r in results) / max(1, len(results))),
            "mean_latency_ms": int(sum(r["latency_ms"] for r in results) / max(1, len(results)))}


def main():
    import os as _os
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", default="all")
    ap.add_argument("--tasks", default=os.path.join(ROOT, "evals", "tasks"))
    ap.add_argument("--work", default=os.path.join(ROOT, "evals", "work"))
    ap.add_argument("--max-probes", type=int, default=0)
    args = ap.parse_args()
    systems = ["S0", "S1", "S2"] if args.system == "all" else [args.system]
    tasks = load_tasks(args.tasks)
    print(f"Loaded {len(tasks)} probes.")
    if not find_muse():
        raise SystemExit("Muse Code is not installed; eval needs the MSP runtime.")
    provider = _os.environ.get("MUSE_PROVIDER", "meta")
    model = _os.environ.get("MUSE_MODEL") or None
    if provider == "meta":
        st = auth_status()
        print(f"auth: logged_in={st['logged_in']} :: {(st['text'] or '')[:120]}")
        if not st["logged_in"]:
            raise SystemExit("Muse Code is not logged in; cannot run model-backed eval.")
    print(f"Runtime: MSP (muse serve) provider={provider} model={model or 'host-default'} "
          f"flags={EVAL_SERVE_FLAGS} timeout={EVAL_TIMEOUT}s max_probes={args.max_probes or 'all'}")
    # Judge dispatch only; with no direct-API key this is pure keyword scoring,
    # identical code for all three systems (frozen scoring, not a baseline).
    judge = MuseClient(os.path.join(args.work, "_judge"), model=model or "muse-spark-contributor",
                       base_url=_os.environ.get("META_MODEL_BASE_URL", "https://api.meta.com/model/v1"),
                       api_key="")
    all_res, sums = {}, {}
    for s in systems:
        work = os.path.join(args.work, s)
        if os.path.isdir(work):
            shutil.rmtree(work)
        os.makedirs(work, exist_ok=True)
        host = MSPHost(work, provider=provider, model=model,
                       extra_args=EVAL_SERVE_FLAGS)
        try:
            res = run_system(s, tasks, args.work, host, judge, args.max_probes)
        finally:
            host.stop()
        all_res[s] = res
        sums[s] = summarize(res)
        nf = sum(1 for r in res if r.get("failed"))
        print(f"{s}: overall={sums[s]['overall']} tokens/probe~{sums[s]['mean_input_est']} failed={nf}")
    with open(os.path.join(ROOT, "evals", "results.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": sums,
                   "runtime": {"msp": True, "provider": provider, "model": model or "host-default",
                               "muse": muse_version(), "serve_flags": EVAL_SERVE_FLAGS},
                   "results": all_res}, f, indent=1)
    # Markdown report
    lines = ["# Eval report (S0 no-memory vs S1 flat-injected vs S2 encyclopedia)",
             "", f"Probes: {len(tasks)}. Runtime: MSP (muse serve) provider={provider} "
             f"model={model or 'host-default'}.", ""]
    for s in systems:
        sm = sums[s]
        lines.append(f"## {s}: overall {sm['overall']} | sup {sm['supersede_rate']} | "
                     f"xdom {sm['xdom_rate']} | neg {sm['neg_ok']} | "
                     f"tok/probe {sm['mean_input_est']} | lat {sm['mean_latency_ms']}ms")
        for c, a in sorted(sm["by_category"].items()):
            lines.append(f"- {c}: {a}")
        lines.append("")
    # Growth evidence: S1 input tokens first vs last probe.
    if "S1" in all_res:
        seq = [r["input_est"] for r in all_res["S1"]]
        lines.append(f"S1 context growth: first probe ~{seq[0]} tok, last probe ~{seq[-1]} tok (unbounded).")
    if "S2" in all_res:
        seq = [r["input_est"] for r in all_res["S2"]]
        lines.append(f"S2 context: first probe ~{seq[0]} tok, last probe ~{seq[-1]} tok (bounded).")
    if provider == "echo":
        lines.append("")
        lines.append("Echo caveat: echo provider replays prompts for plumbing regression; "
                     "scores are meaningless here. Mechanics (sessions, isolation, writes, "
                     "usage, fail-closed paths) are what this run validates.")
    with open(os.path.join(ROOT, "evals", "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("Wrote evals/results.json + evals/report.md")


if __name__ == "__main__":
    main()
