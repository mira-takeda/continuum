"""Attachment staging: sanitize/parse/stage/resolve/prompt/transcript-meta.

Run: python evals/test_attachments.py
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server as server_mod


def multipart_body(boundary, fields, files):
    out = b""
    for k, v in fields.items():
        out += (b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="%s"\r\n\r\n' % k.encode()
                + v.encode() + b"\r\n")
    for name, fn, ct, data in files:
        out += (b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                % (name.encode(), fn.encode())
                + b"Content-Type: " + ct.encode() + b"\r\n\r\n"
                + data + b"\r\n")
    return out + b"--" + boundary + b"--\r\n"


class AttachTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="continuum-att-")

    def test_sanitize(self):
        self.assertEqual(server_mod.att_sanitize("../../etc/passwd"), "passwd")
        self.assertEqual(server_mod.att_sanitize(""), "file")
        self.assertEqual(server_mod.att_sanitize("  "), "file")
        self.assertTrue(len(server_mod.att_sanitize("x" * 500)) <= 120)

    def test_multipart(self):
        b = b"BOUND123"
        body = multipart_body(b, {"session_id": "s1"},
                              [("files", "a.txt", "text/plain", b"hola"),
                               ("files", "b.bin", "application/octet-stream",
                                b"\x00\x01\x02")])
        fields, files = server_mod.att_parse_multipart(
            body, "multipart/form-data; boundary=BOUND123")
        self.assertEqual(fields, {"session_id": "s1"})
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0]["filename"], "a.txt")
        self.assertEqual(files[1]["data"], b"\x00\x01\x02")

    def test_stage_resolve_roundtrip(self):
        staged = server_mod.att_stage(
            self.d, "chat1",
            [{"filename": "a.txt", "ctype": "text/plain", "data": b"hola"}])
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0]["name"], "a.txt")
        self.assertEqual(staged[0]["size"], 4)
        self.assertNotIn("path", staged[0])
        res = server_mod.att_resolve(self.d, "chat1", [staged[0]["id"]])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["type"], "text/plain")
        self.assertTrue(os.path.isfile(res[0]["path"]))
        with open(res[0]["path"], "rb") as f:
            self.assertEqual(f.read(), b"hola")
        with self.assertRaises(ValueError):
            server_mod.att_resolve(self.d, "chat1", ["nope"])
        with self.assertRaises(ValueError):
            server_mod.att_resolve(self.d, "otherchat", [staged[0]["id"]])

    def test_limits(self):
        big = b"x" * (server_mod.MAX_ATT_BYTES + 1)
        with self.assertRaises(ValueError):
            server_mod.att_stage(self.d, "c",
                                 [{"filename": "b.bin", "ctype": "x",
                                   "data": big}])
        many = [{"filename": "f%d" % i, "ctype": "x", "data": b"y"}
                for i in range(server_mod.MAX_ATTS + 1)]
        with self.assertRaises(ValueError):
            server_mod.att_stage(self.d, "c", many)
        with self.assertRaises(ValueError):
            server_mod.att_stage(self.d, "c", [])

    def test_prompt_block(self):
        self.assertEqual(server_mod.att_prompt_block([]), "")
        blk = server_mod.att_prompt_block(
            [{"name": "a.txt", "type": "text/plain", "size": 4,
              "path": "C:/d/att/a.txt"}])
        self.assertIn("<attachments>", blk)
        self.assertIn("a.txt", blk)
        self.assertIn("C:/d/att/a.txt", blk)

    def test_transcript_meta(self):
        p = os.path.join(self.d, "transcripts.json")
        server_mod.transcript_append(p, "s1", "u", "a", "t1",
                                     attachments=[{"name": "a.txt", "size": 4,
                                                   "type": "text/plain",
                                                   "path": "C:/secret/x"}])
        t = server_mod.transcript_load(p, "s1")[0]
        self.assertEqual(t["attachments"],
                         [{"name": "a.txt", "size": 4, "type": "text/plain"}])
        server_mod.transcript_append(p, "s1", "u2", "a2", "t2")
        self.assertEqual(server_mod.transcript_load(p, "s1")[1]["attachments"],
                         [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
