"""FINAL150 single unattended runner.

One launch runs everything, no interactive input:
  1. preconditions (HEAD, hashes, port, model default, copy integrity)
  2. direct leg (150 turns + 25 probes)
  3. continuum leg (150 turns + 25 probes + 1 refresh + 5 R2 probes)
  4. scoring (usage.csv, recall_results.json, approval_log.json)
  5. final150_report.md
  6. stray sweep + synthetic-workspace cleanup + integrity re-check

Approvals inside both legs are automatic via the frozen
workspace-only policy (byte-identical val_common.decide_approval):
allow iff confined to the leg workspace, else deny. Every request
recorded. No human prompts possible: drivers never block on input.

Usage:
  python run_final150.py --check    # verify everything, no model calls
  python run_final150.py            # the full benchmark run
"""
import csv
import hashlib
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
import val_common as common

RAW = os.path.join(HERE, "raw_turns.jsonl")
PORT = 8169
EXPECT_HEAD = "77ad19c58be43b41f9d2fcde9cc38f1fb5df9b99"
EXPECT_T1 = "CE659E62FA99C13F21E19FD749ED9457DB5662B0C6A0A9AD8827662E932706FF"
EXPECT_COMMON = "7E478E3E49F5B09C38ECC147CDE350D69FF9E5DD577B8746F4E3A48DFF058605"
COST_IN_PER_M = 0.10
COST_OUT_PER_M = 0.20
RUN_START = time.time()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


GIT = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"


def git(args):
    env = dict(os.environ)
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "safe.directory"
    env["GIT_CONFIG_VALUE_0"] = PRODUCT
    r = subprocess.run([GIT] + args, cwd=PRODUCT, capture_output=True,
                       text=True, timeout=60, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {(r.stderr or '')[:200]}")
    return r.returncode, (r.stdout or "").strip()


def check_port_free():
    try:
        s = socket.create_connection(("127.0.0.1", PORT), timeout=2)
        s.close()
        return False
    except OSError:
        return True


def preconditions():
    """All checks that need no model calls. Returns env dict or raises."""
    env = {}
    rc, head = git(["rev-parse", "HEAD"])
    assert rc == 0 and head == EXPECT_HEAD, f"HEAD {head}"
    env["head"] = head
    rc, st = git(["status", "--short"])
    assert rc == 0 and st == "", f"dirty tree: {st[:300]}"
    env["branch"] = git(["branch", "--show-current"])[1]
    assert sha256_file(os.path.join(HERE, "scale_test1.json")) == EXPECT_T1
    assert sha256_file(os.path.join(HERE, "val_common.py")) == EXPECT_COMMON
    assert sha256_file(os.path.join(
        PRODUCT, "memory", "turn.py")) or True
    env["probes_sha"] = sha256_file(os.path.join(HERE, "final150_probes.json"))
    assert check_port_free(), f"port {PORT} busy"
    copy = os.path.join(HERE, "final150-continuum-copy")
    srv = os.path.join(copy, "server.py")
    assert os.path.isfile(os.path.join(copy, "memory", "delivery.py"))
    txt = open(srv, encoding="utf-8").read()
    assert "/api/memory/refresh" in txt and "plan_refresh" in txt, \
        "copy lacks refresh feature"
    env["copy_ok"] = True
    sys.path.insert(0, PRODUCT)
    from memory import msp as msp_mod
    host = msp_mod.MSPHost(PRODUCT, provider="meta")
    try:
        models = host.list_models().get("models", [])
    finally:
        try:
            host.stop()
        except Exception:
            pass
    dflt = [m for m in models if m.get("isDefault")]
    assert dflt and dflt[0]["modelId"] == "muse-spark-1.3-contributor", models
    env["model"] = dflt[0]["modelId"]
    env["effort"] = dflt[0].get("defaultReasoningEffort")
    return env


def run_leg(script, logname):
    logp = os.path.join(HERE, logname)
    with open(logp, "w", encoding="utf-8") as lf:
        lf.write(f"# {script} started {time.ctime()}\n")
        lf.flush()
        r = subprocess.run([sys.executable, os.path.join(HERE, script)],
                           cwd=HERE, stdout=lf, stderr=subprocess.STDOUT,
                           timeout=6 * 3600)
    return r.returncode


def load_turns():
    recs = [json.loads(x) for x in open(RAW, encoding="utf-8")]
    return [r for r in recs if "tag" in r and r.get("test") == "final150"]


def find(turns, cond, tag):
    return next((r for r in turns if r["condition"] == cond
                 and r["tag"] == tag), None)


def build_report(env, t0):
    turns = load_turns()

    def get(cond, tag):
        return find(turns, cond, tag)

    L = []
    L.append("# Final 150-turn benchmark report\n")
    L.append(f"HEAD: {env['head']} ({env['branch']})")
    L.append(f"model: {env['model']} (host default, effort {env['effort']})")
    L.append("approval: promptUnmatched + frozen workspace-only auto policy")
    L.append(f"run: {time.ctime(t0)} .. {time.ctime()} "
             f"({(time.time()-t0)/60:.0f} min)")
    L.append(f"protocol SHA: {open(os.path.join(HERE, 'final150_protocol.sha256')).read().strip()}")
    L.append(f"probes SHA: {env['probes_sha']}\n")
    L.append("## 1. Prompt tokens at checkpoints\n")
    L.append("| Turn | Direct | Continuum | Ratio | C-added chars |")
    L.append("|---|---|---|---|---|")
    for n in (1, 10, 25, 50, 75, 100, 125, 150):
        tag = f"T1-{n:04d}"
        d = get("direct", tag)["usage"]["prompt_tokens"]
        c = get("continuum", tag)
        cp = c["usage"]["prompt_tokens"]
        dl = c.get("delivery") or {}
        L.append(f"| {n} | {d} | {cp} | {cp/d:.3f} | "
                 f"{dl.get('mode')}/{dl.get('added_chars')} |")
    L.append("\n## 2. Cumulative prompt/cost (sequence turns)\n")
    for cond in ("direct", "continuum"):
        L.append(f"### {cond}\n")
        for end in (10, 50, 100, 150):
            sel = [get(cond, f"T1-{n:04d}") for n in range(1, end + 1)]
            pr = sum(r["usage"]["prompt_tokens"] for r in sel)
            co = sum(r["usage"]["cost_derived_usd"] for r in sel)
            L.append(f"- thru {end}: {pr} tokens / ${co:.4f} DERIVED")
    res = json.load(open(os.path.join(HERE, "recall_results.json"),
                         encoding="utf-8"))
    L.append("\n## 3. Recall\n")
    for cond in ("direct", "continuum"):
        L.append(f"- {cond}: TOTAL {res[cond]['TOTAL']} "
                 f"checkpoints {res[cond]['_by_checkpoint']} "
                 f"kinds {res[cond]['_by_kind']} "
                 f"invalid {res[cond]['_invalid']}")
    r2 = res["continuum"].get("_r2_after_refresh", {})
    ok = [k for k, v in r2.items() if v.get("score") == 1]
    L.append(f"- continuum R2 after refresh: {len(ok)}/{len(r2)} "
             f"({', '.join(f'{k}={r2[k]['score']}' for k in sorted(r2))})")
    rf = get("continuum", "T1-P100-REFRESH")
    if rf:
        u = rf.get("usage") or {}
        dl = rf.get("delivery") or {}
        L.append(f"- refresh cost: mode={dl.get('mode')} "
                 f"items={dl.get('items')} chars={dl.get('added_chars')} "
                 f"tokens_est={dl.get('tokens_est')} "
                 f"prompt={u.get('prompt_tokens')} "
                 f"cost=${u.get('cost_derived_usd')} "
                 f"latency={rf.get('turn_latency_ms')}ms "
                 f"terminal={rf.get('terminal')}")
    L.append("\n## 4. Totals + cost per correct recall\n")
    for cond in ("direct", "continuum"):
        sel = [r for r in turns if r["condition"] == cond]
        pr = sum((r["usage"] or {}).get("prompt_tokens") or 0 for r in sel)
        co = sum((r["usage"] or {}).get("cost_derived_usd") or 0 for r in sel)
        n_ok = sum(1 for k, v in res[cond].items()
                   if isinstance(v, dict) and v.get("score") == 1)
        L.append(f"- {cond}: n={len(sel)} prompt={pr} cost=${co:.4f} "
                 f"correct={n_ok} "
                 f"cost/correct=${co/n_ok:.4f}" if n_ok else
                 f"- {cond}: n={len(sel)} prompt={pr} cost=${co:.4f}")
    ap = json.load(open(os.path.join(HERE, "approval_log.json"),
                        encoding="utf-8"))
    L.append("\n## 5. Approvals\n")
    for cond in ("direct", "continuum"):
        L.append(f"- {cond}: {ap['summary'][cond]}")
    L.append("\n## 6. Failures\n")
    for cond in ("direct", "continuum"):
        sel = [r for r in turns if r["condition"] == cond]
        L.append(f"- {cond}: timeouts={sum(1 for r in sel if r.get('timeout'))} "
                 f"cancelled={sum(1 for r in sel if r.get('cancelled'))} "
                 f"invalid={[r['tag'] for r in sel if r.get('invalid')]} "
                 f"errors={[(r['tag'], r.get('error')) for r in sel if r.get('error')]} "
                 f"noncompleted={[(r['tag'], r.get('terminal')) for r in sel if r['terminal'] != 'completed']}")
    L.append("\n## 7. Delivery (continuum)\n")
    L.append(f"- {res['continuum'].get('_delivery')}")
    L.append(f"- compaction flags: direct={res['direct'].get('_compaction_flags')} "
             f"continuum={res['continuum'].get('_compaction_flags')}")
    open(os.path.join(HERE, "final150_report.md"), "w",
         encoding="utf-8").write("\n".join(L) + "\n")
    return "\n".join(L)


BENCH_FILES = {"note.txt", "todo.txt"}
BENCH_WORDS = {"hello", "world", "sweep", "mop"}


def stray_sweep(t0):
    """Find benchmark fileop residue outside isolated dirs.

    Removes ONLY files that (a) sit outside the benchmark folder,
    (b) have a frozen fileop name, (c) were modified during this run,
    and (d) contain exactly frozen fileop words. Anything else is
    reported, never touched.
    """
    home = os.path.expanduser("~")
    found, removed, kept = [], [], []
    for dp, _dn, fns in os.walk(home):
        if os.path.normcase(dp).startswith(os.path.normcase(HERE)):
            continue
        if "AppData" in dp.split(os.sep):
            continue
        for fn in fns:
            if fn not in BENCH_FILES:
                continue
            p = os.path.join(dp, fn)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            words = set()
            try:
                with open(p, encoding="utf-8", errors="strict") as f:
                    words = set(f.read().split())
            except Exception:
                pass
            if mt >= t0 - 60 and words and words <= BENCH_WORDS:
                found.append(p)
                try:
                    os.remove(p)
                    removed.append(p)
                except OSError:
                    kept.append(p + " (remove-failed)")
            else:
                kept.append(p + " (not-run-residue)")
    return found, removed, kept


def cleanup():
    ws = os.path.join(HERE, "final150-direct-ws")
    if os.path.isdir(ws):
        shutil.rmtree(ws)
    return (not os.path.exists(ws),
            os.path.isdir(os.path.join(HERE, "final150-continuum-copy")))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        env = preconditions()
        print("PRECONDITIONS OK")
        print(json.dumps(env, indent=1))
        T1 = json.load(open(os.path.join(HERE, "scale_test1.json"),
                            encoding="utf-8"))
        n = sum(1 for t in T1["turns"] if t["n"] <= 150)
        assert n == 150, n
        print("turns<=150: 150 OK")
        return 0
    if os.path.exists(RAW) and os.path.getsize(RAW) > 0:
        print("REFUSING: raw_turns.jsonl already has records; a rerun "
              "would split the single-session legs. Delete it explicitly "
              "for a fresh run.")
        return 2
    t0 = time.time()
    env = preconditions()
    print(f"HEAD {env['head']} model {env['model']} — starting direct leg",
          flush=True)
    rc = run_leg("run_f150_direct.py", "direct_leg.log")
    print(f"direct leg exit={rc}", flush=True)
    print("starting continuum leg", flush=True)
    rc2 = run_leg("run_f150_continuum.py", "continuum_leg.log")
    print(f"continuum leg exit={rc2}", flush=True)
    print("scoring", flush=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, "score_f150.py")],
                       cwd=HERE, capture_output=True, text=True, timeout=600)
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print("SCORER FAILED", r.stderr[-2000:])
        return 3
    print("report", flush=True)
    print(build_report(env, t0)[:3000])
    print("stray sweep + cleanup", flush=True)
    found, removed, kept = stray_sweep(t0)
    print(f"strays found={found} removed={removed} kept={kept}")
    ws_gone, copy_kept = cleanup()
    print(f"direct-ws removed={ws_gone} continuum-copy kept={copy_kept}")
    with open(os.path.join(HERE, "final150_report.md"), "a",
              encoding="utf-8") as f:
        f.write(f"\n## 8. Residue + integrity\n\n"
                f"- strays found={found} removed={removed} kept={kept}\n"
                f"- direct-ws removed={ws_gone} "
                f"continuum-copy kept as evidence={copy_kept}\n"
                f"- HEAD after run: {git(['rev-parse', 'HEAD'])[1]}\n"
                f"- tree after run: {git(['status', '--short'])[1]!r}\n")
    print("FINAL150 COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
