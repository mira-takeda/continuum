"""Automatic write proposals with server-side validation and dedupe.

Two proposal sources share one validator/applier:
- eval/baseline path: an LLM proposes ops (muse_client).
- live path: Muse Code itself appends a ```memory-ops fenced block; the server
  only parses (deterministic, no second LLM) and validates it here.
"""
import json
import re
from . import access, index as index_mod, projects as projects_mod, store

MAX_OPS = 3
MAX_CREATE = 2


def index_gists_text(idx, limit=30):
    lines = []
    for eid, info in list(idx.get("entries", {}).items())[:limit]:
        lines.append(f"- [{eid}] {info.get('title')}: {info.get('gist','')[:120]}")
    return "\n".join(lines) or "(index empty)"


def dedupe_match(idx, title, body):
    """Return existing id if title/body overlaps strongly, else None."""
    cand = access.tokenize(title + " " + body)
    best, bid = 0.0, None
    for eid, info in idx.get("entries", {}).items():
        o = access.overlap(cand, info.get("tokens", []))
        if o > best:
            best, bid = o, eid
    return bid if best >= 0.70 else None


MEMORY_BLOCK_RE = re.compile(r"```memory-ops\s*\n(.*?)```", re.S)
MEMORY_FENCE_OPEN = "```memory-ops"


def parse_memory_block(text):
    """Deterministic parse of Muse Code's ```memory-ops block. No LLM."""
    if not text:
        return []
    try:
        m = MEMORY_BLOCK_RE.search(text)
    except Exception:
        return []
    if not m:
        return []
    try:
        ops = json.loads(m.group(1).strip())
        return ops if isinstance(ops, list) else []
    except Exception:
        return []


def propose_and_apply(root, muse, turn_text, session_turn, system="S2"):
    idx = index_mod.load_or_build(root)
    gists = index_gists_text(idx)
    try:
        raw_ops = muse.extract_ops(turn_text, gists, [], purpose="extract", system=system) or []
    except Exception:
        raw_ops = []
    return apply_ops(root, raw_ops, session_turn, muse.model)


def split_ops_block(text):
    """Split (clean_text, first_block_or_None) for ```memory-ops blocks.

    Same MEMORY_BLOCK_RE as parse_memory_block (single source of truth): the
    server withholds fenced blocks from streamed chat text and re-emits the
    first as a metadata frame. Duplicate echoes are display noise, not ops
    (parsing keeps first-block semantics), so every further complete block
    is also withheld. Unclosed fence -> (text, None): content wins.
    """
    if not text:
        return "", None
    m = MEMORY_BLOCK_RE.search(text)
    if not m:
        return text, None
    clean = text[:m.start()].rstrip() + text[m.end():]
    block = text[m.start():m.end()]
    try:
        rest = MEMORY_BLOCK_RE.sub("", clean)
    except Exception:
        return clean, block
    if rest != clean:
        return rest.rstrip(), block
    return clean, block


def apply_ops_from_block(root, full_text, session_turn, model_name, active_project=None):
    """Live path: parse Muse Code's own block, validate, apply."""
    return apply_ops(root, parse_memory_block(full_text), session_turn, model_name,
                     active_project=active_project)


def apply_project_op(root, op, kind, scope, active_project=None):
    """Route one project/workspace-scoped op to .continuum.md files."""
    if scope == "workspace":
        return {"kind": "rejected", "scope": scope,
                "reason": "workspace map is user-maintained (register projects explicitly)"}
    name = scope.split(":", 1)[1]
    if active_project is not None and name != active_project:
        return {"kind": "rejected", "scope": scope,
                "reason": f"cross-project write blocked (active: {active_project or 'none'})"}
    if name not in projects_mod.list_projects(root):
        return {"kind": "rejected", "scope": scope, "reason": f"unknown project: {name}"}
    section = (op.get("section") or "").strip()
    body = (op.get("body") or "")[:1500]
    eid = f"project:{name}:{section}"
    if kind == "create":
        if not body.strip():
            return {"kind": "rejected", "scope": scope, "reason": "empty body"}
        result, info = projects_mod.append_line(root, name, section, body)
        if result == "duplicate":
            return {"kind": "project-duplicate", "entry_id": eid}
        if result == "rejected":
            return {"kind": "rejected", "scope": scope, "reason": info}
        return {"kind": "project-append", "entry_id": eid}
    if kind == "update":
        old = (op.get("replace") or "")[:300]
        if not body.strip() or not old.strip():
            return {"kind": "rejected", "scope": scope, "reason": "update needs replace+body"}
        result, info = projects_mod.replace_line(root, name, section, old, body)
        if result == "rejected":
            return {"kind": "rejected", "scope": scope, "reason": info}
        return {"kind": "project-replace", "entry_id": eid}
    return {"kind": "rejected", "scope": scope, "reason": f"unsupported project kind: {kind}"}


def apply_ops(root, raw_ops, session_turn, model_name, active_project=None):
    idx = index_mod.load_or_build(root)
    P = store.data_paths(root)
    applied = []
    creates = 0
    for op in raw_ops[:MAX_OPS]:
        kind = (op.get("kind") or "").lower()
        if kind in ("noop", ""):
            continue
        scope = (op.get("scope") or "").strip()
        if scope == "workspace" or scope.startswith("project:"):
            # Project layer: separate files, separate budget (still under MAX_OPS).
            applied.append(apply_project_op(root, op, kind, scope, active_project))
            continue
        if kind == "create":
            if creates >= MAX_CREATE:
                continue
            title = (op.get("title") or "")[:80] or "Untitled"
            types = [t for t in (op.get("types") or ["fact"]) if t in store.ALLOWED_TYPES] or ["fact"]
            body = (op.get("body") or "")[:1500]
            if not body.strip():
                continue
            src = op.get("source") or "conversation"
            if src not in store.ALLOWED_SRC:
                src = "conversation"
            dup = dedupe_match(idx, title, body)
            if dup:
                # Deterministic rule: repeat observation bumps stability, appends evidence.
                store.repeat_observation(P["entries"], dup)
                r = store.update(P["entries"], P["log"], dup, body_append=f"\nRe-observed {session_turn}: {body[:200]}",
                                 reason=f"repeat-observation {session_turn}")
                index_mod.update_one(root, dup)
                applied.append({"kind": "repeat", "entry_id": dup, "op_id": r.get("op_id")})
                continue
            links = [l for l in (op.get("links") or []) if l.get("relation") in store.ALLOWED_REL][:6]
            r = store.create(P["entries"], P["log"], title, types, body, [session_turn], src,
                             model_name, links=links, why=f"auto {session_turn}")
            index_mod.update_one(root, r["entry_id"])
            creates += 1
            applied.append({"kind": "create", "entry_id": r["entry_id"], "op_id": r.get("op_id"), "title": title})
        elif kind == "update":
            eid = op.get("id")
            if not eid or eid not in idx.get("entries", {}):
                continue
            r = store.update(P["entries"], P["log"], eid, body_append=(op.get("body_append") or "")[:1000],
                             links=[l for l in (op.get("links") or []) if l.get("relation") in store.ALLOWED_REL],
                             reason=f"auto-update {session_turn}")
            index_mod.update_one(root, eid)
            applied.append({"kind": "update", "entry_id": eid, "op_id": r.get("op_id")})
        elif kind == "supersede":
            old = op.get("old_id")
            if not old or old not in idx.get("entries", {}):
                continue
            title = (op.get("title") or f"Updated {old}")[:80]
            types = [t for t in (op.get("types") or ["fact"]) if t in store.ALLOWED_TYPES] or ["fact"]
            # Correction from user speech is user-stated; otherwise conversation.
            src = "user-stated" if "you" in (op.get("body") or "").lower() or True else "conversation"
            sbody = (op.get("body") or "")[:1500]
            if not sbody.strip():
                continue
            r = store.supersede(P["entries"], P["log"], old, title, types,
                                sbody, [session_turn], src, model_name,
                                reason=f"supersede {session_turn}")
            index_mod.update_one(root, old)
            index_mod.update_one(root, r["entry_id"])
            applied.append({"kind": "supersede", "entry_id": r["entry_id"], "op_id": r.get("op_id"),
                            "supersedes": old, "title": title})
        elif kind == "confirm":
            eid = op.get("id")
            if not eid or eid not in idx.get("entries", {}):
                continue
            r = store.confirm(P["entries"], P["log"], eid)
            index_mod.update_one(root, eid)
            applied.append({"kind": "confirm", "entry_id": eid, "op_id": r.get("op_id")})
        elif kind == "link":
            f, t = op.get("from"), op.get("to")
            rel = op.get("relation")
            if f in idx.get("entries", {}) and t in idx.get("entries", {}) and rel in store.ALLOWED_REL:
                r = store.update(P["entries"], P["log"], f,
                                 links=[{"to": t, "relation": rel, "weight": float(op.get("weight", 0.5))}],
                                 reason=f"auto-link {session_turn}")
                index_mod.update_one(root, f)
                applied.append({"kind": "link", "entry_id": f, "op_id": r.get("op_id"), "to": t})
    return applied
