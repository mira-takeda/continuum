"""Runtime install flow: explicit approval, platform-correct command, and no
bash heuristic on Windows. Never reinstalls anything (all branches mocked
or short-circuited). Run:
  python evals/test_runtime_install.py
"""
import io
import json
import os
import sys
import unittest
from email.message import Message
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server as server_mod
from memory import msp as msp_mod


class FakeHandler(server_mod.H):
    def __init__(self, method, path, body=None):
        self.command = method
        self.path = path
        raw = json.dumps(body or {}).encode("utf-8") if body is not None else b""
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

    def json_body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class InstallFlowTest(unittest.TestCase):
    def post_install(self, body):
        h = FakeHandler("POST", "/api/runtime/install", body)
        h.do_POST()
        return h

    def test_requires_explicit_approval(self):
        h = self.post_install({})
        self.assertEqual(h.code, 403)
        self.assertIn("explicit approval", h.json_body()["error"])
        self.assertIn("install_cmd", h.json_body())

    def test_install_cmd_is_platform_correct(self):
        with mock.patch.object(os, "name", "nt"):
            self.assertEqual(server_mod.install_command(),
                             msp_mod.OFFICIAL_INSTALL_PS1)
        with mock.patch.object(os, "name", "posix"):
            self.assertEqual(server_mod.install_command(),
                             msp_mod.OFFICIAL_INSTALL_SH)

    def test_already_installed_short_circuits(self):
        with mock.patch.object(server_mod, "find_muse",
                               return_value="muse-fake"), \
             mock.patch.object(server_mod, "muse_version",
                               return_value="muse-test"), \
             mock.patch.object(server_mod.subprocess, "run") as run:
            h = self.post_install({"approve": True})
        self.assertEqual(h.code, 200)
        self.assertTrue(h.json_body()["already"])
        run.assert_not_called()

    def test_windows_returns_copyable_command_without_executing(self):
        with mock.patch.object(os, "name", "nt"), \
             mock.patch.object(server_mod, "find_muse", return_value=None), \
             mock.patch.object(server_mod.subprocess, "run") as run:
            h = self.post_install({"approve": True})
        self.assertEqual(h.code, 200)
        body = h.json_body()
        self.assertFalse(body["ok"])
        self.assertIn("PowerShell", body["output"])
        self.assertIn("install.ps1", body["output"])
        self.assertEqual(body["install_cmd"], msp_mod.OFFICIAL_INSTALL_PS1)
        run.assert_not_called()

    def test_posix_executes_sh_installer(self):
        proc = mock.Mock(returncode=0, stdout="done", stderr="")
        with mock.patch.object(os, "name", "posix"), \
             mock.patch.object(server_mod, "find_muse", return_value=None), \
             mock.patch.object(server_mod.subprocess, "run",
                               return_value=proc) as run:
            h = self.post_install({"approve": True})
        self.assertEqual(h.code, 200)
        self.assertTrue(h.json_body()["ok"])
        run.assert_called_once()
        args, _ = run.call_args
        self.assertIn("install.sh", args[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
