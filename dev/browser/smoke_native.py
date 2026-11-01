"""Native-capability parity check. DEV TOOL ONLY - not product runtime.

Proves Muse through Continuum can do ordinary file work with its NATIVE
tools: the prompt deliberately matches NO pre-turn project pattern, so any
result must come from Muse itself (file tools + native approvals).

Usage (from repo root, env activated):
  python dev/browser/smoke_native.py [--no-start]

Exit codes: 0 pass, 1 failure, 2 degraded (backend/Muse unavailable).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "dev", "browser"))
from smoke import poll_ready  # noqa: E402  (dev-only reuse)

NAME = "NativeProbe"
MESSAGE = ("Make a folder called NativeProbe in Documents and put a hello.txt "
           "file in it with exactly this text: native-ok")
TURN_WAIT_S = 300


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8137")
    ap.add_argument("--port", type=int, default=8137)
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--shots", default=os.path.join(REPO, "dev", "browser", "shots"))
    args = ap.parse_args()
    url = args.url.rstrip("/")
    os.makedirs(args.shots, exist_ok=True)

    docs = os.path.join(os.path.expanduser("~"), "Documents")
    dest = os.path.join(docs, NAME)
    report = {"url": url, "console_errors": [], "page_errors": [],
              "failed_requests": [], "screenshots": []}
    server_proc, server_log, external = None, None, False

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install -r dev/browser/requirements.txt")
        return 1

    sys.path.insert(0, REPO)
    from memory import projects as proj_mod
    assert proj_mod.detect_project_request(MESSAGE) == [], "prompt must avoid pre-turn"
    prev_active = proj_mod.active_for(REPO, "demo")
    shutil.rmtree(dest, ignore_errors=True)

    if not args.no_start:
        server_log = open(os.path.join(args.shots, "server_native.log"), "w",
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
        try:
            page.goto(url + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#page .chat-layout", timeout=15000)
            page.wait_for_timeout(3000)
            page.fill("#prompt", MESSAGE)
            page.click("#send")
            t0 = time.time()
            approved = []
            while time.time() - t0 < TURN_WAIT_S:
                cards = page.locator(".approval-card")
                if cards.count():
                    txt = cards.first.inner_text()
                    if NAME not in txt and "Documents" not in txt:
                        report["unexpected_approval"] = txt[:200]
                        fail = True
                        break
                    btns = cards.first.locator("button")
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
                    approved.append(btns.nth(pick).inner_text() if pick is not None else "?")
                    if pick is None:
                        fail = True
                        break
                    btns.nth(pick).click()
                    page.wait_for_timeout(1500)
                    continue
                if page.eval_on_selector("#stop", "e => e.classList.contains('hidden')"):
                    break
                time.sleep(2)
            report["approvals_auto_allowed"] = approved
            reply = page.locator(".message-row.assistant .message-text").last.inner_text()
            report["reply_head"] = reply[:300]
            page.screenshot(path=os.path.join(args.shots, "n1-native.png"))
            report["screenshots"].append("n1-native.png")

            checks = {}
            hello = os.path.join(dest, "hello.txt")
            try:
                with open(hello, encoding="utf-8") as f:
                    checks["file_exact"] = f.read() == "native-ok"
            except Exception:
                checks["file_exact"] = False
            checks["not_registered"] = NAME not in proj_mod.list_projects(REPO)
            checks["active_unchanged"] = proj_mod.active_for(REPO, "demo") == prev_active
            checks["reply_nonempty"] = bool(reply.strip())
            report["checks"] = checks
            if not all(checks.values()):
                fail = True
            if report["console_errors"] or report["page_errors"] or report["failed_requests"]:
                fail = True
            report["verdict"] = "pass" if not fail else "fail"
            print(json.dumps(report, indent=1, ensure_ascii=True))
        finally:
            shutil.rmtree(dest, ignore_errors=True)
            try:
                with open(os.path.join(args.shots, "summary_native.json"), "w",
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
