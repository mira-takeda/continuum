"""Scalable retrieval: cheap local shortlist -> single Muse rerank -> open top-k."""
from . import access, index as index_mod, store

SHORTLIST = 15
TOP_K = 3
TOP_K_DEEP = 8


def cheap_candidates(idx_entries, query, metas_base):
    qt = access.tokenize(query)
    scored = []
    for eid, info in idx_entries.items():
        cue = access.overlap(qt, info.get("tokens", []))
        base = float(info.get("access_score", 0.0))
        if info.get("status") == "superseded":
            base -= 5.0  # extra rank penalty at query time; entry stays searchable
        scored.append((100 * cue + base, cue, eid))
    scored.sort(reverse=True)
    # Link spread: expand from top seeds 1-2 hops.
    seeds = [e for _, _, e in scored[:6]]
    link_bonus = {}
    for s in seeds:
        sq = next((c for _, c, e in scored if e == s), 0.0)
        for hop1 in idx_entries.get(s, {}).get("neighbors", [])[:6]:
            link_bonus[hop1] = max(link_bonus.get(hop1, 0), sq * 0.5)
            for hop2 in idx_entries.get(hop1, {}).get("neighbors", [])[:4]:
                link_bonus[hop2] = max(link_bonus.get(hop2, 0), sq * 0.25)
    merged = []
    for score, cue, eid in scored:
        lb = link_bonus.get(eid, 0.0)
        merged.append((score + 30 * lb, cue, lb, eid))
    merged.sort(reverse=True)
    return merged[:SHORTLIST]


def retrieve(root, muse, query, system="S2"):
    """Returns {opened:[{id,title,body,types,score,state,path}], shortlist, deep}.

    muse=None selects the live path: cheap local ranking only, no second LLM.
    (Muse Code itself is the only model in the live path.)
    """
    idx = index_mod.load_or_build(root)
    P = store.data_paths(root)
    deep = access.is_deep_recall(query)
    cands = cheap_candidates(idx.get("entries", {}), query, None)
    if not cands:
        return {"opened": [], "shortlist": [], "deep": deep, "paths": []}
    short = [{"id": eid, "title": idx["entries"][eid].get("title", eid),
              "gist": idx["entries"][eid].get("gist", "")} for _, _, _, eid in cands]
    if muse is None:
        qt = access.tokenize(query)
        rel = {s["id"]: access.overlap(qt, access.tokenize(
            s.get("title", "") + " " + s.get("gist", ""))) for s in short}
    else:
        # Single batched rerank — does NOT scale with total memory size.
        try:
            rel = muse.rerank(query, short, purpose="rerank", system=system)
        except Exception:
            rel = {s["id"]: 0.0 for s in short}
    final = []
    for score, cue, lb, eid in cands:
        info = idx["entries"][eid]
        r = float(rel.get(eid, 0.0))
        base = float(info.get("access_score", 0.0))
        fscore = 100 * r + 30 * lb + base
        state = access.compute_state(fscore, max(cue, r))
        final.append((fscore, r, cue, lb, eid, state))
    final.sort(reverse=True)
    k = TOP_K_DEEP if deep else TOP_K
    # Open top-k with positive signal; always open at least the best if rerank > 0.15.
    opened = []
    paths = []
    for fscore, r, cue, lb, eid, state in final[:k]:
        if r < 0.12 and cue < 0.2 and not (deep and fscore >= 25):
            continue
        try:
            meta, body = store.load(P["entries"], eid)
        except Exception:
            continue
        store.touch(P["entries"], eid, cue_hit=(max(r, cue) >= 0.5))
        index_mod.update_one(root, eid)
        path = f"index:{cue:.2f}→rerank:{r:.2f}" + (f"→link:{lb:.2f}" if lb > 0.01 else "") + f"→{state}"
        opened.append({"id": eid, "title": meta.get("title", eid), "body": body,
                       "types": meta.get("types", []), "score": round(fscore, 1),
                       "state": state, "path": path, "status": meta.get("status", "current")})
        paths.append({"id": eid, "path": path, "score": round(fscore, 1)})
    return {"opened": opened, "shortlist": [s["id"] for s in short], "deep": deep, "paths": paths}
