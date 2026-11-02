"""Project-context layer: workspace map + per-project .continuum.md files.

Three context layers, kept separate:
1. Personal encyclopedia (global): identity, preferences, shared history.
2. Workspace map: one .continuum.md at the user-selected workspace root.
3. Project context: one .continuum.md per registered project root.

Rules: project roots come ONLY from explicit registration (never sniffed from
ordinary subfolders); registering or activating a project ensures its
.context file exists (init is a no-op when the file already exists, so
human edits and unknown sections are never touched); files are
human-readable; no secrets are ever written.

Muse-driven lifecycle (```project-ops fenced block, parsed deterministically):
create/register/activate ops are validated and executed HERE by the backend.
Muse never uses shell/mkdir for project setup, and user projects are never
placed inside the app repo unless explicitly allowed per-op.
"""
import json
import os
import re
from datetime import datetime, timezone

FILENAME = ".continuum.md"
SESSIONS_FILE = "project_sessions.json"
WORKSPACE_FILE = "workspace.json"
REGISTRY_FILE = "projects.json"
ASSEMBLE_CAP = 4000

# Canonical section keys in file order; unknown sections are preserved.
PROJECT_SECTIONS = [
    ("what", "## What this is"),
    ("state", "## Current state"),
    ("decisions", "## Decisions"),
    ("pending", "## Pending"),
    ("paths", "## Key paths"),
    ("failed", "## Failed approaches"),
    ("conventions", "## Conventions"),
]
WORKSPACE_SECTIONS = [
    ("projects", "## Projects"),
    ("notes", "## Notes"),
]

SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|passwd|password|bearer|private[_-]?key|"
    r"sk-[a-z0-9]{8,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\btoken\b\s*[:=])")

AUTHORING_PROTOCOL = (
    "Project memory protocol: durable PROJECT-SPECIFIC information learned "
    "here (current state, technical/product decisions, pending work, key "
    "paths/files, failed approaches, project conventions) goes in your "
    "```memory-ops block with \"scope\": \"project:{name}\" plus a \"section\" "
    "from [what, state, decisions, pending, paths, failed, conventions]. "
    "Use kind \"create\" to add, or kind \"update\" with \"replace\" quoting "
    "the outdated text to correct it (history is preserved automatically). "
    "Write ONLY \"scope\": \"project:{name}\" — never another project, even if "
    "mentioned. User identity, preferences, and cross-project history go in "
    "unscoped ops (no scope field) for the personal encyclopedia. Never store "
    "transient chat, command output, logs, or secrets in either place.")

_CACHE = {}


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _data(root):
    d = os.path.join(root, "data")
    os.makedirs(d, exist_ok=True)
    return d


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


# ---------- workspace + registry ----------

def set_workspace(root, path):
    ap = os.path.abspath(path)
    if not os.path.isdir(ap):
        raise ValueError(f"workspace root is not a directory: {path}")
    _save(os.path.join(_data(root), WORKSPACE_FILE), {"root": ap})
    return ap


def get_workspace(root):
    return (_load(os.path.join(_data(root), WORKSPACE_FILE), {}) or {}).get("root")


def list_projects(root):
    return dict((_load(os.path.join(_data(root), REGISTRY_FILE), {}) or {}).get("projects", {}))


def register_project(root, name, path):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name or ""):
        raise ValueError(f"bad project name: {name!r}")
    ap = os.path.abspath(path)
    if not os.path.isdir(ap):
        raise ValueError(f"project root is not a directory: {path}")
    reg = _load(os.path.join(_data(root), REGISTRY_FILE), {}) or {}
    reg.setdefault("projects", {})[name] = ap
    _save(os.path.join(_data(root), REGISTRY_FILE), reg)
    # A registered project without its context file is silently degraded
    # (no project block injected, project-scoped writes rejected), so
    # registration ensures the file. Loud on OSError: the caller sees the
    # failure instead of a project that can never hold context.
    init_project(root, name)
    return ap


def detect_from_path(root, path):
    """Longest-prefix registry match only. Unregistered folders never match."""
    ap = os.path.normcase(os.path.abspath(path))
    best, best_len = None, -1
    for name, p in list_projects(root).items():
        pp = os.path.normcase(p)
        if ap == pp or ap.startswith(pp + os.sep):
            if len(pp) > best_len:
                best, best_len = name, len(pp)
    return best


# ---------- .continuum.md files ----------

def project_file(root, name):
    reg = list_projects(root)
    if name not in reg:
        raise ValueError(f"unknown project: {name}")
    return os.path.join(reg[name], FILENAME)


def workspace_file(root):
    ws = get_workspace(root)
    if not ws:
        raise ValueError("no workspace selected")
    return os.path.join(ws, FILENAME)


def _skeleton(title_line, root_path, sections):
    lines = [title_line, f"root: {root_path}", f"updated: {utcnow()}", ""]
    for _, header in sections:
        lines += [header, ""]
    return "\n".join(lines).rstrip() + "\n"


def init_project(root, name):
    """Explicit creation only. No-op when the file already exists."""
    path = project_file(root, name)
    if os.path.exists(path):
        return path
    reg = list_projects(root)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_skeleton(f"# Project: {name}", reg[name], PROJECT_SECTIONS))
    return path


def init_workspace_map(root):
    path = workspace_file(root)
    if os.path.exists(path):
        return path
    with open(path, "w", encoding="utf-8") as f:
        f.write(_skeleton("# Workspace map", get_workspace(root), WORKSPACE_SECTIONS))
    return path


def parse_file(text):
    """Parse into {title, header:{k:v}, sections:[(header, [lines])]}."""
    lines = (text or "").splitlines()
    title = lines[0] if lines and lines[0].startswith("# ") else ""
    header, sections, cur = {}, [], None
    for line in lines[1:]:
        if line.startswith("## "):
            cur = (line.strip(), [])
            sections.append(cur)
        elif cur is None:
            if ":" in line and line.strip():
                k, v = line.split(":", 1)
                header[k.strip()] = v.strip()
        else:
            cur[1].append(line)
    return {"title": title, "header": header, "sections": sections}


def serialize_file(model, canonical):
    """Re-emit: title, header (updated refreshed), canonical sections in
    fixed order, then unknown sections in original order. All lines kept."""
    order = [h for _, h in canonical]
    by_header = {}
    for h, body in model["sections"]:
        by_header.setdefault(h, []).extend(body)
    out = [model["title"] or "# Project: ?"]
    hdr = dict(model["header"])
    hdr["updated"] = utcnow()
    for k, v in hdr.items():
        out.append(f"{k}: {v}")
    out.append("")
    seen = set()
    for key, h in canonical:
        seen.add(h)
        out.append(h)
        body = [l for l in by_header.get(h, []) if l.strip() != "" or True]
        out.extend(_tidy(body))
        out.append("")
    for h, body in model["sections"]:
        if h not in seen:
            out.append(h)
            out.extend(_tidy(body))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _tidy(body):
    lines = [l.rstrip() for l in body]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def read_cached(root, bid, path):
    """Read once per session; re-read only when mtime changes."""
    key = (os.path.abspath(root), bid, os.path.abspath(path))
    try:
        mt = os.path.getmtime(path)
    except OSError:
        _CACHE.pop(key, None)
        raise
    hit = _CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    _CACHE[key] = (mt, text)
    return text


def cache_has(root, bid):
    pre = (os.path.abspath(root), bid)
    return any(k[:2] == pre for k in _CACHE)


def clear_cache(root=None, bid=None):
    for k in [k for k in _CACHE
              if (root is None or k[0] == os.path.abspath(root))
              and (bid is None or k[1] == bid)]:
        del _CACHE[k]


def read_project(root, name):
    with open(project_file(root, name), encoding="utf-8") as f:
        return f.read()


def find_secret(text):
    m = SECRET_RE.search(text or "")
    return m.group(0)[:40] if m else None


def append_line(root, name, section, text):
    """Append one bullet; skip exact duplicates. Returns (op_kind, path_or_reason)."""
    canon = dict(PROJECT_SECTIONS)
    if section not in canon:
        return "rejected", f"unknown section: {section}"
    path = project_file(root, name)
    if not os.path.exists(path):
        return "rejected", "project file does not exist (init it explicitly)"
    hit = find_secret(text)
    if hit:
        return "rejected", f"secrets-guard matched: {hit}"
    with open(path, encoding="utf-8") as f:
        before = f.read()
    model = parse_file(before)
    bullet = "- " + " ".join((text or "").split())
    for h, body in model["sections"]:
        if h == canon[section] and bullet in [l.strip() for l in body]:
            return "duplicate", path
    placed = False
    for h, body in model["sections"]:
        if h == canon[section]:
            body.append(bullet)
            placed = True
    if not placed:
        model["sections"].append((canon[section], [bullet]))
    with open(path, "w", encoding="utf-8") as f:
        f.write(serialize_file(model, PROJECT_SECTIONS))
    log_op(root, "project-append", name, section, before, bullet)
    return "project-append", path


def replace_line(root, name, section, old_sub, new_text):
    """Correction: replace first bullet containing old_sub; keep struck history."""
    canon = dict(PROJECT_SECTIONS)
    if section not in canon:
        return "rejected", f"unknown section: {section}"
    path = project_file(root, name)
    if not os.path.exists(path):
        return "rejected", "project file does not exist (init it explicitly)"
    hit = find_secret(new_text) or find_secret(old_sub)
    if hit:
        return "rejected", f"secrets-guard matched: {hit}"
    with open(path, encoding="utf-8") as f:
        before = f.read()
    model = parse_file(before)
    bullet = "- " + " ".join((new_text or "").split())
    day = utcnow()[:10]
    for h, body in model["sections"]:
        if h != canon[section]:
            continue
        for i, line in enumerate(body):
            if old_sub and old_sub in line:
                struck = f"- [superseded {day}] {line.strip().lstrip('- ').strip()}"
                body[i] = bullet
                body.append(struck)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(serialize_file(model, PROJECT_SECTIONS))
                log_op(root, "project-replace", name, section, before, bullet)
                return "project-replace", path
    return "rejected", "no matching line for replace"


def log_op(root, kind, name, section, before_text, detail):
    entry = {"kind": kind, "entry_id": f"project:{name}:{section}",
             "before": {"file": project_file(root, name), "text": before_text},
             "after": {"detail": (detail or "")[:300]}}
    from . import store as store_mod
    P = store_mod.data_paths(root)
    return store_mod.append_log(P["log"], entry)


# ---------- sessions: activate + assemble ----------

def _mapping_path(root):
    return os.path.join(_data(root), SESSIONS_FILE)


def activate(root, bid, name_or_path):
    reg = list_projects(root)
    if name_or_path in reg:
        name = name_or_path
    else:
        name = detect_from_path(root, name_or_path)
        if not name:
            raise ValueError(f"unknown project and no registry match: {name_or_path}")
    m = _load(_mapping_path(root), {})
    m[bid] = name
    _save(_mapping_path(root), m)
    # Backfill for projects registered before auto-init existed (or whose
    # file was deleted): best-effort only, so chat is never blocked when
    # the project folder is not writable.
    try:
        init_project(root, name)
    except OSError:
        pass
    return name


def active_for(root, bid):
    return _load(_mapping_path(root), {}).get(bid)


def clear_active(root, bid):
    m = _load(_mapping_path(root), {})
    m.pop(bid, None)
    _save(_mapping_path(root), m)


def clear_mapping(root):
    try:
        os.remove(_mapping_path(root))
    except OSError:
        pass


def clear_registry(root):
    """Forget registered projects (external .continuum.md files untouched)."""
    try:
        os.remove(os.path.join(_data(root), REGISTRY_FILE))
    except OSError:
        pass


def clear_workspace(root):
    try:
        os.remove(os.path.join(_data(root), WORKSPACE_FILE))
    except OSError:
        pass


def assemble(root, bid):
    """Project block for the active project only. '' when none. The ONLY
    project content ever injected; unrelated projects never appear."""
    name = active_for(root, bid)
    if not name:
        return ""
    reg = list_projects(root)
    if name not in reg:
        return ""
    try:
        text = read_cached(root, bid, project_file(root, name))
    except OSError:
        return ""
    if len(text) > ASSEMBLE_CAP:
        text = text[:ASSEMBLE_CAP] + "\n[... truncated]"
    lines = [f"<project-context>", f"Project: {name}", f"Root: {reg[name]}"]
    ws = get_workspace(root)
    if ws:
        lines.append(f"Workspace: {ws}")
        try:
            wtext = read_cached(root, bid, workspace_file(root))
            wmodel = parse_file(wtext)
            for h, body in wmodel["sections"]:
                if h == "## Projects":
                    lines.append("Workspace projects:")
                    lines.extend(l for l in body if l.strip())
        except OSError:
            pass
    lines.append(AUTHORING_PROTOCOL.format(name=name))
    lines += ["---", text.rstrip(), "</project-context>"]
    return "\n".join(lines)


# ---------- Muse-driven lifecycle: create / register / activate ----------

MAX_PROJECT_OPS = 2
PROJECT_BLOCK_RE = re.compile(r"```project-ops\s*\n(.*?)```", re.S)

PROJECT_OPS_HELP = (
    "Continuum project operations (machine-read): your workspace covers the user's "
    "files, so do ordinary file work - including creating project folders - with your NATIVE "
    "file tools and approvals, exactly as in a terminal. NEVER refuse a write inside your "
    "workspace. Continuum tracks projects separately: if the user explicitly asks to register "
    "an existing folder as a project or switch the active project, append a ```project-ops "
    "fenced JSON list at the very end of your reply (after any memory-ops block). "
    "Kinds: {\"kind\":\"register_project\",\"name\",\"path\":<absolute-path>} | "
    "{\"kind\":\"activate_project\",\"name\"} | {\"kind\":\"create_project\",\"name\","
    "\"parent\":\"documents|workspace|<absolute-path>\",\"activate\":true}. "
    "Rules: prefer native creation, then register/switch via project-ops; NEVER place user "
    "projects inside the Continuum app repo unless the user explicitly names it "
    "(then set \"allow_repo_child\":true); results apply after your turn and the next turn "
    "shows the active project. create_project adopts the folder when it already exists (register+init+activate instead of an error).")


def parse_project_block(text):
    """Deterministic parse of Muse's ```project-ops block. No LLM."""
    if not text:
        return []
    try:
        m = PROJECT_BLOCK_RE.search(text)
    except Exception:
        return []
    if not m:
        return []
    try:
        ops = json.loads(m.group(1).strip())
        return ops if isinstance(ops, list) else []
    except Exception:
        return []


def documents_folder():
    """Actual Documents known folder (OneDrive-aware on Windows); None if none."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

            guid = GUID()
            ctypes.windll.ole32.CLSIDFromString(
                "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", ctypes.byref(guid))
            buf = wintypes.LPWSTR()
            hr = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(buf))
            path = buf.value if hr == 0 else None
            try:
                ctypes.windll.ole32.CoTaskMemFree(buf)
            except Exception:
                pass
            if path and os.path.isdir(path):
                return path
        except Exception:
            pass
    fb = os.path.join(os.path.expanduser("~"), "Documents")
    return fb if os.path.isdir(fb) else None


def resolve_parent(root, parent):
    """Safe parent resolution: aliases or existing absolute dir. Never guessed."""
    p = (parent or "").strip()
    low = p.lower()
    if low == "documents":
        d = documents_folder()
        if not d:
            raise ValueError("Documents folder is not available")
        return d
    if low == "workspace":
        ws = get_workspace(root)
        if not ws or not os.path.isdir(ws):
            raise ValueError("no workspace selected")
        return ws
    if not os.path.isabs(p):
        raise ValueError(f"parent must be documents, workspace, or an absolute path: {parent!r}")
    ap = os.path.abspath(p)
    if not os.path.isdir(ap):
        raise ValueError(f"parent is not a directory: {parent!r}")
    return ap


def _inside(child, parent):
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(child)),
                                   os.path.normcase(os.path.abspath(parent))]) == \
            os.path.normcase(os.path.abspath(parent))
    except ValueError:
        return False


def create_project(root, name, parent, allow_repo_child=False):
    """Backend-owned creation: mkdir + register + init. Validated; no guessing."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name or ""):
        raise ValueError(f"bad project name: {name!r}")
    base = resolve_parent(root, parent)
    dest = os.path.join(base, name)
    if _inside(dest, root) and not allow_repo_child:
        raise ValueError("refusing to create a user project inside the Continuum app repo")
    if os.path.lexists(dest):
        # Adopt, don't fight, native creation: Muse usually makes the
        # folder with its own tools first, then emits create_project.
        if not os.path.isdir(dest):
            raise ValueError(
                f"destination exists and is not a directory: {dest}")
        if _inside(dest, root) and not allow_repo_child:
            raise ValueError("refusing to adopt a user project inside "
                             "the Continuum app repo")
        reg = list_projects(root)
        if name in reg and os.path.normcase(reg[name]) != os.path.normcase(dest):
            raise ValueError(
                f"project name already registered elsewhere: {name}")
        register_project(root, name, dest)
        init_project(root, name)
        return {"name": name, "path": dest, "adopted": True}
    reg = list_projects(root)
    if name in reg and os.path.normcase(reg[name]) != os.path.normcase(dest):
        raise ValueError(f"project name already registered elsewhere: {name}")
    try:
        os.mkdir(dest)
    except FileExistsError:
        raise ValueError(f"destination already exists: {dest}")
    register_project(root, name, dest)
    init_project(root, name)
    return {"name": name, "path": dest}


def apply_project_ops(root, ops, bid):
    """Validate + execute one turn's project ops. Results are UI/next-turn data."""
    out = []
    if not isinstance(ops, list):
        return out
    for op in ops[:MAX_PROJECT_OPS]:
        if not isinstance(op, dict):
            out.append({"kind": "rejected", "reason": "op is not an object"})
            continue
        kind = (op.get("kind") or "").lower()
        try:
            if kind == "create_project":
                r = create_project(root, op.get("name"), op.get("parent"),
                                   allow_repo_child=bool(op.get("allow_repo_child")))
                want_active = bool(op.get("activate", True))
                if want_active:
                    activate(root, bid, r["name"])
                r = dict(r)
                r["kind"] = ("project-registered" if r.get("adopted")
                             else "project-created")
                r["activated"] = want_active and active_for(root, bid) == r["name"]
                out.append(r)
            elif kind == "register_project":
                ap = register_project(root, op.get("name"), op.get("path"))
                init_project(root, op.get("name"))
                d = {"kind": "project-registered", "name": op.get("name"), "path": ap}
                if op.get("activate"):
                    activate(root, bid, op.get("name"))
                    d["activated"] = True
                out.append(d)
            elif kind == "activate_project":
                n = activate(root, bid, op.get("name"))
                out.append({"kind": "project-activated", "name": n,
                            "path": list_projects(root)[n], "activated": True})
            elif kind in ("noop", ""):
                continue
            else:
                out.append({"kind": "rejected", "reason": f"unknown project op: {kind}"})
        except (ValueError, OSError) as e:
            out.append({"kind": "rejected", "op": kind, "reason": str(e)[:300]})
    return out


CREATE_PATTERNS = [
    re.compile(r"\bcreate\s+(?:a\s+)?(?:new\s+)?project\s+(?:called|named)\s+"
               r"([A-Za-z0-9_-]{1,64})\s+in\s+([^\s\"']+)", re.I),
    re.compile(r"\bcrea[r]?\s+(?:un\s+)?(?:nuevo\s+)?proyecto\s+"
               r"(?:llamado|llamada|de nombre)\s+([A-Za-z0-9_-]{1,64})"
               r"\s+en\s+(?:mis\s+)?([^\s\"']+)", re.I),
]
CREATE_NONAME_LOC_PATTERNS = [
    re.compile(r"^\s*(?:please\s+)?create\s+(?:a\s+)?(?:new\s+)?project\s+(?:called|named)\s+"
               r"([A-Za-z0-9_-]{1,64})\s*[.?!]?\s*$", re.I),
    re.compile(r"^\s*(?:por\s+favor,?\s+)?crea[r]?\s+(?:un\s+)?(?:nuevo\s+)?proyecto\s+"
               r"(?:llamado|llamada|de nombre)\s+([A-Za-z0-9_-]{1,64})"
               r"\s*[.?!]?\s*$", re.I),
]
SWITCH_PATTERN = re.compile(
    r"\bswitch\s+(?:back\s+)?to\s+(?:the\s+)?([A-Za-z0-9_-]{1,64})\s+project\b", re.I)
SWITCH_PATTERN_ES = re.compile(
    r"\b(?:cambia\s+al|vuelve\s+al|volver\s+al)\s+proyecto\s+([A-Za-z0-9_-]{1,64})\b", re.I)


def detect_project_request(message):
    """Narrow detector for EXPLICIT project lifecycle requests.

    Strict full-shape patterns only; returns an op list (0-1 ops), never
    guesses. Switch ops may run pre-turn (pure session routing, no
    filesystem effect). Create shapes are BOOKKEEPING ONLY (see
    bookkeeping_ops): Muse creates folders with native tools first, and
    Continuum only registers/activates what actually exists afterwards.
    """
    msg = message or ""
    for pat in CREATE_PATTERNS:
        m = pat.search(msg)
        if m:
            loc = m.group(2).strip().rstrip(".,?!;:").strip()
            low = loc.lower()
            if low in ("documents", "documentos", "documento",
                       "my-documents", "mydocuments", "mis-documentos"):
                parent = "documents"
            elif low == "workspace":
                parent = "workspace"
            elif os.path.isabs(loc) and os.path.isdir(loc):
                parent = loc
            else:
                # Fails validation downstream with a clear reason (no guessing).
                return [{"kind": "create_project", "name": m.group(1),
                         "parent": loc, "activate": True}]
            return [{"kind": "create_project", "name": m.group(1),
                     "parent": parent, "activate": True}]
    for pat in CREATE_NONAME_LOC_PATTERNS:
        m = pat.search(msg)
        if m:
            # Explicit name, no location: bookkeeping may only adopt a
            # directory that observably appeared during this turn.
            return [{"kind": "create_project", "name": m.group(1),
                     "parent": None, "activate": True}]
    m = SWITCH_PATTERN.search(msg)
    if m:
        return [{"kind": "activate_project", "name": m.group(1)}]
    m = SWITCH_PATTERN_ES.search(msg)
    if m:
        return [{"kind": "activate_project", "name": m.group(1)}]
    return []


PROJECT_FENCE_OPEN = "```project-ops"


def strip_project_block(text):
    """Split (clean_text, first_block_or_None) for ```project-ops.

    Mirrors extract.split_ops_block semantics: unclosed fence keeps the
    content (content wins); only the first block is machine protocol,
    further complete echoes are display noise and also withheld.
    """
    if not text:
        return "", None
    try:
        m = PROJECT_BLOCK_RE.search(text)
    except Exception:
        return text, None
    if not m:
        return text, None
    clean = text[:m.start()].rstrip() + text[m.end():]
    block = text[m.start():m.end()]
    try:
        rest = PROJECT_BLOCK_RE.sub("", clean)
    except Exception:
        return clean, block
    if rest != clean:
        return rest.rstrip(), block
    return clean, block


def snapshot_dirs(root):
    """Basenames of directories in the canonical project locations."""
    out = {}
    for base in (documents_folder(), get_workspace(root)):
        if not base or not os.path.isdir(base):
            continue
        try:
            names = os.listdir(base)
        except OSError:
            continue
        out[base] = {n for n in names
                     if os.path.isdir(os.path.join(base, n))}
    return out


def diff_new_dirs(before, root):
    """{basename: fullpath} for directories that appeared since snapshot."""
    before = before or {}
    new = {}
    for base in (documents_folder(), get_workspace(root)):
        if not base or not os.path.isdir(base):
            continue
        try:
            cur = {n for n in os.listdir(base)
                   if os.path.isdir(os.path.join(base, n))}
        except OSError:
            continue
        for name in sorted(cur - set(before.get(base, set()))):
            new.setdefault(name, os.path.join(base, name))
    return new


def bookkeeping_ops(root, bid, message, new_dirs=None):
    """Post-turn Continuum bookkeeping for explicit create requests.

    Never creates folders and never guesses paths: registers what Muse
    observably created. With an explicit location, the destination must
    exist; without one (parent None), only a directory that appeared
    during this turn (new_dirs, exact name match) is adopted. Already
    -active projects are left alone. Validation in the normal op path.
    """
    for op in detect_project_request(message or ""):
        if not isinstance(op, dict) or (op.get("kind") or "").lower() != "create_project":
            continue
        name = op.get("name")
        if not name:
            return []
        parent = op.get("parent")
        dest = None
        if parent:
            try:
                dest = os.path.join(resolve_parent(root, parent), name)
            except ValueError:
                dest = None
            if not dest or not os.path.isdir(dest):
                dest = (new_dirs or {}).get(name)  # Muse put it elsewhere
        else:
            dest = (new_dirs or {}).get(name)
        if not dest or not os.path.isdir(dest):
            return []
        reg = list_projects(root)
        if active_for(root, bid) == name and os.path.normcase(reg.get(name, "")) == os.path.normcase(dest):
            return []
        return [{"kind": "register_project", "name": name, "path": dest, "activate": True}]
    return []


def project_ops_help(root, bid):
    """Dynamic per-turn block: state Muse needs for correct project actions."""
    reg = list_projects(root)
    active = active_for(root, bid)
    lines = ["<project-ops>", PROJECT_OPS_HELP]
    if active and active in reg:
        lines.append(f"Active project: {active} at {reg[active]}")
        lines.append(f"Do this turn's project file work inside: {reg[active]}")
    else:
        lines.append("Active project: none")
    lines.append(f"Continuum app repo (never a user workspace): {os.path.abspath(root)}")
    docs = documents_folder()
    lines.append(f"Documents: {docs or 'unavailable'}")
    ws = get_workspace(root)
    lines.append(f"Workspace root: {ws or 'none selected'}")
    if reg:
        lines.append("Registered: " + ", ".join(f"{n} ({p})" for n, p in sorted(reg.items())))
    lines.append("</project-ops>")
    return "\n".join(lines)
