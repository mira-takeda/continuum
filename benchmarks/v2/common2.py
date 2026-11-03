"""V2 shared helpers: approval policy, usage norm, derived cost, V1 scoring.

Scoring rules are IDENTICAL to V1 (benchmark_protocol §7/§9).
"""
import csv
import json
import os
import re

COST_IN_PER_M = 0.10
COST_OUT_PER_M = 0.20

ALLOW_DECISIONS = {"approved", "approve", "allow", "allowed", "allow_once"}
DENY_DECISIONS = {"abort", "aborted", "deny", "denied", "reject", "rejected"}

PATH_RES = [
    re.compile(r"[A-Za-z]:\\\\[^\s\"']*"),      # C:\... (also \\?\C:\...)
    re.compile(r"\\\\\\\\\?\\[^\s\"']*"),        # \\?\... (escaped form)
    re.compile(r"~[\\/][^\s\"']*"),              # ~/...
    re.compile(r"\$env:[^\s\"']*"),              # $env:...
    re.compile(r"(?<![\w:.])([\w.][\w.\-]*[\\/][\w.\\\/\-]+)"),  # bench-files/x
]


def _inside(child, parent):
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(child)),
                                   os.path.normcase(os.path.abspath(parent))]) == \
            os.path.normcase(os.path.abspath(parent))
    except ValueError:
        return False


def paths_confined(text, root):
    """True iff every path-like token in text resolves under root.

    No path-like tokens at all -> confined (read-only/metadata op).
    """
    seen = []
    for rx in PATH_RES:
        seen += [m.group(0).rstrip(".,;:!?\"'") for m in rx.finditer(text or "")]
    for tok in seen:
        low = tok.lower()
        if low.startswith("\\\\?\\"):
            tok = tok[4:]
        if tok.startswith("~") or low.startswith("$env:"):
            home = os.path.expanduser("~")
            rest = tok[1:] if tok.startswith("~") else tok[5:]
            rest = rest.lstrip("/\\")
            tok = rest or home
            if rest:
                tok = os.path.join(home, rest)
        if not os.path.isabs(tok):
            tok = os.path.join(root, tok)
        if not _inside(tok, root):
            return False
    return True


def decide_approval(ev, root):
    """Deterministic policy per protocol §3. Returns (choice_id_or_None,
    decision_label, confined). choice None only when no choices exist."""
    subj = json.dumps({"s": ev.get("subject"), "r": ev.get("rawArgs"),
                       "t": ev.get("toolName")})[:2000]
    choices = ev.get("choices") or []
    confined = paths_confined(subj, root)
    allow = [c for c in choices
             if (c.get("decision") or "").lower() in ALLOW_DECISIONS]
    deny = [c for c in choices
            if (c.get("decision") or "").lower() in DENY_DECISIONS]
    if confined and allow:
        return allow[0].get("choiceId"), "allow-confined", True
    if deny:
        return deny[0].get("choiceId"), \
            "deny-unconfined" if not confined else "deny-noallow", confined
    if allow:
        return allow[0].get("choiceId"), "allow-fallback", confined
    if choices:
        return choices[0].get("choiceId"), "first-fallback", confined
    return None, "no-choices", confined


def answers_for(questions):
    out = []
    for q in questions or []:
        one = {"questionId": q.get("id")}
        opts = q.get("options") or []
        if opts and opts[0].get("label"):
            one["selectedLabel"] = str(opts[0]["label"])[:120]
        else:
            one["freeText"] = ""
        out.append(one)
    return out


def derived_cost(prompt_tokens, output_tokens):
    if prompt_tokens is None or output_tokens is None:
        return None
    return round(prompt_tokens / 1e6 * COST_IN_PER_M
                 + output_tokens / 1e6 * COST_OUT_PER_M, 6)


def norm_direct(usage, context_ev):
    u = usage or {}
    inner = u.get("usage") or {}
    prompt = u.get("promptTokens")
    out = inner.get("outputTokens")
    if out is None and u.get("totalTokens") is not None and prompt is not None:
        out = u["totalTokens"] - prompt
    return {
        "prompt_tokens": prompt,
        "output_tokens": out,
        "total_tokens": u.get("totalTokens"),
        "cached_tokens": inner.get("cachedTokens"),
        "reasoning_tokens": inner.get("reasoningTokens"),
        "model": u.get("modelId"),
        "turn_id": u.get("turnId"),
        "window_tokens": (context_ev or {}).get("windowTokens"),
        "cost_derived_usd": derived_cost(prompt, out),
    }


def norm_continuum(logline):
    e = logline or {}
    prompt = e.get("prompt_tokens")
    total = e.get("total_tokens")
    out = total - prompt if (prompt is not None and total is not None) else None
    return {
        "prompt_tokens": prompt,
        "output_tokens": out,
        "total_tokens": total,
        "cached_tokens": None,
        "reasoning_tokens": None,
        "model": e.get("model"),
        "turn_id": e.get("turn"),
        "window_tokens": None,
        "server_latency_ms": e.get("latency_ms"),
        "cost_derived_usd": derived_cost(prompt, out),
    }


# ---------------- deterministic continuity scoring (V1-identical) ----

def _t(s):
    return (s or "").lower()


def s_numbered(reply):
    t = _t(reply)
    return 1 if ("1." in t and "2." in t and "3." in t) else 0


def s_present(reply, word):
    return 1 if word in _t(reply) else 0


def s_b3(reply):
    t = _t(reply)
    if "terracotta" not in t:
        return 0
    if "not terracotta" in t or "n't terracotta" in t or "is not terracotta" in t:
        return 0
    return 1


def s_b4(reply):
    t = _t(reply)
    return 1 if ("terracotta" in t and "navy" not in t) else 0


def s_b6(reply):
    t = _t(reply)
    i = t.find("kestrel")
    j = t.find("meridian")
    if i < 0 or j < 0:
        return 0
    a = "terracotta" in t[i + 7:i + 7 + 80]
    b = "navy" in t[j + 8:j + 8 + 80]
    return 1 if (a and b) else 0


SCORERS = {
    "B1": s_numbered,
    "B2color": lambda r: s_present(r, "teal"),
    "B2face": lambda r: s_present(r, "east"),
    "B3": s_b3,
    "B4": s_b4,
    "B5": s_numbered,
    "B6": s_b6,
    "B7a": s_numbered,
    "B7b": s_b4,
    "B7c": lambda r: s_present(r, "navy"),
}


def write_csv(path, rows):
    keys = ["condition", "phase", "turn", "session", "prompt_tokens",
            "output_tokens", "total_tokens", "cost_derived_usd",
            "first_visible_ms", "turn_latency_ms", "model", "terminal",
            "error", "approvals", "timeout", "cancelled", "invalid", "ts"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
