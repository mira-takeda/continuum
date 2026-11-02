"""Entry CRUD, supersede, undo. Entries are the ONLY durable truth."""
import json
import os
import re
import uuid
from datetime import datetime, timezone

ALLOWED_TYPES = {"fact", "identity", "history", "correction", "experience"}
ALLOWED_REL = {"part-of", "caused-by", "contradicts", "updates", "example-of", "same-entity", "reminds-of"}
ALLOWED_SRC = {"user-stated", "conversation", "model-inference"}


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def data_paths(root):
    d = os.path.join(root, "data")
    return {"entries": os.path.join(d, "entries"), "index": os.path.join(d, "index.json"),
            "core": os.path.join(d, "core.json"), "log": os.path.join(d, "log.jsonl"),
            "usage": os.path.join(d, "usage.jsonl"), "data": d}


def slugify(title, eid):
    s = re.sub(r"[^a-z0-9]+", "-", (title or "entry").lower()).strip("-")[:30] or "entry"
    return f"{eid}-{s}"


def new_id(now=None):
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("%Y%m%d-%H%M")


def parse_entry(text):
    """Frontmatter is key: value lines where complex values are JSON."""
    parts = text.split("---")
    if len(parts) < 3:
        return {}, text.strip()
    fm, body = parts[1], "---".join(parts[2:]).strip()
    # serialize() prepends a "# Title" display line; strip it on load so it
    # never accumulates in the stored body across save/load cycles.
    body = re.sub(r"^# .*\n?", "", body, count=1).strip()
    meta = {}
    for line in fm.strip().splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        try:
            meta[k] = json.loads(v)
        except Exception:
            meta[k] = v.strip('"')
    return meta, body


def serialize(meta, body):
    order = ["id", "title", "types", "created", "updated", "status", "supersedes",
             "superseded_by", "links", "provenance", "access", "stability", "why"]
    lines = ["---"]
    for k in order:
        if k in meta:
            v = meta[k]
            lines.append(f"{k}: {json.dumps(v)}" if not isinstance(v, str) else f'{k}: {json.dumps(v)}')
    for k in meta:
        if k not in order:
            lines.append(f"{k}: {json.dumps(meta[k])}")
    lines.append("---")
    lines.append(f"# {meta.get('title', '')}")
    lines.append(body or "")
    return "\n".join(lines) + "\n"


def default_meta(eid, title, types, body_turns, source, model, confidence=None):
    now = utcnow()
    if confidence is None:
        confidence = {"user-stated": 0.9, "conversation": 0.6, "model-inference": 0.3}.get(source, 0.5)
    stability = {"user-stated": 0.8, "conversation": 0.6, "model-inference": 0.3}.get(source, 0.5)
    return {"id": eid, "title": title, "types": [t for t in types if t in ALLOWED_TYPES] or ["fact"],
            "created": now, "updated": now, "status": "current", "supersedes": [],
            "superseded_by": None, "links": [],
            "provenance": {"source": source, "turns": body_turns, "model": model, "confidence": confidence},
            "access": {"opens": 0, "last_opened": now, "cue_hits": 0},
            "stability": stability, "why": "auto-extracted durable fact"}


def list_ids(entries_dir):
    if not os.path.isdir(entries_dir):
        return []
    return sorted(f[:-3] for f in os.listdir(entries_dir) if f.endswith(".md"))


EID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def entry_path(entries_dir, eid):
    """Single choke point: validate id, contain resolved path in entries_dir."""
    if not isinstance(eid, str) or not EID_RE.match(eid):
        raise ValueError("invalid entry id")
    if ".." in eid or "/" in eid or "\\" in eid or ":" in eid:
        raise ValueError("invalid entry id")
    base = os.path.realpath(entries_dir)
    p = os.path.realpath(os.path.join(base, eid + ".md"))
    if p != base and not p.startswith(base + os.sep):
        raise ValueError("entry id escapes entries dir")
    return p


def load(entries_dir, eid):
    p = entry_path(entries_dir, eid)
    with open(p, encoding="utf-8") as f:
        return parse_entry(f.read())


def save(entries_dir, eid, meta, body):
    meta["updated"] = utcnow()
    meta["id"] = eid
    p = entry_path(entries_dir, eid)
    with open(p, "w", encoding="utf-8") as f:
        f.write(serialize(meta, body))


def append_log(log_path, op):
    op = dict(op)
    op.setdefault("op_id", uuid.uuid4().hex[:8])
    op.setdefault("ts", utcnow())
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(op) + "\n")
    return op


def create(entries_dir, log_path, title, types, body, turns, source, model, links=None, why=None):
    eid = slugify(title, new_id())
    base, n = eid, 1
    while os.path.exists(os.path.join(entries_dir, eid + ".md")):
        n += 1
        eid = f"{base}-{n}"
    meta = default_meta(eid, title, types, turns, source, model)
    if links:
        meta["links"] = [l for l in links if l.get("relation") in ALLOWED_REL][:6]
    if why:
        meta["why"] = why[:200]
    save(entries_dir, eid, meta, body[:2000])
    return append_log(log_path, {"kind": "create", "entry_id": eid, "before": None,
                                 "after": {"meta": meta, "body": body[:2000]}, "reason": why or "auto-create"})


def update(entries_dir, log_path, eid, body_append=None, links=None, reason="auto-update"):
    meta, body = load(entries_dir, eid)
    before = {"meta": dict(meta), "body": body}
    if body_append:
        body = (body + "\n" + body_append[:1000]).strip()
    if links:
        have = {(l.get("to"), l.get("relation")) for l in meta.get("links", [])}
        for l in links:
            if l.get("relation") in ALLOWED_REL and (l.get("to"), l.get("relation")) not in have:
                meta.setdefault("links", []).append(l)
        meta["links"] = meta["links"][:6]
    save(entries_dir, eid, meta, body)
    return append_log(log_path, {"kind": "update", "entry_id": eid, "before": before,
                                 "after": {"meta": meta, "body": body}, "reason": reason})


def supersede(entries_dir, log_path, old_id, title, types, body, turns, source, model, reason="correction"):
    meta_old, body_old = load(entries_dir, old_id)
    before_old = {"meta": dict(meta_old), "body": body_old}
    # New entry keeps history link.
    op = create(entries_dir, log_path, title, types, body, turns, source, model,
                links=[{"to": old_id, "relation": "updates", "weight": 1.0}],
                why=f"supersedes {old_id}: {reason}"[:200])
    new_id_ = op["entry_id"]
    meta_old["status"] = "superseded"
    meta_old["superseded_by"] = new_id_
    meta_old["stability"] = round(float(meta_old.get("stability", 0.6)) * 0.5, 3)
    save(entries_dir, old_id, meta_old, body_old)
    meta_new, body_new = load(entries_dir, new_id_)
    meta_new["supersedes"] = [old_id]
    save(entries_dir, new_id_, meta_new, body_new)
    append_log(log_path, {"kind": "supersede-mark", "entry_id": old_id, "before": before_old,
                          "after": {"meta": meta_old, "body": body_old}, "reason": reason,
                          "superseded_by": new_id_})
    op["supersedes"] = old_id
    return op


def touch(entries_dir, eid, cue_hit=False):
    meta, body = load(entries_dir, eid)
    acc = meta.setdefault("access", {"opens": 0, "last_opened": utcnow(), "cue_hits": 0})
    acc["opens"] = int(acc.get("opens", 0)) + 1
    acc["last_opened"] = utcnow()
    if cue_hit:
        acc["cue_hits"] = int(acc.get("cue_hits", 0)) + 1
    # Touch rewrites counters only; body untouched.
    p = entry_path(entries_dir, eid)
    with open(p, "w", encoding="utf-8") as f:
        f.write(serialize(meta, body))
    return meta


def confirm(entries_dir, log_path, eid):
    meta, body = load(entries_dir, eid)
    before = {"meta": dict(meta), "body": body}
    meta["stability"] = round(min(1.0, float(meta.get("stability", 0.5)) + 0.15), 3)
    meta["provenance"]["confidence"] = round(min(1.0, float(meta["provenance"].get("confidence", 0.5)) + 0.15), 3)
    save(entries_dir, eid, meta, body)
    return append_log(log_path, {"kind": "confirm", "entry_id": eid, "before": before,
                                 "after": {"meta": meta, "body": body}, "reason": "user confirmation"})


def repeat_observation(entries_dir, eid):
    meta, body = load(entries_dir, eid)
    meta["stability"] = round(min(1.0, float(meta.get("stability", 0.5)) + 0.10), 3)
    p = entry_path(entries_dir, eid)
    with open(p, "w", encoding="utf-8") as f:
        f.write(serialize(meta, body))
    return meta


def unlink(entries_dir, log_path, eid, to_id):
    meta, body = load(entries_dir, eid)
    before = {"meta": dict(meta), "body": body}
    meta["links"] = [l for l in meta.get("links", []) if l.get("to") != to_id]
    save(entries_dir, eid, meta, body)
    return append_log(log_path, {"kind": "unlink", "entry_id": eid, "before": before,
                                 "after": {"meta": meta, "body": body}, "reason": f"unlink {to_id}"})


def undo_last(entries_dir, log_path):
    if not os.path.exists(log_path):
        return None
    with open(log_path, encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    if not lines:
        return None
    # Undo last create/update/supersede/confirm/unlink (skip supersede-mark internals by pairing).
    for line in reversed(lines):
        op = json.loads(line)
        if op.get("undone"):
            continue
        kind = op.get("kind")
        if kind == "create":
            p = entry_path(entries_dir, op["entry_id"])
            if os.path.exists(p):
                os.remove(p)
            op["undone"] = True
            append_log(log_path, {"kind": "undo", "entry_id": op["entry_id"], "reason": f"undo {op.get('op_id')}"})
            return op
        if kind in ("project-append", "project-replace"):
            b = op.get("before") or {}
            if b.get("file") and b.get("text") is not None:
                with open(b["file"], "w", encoding="utf-8") as f:
                    f.write(b["text"])
            op["undone"] = True
            append_log(log_path, {"kind": "undo", "entry_id": op["entry_id"], "reason": f"undo {op.get('op_id')}"})
            return op
        if kind in ("update", "confirm", "unlink", "supersede-mark"):
            b = op.get("before")
            if b:
                p = entry_path(entries_dir, op["entry_id"])
                with open(p, "w", encoding="utf-8") as f:
                    f.write(serialize(b["meta"], b["body"]))
            op["undone"] = True
            append_log(log_path, {"kind": "undo", "entry_id": op["entry_id"], "reason": f"undo {op.get('op_id')}"})
            return op
    return None
