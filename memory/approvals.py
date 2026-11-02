"""Human-readable approval explanations. Local + deterministic, no LLM.

Presentation only: the translator NEVER rewrites the action - the UI always
shows Muse's exact original command alongside. Unknown or risky-to-paraphrase
actions fall back to an explicit low-confidence notice. Stdlib only; the
top-level entry never raises and never touches the network.

The command translator is PowerShell-oriented (cmdlets + switches with `-`).
POSIX-style paths are treated as data, never as switches; Windows-style paths
are basename-normalized on every platform. Anything unrecognized falls back
to an explicit low-confidence notice instead of a wrong explanation.
"""
import os
import re

FALLBACK_TITLE = "Technical operation"
FALLBACK_EFFECT = ("Continuum can't explain this action with enough confidence. "
                   "Review the original command before allowing it.")
READ_ONLY = "Read-only. Nothing will change."

_PWSH_ALIASES = {
    "ls": "get-childitem", "dir": "get-childitem", "gci": "get-childitem",
    "cat": "get-content", "type": "get-content", "gc": "get-content",
    "mkdir": "new-item", "md": "new-item",
    "move": "move-item", "mv": "move-item", "mi": "move-item",
    "copy": "copy-item", "cp": "copy-item", "cpi": "copy-item",
    "ren": "rename-item", "rni": "rename-item",
    "del": "remove-item", "erase": "remove-item", "rd": "remove-item",
    "rm": "remove-item", "rmdir": "remove-item", "ri": "remove-item",
    "sc": "set-content", "ac": "add-content",
}

_GIT_READONLY = {"status": "Show working-tree status", "diff": "Show uncommitted changes",
                 "log": "Show commit history", "show": "Show a commit",
                 "branch": "List branches", "remote": "List remotes"}
_GIT_WRITE = {"clone": "Download a repository", "pull": "Download and apply updates",
              "fetch": "Download updates", "checkout": "Switch branches or files",
              "switch": "Switch branches", "add": "Stage changes",
              "commit": "Record a commit", "push": "Upload commits",
              "merge": "Merge branches", "rebase": "Rewrite commit history",
              "stash": "Shelve changes", "reset": "Reset changes", "tag": "Create a tag"}


def _fallback(lines=None):
    return {"title": FALLBACK_TITLE, "objects": [], "lines": list(lines or []),
            "effects": [FALLBACK_EFFECT], "fallback": True}


def _home():
    try:
        h = os.path.abspath(os.path.expanduser("~"))
        return h if os.path.isdir(h) else None
    except Exception:
        return None


def _shorten(path):
    """Home-relative with forward slashes when under home, else unchanged."""
    if not path:
        return ""
    p = str(path)
    home = _home()
    try:
        ap = os.path.abspath(p)
        if home and (os.path.normcase(ap) == os.path.normcase(home) or
                     ap.startswith(home + os.sep) or
                     os.path.normcase(ap).startswith(os.path.normcase(home) + os.sep)):
            return os.path.relpath(ap, home).replace(os.sep, "/")
    except Exception:
        pass
    return p


def _segments(path):
    """Separator-agnostic path segments (both \\ and /), drive stripped."""
    s = str(path or "").strip().rstrip("\\/")
    if re.match(r"^[A-Za-z]:", s):
        s = s[2:]
    return [g for g in re.split(r"[\\\\/]+", s) if g not in ("", ".")]


def _base(path):
    segs = _segments(path)
    return segs[-1] if segs else str(path or "")


def _parent_display(path):
    segs = _segments(path)
    if len(segs) > 1:
        return "/".join(segs[:-1])
    return str(path or "")


def _noun(path):
    stem, ext = os.path.splitext(_base(path))
    if ext:
        return "file"
    try:
        if path and os.path.isdir(os.path.abspath(path)):
            return "folder"
    except Exception:
        pass
    return "item"


def _exists(path):
    try:
        return os.path.lexists(path)
    except Exception:
        return False


def _strip_quoted_spans(text):
    """Replace '...' and \"...\" regions with spaces (PowerShell '' escape)."""
    out, i, n = [], 0, len(text or "")
    while i < n:
        c = text[i]
        if c in ("'", '"'):
            j = i + 1
            while j < n:
                if text[j] == c:
                    if c == "'" and j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(" " * (min(j, n - 1) - i + 1))
            i = min(j, n - 1) + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


_UNSAFE_OUTSIDE_QUOTES = set("$`;|&<>(){}[],=@#~")


def _tokenize(text):
    """Whitespace split honoring single/double quotes. None when unsafe."""
    if not text or not text.strip():
        return None
    if "*" in text or "?" in text:
        return None  # wildcards: decline to characterize the set
    bare = _strip_quoted_spans(text)
    if any(c in _UNSAFE_OUTSIDE_QUOTES for c in bare):
        return None
    toks, cur, quote, i, n = [], [], None, 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == quote:
                if quote == "'" and i + 1 < n and text[i + 1] == "'":
                    cur.append("'")
                    i += 2
                    continue
                quote = None
            else:
                cur.append(c)
        elif c in ("'", '"'):
            quote = c
        elif c in (" ", "\t"):
            if cur:
                toks.append("".join(cur))
                cur = []
        else:
            cur.append(c)
        i += 1
    if quote:
        return None  # unbalanced quote
    if cur:
        toks.append("".join(cur))
    return toks or None


def _canon_cmd(token):
    base = re.split(r"[\\/]", token or "")[-1]
    base = re.sub(r"\.(exe|cmd|bat|ps1|py)$", "", base, flags=re.I)
    low = base.lower()
    return _PWSH_ALIASES.get(low, low)


def _split_args(toks):
    """(named dict, positionals) from tokens after the command.

    Only `-` starts a switch (PowerShell canonical form). A token starting
    with `/` is a POSIX path, never a switch.
    """
    named, pos, i = {}, [], 1
    while i < len(toks):
        t = toks[i]
        if t[:1] == "-" and len(t) > 1:
            if ":" in t:
                k, v = t.lstrip("-").split(":", 1)
                named[k.lower()] = v
            elif i + 1 < len(toks) and toks[i + 1][:1] != "-":
                named[t.lstrip("-").lower()] = toks[i + 1]
                i += 1
            else:
                named[t.lstrip("-").lower()] = ""
        else:
            pos.append(t)
        i += 1
    return named, pos


def _arg(named, pos, *names, default=""):
    for k in names:
        if k in named and named[k] != "":
            return named[k]
    for p in pos:
        if p != "":
            return p
    return default


def _explain_file_access(subject):
    access = (subject.get("access") or "").lower()
    path = subject.get("path") or ""
    if not path or access not in ("read", "write"):
        return None
    noun, name = _noun(path), _base(path)
    if access == "read":
        return {"title": f"Read a {noun}", "objects": [name],
                "lines": [f"Path: {_shorten(path)}"], "effects": [READ_ONLY],
                "fallback": False}
    if _exists(path):
        return {"title": f"Edit a {noun}", "objects": [name],
                "lines": [f"Path: {_shorten(path)}"],
                "effects": [f"Modifies {name}. Previous content will change."],
                "fallback": False}
    return {"title": f"Create a {noun}", "objects": [name],
            "lines": [f"Path: {_shorten(path)}"], "effects": [f"Creates {name}."],
            "fallback": False}


def _explain_pwsh(cmd, toks):
    named, pos = _split_args(toks)
    if cmd == "get-childitem":
        p = _arg(named, pos, "path", default="")
        lines = [f"Path: {_shorten(p)}"] if p else []
        return {"title": "List files", "objects": ([_base(p)] if p else []),
                "lines": lines, "effects": [READ_ONLY], "fallback": False}
    if cmd == "get-content":
        p = _arg(named, pos, "path")
        if not p:
            return None
        return {"title": f"Read a {_noun(p)}", "objects": [_base(p)],
                "lines": [f"Path: {_shorten(p)}"], "effects": [READ_ONLY],
                "fallback": False}
    if cmd == "test-path":
        p = _arg(named, pos, "path")
        if not p:
            return None
        return {"title": "Check whether a path exists", "objects": [_base(p)],
                "lines": [f"Path: {_shorten(p)}"], "effects": [READ_ONLY],
                "fallback": False}
    if cmd == "new-item":
        first = (toks[0] or "").lower()
        itype = (named.get("itemtype") or "").lower()
        if first in ("mkdir", "md"):
            is_dir = True
        elif itype.startswith("dir"):
            is_dir = True
        elif itype.startswith("file"):
            is_dir = False
        else:
            return None
        p = _arg(named, pos, "path")
        if not p:
            return None
        noun = "folder" if is_dir else "file"
        return {"title": f"Create a {noun}", "objects": [_base(p)],
                "lines": [f"Path: {_shorten(p)}"], "effects": [f"Creates {_base(p)}."],
                "fallback": False}
    if cmd in ("move-item", "copy-item"):
        src = _arg(named, pos, "path")
        dst = named.get("destination") or (pos[1] if len(pos) > 1 else "")
        if not src or not dst:
            return None
        noun, name = _noun(src), _base(src)
        verb = "Move" if cmd == "move-item" else "Copy"
        effects = ([f"The {noun} will change location. It will not be deleted."]
                   if cmd == "move-item"
                   else [f"Copies {name}. The original will stay in place."])
        return {"title": f"{verb} a {noun}", "objects": [name],
                "lines": [f"From: {_shorten(_parent_display(src))}",
                          f"To: {_shorten(dst)}"], "effects": effects,
                "fallback": False}
    if cmd == "rename-item":
        src = _arg(named, pos, "path")
        new = named.get("newname") or (pos[1] if len(pos) > 1 else "")
        if not src or not new:
            return None
        return {"title": f"Rename a {_noun(src)}", "objects": [_base(src)],
                "lines": [f"From: {_base(src)}", f"To: {_base(new)}"],
                "effects": [f"Renames {_base(src)} to {_base(new)}."], "fallback": False}
    if cmd == "remove-item":
        p = _arg(named, pos, "path")
        if not p:
            return None
        return {"title": f"Delete a {_noun(p)}", "objects": [_base(p)],
                "lines": [f"Path: {_shorten(p)}"], "effects": [f"Deletes {_base(p)}."],
                "fallback": False}
    if cmd in ("set-content", "add-content"):
        p = _arg(named, pos, "path")
        if not p:
            return None
        name = _base(p)
        if _exists(p):
            verb = "Edit" if cmd == "set-content" else "Append to"
            return {"title": f"{verb} a {_noun(p)}", "objects": [name],
                    "lines": [f"Path: {_shorten(p)}"],
                    "effects": [f"Modifies {name}. Previous content will change."],
                    "fallback": False}
        return {"title": f"Create a {_noun(p)}", "objects": [name],
                "lines": [f"Path: {_shorten(p)}"], "effects": [f"Creates {name}."],
                "fallback": False}
    return None


def _explain_git(toks):
    if len(toks) < 2:
        return None
    sub = toks[1].lower()
    if sub in _GIT_READONLY:
        return {"title": _GIT_READONLY[sub], "objects": [], "lines": [],
                "effects": [READ_ONLY], "fallback": False}
    if sub in _GIT_WRITE:
        lines = []
        if sub == "clone" and len(toks) > 2:
            lines = [f"From: {toks[2]}"]
        verb_effects = {"clone": "Downloads files onto this computer.",
                        "push": "Sends commits to the remote repository."}
        return {"title": _GIT_WRITE[sub], "objects": [], "lines": lines,
                "effects": [verb_effects.get(sub, "Changes repository state.")],
                "fallback": False}
    return None


def _explain_python(toks):
    if len(toks) > 1 and toks[1].lower().endswith(".py"):
        return {"title": "Run a Python script", "objects": [_base(toks[1])],
                "lines": [f"Script: {_shorten(toks[1])}"],
                "effects": ["Executes code on this computer."], "fallback": False}
    return None


def _explain_package_mgr(toks):
    if len(toks) < 2:
        return None
    action, rest = toks[1].lower(), [t for t in toks[2:] if not t.startswith("-")]
    if action == "install" and rest:
        return {"title": f"Install package {rest[0]}", "objects": [rest[0]],
                "lines": [], "effects": [f"Downloads and installs {rest[0]}."],
                "fallback": False}
    if action == "uninstall" and rest:
        return {"title": f"Remove package {rest[0]}", "objects": [rest[0]],
                "lines": [], "effects": [f"Removes {rest[0]}."], "fallback": False}
    if toks[0].lower().startswith("npm") and action in ("run", "test"):
        what = rest[0] if rest else "default script"
        return {"title": "Run an npm script", "objects": [what], "lines": [],
                "effects": ["Executes code on this computer."], "fallback": False}
    return None


def _explain_one_command(command):
    toks = _tokenize(command)
    if not toks:
        return None
    cmd = _canon_cmd(toks[0])
    for handler in (_explain_pwsh,):
        r = handler(cmd, toks)
        if r:
            return r
    if cmd == "git":
        return _explain_git(toks)
    if cmd in ("python", "python3", "py"):
        return _explain_python(toks)
    if cmd in ("pip", "pip3", "npm"):
        return _explain_package_mgr(toks)
    return None


def _stage_text(stage):
    if isinstance(stage, str):
        return stage
    if isinstance(stage, dict):
        for k in ("command", "text", "argv"):
            v = stage.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, list) and v:
                return " ".join(str(x) for x in v)
    return ""


def _explain_shell(subject):
    stages = subject.get("stages") or []
    if len(stages) > 1:
        parts = [_explain_one_command(_stage_text(s)) for s in stages]
        if not all(parts):
            return None
        lines = [f"{i + 1}. {p['title']}" + (f" — {', '.join(p['objects'])}" if p["objects"] else "")
                 for i, p in enumerate(parts)]
        effects = []
        for p in parts:
            for e in p["effects"]:
                if e not in effects:
                    effects.append(e)
        return {"title": f"Run {len(parts)} commands", "objects": [],
                "lines": lines, "effects": effects, "fallback": False}
    text = _stage_text(stages[0]) if stages else (subject.get("command") or "")
    return _explain_one_command(text)


def _explain(frame):
    subject = (frame or {}).get("subject") or {}
    kind = (subject.get("kind") or "").lower()
    if kind == "fileaccess":
        return _explain_file_access(subject) or _fallback()
    if kind == "shell":
        return _explain_shell(subject) or _fallback()
    if kind == "network" and subject.get("host"):
        return {"title": "Contact a server", "objects": [str(subject["host"])],
                "lines": [f"Server: {subject['host']}" +
                          (f":{subject['port']}" if subject.get("port") else "")],
                "effects": ["Sends data over the network."], "fallback": False}
    tool = (frame or {}).get("toolName") or subject.get("toolName") or ""
    if kind == "process" and (subject.get("target") or tool):
        return {"title": "Control a process",
                "objects": [str(subject.get("target") or tool)],
                "lines": [], "effects": ["Acts on a running process outside Muse."],
                "fallback": False}
    lines = [f"Tool: {tool}"] if tool else []
    return _fallback(lines)


def explain_approval(frame):
    """Explain one approval frame. Total: never raises, never blocks on IO
    beyond a stat, never touches the network."""
    try:
        out = _explain(frame or {})
    except Exception:
        return _fallback()
    if not isinstance(out, dict) or not out.get("title"):
        return _fallback()
    out.setdefault("objects", [])
    out.setdefault("lines", [])
    out.setdefault("effects", [FALLBACK_EFFECT])
    out.setdefault("fallback", True)
    return out


def fallback():
    return _fallback()
