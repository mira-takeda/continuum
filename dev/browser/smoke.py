"""Continuum browser smoke test. DEV TOOL ONLY - never part of product runtime.

Drives the real UI in headless Chromium: console/page/network error capture,
backend-connected check, one chat turn with streamed output, Projects switch,
Memory/Lab views, approval-card detection, screenshots.

Usage (from repo root):
  pip install -r dev/browser/requirements.txt
  python -m playwright install chromium
  python dev/browser/smoke.py
  python dev/browser/smoke.py --no-start --url http://127.0.0.1:8137/

Exit codes: 0 pass, 1 failure, 2 degraded (UI up, backend/Muse unavailable;
frontend-only checks ran and the gap is reported).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHAT_MESSAGE = "Reply with exactly this word: CONTINUUM-SMOKE-OK"
CHAT_WAIT_S = 150


def poll_ready(url, timeout_s):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        try:
            with urllib.request.urlopen(url + "/api/ui/bootstrap", timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1)
    raise RuntimeError(f"server not ready at {url}: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8137")
    ap.add_argument("--port", type=int, default=8137)
    ap.add_argument("--no-start", action="store_true",
                    help="connect to an already-running server instead of starting one")
    ap.add_argument("--shots", default=os.path.join(REPO, "dev", "browser", "shots"))
    ap.add_argument("--message", default=CHAT_MESSAGE)
    args = ap.parse_args()
    url = args.url.rstrip("/")
    os.makedirs(args.shots, exist_ok=True)

    report = {"url": url, "console_errors": [], "page_errors": [],
              "failed_requests": [], "screenshots": [], "skipped": []}
    server_proc, server_log, external = None, None, False

    def shot(name):
        p = os.path.join(args.shots, name)
        page.screenshot(path=p)
        report["screenshots"].append(name)
        return p

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install -r dev/browser/requirements.txt")
        return 1

    # --- server ---
    if not args.no_start:
        server_log = open(os.path.join(args.shots, "server.log"), "w", encoding="utf-8")
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
        external = True  # ours died; a live server already owns the port
        report["server"] = "external (already running, left untouched)"
    else:
        report["server"] = "external" if args.no_start else "started by smoke"
    report["backend_connected"] = bool(boot.get("connected"))
    report["backend_model"] = boot.get("model")

    # --- browser ---
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
            page.wait_for_selector("#page .chat-layout, #page .alt-view", timeout=15000)
            # Let bootstrap settle: chip flips to "en linea" or setup banner shows.
            ui_connected = False
            for _ in range(20):
                rt = page.locator("#runtimeText").inner_text()
                banner_hidden = page.eval_on_selector(
                    "#setupBanner", "e => e.classList.contains('hidden')")
                if "n l" in rt.replace("\u00ed", "i") and banner_hidden:
                    ui_connected = True
                    break
                time.sleep(0.5)
            report["ui_connected"] = ui_connected
            report["runtime_text"] = page.locator("#runtimeText").inner_text()
            shot("01-loaded.png")

            if not ui_connected:
                for view in ["projects", "history", "memory", "lab"]:
                    page.click(f'.nav-link[data-view="{view}"]')
                    page.wait_for_timeout(800)
                    shot(f"degraded-{view}.png")
                report["skipped"] = ["chat", "project-activate", "approval"]
                report["verdict"] = ("degraded: UI loads, backend/Muse unavailable; "
                                     "frontend-only views captured")
                print(json.dumps(report, indent=1, ensure_ascii=True))
                return 2

            # --- one chat turn ---
            page.fill("#prompt", args.message)
            page.click("#send")
            assistant = page.locator(".message-row.assistant .message-text").last
            streamed = ""
            t0 = time.time()
            while time.time() - t0 < CHAT_WAIT_S:
                streamed = assistant.inner_text()
                if streamed.strip():
                    break
                time.sleep(1)
            # Let the stream finish: stop button hides when the turn ends.
            t0 = time.time()
            while time.time() - t0 < 60:
                if page.eval_on_selector("#stop", "e => e.classList.contains('hidden')"):
                    break
                time.sleep(1)
            streamed = assistant.inner_text()
            report["chat_chars"] = len(streamed)
            report["chat_head"] = streamed[:200]
            shot("02-chat.png")
            if not streamed.strip():
                report["verdict"] = "fail: empty assistant reply"
                shot("fail.png")
                print(json.dumps(report, indent=1, ensure_ascii=True))
                return 1

            # --- approval card, if one surfaced ---
            cards = page.locator(".approval-card")
            if cards.count():
                report["approval_seen"] = True
                report["approval_text"] = cards.first.inner_text()[:300]
                shot("03-approval.png")
                report["approval_note"] = ("card captured; no decision clicked "
                                           "(smoke never auto-approves)")
            else:
                report["approval_seen"] = False

            # --- projects: open view, activate first project if any ---
            page.click('.nav-link[data-view="projects"]')
            page.wait_for_timeout(1000)
            shot("04-projects.png")
            openers = page.locator("button", has_text="Abrir proyecto")
            if openers.count():
                openers.first.click()
                page.wait_for_timeout(1000)
                chip_hidden = page.eval_on_selector(
                    "#projectChip", "e => e.classList.contains('hidden')")
                report["project_activated"] = not chip_hidden
                report["project_chip"] = page.locator("#projectChipName").inner_text()
                shot("05-project-active.png")
            else:
                report["project_activated"] = False
                report["project_note"] = "no projects registered; nothing to activate"

            # --- memory + lab ---
            for view in ["memory", "lab"]:
                page.click(f'.nav-link[data-view="{view}"]')
                page.wait_for_timeout(1000)
                shot(f"06-{view}.png")
            report["views_ok"] = True

            bad = (report["console_errors"] or report["page_errors"]
                   or report["failed_requests"])
            report["verdict"] = ("fail: console/page/network errors captured"
                                 if bad else "pass")
            print(json.dumps(report, indent=1, ensure_ascii=True))
            return 1 if bad else 0
        finally:
            try:
                with open(os.path.join(args.shots, "summary.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(report, f, indent=1, ensure_ascii=True)
            except Exception:
                pass
            browser.close()
            if server_proc is not None and not external and server_proc.poll() is None:
                server_proc.terminate()
            if server_log is not None:
                server_log.close()


if __name__ == "__main__":
    sys.exit(main())
