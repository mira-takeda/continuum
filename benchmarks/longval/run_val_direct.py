"""LONGVAL direct driver (raw MSP + frozen approval policy).

Adapted from frozen run_scale_direct.py run_test1: first 100 turns,
probes after 10/50/100, isolated workspace. Appends to raw_turns.jsonl;
skips already-recorded tags (no resends).
"""
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from memory import msp as msp_mod
from memory import turn as turn_mod
import val_common as common

BENCH = HERE
RAW = os.path.join(BENCH, "raw_turns.jsonl")
TURN_TIMEOUT = 200
MAX_N = 100

T1 = json.load(open(os.path.join(BENCH, "scale_test1.json"), encoding="utf-8"))
T1_PROBES_AFTER = {}
for p in T1["probes"]:
    if p["after"] <= MAX_N:
        T1_PROBES_AFTER.setdefault(p["after"], []).append(p)


def load_done(condition, test):
    done = set()
    if os.path.exists(RAW):
        for line in open(RAW, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("condition") == condition and r.get("test") == test \
                    and "tag" in r:
                done.add(r["tag"])
    return done


def log(rec):
    with open(RAW, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def do_turn(host, sid, prompt, wsroot, timeout=TURN_TIMEOUT):
    t0 = time.time()
    first_visible = None
    events = []
    approvals = []
    questions_n = 0
    tool_n = 0
    started_id = None
    done_ev = None
    invalid = None
    try:
        for ev in turn_mod.stream_turn(host, sid, prompt, prompt[:500],
                                       timeout=timeout):
            if ev.get("t") == "text" and first_visible is None:
                first_visible = int((time.time() - t0) * 1000)
            if ev.get("t") == "turn-started":
                started_id = ev.get("turn_id")
            if ev.get("t") in ("tool", "activity"):
                tool_n += 1
            if ev.get("t") == "approval":
                ch, label, _ = common.decide_approval(ev, wsroot)
                rec = {"id": ev.get("approvalId"),
                       "tool": ev.get("toolName"),
                       "subject": json.dumps(ev.get("subject"))[:300],
                       "decision": label, "choice": ch}
                if ch is None:
                    rec["rpc"] = "no-choice"
                    invalid = "approval-no-choice"
                else:
                    try:
                        host.decide(sid, ev.get("approvalId"), ch,
                                    ev.get("requirementId"))
                        rec["rpc"] = "ok"
                    except Exception as e:
                        rec["rpc"] = f"error: {e}"[:200]
                        invalid = "approval-rpc-failed"
                approvals.append(rec)
            if ev.get("t") == "questions":
                questions_n += 1
                try:
                    host.answer_input(
                        sid, ev.get("userInputId"),
                        common.answers_for(ev.get("questions")))
                except Exception as e:
                    invalid = f"questions-rpc-failed: {e}"[:200]
            if ev.get("t") == "done":
                done_ev = ev
            events.append(ev)
    except Exception as e:  # noqa: BLE001 - fail closed
        events.append({"t": "error", "text": f"driver: {e}"})
    s = turn_mod.summarize(events)
    if s["terminal"] is None:
        try:
            host.cancel_turn(sid)
        except Exception:
            pass
    integrity = None
    if done_ev is not None and started_id and done_ev.get("turnId"):
        if done_ev["turnId"] != started_id:
            integrity = "done.turnId != started"
            invalid = "integrity-alert"
    ctx = next((e for e in reversed(events) if e.get("t") == "context"), None)
    md, ag = common.snapshot_light(wsroot)
    return {
        "reply": s["text"], "usage": common.norm_direct(s["usage"], ctx),
        "terminal": s["terminal"], "error": s["error"],
        "timeout": s["terminal"] is None,
        "cancelled": s["terminal"] == "cancelled",
        "started_turn_id": started_id,
        "done_turn_id": (done_ev or {}).get("turnId"),
        "done_reason": (done_ev or {}).get("reason"),
        "done_duration_ms": (done_ev or {}).get("durationMs"),
        "approvals": approvals, "questions_answered": questions_n,
        "tool_events": tool_n, "integrity_alert": integrity,
        "invalid": invalid, "first_visible_ms": first_visible,
        "turn_latency_ms": int((time.time() - t0) * 1000),
        "md_count": md, "agents_present": ag,
    }


def fresh_sid(host, ws):
    res = host.start_session(approval_mode="promptUnmatched", workspace_root=ws)
    sid = (res.get("session") or {}).get("sessionId")
    host.subscribe(sid)
    return sid


def snap_marker(condition, test, wsroot, label):
    log({"marker": "snapshot", "condition": condition, "test": test,
         "label": label, "files": common.snapshot_full(wsroot),
         "ts": time.time()})


def main():
    ws = os.path.join(BENCH, "lv-direct-ws")
    os.makedirs(os.path.join(ws, "bench-files-scale"), exist_ok=True)
    done = load_done("direct", "longval1")
    host = msp_mod.MSPHost(ws, provider="meta")
    try:
        sid = fresh_sid(host, ws)
        log({"marker": "session", "condition": "direct", "test": "longval1",
             "session": "lvmain", "sid": sid, "ts": time.time()})
        snap_marker("direct", "longval1", ws, "start")
        for t in T1["turns"]:
            if t["n"] > MAX_N:
                continue
            tag = f"T1-{t['n']:04d}"
            if tag not in done:
                r = do_turn(host, sid, t["text"], ws)
                r.update({"condition": "direct", "test": "longval1", "tag": tag,
                          "kind": t["kind"], "ref": t.get("ref"),
                          "session": "lvmain", "prompt": t["text"],
                          "ts": time.time()})
                log(r)
                done.add(tag)
                print(f"{tag} {t['kind']}: term={r['terminal']} "
                      f"lat={r['turn_latency_ms']}ms "
                      f"tok={r['usage']['total_tokens']} "
                      f"appr={len(r['approvals'])} md={r['md_count']} "
                      f"ag={r['agents_present']}", flush=True)
            if t["n"] in T1_PROBES_AFTER:
                for p in T1_PROBES_AFTER[t["n"]]:
                    if p["id"] not in done:
                        r = do_turn(host, sid, p["text"], ws)
                        r.update({"condition": "direct", "test": "longval1",
                                  "tag": p["id"], "kind": "probe",
                                  "ref": p["ref"], "session": "lvmain",
                                  "prompt": p["text"], "ts": time.time()})
                        log(r)
                        done.add(p["id"])
                        print(f"{p['id']}: term={r['terminal']} "
                              f"reply={r['reply'][:80]!r}", flush=True)
                snap_marker("direct", "longval1", ws, f"after-{t['n']}")
        snap_marker("direct", "longval1", ws, "end")
        try:
            host.leave_session(sid)
        except Exception:
            pass
    finally:
        try:
            host.stop()
        except Exception:
            pass
    print("direct longval1 done")


if __name__ == "__main__":
    main()
