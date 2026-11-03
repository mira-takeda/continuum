"""FINAL150 continuum driver (server copy over HTTP + approval policy).

Adapted from longval run_val_continuum.py: first 150 turns, probes
after 10/50/100/125/150, one Refresh Memory + R2 probes at 100,
isolated tree copy, port 8169. Additionally
captures per-turn delivery stats (bootstrap/delta), native sid, and
delivery-fallback events. Appends to raw_turns.jsonl; skips
already-recorded tags (no resends).
"""
import http.client
import json
import os
import re
import select
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import val_common as common

BENCH = HERE
RAW = os.path.join(BENCH, "raw_turns.jsonl")
PORT = 8169
TURN_TIMEOUT = 200
MAX_N = 150

T1 = json.load(open(os.path.join(BENCH, "scale_test1.json"), encoding="utf-8"))
T1_PROBES_AFTER = {}
for p in T1["probes"]:
    if p["after"] in (10, 50, 100):
        T1_PROBES_AFTER.setdefault(p["after"], []).append(p)
NEW = json.load(open(os.path.join(BENCH, "final150_probes.json"),
                     encoding="utf-8"))
for p in NEW["probes"]:
    assert p["after"] in (125, 150), p["id"]
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


class Server:
    def __init__(self, copy_dir):
        self.copy = copy_dir
        self.proc = None
        self.token = None
        self.usage_off = 0

    def port_open(self):
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=2)
            s.close()
            return True
        except OSError:
            return False

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "server.py", "--port", str(PORT)],
            cwd=self.copy, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < 120:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
                conn.request("GET", "/api/ui/bootstrap")
                if conn.getresponse().status == 200:
                    break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("server did not boot in 120s")
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        conn.request("GET", "/")
        html = conn.getresponse().read().decode("utf-8")
        m = re.search(r'name="continuum-token" content="([^"]+)"', html)
        assert m and "__CONTINUUM" not in m.group(1), "no token in page"
        self.token = m.group(1)
        up = os.path.join(self.copy, "data", "usage.jsonl")
        self.usage_off = os.path.getsize(up) if os.path.exists(up) else 0

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except Exception:
                self.proc.kill()
            self.proc = None
        t0 = time.time()
        while self.port_open() and time.time() - t0 < 30:
            time.sleep(1)
        assert not self.port_open(), "port still open after stop"

    def api(self, path, obj=None):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        body = json.dumps(obj or {}).encode()
        conn.request("POST" if obj is not None else "GET", path, body,
                     {"Content-Type": "application/json",
                      "X-Continuum-Token": self.token})
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def answer_approval(self, session_id, fr):
        ch, label, _ = common.decide_approval(fr, self.copy)
        rec = {"id": fr.get("approvalId"), "tool": fr.get("toolName"),
               "subject": json.dumps(fr.get("subject"))[:300],
               "decision": label, "choice": ch}
        if ch is None:
            rec["rpc"] = "no-choice"
            return rec, "approval-no-choice"
        try:
            st, _ = self.api("/api/approve", {
                "session_id": session_id, "approvalId": fr.get("approvalId"),
                "choiceId": ch, "requirementId": fr.get("requirementId")})
            rec["rpc"] = f"http-{st}"
            if st != 200:
                return rec, "approval-rpc-failed"
        except Exception as e:
            rec["rpc"] = f"error: {e}"[:200]
            return rec, "approval-rpc-failed"
        return rec, None

    def chat(self, session_id, message, wsroot, timeout=TURN_TIMEOUT):
        t0 = time.time()
        texts, first_visible = [], None
        terminal, error = None, None
        started_id, done_id, done_reason, done_dur = None, None, None, None
        approvals, questions_n, tool_n, invalid = [], 0, 0, None
        delivery, native_sid, fallbacks = None, None, []
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
            s.setblocking(False)
            body = json.dumps({"session_id": session_id,
                               "message": message}).encode()
            req = (b"POST /api/chat_stream HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                   b"Content-Type: application/json\r\nContent-Length: "
                   + str(len(body)).encode() + b"\r\nX-Continuum-Token: "
                   + self.token.encode() + b"\r\nConnection: close\r\n\r\n"
                   + body)
            s.sendall(req)
            buf = b""
            headers_done = False
            while time.time() - t0 < timeout:
                rl, _, _ = select.select([s], [], [], 1.0)
                if not rl:
                    continue
                try:
                    chunk = s.recv(65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                buf += chunk
                if not headers_done:
                    if b"\r\n\r\n" not in buf:
                        continue
                    head, buf = buf.split(b"\r\n\r\n", 1)
                    try:
                        status = int(head.split(b"\r\n", 1)[0].split()[1])
                    except Exception:
                        status = 0
                    headers_done = True
                    if status != 200:
                        error = f"http-{status}"
                        break
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    try:
                        fr = json.loads(line[5:])
                    except Exception:
                        continue
                    t = fr.get("t")
                    if t == "text":
                        if first_visible is None:
                            first_visible = int((time.time() - t0) * 1000)
                        texts.append(fr.get("text", ""))
                    elif t == "started":
                        started_id = fr.get("turn_id")
                        delivery = fr.get("delivery")
                        native_sid = fr.get("session_id")
                    elif t == "delivery-fallback":
                        fallbacks.append(str(fr.get("error", ""))[:200])
                    elif t in ("tool", "activity"):
                        tool_n += 1
                    elif t == "approval":
                        rec, bad = self.answer_approval(session_id, fr)
                        approvals.append(rec)
                        if bad and invalid is None:
                            invalid = bad
                    elif t == "questions":
                        questions_n += 1
                        try:
                            st, _ = self.api("/api/answer", {
                                "session_id": session_id,
                                "userInputId": fr.get("userInputId"),
                                "answers": common.answers_for(
                                    fr.get("questions"))})
                            if st != 200 and invalid is None:
                                invalid = "questions-rpc-failed"
                        except Exception as e:
                            if invalid is None:
                                invalid = f"questions-rpc-failed: {e}"[:200]
                    elif t == "done":
                        terminal = fr.get("terminal", "done")
                        done_id = fr.get("turnId")
                        done_reason = fr.get("reason")
                        done_dur = fr.get("durationMs")
                    elif t == "error":
                        error = fr.get("text", "error")
                        if fr.get("timeout"):
                            error = "server-timeout: " + error[:200]
                if terminal or error:
                    break
            try:
                s.close()
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001 - fail closed
            error = f"chat-transport: {e}"
        lat = int((time.time() - t0) * 1000)
        timed_out = False
        if terminal is None and error is None:
            error = "client-timeout-200s"
            timed_out = True
            try:
                self.api("/api/stop", {"session_id": session_id})
            except Exception:
                pass
        integrity = None
        if terminal is not None and started_id and done_id:
            if done_id != started_id:
                integrity = f"done.turnId {done_id} != started {started_id}"
                invalid = "integrity-alert"
        md, ag = common.snapshot_light(wsroot)
        return {"reply": "".join(texts), "terminal": terminal, "error": error,
                "timeout": timed_out, "cancelled": terminal == "cancelled",
                "started_turn_id": started_id, "done_turn_id": done_id,
                "done_reason": done_reason, "done_duration_ms": done_dur,
                "approvals": approvals, "questions_answered": questions_n,
                "tool_events": tool_n, "integrity_alert": integrity,
                "invalid": invalid, "first_visible_ms": first_visible,
                "turn_latency_ms": lat, "md_count": md,
                "agents_present": ag, "delivery": delivery,
                "native_sid": native_sid,
                "delivery_fallbacks": fallbacks}

    def last_usage(self, purpose="chat-msp"):
        up = os.path.join(self.copy, "data", "usage.jsonl")
        t0 = time.time()
        while time.time() - t0 < 15:
            if os.path.exists(up) and os.path.getsize(up) > self.usage_off:
                break
            time.sleep(0.5)
        if not os.path.exists(up):
            return None
        with open(up, encoding="utf-8") as f:
            f.seek(self.usage_off)
            lines = [json.loads(x) for x in f if x.strip()]
        self.usage_off = os.path.getsize(up)
        chats = [x for x in lines if x.get("purpose") == purpose]
        return chats[-1] if chats else None


def snap_marker(condition, test, wsroot, label):
    log({"marker": "snapshot", "condition": condition, "test": test,
         "label": label, "files": common.snapshot_full(wsroot),
         "ts": time.time()})


def main():
    copy = os.path.join(BENCH, "final150-continuum-copy")
    os.makedirs(os.path.join(copy, "bench-files-scale"), exist_ok=True)
    done = load_done("continuum", "final150")
    srv = Server(copy)
    srv.start()
    print("server up", flush=True)
    try:
        snap_marker("continuum", "final150", copy, "start")
        for t in T1["turns"]:
            if t["n"] > MAX_N:
                continue
            tag = f"T1-{t['n']:04d}"
            if tag not in done:
                r = srv.chat("fmain", t["text"], copy)
                r["usage"] = common.norm_continuum(srv.last_usage())
                r.update({"condition": "continuum", "test": "final150",
                          "tag": tag, "kind": t["kind"], "ref": t.get("ref"),
                          "session": "fmain", "prompt": t["text"],
                          "ts": time.time()})
                log(r)
                done.add(tag)
                dl = r.get("delivery") or {}
                print(f"{tag} {t['kind']}: term={r['terminal']} "
                      f"lat={r['turn_latency_ms']}ms "
                      f"tok={r['usage']['total_tokens']} "
                      f"appr={len(r['approvals'])} md={r['md_count']} "
                      f"dl={dl.get('mode')}/{dl.get('added_chars')}",
                      flush=True)
            if t["n"] in T1_PROBES_AFTER:
                for p in T1_PROBES_AFTER[t["n"]]:
                    if p["id"] not in done:
                        r = srv.chat("fmain", p["text"], copy)
                        r["usage"] = common.norm_continuum(srv.last_usage())
                        r.update({"condition": "continuum", "test": "final150",
                                  "tag": p["id"], "kind": "probe",
                                  "ref": p["ref"], "session": "fmain",
                                  "prompt": p["text"], "ts": time.time()})
                        log(r)
                        done.add(p["id"])
                        print(f"{p['id']}: term={r['terminal']} "
                              f"reply={r['reply'][:80]!r}", flush=True)
            if t["n"] == 100:
                # Labeled product intervention (protocol section 5): one
                # Refresh Memory, then the same 5 probes with -R2 tags.
                rtag = "T1-P100-REFRESH"
                if rtag not in done:
                    t0 = time.time()
                    try:
                        st, body = srv.api("/api/memory/refresh",
                                           {"session_id": "fmain"})
                    except Exception as e:
                        st, body = 0, {"ok": False,
                                       "error": "refresh-rpc: %s" % str(e)[:200]}
                    rlat = int((time.time() - t0) * 1000)
                    ok = (st == 200 and bool((body or {}).get("ok")))
                    log({"condition": "continuum", "test": "final150",
                         "tag": rtag, "kind": "refresh", "session": "fmain",
                         "prompt": "POST /api/memory/refresh",
                         "reply": (body or {}).get("reply", ""),
                         "terminal": "completed" if ok else None,
                         "error": None if ok else str(
                             (body or {}).get("error"))[:200],
                         "timeout": False, "cancelled": False,
                         "started_turn_id": None,
                         "done_turn_id": None, "done_reason": None,
                         "done_duration_ms": None, "approvals": [],
                         "questions_answered": 0, "tool_events": 0,
                         "integrity_alert": None,
                         "invalid": None if ok else "refresh-failed",
                         "usage": common.norm_continuum(
                             srv.last_usage("refresh")),
                         "first_visible_ms": None, "turn_latency_ms": rlat,
                         "md_count": 0, "agents_present": False,
                         "delivery": {"mode": (body or {}).get("mode"),
                                      "added_chars": (body or {}).get("chars"),
                                      "items": (body or {}).get("items"),
                                      "tokens_est": (body or {}).get(
                                          "tokens_est")},
                         "native_sid": (body or {}).get("native_sid"),
                         "delivery_fallbacks": [], "http_status": st,
                         "ts": time.time()})
                    done.add(rtag)
                    print(f"{rtag}: http={st} ok={ok} "
                          f"body={json.dumps(body)[:200]}", flush=True)
                for p in T1_PROBES_AFTER[100]:
                    r2 = p["id"] + "-R2"
                    if r2 not in done:
                        r = srv.chat("fmain", p["text"], copy)
                        r["usage"] = common.norm_continuum(srv.last_usage())
                        r.update({"condition": "continuum", "test": "final150",
                                  "tag": r2, "kind": "probe",
                                  "ref": p["ref"], "session": "fmain",
                                  "prompt": p["text"], "ts": time.time(),
                                  "r2_of": p["id"]})
                        log(r)
                        done.add(r2)
                        print(f"{r2}: term={r['terminal']} "
                              f"reply={r['reply'][:80]!r}", flush=True)
                snap_marker("continuum", "final150", copy, f"after-{t['n']}")
        snap_marker("continuum", "final150", copy, "end")
    finally:
        srv.stop()
        print("server stopped", flush=True)
    print("continuum final150 done")


if __name__ == "__main__":
    main()
