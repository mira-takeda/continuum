"""Fix 3: reset clears all Continuum-owned local state but never external
.continuum.md project files. Run:
  python evals/test_reset.py
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from memory import projects as projects_mod
from memory import store as store_mod
import server as server_mod

EXTERNAL_MD = "# Project A\n\ndecisions:\n- use cedar instead of balsa\n"


def seed_state(tmp):
    data = os.path.join(tmp, "data")
    ent = os.path.join(data, "entries")
    os.makedirs(ent, exist_ok=True)
    P = store_mod.data_paths(tmp)
    store_mod.create(ent, P["log"], "Seeded memory", ["fact"], "seeded body",
                     ["t1"], "conversation", "probe")
    with open(P["usage"], "w") as f:
        f.write('{"probe": 1}\n')
    with open(os.path.join(data, "delivery.json"), "w") as f:
        f.write('{"run_id": "probe", "sids": {"s1": {}}}\n')
    server_mod.save_json(os.path.join(data, "sessions.json"), {"demo": {"a": 1}})
    server_mod.save_json(os.path.join(data, "transcripts.json"),
                         {"demo": [{"user": "hi", "muse": "hello"}]})
    server_mod.save_json(os.path.join(data, "msp_sessions.json"),
                         {"demo": {"sid": "sid-fake"}})
    att = os.path.join(data, "attachments", "demo")
    os.makedirs(att, exist_ok=True)
    with open(os.path.join(att, "f.bin"), "wb") as f:
        f.write(b"staged-bytes")
    proj = os.path.join(tmp, "projA")
    os.makedirs(proj, exist_ok=True)
    with open(os.path.join(proj, ".continuum.md"), "w", encoding="utf-8") as f:
        f.write(EXTERNAL_MD)
    projects_mod.register_project(tmp, "projA", proj)
    projects_mod.set_workspace(tmp, tmp)
    projects_mod.activate(tmp, "demo", "projA")
    return data, proj


class ResetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="reset-")
        cls.patched = {}
        for name, fname in (("SESSIONS", "sessions.json"),
                            ("TRANSCRIPTS", "transcripts.json"),
                            ("MSP_SESSIONS", "msp_sessions.json")):
            cls.patched[name] = getattr(server_mod, name)
            setattr(server_mod, name, os.path.join(cls.tmp, "data", fname))
        cls.old_root = server_mod.ROOT
        cls.old_log = server_mod.H.log_message
        server_mod.ROOT = cls.tmp
        server_mod.H.log_message = lambda *a, **k: None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.H)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        server_mod.ROOT = cls.old_root
        server_mod.H.log_message = cls.old_log
        for name, val in cls.patched.items():
            setattr(server_mod, name, val)

    def post_reset(self, obj):
        url = f"http://127.0.0.1:{self.port}/api/reset"
        req = urllib.request.Request(
            url, data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     server_mod.TOKEN_HEADER: server_mod.API_TOKEN})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def test_reset_wipes_owned_state_keeps_external(self):
        data, proj = seed_state(self.tmp)
        code, body = self.post_reset({})
        self.assertEqual(code, 200)
        self.assertTrue(body.get("ok"))
        ent = os.path.join(data, "entries")
        self.assertEqual(store_mod.list_ids(ent), [])
        with open(os.path.join(data, "index.json")) as f:
            self.assertEqual(json.load(f)["entries"], {})
        with open(os.path.join(data, "core.json")) as f:
            self.assertEqual(json.load(f)["entry_ids"], [])
        for gone in ("log.jsonl", "usage.jsonl", "delivery.json", "sessions.json",
                     "transcripts.json",
                     "projects.json", "workspace.json",
                     "project_sessions.json"):
            self.assertFalse(os.path.exists(os.path.join(data, gone)), gone)
        with open(os.path.join(data, "msp_sessions.json")) as f:
            self.assertEqual(json.load(f), {})
        self.assertFalse(os.path.isdir(os.path.join(data, "attachments")))
        self.assertEqual(projects_mod.list_projects(self.tmp), {})
        with open(os.path.join(proj, ".continuum.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), EXTERNAL_MD)

    def test_reset_sample_mode_still_clears_transcripts(self):
        data, _ = seed_state(self.tmp)
        code, body = self.post_reset({"mode": "sample"})
        self.assertEqual(code, 200)
        self.assertTrue(body.get("ok"))
        self.assertGreater(len(store_mod.list_ids(
            os.path.join(data, "entries"))), 0)
        self.assertFalse(os.path.exists(os.path.join(data, "transcripts.json")))
        self.assertEqual(projects_mod.list_projects(self.tmp), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
