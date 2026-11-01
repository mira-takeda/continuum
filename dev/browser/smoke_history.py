"""History/resume + New Chat regression smoke.

DEV TOOL ONLY - not product runtime. Seeds runtime data files (backed up
and restored), then drives the real UI: History card -> "Continuar sesión"
(resume button) -> full transcript bubbles; view switch keeps the thread
via cache.
FLOW A: New Chat clears the thread, keeps the project, starts genuinely
new Muse sessions per chat (proved with real turns), old chats resume
intact on the same MSP session.

Usage (from repo root, env activated):
  python dev/browser/smoke_history.py [--no-start --url ...]

Exit codes: 0 pass, 1 failure, 2 degraded (server unreachable).
"""
import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "dev", "browser"))
from smoke import poll_ready  # noqa: E402  (dev-only reuse)

SEED_BID = "histseed"
SEED_TURNS = [
    {"turn": "shistseedt1", "ts": "2026-11-01T00:00:01Z",
     "user": "semilla uno", "assistant": "respuesta uno"},
    {"turn": "shistseedt2", "ts": "2026-11-01T00:00:02Z",
     "user": "semilla dos", "assistant": "respuesta dos"},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8138")
    ap.add_argument("--port", type=int, default=8138)
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--shots", default=os.path.join(REPO, "dev", "browser", "shots"))
    args = ap.parse_args()
    url = args.url.rstrip("/")
    os.makedirs(args.shots, exist_ok=True)
    report = {"url": url, "console_errors": [], "page_errors": [],
              "failed_requests": [], "screenshots": []}
    server_proc, server_log, external = None, None, False
    backups = {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install -r dev/browser/requirements.txt")
        return 1

    def cleanup(code):
        for path, saved in backups.items():
            try:
                if saved is None:
                    if os.path.exists(path):
                        os.remove(path)
                else:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(saved)
            except Exception:
                pass
        try:
            if entries_bak and os.path.isdir(entries_bak):
                shutil.rmtree(entries_dir, ignore_errors=True)
                shutil.copytree(entries_bak, entries_dir)
                shutil.rmtree(entries_bak, ignore_errors=True)
        except Exception:
            pass
        if server_proc is not None and not external and server_proc.poll() is None:
            server_proc.terminate()
        if server_log is not None:
            server_log.close()
        return code

    atexit.register(cleanup, 1)  # backups+server restored even on crash

    for name in ("sessions.json", "transcripts.json", "msp_sessions.json",
                 "project_sessions.json", "usage.jsonl", "log.jsonl",
                 "index.json", "core.json"):
        p = os.path.join(REPO, "data", name)
        try:
            with open(p, encoding="utf-8") as f:
                backups[p] = f.read()
        except OSError:
            backups[p] = None
    entries_dir = os.path.join(REPO, "data", "entries")
    entries_bak = os.path.join(args.shots, "entries_bak")
    if os.path.isdir(entries_bak):
        shutil.rmtree(entries_bak, ignore_errors=True)
    if os.path.isdir(entries_dir):
        shutil.copytree(entries_dir, entries_bak)
    else:
        entries_bak = None
    os.makedirs(os.path.join(REPO, "data"), exist_ok=True)
    with open(os.path.join(REPO, "data", "sessions.json"), "w",
              encoding="utf-8") as f:
        json.dump({SEED_BID: len(SEED_TURNS)}, f)
    with open(os.path.join(REPO, "data", "transcripts.json"), "w",
              encoding="utf-8") as f:
        json.dump({SEED_BID: SEED_TURNS}, f)

    if not args.no_start:
        server_log = open(os.path.join(args.shots, "server_history.log"), "w",
                          encoding="utf-8")
        server_proc = subprocess.Popen(
            [sys.executable, "-u", "server.py", "--port", str(args.port)],
            cwd=REPO, stdout=server_log, stderr=subprocess.STDOUT)
    try:
        boot = poll_ready(url, 45)
    except RuntimeError as e:
        report["fatal"] = str(e)
        print(json.dumps(report, indent=1, ensure_ascii=True))
        return cleanup(2)
    if server_proc is not None and server_proc.poll() is not None:
        external = True
    if not boot.get("connected"):
        report["fatal"] = "backend reports Muse unavailable"
        print(json.dumps(report, indent=1, ensure_ascii=True))
        return cleanup(2)

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
            page.wait_for_selector("#runtimeChip.connected", timeout=20000)
            page.wait_for_timeout(800)
            page.click(".nav-link[data-view=history]")
            page.wait_for_selector(".alt-card", timeout=10000)
            cards = page.locator(".alt-grid").inner_text()
            h = {"title": ("Semilla uno" in cards),
                 "preview": ("semilla uno" in cards),
                 "turns": ("Turnos: 2" in cards),
                 "no-raw-id": (SEED_BID not in cards)}
            if not (h["title"] and h["preview"] and h["turns"]
                    and h["no-raw-id"]):
                fail = True
            page.locator(".alt-card button",
                         has_text="Continuar").first.click()
            page.wait_for_function(
                "() => document.querySelectorAll("
                "'.message-row.user').length === 2", timeout=15000)
            users = page.locator(
                ".message-row.user .message-text").all_inner_texts()
            assts = page.locator(
                ".message-row.assistant .message-text").all_inner_texts()
            h["users"] = users
            h["assts"] = assts
            if users != ["semilla uno", "semilla dos"]:
                fail = True
            if assts != ["respuesta uno", "respuesta dos"]:
                fail = True
            h["welcome_gone"] = page.locator("#welcome").count() == 0
            if not h["welcome_gone"]:
                fail = True
            page.screenshot(path=os.path.join(args.shots,
                                              "h1-history-resume.png"))
            report["screenshots"].append("h1-history-resume.png")
            page.click(".nav-link[data-view=projects]")
            page.wait_for_selector(".register-form", timeout=10000)
            page.click(".nav-link[data-view=chat]")
            page.wait_for_function(
                "() => document.querySelectorAll("
                "'.message-row.user').length === 2", timeout=15000)
            h["switch_keeps_users"] = page.locator(
                ".message-row.user").count()
            h["switch_keeps_assts"] = page.locator(
                ".message-row.assistant").count()
            if h["switch_keeps_users"] != 2 or h["switch_keeps_assts"] != 2:
                fail = True
            report["history"] = h
            # ---- FLOW A: New Chat with real Muse sessions ----
            adata = lambda n: json.load(open(os.path.join(REPO, "data", n),
                                             encoding="utf-8"))

            def send_and_wait(text):
                page.fill("#prompt", text)
                page.click("#send")
                page.wait_for_timeout(1500)
                page.wait_for_function(
                    "() => !document.querySelector('#stop').classList"
                    ".contains('hidden')", timeout=30000)
                page.wait_for_function(
                    "() => document.querySelector('#stop').classList"
                    ".contains('hidden')", timeout=180000)
                page.wait_for_timeout(600)

            an = {}
            proj_btn = page.locator("#sidebarProjects button").first
            proj_name = proj_btn.locator("span").inner_text().strip()
            proj_btn.click()
            page.wait_for_selector("#projectChip:not(.hidden)", timeout=10000)
            chip = page.locator("#projectChipName").inner_text().strip()
            an["proj_activate"] = chip == proj_name
            if not an["proj_activate"]:
                fail = True
            page.fill("#prompt", "borrador A")
            page.click("#newChat")
            page.wait_for_timeout(800)
            an["cleared"] = page.locator(".message-row").count() == 0
            an["welcome"] = page.locator("#welcome").count() == 1
            an["draft_kept"] = page.input_value("#prompt") == "borrador A"
            bid1 = page.locator("#sessionName").inner_text().strip()
            an["bid1"] = bid1
            an["new_bid"] = bool(bid1) and bid1 != SEED_BID
            an["proj_kept"] = (page.locator("#projectChipName").inner_text()
                               .strip() == proj_name)
            if not (an["cleared"] and an["welcome"] and an["draft_kept"]
                    and an["new_bid"] and an["proj_kept"]):
                fail = True
            send_and_wait("dime solo la palabra: NARANJA")
            an["t1_users"] = page.locator(".message-row.user").count()
            an["t1_assts"] = page.locator(".message-row.assistant").count()
            if an["t1_users"] != 1 or an["t1_assts"] < 1:
                fail = True
            sid1 = (adata("msp_sessions.json").get(bid1) or {}).get("sid")
            an["sid1"] = bool(sid1)
            if not sid1:
                fail = True
            page.click("#newChat")
            page.wait_for_timeout(800)
            bid2 = page.locator("#sessionName").inner_text().strip()
            an["bid2"] = bid2
            an["bid2_new"] = bool(bid2) and bid2 != bid1
            an["cleared2"] = page.locator(".message-row").count() == 0
            if not (an["bid2_new"] and an["cleared2"]):
                fail = True
            send_and_wait("dime solo la palabra: AZUL")
            sid2 = (adata("msp_sessions.json").get(bid2) or {}).get("sid")
            an["sids_differ"] = bool(sid2) and sid2 != sid1
            if not an["sids_differ"]:
                fail = True
            tr = adata("transcripts.json")
            an["tr_sep"] = (len(tr.get(bid1, [])) == 1
                            and len(tr.get(bid2, [])) == 1
                            and len(tr.get(SEED_BID, [])) == 2
                            and "NARANJA" in tr[bid1][0]["user"]
                            and "AZUL" in tr[bid2][0]["user"])
            if not an["tr_sep"]:
                fail = True
            page.click(".nav-link[data-view=history]")
            page.wait_for_selector(".alt-card", timeout=10000)
            cards = page.locator(".alt-grid").inner_text()
            an["hist_lists"] = all(x in cards for x in
                                   ("Solo la palabra: NARANJA",
                                    "Solo la palabra: AZUL", "Semilla uno",
                                    proj_name))
            if not an["hist_lists"]:
                fail = True
            page.locator(".alt-card", has_text="NARANJA").locator(
                "button", has_text="Continuar").click()
            page.wait_for_function(
                "() => document.querySelectorAll("
                "'.message-row.user').length === 1", timeout=15000)
            u1 = page.locator(
                ".message-row.user .message-text").all_inner_texts()
            an["resume_intact"] = bool(u1) and "NARANJA" in u1[0]
            if not an["resume_intact"]:
                fail = True
            send_and_wait("dime solo la palabra: VERDE")
            sid1b = (adata("msp_sessions.json").get(bid1) or {}).get("sid")
            an["same_msp_continues"] = sid1b == sid1
            an["tr_grew"] = len(adata("transcripts.json").get(bid1, [])) == 2
            if not (an["same_msp_continues"] and an["tr_grew"]):
                fail = True
            page.screenshot(path=os.path.join(args.shots, "h2-newchat.png"))
            report["screenshots"].append("h2-newchat.png")
            report["newchat"] = an
            if report["console_errors"] or report["page_errors"] or \
               report["failed_requests"]:
                fail = True
            report["verdict"] = "pass" if not fail else "fail"
            print(json.dumps(report, indent=1, ensure_ascii=True))
        finally:
            try:
                with open(os.path.join(args.shots, "summary_history.json"),
                          "w", encoding="utf-8") as f:
                    json.dump(report, f, indent=1, ensure_ascii=True)
            except Exception:
                pass
            browser.close()
            if server_proc is not None and not external and \
               server_proc.poll() is None:
                server_proc.terminate()
    return cleanup(1 if fail else 0)


if __name__ == "__main__":
    sys.exit(main())
