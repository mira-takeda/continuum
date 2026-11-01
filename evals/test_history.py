"""History card titles: deterministic, no model call.

Run: python evals/test_history.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server as server_mod


class HistoryTitleTest(unittest.TestCase):
    def test_spanish_examples(self):
        self.assertEqual(
            server_mod.history_title("ayúdame con el regalo de navidad para Sato"),
            "Regalo de navidad para Sato")
        self.assertEqual(
            server_mod.history_title("muéstrame los permisos actuales de Muse"),
            "Permisos actuales de Muse")

    def test_filler_and_caps(self):
        self.assertEqual(
            server_mod.history_title("dime solo la palabra: NARANJA"),
            "Solo la palabra: NARANJA")
        self.assertEqual(
            server_mod.history_title("please show me the current usage stats now"),
            "Show me the current usage stats")
        self.assertEqual(server_mod.history_title(""), "")
        self.assertEqual(server_mod.history_title("   "), "")
        self.assertEqual(server_mod.history_title("hola"), "Hola")

    def test_length_cap(self):
        t = server_mod.history_title(
            "vamos a preparar mi cumpleaños con tematica de hermanos Grimm "
            "y muchos invitados especiales del extranjero")
        self.assertTrue(len(t) <= 45)
        self.assertEqual(len(t.split()), 6)

    def test_seed_title(self):
        self.assertEqual(server_mod.history_title("semilla uno"), "Semilla uno")


if __name__ == "__main__":
    unittest.main(verbosity=1)
