"""Scale benchmark shared helpers: approval policy, usage, matcher, scoring."""
import csv
import json
import os
import re

COST_IN_PER_M = 0.10
COST_OUT_PER_M = 0.20

ALLOW_DECISIONS = {"approved", "approve", "allow", "allowed", "allow_once"}
DENY_DECISIONS = {"abort", "aborted", "deny", "denied", "reject", "rejected"}

PATH_RES = [
    re.compile(r"[A-Za-z]:\\[^\s\"']*"),
    re.compile(r"\\\\\?\\[^\s\"']*"),
    re.compile(r"~[\\/][^\s\"']*"),
    re.compile(r"\$env:[^\s\"']*"),
    re.compile(r"(?<![\w:.])([\w.][\w.\-]*[\\/][\w.\\\/\-]+)"),
]


def _inside(child, parent):
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(child)),
                                   os.path.normcase(os.path.abspath(parent))]) == \
            os.path.normcase(os.path.abspath(parent))
    except ValueError:
        return False


def paths_confined(text, root):
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
            tok = os.path.join(home, rest) if rest else home
        if not os.path.isabs(tok):
            tok = os.path.join(root, tok)
        if not _inside(tok, root):
            return False
    return True


def decide_approval(ev, root):
    """Frozen §8 policy. workspaceRoot metadata excluded (context, not a
    target); JSON escapes normalized before path resolution."""
    subj = dict(ev.get("subject") or {})
    subj.pop("workspaceRoot", None)
    subj.pop("workspace_root", None)
    raw = json.dumps({"s": subj, "r": ev.get("rawArgs"),
                      "t": ev.get("toolName")})
    norm = raw.replace("\\\\", "\\")
    choices = ev.get("choices") or []
    confined = paths_confined(norm, root)
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
        "prompt_tokens": prompt, "output_tokens": out,
        "total_tokens": u.get("totalTokens"),
        "cached_tokens": inner.get("cachedTokens"),
        "reasoning_tokens": inner.get("reasoningTokens"),
        "model": u.get("modelId"), "turn_id": u.get("turnId"),
        "window_tokens": (context_ev or {}).get("windowTokens"),
        "cost_derived_usd": derived_cost(prompt, out),
    }


def norm_continuum(logline):
    e = logline or {}
    prompt = e.get("prompt_tokens")
    total = e.get("total_tokens")
    out = total - prompt if (prompt is not None and total is not None) else None
    return {
        "prompt_tokens": prompt, "output_tokens": out, "total_tokens": total,
        "cached_tokens": None, "reasoning_tokens": None,
        "model": e.get("model"), "turn_id": e.get("turn"),
        "window_tokens": None, "server_latency_ms": e.get("latency_ms"),
        "cost_derived_usd": derived_cost(prompt, out),
    }


# ---------------- frozen value matcher (§6) ----------------

def match(value, reply):
    t = (reply or "").lower()
    v = (value or "").lower()
    if not v:
        return False
    if v in t:
        return True
    toks = v.split()
    return len(toks) > 1 and all(tok in t for tok in toks)


def score_rule(rule, reply):
    r = rule["rule"]
    if r == "present":
        return 1 if all(match(w, reply) for w in rule["words"]) else 0
    if r == "corrected":
        new = rule["new"]
        if not match(new, reply):
            return 0
        t = (reply or "").lower()
        nl = new.lower()
        if f"not {nl}" in t or f"n't {nl}" in t:
            return 0
        return 1
    if r == "present_absent":
        return 1 if (all(match(w, reply) for w in rule["present"])
                    and not any(match(w, reply) for w in rule["absent"])) \
            else 0
    if r == "ab":
        t = (reply or "").lower()
        i = t.find(rule["a"].lower())
        j = t.find(rule["b"].lower())
        if i < 0 or j < 0:
            return 0
        win_a = t[i:i + 80]
        win_b = t[j:j + 80]
        # token-relaxed inside window: full value or all tokens
        def win(v, w):
            vl = v.lower()
            if vl in w:
                return True
            toks = vl.split()
            return len(toks) > 1 and all(tok in w for tok in toks)
        return 1 if (win(rule["va"], win_a) and win(rule["vb"], win_b)) else 0
    if r == "clarify":
        t = (reply or "")
        return 1 if ("?" in t and not any(match(w, reply)
                                          for w in rule["absent"])) else 0
    raise ValueError(rule)


def snapshot_light(wsroot):
    md = 0
    agents = os.path.isdir(os.path.join(wsroot, ".agents"))
    for _dp, _dn, fns in os.walk(wsroot):
        md += sum(1 for f in fns if f.endswith(".md"))
    return md, agents


def snapshot_full(wsroot):
    out = []
    for dp, _dn, fns in os.walk(wsroot):
        for f in sorted(fns):
            p = os.path.join(dp, f)
            try:
                out.append(os.path.relpath(p, wsroot).replace("\\", "/")
                           + ":" + str(os.path.getsize(p)))
            except OSError:
                pass
    return sorted(out)


def write_csv(path, rows, keys):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
