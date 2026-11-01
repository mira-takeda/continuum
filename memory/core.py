"""Auto-derived CORE context. Never hand-edited; regenerated on every write."""
import json
import os
from . import store

CORE_TOKEN_BUDGET = 400
CORE_RULE = "status=current AND stability>=0.60, order by (stability, opens) desc, fill to ~400 tokens"


def est_tokens(text):
    return max(1, len(text or "") // 4)


def rebuild(root):
    P = store.data_paths(root)
    cands = []
    for eid in store.list_ids(P["entries"]):
        try:
            meta, body = store.load(P["entries"], eid)
        except Exception:
            continue
        if meta.get("status") != "current":
            continue
        st = float(meta.get("stability", 0.0))
        if st < 0.60:
            continue
        opens = int((meta.get("access") or {}).get("opens", 0) or 0)
        cands.append((st, opens, eid, meta, body))
    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    chosen, reasons, used = [], [], 0
    for st, opens, eid, meta, body in cands:
        line = f"- [{eid}] {meta.get('title')}: {' '.join(body.split())[:160]}"
        t = est_tokens(line)
        if used + t > CORE_TOKEN_BUDGET and chosen:
            break
        chosen.append(eid)
        used += t
        reasons.append({"id": eid, "stability": st, "opens": opens, "tokens": t})
    core = {"generated_at": store.utcnow(), "rule": CORE_RULE, "entry_ids": chosen,
            "reasons": reasons, "tokens": used}
    with open(P["core"], "w", encoding="utf-8") as f:
        json.dump(core, f, indent=1)
    return core


def load_or_build(root):
    P = store.data_paths(root)
    if os.path.exists(P["core"]):
        try:
            with open(P["core"], encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return rebuild(root)


def render(root):
    """Return CORE text block for system prompt."""
    P = store.data_paths(root)
    core = load_or_build(root)
    if not core.get("entry_ids"):
        return "(CORE empty — no stable entries yet.)"
    lines = []
    for eid in core["entry_ids"]:
        try:
            meta, body = store.load(P["entries"], eid)
            lines.append(f"- [{eid}] {meta.get('title')}: {' '.join(body.split())[:200]}")
        except Exception:
            continue
    return "\n".join(lines) if lines else "(CORE empty.)"
