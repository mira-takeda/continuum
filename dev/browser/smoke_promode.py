"""Pro Mode presentation smoke. DEV TOOL ONLY - not product runtime.

Canned SSE through the real UI: asserts Normal shows explanations with
collapsed technical details, Pro hides explanations and expands native
technical output, and toggling back restores Normal. No backend changes
are exercised (Phase 1 is presentation-only).

Usage (from repo root, env activated):
  python dev/browser/smoke_promode.py [--no-start --url ...]

Exit codes: 0 pass, 1 failure, 2 degraded (server unreachable).
"""
import argparse
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "dev", "browser"))
from smoke import poll_ready  # noqa: E402  (dev-only reuse)


def canned_sse():
    sys.path.insert(0, REPO)
    from memory import approvals as ap
    frame = {"t": "approval", "approvalId": "ap-pro-1",
             "requirementId": "rq-pro-1", "toolName": None,
             "subject": {"kind": "shell",
                         "command": "Get-ChildItem 'C:\\Users\\test\\Documents'",
                         "stages": ["Get-ChildItem 'C:\\Users\\test\\Documents'"]},
             "choices": [
                 {"choiceId": "c-allow", "label": "Allow once",
                  "decision": "allow", "acceptsFeedback": False},
                 {"choiceId": "c-deny", "label": "Reject",
                  "decision": "deny", "acceptsFeedback": False}],
             "rawArgs": ""}
    frame["explanation"] = ap.explain_approval(frame)
    parts = [json.dumps({"t": "started", "session_id": "stub",
                         "opened": [], "paths": []}),
             json.dumps({"t": "tool", "kind": "toolCall", "status": "completed",
                         "toolName": "powershell",
                         "command": "Get-ChildItem 'C:\\Users\\test\\Documents'",
                         "text": "Directory listing ok"}),
             json.dumps(frame),
             json.dumps({"t": "usage", "modelId": "muse-spark-pro-1",
                         "promptTokens": 111, "totalTokens": 222}),
             json.dumps({"t": "context", "windowTokens": 1000,
                         "usedTokens": 250, "pressure": 0.25}),
             json.dumps({"t": "status", "method": "session/statusChanged",
                         "status": "running"}),
             json.dumps({"t": "todos", "sessionId": "stub",
                         "items": [{"status": "inProgress",
                                    "text": "Canned todo alpha"},
                                   {"status": "pending",
                                    "text": "Canned todo beta"}],
                         "revision": 7, "sourceTool": "todo_write"}),
             json.dumps({"t": "text", "text": "Stub pro reply."}),
             json.dumps({"t": "done", "terminal": "completed"})]
    return "".join("data: " + p + "\n\n" for p in parts), frame


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

    if not args.no_start:
        server_log = open(os.path.join(args.shots, "server_promode.log"), "w",
                          encoding="utf-8")
        server_proc = subprocess.Popen(
            [sys.executable, "-u", "server.py", "--port", str(args.port)],
            cwd=REPO, stdout=server_log, stderr=subprocess.STDOUT)
    try:
        boot = poll_ready(url, 45)
    except RuntimeError as e:
        report["fatal"] = str(e)
        print(json.dumps(report, indent=1, ensure_ascii=True))
        if server_proc is not None and server_proc.poll() is None:
            server_proc.terminate()
        if server_log is not None:
            server_log.close()
        return 2
    if server_proc is not None and server_proc.poll() is not None:
        external = True
    if not boot.get("connected"):
        report["fatal"] = "backend reports Muse unavailable"
        print(json.dumps(report, indent=1, ensure_ascii=True))
        if server_proc is not None and not external and server_proc.poll() is None:
            server_proc.terminate()
        if server_log is not None:
            server_log.close()
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
            sse, frame = canned_sse()
            page.goto(url + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#page .chat-layout", timeout=15000)
            page.wait_for_selector("#runtimeChip.connected", timeout=20000)
            page.wait_for_timeout(1000)
            panels = {
                "msp_session": "msp-pro-1", "host_alive": True,
                "session": {"session": {
                    "sessionId": "msp-pro-1", "status": "idle",
                    "modelId": "muse-spark-pro-1", "turnCount": 3,
                    "workspaceRoot": "C:\\Users\\test",
                    "approvalMode": {"mode": "promptUnmatched"}}},
                "tasks": {"tasks": [
                    {"taskId": "task-abc123", "kind": "tool",
                     "status": "inProgress", "tool": "powershell"}]},
                "usage": {"usage": {"tier": "pro-tier",
                                    "window": {"usedPct": 11},
                                    "weekly": {"usedPct": 22}}},
                "pending": {"approvals": [], "userInputs": []}}
            page.evaluate(
                """(a) => {
                    const orig = window.fetch.bind(window);
                    window.__controlCalls = [];
                    const canned = {
                      listModels: {ok: true, result: {models: [
                        {modelId: 'model-a', displayLabel: 'Model A',
                         providerId: 'meta', isActive: true,
                         variants: ['low', 'medium', 'high']},
                        {modelId: 'model-b', displayLabel: 'Model B',
                         providerId: 'meta', isActive: false}]}},
                      listSkills: {ok: true, result: {skills: [
                        {selector: 'fix-bug', displayName: 'Fix Bug',
                         description: 'd', source: 'bundled'},
                        {selector: 'acme:deploy', displayName: 'Deploy',
                         description: 'd', source: 'plugin'}]}}
                    };
                    window.fetch = async (u, o) => {
                      const s = String(u);
                      if (s.includes('/api/chat_stream'))
                        return new Response(a.sse, {headers: {
                          'Content-Type': 'text/event-stream'}});
                      if (s.includes('/api/pro/panels'))
                        return new Response(JSON.stringify(a.panels),
                          {headers: {'Content-Type': 'application/json'}});
                      if (s.includes('/api/pro/control')) {
                        const body = JSON.parse(o.body);
                        window.__controlCalls.push(body);
                        if(body.op==='listPending')canned.listPending={ok:true,result:{approvals:[
                          {approvalId:'ap-pend-1',toolName:'shell',
                           subject:{command:'Remove-Item C:/Temp/x'},
                           choices:[{choiceId:'c1'},{choiceId:'c2'}]}],userInputs:[]}};
                        if(body.op==='expProfiles')canned.expProfiles={ok:true,result:{profiles:[
                          {profileId:':ask-me',label:'Ask me',available:true,reviewerAuthority:'human'},
                          {profileId:':read-only',label:'Read only',available:false,reviewerAuthority:'none'}]}};
                        if(body.op==='expLogStreams')canned.expLogStreams={ok:true,result:{streams:[
                          {stream:{id:'stream-1',kind:'run'},
                           firstAvailable:{id:'e0',sequence:0},prunedThrough:null,
                           tail:{id:'e9',sequence:9}}]}};
                        if(body.op==='expLogPage')canned.expLogPage={ok:true,result:{records:[
                          {seq:1,note:'row-one'}],
                          nextCursor:{after:{id:'e1',sequence:1}}}};
                        const r = canned[body.op] || {ok: true, result:
                          {echoOp: body.op, args: body.args}};
                        return new Response(JSON.stringify(r),
                          {headers: {'Content-Type': 'application/json'}});
                      }
                      return orig(u, o);
                    };
                  }""", {"sse": sse, "panels": panels})
            report["toggle_default"] = page.locator("#modeToggle").inner_text()
            page.fill("#prompt", "stub turn")
            page.click("#send")
            page.wait_for_selector(".approval-card", timeout=15000)
            page.wait_for_timeout(1000)
            expl = page.locator(".approval-card .approval-expl")
            pre = page.locator(".approval-card .tech-details pre")
            raw = page.locator("#activityFeed .activity-item .raw-data").first
            n = {"expl_visible": expl.is_visible(),
                 "tech_collapsed": not pre.is_visible(),
                 "raw_hidden": not raw.is_visible()}
            report["normal"] = n
            if not all(n.values()):
                fail = True
            page.screenshot(path=os.path.join(args.shots, "m1-normal.png"))
            report["screenshots"].append("m1-normal.png")

            page.click("#modeToggle")
            page.wait_for_timeout(500)
            p = {"body_pro": page.evaluate("() => document.body.classList.contains('pro')"),
                 "label": page.locator("#modeToggle").inner_text(),
                 "expl_hidden": not expl.is_visible(),
                 "tech_expanded": pre.is_visible(),
                 "raw_visible": raw.is_visible()}
            report["pro"] = p
            if not (p["body_pro"] and p["label"] == "Pro" and p["expl_hidden"]
                    and p["tech_expanded"] and p["raw_visible"]):
                fail = True
            page.screenshot(path=os.path.join(args.shots, "m2-pro.png"))
            report["screenshots"].append("m2-pro.png")
            ident = page.evaluate(
                """(a) => {
                    const s = JSON.stringify(a.subj, null, 1)
                      + (a.raw ? '\\nargs: ' + a.raw : '');
                    const t = document.querySelector(
                      '.approval-card .tech-details pre').textContent;
                    return {match: s === t};
                  }""",
                {"subj": frame["subject"], "raw": frame["rawArgs"]})
            report["byte_identity"] = ident
            if not ident.get("match"):
                fail = True

            page.click("#modeToggle")
            page.wait_for_timeout(500)
            back = {"body_pro": page.evaluate("() => document.body.classList.contains('pro')"),
                    "label": page.locator("#modeToggle").inner_text(),
                    "expl_visible": expl.is_visible(),
                    "tech_collapsed": not pre.is_visible()}
            report["back_to_normal"] = back
            if back["body_pro"] or back["label"] != "Normal" or \
               not back["expl_visible"] or not back["tech_collapsed"]:
                fail = True
            page.screenshot(path=os.path.join(args.shots, "m3-normal-again.png"))
            report["screenshots"].append("m3-normal-again.png")

            # ---- Phase 2: read-only Pro panels ----
            pro_panels = page.locator("#proPanels")
            p2_hidden = not pro_panels.is_visible()
            report["p2_normal_hidden"] = p2_hidden
            if not p2_hidden:
                fail = True
            page.click("#modeToggle")  # entering Pro refreshes panels (stubbed)
            page.wait_for_selector("#proPanelsBody .pro-sec", timeout=10000)
            page.wait_for_timeout(800)
            body_text = page.locator("#proPanelsBody").inner_text()
            want = ["msp-pro-1", "muse-spark-pro-1", "111", "222", "1000",
                    "250", "Canned todo alpha", "inProgress", "powershell",
                    "task-abc", "pro-tier", "promptUnmatched"]
            vals = {c: (c in body_text) for c in want}
            report["p2_values"] = vals
            raw_n = page.locator("#proPanelsBody details.raw-wrap").count()
            report["p2_raw_dumps"] = raw_n
            if not all(vals.values()) or raw_n != 6:
                fail = True
            page.screenshot(path=os.path.join(args.shots, "m4-pro-panels.png"))
            report["screenshots"].append("m4-pro-panels.png")
            page.evaluate("document.querySelector('#contextRail').scrollTo({top: 99999})")
            page.wait_for_timeout(400)
            page.locator("#contextRail").screenshot(
                path=os.path.join(args.shots, "m4b-rail.png"))
            report["screenshots"].append("m4b-rail.png")
            report["geo"] = page.evaluate("""() => {
              const q = s => document.querySelector(s);
              const r = s => { const e = q(s); if (!e) return null;
                const b = e.getBoundingClientRect();
                return {x: Math.round(b.x), y: Math.round(b.y),
                        w: Math.round(b.width), h: Math.round(b.height)}; };
              const feed = q('#activityFeed');
              const cs = feed ? getComputedStyle(feed) : null;
              const pre = q('#activityFeed .activity-item .raw-data');
              const pcs = pre ? getComputedStyle(pre) : null;
              return {feed: r('#activityFeed'), card: r('.activity-card'),
                pre: r('#activityFeed .activity-item .raw-data'),
                panels: r('#proPanels'), refresh: r('#proRefresh'),
                feedOverflowY: cs && cs.overflowY, feedMaxH: cs && cs.maxHeight,
                preDisplay: pcs && pcs.display, prePos: pcs && pcs.position,
                railScrollTop: Math.round(q('#contextRail').scrollTop),
                railScrollH: Math.round(q('#contextRail').scrollHeight)};
            }""")
            try:
                page.click("#proRefresh", timeout=5000)
            except Exception as e:
                report["refresh_click_error"] = str(e)[:200]
                page.click("#proRefresh", force=True)
                report["refresh_forced"] = True
                fail = True
            page.wait_for_timeout(800)
            refr = "msp-pro-1" in page.locator("#proPanelsBody").inner_text()
            report["p2_refresh"] = refr
            if not refr:
                fail = True
            page.click("#modeToggle")
            page.wait_for_timeout(400)
            p2_back = not pro_panels.is_visible()
            report["p2_back_hidden"] = p2_back
            if not p2_back:
                fail = True

            # ---- Phase 3: native controls ----
            page.click("#modeToggle")  # enter Pro: auto-loads models+skills
            page.wait_for_selector("#ctlModel option", timeout=10000,
                                       state="attached")
            page.wait_for_selector("#ctlSkills .skill-row", timeout=10000)
            page.wait_for_selector("#ctlEffort option", timeout=10000,
                                       state="attached")
            model_opts = page.locator("#ctlModel option").all_inner_texts()
            effort_opts = page.locator("#ctlEffort option").all_inner_texts()
            n_skills = page.locator("#ctlSkills .skill-row").count()
            report["p3_options"] = {"models": model_opts,
                                    "efforts": effort_opts,
                                    "skills": n_skills}
            if model_opts != ["Model A \u25cf", "Model B"] or \
               effort_opts != ["low", "medium", "high"] or n_skills != 2:
                fail = True
            page.select_option("#ctlModel", "model-b")
            page.click("#ctlModelApply")
            page.select_option("#ctlEffort", "high")
            page.click("#ctlEffortApply")
            page.fill("#ctlName", "Pro Session 1")
            page.click("#ctlRename")
            page.fill("#ctlGoal", "Ship pro mode")
            page.click("#ctlGoalSet")
            page.click("#ctlGoalPause")
            page.click("#ctlFork")
            row = page.locator("#ctlSkills .skill-row").first
            row.locator(".skill-scope").select_option("user")
            row.locator(".skill-act").select_option("off")
            row.locator("button").click()
            dialog_actions = []

            def on_dialog(d):
                report.setdefault("dialogs", []).append(d.message[:80])
                if dialog_actions and dialog_actions.pop(0) == "dismiss":
                    d.dismiss()
                else:
                    d.accept()

            page.on("dialog", on_dialog)
            page.click("#ctlCompact")
            page.wait_for_timeout(1200)
            calls = page.evaluate("() => window.__controlCalls")
            mut = [(c.get("op"), c.get("args")) for c in calls
                   if c.get("op") not in ("listModels", "listSkills")][-8:]
            report["p3_calls"] = mut
            exp = [("setModel", {"modelId": "model-b", "providerId": "meta",
                                 "displayLabel": "Model B"}),
                   ("setReasoningEffort", {"effort": "high"}),
                   ("rename", {"name": "Pro Session 1"}),
                   ("goalSet", {"objective": "Ship pro mode"}),
                   ("goalPause", {}),
                   ("fork", {}),
                   ("skillActivation", {"selector": "fix-bug", "scope": "user",
                                        "activation": "off"}),
                   ("compact", {})]
            if mut != exp:
                fail = True
            if not report.get("dialogs"):
                fail = True
            res_text = page.locator("#ctlResult").inner_text()
            if '"echoOp": "compact"' not in res_text:
                fail = True
            page.evaluate("document.querySelector('#contextRail').scrollTo({top: 99999})")
            page.wait_for_timeout(400)
            page.locator("#contextRail").screenshot(
                path=os.path.join(args.shots, "m5-pro-controls.png"))
            report["screenshots"].append("m5-pro-controls.png")
            page.click("#modeToggle")
            page.wait_for_timeout(400)
            p3_back = not page.locator("#proControls").is_visible()
            report["p3_back_hidden"] = p3_back
            if not p3_back:
                fail = True

            # ---- Phase 4: approval mode + pending ----
            page.click("#modeToggle")
            page.wait_for_selector("#ctlMode option", timeout=10000,
                                       state="attached")
            page.wait_for_selector("#ctlPending .rail-mini-row", timeout=10000)
            mode_opts = page.locator("#ctlMode option").all_inner_texts()
            cur_mode = page.locator("#ctlMode").input_value()
            pend_text = page.locator("#ctlPending").inner_text()
            report["p4"] = {"modes": mode_opts, "current": cur_mode,
                            "pending": ("ap-pend-1" in pend_text)}
            if mode_opts != ["allowAll", "promptUnmatched \u25cf",
                             "onRequest", "denyUnmatched"] or \
               cur_mode != "promptUnmatched" or "ap-pend-1" not in pend_text:
                fail = True
            calls_before = page.evaluate(
                "() => window.__controlCalls.length")
            dialog_actions.append("dismiss")
            page.select_option("#ctlMode", "allowAll")
            page.click("#ctlModeApply")
            page.wait_for_timeout(600)
            calls_mid = page.evaluate("() => window.__controlCalls.length")
            report["p4_dismiss_no_call"] = (calls_mid == calls_before)
            if calls_mid != calls_before:
                fail = True
            page.click("#ctlModeApply")  # confirm again, this time accept
            page.wait_for_timeout(800)
            calls = page.evaluate("() => window.__controlCalls")
            last = calls[-1]
            report["p4_allow_call"] = last
            if last.get("op") != "setApprovalMode" or \
               last.get("args") != {"mode": "allowAll"}:
                fail = True
            if '"echoOp": "setApprovalMode"' not in \
               page.locator("#ctlResult").inner_text():
                fail = True
            page.evaluate("document.querySelector('#contextRail').scrollTo({top: 99999})")
            page.wait_for_timeout(400)
            page.locator("#proControls").screenshot(
                path=os.path.join(args.shots, "m6-pro-approval.png"))
            report["screenshots"].append("m6-pro-approval.png")
            page.click("#modeToggle")
            page.wait_for_timeout(400)
            p4_back = not page.locator("#proControls").is_visible()
            report["p4_back_hidden"] = p4_back
            if not p4_back:
                fail = True

            # ---- Phase 5: experimental-gated ----
            page.click("#modeToggle")
            page.wait_for_selector("#ctlProfile option", timeout=10000,
                                       state="attached")
            page.wait_for_selector("#ctlStream option", timeout=10000,
                                       state="attached")
            exp_closed = page.evaluate(
                "() => !document.querySelector('#proControls .exp-sec').open")
            report["p5_gated"] = exp_closed
            if not exp_closed:
                fail = True
            page.evaluate("document.querySelector('#proControls .exp-sec').open = true")
            page.wait_for_timeout(300)
            prof_opts = page.locator("#ctlProfile option").all_inner_texts()
            prof_disabled = page.locator("#ctlProfile option").nth(1).is_disabled()
            stream_opts = page.locator("#ctlStream option").all_inner_texts()
            report["p5"] = {"profiles": prof_opts,
                            "second_disabled": prof_disabled,
                            "streams": stream_opts}
            if prof_opts != ["Ask me", "Read only (no disponible)"] or \
               not prof_disabled or stream_opts != ["run:stream-1"]:
                fail = True
            page.select_option("#ctlProfile", ":ask-me")
            page.click("#ctlProfileApply")
            page.wait_for_timeout(600)
            calls = page.evaluate("() => window.__controlCalls")
            prof_calls = [c for c in calls if c.get("op") == "expSetProfile"]
            report["p5_profile_call"] = prof_calls[-1] if prof_calls else None
            if not prof_calls or \
               prof_calls[-1].get("args") != {"profileId": ":ask-me"}:
                fail = True
            if not any("perfil experimental" in d
                       for d in report.get("dialogs", [])):
                fail = True
            page.select_option("#ctlStream", "0")
            page.click("#ctlLogPage")
            page.wait_for_selector("#ctlLogOut .raw-data", timeout=10000,
                                       state="attached")
            page.evaluate("document.querySelector('#ctlLogOut details').open = true")
            page.wait_for_selector("#ctlLogOut .raw-data", timeout=10000)
            page.click("#ctlLogNext")
            page.wait_for_timeout(600)
            page.evaluate("document.querySelector('#ctlLogOut details').open = true")
            calls = page.evaluate("() => window.__controlCalls")
            log_calls = [c.get("args") for c in calls
                         if c.get("op") == "expLogPage"]
            report["p5_log_calls"] = log_calls
            if len(log_calls) < 2 or \
               log_calls[0].get("stream") != {"id": "stream-1",
                                              "kind": "run"} or \
               log_calls[0].get("limit") != 20 or \
               log_calls[1].get("from") != {"id": "e1", "sequence": 1}:
                fail = True
            if "row-one" not in page.locator("#ctlLogOut").inner_text():
                fail = True
            page.evaluate("document.querySelector('#contextRail').scrollTo({top: 99999})")
            page.wait_for_timeout(400)
            page.locator("#proControls").screenshot(
                path=os.path.join(args.shots, "m7-pro-exp.png"))
            report["screenshots"].append("m7-pro-exp.png")
            page.click("#modeToggle")
            page.wait_for_timeout(400)
            p5_back = not page.locator("#proControls").is_visible()
            report["p5_back_hidden"] = p5_back
            if not p5_back:
                fail = True

            if report["console_errors"] or report["page_errors"] or report["failed_requests"]:
                fail = True
            report["verdict"] = "pass" if not fail else "fail"
            print(json.dumps(report, indent=1, ensure_ascii=True))
        finally:
            try:
                with open(os.path.join(args.shots, "summary_promode.json"), "w",
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
