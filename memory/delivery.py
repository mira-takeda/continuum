"""Per-session delivery bookkeeping: full bootstrap once, incremental deltas after.

Root cause addressed: Continuum used to re-inject its full memory/context block
on every turn while MSP permanently retains every prior block in native session
history, so per-turn context grew ~2.4k tokens/turn (551k at turn 250 in the
scale run). This module tracks, per native Muse session id, what Continuum has
already delivered, sends one complete bootstrap to a new session, and afterwards
only new/changed information (entry adds/updates/supersessions, project
switches). It stores IDs/hashes, never prompt copies. It is delivery
bookkeeping only, not a second memory system.

Fail-safe rules (correctness over tokens):
- brand-new sid, run-id mismatch (process/host restart), protocol bump, or an
  explicit compact op -> bootstrap again;
- >30% prompt-token drop on the same sid -> invalidate, bootstrap next turn;
- commit delivery state only after turn/start acceptance (turn-started event);
- unreadable/corrupt state -> treat as fresh; failed commit write -> old state
  stays, so the next turn re-sends (wasteful but correct).
"""
import hashlib
import json
import os
import re
import threading
import uuid

from . import projects as projects_mod
from . import store
from . import turn as turn_mod

DELIVERY_PROTOCOL = 1
COMPACTION_DROP = 0.30
BODY_TRUNC = 1200  # parity with the opened-entry truncation in build_turn_text
QUOTE_TRUNC = 300  # Was:/Now: quote length in supersession lines
MAX_SIDS = 50  # cap delivery.json growth; oldest sessions pruned first

RUN_ID = uuid.uuid4().hex

_LOCK = threading.Lock()


def _path(root):
    return os.path.join(store.data_paths(root)["data"], "delivery.json")


def _load_state(root):
    try:
        with open(_path(root), encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict) and isinstance(st.get("sids"), dict):
            return st
    except (OSError, ValueError):
        pass
    return {"run_id": RUN_ID, "protocol": DELIVERY_PROTOCOL, "sids": {}}


def _save_state(root, st):
    try:
        os.makedirs(os.path.dirname(_path(root)), exist_ok=True)
        with open(_path(root), "w", encoding="utf-8") as f:
            json.dump(st, f, indent=1)
        return True
    except OSError:
        return False


def content_hash(status, title, body):
    h = hashlib.sha256()
    h.update((str(status or "") + "\n" + str(title or "") + "\n" + str(body or ""))
             .encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def _entry_snapshot(entries_dir, eid):
    try:
        meta, body = store.load(entries_dir, eid)
    except Exception:
        return None
    return {"status": meta.get("status", "current"),
            "title": meta.get("title", eid),
            "types": meta.get("types", []),
            "body": body or "",
            "supersedes": list(meta.get("supersedes") or [])}


def _index_top_ids(idx, limit=30):
    # Same ordering as turn.index_gists_compact (which has no status filter).
    rows = sorted((idx.get("entries", {}) or {}).items(),
                  key=lambda kv: kv[1].get("access_score", 0), reverse=True)[:limit]
    return [eid for eid, _ in rows]


def _core_ids(root):
    try:
        with open(store.data_paths(root)["core"], encoding="utf-8") as f:
            core = json.load(f)
        ids = core.get("entry_ids") or []
        return [i for i in ids if isinstance(i, str)]
    except (OSError, ValueError):
        return []


def _type_list(types):
    return ",".join(types) if types else ""


def mentioned_projects(root, message, active_name):
    """Inactive registered projects explicitly named in the message (sorted)."""
    try:
        reg = projects_mod.list_projects(root) or {}
    except Exception:
        return []
    found = []
    for name in reg:
        if not name or name == active_name or len(name) < 2:
            continue
        try:
            if re.search(r"\b" + re.escape(name) + r"\b", message or "",
                         re.IGNORECASE):
                found.append(name)
        except re.error:
            continue
    return sorted(found)


def _project_section_for(root, bid, name):
    """Minimal current section for a non-active registered project (A+B case).

    Deliberately smaller than assemble(): authoring protocol and workspace map
    were already bootstrapped, so only current project state is included.
    """
    try:
        reg = projects_mod.list_projects(root) or {}
        if name not in reg:
            return ""
        text = projects_mod.read_cached(root, bid, projects_mod.project_file(root, name))
        cap = getattr(projects_mod, "ASSEMBLE_CAP", 8000)
        if len(text) > cap:
            text = text[:cap] + "\n[... truncated]"
        return "\n".join(["<project-context>", "Project: %s (also requested)" % name,
                          "Root: %s" % reg[name], "---", text.rstrip(),
                          "</project-context>"])
    except (OSError, ValueError):
        return ""


def plan_turn(root, sid, bid, message, ret, idx, proj_section, ops_help):
    """Decide bootstrap vs delta for this turn. Pure read (no state writes).

    Returns {"mode", "full", "commit", "stats"}. "full" is the complete model
    input before per-turn ephemeral appends (project pre-note, attachments).
    commit=None means nothing new to record. Call commit() only after the
    turn/start acceptance for the intended sid.
    """
    with _LOCK:
        st = _load_state(root)
        if (st.get("run_id") != RUN_ID
                or st.get("protocol") != DELIVERY_PROTOCOL):
            st = {"run_id": RUN_ID, "protocol": DELIVERY_PROTOCOL, "sids": {}}
            return _bootstrap(root, st, sid, bid, message, ret, idx,
                              proj_section, ops_help)
        s = st["sids"].get(sid)
        if not s or not s.get("bootstrapped"):
            return _bootstrap(root, st, sid, bid, message, ret, idx,
                              proj_section, ops_help)
        return _delta(root, st, sid, bid, message, ret, idx, proj_section)


def _snapshot_for_commit(root, idx, ret, proj_section, active_name):
    entries_dir = store.data_paths(root)["entries"]
    seen = set(_index_top_ids(idx)) | set(_core_ids(root))
    bodies = {}
    for e in ret.get("opened") or []:
        eid = e.get("id")
        snap = _entry_snapshot(entries_dir, eid) if eid else None
        if snap is None:
            continue
        seen.add(eid)
        bodies[eid] = content_hash(snap["status"], snap["title"], snap["body"])
    projects = {}
    if active_name and proj_section:
        projects[active_name] = hashlib.sha256(
            proj_section.encode("utf-8", "replace")).hexdigest()[:16]
    return seen, bodies, projects


def _bootstrap(root, st, sid, bid, message, ret, idx, proj_section, ops_help):
    full = turn_mod.build_turn_text(root, message, ret, idx, proj_section,
                                    leak_guard=True)
    if ops_help:
        full = ("<continuum-system>\n" + ops_help + "\n</continuum-system>\n\n"
                + full)
    try:
        active = projects_mod.active_for(root, bid)
    except Exception:
        active = None
    seen, bodies, projects = _snapshot_for_commit(root, idx, ret, proj_section,
                                                  active)
    try:
        reg = projects_mod.list_projects(root) or {}
        mentioned = [n for n in mentioned_projects(root, message, active)
                     if n in reg]
    except Exception:
        mentioned = []
    extra_sections = []
    for name in mentioned:
        sec = _project_section_for(root, bid, name)
        if sec:
            extra_sections.append(sec)
            projects[name] = hashlib.sha256(
                sec.encode("utf-8", "replace")).hexdigest()[:16]
    if extra_sections:
        full += "\n\n" + "\n\n".join(extra_sections)
    commit = {"seen": sorted(seen), "bodies": bodies, "projects": projects,
              "last_project": active,
              "protocol": DELIVERY_PROTOCOL, "bootstrapped": True}
    stats = {"mode": "bootstrap", "added_chars": len(full),
             "new_entries": len(bodies), "extra_projects": mentioned}
    return {"mode": "bootstrap", "full": full, "commit": commit, "stats": stats}


def _delta(root, st, sid, bid, message, ret, idx, proj_section):
    s = st["sids"][sid]
    entries_dir = store.data_paths(root)["entries"]
    seen = set(s.get("seen") or [])
    bodies = dict(s.get("bodies") or {})
    projs = dict(s.get("projects") or {})
    try:
        active = projects_mod.active_for(root, bid)
    except Exception:
        active = None
    cur_ids = set(_index_top_ids(idx)) | set(_core_ids(root))
    opened_ids = {e.get("id") for e in (ret.get("opened") or []) if e.get("id")}
    cur_ids |= opened_ids
    lines_add, lines_upd, lines_sup, lines_wd = [], [], [], []
    new_seen, new_bodies = set(), {}
    repl_for_old = {}
    for eid in sorted(cur_ids | seen | set(bodies)):
        snap = _entry_snapshot(entries_dir, eid)
        if snap is None:
            if eid in seen or eid in bodies:
                lines_wd.append('WITHDRAWN [%s] (entry deleted).' % eid)
            continue
        h = content_hash(snap["status"], snap["title"], snap["body"])
        if eid not in cur_ids:
            if bodies.get(eid) == h:
                continue  # body-delivered and unchanged
            if eid in seen and eid not in bodies and snap["status"] != "superseded":
                continue  # gist-known, unchanged, body never needed
        if eid not in bodies and (eid not in seen or eid in opened_ids):
            # First full delivery: brand-new id, or gist-known id now opened.
            # If it supersedes known olds, the SUPERSEDED line carries it.
            linked_sup = False
            for old in snap["supersedes"]:
                if old in seen or old in bodies:
                    repl_for_old[old] = eid
                    osnap = _entry_snapshot(entries_dir, old)
                    if osnap is not None and osnap["status"] == "superseded":
                        linked_sup = True
            if linked_sup:
                new_seen.add(eid)
                continue
            body = snap["body"][:BODY_TRUNC]
            lines_add.append("ADD [%s] %s (%s, %s):\n%s"
                             % (eid, snap["title"],
                                _type_list(snap["types"]), snap["status"], body))
            new_seen.add(eid)
            new_bodies[eid] = h
        elif eid in bodies and bodies[eid] != h and snap["status"] == "current":
            body = snap["body"][:BODY_TRUNC]
            lines_upd.append("UPDATE [%s] %s:\n%s" % (eid, snap["title"], body))
            new_seen.add(eid)
            new_bodies[eid] = h
        # Status flips to superseded are handled by the flipped scan below.
    # Supersessions: delivered entries that flipped to superseded, plus any
    # old id referenced by a new entry's supersedes links.
    sup_noted = dict(s.get("sup_noted") or {})
    flipped = []
    for eid in sorted(seen | set(bodies)):
        snap = _entry_snapshot(entries_dir, eid)
        if snap is None or snap["status"] != "superseded":
            continue
        if bodies.get(eid) == content_hash(snap["status"], snap["title"], snap["body"]):
            continue  # already delivered with superseded status known
        new_id = repl_for_old.get(eid)
        if new_id is None:
            try:
                meta, _ = store.load(entries_dir, eid)
                nb = meta.get("superseded_by")
                new_id = nb if isinstance(nb, str) else None
            except Exception:
                new_id = None
        if sup_noted.get(eid) == (new_id or ""):
            continue
        flipped.append((eid, snap, new_id))
    for eid, snap, new_id in flipped:
        if new_id:
            nsnap = _entry_snapshot(entries_dir, new_id)
            ntitle = nsnap["title"] if nsnap else new_id
            now = (nsnap["body"] if nsnap else "")[:QUOTE_TRUNC]
        else:
            ntitle, now = "(pending)", ""
        was = snap["body"][:QUOTE_TRUNC]
        if new_id:
            lines_sup.append(
                'SUPERSEDED [%s] "%s" by [%s] "%s".\nWas: "%s"\nNow: "%s".'
                % (eid, snap["title"], new_id, ntitle, was, now))
        else:
            lines_sup.append('SUPERSEDED [%s] "%s" (no longer current).' % (eid, snap["title"]))
        sup_noted[eid] = new_id or ""
        if new_id and nsnap and len(nsnap["body"]) <= QUOTE_TRUNC:
            new_bodies[new_id] = content_hash(nsnap["status"], nsnap["title"], nsnap["body"])
        if new_id:
            new_seen.add(new_id)
    # Relevance pointer: opened entries whose full bodies were already delivered.
    ptr = []
    for e in ret.get("opened") or []:
        eid = e.get("id")
        if not eid or eid in new_bodies:
            continue
        snap = _entry_snapshot(entries_dir, eid)
        if snap is None:
            continue
        if bodies.get(eid) == content_hash(snap["status"], snap["title"], snap["body"]):
            ptr.append("[%s] %s" % (eid, snap["title"]))
    # Project handling.
    proj_lines, proj_sections = [], []
    if active:
        h = hashlib.sha256((proj_section or "").encode("utf-8", "replace")).hexdigest()[:16]
        if projs.get(active) != h:
            if proj_section:
                proj_sections.append(proj_section)
            projs[active] = h
        elif s.get("last_project") != active and active in projs:
            proj_lines.append('PROJECT: "%s" is active again; its previously '
                              "provided context is unchanged and still applies." % active)
    try:
        reg = projects_mod.list_projects(root) or {}
        mentioned = [n for n in mentioned_projects(root, message, active) if n in reg]
    except Exception:
        mentioned = []
    for name in mentioned:
        sec = _project_section_for(root, bid, name)
        if not sec:
            continue
        h = hashlib.sha256(sec.encode("utf-8", "replace")).hexdigest()[:16]
        if projs.get(name) != h:
            proj_sections.append(sec)
            projs[name] = h
    commit = {"seen_add": sorted(new_seen), "bodies": new_bodies,
              "projects": projs, "last_project": active,
              "sup_noted": sup_noted}
    parts = []
    parts.extend(lines_add)
    parts.extend(lines_upd)
    parts.extend(lines_sup)
    if lines_sup:
        parts.append("Supersessions above apply only to the named entries.")
    parts.extend(lines_wd)
    if ptr:
        parts.append("RELEVANT (already provided for this turn): %s." % "; ".join(sorted(ptr)))
    parts.extend(proj_lines)
    body = "\n".join(parts)
    if proj_sections:
        body = (body + "\n\n" + "\n\n".join(proj_sections)) if body else "\n\n".join(proj_sections)
    if not body.strip():
        full = "USER MESSAGE:\n" + message
        stats = {"mode": "delta-empty", "added_chars": 0, "new_entries": 0,
                 "superseded": 0}
    else:
        full = ("<continuum-delta>\nPreviously provided memory still applies. "
                "Only changes are listed below.\n" + body + "\n</continuum-delta>\n\n"
                "USER MESSAGE:\n" + message)
        stats = {"mode": "delta", "added_chars": len(full) - len(message) - len("USER MESSAGE:\n"),
                 "new_entries": len(lines_add) + len(lines_upd),
                 "superseded": len(lines_sup)}
    return {"mode": stats["mode"], "full": full, "commit": commit, "stats": stats}


def commit(root, sid, commit_data):
    """Record a delivery AFTER turn/start acceptance. Merges, never resets."""
    if not commit_data:
        return False
    with _LOCK:
        st = _load_state(root)
        if st.get("run_id") != RUN_ID:
            st = {"run_id": RUN_ID, "protocol": DELIVERY_PROTOCOL, "sids": {}}
        s = st["sids"].get(sid) or {}
        if "seen" in commit_data:
            s["seen"] = sorted(set(commit_data["seen"]))
            s["bodies"] = dict(commit_data.get("bodies") or {})
            s["projects"] = dict(commit_data.get("projects") or {})
            s["last_project"] = commit_data.get("last_project")
            s["protocol"] = DELIVERY_PROTOCOL
            s["bootstrapped"] = True
            s.pop("sup_noted", None)
        else:
            s["seen"] = sorted(set(s.get("seen") or []) | set(commit_data.get("seen_add") or []))
            bodies = dict(s.get("bodies") or {})
            bodies.update(commit_data.get("bodies") or {})
            s["bodies"] = bodies
            s["projects"] = dict(commit_data.get("projects") or s.get("projects") or {})
            s["last_project"] = commit_data.get("last_project", s.get("last_project"))
            sup = dict(s.get("sup_noted") or {})
            sup.update(commit_data.get("sup_noted") or {})
            s["sup_noted"] = sup
        import time as _t
        s["last_seen"] = _t.time()
        st["sids"][sid] = s
        while len(st["sids"]) > MAX_SIDS:
            oldest = min(st["sids"], key=lambda k: st["sids"][k].get("last_seen", 0))
            if oldest == sid:
                break
            st["sids"].pop(oldest, None)
        return _save_state(root, st)


def note_usage(root, sid, prompt_tokens):
    """Post-turn compaction guard. Returns 'ok' | 'compacted' | 'missing'.

    A >30% prompt-token drop on the same sid invalidates delivery state so the
    next turn bootstraps again. Missing usage preserves state AND baseline, so
    the next observed reading is still compared against the pre-gap baseline.
    """
    if not isinstance(prompt_tokens, int):
        return "missing"
    with _LOCK:
        st = _load_state(root)
        s = st["sids"].get(sid)
        if not s:
            return "missing"
        prev = s.get("last_prompt")
        s["last_prompt"] = prompt_tokens
        import time as _t
        s["last_seen"] = _t.time()
        if isinstance(prev, int) and prev > 0 and prompt_tokens < prev * (1 - COMPACTION_DROP):
            st["sids"].pop(sid, None)
            _save_state(root, st)
            return "compacted"
        st["sids"][sid] = s
        _save_state(root, st)
        return "ok"


def invalidate(root, sid):
    """Drop delivery state for one sid (explicit compact op)."""
    with _LOCK:
        st = _load_state(root)
        if sid in st.get("sids", {}):
            st["sids"].pop(sid, None)
            _save_state(root, st)
            return True
        return False


def delivery_path(root):
    return _path(root)
