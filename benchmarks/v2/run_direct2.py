"""DIRECT V2 driver: raw MSP turns + deterministic approval handling.

Usage: run_direct2.py <phase:A|B>  (appends to raw_results_v2.jsonl)
Prompts copied verbatim from benchmark_protocol_v2.md §8/§9.
"""
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import msp as msp_mod
from memory import turn as turn_mod
import common2 as common

BENCH = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(BENCH, "direct-ws-v2")
RAW = os.path.join(BENCH, "raw_results_v2.jsonl")
TURN_TIMEOUT = 200

PART_A = [
    "Explain in at most 3 sentences why caching helps repeated computations.",
    "Read the file bench-files/notes-seed.txt in the workspace and list the three items it contains.",
    "Create a file named bench-files/shopping.txt containing exactly these three lines: apples, bread, cheese. Reply with one sentence confirming.",
    "Add a fourth line with the word milk to bench-files/shopping.txt, then confirm in one sentence.",
    "Correction: the file should be named bench-files/groceries.txt, not bench-files/shopping.txt. Rename it and confirm in one sentence.",
    "What was the second item in bench-files/notes-seed.txt?",
    "We are starting a small balcony garden plan. Remember that we chose cherry tomatoes as the first crop. Reply in one sentence.",
    "Summarize this conversation so far in at most 4 bullet points.",
    "Give two practical reasons to prefer walking over driving for short errands. At most 3 sentences.",
    "What filename did I correct you about earlier, and what is it called now?",
]

# (step_tag, session_key, prompt); session_key groups turns into one sid.
PART_B = [
    ("B1teach", "b1t", "A general preference for all our chats: when you list options for me, always number them instead of using bullets. Acknowledge briefly."),
    ("B1", "b1p", "Give me three options for a weekend hike."),
    ("B2setup1", "b2s", "Decision one for the Kestrel birdhouse: it will be painted teal."),
    ("B2setup2", "b2s", "Decision two for the Kestrel birdhouse: the entrance hole faces east."),
    ("B2", "b2p", "What color will the Kestrel birdhouse be, and which way does the entrance hole face?"),
    ("B3corr", "b3c", "Correction for Kestrel: paint it terracotta, not teal."),
    ("B3", "b3p", "What color will the Kestrel birdhouse be now?"),
    ("B4setup", "b4s", "For the Meridian birdhouse: it will be painted navy."),
    ("B4", "b4p", "What color is the Kestrel birdhouse?"),
    ("B5", "b5", "Give me three options for birdseed."),
    ("B6", "b6", "Compare the Kestrel and Meridian birdhouses: what color is each?"),
    ("B7a", "b7", "Give me three options for a birdhouse camera."),
    ("B7b", "b7", "What color is the Kestrel birdhouse now?"),
    ("B7c", "b7", "What color is the Meridian birdhouse?"),
]


def log(rec):
    with open(RAW, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def do_turn(host, sid, prompt, timeout=TURN_TIMEOUT):
    t0 = time.time()
    first_visible = None
    events = []
    approvals = []
    questions_n = 0
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
            if ev.get("t") == "approval":
                ch, label, _ = common.decide_approval(ev, WS)
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
    except Exception as e:  # noqa: BLE001 - fail closed per protocol
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
            integrity = (f"done.turnId {done_ev['turnId']} != "
                         f"started {started_id}")
            invalid = "integrity-alert"
    ctx = next((e for e in reversed(events) if e.get("t") == "context"), None)
    return {
        "reply": s["text"],
        "usage": common.norm_direct(s["usage"], ctx),
        "terminal": s["terminal"],
        "error": s["error"],
        "timeout": s["terminal"] is None,
        "cancelled": s["terminal"] == "cancelled",
        "started_turn_id": started_id,
        "done_turn_id": (done_ev or {}).get("turnId"),
        "done_reason": (done_ev or {}).get("reason"),
        "done_duration_ms": (done_ev or {}).get("durationMs"),
        "approvals": approvals,
        "questions_answered": questions_n,
        "integrity_alert": integrity,
        "invalid": invalid,
        "first_visible_ms": first_visible,
        "turn_latency_ms": int((time.time() - t0) * 1000),
    }


def fresh_sid(host, ws):
    res = host.start_session(approval_mode="promptUnmatched", workspace_root=ws)
    sid = (res.get("session") or {}).get("sessionId")
    host.subscribe(sid)
    return sid


def main():
    phase = sys.argv[1]
    os.makedirs(WS, exist_ok=True)
    os.makedirs(os.path.join(WS, "bench-files"), exist_ok=True)
    host = msp_mod.MSPHost(WS, provider="meta")
    sids = {}
    try:
        if phase == "A":
            with open(os.path.join(WS, "bench-files", "notes-seed.txt"), "w") as f:
                f.write("shelf screws\nwall anchors\nsandpaper\n")
            sid = fresh_sid(host, WS)
            for i, prompt in enumerate(PART_A, 1):
                r = do_turn(host, sid, prompt)
                r.update({"condition": "direct", "phase": "A",
                          "turn": f"A{i}", "session": "benchA",
                          "prompt": prompt, "ts": time.time()})
                log(r)
                print(f"A{i}: terminal={r['terminal']} lat={r['turn_latency_ms']}ms "
                      f"tok={r['usage']['total_tokens']} appr={len(r['approvals'])}",
                      flush=True)
            host.leave_session(sid)
        else:
            for tag, skey, prompt in PART_B:
                if tag == "B7a":  # full process restart per protocol
                    host.stop()
                    host = msp_mod.MSPHost(WS, provider="meta")
                    sids.clear()
                    log({"marker": "host-restart", "condition": "direct",
                         "phase": "B", "ok": host.alive(),
                         "ts": time.time()})
                if skey not in sids:
                    sids[skey] = fresh_sid(host, WS)
                r = do_turn(host, sids[skey], prompt)
                r.update({"condition": "direct", "phase": "B",
                          "turn": tag, "session": skey,
                          "prompt": prompt, "ts": time.time()})
                log(r)
                print(f"{tag}: terminal={r['terminal']} lat={r['turn_latency_ms']}ms "
                      f"appr={len(r['approvals'])} invalid={r['invalid']}",
                      flush=True)
            for sid in sids.values():
                try:
                    host.leave_session(sid)
                except Exception:
                    pass
    finally:
        try:
            host.stop()
        except Exception:
            pass
    print("direct phase", phase, "done")


if __name__ == "__main__":
    main()
