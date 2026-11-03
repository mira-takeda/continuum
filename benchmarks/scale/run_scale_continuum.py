"""SCALE continuum driver (frozen server copy over HTTP + approval policy).

Usage:
  run_scale_continuum.py test1
  run_scale_continuum.py test2 <1|2|3|R>
Appends to raw_turns.jsonl; skips already-recorded tags (no resends).
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
import scale_common as common

BENCH = HERE
RAW = os.path.join(BENCH, "raw_turns.jsonl")
PORT = 8161
TURN_TIMEOUT = 200

T1 = json.load(open(os.path.join(BENCH, "scale_test1.json"), encoding="utf-8"))
T2 = json.load(open(os.path.join(BENCH, "scale_test2.json"), encoding="utf-8"))
T1_PROBES_AFTER = {}
for p in T1["probes"]:
    T1_PROBES_AFTER.setdefault(p["after"], []).append(p)
PROJECTS = ["Acorn", "Birch", "Cedar", "Dogwood", "Elm"]


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
                "agents_present": ag}

    def last_usage(self):
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
        chats = [x for x in lines if x.get("purpose") == "chat-msp"]
        return chats[-1] if chats else None


def snap_marker(condition, test, wsroot, label):
    log({"marker": "snapshot", "condition": condition, "test": test,
         "label": label, "files": common.snapshot_full(wsroot),
         "ts": time.time()})


def run_test1():
    copy = os.path.join(BENCH, "scale-test1", "continuum-copy")
    os.makedirs(os.path.join(copy, "bench-files-scale"), exist_ok=True)
    done = load_done("continuum", "test1")
    srv = Server(copy)
    srv.start()
    print("server up", flush=True)
    try:
        snap_marker("continuum", "test1", copy, "start")
        for t in T1["turns"]:
            tag = f"T1-{t['n']:04d}"
            if tag not in done:
                r = srv.chat("t1main", t["text"], copy)
                r["usage"] = common.norm_continuum(srv.last_usage())
                r.update({"condition": "continuum", "test": "test1",
                          "tag": tag, "kind": t["kind"], "ref": t.get("ref"),
                          "session": "t1main", "prompt": t["text"],
                          "ts": time.time()})
                log(r)
                done.add(tag)
                print(f"{tag} {t['kind']}: term={r['terminal']} "
                      f"lat={r['turn_latency_ms']}ms "
                      f"tok={r['usage']['total_tokens']} "
                      f"appr={len(r['approvals'])} md={r['md_count']}",
                      flush=True)
            if t["n"] in T1_PROBES_AFTER:
                for p in T1_PROBES_AFTER[t["n"]]:
                    if p["id"] not in done:
                        r = srv.chat("t1main", p["text"], copy)
                        r["usage"] = common.norm_continuum(srv.last_usage())
                        r.update({"condition": "continuum", "test": "test1",
                                  "tag": p["id"], "kind": "probe",
                                  "ref": p["ref"], "session": "t1main",
                                  "prompt": p["text"], "ts": time.time()})
                        log(r)
                        done.add(p["id"])
                        print(f"{p['id']}: term={r['terminal']} "
                              f"reply={r['reply'][:80]!r}", flush=True)
                snap_marker("continuum", "test1", copy, f"after-{t['n']}")
        snap_marker("continuum", "test1", copy, "end")
    finally:
        srv.stop()
        print("server stopped", flush=True)
    print("continuum test1 done")


def ensure_projects(srv):
    for name in PROJECTS:
        pdir = os.path.join(srv.copy, "bench-proj", name.lower())
        os.makedirs(pdir, exist_ok=True)
        st, body = srv.api("/api/projects/register",
                           {"name": name, "path": pdir})
        if st != 200:
            print(f"register {name}: http={st} {body}", flush=True)


def run_test2(level):
    copy = os.path.join(BENCH, "scale-test2", "continuum-copy")
    done = load_done("continuum", "test2")
    srv = Server(copy)
    srv.start()
    print("server up", flush=True)
    try:
        ensure_projects(srv)
        if level in ("1", "2", "3"):
            lv = int(level)
            teach = [b for b in T2["teaching"] if b["level"] == lv]
            for b in teach:
                tag = f"T2-teach-{b['seq']:03d}"
                if tag in done:
                    continue
                if b["project"]:
                    st, body = srv.api("/api/projects/activate",
                                       {"session_id": f"teach-L{lv}",
                                        "project_id": b["project"]})
                    if st != 200:
                        log({"condition": "continuum", "test": "test2",
                             "tag": tag, "kind": "teaching", "batch": b["batch"],
                             "level": lv, "session": f"teach-L{lv}",
                             "prompt": b["text"], "reply": "",
                             "terminal": None,
                             "error": f"activate-http-{st}: {body}",
                             "timeout": False, "cancelled": False,
                             "started_turn_id": None, "done_turn_id": None,
                             "done_reason": None, "done_duration_ms": None,
                             "approvals": [], "questions_answered": 0,
                             "tool_events": 0, "integrity_alert": None,
                             "invalid": "activate-failed",
                             "usage": common.norm_continuum(None),
                             "first_visible_ms": None, "turn_latency_ms": 0,
                             "md_count": 0, "agents_present": False,
                             "ts": time.time()})
                        print(f"{tag}: ACTIVATE FAILED", flush=True)
                        done.add(tag)
                        continue
                r = srv.chat(f"teach-L{lv}", b["text"], copy)
                r["usage"] = common.norm_continuum(srv.last_usage())
                r.update({"condition": "continuum", "test": "test2",
                          "tag": tag, "kind": "teaching", "batch": b["batch"],
                          "level": lv, "session": f"teach-L{lv}",
                          "prompt": b["text"], "ts": time.time()})
                log(r)
                done.add(tag)
                print(f"{tag} {b['batch']}: term={r['terminal']} "
                      f"lat={r['turn_latency_ms']}ms md={r['md_count']}",
                      flush=True)
            snap_marker("continuum", "test2", copy, f"taught-L{lv}")
            for p in [x for x in T2["probes"] if x["level"] == lv]:
                if p["id"] in done:
                    continue
                if p["project"]:
                    st, _ = srv.api("/api/projects/activate",
                                    {"session_id": "probe-" + p["id"],
                                     "project_id": p["project"]})
                    if st != 200:
                        print(f"{p['id']}: ACTIVATE FAILED", flush=True)
                        continue
                r = srv.chat("probe-" + p["id"], p["text"], copy)
                r["usage"] = common.norm_continuum(srv.last_usage())
                r.update({"condition": "continuum", "test": "test2",
                          "tag": p["id"], "kind": "probe", "level": lv,
                          "session": "probe-" + p["id"], "prompt": p["text"],
                          "ts": time.time()})
                log(r)
                done.add(p["id"])
                print(f"{p['id']}: term={r['terminal']} "
                      f"reply={r['reply'][:80]!r}", flush=True)
            snap_marker("continuum", "test2", copy, f"probed-L{lv}")
        else:
            srv.stop()
            print("server stopped for restart", flush=True)
            srv.start()
            print("server restarted", flush=True)
            log({"marker": "server-restart", "condition": "continuum",
                 "test": "test2", "ok": True, "ts": time.time()})
            ensure_projects(srv)
            for p in [x for x in T2["probes"] if x["level"] == "R"]:
                if p["id"] in done:
                    continue
                if p["project"]:
                    st, _ = srv.api("/api/projects/activate",
                                    {"session_id": "probe-" + p["id"],
                                     "project_id": p["project"]})
                    if st != 200:
                        print(f"{p['id']}: ACTIVATE FAILED", flush=True)
                        continue
                r = srv.chat("probe-" + p["id"], p["text"], copy)
                r["usage"] = common.norm_continuum(srv.last_usage())
                r.update({"condition": "continuum", "test": "test2",
                          "tag": p["id"], "kind": "probe", "level": "R",
                          "session": "probe-" + p["id"], "prompt": p["text"],
                          "ts": time.time()})
                log(r)
                done.add(p["id"])
                print(f"{p['id']}: term={r['terminal']} "
                      f"reply={r['reply'][:80]!r}", flush=True)
            snap_marker("continuum", "test2", copy, "restart-probed")
    finally:
        srv.stop()
        print("server stopped", flush=True)
    print("continuum test2", level, "done")


if __name__ == "__main__":
    if sys.argv[1] == "test1":
        run_test1()
    else:
        run_test2(sys.argv[2])
