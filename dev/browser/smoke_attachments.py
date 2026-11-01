"""FLOW B: real file attachments end to end.

DEV TOOL ONLY - not product runtime. Seeds nothing; backs up and restores
runtime data files (sessions, transcripts, msp, entries dir, attachments
dir). Drives the real UI with real Muse turns: select/drop files, chips,
remove, draft survival, lang switch with pending files, send, Muse reads
the staged file (reply quotes its content), transcript metadata, resume
shows attachment chips, Normal + Pro, EN + ES.

Usage (from repo root, env activated):
  python dev/browser/smoke_attachments.py [--no-start --url ...]

Exit codes: 0 pass, 1 failure, 2 degraded (server unreachable).
"""
import argparse
import atexit
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "dev", "browser"))
from smoke import poll_ready  # noqa: E402  (dev-only reuse)

TXT_BODY = "LINEA-VERDE-123\nsegunda linea\n"
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
           "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8138")
    ap.add_argument("--port", type=int, default=8138)
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--shots", default=os.path.join(REPO, "dev", "browser", "shots"))
    args = ap.parse_args()
    url = args.url.rstrip("/")
    os.makedirs(args.shots, exist_ok=True)
    report = {"url": url, "checks": {}, "console_errors": [],
              "page_errors": [], "failed_requests": [], "screenshots": []}
    server_proc, server_log, external = None, None, False
    backups = {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install -r dev/browser/requirements.txt")
        return 1

    tmpd = tempfile.mkdtemp(prefix="continuum-att-")
    txt_path = os.path.join(tmpd, "nota.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(TXT_BODY)
    png_path = os.path.join(tmpd, "punto.png")
    with open(png_path, "wb") as f:
        f.write(base64.b64decode(PNG_B64))

    entries_dir = os.path.join(REPO, "data", "entries")
    entries_bak = os.path.join(args.shots, "entries_bak_att")
    att_dir = os.path.join(REPO, "data", "attachments")
    att_bak = os.path.join(args.shots, "att_bak")

    def snap(src, dst):
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
            return True
        return False

    def unsnap(src, dst, had):
        try:
            if had and os.path.isdir(dst):
                shutil.rmtree(src, ignore_errors=True)
                shutil.copytree(dst, src)
            elif not had and os.path.isdir(src):
                shutil.rmtree(src, ignore_errors=True)
        except Exception:
            pass
        shutil.rmtree(dst, ignore_errors=True)

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
        unsnap(entries_dir, entries_bak, had_entries)
        unsnap(att_dir, att_bak, had_att)
        shutil.rmtree(tmpd, ignore_errors=True)
        if server_proc is not None and not external and server_proc.poll() is None:
            server_proc.terminate()
        if server_log is not None:
            server_log.close()
        return code

    atexit.register(cleanup, 1)
    for name in ("sessions.json", "transcripts.json", "msp_sessions.json",
                 "project_sessions.json", "usage.jsonl", "log.jsonl",
                 "index.json", "core.json"):
        p = os.path.join(REPO, "data", name)
        try:
            with open(p, encoding="utf-8") as f:
                backups[p] = f.read()
        except OSError:
            backups[p] = None
    had_entries = snap(entries_dir, entries_bak)
    had_att = snap(att_dir, att_bak)

    if not args.no_start:
        server_log = open(os.path.join(args.shots, "server_att.log"), "w",
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

    def check(name, ok, detail=""):
        nonlocal fail
        report["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:200]}
        if not ok:
            fail = True
            print(f"FAIL {name}: {detail}"[:300])

    def shot(page, name):
        p = os.path.join(args.shots, name)
        page.screenshot(path=p)
        report["screenshots"].append(p)

    adata = lambda n: json.load(open(os.path.join(REPO, "data", n),
                                     encoding="utf-8"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: report["console_errors"].append(m.text[:300])
                if m.type == "error" else None)
        page.on("pageerror", lambda e: report["page_errors"].append(str(e)[:300]))
        page.on("requestfailed",
                lambda r: report["failed_requests"].append(r.url[:200]))

        def send_and_wait(text, allow_reads=True):
            page.fill("#prompt", text)
            page.click("#send")
            page.wait_for_timeout(1500)
            page.wait_for_function(
                "() => !document.querySelector('#stop').classList"
                ".contains('hidden')", timeout=30000)
            # allow read-only approvals (file reads) so the turn can proceed
            for _ in range(90):
                if page.evaluate("document.querySelector('#stop').classList"
                                 ".contains('hidden')"):
                    break
                if allow_reads:
                    cards = page.locator(".approval-card")
                    for i in range(cards.count()):
                        card = cards.nth(i)
                        if card.locator("button:not([disabled])").count() == 0:
                            continue
                        txt = (card.inner_text() or "").lower()
                        if any(k in txt for k in
                               ("read", "leer", "lectura", "list", "lista")):
                            btn = card.locator(
                                ".approval-actions button:not(.reject)"
                                ":not([disabled])").first
                            if btn.count():
                                btn.click()
                page.wait_for_timeout(2000)
            page.wait_for_function(
                "() => document.querySelector('#stop').classList"
                ".contains('hidden')", timeout=60000)
            page.wait_for_timeout(600)

        try:
            page.goto(url + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#page .chat-layout", timeout=15000)
            page.wait_for_selector("#runtimeChip.connected", timeout=20000)
            page.wait_for_timeout(800)
            page.click("#newChat")
            page.wait_for_timeout(800)
            bid = page.locator("#sessionName").inner_text().strip()
            check("fresh-bid", bool(bid), bid)

            # draft first, then files via select + drop; draft must survive
            page.fill("#prompt", "borrador B")
            page.set_input_files("#fileInput", [png_path])
            page.wait_for_timeout(400)
            page.evaluate(
                """(p) => {
                    const dt = new DataTransfer();
                    dt.items.add(new File(['LINEA-VERDE-123\\nsegunda linea\\n'],
                                          'nota.txt', {type: 'text/plain'}));
                    document.querySelector('#dropOverlay').dispatchEvent(
                      new DragEvent('drop', {dataTransfer: dt, bubbles: true}));
                  }""")
            page.wait_for_timeout(500)
            chips = page.locator("#attChips .att-chip").count()
            check("two-chips", chips == 2, f"{chips} chips")
            check("draft-kept", page.input_value("#prompt") == "borrador B")
            names = page.locator("#attChips .att-chip").all_inner_texts()
            check("chip-names", any("nota.txt" in n for n in names)
                  and any("punto.png" in n for n in names), names)

            # FLOW D: lang switch keeps chips + draft
            page.click('#langSwitch [data-lang="en"]')
            page.wait_for_timeout(500)
            check("chips-survive-en",
                  page.locator("#attChips .att-chip").count() == 2
                  and page.input_value("#prompt") == "borrador B")
            check("attach-title-en",
                  page.get_attribute("#attach", "title") == "Add file")
            page.click('#langSwitch [data-lang="es"]')
            page.wait_for_timeout(500)
            check("chips-survive-es",
                  page.locator("#attChips .att-chip").count() == 2)
            check("attach-title-es",
                  page.get_attribute("#attach", "title") == "Agregar archivo")
            shot(page, "b1-chips.png")

            # remove the png, send with txt only
            page.locator("#attChips .att-chip", has_text="punto.png"
                         ).locator("button").click()
            page.wait_for_timeout(300)
            left = page.locator("#attChips .att-chip").count()
            check("remove-one", left == 1
                  and page.input_value("#prompt") == "borrador B",
                  f"{left} chips")
            send_and_wait("quote exactly the first line of the attached "
                          "text file, nothing else")
            assts = page.locator(
                ".message-row.assistant .message-text").all_inner_texts()
            check("muse-read-file",
                  any("LINEA-VERDE-123" in a for a in assts),
                  (assts[-1][:200] if assts else None))
            check("chips-cleared-after-send",
                  page.locator("#attChips .att-chip").count() == 0)
            bubble = page.locator(
                ".message-row.user .att-chip").all_inner_texts()
            check("bubble-shows-att",
                  any("nota.txt" in b for b in bubble), bubble)
            tr = adata("transcripts.json").get(bid, [])
            check("transcript-meta",
                  len(tr) == 1 and len(tr[0].get("attachments", [])) == 1
                  and tr[0]["attachments"][0]["name"] == "nota.txt",
                  tr[0].get("attachments") if tr else None)
            staged = []
            sdir = os.path.join(att_dir, bid)
            if os.path.isdir(sdir):
                staged = [n for n in os.listdir(sdir)
                          if n != "_manifest.json"]
            check("staged-on-disk", len(staged) == 1, staged)
            if staged:
                with open(os.path.join(sdir, staged[0]), "rb") as f:
                    check("staged-bytes", f.read().decode() == TXT_BODY)
            with open(txt_path, encoding="utf-8") as f:
                check("original-untouched", f.read() == TXT_BODY)
            shot(page, "b2-sent.png")

            # FLOW D: thread + draft + pending file across ES->EN->ES
            page.set_input_files("#fileInput", [png_path])
            page.fill("#prompt", "borrador D")
            page.wait_for_timeout(400)
            rows0 = page.locator(".message-row").count()
            page.click('#langSwitch [data-lang="en"]')
            page.wait_for_timeout(500)
            check("flowD-en",
                  page.locator(".message-row").count() == rows0
                  and page.locator("#attChips .att-chip").count() == 1
                  and page.input_value("#prompt") == "borrador D")
            page.click('#langSwitch [data-lang="es"]')
            page.wait_for_timeout(500)
            check("flowD-es",
                  page.locator(".message-row").count() == rows0
                  and page.locator("#attChips .att-chip").count() == 1
                  and page.input_value("#prompt") == "borrador D")
            page.locator("#attChips .att-chip button").first.click()
            page.fill("#prompt", "")

            # Pro shows the same thread, then back to Normal
            page.click("#modeToggle")
            page.wait_for_timeout(1000)
            check("pro-thread-intact",
                  page.locator(".message-row.user .att-chip").count() >= 1)
            page.click("#modeToggle")
            page.wait_for_timeout(500)

            # resume from History shows attachment presence
            page.click("#newChat")
            page.wait_for_timeout(800)
            page.click(".nav-link[data-view=history]")
            page.wait_for_selector(".alt-card", timeout=10000)
            page.locator(".alt-card", has_text="attached text file").locator(
                "button", has_text="Continuar").click()
            page.wait_for_function(
                "() => document.querySelectorAll("
                "'.message-row.user').length === 1", timeout=15000)
            resumed = page.locator(
                ".message-row.user .att-chip").all_inner_texts()
            check("resume-shows-att",
                  any("nota.txt" in r for r in resumed), resumed)
            shot(page, "b3-resume.png")

            check("no-console-errors", not report["console_errors"],
                  report["console_errors"][:3])
            check("no-page-errors", not report["page_errors"],
                  report["page_errors"][:3])
            check("no-failed-requests", not report["failed_requests"],
                  report["failed_requests"][:3])
        finally:
            browser.close()

    print(json.dumps(report, indent=1, ensure_ascii=True))
    return cleanup(1 if fail else 0)


if __name__ == "__main__":
    sys.exit(main())
