"""Bookkeeping-order smoke. DEV TOOL ONLY - not product runtime.

Proves the restored architecture: Muse creates a project folder with NATIVE
tools, and Continuum only registers/activates afterwards. Captures the raw
chat SSE to assert no pre-turn/project-created filesystem action occurred.

Usage (from repo root, env activated):
  python dev/browser/smoke_bookkeeping.py [--no-start]

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

NAME = "NativeTest"
CREATE_MSG = "Create a project called NativeTest in Documents."
DECIDE_MSG = "For the NativeTest project, we decided to use pine instead of oak."
TURN_WAIT_S = 300


def sse_writes_kinds(sse_text):
    kinds = []
    for chunk in (sse_text or "").split("\n\n"):
        for line in chunk.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                f = json.loads(line[5:].strip())
            except Exception:
                continue
            if f.get("t") == "writes":
                kinds += [(w.get("kind"), w.get("name") or w.get("entry_id"))
                          for w in f.get("writes", [])]
    return kinds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8137")
    ap.add_argument("--port", type=int, default=8137)
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--shots", default=os.path.join(REPO, "dev", "browser", "shots"))
    args = ap.parse_args()
    url = args.url.rstrip("/")
    os.makedirs(args.shots, exist_ok=True)

    report = {"url": url, "console_errors": [], "page_errors": [],
              "failed_requests": [], "screenshots": [], "sse_kinds": []}
    server_proc, server_log, external = None, None, False

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install -r dev/browser/requirements.txt")
        return 1

    sys.path.insert(0, REPO)
    from memory import projects as proj_mod
    docs = proj_mod.documents_folder()
    dest = os.path.join(docs, NAME)
    prev_active = proj_mod.active_for(REPO, "demo")
    shutil.rmtree(dest, ignore_errors=True)
    reg = proj_mod.list_projects(REPO)
    if NAME in reg:
        full = json.load(open(os.path.join(REPO, "data", "projects.json"),
                              encoding="utf-8"))
        full.get("projects", {}).pop(NAME, None)
        json.dump(full, open(os.path.join(REPO, "data", "projects.json"), "w",
                             encoding="utf-8"))

    if not args.no_start:
        server_log = open(os.path.join(args.shots, "server_book.log"), "w",
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

    def cleanup():
        try:
            shutil.rmtree(dest, ignore_errors=True)
            full = json.load(open(os.path.join(REPO, "data", "projects.json"),
                                  encoding="utf-8"))
            full.get("projects", {}).pop(NAME, None)
            json.dump(full, open(os.path.join(REPO, "data", "projects.json"), "w",
                                 encoding="utf-8"))
            if prev_active:
                try:
                    proj_mod.activate(REPO, "demo", prev_active)
                except Exception:
                    proj_mod.clear_active(REPO, "demo")
            else:
                proj_mod.clear_active(REPO, "demo")
        except Exception as e:
            report["cleanup_warning"] = str(e)[:200]

    fail = False
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: report["console_errors"].append(m.text[:300])
                if m.type == "error" else None)
        page.on("pageerror", lambda e: report["page_errors"].append(str(e)[:300]))
        page.on("requestfailed",
                lambda r: report["failed_requests"].append(r.url[:200]))
        sse_bodies = []
        page.on("response", lambda r: sse_bodies.append(r.text())
                if "/api/chat_stream" in r.url else None)

        def chat(msg):
            page.fill("#prompt", msg)
            page.click("#send")
            t0 = time.time()
            while time.time() - t0 < TURN_WAIT_S:
                cards = page.locator(".approval-card")
                if cards.count():
                    txt = cards.first.inner_text()
                    if NAME not in txt and "Documents" not in txt:
                        return f"unexpected approval: {txt[:200]}"
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
                    if pick is None:
                        return "no allow button"
                    btns.nth(pick).click()
                    page.wait_for_timeout(1500)
                    continue
                if page.eval_on_selector("#stop", "e => e.classList.contains('hidden')"):
                    break
                time.sleep(2)
            else:
                return "turn timeout"
            return ""

        try:
            page.goto(url + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#page .chat-layout", timeout=15000)
            page.wait_for_timeout(3000)

            err = chat(CREATE_MSG)
            kinds = sse_writes_kinds(sse_bodies[-1] if sse_bodies else "")
            report["sse_kinds"] = kinds
            reg = proj_mod.list_projects(REPO)
            checks = {
                "no_preturn_create": err == "" and "project-created" not in [k for k, _ in kinds],
                "bookkeeping_registered": ("project-registered", NAME) in kinds,
                "folder_exists": os.path.isdir(dest),
                "registered": reg.get(NAME) == dest,
                "context_file": os.path.exists(os.path.join(dest, ".continuum.md")),
                "chip_active": (not page.eval_on_selector(
                    "#projectChip", "e => e.classList.contains('hidden')"))
                and page.locator("#projectChipName").inner_text() == NAME,
            }
            report["create_checks"] = checks
            if err or not all(checks.values()):
                fail = True
            page.screenshot(path=os.path.join(args.shots, "b1-created.png"))
            report["screenshots"].append("b1-created.png")

            if not fail:
                err = chat(DECIDE_MSG)
                try:
                    with open(os.path.join(dest, ".continuum.md"), encoding="utf-8") as f:
                        proj_txt = f.read()
                except Exception:
                    proj_txt = ""
                hits = []
                entries = os.path.join(REPO, "data", "entries")
                if os.path.isdir(entries):
                    for fn in os.listdir(entries):
                        try:
                            with open(os.path.join(entries, fn), encoding="utf-8") as f:
                                if "pine" in f.read().lower():
                                    hits.append(fn)
                        except Exception:
                            pass
                ok = (not err and "pine" in proj_txt.lower() and not hits)
                report["decision"] = {"in_project_file": "pine" in proj_txt.lower(),
                                      "global_hits": hits, "err": err}
                fail = fail or not ok
                page.screenshot(path=os.path.join(args.shots, "b2-decision.png"))
                report["screenshots"].append("b2-decision.png")

            if report["console_errors"] or report["page_errors"] or report["failed_requests"]:
                fail = True
            report["verdict"] = "pass" if not fail else "fail"
            print(json.dumps(report, indent=1, ensure_ascii=True))
        finally:
            cleanup()
            try:
                with open(os.path.join(args.shots, "summary_bookkeeping.json"), "w",
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
