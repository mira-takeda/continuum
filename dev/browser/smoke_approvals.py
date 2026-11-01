"""Approval-explanation smoke. DEV TOOL ONLY - not product runtime.

Drives two shell turns through the real UI + backend + Muse:
1. mapped cmdlet (Get-ChildItem) -> explanation shown, byte-identical
   technical details, Allow executes (known entry in reply);
2. unmapped command (Get-Date) -> fallback explanation shown, Deny
   prevents execution.
Also asserts no /api calls happen merely to render an explanation.

Usage (from repo root, env activated):
  python dev/browser/smoke_approvals.py [--no-start]

Exit codes: 0 pass, 1 failure, 2 degraded (backend/Muse unavailable).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "dev", "browser"))
from smoke import poll_ready  # noqa: E402  (dev-only reuse)

T1 = ("Run this exact PowerShell command via your shell tool and nothing else: "
      "Get-ChildItem 'C:\\Users\\test\\Documents'. Then list the names you see.")
T2 = ("Run this exact PowerShell command via your shell tool: "
      "Get-Date -Format yyyy-MM-dd. Reply with ONLY the command output, nothing else.")
TURN_WAIT_S = 300
CARD_WAIT_S = 180


def approval_frames(sse_text):
    out = []
    for chunk in (sse_text or "").split("\n\n"):
        for line in chunk.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                f = json.loads(line[5:].strip())
            except Exception:
                continue
            if f.get("t") == "approval":
                out.append(f)
    return out


def canned_turn(cmd, approval_id, reply_text):
    """SSE bytes for one stubbed turn; explanation by the REAL translator."""
    sys.path.insert(0, REPO)
    from memory import approvals as ap
    frame = {"t": "approval", "approvalId": approval_id,
             "requirementId": "rq-" + approval_id, "toolName": None,
             "subject": {"kind": "shell", "command": cmd, "stages": [cmd]},
             "choices": [
                 {"choiceId": "c-allow", "label": "Allow once",
                  "decision": "allow", "acceptsFeedback": False},
                 {"choiceId": "c-deny", "label": "Reject",
                  "decision": "deny", "acceptsFeedback": False}],
             "rawArgs": ""}
    frame["explanation"] = ap.explain_approval(frame)
    parts = [json.dumps({"t": "started", "session_id": "stub",
                         "opened": [], "paths": []}),
             json.dumps(frame),
             json.dumps({"t": "text", "text": reply_text}),
             json.dumps({"t": "done", "terminal": "completed"})]
    return "".join("data: " + p + "\n\n" for p in parts), frame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8137")
    ap.add_argument("--port", type=int, default=8137)
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--stub", action="store_true",
                    help="stub chat_stream/approve transport (real UI + real "
                         "translator; for machines without shell sandbox setup)")
    ap.add_argument("--shots", default=os.path.join(REPO, "dev", "browser", "shots"))
    args = ap.parse_args()
    url = args.url.rstrip("/")
    os.makedirs(args.shots, exist_ok=True)

    report = {"url": url, "console_errors": [], "page_errors": [],
              "failed_requests": [], "screenshots": [], "turns": {}}
    server_proc, server_log, external = None, None, False

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install -r dev/browser/requirements.txt")
        return 1

    if not args.no_start:
        server_log = open(os.path.join(args.shots, "server_approvals.log"), "w",
                          encoding="utf-8")
        server_proc = subprocess.Popen(
            [sys.executable, "-u", "server.py", "--port", str(args.port)],
            cwd=REPO, stdout=server_log, stderr=subprocess.STDOUT)
    try:
        boot = poll_ready(url, 45)
    except RuntimeError as e:
        report["fatal"] = str(e)
        print(json.dumps(report, indent=1, ensure_ascii=True))
        return 2
    if server_proc is not None and server_proc.poll() is not None:
        external = True
    if not boot.get("connected"):
        report["fatal"] = "backend reports Muse unavailable"
        print(json.dumps(report, indent=1, ensure_ascii=True))
        return 2

    fail = False
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: report["console_errors"].append(m.text[:300])
                if m.type == "error" else None)
        page.on("pageerror", lambda e: report["page_errors"].append(str(e)[:300]))
        page.on("requestfailed",
                lambda r: report["failed_requests"].append(r.url[:200]))
        api_calls = []
        page.on("request", lambda r: api_calls.append(r.url)
                if "/api/" in r.url else None)
        sse_bodies = []
        page.on("response", lambda r: sse_bodies.append(r.text())
                if "/api/chat_stream" in r.url else None)

        def turn(msg, tag, expect_expl, decision, shot):
            """Send one turn; handle the first approval card; return details."""
            nonlocal fail
            det = {"reply": ""}
            base_cards = page.locator(".approval-card").count()
            calls_before = len(api_calls)
            page.fill("#prompt", msg)
            page.click("#send")
            t0 = time.time()
            while page.locator(".approval-card").count() == base_cards:
                if page.eval_on_selector("#stop", "e => e.classList.contains('hidden')"):
                    det["error"] = "no approval card surfaced"
                    det["reply"] = page.locator(
                        ".message-row.assistant .message-text").last.inner_text()[:300]
                    return det
                if time.time() - t0 > CARD_WAIT_S:
                    det["error"] = "timeout waiting for approval card"
                    return det
                time.sleep(1)
            card = page.locator(".approval-card").last
            det["expl"] = card.locator(".expl-wants").inner_text() if \
                card.locator(".expl-wants").count() else None
            det["expl_ok"] = bool(det["expl"]) and expect_expl in det["expl"]
            calls_at_card = len(api_calls)
            page.wait_for_timeout(1500)
            det["api_calls_while_displayed"] = len(api_calls) - calls_at_card
            page.screenshot(path=os.path.join(args.shots, shot))
            report["screenshots"].append(shot)
            if decision == "allow":
                btns = card.locator("button")
                pick = None
                for i in range(btns.count()):
                    if "once" in btns.nth(i).inner_text().lower():
                        pick = i
                        break
                if pick is None:
                    for i in range(btns.count()):
                        if "allow" in btns.nth(i).inner_text().lower():
                            pick = i
                            break
                det["decision"] = btns.nth(pick).inner_text() if pick is not None else None
            else:
                rej = card.locator("button.reject")
                if rej.count():
                    det["decision"] = rej.first.inner_text()
                    rej.first.click()
                else:
                    det["decision"] = None
                pick = None
            if decision == "allow":
                if pick is None:
                    det["error"] = "no allow button"
                    return det
                btns.nth(pick).click()
            elif det["decision"] is None:
                det["error"] = "no deny button"
                return det
            t0 = time.time()
            while time.time() - t0 < TURN_WAIT_S:
                if page.eval_on_selector("#stop", "e => e.classList.contains('hidden')"):
                    break
                time.sleep(2)
            det["reply"] = page.locator(
                ".message-row.assistant .message-text").last.inner_text()
            det["api_calls_total"] = len(api_calls) - calls_before
            return det

        try:
            page.goto(url + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#page .chat-layout", timeout=15000)
            page.wait_for_selector("#runtimeChip.connected", timeout=30000)
            page.wait_for_timeout(1000)
            stub_frames = []
            if args.stub:
                sse1, f1 = canned_turn(
                    "Get-ChildItem 'C:\\Users\\test\\Documents'",
                    "ap-stub-1", "Stub reply one.")
                sse2, f2 = canned_turn(
                    "Get-Date -Format yyyy-MM-dd", "ap-stub-2", "Stub reply two.")
                stub_frames = [f1, f2]
                page.evaluate(
                    """(sse) => {
                        window.__sseQueue = sse.slice();
                        window.__approveCalls = [];
                        const orig = window.fetch.bind(window);
                        window.fetch = async (u, o) => {
                          const s = String(u);
                          if (s.includes('/api/chat_stream'))
                            return new Response(window.__sseQueue.shift() || '',
                              {headers: {'Content-Type': 'text/event-stream'}});
                          if (s.includes('/api/approve')) {
                            window.__approveCalls.push(JSON.parse(o.body));
                            return new Response(JSON.stringify({ok: true}),
                              {headers: {'Content-Type': 'application/json'}});
                          }
                          return orig(u, o);
                        };
                      }""", [sse1, sse2])

            d1 = turn(T1, "list", "List files", "allow", "a1-card.png")
            report["turns"]["allow_list"] = {k: (v[:300] if isinstance(v, str) else v)
                                             for k, v in d1.items()}
            if args.stub:
                ok1 = (not d1.get("error") and d1.get("expl_ok") and
                       d1.get("api_calls_while_displayed") == 0 and
                       "Stub reply one." in d1.get("reply", ""))
            else:
                ok1 = (not d1.get("error") and d1.get("expl_ok") and
                       d1.get("api_calls_while_displayed") == 0 and
                       "Cablerios" in d1.get("reply", ""))
            if not ok1:
                fail = True

            d2 = turn(T2, "date", "Technical operation", "deny", "a2-card.png")
            report["turns"]["deny_date"] = {k: (v[:300] if isinstance(v, str) else v)
                                            for k, v in d2.items()}
            if args.stub:
                ok2 = (not d2.get("error") and d2.get("expl_ok") and
                       d2.get("api_calls_while_displayed") == 0 and
                       "Stub reply two." in d2.get("reply", ""))
            else:
                ok2 = (not d2.get("error") and d2.get("expl_ok") and
                       d2.get("api_calls_while_displayed") == 0 and
                       not re.search(r"\d{4}-\d{2}-\d{2}", d2.get("reply", "")))
            if not ok2:
                fail = True

            # Byte-identity: UI technical details == SSE frame, compared in-page.
            frames = list(stub_frames) if args.stub else []
            for body in sse_bodies:
                frames += approval_frames(body)
            if args.stub:
                calls = page.evaluate("() => window.__approveCalls || []")
                report["approve_calls"] = calls
                want = [("ap-stub-1", "c-allow"), ("ap-stub-2", "c-deny")]
                got = [(c.get("approvalId"), c.get("choiceId")) for c in calls]
                if got != want:
                    fail = True
            report["approval_frames"] = len(frames)
            ident = []
            cards = page.locator(".approval-card")
            for i in range(min(len(frames), cards.count())):
                f = frames[i]
                r = page.evaluate(
                    """(a) => {
                        const s = JSON.stringify(a.subj, null, 1)
                          + (a.raw ? '\\nargs: ' + a.raw : '');
                        const els = document.querySelectorAll('.approval-card .tech-details pre');
                        const pre = els[a.i] ? els[a.i].textContent : null;
                        return {match: s === pre, frameLen: s.length,
                                preLen: pre === null ? -1 : pre.length};
                      }""",
                    {"subj": f.get("subject") or {}, "raw": f.get("rawArgs") or "",
                     "i": i})
                ident.append(r)
            report["byte_identity"] = ident
            if not ident or not all(r.get("match") for r in ident):
                fail = True

            if report["console_errors"] or report["page_errors"] or report["failed_requests"]:
                fail = True
            blob = json.dumps(report.get("turns", {}))
            if "no approval card surfaced" in blob and "setup_required" in blob:
                report["env_blocker"] = (
                    "shell approvals cannot surface: `muse sandbox windows check` "
                    "reports setup_required (sandbox users missing; needs elevated "
                    "`muse sandbox windows setup`). Re-run live mode after setup, "
                    "or use --stub.")
            report["verdict"] = "pass" if not fail else "fail"
            print(json.dumps(report, indent=1, ensure_ascii=True))
        finally:
            try:
                with open(os.path.join(args.shots, "summary_approvals.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(report, f, indent=1, ensure_ascii=True)
            except Exception:
                pass
            browser.close()
            if server_proc is not None and not external and server_proc.poll() is None:
                server_proc.terminate()
            if server_log is not None:
                server_log.close()
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
