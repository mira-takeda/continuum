"""Derived dynamic index. Fully rebuildable from entries; safe to delete."""
import json
import os
from . import access, store

CAP_ENTRIES = 300  # hackathon guardrail only, not an architectural limit.


def gist_of(body, limit=140):
    b = " ".join((body or "").split())
    return b[:limit]


def build(root):
    P = store.data_paths(root)
    os.makedirs(P["entries"], exist_ok=True)
    entries = {}
    for eid in store.list_ids(P["entries"]):
        try:
            meta, body = store.load(P["entries"], eid)
        except Exception:
            continue
        toks = access.tokenize(meta.get("title", "") + " " + body, 30)
        entries[eid] = {"title": meta.get("title", eid), "types": meta.get("types", ["fact"]),
                        "tokens": toks, "neighbors": [l.get("to") for l in meta.get("links", []) if l.get("to")],
                        "access_score": access.base_score(meta), "state": access.compute_state(access.base_score(meta), 0.0),
                        "status": meta.get("status", "current"), "updated": meta.get("updated", ""),
                        "gist": gist_of(body)}
    idx = {"generated_at": store.utcnow(),
           "config": {"cap_entries": CAP_ENTRIES, "cap_note": "hackathon guardrail only; not an architectural limit"},
           "entries": entries}
    with open(P["index"], "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1)
    return idx


def load_or_build(root):
    P = store.data_paths(root)
    if os.path.exists(P["index"]):
        try:
            with open(P["index"], encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return build(root)


def update_one(root, eid):
    idx = load_or_build(root)
    P = store.data_paths(root)
    try:
        meta, body = store.load(P["entries"], eid)
    except Exception:
        idx["entries"].pop(eid, None)
        with open(P["index"], "w", encoding="utf-8") as f:
            json.dump(idx, f, indent=1)
        return idx
    toks = access.tokenize(meta.get("title", "") + " " + body, 30)
    s = access.base_score(meta)
    idx["entries"][eid] = {"title": meta.get("title", eid), "types": meta.get("types", ["fact"]),
                           "tokens": toks, "neighbors": [l.get("to") for l in meta.get("links", []) if l.get("to")],
                           "access_score": s, "state": access.compute_state(s, 0.0),
                           "status": meta.get("status", "current"), "updated": meta.get("updated", ""),
                           "gist": gist_of(body)}
    idx["generated_at"] = store.utcnow()
    with open(P["index"], "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1)
    return idx
