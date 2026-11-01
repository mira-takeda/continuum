"""Deterministic integration test: 3-layer context isolation (A->B->C->A).

Layers: personal encyclopedia (global) + workspace map + per-project
.continuum.md files. No LLM, no MSP, no network. Run:
  python evals/test_project_context.py
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from memory import core as core_mod
from memory import extract as extract_mod
from memory import index as index_mod
from memory import projects as proj_mod
from memory import retrieve as retrieve_mod
from memory import turn as turn_mod


def apply(root, ops, turn="t0", active=None):
    return extract_mod.apply_ops(root, ops, turn, "test", active_project=active)


def full_text(root, bid, message):
    idx = index_mod.load_or_build(root)
    ret = retrieve_mod.retrieve(root, None, message, system="S2")
    return turn_mod.build_turn_text(root, message, ret, idx, proj_mod.assemble(root, bid))


class ThreeLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ctx3-")
        self.root = os.path.join(self.tmp, "app")
        self.ws = os.path.join(self.tmp, "ws")
        self.paths = {}
        os.makedirs(self.ws)
        for name in ("A", "B", "C"):
            p = os.path.join(self.ws, "proj" + name)
            os.makedirs(p)
            self.paths[name] = p
        proj_mod.set_workspace(self.root, self.ws)
        for name, path in self.paths.items():
            proj_mod.register_project(self.root, name, path)
            proj_mod.init_project(self.root, name)
        apply(self.root, [{"kind": "create", "title": "Global preference",
                            "types": ["identity"],
                            "body": "TOKEN_PREF_CONCISE: the user prefers concise answers everywhere.",
                            "source": "user-stated"}])
        core_mod.rebuild(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed_decisions(self):
        # Each write runs with its own project active, mirroring the server.
        apply(self.root, [{"kind": "create", "scope": "project:A", "section": "decisions",
                            "title": "x", "body": "TOKEN_DECISION_A_APPLE: Project A standardizes on Apple SDK."}],
              active="A")
        apply(self.root, [{"kind": "create", "scope": "project:B", "section": "decisions",
                            "title": "x", "body": "TOKEN_DECISION_B_BOOST: Project B uses Boost for parsing."}],
              active="B")
        apply(self.root, [{"kind": "create", "scope": "project:C", "section": "decisions",
                            "title": "x", "body": "TOKEN_DECISION_C_CUDA: Project C trains on CUDA."}],
              active="C")

    def test_global_preference_follows_all_projects(self):
        for name in ("A", "B", "C"):
            proj_mod.activate(self.root, "s1", name)
            self.assertIn("TOKEN_PREF_CONCISE", full_text(self.root, "s1", "hello"))
        proj_mod.clear_active(self.root, "s1")
        self.assertIn("TOKEN_PREF_CONCISE", full_text(self.root, "s1", "hello"))

    def test_project_decisions_isolated(self):
        self.seed_decisions()
        cases = {"A": "TOKEN_DECISION_A_APPLE", "B": "TOKEN_DECISION_B_BOOST",
                 "C": "TOKEN_DECISION_C_CUDA"}
        for name, tok in cases.items():
            proj_mod.activate(self.root, "s1", name)
            text = full_text(self.root, "s1", "status update?")
            self.assertIn(tok, text)
            for other, otok in cases.items():
                if other != name:
                    self.assertNotIn(otok, text)

    def test_correction_in_B_stays_in_B(self):
        self.seed_decisions()
        before_a = proj_mod.read_project(self.root, "A")
        before_c = proj_mod.read_project(self.root, "C")
        r = apply(self.root, [{"kind": "update", "scope": "project:B", "section": "decisions",
                               "title": "x", "replace": "TOKEN_DECISION_B_BOOST",
                               "body": "TOKEN_DECISION_B_NEW: Project B switched to PEGTL."}],
                  active="B")
        self.assertEqual(r[0]["kind"], "project-replace")
        proj_mod.activate(self.root, "s1", "B")
        tb = full_text(self.root, "s1", "status?")
        self.assertIn("TOKEN_DECISION_B_NEW", tb)
        self.assertIn("superseded", proj_mod.read_project(self.root, "B"))
        self.assertEqual(proj_mod.read_project(self.root, "A"), before_a)
        self.assertEqual(proj_mod.read_project(self.root, "C"), before_c)
        proj_mod.activate(self.root, "s1", "A")
        ta = full_text(self.root, "s1", "status?")
        self.assertNotIn("TOKEN_DECISION_B_NEW", ta)
        self.assertNotIn("TOKEN_DECISION_B_BOOST", ta)

    def test_pending_in_A_and_fresh_session_resume(self):
        self.seed_decisions()
        apply(self.root, [{"kind": "create", "scope": "project:A", "section": "pending",
                            "title": "x", "body": "TOKEN_PENDING_A_MIGRATE: migrate A storage layer."}],
              active="A")
        proj_mod.activate(self.root, "s1", "A")
        first = full_text(self.root, "s1", "what is pending?")
        self.assertIn("TOKEN_PENDING_A_MIGRATE", first)
        # Fresh session returns to A: same project state, no B/C content.
        proj_mod.activate(self.root, "s2-fresh", "A")
        again = full_text(self.root, "s2-fresh", "what is pending?")
        self.assertIn("TOKEN_PENDING_A_MIGRATE", again)
        self.assertNotIn("TOKEN_DECISION_B_BOOST", again)
        self.assertNotIn("TOKEN_DECISION_C_CUDA", again)

    def test_mid_conversation_switch(self):
        self.seed_decisions()
        proj_mod.activate(self.root, "s1", "A")
        ta = full_text(self.root, "s1", "status?")
        self.assertIn("TOKEN_DECISION_A_APPLE", ta)
        proj_mod.activate(self.root, "s1", "B")  # same conversation switches
        tb = full_text(self.root, "s1", "status?")
        self.assertIn("TOKEN_DECISION_B_BOOST", tb)
        self.assertNotIn("TOKEN_DECISION_A_APPLE", tb)
        self.assertIn("TOKEN_PREF_CONCISE", tb)  # global still travels

    def test_no_project_no_injection(self):
        self.seed_decisions()
        self.assertEqual(proj_mod.assemble(self.root, "nobody"), "")
        self.assertIsNone(proj_mod.active_for(self.root, "nobody"))
        with self.assertRaises(ValueError):
            proj_mod.activate(self.root, "nobody", "Z")
        # Unregistered folder with its own .continuum.md is ignored: no match.
        stray = os.path.join(self.ws, "stray")
        os.makedirs(stray)
        with open(os.path.join(stray, ".continuum.md"), "w", encoding="utf-8") as f:
            f.write("# Project: Rogue\nTOKEN_ROGUE_SHOULD_NEVER_APPEAR\n")
        self.assertIsNone(proj_mod.detect_from_path(self.root, stray))
        # A subfolder inside a registered project belongs to that project...
        inner = os.path.join(self.paths["A"], "sub")
        os.makedirs(inner)
        self.assertEqual(proj_mod.detect_from_path(self.root, inner), "A")
        # ...but stray content is never injected anywhere.
        proj_mod.activate(self.root, "s1", "A")
        self.assertNotIn("TOKEN_ROGUE_SHOULD_NEVER_APPEAR",
                         full_text(self.root, "s1", "status?"))
        self.assertEqual(proj_mod.assemble(self.root, "nobody"), "")

    def test_no_automatic_creation(self):
        epath = os.path.join(self.ws, "projE")
        os.makedirs(epath)
        proj_mod.register_project(self.root, "E", epath)
        self.assertFalse(os.path.exists(os.path.join(epath, ".continuum.md")))
        self.assertEqual(proj_mod.detect_from_path(self.root, epath), "E")
        proj_mod.activate(self.root, "s1", "E")
        self.assertEqual(proj_mod.assemble(self.root, "s1"), "")  # missing file, no crash

    def test_secrets_guard(self):
        before = proj_mod.read_project(self.root, "A")
        r = apply(self.root, [{"kind": "create", "scope": "project:A", "section": "notes",
                               "title": "x", "body": "db password TOKEN_SECRET_XYZ hunter2"}],
                  active="A")
        self.assertEqual(r[0]["kind"], "rejected")  # unknown section rejects too
        # Use a valid section to test the secrets guard itself.
        r = apply(self.root, [{"kind": "create", "scope": "project:A", "section": "state",
                               "title": "x", "body": "api_key = TOKEN_SECRET_XYZ"}],
                  active="A")
        self.assertEqual(r[0]["kind"], "rejected")
        self.assertIn("secret", r[0]["reason"])
        after = proj_mod.read_project(self.root, "A")
        self.assertEqual(after, before)
        self.assertNotIn("TOKEN_SECRET_XYZ", after)

    def test_read_once_until_changed(self):
        self.seed_decisions()
        proj_mod.activate(self.root, "s1", "A")
        first = proj_mod.assemble(self.root, "s1")
        again = proj_mod.assemble(self.root, "s1")
        self.assertEqual(first, again)
        self.assertTrue(proj_mod.cache_has(self.root, "s1"))
        # External edit with bumped mtime is picked up.
        p = proj_mod.project_file(self.root, "A")
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n## Notes\n- TOKEN_EXTERNAL_EDIT_VISIBLE\n")
        st = os.stat(p)
        os.utime(p, (st.st_atime + 5, st.st_mtime + 5))
        third = proj_mod.assemble(self.root, "s1")
        self.assertIn("TOKEN_EXTERNAL_EDIT_VISIBLE", third)

    def test_authoring_protocol_rendered(self):
        proj_mod.activate(self.root, "s1", "A")
        block = proj_mod.assemble(self.root, "s1")
        self.assertIn('"scope": "project:A"', block)
        self.assertIn("[what, state, decisions, pending, paths, failed, conventions]", block)
        self.assertIn("never another project", block)
        self.assertIn("unscoped ops", block)
        self.assertNotIn('"scope": "project:B"', block)
        # Inactive sessions get no protocol at all (frozen path untouched).
        self.assertEqual(proj_mod.assemble(self.root, "nobody"), "")

    def test_cross_project_write_blocked(self):
        before_b = proj_mod.read_project(self.root, "B")
        r = apply(self.root, [{"kind": "create", "scope": "project:B", "section": "decisions",
                               "title": "x", "body": "TOKEN_SNEAK_SHOULD_FAIL"}], active="A")
        self.assertEqual(r[0]["kind"], "rejected")
        self.assertIn("cross-project", r[0]["reason"])
        self.assertEqual(proj_mod.read_project(self.root, "B"), before_b)

    def test_no_active_project_rejects(self):
        r = apply(self.root, [{"kind": "create", "scope": "project:A", "section": "state",
                               "title": "x", "body": "TOKEN_ORPHAN_SHOULD_FAIL"}], active="")
        self.assertEqual(r[0]["kind"], "rejected")
        self.assertIn("cross-project", r[0]["reason"])

    def test_global_routing_unaffected_by_active(self):
        proj_mod.activate(self.root, "s1", "A")
        r = apply(self.root, [{"kind": "create", "title": "Global cheese",
                               "types": ["identity"],
                               "body": "TOKEN_GLOBAL_CHEESE: user loves aged gouda."}],
                  active="A")
        self.assertEqual(r[0]["kind"], "create")
        self.assertNotIn("TOKEN_GLOBAL_CHEESE", proj_mod.read_project(self.root, "A"))
        self.assertIn("TOKEN_GLOBAL_CHEESE", full_text(self.root, "s1", "hello"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
