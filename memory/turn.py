"""Shared MSP turn core: the ONE path for product chat and eval turns.

Both the browser server and the eval harness drive Muse Code through
stream_turn() + build_turn_text() + norm_frame() so they cannot silently
diverge. All Muse content stays verbatim; this module only frames and routes.
"""
import json
import time

from . import core as core_mod
from .msp import MSPError

TURN_TIMEOUT = 600


def index_gists_compact(idx, limit=30):
    rows = sorted(idx.get("entries", {}).items(),
                  key=lambda kv: kv[1].get("access_score", 0), reverse=True)[:limit]
    return "\n".join(f"- [{eid}] {v.get('title')} ({','.join(v.get('types', []))}): {v.get('gist','')[:100]}"
                     for eid, v in rows) or "(index empty)"


MEMORY_OPS_HELP = (
    "Memory protocol (local encyclopedia, machine-read): if this turn taught you durable "
    "facts/decisions/corrections/preferences about the user or your shared history, append a "
    "```memory-ops fenced JSON list at the very end of your reply (0-3 ops). Kinds: "
    '{"kind":"create","title","types":["fact|identity|history|correction|experience"],"body","source":"user-stated|conversation|model-inference"} '
    '| {"kind":"update","id","body_append"} | {"kind":"supersede","old_id","title","types","body"} '
    '| {"kind":"confirm","id"} | {"kind":"link","from","to","relation","weight"}. '
    "Use only [ids] shown above; never invent ids. At most 2 creates per turn: combine related "
    "facts from one turn into a single entry with a complete body (names, values, and context all "
    "in the body text, so future retrieval can match on any of them). Emit nothing when nothing "
    "durable happened. Your visible reply must stand alone; the block only maintains memory.")


def build_turn_text(root, message, ret, idx, extra_before_user="", leak_guard=False):
    """Exact product memory path: CORE + selective entries + index + protocol.

    extra_before_user (default "") inserts one optional block — the active
    project context — between the encyclopedia and the user message. Empty
    produces byte-identical output to the frozen eval path.
    """
    core_text = core_mod.render(root)
    opened_block = "\n".join(
        f"Entry [{e['id']}] ({e['status']}/{e['state']}) {e['title']}:\n{e['body'][:1200]}"
        for e in ret["opened"]) or "(no entries opened)"
    cite_note = ("Use [ids] ONLY inside the ```memory-ops block. In your visible reply refer "
                   "to entries by title; never print bracketed entry ids.\n" if leak_guard else "")
    entries_head = ("Retrieved encyclopedia entries for this turn:\n" if leak_guard else
                    "Retrieved encyclopedia entries for this turn (cite [id] when used):\n")
    base = (f"<personal-memory>\nCORE (auto-derived, never hand-edited):\n{core_text}\n\n"
            f"{entries_head}{opened_block}\n\n{cite_note}"
            f"Compact index:\n{index_gists_compact(idx)}\n\n{MEMORY_OPS_HELP}\n</personal-memory>\n\n")
    if extra_before_user:
        base += extra_before_user.rstrip() + "\n\n"
    return base + f"USER MESSAGE:\n{message}"


def norm_frame(frame, want_sid):
    """Normalize one MSP notification to a small view event (verbatim content)."""
    m = frame.get("method", "")
    p = frame.get("params", {}) or {}
    if frame.get("method") is None and "id" in frame:
        return None  # rpc response; not a view event
    if p.get("sessionId") and p.get("sessionId") != want_sid:
        return None
    if m == "item/delta":
        d = p.get("delta")
        text = d if isinstance(d, str) else json.dumps(d)[:2000]
        return {"t": "text", "text": text or "", "field": p.get("field"),
                "itemId": str(p.get("itemId") or "")[:8]}
    if m in ("item/started", "item/updated", "item/completed"):
        it = p.get("item") or {}
        ev = {"t": "tool", "event": m.split("/")[1], "kind": it.get("kind"),
              "status": it.get("status"), "itemId": str(p.get("itemId") or it.get("itemId") or "")[:8]}
        for k in ("text", "title", "name", "toolName", "command", "path", "url"):
            if it.get(k):
                ev[k] = str(it[k])[:800]
        if it.get("args"):
            ev["args"] = str(it["args"])[:800]
        return ev
    if m in ("approval/requested", "approval/request"):
        return {"t": "approval", "approvalId": p.get("approvalId"),
                "requirementId": p.get("currentRequirementId"),
                "toolName": p.get("toolName"), "subject": p.get("subject"),
                "choices": [{"choiceId": c.get("choiceId"), "label": c.get("label"),
                             "decision": c.get("decision"),
                             "acceptsFeedback": bool(c.get("acceptsFeedback"))}
                            for c in (p.get("availableChoices") or [])],
                "rawArgs": str(p.get("rawArgs") or "")[:2000]}
    if m in ("approval/resolved", "approval/updated"):
        return {"t": "approval-resolved", "method": m,
                "approvalId": p.get("approvalId"), "params": p}
    if m == "userInput/requested":
        return {"t": "questions", "userInputId": p.get("userInputId"),
                "questions": p.get("questions"), "toolName": p.get("toolName")}
    if m == "userInput/settled":
        return {"t": "questions-settled", "userInputId": p.get("userInputId"),
                "params": p}
    if m == "session/tokenUsage":
        return {"t": "usage", "modelId": p.get("modelId"), "turnId": p.get("turnId"),
                "promptTokens": p.get("promptTokens"), "totalTokens": p.get("totalTokens"),
                "usage": p.get("usage")}
    if m == "session/contextUsage":
        return {"t": "context", "windowTokens": p.get("windowTokens"),
                "usedTokens": p.get("usedTokens"), "pressure": p.get("pressure")}
    if m == "turn/completed":
        return {"t": "done", "terminal": p.get("terminal"), "error": p.get("error"),
                "reason": p.get("reason"), "usage": p.get("usage"),
                "durationMs": p.get("durationMs"), "turnId": p.get("turnId")}
    if m in ("turn/started", "turn/foregroundCompleted", "session/statusChanged"):
        return {"t": "status", "method": m, "status": p.get("status")}
    if m == "session/todoListChanged":
        # Wholesale replace per schema: empty items = cleared list, not no-op.
        return {"t": "todos", "sessionId": p.get("sessionId"), "items": p.get("items", []),
                "revision": p.get("revision"), "sourceTool": p.get("sourceTool")}
    if m == "usage/changed":
        return {"t": "usage-changed", "usage": p.get("usage")}
    if m == "__host_eof__":
        return {"t": "error", "text": "Muse host process ended unexpectedly."}
    if m.startswith(("session/", "turn/", "view/")):
        return None  # routine lifecycle noise
    return {"t": "event", "method": m, "params": str(p)[:2000]}


def stream_turn(host, sid, full_text, display_text, timeout=TURN_TIMEOUT, on_idle=None):
    """Run one turn; yield normalized events. First yields pending re-surface,
    then {"t":"turn-started","turn_id":...}, then routed MSP events, ending at
    the 'done' event or silently at timeout (detect via summarize())."""
    try:
        pend = host.list_pending(sid) or {}
    except Exception:
        pend = {}
    for p in pend.get("approvals", []) or []:
        ev = norm_frame({"method": "approval/requested", "params": p}, sid)
        if ev:
            yield ev
    for p in pend.get("userInputs", []) or []:
        ev = norm_frame({"method": "userInput/requested", "params": p}, sid)
        if ev:
            yield ev
    start = host.start_turn(sid, full_text, display_text=display_text[:500])
    expected_turn = (start or {}).get("turnId")
    yield {"t": "turn-started", "turn_id": expected_turn}
    item_kinds = {}
    seen_approvals = set()
    last_idle = time.time()
    deadline = time.time() + timeout
    while time.time() < deadline:
        fr = host.notifications(timeout=0.5, sid=sid)
        if on_idle is not None and time.time() - last_idle > 15:
            try:
                on_idle()
            except Exception:
                return
            last_idle = time.time()
        if fr is None:
            if not host.alive():
                yield {"t": "error", "text": "Muse host process died mid-turn."}
                return
            continue
        ev = norm_frame(fr, sid)
        if ev is None:
            continue
        if ev.get("t") == "done":
            tid = ev.get("turnId")
            if expected_turn and tid and tid != expected_turn:
                continue  # stale: completion of an older turn on this session
        if ev.get("t") == "approval":
            aid = ev.get("approvalId")
            if aid and aid in seen_approvals:
                continue  # de-duplicated delivery (request + requested)
            if aid:
                seen_approvals.add(aid)
        if ev.get("t") in ("approval", "questions", "approval-resolved", "questions-settled"):
            deadline = time.time() + timeout  # deciding; don't time out
        if ev.get("t") == "tool" and ev.get("itemId") and ev.get("kind"):
            item_kinds[ev["itemId"]] = ev["kind"]
        if ev.get("t") == "text":
            # Route by originating item: agent message vs other activity
            # (e.g. reasoning), matching terminal placement. Verbatim both.
            if ev.get("itemId") and item_kinds.get(ev["itemId"], "agentMessage") != "agentMessage":
                ev = {"t": "activity", "kind": item_kinds[ev["itemId"]], "text": ev.get("text", "")}
        yield ev
        if ev.get("t") == "done":
            return


def summarize(events):
    """Pure fold over stream_turn() output. timed_out = no 'done' seen."""
    text, usage, terminal, error = [], {}, None, None
    for ev in events:
        if ev.get("t") == "text":
            text.append(ev.get("text", ""))
        elif ev.get("t") == "usage":
            usage = ev
        elif ev.get("t") == "done":
            terminal, error = ev.get("terminal"), ev.get("error")
    return {"text": "".join(text), "usage": usage, "terminal": terminal,
            "error": error, "timed_out": terminal is None}
