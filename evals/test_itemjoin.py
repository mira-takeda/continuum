"""ItemTextJoiner: separate native message items, verbatim inside an item.

Regression for the docx turn (transcript s6c752355t14): two distinct
assistant_message_committed items were glued as "...docx.Ale...".
Terminal Muse renders each committed message as its own block.

Run: python evals/test_itemjoin.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server as server_mod


class JoinerTest(unittest.TestCase):
    def test_same_item_verbatim(self):
        j = server_mod.ItemTextJoiner()
        out = j.feed("Hello ", "aa11") + j.feed("world.", "aa11")
        self.assertEqual(out, "Hello world.")

    def test_item_change_inserts_break(self):
        j = server_mod.ItemTextJoiner()
        out = j.feed("...eliges docx.", "msg_01") + j.feed("Ale, te debo", "msg_02")
        self.assertEqual(out, "...eliges docx.\n\nAle, te debo")

    def test_existing_whitespace_no_double_break(self):
        j = server_mod.ItemTextJoiner()
        out = j.feed("primero.\n", "aa") + j.feed("segundo", "bb")
        self.assertEqual(out, "primero.\nsegundo")
        j = server_mod.ItemTextJoiner()
        out = j.feed("primero.", "aa") + j.feed("\nsegundo", "bb")
        self.assertEqual(out, "primero.\nsegundo")

    def test_empty_item_keeps_flowing(self):
        j = server_mod.ItemTextJoiner()
        out = j.feed("a", "") + j.feed("b", "") + j.feed("c", "x1")
        self.assertEqual(out, "abc")

    def test_gate_still_withholds_fences(self):
        j = server_mod.ItemTextJoiner()
        g = server_mod.OpsGate()
        shown = ""
        for txt, iid in [("Visible uno. ", "m1"),
                         ("```memory-ops\n[{\"kind\": \"confirm\"}]\n```", "m1")]:
            shown += g.feed(j.feed(txt, iid)) or ""
        tail, ops, _ = g.flush_remainder()
        shown += tail or ""
        self.assertIn("Visible uno.", shown)
        self.assertNotIn("memory-ops", shown)
        self.assertIsNotNone(ops)

    def test_two_items_through_gate(self):
        j = server_mod.ItemTextJoiner()
        g = server_mod.OpsGate()
        shown = ""
        for txt, iid in [("Respuesta uno.", "m1"), ("Respuesta dos.", "m2")]:
            shown += g.feed(j.feed(txt, iid)) or ""
        tail, _, _ = g.flush_remainder()
        shown += tail or ""
        self.assertEqual(shown, "Respuesta uno.\n\nRespuesta dos.")


if __name__ == "__main__":
    unittest.main(verbosity=1)
