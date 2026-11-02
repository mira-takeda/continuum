"""THE one process: UI + API + memory + Muse Code runtime (MSP). Stdlib only.

Run:  python server.py [--port 8137]
Open: http://127.0.0.1:8137/

Conversation path: browser -> this app -> `muse serve` (MSP stdio) -> Muse Spark.
Muse Code's text, tool activity, approvals, questions, errors and results are
streamed to the browser verbatim. Memory context (derived CORE + retrieved
entries) augments each turn; Muse Code's own ```memory-ops block maintains
the encyclopedia (parsed deterministically, no second LLM).
"""
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from memory import access, approvals as approvals_mod
from memory import core as core_mod, extract as extract_mod, index as index_mod
from memory import projects as projects_mod
from memory import retrieve as retrieve_mod, store
from memory import turn as turn_mod
from memory.msp import (MSPError, MSPHost, OFFICIAL_DOCS, OFFICIAL_INSTALL_PS1,
                        OFFICIAL_INSTALL_SH,
                        OFFICIAL_SETUP, auth_status, find_muse, muse_version)

SESSIONS = os.path.join(ROOT, "data", "sessions.json")
MSP_SESSIONS = os.path.join(ROOT, "data", "msp_sessions.json")
TRANSCRIPTS = os.path.join(ROOT, "data", "transcripts.json")
MAX_TRANSCRIPT_TURNS = 500
TURN_TIMEOUT = 600


def install_command():
    """Platform-correct copyable installer (official flow per OS)."""
    return OFFICIAL_INSTALL_PS1 if os.name == "nt" else OFFICIAL_INSTALL_SH

# Localhost hardening (audit fix 1): per-process token + Host/Origin checks.
# The token is generated fresh on every process start, served only inside the
# page this process renders, and never persisted (no data/, logs, Git).
API_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-Continuum-Token"
TOKEN_PLACEHOLDER = "__CONTINUUM_TOKEN__"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SEC_HEADERS = {"X-Content-Type-Options": "nosniff",
               "Referrer-Policy": "no-referrer",
               "X-Frame-Options": "DENY",
               "Content-Security-Policy": "frame-ancestors 'none'"}


def _host_ok(host_hdr):
    if not host_hdr:
        return True  # non-browser clients; the token still guards mutations
    h = host_hdr.strip().lower().split(",")[0].strip()
    if h.startswith("["):
        h = h[1:].split("]")[0]
    else:
        h = h.split(":")[0]
    return h in LOCAL_HOSTS


def _origin_ok(headers):
    for k in ("Origin", "Referer"):
        v = headers.get(k)
        if v:
            try:
                host = (urllib.parse.urlparse(v).hostname or "").lower()
            except Exception:
                return False
            if host not in LOCAL_HOSTS:
                return False
    return True


def _token_ok(headers):
    try:
        return secrets.compare_digest(headers.get(TOKEN_HEADER, "").encode("ascii"),
                                      API_TOKEN.encode("ascii"))
    except Exception:
        return False

HOST = None
HOST_LOCK = threading.Lock()
RESUMED = set()
TURN_LOCKS = {}
ACTIVE_TURNS = {}  # bid -> running turn_id; read unlocked (steer refusal closes the race)
LOGIN_PROC = None
LOGIN_OUT = []
LOGIN_LOCK = threading.Lock()


def _login_drain(proc):
    try:
        for line in proc.stdout:
            with LOGIN_LOCK:
                LOGIN_OUT.append(line)
                del LOGIN_OUT[:-100]
    except Exception:
        pass


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def transcript_load(path, bid):
    """Turn pairs [{turn, ts, user, assistant}] for one browser session."""
    data = load_json(path, {}) or {}
    turns = data.get(bid) or []
    return [t for t in turns if isinstance(t, dict)]


def transcript_append(path, bid, user_text, assistant_text, turn_label,
                      attachments=None):
    """Persist one completed turn pair; oldest trimmed past the cap."""
    data = load_json(path, {}) or {}
    turns = [t for t in (data.get(bid) or []) if isinstance(t, dict)]
    meta = []
    for a in (attachments or []):
        if isinstance(a, dict):
            meta.append({"name": a.get("name", ""), "size": a.get("size", 0),
                         "type": a.get("type", "")})
    turns.append({"turn": turn_label, "ts": store.utcnow(),
                  "user": user_text or "", "assistant": assistant_text or "",
                  "attachments": meta})
    data[bid] = turns[-MAX_TRANSCRIPT_TURNS:]
    save_json(path, data)
    return len(data[bid])


ATT_SUBDIR = "attachments"
MAX_ATTS = 5
MAX_ATT_BYTES = 10_000_000


def att_sanitize(name):
    base = os.path.basename((name or "").strip()) or "file"
    base = "".join(c for c in base if c not in "\x00/\\")
    return (base.strip().strip(".") or "file")[:120]


def att_parse_multipart(body, ctype):
    """Minimal multipart/form-data parser (stdlib email)."""
    from email.parser import BytesParser
    from email.policy import HTTP
    raw = (b"Content-Type: " + ctype.encode("latin-1", "ignore")
           + b"\r\nMIME-Version: 1.0\r\n\r\n" + (body or b""))
    msg = BytesParser(policy=HTTP).parsebytes(raw)
    fields, files = {}, []
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition") or ""
        fn = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if fn is None:
            try:
                fields[name] = payload.decode("utf-8")
            except Exception:
                fields[name] = ""
        else:
            files.append({"field": name, "filename": fn,
                          "ctype": part.get_content_type(), "data": payload})
    return fields, files


def att_stage(root, bid, files):
    """Store uploaded bytes under data/attachments/<bid>/; no interpretation."""
    import secrets
    if not files or len(files) > MAX_ATTS:
        raise ValueError("send 1..%d files" % MAX_ATTS)
    d = os.path.join(root, "data", ATT_SUBDIR, bid)
    os.makedirs(d, exist_ok=True)
    out = []
    for f in files:
        data = f.get("data") or b""
        if len(data) > MAX_ATT_BYTES:
            raise ValueError("file too large (max 10 MB): "
                             + str(f.get("filename") or "")[:80])
        attid = secrets.token_hex(8)
        safe = att_sanitize(f.get("filename"))
        with open(os.path.join(d, attid + "__" + safe), "wb") as fh:
            fh.write(data)
        out.append({"id": attid, "name": safe, "size": len(data),
                    "type": f.get("ctype") or ""})
    try:
        mp = os.path.join(d, "_manifest.json")
        man = load_json(mp, {}) or {}
        for a in out:
            man[a["id"]] = {"type": a["type"]}
        save_json(mp, man)
    except Exception:
        pass
    return out


def att_resolve(root, bid, ids):
    """Map staged attachment ids back to absolute paths (same chat only)."""
    if not ids:
        return []
    d = os.path.join(root, "data", ATT_SUBDIR, bid)
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    found = {}
    for n in names:
        if "__" in n:
            found[n.split("__", 1)[0]] = n
    man = load_json(os.path.join(d, "_manifest.json"), {}) or {}
    out = []
    for i in ids:
        n = found.get(i)
        if not n:
            raise ValueError("unknown attachment id for this chat")
        p = os.path.join(d, n)
        try:
            size = os.path.getsize(p)
        except OSError:
            raise ValueError("attachment file missing")
        out.append({"id": i, "name": n.split("__", 1)[1], "size": size,
                    "type": (man.get(i) or {}).get("type", ""),
                    "path": os.path.abspath(p)})
    return out


def att_prompt_block(files):
    if not files:
        return ""
    lines = ["<attachments>",
             "The user attached file(s) to this message. Read them with your "
             "file tools when relevant. Do not invent their contents."]
    for f in files:
        lines.append("- %s (%s, %d bytes): %s"
                     % (f.get("name"), f.get("type") or "file",
                        f.get("size", 0), f.get("path")))
    lines.append("</attachments>")
    return "\n".join(lines)


def turn_lock(bid):
    return TURN_LOCKS.setdefault(bid, threading.Lock())


REAP_TIMEOUT = 12


def _reap_orphan_turn(host, sid, turn_id, saw_done):
    """Best-effort: cancel an orphaned turn and consume its completion.

    Called when the turn thread exits WITHOUT having seen done (client
    disconnected, or server-side timeout). Without this, the orphaned
    turn's late done poisons the next turn on the same session
    (born-cancelled turn). Returns True when the orphan's done was
    consumed. Never raises; never blocks past REAP_TIMEOUT.
    """
    if not turn_id or saw_done or sid is None:
        return False
    try:
        host.cancel_turn(sid, turn_id)
    except Exception:
        pass
    deadline = time.time() + REAP_TIMEOUT
    try:
        while time.time() < deadline:
            try:
                fr = host.notifications(timeout=0.5, sid=sid)
            except TypeError:
                fr = host.notifications(timeout=0.5)
            if fr is None:
                try:
                    alive = host.alive()
                except Exception:
                    alive = True
                if not alive:
                    return True
                continue
            try:
                ev = turn_mod.norm_frame(fr, sid)
            except Exception:
                continue
            if ev is None:
                continue
            if ev.get("t") == "done":
                end2 = time.time() + 1.0
                while time.time() < end2:
                    try:
                        host.notifications(timeout=0.2, sid=sid)
                    except TypeError:
                        host.notifications(timeout=0.2)
                return True
    except Exception:
        pass
    return False


def _wait_turn_end(bid, timeout=20):
    """True once no turn runs for bid (lock free + no active turn). Bounded."""
    deadline = time.time() + timeout
    lk = turn_lock(bid)
    while time.time() < deadline:
        if ACTIVE_TURNS.get(bid) is None and lk.acquire(blocking=False):
            lk.release()
            return True
        time.sleep(0.1)
    return False


def get_host():
    global HOST
    with HOST_LOCK:
        if HOST is not None and HOST.alive():
            return HOST
        if HOST is not None:
            try:
                HOST.stop()
            except Exception:
                pass
        HOST = None
        RESUMED.clear()
        HOST = MSPHost(ROOT, experimental=True)
        return HOST


def stop_host():
    global HOST
    with HOST_LOCK:
        if HOST is not None:
            try:
                HOST.stop()
            except Exception:
                pass
        HOST = None
        RESUMED.clear()


def _msp_sid(mapping, bid):
    v = (mapping or {}).get(bid)
    if isinstance(v, dict):
        return v.get("sid")
    return v


def _desired_root(bid):
    """Stable broad workspace (user home), independent of the active project.

    Terminal parity: Muse's file tools only operate inside their workspace
    root, so rooting sessions at the app repo or a single project folder
    artificially narrows Muse below its normal capability. The active
    project still drives .continuum.md context, memory routing, history,
    and UI state - it just no longer fences the filesystem. Never raises.
    """
    try:
        home = os.path.expanduser("~")
        if home and os.path.isdir(home):
            return os.path.abspath(home)
    except Exception:
        pass
    return ROOT


def ensure_msp_session(host, bid):
    """Resume the mapped Muse session on this host, or start+subscribe one.

    The session's workspace root follows the active project (else app ROOT).
    A root change leaves the old session quietly and starts a fresh one, so
    file operations always land in the active project's folder.
    """
    mapping = load_json(MSP_SESSIONS, {})
    v = mapping.get(bid)
    if isinstance(v, dict):
        sid, mapped_root = v.get("sid"), v.get("root")
    else:
        sid, mapped_root = v, None
    desired = _desired_root(bid)
    same_root = (os.path.normcase(mapped_root or ROOT) == os.path.normcase(desired))
    if sid and not same_root:
        try:
            host.leave_session(sid)
        except Exception:
            pass
        RESUMED.discard(sid)
        sid = None
    if sid and sid in RESUMED and host.alive():
        mapping[bid] = {"sid": sid, "root": desired}
        save_json(MSP_SESSIONS, mapping)
        return sid
    if sid:
        for attempt in range(2):
            try:
                host.resume_session(sid)
                host.subscribe(sid)
                RESUMED.add(sid)
                mapping[bid] = {"sid": sid, "root": desired}
                save_json(MSP_SESSIONS, mapping)
                return sid
            except MSPError as e:
                # Restart race: previous host may still be releasing the lock.
                if "sessionInUse" in str(e) and attempt == 0:
                    time.sleep(3)
                    continue
                break
    # promptUnmatched = terminal default: prompt for anything policy doesn't
    # auto-decide (writes, shell, ...). onRequest would skip most prompts.
    res = host.start_session(approval_mode="promptUnmatched", workspace_root=desired)
    sid = (res.get("session") or {}).get("sessionId")
    if not sid:
        raise MSPError("session/start returned no sessionId")
    host.subscribe(sid)
    mapping[bid] = {"sid": sid, "root": desired}
    save_json(MSP_SESSIONS, mapping)
    RESUMED.add(sid)
    return sid


def log_usage(entry):
    P = store.data_paths(ROOT)
    with open(P["usage"], "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


OPS_TAIL_HOLD = max(len("```memory-ops"), len("```project-ops"))  # partial markers never emitted


class ItemTextJoiner:
    """Separate consecutive text runs from DIFFERENT native message items.

    One turn may commit several assistant messages (native log kind
    assistant_message_committed, distinct itemIds). Terminal Muse renders
    each as its own block; joining with "" glues them ("...docx.Ale...").
    Same-item chunks stream verbatim; on item change a paragraph break is
    inserted only when neither side already has whitespace.
    """

    def __init__(self):
        self.last_item = None
        self.ends_ws = True

    def feed(self, txt, item_id):
        txt = txt or ""
        iid = item_id or ""
        if iid and self.last_item and iid != self.last_item \
           and not self.ends_ws and txt and not txt[0].isspace():
            txt = "\n\n" + txt
        if txt:
            if iid:
                self.last_item = iid
            self.ends_ws = txt[-1].isspace()
        return txt


class OpsGate:
    """Incremental protocol-fence withhold for streamed chat text.

    feed() returns newly displayable clean text; flush_remainder() returns
    (tail, memory_block_or_None, project_block_or_None) via the two
    single parse authorities (extract.split_ops_block and
    projects.strip_project_block). Structural separation, not a text
    scrubber: the earliest opening fence is detected incrementally,
    everything from it is held, and an unclosed fence at flush emits as
    plain text — user-visible content is never lost.
    """
    def __init__(self):
        self.raw = ""
        self.buf = ""
        self.shown = 0
        self.blocked = False
        self.flushed = False

    def feed(self, chunk):
        self.raw += chunk or ""
        if self.blocked:
            return ""
        self.buf += chunk or ""
        i = self.buf.find(extract_mod.MEMORY_FENCE_OPEN)
        j = self.buf.find(projects_mod.PROJECT_FENCE_OPEN)
        if j >= 0 and (i < 0 or j < i):
            i = j
        if i >= 0:
            self.blocked = True
            out = self.buf[:i].rstrip()
            self.buf = ""
            self.shown += len(out)
            return out
        if len(self.buf) <= OPS_TAIL_HOLD:
            return ""
        out = self.buf[:-OPS_TAIL_HOLD]
        self.buf = self.buf[-OPS_TAIL_HOLD:]
        self.shown += len(out)
        return out

    def flush_remainder(self):
        if self.flushed:
            return "", None, None
        self.flushed = True
        clean, block = extract_mod.split_ops_block(self.raw)
        clean, proj = projects_mod.strip_project_block(clean)
        out = clean[self.shown:]
        self.shown = len(clean)
        return out, block, proj


def run_turn_sse_or_steer(handler, bid, message, _host=None, attachments=None):
    """Steer into the running turn when one is active, else run a normal turn.

    Native turn/steer with expectedTurnId: the id closes the race where the
    turn completes between our read and the steer (tdd SS3.3) — input meant
    for turn A can never leak into turn B. A refused steer answers {t:queued}
    and the CLIENT resubmits at turn end, so input is never lost and never
    silently dropped. _host is a test seam (unit tests inject a fake).
    """
    lk = turn_lock(bid)
    if lk.acquire(blocking=False):
        lk.release()
        run_turn_sse(handler, bid, message, attachments=attachments)
        return

    def emit(obj):
        try:
            handler.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
            handler.wfile.flush()
        except Exception:
            raise StopIteration

    try:
        host = _host or get_host()
    except MSPError as e:
        try:
            emit({"t": "error", "text": str(e)})
        except StopIteration:
            pass
        return
    mapping = load_json(MSP_SESSIONS, {})
    sid = _msp_sid(mapping, bid)
    turn_id = ACTIVE_TURNS.get(bid)
    if sid and turn_id:
        try:
            host.steer_turn(sid, turn_id, message)
        except MSPError:
            pass  # refused: turn ended mid-race -> explicit queue below
        else:
            try:
                emit({"t": "steered", "turn_id": turn_id})
                emit({"t": "done", "terminal": "steered"})
            except StopIteration:
                pass
            return
    try:
        emit({"t": "queued"})
        emit({"t": "done", "terminal": "queued"})
    except StopIteration:
        pass


def run_turn_sse(handler, bid, message, attachments=None):
    def emit(obj):
        try:
            handler.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
            handler.wfile.flush()
        except Exception:
            raise StopIteration

    t0 = time.time()
    P = store.data_paths(ROOT)
    os.makedirs(P["entries"], exist_ok=True)
    try:
        host = get_host()
    except MSPError as e:
        emit({"t": "error", "text": str(e), "need_install": True})
        return
    if host.provider == "meta":
        st = auth_status()
        if not st["logged_in"]:
            emit({"t": "error", "text": "Muse Code is not logged in. " + st["text"][:300],
                  "need_auth": True})
            return
    lk = turn_lock(bid)
    if not lk.acquire(blocking=False):
        emit({"t": "error", "text": "A turn is already running in this chat."})
        return
    sid, turn_id, saw_done = None, None, False
    try:
        # Pre-turn: session routing ONLY (project switches, no filesystem
        # effect). Creation is never intercepted: Muse acts with native
        # tools first, and post-turn bookkeeping registers what exists.
        try:
            pre_ops = [o for o in projects_mod.detect_project_request(message)
                       if isinstance(o, dict) and (o.get("kind") or "").lower() == "activate_project"]
            pre_writes = projects_mod.apply_project_ops(ROOT, pre_ops, bid) if pre_ops else []
        except Exception:
            pre_ops, pre_writes = [], []
        pre_note = ""
        if pre_writes:
            parts = []
            for w in pre_writes:
                k = w.get("kind")
                if k == "project-created":
                    parts.append(f"created project {w.get('name')} at {w.get('path')} (now active)")
                elif k == "project-registered":
                    parts.append(f"registered project {w.get('name')} at {w.get('path')}")
                elif k == "project-activated":
                    parts.append(f"switched to project {w.get('name')} at {w.get('path')}")
                else:
                    parts.append(f"FAILED ({w.get('op')}: {w.get('reason')})")
            pre_note = ("<continuum-note>This turn's project action was ALREADY executed by the "
                        "backend before your reply: " + "; ".join(parts) + ". Do NOT emit "
                        "project-ops for it again. Acknowledge briefly and continue in the "
                        "active project folder.</continuum-note>")
        try:
            _need_dirs = any((o.get("kind") or "").lower() == "create_project"
                             for o in projects_mod.detect_project_request(message)
                             if isinstance(o, dict))
            dir_snap = projects_mod.snapshot_dirs(ROOT) if _need_dirs else None
        except Exception:
            dir_snap = None
        sid = ensure_msp_session(host, bid)
        sessions = load_json(SESSIONS, {})
        n = int(sessions.get(bid, 0)) + 1
        sessions[bid] = n
        save_json(SESSIONS, sessions)
        turn = f"s{bid}t{n}"
        idx = index_mod.load_or_build(ROOT)
        ret = retrieve_mod.retrieve(ROOT, None, message, system="S2")
        try:
            proj_section = projects_mod.assemble(ROOT, bid)
        except Exception:
            proj_section = ""
        try:
            ops_help = projects_mod.project_ops_help(ROOT, bid)
        except Exception:
            ops_help = ""
        # Product-only prefix: project ops are instructions, not memory, so they
        # lead the prompt instead of sitting inside <personal-memory>. The shared
        # turn core is untouched; evals never see this prefix.
        full = turn_mod.build_turn_text(ROOT, message, ret, idx, proj_section, leak_guard=True)
        if ops_help:
            full = "<continuum-system>\n" + ops_help + "\n</continuum-system>\n\n" + full
        if pre_note:
            full += "\n\n" + pre_note
        att_block = att_prompt_block(attachments or [])
        if att_block:
            full += "\n\n" + att_block

        def on_idle():
            handler.wfile.write(b": ping\n\n")
            handler.wfile.flush()

        events = []
        turn_id = None
        gate = OpsGate()
        joiner = ItemTextJoiner()
        shown = []
        for ev in turn_mod.stream_turn(host, sid, full, message, TURN_TIMEOUT, on_idle):
            if ev.get("t") == "turn-started":
                turn_id = ev.get("turn_id")
                ACTIVE_TURNS[bid] = turn_id
                emit({"t": "started", "session_id": sid, "turn_id": turn_id,
                      "provider": host.provider,
                      "model": getattr(host, "model", None) or "muse-default",
                      "opened": ret["opened"], "paths": ret["paths"],
                      "shortlist": ret["shortlist"], "deep": ret["deep"],
                      "attachments": attachments or []})
                continue
            if ev.get("t") == "approval":
                # Presentation-only explanation; the original subject/args
                # below stay byte-identical and the decide path is untouched.
                try:
                    ev["explanation"] = approvals_mod.explain_approval(ev)
                except Exception:
                    ev["explanation"] = approvals_mod.fallback()
            if ev.get("t") == "text":
                events.append(ev)
                chunk = gate.feed(joiner.feed(ev.get("text") or "",
                                              ev.get("itemId") or ""))
                if chunk:
                    shown.append(chunk)
                    emit({"t": "text", "text": chunk})
                continue
            events.append(ev)
            if ev.get("t") == "done":
                events.append(ev)
                saw_done = True
                tail, ops_block, proj_block = gate.flush_remainder()
                if tail:
                    shown.append(tail)
                    emit({"t": "text", "text": tail})
                if ops_block:
                    emit({"t": "memory-ops", "block": ops_block})
                if proj_block:
                    emit({"t": "project-ops", "block": proj_block})
                emit(ev)
                continue
            events.append(ev)
            emit(ev)
        tail, ops_block, proj_block = gate.flush_remainder()
        if tail:
            shown.append(tail)
            emit({"t": "text", "text": tail})
        if ops_block:
            emit({"t": "memory-ops", "block": ops_block})
        if proj_block:
            emit({"t": "project-ops", "block": proj_block})
        summary = turn_mod.summarize(events)
        if summary["terminal"] is None and turn_id:
            # Server-side timeout (or silent stream end): explicit error,
            # never a silent empty turn. Then reap the orphan so its late
            # completion cannot poison the next turn on this session.
            emit({"t": "error", "timeout": True, "terminal": "timeout",
                  "text": f"Turn timed out after {TURN_TIMEOUT}s with no "
                          "completion from Muse. Partial output (if any) is "
                          "kept below; send your message again to retry."})
            _reap_orphan_turn(host, sid, turn_id, saw_done)
        text = summary["text"]
        shown_text = "".join(shown)
        tok = summary["usage"] or {}
        model_name = f"muse-code/{muse_version() or 'unknown'}"
        pre_done = set()
        for o in (pre_ops or []):
            if isinstance(o, dict):
                pre_done.add(((o.get("kind") or "").lower(), (o.get("name") or "").lower()))
        post_ops = [o for o in projects_mod.parse_project_block(text)
                    if not isinstance(o, dict) or
                    ((o.get("kind") or "").lower(), (o.get("name") or "").lower()) not in pre_done]
        proj_writes = projects_mod.apply_project_ops(ROOT, post_ops, bid)
        try:
            new_dirs = projects_mod.diff_new_dirs(dir_snap, ROOT) if dir_snap else {}
        except Exception:
            new_dirs = {}
        try:
            keep_writes = projects_mod.apply_project_ops(
                ROOT, projects_mod.bookkeeping_ops(ROOT, bid, message, new_dirs), bid)
        except Exception:
            keep_writes = []
        writes = extract_mod.apply_ops_from_block(
            ROOT, text, turn, model_name,
            active_project=(projects_mod.active_for(ROOT, bid) or ""))
        writes = (pre_writes or []) + (proj_writes or []) + (keep_writes or []) + (writes or [])
        # Transcript = exactly what the user saw (gated stream, already
        # item-separated); fall back to stripped raw text if nothing emitted.
        if shown_text:
            display_text = shown_text
        else:
            try:
                display_text = extract_mod.split_ops_block(text)[0]
            except Exception:
                display_text = text
            try:
                display_text = projects_mod.strip_project_block(display_text)[0]
            except Exception:
                pass
        try:
            transcript_append(TRANSCRIPTS, bid, message, display_text, turn,
                                attachments=attachments)
        except Exception:
            pass
        for w in writes:
            if (w.get("kind") or "").startswith(("project-", "rejected", "duplicate")):
                continue  # project layer has no index entries
            try:
                index_mod.update_one(ROOT, w["entry_id"])
            except Exception:
                pass
        core_mod.rebuild(ROOT)
        idx2 = index_mod.load_or_build(ROOT)
        cap = idx2.get("config", {}).get("cap_entries", 300)
        ms = int((time.time() - t0) * 1000)
        log_usage({"ts": store.utcnow(), "model": model_name, "purpose": "chat-msp",
                   "system": "S2", "timeout": summary["terminal"] is None,
                   "prompt_tokens": tok.get("promptTokens"),
                   "total_tokens": tok.get("totalTokens"), "agent_chars": len(text),
                   "latency_ms": ms, "mocked": False, "provider": host.provider,
                   "session": sid, "turn": turn_id})
        emit({"t": "writes", "writes": writes, "turn": turn,
              "index_size": len(idx2.get("entries", {})),
              "cap_warning": len(idx2.get("entries", {})) >= cap,
              "full_text": text})
    except StopIteration:
        # Client gone mid-turn: cancel + reap the orphan before releasing
        # the lock so its late done cannot terminate the next turn.
        try:
            _reap_orphan_turn(host, sid, turn_id, saw_done)
        except Exception:
            pass
    except MSPError as e:
        try:
            emit({"t": "error", "text": str(e)})
        except StopIteration:
            pass
    except Exception as e:
        try:
            emit({"t": "error", "text": f"app error: {e}"})
        except StopIteration:
            pass
    finally:
        try:
            ACTIVE_TURNS.pop(bid, None)
            lk.release()
        except Exception:
            pass


def lab_state():
    P = store.data_paths(ROOT)
    idx = index_mod.load_or_build(ROOT)
    core = core_mod.load_or_build(ROOT)
    log_tail, usage_tail = [], []
    if os.path.exists(P["log"]):
        with open(P["log"], encoding="utf-8") as f:
            log_tail = [json.loads(l) for l in f.read().splitlines()[-20:] if l.strip()]
    tot_in = tot_out = calls = 0
    if os.path.exists(P["usage"]):
        with open(P["usage"], encoding="utf-8") as f:
            for l in f.read().splitlines()[-200:]:
                try:
                    u = json.loads(l)
                    if isinstance(u.get("prompt_tokens"), int):
                        tot_in += u["prompt_tokens"]
                    if isinstance(u.get("total_tokens"), int):
                        tot_out += u["total_tokens"]
                    calls += 1
                    usage_tail.append(u)
                except Exception:
                    pass
    mapping = load_json(MSP_SESSIONS, {})
    try:
        host = HOST if (HOST is not None and HOST.alive()) else None
    except Exception:
        host = None
    return {"index": idx, "core": core, "log": log_tail[-12:],
            "usage_tail": usage_tail[-12:],
            "usage_totals": {"calls": calls, "prompt_tokens": tot_in, "total_tokens": tot_out},
            "msp_sessions": mapping,
            "host": {"alive": host is not None,
                     "provider": (host.provider if host else os.environ.get("MUSE_PROVIDER", "meta"))}}


def pro_panels_state(host, sid):
    """Pro Mode read-only panels. Fail-soft per section; never raises.

    host is an MSPHost (or duck-typed fake in tests); sid may be None when
    no Muse session exists yet for this chat.
    """
    def section(fn, *a):
        try:
            r = fn(*a)
            return r if isinstance(r, dict) else {"value": r}
        except Exception as e:
            return {"unavailable": str(e)[:200]}
    out = {"msp_session": sid, "host_alive": bool(host is not None)}
    if host is None or not sid:
        out.update({"session": {}, "tasks": {}, "usage": {}, "pending": {}})
        return out
    out["session"] = section(host.read_session, sid)
    out["tasks"] = section(host.list_tasks, sid)
    out["usage"] = section(host.read_usage)
    out["pending"] = section(host.list_pending, sid)
    return out


REASONING_TIERS = ("none", "minimal", "low", "medium", "high", "xhigh",
                   "max", "ultra")
SKILL_SCOPES = ("user", "project", "bundled", "plugin")
SKILL_ACTIVATIONS = ("on", "userInvocableOnly", "off")
APPROVAL_MODES = ("allowAll", "promptUnmatched", "onRequest", "denyUnmatched")


def pro_control(host, sid, op, args):
    """Dispatch one validated native control op. Returns the MSP result verbatim.

    Raises ValueError (bad op/args -> HTTP 400) or MSPError (-> HTTP 500).
    """
    a = args if isinstance(args, dict) else {}
    if op == "listModels":
        return host.list_models(sid)
    if op == "listSkills":
        return host.list_skills(sid)
    if op == "setModel":
        mid = (a.get("modelId") or "").strip()
        if not mid:
            raise ValueError("setModel needs a non-empty modelId")
        return host.set_model(sid, mid, a.get("providerId") or None,
                              a.get("profileId"),
                              a.get("displayLabel") or None)
    if op == "setReasoningEffort":
        eff = a.get("effort")
        if eff not in REASONING_TIERS:
            raise ValueError("setReasoningEffort needs one of: "
                             + ", ".join(REASONING_TIERS))
        return host.set_reasoning_effort(sid, eff)
    if op == "rename":
        name = (a.get("name") or "").strip()
        if not name or len(name) > 200:
            raise ValueError("rename needs a non-empty name (<=200 chars)")
        return host.rename_session(sid, name)
    if op == "fork":
        return host.fork_session(sid)
    if op == "sideChat":
        return host.side_chat(sid)
    if op == "compact":
        return host.compact_session(sid)
    if op == "skillActivation":
        sel = (a.get("selector") or "").strip()
        scope, act = a.get("scope"), a.get("activation")
        if not sel:
            raise ValueError("skillActivation needs a selector")
        if scope not in SKILL_SCOPES:
            raise ValueError("skillActivation scope must be one of: "
                             + ", ".join(SKILL_SCOPES))
        if act not in SKILL_ACTIVATIONS:
            raise ValueError("skillActivation activation must be one of: "
                             + ", ".join(SKILL_ACTIVATIONS))
        return host.set_skill_activation(sid, sel, scope, act)
    if op in ("goalSet", "goalEdit"):
        obj = (a.get("objective") or "").strip()
        if not obj:
            raise ValueError(op + " needs a non-empty objective")
        fn = host.goal_set if op == "goalSet" else host.goal_edit
        return fn(sid, obj)
    if op == "goalPause":
        return host.goal_pause(sid)
    if op == "goalResume":
        return host.goal_resume(sid)
    if op == "goalClear":
        return host.goal_clear(sid)
    if op == "setApprovalMode":
        mode = a.get("mode")
        if mode not in APPROVAL_MODES:
            raise ValueError("setApprovalMode mode must be one of: "
                             + ", ".join(APPROVAL_MODES))
        return host.set_approval_mode(sid, mode)
    if op == "listPending":
        return host.list_pending(sid)
    if op == "expLogStreams":
        return host.exp_log_streams(sid)
    if op == "expLogPage":
        st = a.get("stream") if isinstance(a.get("stream"), dict) else {}
        if not (st.get("id") and st.get("kind")):
            raise ValueError("expLogPage needs stream:{id,kind}")
        try:
            limit = int(a.get("limit", 20))
        except (TypeError, ValueError):
            raise ValueError("expLogPage limit must be 1..100")
        if not 1 <= limit <= 100:
            raise ValueError("expLogPage limit must be 1..100")
        frm = a.get("from")
        if frm is not None and not isinstance(frm, dict):
            raise ValueError("expLogPage from must be a cursor object")
        return host.exp_log_page(sid, {"id": st["id"], "kind": st["kind"]},
                                 limit, frm)
    if op == "expProfiles":
        return host.exp_profiles(sid)
    if op == "expSetProfile":
        pid = (a.get("profileId") or "").strip()
        if not pid:
            raise ValueError("expSetProfile needs a profileId")
        return host.exp_set_profile(sid, pid)
    if op == "expLogStreams":
        return host.exp_log_streams(sid)
    if op == "expLogPage":
        st = a.get("stream") if isinstance(a.get("stream"), dict) else {}
        if not (st.get("id") and st.get("kind")):
            raise ValueError("expLogPage needs stream:{id,kind}")
        try:
            limit = int(a.get("limit", 20))
        except (TypeError, ValueError):
            raise ValueError("expLogPage limit must be 1..100")
        if not 1 <= limit <= 100:
            raise ValueError("expLogPage limit must be 1..100")
        frm = a.get("from")
        if frm is not None and not isinstance(frm, dict):
            raise ValueError("expLogPage from must be a cursor object")
        return host.exp_log_page(sid, {"id": st["id"], "kind": st["kind"]},
                                 limit, frm)
    if op == "expProfiles":
        return host.exp_profiles(sid)
    if op == "expSetProfile":
        pid = (a.get("profileId") or "").strip()
        if not pid:
            raise ValueError("expSetProfile needs a profileId")
        return host.exp_set_profile(sid, pid)
    raise ValueError(f"unknown pro op: {op}")


def pro_attach(mapping, bid, new_sid, host):
    """Point one chat at an existing Muse session (fork/sidechat follow-up).

    Verifies via session/read, then rewrites mapping[bid]. The next chat turn
    resumes+subscribes it through the normal path. Raises ValueError.
    """
    sid = (new_sid or "").strip()
    if not sid:
        raise ValueError("attach needs a sessionId")
    rd = host.read_session(sid) or {}
    got = ((rd.get("session") or {}).get("sessionId") or "")
    if got != sid:
        raise ValueError("session/read did not confirm " + sid[:16])
    mapping[bid] = {"sid": sid, "root": _desired_root(bid)}
    return {"attached": sid, "session": rd.get("session", {})}


HIST_FILLER = re.compile(
    r"^(por favor|please|hola|oye|mira|necesito que|quiero que|puedes|"
    r"me puedes|vamos a|vamos|ayuda con|ayudame con|ayúdame con|ayuda|"
    r"muestrame|muéstrame|dime|dame|crea|haz|hazme|genera|abre|explica|"
    r"cuentame|cuéntame|el|la|los|las|un|una|the|a|an)\b[\s,;:]*",
    re.IGNORECASE)


def history_title(text):
    """Deterministic short chat title from the first user message.

    No model call: strip filler openers, keep up to 6 words, cap length.
    Returns "" when there is nothing meaningful (UI shows a localized
    'new conversation' fallback).
    """
    s = re.sub(r"\s+", " ", (text or "").strip().strip("\"'“”‘’.,;:!?")).strip()
    for _ in range(3):
        ns = HIST_FILLER.sub("", s).strip()
        if ns == s or not ns:
            break
        s = ns
    s = " ".join(s.split()[:6])
    if len(s) > 44:
        s = s[:44].rsplit(" ", 1)[0] + "…"
    if not s:
        return ""
    return s[0].upper() + s[1:]


def ui_bootstrap():
    """Aggregation for frontend navigation. Read-only, existing structures."""
    lab = lab_state()
    idx_entries = (lab["index"] or {}).get("entries", {})
    by_state = {"Active": 0, "Recent": 0, "Deep": 0}
    for v in idx_entries.values():
        s = v.get("state", "Recent")
        by_state[s] = by_state.get(s, 0) + 1
    sessions = load_json(SESSIONS, {})
    msp_map = load_json(MSP_SESSIONS, {})
    transcripts = load_json(TRANSCRIPTS, {}) or {}
    history = []
    for bid, n in sorted(sessions.items()):
        turns = [x for x in (transcripts.get(bid) or []) if isinstance(x, dict)]
        first = next((x.get("user", "") for x in turns if x.get("user")), "")
        try:
            proj = projects_mod.active_for(ROOT, bid) or None
        except Exception:
            proj = None
        history.append({
            "id": bid, "turns": n,
            "muse_session": (_msp_sid(msp_map, bid) or "")[:8] or None,
            "title": history_title(first),
            "preview": re.sub(r"\s+", " ", first).strip()[:90],
            "updated": turns[-1].get("ts", "") if turns else "",
            "project": proj})
    exe = find_muse()
    provider = os.environ.get("MUSE_PROVIDER", "meta")
    st = auth_status() if exe else {"logged_in": False, "allowance": False, "text": "not installed"}
    reg = projects_mod.list_projects(ROOT)
    projs = []
    for name, path in sorted(reg.items()):
        fp = os.path.join(path, projects_mod.FILENAME)
        projs.append({"id": name, "name": name, "path": path, "has_file": os.path.exists(fp)})
    return {
        "connected": bool(exe) and (provider == "echo" or st["logged_in"]),
        "session_id": None,
        "model": os.environ.get("MUSE_MODEL") or "Muse Spark",
        "runtime": {"installed": bool(exe), "path": exe, "version": muse_version() if exe else None,
                    "provider": provider, "logged_in": st["logged_in"],
                    "allowance": st.get("allowance", False), "auth_text": st["text"][:300],
                    "docs": OFFICIAL_DOCS, "setup": OFFICIAL_SETUP,
                    "install_sh": OFFICIAL_INSTALL_SH,
                    "install_cmd": install_command()},
        "workspace": projects_mod.get_workspace(ROOT),
        "projects": projs,
        "history": history,
        "memory": {"core_ids": lab["core"].get("entry_ids", []),
                   "core_tokens": lab["core"].get("tokens", 0),
                   "index_total": len(idx_entries), "by_state": by_state},
        "lab": {"events": lab["log"], "usage_totals": lab["usage_totals"]},
    }


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json; charset=utf-8", extra=None):
        body = json.dumps(obj).encode() if isinstance(obj, (dict, list)) else obj
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SEC_HEADERS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _check_host(self):
        if not _host_ok(self.headers.get("Host")):
            self._send(403, {"error": "forbidden host"})
            return False
        return True

    def _check_mutation(self):
        if not self._check_host():
            return False
        if not _origin_ok(self.headers):
            self._send(403, {"error": "forbidden origin"})
            return False
        if not _token_ok(self.headers):
            self._send(403, {"error": "missing or invalid api token"})
            return False
        return True

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except Exception:
            n = 0
        return json.loads(self.rfile.read(n).decode() or "{}") if n else {}

    def do_GET(self):
        if not self._check_host():
            return
        p = urllib.parse.urlparse(self.path).path
        if p in ("/", "/index.html"):
            with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as f:
                html = f.read().replace(TOKEN_PLACEHOLDER, API_TOKEN)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                              extra={"Cache-Control": "no-store"})
        if p == "/api/pro/panels":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            bid = (q.get("session_id") or ["demo"])[0]
            try:
                host = get_host()
            except MSPError as e:
                return self._send(200, {"host_alive": False, "detail": str(e)[:200],
                                        "msp_session": None, "session": {}, "tasks": {},
                                        "usage": {}, "pending": {}})
            mapping = load_json(MSP_SESSIONS, {})
            sid = _msp_sid(mapping, bid)
            return self._send(200, pro_panels_state(host, sid))
        if p.startswith("/web/") or p in ("/app.js", "/styles.css", "/bridge.js", "/i18n.js",
                                         "/logo.png", "/favicon.ico"):
            name = p.split("/")[-1]
            base, ct = {"app.js": ("app.js", "text/javascript; charset=utf-8"),
                        "styles.css": ("styles.css", "text/css; charset=utf-8"),
                        "bridge.js": ("bridge.js", "text/javascript; charset=utf-8"),
                        "i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
                        "logo.png": ("logo.png", "image/png"),
                        "favicon.ico": ("logo.png", "image/png")}.get(name, (None, None))
            if base:
                return self._send(200, open(os.path.join(ROOT, "web", base), "rb").read(), ct)
            self.send_error(404)
            return
        if p == "/api/lab":
            return self._send(200, lab_state())
        if p == "/api/runtime/status":
            exe = find_muse()
            st = auth_status() if exe else {"logged_in": False, "text": "not installed"}
            return self._send(200, {
                "installed": bool(exe), "path": exe, "version": muse_version() if exe else None,
                "provider": os.environ.get("MUSE_PROVIDER", "meta"),
                "model": os.environ.get("MUSE_MODEL") or None,
                "logged_in": st["logged_in"], "allowance": st.get("allowance", False),
                "auth_text": st["text"][:500],
                "docs": OFFICIAL_DOCS, "setup": OFFICIAL_SETUP,
                "install_sh": OFFICIAL_INSTALL_SH,
                "install_cmd": install_command(),
                "host_alive": bool(HOST is not None and HOST.alive()),
                "msp_sessions": load_json(MSP_SESSIONS, {})})
        if p == "/api/entries":
            P = store.data_paths(ROOT)
            out = []
            for eid in store.list_ids(P["entries"]):
                meta, body = store.load(P["entries"], eid)
                out.append({"id": eid, "title": meta.get("title"), "types": meta.get("types"),
                            "status": meta.get("status"), "updated": meta.get("updated"),
                            "stability": meta.get("stability"), "links": meta.get("links", []),
                            "why": meta.get("why", ""), "gist": " ".join(body.split())[:200]})
            return self._send(200, {"entries": out})
        if p.startswith("/api/entries/"):
            P = store.data_paths(ROOT)
            eid = urllib.parse.unquote(p.rsplit("/", 1)[-1])
            try:
                meta, body = store.load(P["entries"], eid)
                return self._send(200, {"meta": meta, "body": body})
            except ValueError:
                return self._send(400, {"error": "invalid entry id"})
            except Exception:
                return self._send(404, {"error": "entry not found"})
        if p == "/api/ui/bootstrap":
            return self._send(200, ui_bootstrap())
        if p == "/api/projects":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            bid = (q.get("session_id") or [None])[0]
            reg = projects_mod.list_projects(ROOT)
            active = projects_mod.active_for(ROOT, bid) if bid else None
            items = []
            for name, path in sorted(reg.items()):
                fp = os.path.join(path, projects_mod.FILENAME)
                items.append({"id": name, "name": name, "path": path,
                              "active": name == active, "has_file": os.path.exists(fp)})
            return self._send(200, {"workspace": projects_mod.get_workspace(ROOT),
                                    "projects": items, "active": active})
        if p == "/api/project-file":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = (q.get("project_id") or [""])[0]
            try:
                content = projects_mod.read_project(ROOT, name)
            except ValueError as e:
                return self._send(404, {"error": str(e)})
            except OSError as e:
                return self._send(404, {"error": f"cannot read file: {e}"})
            trunc = len(content) > 20000
            return self._send(200, {"name": name,
                                    "path": os.path.join(projects_mod.list_projects(ROOT)[name],
                                                         projects_mod.FILENAME),
                                    "content": content[:20000], "truncated": trunc})
        if p == "/api/transcript":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            bid = (q.get("session_id") or ["demo"])[0]
            return self._send(200, {"session_id": bid,
                                    "turns": transcript_load(TRANSCRIPTS, bid)})
        self.send_error(404)

    def do_POST(self):
        global LOGIN_PROC
        if not self._check_mutation():
            return
        p = urllib.parse.urlparse(self.path).path
        P = store.data_paths(ROOT)
        if p == "/api/attachments":
            ctype = self.headers.get("Content-Type", "")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except Exception:
                length = 0
            if length <= 0 or length > 64 * 1024 * 1024:
                return self._send(413, {"error": "attachment body empty or too large"})
            try:
                fields, files = att_parse_multipart(self.rfile.read(length), ctype)
            except Exception as e:
                return self._send(400, {"error": "bad multipart: %s" % e})
            bid = (fields.get("session_id") or "demo").strip() or "demo"
            try:
                staged = att_stage(ROOT, bid, files)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True, "attachments": staged})
        if p == "/api/chat_stream":
            b = self._body()
            if not find_muse():
                return self._send(409, {"error": "Muse Code is not installed.", "need_install": True})
            if b.get("project_id"):
                try:
                    projects_mod.activate(ROOT, b.get("session_id", "demo"), b["project_id"])
                except ValueError as e:
                    return self._send(404, {"error": str(e)})
            bid = b.get("session_id", "demo")
            try:
                att_files = att_resolve(ROOT, bid, b.get("attachments") or [])
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            run_turn_sse_or_steer(self, bid, b.get("message", ""),
                                    attachments=att_files)
            self.close_connection = True
            return
        if p == "/api/projects/activate":
            b = self._body()
            bid = b.get("session_id") or "demo"
            pid = b.get("project_id")
            if not pid:
                projects_mod.clear_active(ROOT, bid)
                return self._send(200, {"ok": True, "active": None})
            try:
                name = projects_mod.activate(ROOT, bid, pid)
            except ValueError as e:
                return self._send(404, {"error": str(e)})
            return self._send(200, {"ok": True, "active": name})
        if p == "/api/projects/register":
            b = self._body()
            try:
                path = projects_mod.register_project(ROOT, b.get("name"), b.get("path"))
            except (ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True, "name": b.get("name"), "path": path})
        if p == "/api/projects/init":
            b = self._body()
            try:
                path = projects_mod.init_project(ROOT, b.get("project_id"))
            except ValueError as e:
                return self._send(404, {"error": str(e)})
            except OSError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True, "path": path})
        if p == "/api/approve":
            b = self._body()
            try:
                host = get_host()
                mapping = load_json(MSP_SESSIONS, {})
                sid = _msp_sid(mapping, b.get("session_id", "demo"))
                if not sid:
                    return self._send(404, {"error": "no Muse session for this chat"})
                r = host.decide(sid, b.get("approvalId"), b.get("choiceId"),
                                b.get("requirementId"), b.get("feedback"))
                return self._send(200, {"ok": True, "result": r})
            except MSPError as e:
                return self._send(500, {"error": str(e)})
        if p == "/api/pro/control":
            b = self._body()
            op = (b.get("op") or "")
            try:
                host = get_host()
            except MSPError as e:
                return self._send(500, {"error": str(e)})
            mapping = load_json(MSP_SESSIONS, {})
            bid = b.get("session_id", "demo")
            sid = _msp_sid(mapping, bid)
            try:
                if op == "attach":
                    r = pro_attach(mapping, bid,
                                   (b.get("args") or {}).get("sessionId"), host)
                    save_json(MSP_SESSIONS, mapping)
                    return self._send(200, {"ok": True, "result": r})
                if op == "listModels":
                    try:
                        sid = ensure_msp_session(host, bid)
                    except MSPError as e:
                        return self._send(500, {"error": str(e)})
                if op != "listModels" and not sid:
                    return self._send(404, {"error": "no Muse session for this chat"})
                r = pro_control(host, sid, op, b.get("args") or {})
                return self._send(200, {"ok": True, "result": r})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except MSPError as e:
                return self._send(500, {"error": str(e)})
        if p == "/api/answer":
            b = self._body()
            try:
                host = get_host()
                mapping = load_json(MSP_SESSIONS, {})
                sid = _msp_sid(mapping, b.get("session_id", "demo"))
                if not sid:
                    return self._send(404, {"error": "no Muse session for this chat"})
                r = host.answer_input(sid, b.get("userInputId"), b.get("answers", []))
                return self._send(200, {"ok": True, "result": r})
            except MSPError as e:
                return self._send(500, {"error": str(e)})
        if p == "/api/stop":
            b = self._body()
            try:
                host = get_host()
                mapping = load_json(MSP_SESSIONS, {})
                bid = b.get("session_id", "demo")
                sid = _msp_sid(mapping, bid)
                if sid:
                    host.cancel_turn(sid, None)
                # Synchronous stop: let the turn thread observe the cancel
                # (bounded) so an immediate resend cannot overlap the dying
                # turn's cancel window.
                quiesced = _wait_turn_end(bid, timeout=20)
                return self._send(200, {"ok": True, "quiesced": quiesced})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        if p == "/api/undo":
            r = store.undo_last(P["entries"], P["log"])
            index_mod.build(ROOT)
            core_mod.rebuild(ROOT)
            return self._send(200, {"undone": bool(r), "op": r})
        if p == "/api/unlink":
            b = self._body()
            try:
                r = store.unlink(P["entries"], P["log"], b.get("from"), b.get("to"))
            except ValueError:
                return self._send(400, {"error": "invalid entry id"})
            index_mod.update_one(ROOT, b.get("from"))
            core_mod.rebuild(ROOT)
            return self._send(200, {"ok": True, "op_id": r.get("op_id")})
        if p == "/api/reset":
            import shutil
            b = self._body()
            for v in load_json(MSP_SESSIONS, {}).values():
                sid = v.get("sid") if isinstance(v, dict) else v
                if not sid:
                    continue
                try:
                    if HOST is not None and HOST.alive():
                        HOST.leave_session(sid)
                except Exception:
                    pass
            save_json(MSP_SESSIONS, {})
            if os.path.isdir(P["entries"]):
                shutil.rmtree(P["entries"])
            os.makedirs(P["entries"], exist_ok=True)
            for k in ("index", "core", "log", "usage"):
                if os.path.exists(P[k]):
                    os.remove(P[k])
            if os.path.exists(SESSIONS):
                os.remove(SESSIONS)
            if os.path.exists(TRANSCRIPTS):
                os.remove(TRANSCRIPTS)
            att_dir = os.path.join(P["data"], ATT_SUBDIR)
            if os.path.isdir(att_dir):
                shutil.rmtree(att_dir)
            projects_mod.clear_mapping(ROOT)
            projects_mod.clear_registry(ROOT)
            projects_mod.clear_workspace(ROOT)
            projects_mod.clear_cache(ROOT)
            # NOTE: external .continuum.md project files are deliberately NOT
            # deleted here; they are human-owned project docs, not runtime state.
            if b.get("mode") == "sample":
                from evals.sample_pack import SEED
                for s in SEED:
                    store.create(P["entries"], P["log"], s["title"], s["types"], s["body"],
                                 ["seed"], "user-stated", "sample-pack", why="sample pack")
            index_mod.build(ROOT)
            core_mod.rebuild(ROOT)
            return self._send(200, {"ok": True})
        if p == "/api/runtime/install":
            b = self._body()
            if not b.get("approve"):
                return self._send(403, {"error": "Installation needs explicit approval.",
                                        "docs": OFFICIAL_DOCS, "install_sh": OFFICIAL_INSTALL_SH,
                                        "install_cmd": install_command()})
            if find_muse():
                return self._send(200, {"ok": True, "already": True, "version": muse_version()})
            if os.name == "nt":
                # No bash heuristic on Windows: the official flow is the
                # PowerShell installer, run by the user. Show the exact
                # copyable command instead of attempting the wrong shell.
                return self._send(200, {"ok": False,
                                        "output": "Automatic install is not supported on Windows. "
                                                  "Run this in PowerShell: " + OFFICIAL_INSTALL_PS1,
                                        "install_cmd": OFFICIAL_INSTALL_PS1,
                                        "docs": OFFICIAL_DOCS})
            # Official installer only; never silently.
            try:
                r = subprocess.run(OFFICIAL_INSTALL_SH, shell=True, capture_output=True,
                                   text=True, timeout=300, encoding="utf-8")
                out = ((r.stdout or "") + (r.stderr or "")).strip()
                return self._send(200, {"ok": r.returncode == 0, "output": out[-3000:],
                                        "docs": OFFICIAL_DOCS})
            except FileNotFoundError:
                return self._send(200, {"ok": False, "output": "No bash shell found.",
                                        "docs": OFFICIAL_DOCS})
            except Exception as e:
                return self._send(500, {"error": str(e), "docs": OFFICIAL_DOCS})
        if p == "/api/runtime/auth/start":
            exe = find_muse()
            if not exe:
                return self._send(409, {"error": "Muse Code is not installed."})
            with LOGIN_LOCK:
                if LOGIN_PROC is not None and LOGIN_PROC.poll() is None:
                    return self._send(200, {"ok": True, "already": True})
                del LOGIN_OUT[:]
                LOGIN_PROC = subprocess.Popen(
                    [exe, "login"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", bufsize=1, shell=(os.name == "nt"), cwd=ROOT)
                threading.Thread(target=_login_drain, args=(LOGIN_PROC,), daemon=True).start()
                return self._send(200, {"ok": True, "setup": OFFICIAL_SETUP})
        if p == "/api/runtime/auth/poll":
            with LOGIN_LOCK:
                proc = LOGIN_PROC
                out = "".join(LOGIN_OUT)
            running = bool(proc is not None and proc.poll() is None)
            st = auth_status()
            return self._send(200, {"running": running, "output": out[-2000:],
                                    "logged_in": st["logged_in"], "auth_text": st["text"][:500]})
        if p == "/api/runtime/auth/cancel":
            with LOGIN_LOCK:
                if LOGIN_PROC is not None and LOGIN_PROC.poll() is None:
                    try:
                        LOGIN_PROC.kill()
                    except Exception:
                        pass
                LOGIN_PROC = None
            return self._send(200, {"ok": True})
        self.send_error(404)

    def log_message(self, *a):
        pass


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8137)
    args = ap.parse_args()
    index_mod.load_or_build(ROOT)
    core_mod.load_or_build(ROOT)
    print(f"continuum on http://127.0.0.1:{args.port}/  muse={find_muse() or 'NOT INSTALLED'}")
    print(f"provider={os.environ.get('MUSE_PROVIDER', 'meta')} (Muse Code manages auth; `muse login` for Meta account)")
    ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()


if __name__ == "__main__":
    main()
