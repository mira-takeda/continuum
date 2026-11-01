"""i18n regression smoke: EN/ES chrome switch without losing the thread.

DEV TOOL ONLY - not product runtime. Canned SSE through the real UI.
Covers: default ES chrome; ES->EN->ES switch preserving composer draft and
thread; localStorage persistence across reload; Pro panels follow locale;
canned assistant reply rendered verbatim (never translated); status line in
the selected locale; zero console/page errors (catches i18n t() shadowing
regressions like the handleEvent/followupEvent `t` collision).

Usage (from repo root, env activated):
  python dev/browser/smoke_i18n.py [--no-start --url ...]

Exit codes: 0 pass, 1 failure, 2 degraded (server unreachable).
"""
import argparse
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "dev", "browser"))
from smoke import poll_ready  # noqa: E402  (dev-only reuse)

REPLY = "¡Hola! Me llamo Muse y tú te llamas Ada."


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

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: pip install -r dev/browser/requirements.txt")
        return 1

    def cleanup(code):
        if server_proc is not None and not external and server_proc.poll() is None:
            server_proc.terminate()
        if server_log is not None:
            server_log.close()
        return code

    def shot(page, name):
        p = os.path.join(args.shots, name)
        page.screenshot(path=p)
        report["screenshots"].append(p)

    if not args.no_start:
        server_log = open(os.path.join(args.shots, "server_i18n.log"), "w",
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
            nav = page.inner_text(".sidebar nav")
            check("default-es-nav", "Conversación" in nav, nav.splitlines()[:6])
            check("default-es-ph",
                  page.get_attribute("#prompt", "placeholder") ==
                  "Escribe lo que tienes en mente...",
                  page.get_attribute("#prompt", "placeholder"))
            shot(page, "i18n_1_es.png")

            # Draft + switch to EN: chrome flips, draft survives.
            page.fill("#prompt", "borrador sin enviar 123")
            page.click('#langSwitch [data-lang="en"]')
            page.wait_for_timeout(600)
            nav = page.inner_text(".sidebar nav")
            check("en-nav", "Conversation" in nav, nav.splitlines()[:6])
            check("draft-preserved",
                  page.input_value("#prompt") == "borrador sin enviar 123",
                  repr(page.input_value("#prompt")))
            check("en-ph",
                  page.get_attribute("#prompt", "placeholder") ==
                  "Type what is on your mind...",
                  page.get_attribute("#prompt", "placeholder"))
            check("lang-stored",
                  page.evaluate("localStorage.getItem('continuumLang')") == "en")
            shot(page, "i18n_2_en.png")

            # Canned turn in EN chrome: EN status, verbatim ES reply.
            page.evaluate(
                """(reply) => {
                    const orig = window.fetch.bind(window);
                    const enc = new TextEncoder();
                    window.fetch = async (u, o) => {
                      if (String(u).includes('/api/chat_stream')) {
                        const frames = [
                          {t:'started',session_id:'stub',opened:[],paths:[]},
                          {t:'text',text:reply},
                          {t:'done',terminal:'completed'}];
                        const body = frames.map(
                          f => 'data: '+JSON.stringify(f)+'\\n\\n').join('');
                        return new Response(enc.encode(body), {status:200});
                      }
                      return orig(u, o);
                    };
                  }""", REPLY)
            page.fill("#prompt", "")
            page.fill("#prompt", "como te llamas")
            page.click("#send")
            page.wait_for_selector(".message-row.assistant .message-text",
                                   timeout=15000)
            page.wait_for_timeout(800)
            bodies = page.eval_on_selector_all(
                ".message-row.assistant .message-text",
                "els=>els.map(e=>e.innerText)")
            check("reply-verbatim", bodies and bodies[-1] == REPLY,
                  repr(bodies[-1][:120] if bodies else None))
            check("reply-not-translated",
                  not (bodies and "Hello" in bodies[-1]))

            # Back to ES: chrome flips, thread survives.
            page.click('#langSwitch [data-lang="es"]')
            page.wait_for_timeout(600)
            nav = page.inner_text(".sidebar nav")
            check("es-again-nav", "Conversación" in nav)
            bodies = page.eval_on_selector_all(
                ".message-row.assistant .message-text",
                "els=>els.map(e=>e.innerText)")
            check("thread-preserved", bodies and bodies[-1] == REPLY,
                  f"{len(bodies)} assistant rows")
            gone = page.eval_on_selector_all(
                "#toggleRail, .memory-card, .design-mode, .soft-badge,"
                " .brand-text small",
                "els=>els.length")
            check("removed-els-absent", gone == 0, f"{gone} leftover nodes")

            # Persist EN across reload.
            page.click('#langSwitch [data-lang="en"]')
            page.wait_for_timeout(400)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#page .chat-layout", timeout=15000)
            page.wait_for_selector("#runtimeChip.connected", timeout=20000)
            page.wait_for_timeout(600)
            nav = page.inner_text(".sidebar nav")
            check("reload-persists-en", "Conversation" in nav,
                  nav.splitlines()[:6])

            # Pro panels follow locale, then back to Normal.
            page.click("#modeToggle")
            page.wait_for_timeout(1200)
            rail = page.inner_text("#contextRail")
            check("pro-en", "NATIVE CONTROLS" in rail and "Modelo" not in rail,
                  rail[:200])
            shot(page, "i18n_3_pro_en.png")
            page.click("#modeToggle")
            page.wait_for_timeout(600)

            # Pro panels follow ES locale too.
            page.click('#langSwitch [data-lang="es"]')
            page.wait_for_timeout(600)
            page.click("#modeToggle")
            page.wait_for_timeout(1200)
            rail = page.inner_text("#contextRail")
            check("pro-es", "CONTROLES NATIVOS" in rail
                  and "NATIVE CONTROLS" not in rail, rail[:200])
            gone = page.eval_on_selector_all(
                "#toggleRail, .memory-card, .design-mode",
                "els=>els.length")
            check("removed-els-absent-pro", gone == 0,
                  f"{gone} leftover nodes")
            shot(page, "i18n_4_pro_es.png")
            page.click("#modeToggle")
            page.wait_for_timeout(600)

            # Narrow viewport: no opener on desktop, clear opener when narrow.
            check("desktop-no-narrow-toggle",
                  not page.is_visible("#railToggleNarrow"))
            page.set_viewport_size({"width": 900, "height": 800})
            page.wait_for_timeout(500)
            check("narrow-toggle-visible",
                  page.is_visible("#railToggleNarrow"))
            check("narrow-toggle-title-es",
                  page.get_attribute("#railToggleNarrow", "title") ==
                  "Panel de detalles",
                  page.get_attribute("#railToggleNarrow", "title"))
            check("narrow-rail-hidden-first",
                  not page.is_visible("#contextRail"))
            page.click("#railToggleNarrow")
            page.wait_for_timeout(400)
            check("narrow-rail-opens",
                  page.is_visible("#contextRail"))
            shot(page, "i18n_5_narrow_es.png")
            page.click("#railClose")
            page.wait_for_timeout(400)
            check("narrow-rail-closes",
                  not page.is_visible("#contextRail"))
            page.click('#langSwitch [data-lang="en"]')
            page.wait_for_timeout(400)
            check("narrow-toggle-title-en",
                  page.get_attribute("#railToggleNarrow", "title") ==
                  "Details panel",
                  page.get_attribute("#railToggleNarrow", "title"))
            page.click("#modeToggle")
            page.wait_for_timeout(1000)
            page.click("#railToggleNarrow")
            page.wait_for_timeout(400)
            check("narrow-rail-opens-pro-en",
                  page.is_visible("#contextRail"))
            shot(page, "i18n_6_narrow_pro_en.png")
            page.click("#railClose")
            page.wait_for_timeout(400)
            check("narrow-railclose-works",
                  not page.is_visible("#contextRail"))
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(400)

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
