"""UX regression smoke: provenance, composer-during-turn, activity state.

DEV TOOL ONLY - not product runtime. Canned SSE through the real UI.
Fix 1 (provenance): memory-ops frames never surface ids in Normal chat;
Pro shows the raw block byte-identical.

Usage (from repo root, env activated):
  python dev/browser/smoke_ux.py [--no-start --url ...]

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

BLOCK = ("```memory-ops\n"
         "[{\"kind\": \"confirm\", \"id\": \"20261101-1400-probe\"}]\n```")


def prov_sse():
    parts = [json.dumps({"t": "started", "session_id": "stub",
                         "opened": [], "paths": []}),
             json.dumps({"t": "text", "text": "Claro, te llamaré Muse. "}),
             json.dumps({"t": "memory-ops", "block": BLOCK}),
             json.dumps({"t": "text", "text": "Listo."}),
             json.dumps({"t": "done", "terminal": "completed"})]
    return "".join("data: " + p + "\n\n" for p in parts)


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
        server_log = open(os.path.join(args.shots, "server_ux.log"), "w",
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
            page.wait_for_timeout(1000)
            page.evaluate(
                """(a) => {
                    const orig = window.fetch.bind(window);
                    const enc = new TextEncoder();
                    window.__chatCalls = [];
                    window.__chatScript = (a.script || []).slice();
                    window.__openCtl = null;
                    const frame = (o) => enc.encode(
                      "data: " + JSON.stringify(o) + "\\n\\n");
                    window.__emitSSE = (o) => {
                      if (window.__openCtl)
                        window.__openCtl.enqueue(frame(o));
                    };
                    window.__closeSSE = () => {
                      const c = window.__openCtl; window.__openCtl = null;
                      if (c) {
                        try { c.enqueue(frame({t: "done",
                                              terminal: "completed"})); } catch {}
                        try { c.close(); } catch {}
                      }
                    };
                    window.__closeSSENoDone = () => {
                      const c = window.__openCtl; window.__openCtl = null;
                      if (c) { try { c.close(); } catch {} }
                    };
                    window.__closeSSESplit = (ms) => {
                      const c = window.__openCtl;
                      if (c) {
                        try { c.enqueue(frame({t: "done",
                                              terminal: "completed"})); } catch {}
                      }
                      setTimeout(() => {
                        window.__openCtl = null;
                        if (c) { try { c.close(); } catch {} }
                      }, ms);
                    };
                    window.fetch = async (u, o) => {
                      const x = String(u);
                      if (x.includes('/api/chat_stream')) {
                        let msg = "";
                        try { msg = JSON.parse(o.body).message; } catch {}
                        window.__chatCalls.push(msg);
                        const mode = window.__chatScript.length
                          ? window.__chatScript.shift() : 'default';
                        if (mode === 'open') {
                          if (o && o.signal) o.signal.addEventListener(
                            'abort', () => {
                              try { window.__openCtl.close(); } catch {}
                              window.__openCtl = null;
                            });
                          return new Response(new ReadableStream({
                            start(c) {
                              window.__openCtl = c;
                              c.enqueue(frame({t: "started",
                                               session_id: "stub",
                                               opened: [], paths: []}));
                            }
                          }), {headers: {
                            'Content-Type': 'text/event-stream'}});
                        }
                        const s = (mode === 'default') ? a.dflt : mode;
                        return new Response(s, {headers: {
                          'Content-Type': 'text/event-stream'}});
                      }
                      if (x.includes('/api/stop'))
                        return new Response(JSON.stringify({ok: true}),
                          {headers: {'Content-Type': 'application/json'}});
                      if (x.includes('/api/pro/panels') ||
                          x.includes('/api/pro/control'))
                        return new Response(JSON.stringify({}), {headers: {
                          'Content-Type': 'application/json'}});
                      return orig(u, o);
                    };
                  }""", {"dflt": prov_sse(), "script": []})

            # ---- Fix 1: provenance ----
            page.fill("#prompt", "hola")
            page.click("#send")
            page.wait_for_selector(".message-row.assistant .message-text",
                                   timeout=15000)
            page.wait_for_timeout(800)
            chat = page.locator(
                ".message-row.assistant .message-text").last.inner_text()
            leak = ("memory-ops" in chat) or ("[20261101" in chat)
            report["f1_chat"] = chat[:120]
            report["f1_no_leak"] = not leak
            if leak or chat != "Claro, te llamaré Muse. Listo.":
                fail = True
            feed = page.locator("#activityFeed").inner_text()
            report["f1_activity"] = ("Memoria actualizada" in feed)
            if "Memoria actualizada" not in feed:
                fail = True
            raw = page.locator(
                "#activityFeed .activity-item .raw-data").first
            report["f1_normal_raw_hidden"] = not raw.is_visible()
            if raw.is_visible():
                fail = True
            page.click("#modeToggle")
            page.wait_for_timeout(500)
            report["f1_pro_raw_visible"] = raw.is_visible()
            ident = page.evaluate(
                """(b) => document.querySelector(
                    '#activityFeed .activity-item .raw-data').textContent === b
                """, BLOCK)
            report["f1_pro_byte_identity"] = ident
            if not raw.is_visible() or not ident:
                fail = True
            page.screenshot(path=os.path.join(args.shots, "u1-ux-prov.png"))
            report["screenshots"].append("u1-ux-prov.png")
            page.click("#modeToggle")
            page.wait_for_timeout(400)

            # ---- Fix 1b: display surfaces never show ids ----
            page.evaluate("() => { window.__chatScript = ['open']; }")
            page.fill("#prompt", "dime algo")
            page.click("#send")
            page.wait_for_selector("#stop:not(.hidden)", timeout=10000)
            page.evaluate("(o) => window.__emitSSE(o)",
                          {"t": "started", "session_id": "stub", "turn_id": "t9",
                           "provider": "meta", "deep": False,
                           "opened": [{"id": "20261101-1500-display",
                                       "title": "Display Title"}],
                           "paths": []})
            page.wait_for_timeout(300)
            feed1 = page.locator("#activityFeed").inner_text()
            f1b = {"titles": ("Display Title" in feed1),
                   "no_id": ("20261101-1500-display" not in feed1)}
            page.evaluate("(o) => window.__emitSSE(o)",
                          {"t": "writes", "writes": [
                              {"kind": "update",
                               "entry_id": "20261101-1500-display",
                               "op_id": "abc"}]})
            page.wait_for_timeout(300)
            toast_txt = page.locator("#toast").inner_text()
            f1b["toast"] = toast_txt
            f1b["toast_clean"] = ("20261101" not in toast_txt)
            if not (f1b["titles"] and f1b["no_id"] and f1b["toast_clean"]):
                fail = True
            page.click("#modeToggle")
            page.wait_for_timeout(300)
            raw_txt = page.locator(
                "#activityFeed .activity-item .raw-data").first.inner_text()
            f1b["pro_raw_has_id"] = ("20261101-1500-display" in raw_txt)
            if "20261101-1500-display" not in raw_txt:
                fail = True
            page.click("#modeToggle")
            page.evaluate("() => window.__closeSSE()")
            page.wait_for_timeout(500)
            report["f1b"] = f1b

            # ---- Fix 5c: project-ops fence never in Normal chat ----
            pblock = ("```project-ops\n"
                      "[{\"kind\": \"create_project\", \"name\": \"Demo\"}]\n```")
            page.evaluate("() => { window.__chatScript = ['open']; }")
            page.fill("#prompt", "crea Demo")
            page.click("#send")
            page.wait_for_selector("#stop:not(.hidden)", timeout=10000)
            page.evaluate("(o) => window.__emitSSE(o)",
                          {"t": "text", "text": "Carpeta lista. "})
            page.evaluate("(o) => window.__emitSSE(o)",
                          {"t": "project-ops", "block": pblock})
            page.wait_for_timeout(400)
            chat5 = page.locator(
                ".message-row.assistant .message-text").last.inner_text()
            f1c = {"clean": ("project-ops" not in chat5)}
            feed5 = page.locator("#activityFeed").inner_text()
            f1c["activity"] = ("Proyecto actualizado" in feed5)
            if not (f1c["clean"] and f1c["activity"]):
                fail = True
            page.click("#modeToggle")
            page.wait_for_timeout(300)
            prow = page.evaluate(
                """(b) => [...document.querySelectorAll(
                    '#activityFeed .activity-item .raw-data')]
                    .some(n => n.textContent === b)
                """, pblock)
            f1c["pro_byte_identity"] = prow
            if not prow:
                fail = True
            page.click("#modeToggle")
            page.evaluate("() => window.__closeSSE()")
            page.wait_for_function(
                "() => document.querySelector('#stop')"
                ".classList.contains('hidden')", timeout=10000)
            report["f1c"] = f1c

            # ---- Fix 2: composer during turn ----
            def sse(*frames):
                return "".join("data: " + json.dumps(f) + "\n\n"
                               for f in frames)

            steered_sse = sse({"t": "steered", "turn_id": "t1"},
                              {"t": "done", "terminal": "steered"})
            queued_sse = sse({"t": "queued"},
                             {"t": "done", "terminal": "queued"})
            instant_sse = sse({"t": "started", "session_id": "stub",
                               "opened": [], "paths": []},
                              {"t": "text", "text": "ok tercero."},
                              {"t": "done", "terminal": "completed"})
            page.evaluate("() => { window.__chatScript = ['open']; }")
            page.fill("#prompt", "primer mensaje")
            page.click("#send")
            page.wait_for_selector("#stop:not(.hidden)", timeout=10000)
            page.wait_for_timeout(300)
            f2 = {"prompt_enabled": page.locator("#prompt").is_enabled(),
                  "send_enabled": page.locator("#send").is_enabled(),
                  "stop_visible": page.locator("#stop").is_visible()}
            if not all(f2.values()):
                fail = True
            page.evaluate("(s) => { window.__chatScript = [s]; }",
                          steered_sse)
            page.fill("#prompt", "segundo mensaje")
            page.click("#send")
            page.wait_for_timeout(800)
            calls = page.evaluate("() => window.__chatCalls")
            f2["calls"] = calls
            if calls != ["hola", "dime algo", "crea Demo", "primer mensaje",
                           "segundo mensaje"]:
                fail = True
            feed2 = page.locator("#activityFeed").inner_text()
            f2["steered"] = ("Dirigido al turno en curso" in feed2)
            if "Dirigido al turno en curso" not in feed2:
                fail = True
            page.evaluate("(a) => { window.__chatScript = a; }",
                          [queued_sse, instant_sse])
            page.fill("#prompt", "tercer mensaje")
            page.click("#send")
            page.wait_for_selector("#queueBadge:not(.hidden)", timeout=10000)
            f2["badge"] = page.locator("#queueBadge").inner_text()
            if f2["badge"] != "En cola (1)":
                fail = True
            page.screenshot(path=os.path.join(args.shots, "u2-ux-composer.png"))
            report["screenshots"].append("u2-ux-composer.png")
            page.evaluate("() => window.__closeSSE()")
            page.wait_for_timeout(1500)
            calls2 = page.evaluate("() => window.__chatCalls")
            f2["calls_after"] = calls2
            if len(calls2) != len(calls) + 2 or calls2[-1] != "tercer mensaje":
                fail = True
            f2["badge_hidden"] = page.locator("#queueBadge").is_hidden()
            if not f2["badge_hidden"]:
                fail = True
            f2["idle"] = page.locator("#stop").is_hidden()
            if not f2["idle"]:
                fail = True
            report["f2"] = f2

            # ---- Fix 2b: queued delivery under realistic split timing ----
            # Live networks deliver 'done' and stream EOF in separate I/O
            # turns; a flush scheduled from the done handler races working.
            page.evaluate("() => { window.__chatScript = ['open']; }")
            page.fill("#prompt", "lento uno")
            page.click("#send")
            page.wait_for_selector("#stop:not(.hidden)", timeout=10000)
            page.evaluate("(a) => { window.__chatScript = a; }",
                          [queued_sse, instant_sse])
            page.fill("#prompt", "encolado dos")
            page.click("#send")
            page.wait_for_selector("#queueBadge:not(.hidden)", timeout=10000)
            page.fill("#prompt", "borrador tres")
            page.evaluate("(ms) => window.__closeSSESplit(ms)", 150)
            page.wait_for_function(
                "() => document.querySelector('#queueBadge')"
                ".classList.contains('hidden')", timeout=15000)
            page.wait_for_function(
                "() => document.querySelector('#stop')"
                ".classList.contains('hidden')", timeout=30000)
            f2b = {}
            calls3 = page.evaluate("() => window.__chatCalls")
            f2b["calls_tail"] = calls3[-3:]
            if calls3[-2:] != ["encolado dos", "encolado dos"]:
                fail = True
            last_asst = page.locator(
                ".message-row.assistant .message-text").last.inner_text()
            f2b["delivered"] = ("ok tercero." in last_asst)
            if "ok tercero." not in last_asst:
                fail = True
            dup = page.evaluate(
                "() => [...document.querySelectorAll("
                "'.message-row.user .message-text')]"
                ".filter(n => n.textContent === 'encolado dos').length")
            f2b["user_bubbles_encolado"] = dup
            if dup != 1:
                fail = True
            draft = page.locator("#prompt").input_value()
            f2b["draft_kept"] = (draft == "borrador tres")
            if draft != "borrador tres":
                fail = True
            f2b["idle"] = page.locator("#stop").is_hidden()
            if not f2b["idle"]:
                fail = True
            report["f2b"] = f2b
            page.fill("#prompt", "")

            # ---- Fix 2c: pre-window queue retries steer on started ----
            # Simulates the slow-start case: queued before turn-started,
            # then 'started' arrives and the client must steer, not wait.
            page.evaluate("() => { window.__chatScript = ['open']; }")
            page.fill("#prompt", "base ce")
            page.click("#send")
            page.wait_for_selector("#stop:not(.hidden)", timeout=10000)
            page.wait_for_timeout(400)
            page.evaluate("(a) => { window.__chatScript = a; }",
                          [queued_sse])
            page.fill("#prompt", "temprano ce")
            page.click("#send")
            page.wait_for_selector("#queueBadge:not(.hidden)", timeout=10000)
            page.evaluate("(a) => { window.__chatScript = a; }",
                          [steered_sse])
            page.evaluate("(o) => window.__emitSSE(o)",
                          {"t": "started", "session_id": "stub",
                           "turn_id": "tC", "provider": "meta",
                           "opened": [], "paths": []})
            page.wait_for_function(
                "() => document.querySelector('#queueBadge')"
                ".classList.contains('hidden')", timeout=10000)
            f2c = {}
            calls4 = page.evaluate("() => window.__chatCalls")
            f2c["retry_call"] = calls4[-1]
            if calls4[-1] != "temprano ce":
                fail = True
            feedc = page.locator("#activityFeed").inner_text()
            f2c["steered"] = ("Dirigido al turno en curso" in feedc)
            if "Dirigido al turno en curso" not in feedc:
                fail = True
            page.evaluate("() => window.__closeSSE()")
            page.wait_for_function(
                "() => document.querySelector('#stop')"
                ".classList.contains('hidden')", timeout=15000)
            f2c["idle"] = page.locator("#stop").is_hidden()
            if not f2c["idle"]:
                fail = True
            report["f2c"] = f2c

            # ---- Fix 3: activity status ----
            import re as _re
            page.evaluate("() => { window.__chatScript = ['open']; }")
            page.fill("#prompt", "estado?")
            page.click("#send")
            page.wait_for_selector("#turnStatusRow:not(.hidden)",
                                   timeout=10000)
            f3 = {"thinking": page.locator("#turnStatus").inner_text()}
            el1 = page.locator("#turnElapsed").inner_text()
            page.wait_for_timeout(700)
            el2 = page.locator("#turnElapsed").inner_text()
            f3["elapsed"] = [el1, el2]
            if f3["thinking"] != "Pensando…" or \
               not _re.fullmatch(r"\d+:\d\d", el1) or \
               not _re.fullmatch(r"\d+:\d\d", el2):
                fail = True
            page.evaluate("(o) => window.__emitSSE(o)",
                          {"t": "tool", "kind": "toolCall", "status": "running",
                           "toolName": "powershell", "command": "Get-ChildItem",
                           "text": ""})
            page.wait_for_timeout(300)
            f3["tool"] = page.locator("#turnStatus").inner_text()
            if f3["tool"] != "Usando una herramienta…":
                fail = True
            page.evaluate("(o) => window.__emitSSE(o)",
                          {"t": "text", "text": "Hola"})
            page.wait_for_timeout(300)
            f3["responding"] = page.locator("#turnStatus").inner_text()
            if f3["responding"] != "Respondiendo…":
                fail = True
            page.click("#modeToggle")
            page.evaluate("(o) => window.__emitSSE(o)",
                          {"t": "tool", "kind": "toolCall", "status": "running",
                           "toolName": "powershell", "text": ""})
            page.wait_for_timeout(300)
            f3["pro_native"] = page.locator("#turnStatus").inner_text()
            if f3["pro_native"] != "Usando una herramienta… · powershell":
                fail = True
            page.screenshot(path=os.path.join(args.shots, "u3-ux-status.png"))
            report["screenshots"].append("u3-ux-status.png")
            page.click("#modeToggle")
            page.evaluate("() => window.__closeSSE()")
            page.wait_for_function(
                "() => document.querySelector('#turnStatusRow')"
                ".classList.contains('hidden')", timeout=10000)
            f3["cleared"] = page.locator("#turnStatusRow").is_hidden()
            if not f3["cleared"]:
                fail = True
            page.evaluate("() => { window.__chatScript = ['open']; }")
            page.fill("#prompt", "detenme")
            page.click("#send")
            page.wait_for_selector("#stop:not(.hidden)", timeout=10000)
            page.click("#stop")
            page.wait_for_timeout(600)
            f3["stopped"] = page.locator("#turnStatus").inner_text()
            if f3["stopped"] != "Detenido":
                fail = True
            report["f3"] = f3

            # ---- Fix 3b: status clears even if the stream ends
            # without a terminal done frame (provider timeout path:
            # server emits only writes, then EOF). ----
            page.evaluate("() => { window.__chatScript = ['open']; }")
            page.fill("#prompt", "corte sin done")
            page.click("#send")
            page.wait_for_selector("#stop:not(.hidden)", timeout=10000)
            page.evaluate("(o) => window.__emitSSE(o)",
                          {"t": "text", "text": "algo"})
            page.wait_for_timeout(300)
            f3b = {"responding": page.locator("#turnStatus").inner_text()}
            if f3b["responding"] != "Respondiendo…":
                fail = True
            page.evaluate("() => window.__closeSSENoDone()")
            page.wait_for_function(
                "() => document.querySelector('#stop')"
                ".classList.contains('hidden')", timeout=10000)
            page.wait_for_function(
                "() => document.querySelector('#turnStatusRow')"
                ".classList.contains('hidden')", timeout=10000)
            f3b["row_hidden"] = True
            f3b["elapsed_hidden"] = page.locator(
                "#turnElapsed").is_hidden()
            if not f3b["elapsed_hidden"]:
                fail = True
            report["f3b"] = f3b

            if report["console_errors"] or report["page_errors"] or report["failed_requests"]:
                fail = True
            report["verdict"] = "pass" if not fail else "fail"
            print(json.dumps(report, indent=1, ensure_ascii=True))
        finally:
            try:
                with open(os.path.join(args.shots, "summary_ux.json"), "w",
                          encoding="utf-8") as f:
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
