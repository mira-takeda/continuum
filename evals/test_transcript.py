"""Bug 4: transcript persist/trim/load. Run:
  python evals/test_transcript.py
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server as server_mod


class TranscriptTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="continuum-transcript-")
        self.p = os.path.join(self.d, "transcripts.json")

    def test_roundtrip(self):
        server_mod.transcript_append(self.p, "s1", "hola", "Hola!", "ss1t1")
        server_mod.transcript_append(self.p, "s1", "dos", "Dos.", "ss1t2")
        turns = server_mod.transcript_load(self.p, "s1")
        self.assertEqual([(t["user"], t["assistant"]) for t in turns],
                         [("hola", "Hola!"), ("dos", "Dos.")])
        self.assertTrue(all(t["ts"] and t["turn"] for t in turns))

    def test_sessions_isolated(self):
        server_mod.transcript_append(self.p, "s1", "a", "A", "t1")
        server_mod.transcript_append(self.p, "s2", "b", "B", "t1")
        self.assertEqual(len(server_mod.transcript_load(self.p, "s1")), 1)
        self.assertEqual(server_mod.transcript_load(self.p, "s2")[0]["user"],
                         "b")

    def test_missing_is_empty(self):
        self.assertEqual(server_mod.transcript_load(self.p, "nope"), [])
        self.assertEqual(server_mod.transcript_load(
            os.path.join(self.d, "missing.json"), "s1"), [])

    def test_cap_trims_oldest(self):
        old = server_mod.MAX_TRANSCRIPT_TURNS
        server_mod.MAX_TRANSCRIPT_TURNS = 3
        try:
            for i in range(5):
                server_mod.transcript_append(self.p, "s1", "u%d" % i,
                                             "a%d" % i, "t%d" % i)
            turns = server_mod.transcript_load(self.p, "s1")
            self.assertEqual([t["user"] for t in turns], ["u2", "u3", "u4"])
        finally:
            server_mod.MAX_TRANSCRIPT_TURNS = old

    def test_corrupt_file_recovers(self):
        with open(self.p, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(server_mod.transcript_load(self.p, "s1"), [])
        n = server_mod.transcript_append(self.p, "s1", "a", "A", "t1")
        self.assertEqual(n, 1)
        self.assertEqual(server_mod.transcript_load(self.p, "s1")[0]["user"],
                         "a")


if __name__ == "__main__":
    unittest.main(verbosity=1)
