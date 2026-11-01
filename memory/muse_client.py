"""Central Muse client. ALL model reasoning goes through here.

Real path: Muse Spark Contributor via Meta Model API (OpenAI-compatible).
Mock path: deterministic local fallback when no API key is set, for offline
vertical-slice tests. Mock calls are always logged with mocked=true.
"""
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def est_tokens(text):
    return max(1, len(text or "") // 4)


class MuseClient:
    def __init__(self, data_dir, model=None, base_url=None, api_key=None):
        self.data_dir = data_dir
        self.model = model or os.environ.get("MUSE_MODEL", "muse-spark-contributor")
        self.base_url = (base_url or os.environ.get("META_MODEL_BASE_URL",
                         "https://api.meta.com/model/v1")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("META_MODEL_API_KEY", "")
        self.mocked = (not self.api_key) or os.environ.get("MOCK_MUSE", "") == "1"
        self.usage_path = os.path.join(data_dir, "usage.jsonl")
        os.makedirs(data_dir, exist_ok=True)

    def log(self, purpose, system, in_tok, out_tok, latency_ms):
        rec = {"ts": utcnow(), "model": self.model, "purpose": purpose,
               "system": system, "input_tokens": in_tok, "output_tokens": out_tok,
               "latency_ms": latency_ms, "mocked": self.mocked,
               "base_url": self.base_url}
        with open(self.usage_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def _post(self, messages, max_tokens=800, temperature=0.2):
        body = json.dumps({"model": self.model, "messages": messages,
                           "max_tokens": max_tokens, "temperature": temperature}).encode()
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + self.api_key})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = json.loads(r.read().decode())
        ms = int((time.time() - t0) * 1000)
        text = raw["choices"][0]["message"]["content"]
        u = raw.get("usage", {})
        return text, u.get("prompt_tokens", sum(est_tokens(m.get("content", "")) for m in messages)), \
            u.get("completion_tokens", est_tokens(text)), ms

    def chat(self, messages, purpose="chat", system="S2", max_tokens=800, temperature=0.3):
        if self.mocked:
            t0 = time.time()
            text = self._mock_chat(messages)
            ms = int((time.time() - t0) * 1000)
            it = sum(est_tokens(m.get("content", "")) for m in messages)
            self.log(purpose, system, it, est_tokens(text), ms)
            return text
        text, it, ot, ms = self._post(messages, max_tokens, temperature)
        self.log(purpose, system, it, ot, ms)
        return text

    def rerank(self, query, candidates, purpose="rerank", system="S2"):
        """Single batched call. candidates: [{id,title,gist}]. Returns {id: 0..1}."""
        if self.mocked or not candidates:
            return {c["id"]: _overlap(query, c.get("title", "") + " " + c.get("gist", ""))
                    for c in candidates}
        msgs = [{"role": "system", "content": (
            "Score each candidate 0.0-1.0 for relevance to the query. "
            "Reply ONLY as JSON object {id: score}.")},
            {"role": "user", "content": "Query: " + query + "\nCandidates:\n" +
             "\n".join(f'- {c["id"]}: {c.get("title","")} — {c.get("gist","")}' for c in candidates)}]
        t0 = time.time()
        try:
            text, it, ot, ms = self._post(msgs, max_tokens=600, temperature=0.0)
        except Exception:
            return {c["id"]: _overlap(query, c.get("title", "") + " " + c.get("gist", ""))
                    for c in candidates}
        self.log(purpose, system, it, ot, ms)
        try:
            m = re.search(r"\{.*\}", text, re.S)
            scores = json.loads(m.group(0)) if m else {}
            return {c["id"]: float(scores.get(c["id"], 0.0)) for c in candidates}
        except Exception:
            return {c["id"]: _overlap(query, c.get("title", "") + " " + c.get("gist", ""))
                    for c in candidates}

    def extract_ops(self, turn_text, index_gists, retrieved_ids, purpose="extract", system="S2"):
        """Return list of validated-shape op dicts (may be empty)."""
        if self.mocked:
            return _mock_extract(turn_text, index_gists)
        msgs = [{"role": "system", "content": (
            "You maintain a personal encyclopedia. From this turn propose 0-3 memory ops as JSON list. "
            "Op kinds: create{title,types,body,links,source}, update{id,body_append,links}, "
            "supersede{old_id,title,types,body}, confirm{id}, link{from,to,relation,weight}, noop. "
            "Write ONLY durable facts/decisions/corrections/preferences/repeat behavior. "
            "Never write chit-chat. types subset of [fact,identity,history,correction,experience]. "
            "relations subset of [part-of,caused-by,contradicts,updates,example-of,same-entity,reminds-of]. "
            "Reply ONLY JSON list.")},
            {"role": "user", "content": f"Turn:\n{turn_text}\n\nIndex gists:\n{index_gists}\n\nRetrieved: {retrieved_ids}"}]
        try:
            text, it, ot, ms = self._post(msgs, max_tokens=700, temperature=0.1)
        except Exception:
            return []
        self.log(purpose, system, it, ot, ms)
        try:
            m = re.search(r"\[.*\]", text, re.S)
            ops = json.loads(m.group(0)) if m else []
            return ops if isinstance(ops, list) else []
        except Exception:
            return []

    def _mock_chat(self, messages):
        user = ""
        ctx_parts = []
        for m in messages:
            if m.get("role") == "user":
                user = m.get("content", "")
            if m.get("role") == "system":
                ctx_parts.append(m.get("content", ""))
        ctx = "\n".join(ctx_parts)
        # Cite opened entries if present in system context.
        ids = re.findall(r"\[([0-9]{8}-[0-9]{4}-[a-z0-9-]+)\]", ctx)
        low = user.lower()
        ctx_low = ctx.lower()
        # Negative probes: unknown only when memory genuinely lacks the concept.
        for phrase, key in [("mother", "mother"), ("birthday", "birthday"), ("bank pin", "pin"),
                            ("spouse", "spouse"), ("paris", "paris"), ("password", "password")]:
            if phrase in low and key not in ctx_low:
                return "I don't have that in memory. I don't want to guess."
        # Answer from the best-matching memory line (mock of "read memory, answer").
        uf = _filtered(user)
        best, bline = 0.0, None
        for line in ctx.splitlines():
            s = line.strip()
            # Only memory lines are candidates (never rules/BOOT text).
            if not (s.startswith("[") or s.startswith("-") or s.startswith("Entry ")):
                continue
            o = _overlap(uf, _filtered(line))
            if o > best:
                best, bline = o, line.strip()
        if bline and best >= 0.12:
            return f"Based on memory: {bline[:280]}"
        # Statement, not a question: acknowledge (extraction still runs server-side).
        if not re.search(r"\?\s*$", user.strip()) and not re.match(
                r"(?i)(what|who|when|where|which|how|do|does|did|is|are|can|should|am)\b", user.strip()):
            return f"(mock Muse) Noted: {user.strip()[:200]}"
        if ids:
            return f"Based on memory {', '.join('[' + i + ']' for i in ids[:3])}: {user.strip()[:160]}"
        return "I don't have that in memory. I don't want to guess."


def _overlap(a, b):
    ta = set(re.findall(r"[a-z0-9]+", (a or "").lower()))
    tb = set(re.findall(r"[a-z0-9]+", (b or "").lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


_STOP = set(("the a an and or of to in on for with is are was were be been being it its this that "
                 "you your yours we our ours they their them he she him her his my me mine do does did done "
                 "have has had having am an at as by from into over than too very just also any some more most "
                 "other such no nor if then there here when what which who whom how where why can could should "
                 "would will shall may might must user assistant based memory noted s t don").split())


def _filtered(text):
    return " ".join(t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP)


def _mock_extract(turn_text, index_gists):
    """Deterministic mock: catches 'my X is Y', 'we decided', corrections->supersede."""
    ops = []
    t = turn_text or ""
    # Parse existing gists for supersede matching: "- [id] title: gist".
    existing = re.findall(r"\[([0-9]{8}-[0-9]{4}-[a-z0-9-]+)\]\s*([^:\n]*):?\s*([^\n]*)",
                          index_gists or "")
    if re.search(r"\b(actually|correction|not .* anymore|wrong|moved|changed|now use)\b", t, re.I):
        best, bid, btitle = 0.0, None, ""
        for eid, title, gist in existing:
            o = _overlap(_filtered(t), _filtered(title + " " + title + " " + gist))
            if o > best:
                best, bid, btitle = o, eid, title
        if bid and best >= 0.20:
            turn_first = re.sub(r"^(user|assistant):\s*", "", t.strip().splitlines()[0], flags=re.I)[:80]
            return [{"kind": "supersede", "old_id": bid,
                     "title": turn_first or ("Updated " + (btitle or ""))[:80],
                     "types": ["correction"], "body": t.strip()[:400]}]
        ops.append({"kind": "create", "title": "Correction noted",
                    "types": ["correction"], "body": t.strip()[:400], "source": "user-stated"})
        return ops[:3]
    m = re.search(r"my ([\w ]{2,30}) is ([^.!\n]{2,80})", t, re.I)
    if m:
        ops.append({"kind": "create", "title": f"User {m.group(1).strip()}",
                    "types": ["identity"], "body": t.strip()[:400], "source": "user-stated"})
    m2 = re.search(r"we decided ([^.!\n]{3,120})", t, re.I)
    if m2:
        ops.append({"kind": "create", "title": f"Decision: {m2.group(1).strip()[:50]}",
                    "types": ["history"], "body": t.strip()[:400], "source": "user-stated"})
    # Generic fallback (mock of real extraction): store substantive turns as facts.
    # Never store pure questions (no new durable fact in a question).
    first_user = re.sub(r"^(user|assistant):\s*", "", t.strip().splitlines()[0]) if t.strip() else ""
    if not ops and not first_user.strip().endswith("?") and len(_filtered(t).split()) >= 5 and len(t.strip()) >= 30:
        first = re.sub(r"^(user|assistant):\s*", "", t.strip().splitlines()[0])[:60]
        ops.append({"kind": "create", "title": first or "Noted fact",
                    "types": ["fact"], "body": t.strip()[:400], "source": "conversation"})
    return ops[:3]
