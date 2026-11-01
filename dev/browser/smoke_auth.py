"""Auth/first-run banner states with stubbed bootstrap (no Muse changes).

DEV TOOL ONLY - not product runtime. Intercepts /api/ui/bootstrap with
add_init_script so each first-run state is exercised without touching the
real Muse installation: not-installed, installed-but-logged-out,
no-allowance, plus a connected control. Asserts EN/ES banner copy, action
links, recheck, no secret storage in localStorage, and zero page errors.

Usage (from repo root, env activated):
  python dev/browser/smoke_auth.py [--no-start --url ...]

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

STATES = {
    "noinstall": {"connected": False, "runtime": {
        "installed": False, "provider": "meta", "logged_in": False,
        "allowance": False, "auth_text": "not installed",
        "docs": "https://dev.meta.ai/docs/muse-code/",
        "setup": "https://dev.meta.ai/api/auth/login/",
        "install_sh": "curl -fsSL https://dev.meta.ai/install.sh | bash"}},
    "nologin": {"connected": False, "runtime": {
        "installed": True, "provider": "meta", "logged_in": False,
        "allowance": False, "auth_text": "muse login required",
        "docs": "https://dev.meta.ai/docs/muse-code/",
        "setup": "https://dev.meta.ai/api/auth/login/",
        "install_sh": ""}},
    "noallow": {"connected": False, "runtime": {
        "installed": True, "provider": "meta", "logged_in": True,
        "allowance": False, "auth_text": "No subscription allowance",
        "docs": "https://dev.meta.ai/docs/muse-code/",
        "setup": "https://dev.meta.ai/api/auth/login/",
        "install_sh": ""}},
}


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

    if not args.no_start:
        server_log = open(os.path.join(args.shots, "server_auth.log"), "w",
                          encoding="utf-8")
        server_proc = subprocess.Popen(
            [sys.executable, "-u", "server.py", "--port", str(args.port)],
            cwd=REPO, stdout=server_log, stderr=subprocess.STDOUT)
    try:
        poll_ready(url, 45)
    except RuntimeError as e:
        report["fatal"] = str(e)
        print(json.dumps(report, indent=1, ensure_ascii=True))
        return cleanup(2)
    if server_proc is not None and server_proc.poll() is not None:
        external = True

    fail = False

    def check(name, ok, detail=""):
        nonlocal fail
        report["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:200]}
        if not ok:
            fail = True
            print(f"FAIL {name}: {detail}"[:300])

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for state, boot in STATES.items():
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_init_script(
                """(() => {
                    const boot = %s;
                    const orig = window.fetch.bind(window);
                    window.fetch = (u, o) => {
                      if (String(u).includes('/api/ui/bootstrap'))
                        return Promise.resolve(new Response(
                          JSON.stringify(boot), {status: 200,
                           headers: {'Content-Type': 'application/json'}}));
                      return orig(u, o);
                    };
                  })();""" % json.dumps(boot))
            page = ctx.new_page()
            page.on("console", lambda m: report["console_errors"].append(m.text[:300])
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: report["page_errors"].append(str(e)[:300]))
            page.goto(url + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#page .chat-layout", timeout=15000)
            page.wait_for_selector("#setupBanner:not(.hidden)", timeout=15000)
            msg = page.locator("#setupBanner span").first.inner_text()
            href = page.get_attribute("#setupAction", "href")
            if state == "noinstall":
                check("noinstall-es", "no está instalado" in msg
                      and "install.sh" in msg, msg[:150])
                check("noinstall-docs", href and "muse-code" in href, href)
                page.click('#langSwitch [data-lang="en"]')
                page.wait_for_timeout(400)
                msg = page.locator("#setupBanner span").first.inner_text()
                check("noinstall-en", "not installed" in msg, msg[:150])
                check("noinstall-docs-en",
                      page.locator("#setupAction").inner_text() == "Setup docs")
            elif state == "nologin":
                check("nologin-es", "no ha iniciado sesión" in msg, msg[:150])
                check("nologin-connect",
                      page.locator("#setupAction").inner_text() == "Conectar Meta"
                      and href and "auth/login" in href, href)
                page.click('#langSwitch [data-lang="en"]')
                page.wait_for_timeout(400)
                msg = page.locator("#setupBanner span").first.inner_text()
                check("nologin-en", "not authenticated" in msg, msg[:150])
                check("nologin-connect-en",
                      page.locator("#setupAction").inner_text() == "Connect Meta")
            else:
                check("noallow-es", "no está utilizable" in msg, msg[:150])
                page.click('#langSwitch [data-lang="en"]')
                page.wait_for_timeout(400)
                msg = page.locator("#setupBanner span").first.inner_text()
                check("noallow-en", "not usable" in msg, msg[:150])
            keys = page.evaluate("Object.keys(localStorage)")
            check(f"{state}-no-secrets",
                  not any(k for k in keys
                          if any(w in k.lower() for w in
                                 ("key", "token", "secret", "auth"))),
                  keys)
            page.screenshot(path=os.path.join(args.shots, f"auth-{state}.png"))
            report["screenshots"].append(f"auth-{state}.png")
            ctx.close()

        # connected control: real backend, banner stays hidden
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: report["page_errors"].append(str(e)[:300]))
        page.goto(url + "/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#runtimeChip.connected", timeout=30000)
        page.wait_for_timeout(500)
        check("connected-no-banner",
              page.locator("#setupBanner.hidden").count() == 1)
        browser.close()
        check("no-console-errors", not report["console_errors"],
              report["console_errors"][:3])
        check("no-page-errors", not report["page_errors"],
              report["page_errors"][:3])

    print(json.dumps(report, indent=1, ensure_ascii=True))
    return cleanup(1 if fail else 0)


if __name__ == "__main__":
    sys.exit(main())
