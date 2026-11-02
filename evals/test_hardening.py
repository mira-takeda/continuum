"""Fix 1: localhost API hardening. Host/Origin/token guards, security headers,
per-start token delivery. Stdlib + repo only, no Muse needed. Run:
  python evals/test_hardening.py
"""
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server as server_mod


class HardeningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hard-")
        os.makedirs(os.path.join(cls.tmp, "data", "entries"))
        os.makedirs(os.path.join(cls.tmp, "web"))
        with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as f:
            tracked_html = f.read()
        with open(os.path.join(cls.tmp, "web", "index.html"), "w",
                  encoding="utf-8") as f:
            f.write(tracked_html)
        cls.old_root = server_mod.ROOT
        cls.old_log = server_mod.H.log_message
        server_mod.ROOT = cls.tmp
        server_mod.H.log_message = lambda *a, **k: None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.H)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        server_mod.ROOT = cls.old_root
        server_mod.H.log_message = cls.old_log

    def req(self, path, method="GET", obj=None, headers=None):
        data = json.dumps(obj).encode("utf-8") if obj is not None else None
        hdrs = dict(headers or {})
        if obj is not None:
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs,
                                     method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read(), dict(r.headers.items())
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers.items())

    def tok(self, extra=None):
        h = {server_mod.TOKEN_HEADER: server_mod.API_TOKEN}
        h.update(extra or {})
        return h

    def test_host_must_be_localhost(self):
        code, body, _ = self.req("/", headers={"Host": "evil.example.com"})
        self.assertEqual(code, 403)
        self.assertIn("forbidden host", body.decode())
        code, _, _ = self.req("/", headers={"Host": "evil.example.com:8137"})
        self.assertEqual(code, 403)
        code, _, _ = self.req("/api/entries", headers={"Host": "127.0.0.1"})
        self.assertEqual(code, 200)
        code, _, _ = self.req("/api/entries",
                              headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(code, 200)
        code, body, _ = self.req("/api/unlink", method="POST", obj={},
                                 headers={"Host": "evil.example.com",
                                          **self.tok()})
        self.assertEqual(code, 403)

    def test_post_requires_token(self):
        code, body, _ = self.req("/api/unlink", method="POST",
                                 obj={"from": "a", "to": "b"})
        self.assertEqual(code, 403)
        self.assertIn("api token", body.decode())
        code, _, _ = self.req("/api/unlink", method="POST",
                              obj={"from": "a", "to": "b"},
                              headers={server_mod.TOKEN_HEADER: "wrong"})
        self.assertEqual(code, 403)
        # Valid token passes the guard, then id validation runs (400, not 403).
        code, _, _ = self.req("/api/unlink", method="POST",
                              obj={"from": "../x", "to": "y"},
                              headers=self.tok())
        self.assertEqual(code, 400)

    def test_origin_checked_on_mutations(self):
        code, body, _ = self.req(
            "/api/unlink", method="POST", obj={"from": "a", "to": "b"},
            headers=self.tok({"Origin": "http://evil.example.com"}))
        self.assertEqual(code, 403)
        self.assertIn("forbidden origin", body.decode())
        code, _, _ = self.req(
            "/api/unlink", method="POST", obj={"from": "../x", "to": "y"},
            headers=self.tok({"Origin": f"http://127.0.0.1:{self.port}"}))
        self.assertEqual(code, 400)
        code, _, _ = self.req(
            "/api/unlink", method="POST", obj={"from": "../x", "to": "y"},
            headers=self.tok({"Referer": "http://evil.example.com/p"}))
        self.assertEqual(code, 403)
        # Non-browser clients send no Origin; the token is the guard.
        code, _, _ = self.req("/api/unlink", method="POST",
                              obj={"from": "../x", "to": "y"},
                              headers=self.tok())
        self.assertEqual(code, 400)

    def test_cross_origin_simple_post_blocked(self):
        req = urllib.request.Request(
            self.base + "/api/reset", data=b"{}",
            headers={"Content-Type": "text/plain",
                     "Origin": "http://evil.example.com"},
            method="POST")
        try:
            urllib.request.urlopen(req)
            self.fail("simple cross-origin POST must not succeed")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)

    def test_token_delivered_only_in_served_page(self):
        code, body, hdrs = self.req("/")
        self.assertEqual(code, 200)
        html = body.decode("utf-8")
        self.assertIn(server_mod.API_TOKEN, html)
        self.assertNotIn(server_mod.TOKEN_PLACEHOLDER, html)
        self.assertIn("no-store", hdrs.get("Cache-Control", ""))
        tracked = os.path.join(ROOT, "web", "index.html")
        with open(tracked, encoding="utf-8") as f:
            src = f.read()
        self.assertIn(server_mod.TOKEN_PLACEHOLDER, src)
        self.assertNotIn(server_mod.API_TOKEN, src)

    def test_security_headers(self):
        for path in ("/", "/api/entries"):
            code, _, hdrs = self.req(path)
            self.assertEqual(code, 200, path)
            low = {k.lower(): v for k, v in hdrs.items()}
            self.assertEqual(low.get("x-content-type-options"), "nosniff", path)
            self.assertEqual(low.get("x-frame-options"), "DENY", path)
            self.assertEqual(low.get("referrer-policy"), "no-referrer", path)
            self.assertIn("frame-ancestors", low.get("content-security-policy", ""),
                          path)

    def test_token_not_persisted(self):
        self.assertGreaterEqual(len(server_mod.API_TOKEN), 32)
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]+", server_mod.API_TOKEN))
        for dirpath, _, files in os.walk(os.path.join(self.tmp, "data")):
            for fn in files:
                with open(os.path.join(dirpath, fn), "rb") as f:
                    self.assertNotIn(server_mod.API_TOKEN.encode(),
                                     f.read(), fn)


if __name__ == "__main__":
    unittest.main(verbosity=2)
