"""CONTINUUM V2 driver: frozen server copy over HTTP + approval handling.

Usage: run_continuum2.py <phase:A|B>  (appends to raw_results_v2.jsonl)
Prompts copied verbatim from benchmark_protocol_v2.md §8/§9.
"""
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common2 as common

BENCH = os.path.dirname(os.path.abspath(__file__))
COPY = os.path.join(BENCH, "continuum-copy-v2")
RAW = os.path.join(BENCH, "raw_results_v2.jsonl")
PORT = 8161
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

# (step_tag, session_key, project_or_None, prompt)
PART_B = [
    ("B1teach", "cB1t", None, "A general preference for all our chats: when you list options for me, always number them instead of using bullets. Acknowledge briefly."),
    ("B1", "cB1p", None, "Give me three options for a weekend hike."),
    ("B2setup1", "cB2s", "Kestrel", "Decision one for the Kestrel birdhouse: it will be painted teal."),
    ("B2setup2", "cB2s", "Kestrel", "Decision two for the Kestrel birdhouse: the entrance hole faces east."),
    ("B2", "cB2p", "Kestrel", "What color will the Kestrel birdhouse be, and which way does the entrance hole face?"),
    ("B3corr", "cB3c", "Kestrel", "Correction for Kestrel: paint it terracotta, not teal."),
    ("B3", "cB3p", "Kestrel", "What color will the Kestrel birdhouse be now?"),
    ("B4setup", "cB4s", "Meridian", "For the Meridian birdhouse: it will be painted navy."),
    ("B4", "cB4p", "Kestrel", "What color is the Kestrel birdhouse?"),
    ("B5", "cB5", "Meridian", "Give me three options for birdseed."),
    ("B6", "cB6", None, "Compare the Kestrel and Meridian birdhouses: what color is each?"),
    ("B7a", "cB7", "Kestrel", "Give me three options for a birdhouse camera."),
    ("B7b", "cB7", "Kestrel", "What color is the Kestrel birdhouse now?"),
    ("B7c", "cB7", "Kestrel", "What color is the Meridian birdhouse?"),
]


def log(rec):
    with open(RAW, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class Server:
    def __init__(self):
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
            [sys.executable, "-u", "server.py", "--port", str(PORT)], cwd=COPY,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < 120:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
                conn.request("GET", "/api/ui/bootstrap")
                r = conn.getresponse()
                if r.status == 200:
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
        up = os.path.join(COPY, "data", "usage.jsonl")
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

    def api(self, path, obj=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        body = json.dumps(obj or {}).encode()
        conn.request("POST" if obj is not None else "GET", path, body,
                     {"Content-Type": "application/json",
                      "X-Continuum-Token": self.token})
        r = conn.getresponse()
        data = r.read()
        return (r.status, data) if raw else (r.status, json.loads(data or b"{}"))

    def answer_approval(self, session_id, fr):
        ch, label, _ = common.decide_approval(fr, COPY)
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

    def chat(self, session_id, message, timeout=TURN_TIMEOUT):
        t0 = time.time()
        texts, first_visible = [], None
        terminal, error = None, None
        started_id, done_id, done_reason, done_dur = None, None, None, None
        approvals, questions_n, invalid = [], 0, None
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
            import select
            buf = b""
            headers_done = False
            status = None
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
        except Exception as e:  # noqa: BLE001 - fail closed per protocol
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
        return {"reply": "".join(texts), "terminal": terminal, "error": error,
                "timeout": timed_out,
                "cancelled": terminal == "cancelled",
                "started_turn_id": started_id, "done_turn_id": done_id,
                "done_reason": done_reason, "done_duration_ms": done_dur,
                "approvals": approvals, "questions_answered": questions_n,
                "integrity_alert": integrity, "invalid": invalid,
                "first_visible_ms": first_visible, "turn_latency_ms": lat}

    def last_usage(self):
        up = os.path.join(COPY, "data", "usage.jsonl")
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
        chats = [x for x in lines if x.get("purpose") == "chat-msp"]
        return chats[-1] if chats else None


def main():
    phase = sys.argv[1]
    srv = Server()
    srv.start()
    print("server up", flush=True)
    try:
        if phase == "A":
            bf = os.path.join(COPY, "bench-files")
            os.makedirs(bf, exist_ok=True)
            with open(os.path.join(bf, "notes-seed.txt"), "w") as f:
                f.write("shelf screws\nwall anchors\nsandpaper\n")
            for i, prompt in enumerate(PART_A, 1):
                r = srv.chat("benchA", prompt)
                r["usage"] = common.norm_continuum(srv.last_usage())
                r.update({"condition": "continuum", "phase": "A",
                          "turn": f"A{i}", "session": "benchA",
                          "prompt": prompt, "ts": time.time()})
                log(r)
                print(f"A{i}: terminal={r['terminal']} lat={r['turn_latency_ms']}ms "
                      f"tok={r['usage']['total_tokens']} appr={len(r['approvals'])} "
                      f"err={r['error']}", flush=True)
        else:
            for name in ("Kestrel", "Meridian"):
                pdir = os.path.join(COPY, "bench-proj", name.lower())
                os.makedirs(pdir, exist_ok=True)
                st, _ = srv.api("/api/projects/register",
                                {"name": name, "path": pdir})
                print(f"register {name}: http={st}", flush=True)
            for tag, skey, proj, prompt in PART_B:
                if tag == "B7a":  # full server restart per protocol
                    srv.stop()
                    print("server stopped for B7", flush=True)
                    srv.start()
                    print("server restarted", flush=True)
                    log({"marker": "server-restart", "condition": "continuum",
                         "phase": "B", "ok": True, "ts": time.time()})
                if proj:
                    st, body = srv.api("/api/projects/activate",
                                       {"session_id": skey, "project_id": proj})
                    if st != 200:
                        log({"condition": "continuum", "phase": "B",
                             "turn": tag, "session": skey, "prompt": prompt,
                             "reply": "", "terminal": None,
                             "error": f"activate-http-{st}: {body}",
                             "timeout": False, "cancelled": False,
                             "started_turn_id": None, "done_turn_id": None,
                             "done_reason": None, "done_duration_ms": None,
                             "approvals": [], "questions_answered": 0,
                             "integrity_alert": None,
                             "invalid": "activate-failed",
                             "usage": common.norm_continuum(None),
                             "first_visible_ms": None,
                             "turn_latency_ms": 0, "ts": time.time()})
                        print(f"{tag}: ACTIVATE FAILED http={st}", flush=True)
                        continue
                r = srv.chat(skey, prompt)
                r["usage"] = common.norm_continuum(srv.last_usage())
                r.update({"condition": "continuum", "phase": "B",
                          "turn": tag, "session": skey, "project": proj,
                          "prompt": prompt, "ts": time.time()})
                log(r)
                print(f"{tag}: terminal={r['terminal']} lat={r['turn_latency_ms']}ms "
                      f"appr={len(r['approvals'])} invalid={r['invalid']} "
                      f"err={r['error']}", flush=True)
    finally:
        srv.stop()
        print("server stopped", flush=True)
    print("continuum phase", phase, "done")


if __name__ == "__main__":
    main()
