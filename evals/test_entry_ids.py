"""Fix 2: entry-id path containment. Traversal ids must never escape data/entries,
via store.* or HTTP /api/entries/<id>. Run:
  python evals/test_entry_ids.py
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

from memory import store as store_mod
import server as server_mod

EVIL_IDS = [
    "../secret",
    "..\\secret",
    "../..",
    "..",
    "a/b",
    "a\\b",
    "/abs/path",
    "C:\\win\\path",
    "C:/win/path",
    "",
    "has space",
    "semi;colon:yes",
]

OUTSIDE_TEXT = "OUTSIDE-MARKER-DO-NOT-SERVE"


def fixture_root():
    tmp = tempfile.mkdtemp(prefix="eid-")
    ent = os.path.join(tmp, "data", "entries")
    os.makedirs(ent)
    store_mod.save(ent, "legit-entry", store_mod.default_meta(
        "legit-entry", "Legit", ["fact"], ["t1"], "conversation", "probe"), "legit body")
    with open(os.path.join(tmp, "secret.md"), "w") as f:
        f.write(OUTSIDE_TEXT)
    with open(os.path.join(tmp, "data", "secret.md"), "w") as f:
        f.write(OUTSIDE_TEXT)
    return tmp


class ContainmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = fixture_root()
        self.ent = os.path.join(self.tmp, "data", "entries")
        self.log = os.path.join(self.tmp, "data", "log.jsonl")

    def test_entry_path_rejects_evil(self):
        for eid in EVIL_IDS:
            with self.assertRaises(ValueError, msg=eid):
                store_mod.entry_path(self.ent, eid)

    def test_entry_path_accepts_slug(self):
        p = store_mod.entry_path(self.ent, "20261101-1400-some-slug_9.x")
        self.assertTrue(p.endswith("20261101-1400-some-slug_9.x.md"))

    def test_read_paths_reject(self):
        for eid in EVIL_IDS:
            with self.assertRaises(ValueError, msg="load " + eid):
                store_mod.load(self.ent, eid)
            with self.assertRaises(ValueError, msg="touch " + eid):
                store_mod.touch(self.ent, eid)

    def test_write_paths_reject_and_leave_no_file(self):
        meta = store_mod.default_meta("x", "X", ["fact"], ["t"], "conversation", "probe")
        for eid in EVIL_IDS:
            with self.assertRaises(ValueError, msg="save " + eid):
                store_mod.save(self.ent, eid, dict(meta), "evil")
            with self.assertRaises(ValueError, msg="update " + eid):
                store_mod.update(self.ent, self.log, eid, body_append="evil")
            with self.assertRaises(ValueError, msg="unlink " + eid):
                store_mod.unlink(self.ent, self.log, eid, "legit-entry")
            with self.assertRaises(ValueError, msg="repeat " + eid):
                store_mod.repeat_observation(self.ent, eid)
            with self.assertRaises(ValueError, msg="confirm " + eid):
                store_mod.confirm(self.ent, self.log, eid)
        leftovers = [f for _, _, fs in os.walk(self.tmp) for f in fs
                     if f not in ("legit-entry.md", "secret.md", "log.jsonl")]
        self.assertEqual(leftovers, [])

    def test_outside_files_untouched(self):
        for rel in ("secret.md", os.path.join("data", "secret.md")):
            with open(os.path.join(self.tmp, rel)) as f:
                self.assertEqual(f.read(), OUTSIDE_TEXT)

    def test_legit_roundtrip_still_works(self):
        op = store_mod.create(self.ent, self.log, "Roundtrip Probe", ["fact"],
                              "roundtrip body", ["t1"], "conversation", "probe")
        meta, body = store_mod.load(self.ent, op["entry_id"])
        self.assertIn("roundtrip body", body)
        store_mod.update(self.ent, self.log, op["entry_id"], body_append="more")
        _, body2 = store_mod.load(self.ent, op["entry_id"])
        self.assertIn("more", body2)


class HttpEntriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = fixture_root()
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

    def get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def post(self, path, obj):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, data=json.dumps(obj).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def test_legit_entry_200(self):
        code, body = self.get("/api/entries/legit-entry")
        self.assertEqual(code, 200)
        self.assertIn("legit body", body)

    def test_missing_entry_404_no_path_leak(self):
        code, body = self.get("/api/entries/no-such-entry")
        self.assertEqual(code, 404)
        self.assertNotIn(self.tmp, body)
        self.assertNotIn("entries", body)

    def test_traversal_returns_400_never_outside(self):
        for raw in ("..%2fsecret", "..%5csecret", "..%2f..%2fsecret",
                    "%2e%2e%2fsecret", "a%2fb"):
            code, body = self.get("/api/entries/" + raw)
            self.assertEqual(code, 400, raw)
            self.assertNotIn(OUTSIDE_TEXT, body)

    def test_unlink_traversal_400(self):
        code, body = self.post("/api/unlink", {"from": "../secret", "to": "legit-entry"})
        self.assertEqual(code, 400)
        with open(os.path.join(self.tmp, "secret.md")) as f:
            self.assertEqual(f.read(), OUTSIDE_TEXT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
