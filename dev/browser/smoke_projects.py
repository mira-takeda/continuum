"""Project-lifecycle browser smoke. DEV TOOL ONLY - not product runtime.

End-to-end through real UI + backend + Muse: NL project creation in
Documents, workspace transition (index.html lands in the project), project
decision routing, switch away/back with continuation. Cleans up its temp
project afterwards (folder, registry key, session mapping).

Usage (from repo root, env activated):
  python dev/browser/smoke_projects.py [--no-start]

Exit codes: 0 pass, 1 failure, 2 degraded (backend/Muse unavailable).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "dev", "browser"))
from smoke import poll_ready  # noqa: E402  (dev-only reuse)

TURN_WAIT_S = 300


def reg_path():
    return os.path.join(REPO, "data", "projects.json")


def read_registry():
    try:
        with open(reg_path(), encoding="utf-8") as f:
            return (json.load(f) or {}).get("projects", {})
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8137")
    ap.add_argument("--port", type=int, default=8137)
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--shots", default=os.path.join(REPO, "dev", "browser", "shots"))
    args = ap.parse_args()
    url = args.url.rstrip("/")
    os.makedirs(args.shots, exist_ok=True)
    name = f"SmokeTmp{int(time.time()) % 100000}"

    report = {"url": url, "project": name, "console_errors": [],
              "page_errors": [], "failed_requests": [], "screenshots": [],
              "steps": {}}
    server_proc, server_log, external = None, None, False

    def step(key, ok, detail=""):
        report["steps"][key] = {"ok": bool(ok), "detail": str(detail)[:300]}
        print(f"STEP {key}: {'ok' if ok else 'FAIL'} {str(detail)[:160]}",
              flush=True)
        return ok

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install -r dev/browser/requirements.txt")
        return 1

    if not args.no_start:
        server_log = open(os.path.join(args.shots, "server_projects.log"), "w",
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

    # Remember pre-run demo mapping for restore (best effort).
    prev_active = None
    try:
        sys.path.insert(0, REPO)
        from memory import projects as proj_mod
        prev_active = proj_mod.active_for(REPO, "demo")
    except Exception:
        proj_mod = None

    def cleanup():
        try:
            docs = proj_mod.documents_folder() if proj_mod else None
            if docs:
                shutil.rmtree(os.path.join(docs, name), ignore_errors=True)
            reg = read_registry()
            if name in reg:
                with open(reg_path(), encoding="utf-8") as f:
                    full = json.load(f)
                full.get("projects", {}).pop(name, None)
                with open(reg_path(), "w", encoding="utf-8") as f:
                    json.dump(full, f)
            if proj_mod:
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

        def shot(nm):
            p = os.path.join(args.shots, nm)
            page.screenshot(path=p)
            report["screenshots"].append(nm)

        def chat(msg, tag):
            page.fill("#prompt", msg)
            page.click("#send")
            page.wait_for_timeout(1200)
            page.wait_for_function("() => !document.querySelector('#stop').classList.contains('hidden')", timeout=30000)
            t0 = time.time()
            approved = []
            while time.time() - t0 < TURN_WAIT_S:
                cards = page.locator(".approval-card")
                if cards.count():
                    txt = cards.first.inner_text()
                    if name not in txt:
                        return None, f"unexpected approval outside {name}: {txt[:200]}"
                    btns = cards.first.locator("button")
                    pick = None
                    for i in range(btns.count()):
                        t = btns.nth(i).inner_text().lower()
                        if "once" in t:
                            pick = i
                            break
                    if pick is None:
                        for i in range(btns.count()):
                            if "allow" in btns.nth(i).inner_text().lower():
                                pick = i
                                break
                    if pick is None:
                        return None, f"no allow button in: {txt[:200]}"
                    approved.append(btns.nth(pick).inner_text())
                    btns.nth(pick).click()
                    page.wait_for_timeout(1500)
                    continue
                if page.locator(".question-card").count():
                    return None, "Muse asked a question; smoke cannot answer: " + \
                        page.locator(".question-card").first.inner_text()[:200]
                if page.eval_on_selector("#stop", "e => e.classList.contains('hidden')"):
                    break
                time.sleep(2)
            else:
                return None, "turn timeout"
            if approved:
                report.setdefault("approvals", []).append({tag: approved})
            txt = page.locator(".message-row.assistant .message-text").last.inner_text()
            report.setdefault("replies", {})[tag] = txt[:500]
            return txt, ""

        try:
            page.goto(url + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#page .chat-layout", timeout=15000)
            page.wait_for_selector("#runtimeChip.connected", timeout=30000)
            page.wait_for_timeout(800)

            # 1. NL create in Documents.
            reply, err = chat(f"Create a new project called {name} in Documents.", "create")
            if err:
                step("create", False, err)
                fail = True
            else:
                docs = proj_mod.documents_folder()
                dest = os.path.join(docs, name)
                reg = read_registry()
                checks = {
                    "folder_outside_repo": os.path.isdir(dest) and
                    os.path.abspath(REPO) not in os.path.abspath(dest),
                    "registered": reg.get(name) == dest,
                    "context_file": os.path.exists(os.path.join(dest, ".continuum.md")),
                    "chip_active": (not page.eval_on_selector(
                        "#projectChip", "e => e.classList.contains('hidden')"))
                    and page.locator("#projectChipName").inner_text() == name,
                }
                try:
                    with open(os.path.join(REPO, "data", "msp_sessions.json"),
                              encoding="utf-8") as f:
                        mm = json.load(f).get("demo")
                    home = os.path.abspath(os.path.expanduser("~"))
                    checks["msp_broad_root"] = (
                        isinstance(mm, dict) and
                        os.path.normcase(mm.get("root", "")) == os.path.normcase(home))
                except Exception as e:
                    checks["msp_broad_root"] = f"unreadable: {e}"
                step("create", all(v is True for v in checks.values()), str(checks))
                fail = fail or not all(v is True for v in checks.values())
                shot("p1-created.png")
                # Appears in Projects view?
                page.click('.nav-link[data-view="projects"]')
                page.wait_for_timeout(1500)
                shot("p2-projects.png")
                found = page.locator(".alt-card", has_text=name).count() > 0
                step("projects_view", found, "card present" if found else "missing")
                fail = fail or not found
                page.click('.nav-link[data-view="chat"]')
                page.wait_for_selector("#prompt", timeout=10000)

            # 2. index.html lands in the project (workspace transition).
            if not fail:
                reply, err = chat(
                    f"Create an index.html file in the {name} project with a short hello page.",
                    "index")
                dest = os.path.join(proj_mod.documents_folder(), name)
                idx = os.path.join(dest, "index.html")
                stray = os.path.join(REPO, name, "index.html")
                ok = not err and os.path.exists(idx) and not os.path.exists(stray)
                step("workspace_file", ok,
                     err or f"index.html in project={os.path.exists(idx)} repo_stray={os.path.exists(stray)}")
                fail = fail or not ok
                shot("p3-index.png")

            # 3. Project decision routes to .continuum.md, not global memory.
            if not fail:
                reply, err = chat(
                    f"For the {name} project, we decided to use cedar instead of balsa.",
                    "decision")
                dest = os.path.join(proj_mod.documents_folder(), name)
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
                                if "cedar" in f.read().lower():
                                    hits.append(fn)
                        except Exception:
                            pass
                ok = (not err and "cedar" in proj_txt.lower() and not hits)
                step("decision_routing", ok,
                     err or f"in_project_file={'cedar' in proj_txt.lower()} global_hits={hits}")
                fail = fail or not ok
                shot("p4-decision.png")

            # 4. Switch away and back, then continuation.
            if not fail:
                other = next((k for k in read_registry() if k != name), None)
                reply, err = chat(f"Switch to the {other} project.", "switch_away") if other else (None, "no second project registered")
                chip = page.locator("#projectChipName").inner_text() if not page.eval_on_selector(
                    "#projectChip", "e => e.classList.contains('hidden')") else None
                ok = not err and other is not None and chip == other
                step("switch_away", ok, err or f"chip={chip}")
                fail = fail or not ok
                reply, err = chat(f"Switch back to the {name} project.", "switch_back")
                chip = page.locator("#projectChipName").inner_text() if not page.eval_on_selector(
                    "#projectChip", "e => e.classList.contains('hidden')") else None
                ok = not err and chip == name
                step("switch_back", ok, err or f"chip={chip}")
                fail = fail or not ok
                reply, err = chat("What material did we choose for the enclosure in this project?",
                                  "recall")
                ok = not err and "cedar" in (reply or "").lower()
                step("continuation", ok, err or (reply or "")[:200])
                fail = fail or not ok
                shot("p5-continuation.png")

            bad = (report["console_errors"] or report["page_errors"]
                   or report["failed_requests"])
            if bad:
                fail = True
            report["verdict"] = "pass" if not fail else "fail"
            print(json.dumps(report, indent=1, ensure_ascii=True))
        finally:
            cleanup()
            try:
                with open(os.path.join(args.shots, "summary_projects.json"), "w",
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
