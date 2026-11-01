"""Encoding regression: UTF-8 end-to-end (static, JSON, SSE headers, MSP stdio).

No real MSP, no network, no data/ writes. Run:
  python evals/test_encoding.py
"""
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server
from memory import msp as msp_mod

S1 = "\u00a1Hola! \u00bfEn qu\u00e9 te puedo ayudar hoy?"
S2 = "M\u00e9xico, jalape\u00f1o, caf\u00e9, programaci\u00f3n"
S3 = "\u3053\u3093\u306b\u3061\u306f\u3001\u4e16\u754c"

FAKE_SERVE = (
    "import sys, json\n"
    "for line in sys.stdin:\n"
    "    line = line.strip()\n"
    "    if not line:\n"
    "        continue\n"
    "    try:\n"
    "        req = json.loads(line)\n"
    "    except Exception:\n"
    "        continue\n"
    "    rid, m, p = req.get('id'), req.get('method'), req.get('params') or {}\n"
    "    if m == 'initialize':\n"
    "        res = {'server': 'fake'}\n"
    "    elif m == 'session/start':\n"
    "        res = {'session': {'sessionId': 'fake-sid-1'}}\n"
    "    elif m == 'echo':\n"
    "        res = {'s': p.get('s')}\n"
    "    else:\n"
    "        res = {}\n"
    "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': res},"
    " ensure_ascii=False) + chr(10))\n"
    "    sys.stdout.flush()\n"
)


class FakeHandler(server.H):
    def __init__(self, method, path, body=None):
        self.command = method
        self.path = path
        raw = json.dumps(body or {}).encode("utf-8") if body is not None else b""
        from email.message import Message
        h = Message()
        h["Content-Length"] = str(len(raw))
        self.headers = h
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.code = None
        self.hdrs = {}

    def send_response(self, code, msg=None):
        self.code = code

    def send_header(self, k, v):
        self.hdrs[k] = v

    def end_headers(self):
        pass

    def send_error(self, code, msg=None):
        self.code = code


def make_fake_muse(tmp):
    serve = os.path.join(tmp, "fake_serve.py")
    with open(serve, "w", encoding="utf-8") as f:
        f.write(FAKE_SERVE)
    if os.name == "nt":
        exe = os.path.join(tmp, "muse.cmd")
        with open(exe, "w", encoding="utf-8") as f:
            f.write('@echo off\npython "%~dp0fake_serve.py" %*\n')
    else:
        exe = os.path.join(tmp, "muse")
        with open(exe, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env python3\n" + FAKE_SERVE)
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
    return exe


class EncodingTest(unittest.TestCase):
    WEB = ["index.html", "app.js", "bridge.js", "styles.css"]

    def test_web_sources_are_valid_utf8(self):
        for name in self.WEB:
            with open(os.path.join(ROOT, "web", name), "rb") as f:
                raw = f.read()
            self.assertTrue(raw, name)
            text = raw.decode("utf-8")  # strict: raises on bad bytes
            if name in ("index.html", "app.js"):
                self.assertTrue(any(ord(c) > 127 for c in text), name)

    def test_meta_charset_utf8(self):
        with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as f:
            head = f.read(2000)
        self.assertIn('<meta charset="utf-8"', head.lower().replace("'", '"'))

    def test_static_headers_declare_utf8(self):
        for path in ["/", "/app.js", "/styles.css", "/bridge.js"]:
            h = FakeHandler("GET", path)
            h.do_GET()
            self.assertEqual(h.code, 200, path)
            self.assertIn("charset=utf-8", h.hdrs.get("Content-Type", ""), path)

    def test_json_headers_and_roundtrip(self):
        h = FakeHandler("GET", "/api/ui/bootstrap")
        h.do_GET()
        self.assertEqual(h.code, 200)
        self.assertIn("charset=utf-8", h.hdrs.get("Content-Type", ""))
        h2 = FakeHandler("GET", "/x")
        h2._send(200, {"s1": S1, "s2": S2, "s3": S3})
        wire = h2.wfile.getvalue()
        self.assertIn("charset=utf-8", h2.hdrs.get("Content-Type", ""))
        back = json.loads(wire.decode("utf-8"))
        self.assertEqual((back["s1"], back["s2"], back["s3"]), (S1, S2, S3))

    def test_sse_headers_declare_utf8(self):
        h = FakeHandler("POST", "/api/chat_stream",
                        {"session_id": "t", "message": "hi"})
        with mock.patch.object(server, "find_muse", return_value="muse-fake"), \
             mock.patch.object(server, "run_turn_sse", return_value=None):
            h.do_POST()
        self.assertEqual(h.code, 200)
        self.assertIn("text/event-stream", h.hdrs.get("Content-Type", ""))
        self.assertIn("charset=utf-8", h.hdrs.get("Content-Type", ""))

    def test_msp_stdio_roundtrips_utf8(self):
        tmp = tempfile.mkdtemp(prefix="enc-msp-")
        try:
            exe = make_fake_muse(tmp)
            old = os.environ.get("PYTHONIOENCODING")
            os.environ["PYTHONIOENCODING"] = "utf-8"
            try:
                with mock.patch.object(msp_mod, "_muse_cmd", return_value=exe):
                    host = msp_mod.MSPHost(tmp, provider="meta")
                    try:
                        for s in (S1, S2, S3):
                            r = host.call("echo", {"s": s}, timeout=30)
                            self.assertEqual(r["s"], s)
                    finally:
                        host.stop()
            finally:
                if old is None:
                    os.environ.pop("PYTHONIOENCODING", None)
                else:
                    os.environ["PYTHONIOENCODING"] = old
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
