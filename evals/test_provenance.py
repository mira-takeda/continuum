"""Fix 1: memory provenance never leaks into Normal chat. Run:
  python evals/test_provenance.py
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from memory import extract as extract_mod
from memory import turn as turn_mod
import server as server_mod

EID = "20261101-1400-probe-entry"


def fixture_root():
    tmp = tempfile.mkdtemp(prefix="prov-")
    os.makedirs(os.path.join(tmp, "data", "entries"))
    with open(os.path.join(tmp, "data", "core.json"), "w") as f:
        json.dump({"entry_ids": []}, f)
    return tmp


def fixture_ret():
    return {"opened": [{"id": EID, "status": "current", "state": "Recent",
                        "title": "Probe entry", "body": "user prefers Muse"}],
            "paths": [], "shortlist": [], "deep": 0}


class LeakGuardTest(unittest.TestCase):
    def test_default_is_frozen_protocol(self):
        t = turn_mod.build_turn_text(fixture_root(), "hola", fixture_ret(),
                                     {"entries": {}})
        self.assertIn("(cite [id] when used)", t)  # frozen pin, evals rely on it
        self.assertIn("Entry [%s]" % EID, t)

    def test_guarded_keeps_ids_for_ops_bans_visible_echo(self):
        t = turn_mod.build_turn_text(fixture_root(), "hola", fixture_ret(),
                                     {"entries": {}}, leak_guard=True)
        self.assertNotIn("(cite [id] when used)", t)
        self.assertIn("Entry [%s]" % EID, t)  # ops still need ids
        self.assertIn("ONLY inside the ```memory-ops block", t)
        self.assertIn("never print bracketed entry ids", t)


class SplitOpsBlockTest(unittest.TestCase):
    def test_empty_and_none(self):
        self.assertEqual(extract_mod.split_ops_block(""), ("", None))
        self.assertEqual(extract_mod.split_ops_block(None), ("", None))

    def test_no_block(self):
        self.assertEqual(extract_mod.split_ops_block("plain reply"),
                         ("plain reply", None))

    def test_block_at_end(self):
        clean, block = extract_mod.split_ops_block(
            "Claro.\n\n```memory-ops\n[{\"kind\":\"confirm\",\"id\":\"%s\"}]\n```" % EID)
        self.assertEqual(clean, "Claro.")
        self.assertTrue(block.startswith("```memory-ops"))
        self.assertIn(EID, block)

    def test_block_middle_keeps_tail(self):
        clean, block = extract_mod.split_ops_block(
            "A\n```memory-ops\n[]\n```\nB")
        self.assertEqual(clean, "A\nB")
        self.assertIsNotNone(block)

    def test_unclosed_is_content(self):
        s = "text\n```memory-ops\n[{\"kind\": \"create\""
        self.assertEqual(extract_mod.split_ops_block(s), (s, None))

    def test_agrees_with_parser(self):
        s = "R.\n```memory-ops\n[{\"kind\":\"confirm\",\"id\":\"%s\"}]\n```" % EID
        _, block = extract_mod.split_ops_block(s)
        ops = extract_mod.parse_memory_block(s)
        self.assertEqual(len(ops), 1)
        self.assertIn(EID, block)


def drive(chunks):
    g = server_mod.OpsGate()
    out = "".join(g.feed(c) for c in chunks)
    tail, block, _proj = g.flush_remainder()
    return out + tail, block


class OpsGateTest(unittest.TestCase):
    def test_no_block_reassembles_exactly(self):
        for chunks in (["hello world, this is a reply without any block"],
                       ["hel", "lo ", "wor", "ld split across frames"],
                       ["a", "b", "c", "d", "e", "f", "tiny pieces here"]):
            text, block = drive(chunks)
            self.assertEqual(text, "".join(chunks))
            self.assertIsNone(block)

    def test_block_withheld_split_marker(self):
        chunks = ["Claro, te llamaré Muse. \n\n```mem", "ory-ops\n[{\"kind\":",
                  "\"confirm\",\"id\":\"%s\"}]\n```" % EID]
        text, block = drive(chunks)
        self.assertEqual(text, "Claro, te llamaré Muse.")
        self.assertIn(EID, block)
        self.assertNotIn("memory-ops", text)

    def test_unclosed_flushes_as_text(self):
        chunks = ["Reply start ", "```memory-ops\n[{\"kind\":"]
        text, block = drive(chunks)
        self.assertEqual(text, "".join(chunks))  # content wins
        self.assertIsNone(block)

    def test_short_text_under_hold(self):
        text, block = drive(["Hi!"])
        self.assertEqual(text, "Hi!")
        self.assertIsNone(block)

    def test_flush_idempotent(self):
        g = server_mod.OpsGate()
        g.feed("hi")
        self.assertEqual(g.flush_remainder(), ("hi", None, None))
        self.assertEqual(g.flush_remainder(), ("", None, None))

    def test_matches_split_authority(self):
        cases = ["plain",
                 "A\n```memory-ops\n[]\n```",
                 "x" * 50 + "\n```memory-ops\n[1]\n```\ntail here"]
        for s in cases:
            halves = [s[:len(s) // 2], s[len(s) // 2:]]
            gated, gb = drive(halves)
            clean, sb = extract_mod.split_ops_block(s)
            self.assertEqual(gated, clean)
            self.assertEqual(gb, sb)

    def test_project_block_withheld(self):
        chunks = ["Listo. Carpeta creada. \n\n```proj", "ect-ops\n[{\"kind\":" ,
                  "\"create_project\"}]\n```"]
        g = server_mod.OpsGate()
        out = "".join(g.feed(c) for c in chunks)
        tail, mem, proj = g.flush_remainder()
        self.assertEqual(out + tail, "Listo. Carpeta creada.")
        self.assertIsNone(mem)
        self.assertIn("create_project", proj)
        self.assertNotIn("project-ops", out + tail)

    def test_both_blocks_split(self):
        for s in (
            "Ok.\n```memory-ops\n[{\"kind\":\"confirm\",\"id\":\"x\"}]\n```\n"
            "```project-ops\n[{\"kind\":\"register_project\"}]\n```",
            "Ok.\n```project-ops\n[{\"kind\":\"register_project\"}]\n```\n"
            "```memory-ops\n[{\"kind\":\"confirm\",\"id\":\"x\"}]\n```"):
            g = server_mod.OpsGate()
            out = g.feed(s)
            tail, mem, proj = g.flush_remainder()
            self.assertEqual(out + tail, "Ok.")
            self.assertIn("confirm", mem)
            self.assertIn("register_project", proj)

    def test_duplicate_memory_echo_withheld(self):
        s = ("Listo.\n```memory-ops\n[{\"kind\":\"confirm\",\"id\":\"a\"}]\n```\n"
             "```memory-ops\n[{\"kind\":\"confirm\",\"id\":\"b\"}]\n```")
        g = server_mod.OpsGate()
        out = g.feed(s)
        tail, mem, proj = g.flush_remainder()
        self.assertEqual(out + tail, "Listo.")
        self.assertIn(chr(34) + "a" + chr(34), mem)
        self.assertIsNone(proj)

    def test_duplicate_project_echo_withheld(self):
        s = ("Listo.\n```project-ops\n[{\"kind\":\"create_project\"}]\n```\n"
             "```project-ops\n[{\"kind\":\"create_project\"}]\n```")
        g = server_mod.OpsGate()
        out = g.feed(s)
        tail, mem, proj = g.flush_remainder()
        self.assertEqual(out + tail, "Listo.")
        self.assertIsNone(mem)
        self.assertIn("create_project", proj)


if __name__ == "__main__":
    unittest.main(verbosity=1)
