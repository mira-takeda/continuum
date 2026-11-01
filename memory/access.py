"""Accessibility: states computed per query, never stored as folders."""
import math
import re
from datetime import datetime, timezone

STOP = set("the a an and or of to in on for with is are was were be been it its this that you your we our they their".split())


def tokenize(text, limit=40):
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [t for t in toks if t not in STOP][:limit]


def overlap(a_toks, b_toks):
    if not a_toks or not b_toks:
        return 0.0
    sa, sb = set(a_toks), set(b_toks)
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


def days_since(iso):
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except Exception:
        return 30.0


def base_score(meta):
    stability = float(meta.get("stability", 0.5))
    acc = meta.get("access", {}) or {}
    opens = int(acc.get("opens", 0) or 0)
    last = acc.get("last_opened", meta.get("updated", ""))
    score = 12 * stability + 4 * math.log1p(opens) + 6 * math.exp(-days_since(last) / 14.0)
    if meta.get("status") == "superseded":
        score -= 15.0
    return round(score, 2)


def compute_state(score, cue):
    if score >= 60 or cue >= 0.85:
        return "Active"
    if score >= 25:
        return "Recent"
    return "Deep"


DEEP_CUES = ("years ago", "long ago", "ages ago", "remember that", "old project",
             "way back", "deep memory")


def is_deep_recall(query):
    q = (query or "").lower()
    return any(p in q for p in DEEP_CUES)
