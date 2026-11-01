"""Project lifecycle ops: create/register/activate + safe parent resolution.

Backend-owned project creation for Muse-driven ```project-ops blocks.
Tmp dirs only; no MSP, no network. Run:
  python evals/test_project_ops.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from memory import projects as proj


def mktree():
    tmp = tempfile.mkdtemp(prefix="projops-")
    root = os.path.join(tmp, "app")
    parent = os.path.join(tmp, "docs")
    os.makedirs(root)
    os.makedirs(parent)
    return tmp, root, parent


class ProjectOpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root, self.parent = mktree()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_block(self):
        text = ('hello\n```project-ops\n[{"kind": "activate_project", "name": "A"}]\n```\n')
        self.assertEqual(proj.parse_project_block(text),
                         [{"kind": "activate_project", "name": "A"}])
        self.assertEqual(proj.parse_project_block("no block here"), [])
        self.assertEqual(proj.parse_project_block("```project-ops\n{bad\n```"), [])
        self.assertEqual(proj.parse_project_block("```project-ops\n{}\n```"), [])

    def test_documents_folder_safe(self):
        d = proj.documents_folder()
        self.assertTrue(d is None or os.path.isdir(d))

    def test_resolve_parent(self):
        with mock.patch.object(proj, "documents_folder", return_value=self.parent):
            self.assertEqual(proj.resolve_parent(self.root, "documents"), self.parent)
            self.assertEqual(proj.resolve_parent(self.root, "Documents"), self.parent)
        proj.set_workspace(self.root, self.parent)
        self.assertEqual(proj.resolve_parent(self.root, "workspace"), self.parent)
        self.assertEqual(proj.resolve_parent(self.root, self.parent), self.parent)
        with self.assertRaises(ValueError):
            proj.resolve_parent(self.root, "relative/guess")
        with self.assertRaises(ValueError):
            proj.resolve_parent(self.root, os.path.join(self.parent, "nope"))
        with mock.patch.object(proj, "documents_folder", return_value=None):
            with self.assertRaises(ValueError):
                proj.resolve_parent(self.root, "documents")

    def test_create_happy_path(self):
        r = proj.create_project(self.root, "Cablerios", self.parent)
        dest = os.path.join(self.parent, "Cablerios")
        self.assertEqual(r, {"name": "Cablerios", "path": dest})
        self.assertTrue(os.path.isdir(dest))
        self.assertEqual(proj.list_projects(self.root)["Cablerios"], dest)
        self.assertTrue(os.path.exists(os.path.join(dest, proj.FILENAME)))

    def test_create_conflicts(self):
        r = proj.create_project(self.root, "A", self.parent)
        self.assertNotIn("adopted", r)
        r2 = proj.create_project(self.root, "A", self.parent)  # adopts
        self.assertTrue(r2.get("adopted"))
        self.assertEqual(proj.list_projects(self.root)["A"], r["path"])
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        with self.assertRaises(ValueError):
            proj.create_project(self.root, "A", other)  # name taken elsewhere
        for bad in ["", "has space", "a/b", "..", "x" * 65, None]:
            with self.assertRaises(ValueError):
                proj.create_project(self.root, bad, self.parent)

    def test_create_adopt_edges(self):
        f = os.path.join(self.parent, "NotDir")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("x")
        with self.assertRaises(ValueError):
            proj.create_project(self.root, "NotDir", self.parent)
        sneaky = os.path.join(self.root, "SneakyAdopt")
        os.makedirs(sneaky)
        with self.assertRaises(ValueError):
            proj.create_project(self.root, "SneakyAdopt", self.root)
        r = proj.create_project(self.root, "SneakyAdopt", self.root,
                                allow_repo_child=True)
        self.assertTrue(r.get("adopted"))

    def test_create_repo_child_blocked(self):
        with self.assertRaises(ValueError):
            proj.create_project(self.root, "Sneaky", self.root)
        r = proj.create_project(self.root, "Sneaky", self.root, allow_repo_child=True)
        self.assertTrue(os.path.isdir(r["path"]))

    def test_apply_ops(self):
        out = proj.apply_project_ops(self.root, [
            {"kind": "create_project", "name": "P1", "parent": self.parent,
             "activate": True}], "b1")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "project-created")
        self.assertTrue(out[0]["activated"])
        self.assertEqual(proj.active_for(self.root, "b1"), "P1")

    def test_apply_ops_create_existing_adopts(self):
        dd = os.path.join(self.parent, "Ad1")
        os.makedirs(dd)
        out = proj.apply_project_ops(self.root, [
            {"kind": "create_project", "name": "Ad1", "parent": self.parent,
             "activate": True}], "ba")
        self.assertEqual(out[0]["kind"], "project-registered")
        self.assertTrue(out[0]["activated"])
        self.assertEqual(proj.active_for(self.root, "ba"), "Ad1")
        self.assertTrue(os.path.exists(os.path.join(dd, proj.FILENAME)))

    def test_apply_ops_register_activate(self):
        d = os.path.join(self.parent, "P2")
        os.makedirs(d)
        out = proj.apply_project_ops(self.root, [
            {"kind": "register_project", "name": "P2", "path": d},
            {"kind": "activate_project", "name": "P2"}], "b2")
        self.assertEqual([o["kind"] for o in out],
                         ["project-registered", "project-activated"])
        self.assertEqual(proj.active_for(self.root, "b2"), "P2")
        self.assertTrue(os.path.exists(os.path.join(d, proj.FILENAME)))

    def test_apply_ops_rejections_and_cap(self):
        out = proj.apply_project_ops(self.root, [
            {"kind": "activate_project", "name": "Ghost"},
            {"kind": "explode", "name": "x"},
            "not-a-dict",
            {"kind": "activate_project", "name": "Ghost"}], "b3")
        self.assertEqual(len(out), proj.MAX_PROJECT_OPS)
        self.assertTrue(all(o["kind"] == "rejected" for o in out))

    def test_ops_help(self):
        proj.create_project(self.root, "H", self.parent)
        proj.activate(self.root, "b9", "H")
        h = proj.project_ops_help(self.root, "b9")
        for needle in ("project-ops", "Active project: H", "create_project",
                       "Continuum app repo", "Documents:"):
            self.assertIn(needle, h)
        h2 = proj.project_ops_help(self.root, "nobody")
        self.assertIn("Active project: none", h2)

    def test_detect_create(self):
        ops = proj.detect_project_request(
            "Create a new project called Cablerios in Documents.")
        self.assertEqual(ops, [{"kind": "create_project", "name": "Cablerios",
                                "parent": "documents", "activate": True}])
        ops = proj.detect_project_request("create a project named X1 in workspace")
        self.assertEqual(ops[0]["parent"], "workspace")
        ops = proj.detect_project_request(
            f"Create project called Abs1 in {self.parent}")
        self.assertEqual(ops[0]["parent"], self.parent)

    def test_detect_create_strict(self):
        for msg in ["Create a new project plan for Q3.",
                    "Create a new project called  in Documents.",
                    "Remind me to create a project called Later.",
                    "What projects do I have in Documents?",
                    "Create a new folder called X in Documents.",
                    "Create a new project called Bad Name! in Documents.",
                    "hello, how are you?"]:
            self.assertEqual(proj.detect_project_request(msg), [], msg)

    def test_detect_create_unresolved_location(self):
        ops = proj.detect_project_request(
            "Create a new project called Far in Mordor.")
        self.assertEqual(len(ops), 1)
        out = proj.apply_project_ops(self.root, ops, "b0")
        self.assertEqual(out[0]["kind"], "rejected")

    def test_detect_switch(self):
        ops = proj.detect_project_request("Switch to the Cablerios project.")
        self.assertEqual(ops, [{"kind": "activate_project", "name": "Cablerios"}])
        ops = proj.detect_project_request("Please switch back to MyProj project now.")
        self.assertEqual(ops, [{"kind": "activate_project", "name": "MyProj"}])
        for msg in ["Switch to dark mode.", "Switch the project to TypeScript.",
                    "Let's switch topics."]:
            self.assertEqual(proj.detect_project_request(msg), [], msg)

    def test_detect_create_spanish(self):
        ops = proj.detect_project_request(
            "Crea un proyecto llamado AuditoriaCinco en Documentos. Dentro crea x.")
        self.assertEqual(ops, [{"kind": "create_project", "name": "AuditoriaCinco",
                                "parent": "documents", "activate": True}])
        ops = proj.detect_project_request(
            "Crear un proyecto llamado MiApp en mis Documentos")
        self.assertEqual(ops[0]["name"], "MiApp")
        self.assertEqual(ops[0]["parent"], "documents")

    def test_detect_create_name_only(self):
        ops = proj.detect_project_request("Crea un proyecto llamado SoloNombre")
        self.assertEqual(ops, [{"kind": "create_project", "name": "SoloNombre",
                                "parent": None, "activate": True}])
        ops = proj.detect_project_request("Create a project called JustName.")
        self.assertEqual(ops[0]["parent"], None)
        ops = proj.detect_project_request("Create a project called X1 in workspace")
        self.assertEqual(ops[0]["parent"], "workspace")

    def test_detect_switch_spanish(self):
        ops = proj.detect_project_request("cambia al proyecto DemoX")
        self.assertEqual(ops, [{"kind": "activate_project", "name": "DemoX"}])
        self.assertEqual(proj.detect_project_request("hola que tal"), [])

    def test_desired_root_independent_of_active_project(self):
        import server as server_mod
        home = os.path.abspath(os.path.expanduser("~"))
        self.assertEqual(server_mod._desired_root("bX"), home)
        proj.create_project(self.root, "Q", self.parent)
        proj.activate(self.root, "bX", "Q")
        self.assertEqual(server_mod._desired_root("bX"), home)

    def test_bookkeeping_registers_existing(self):
        os.makedirs(os.path.join(self.parent, "Bk1"))
        with mock.patch.object(proj, "documents_folder", return_value=self.parent):
            ops = proj.bookkeeping_ops(
                self.root, "bk", "Create a project called Bk1 in Documents.")
        self.assertEqual(ops, [{"kind": "register_project", "name": "Bk1",
                                "path": os.path.join(self.parent, "Bk1"),
                                "activate": True}])
        out = proj.apply_project_ops(self.root, ops, "bk")
        self.assertEqual(out[0]["kind"], "project-registered")
        self.assertTrue(out[0]["activated"])
        self.assertEqual(proj.active_for(self.root, "bk"), "Bk1")
        self.assertTrue(os.path.exists(
            os.path.join(self.parent, "Bk1", proj.FILENAME)))

    def test_bookkeeping_never_creates(self):
        with mock.patch.object(proj, "documents_folder", return_value=self.parent):
            self.assertEqual(proj.bookkeeping_ops(
                self.root, "bk", "Create a project called Ghost in Documents."), [])
            self.assertEqual(proj.bookkeeping_ops(
                self.root, "bk", "hello there"), [])
            self.assertEqual(proj.bookkeeping_ops(
                self.root, "bk", "Create a project called Far in Mordor."), [])
        self.assertFalse(os.path.exists(os.path.join(self.parent, "Ghost")))

    def test_bookkeeping_skips_active(self):
        proj.create_project(self.root, "Bk2", self.parent)
        proj.activate(self.root, "bk", "Bk2")
        with mock.patch.object(proj, "documents_folder", return_value=self.parent):
            self.assertEqual(proj.bookkeeping_ops(
                self.root, "bk", "Create a project called Bk2 in Documents."), [])

    def test_bookkeeping_name_only_adopts_new(self):
        dest = os.path.join(self.parent, "Solo1")
        os.makedirs(dest)
        ops = proj.bookkeeping_ops(self.root, "bk",
            "Crea un proyecto llamado Solo1", new_dirs={"Solo1": dest})
        self.assertEqual(ops, [{"kind": "register_project", "name": "Solo1",
                                "path": dest, "activate": True}])

    def test_bookkeeping_name_only_ignores_preexisting(self):
        dest = os.path.join(self.parent, "Viejo1")
        os.makedirs(dest)
        self.assertEqual(proj.bookkeeping_ops(
            self.root, "bk", "Crea un proyecto llamado Viejo1"), [])
        self.assertEqual(proj.bookkeeping_ops(
            self.root, "bk", "Crea un proyecto llamado Viejo1",
            new_dirs={"Otro": os.path.join(self.parent, "Otro")}), [])

    def test_bookkeeping_location_fallback_to_new(self):
        dest = os.path.join(self.parent, "Fb1")
        os.makedirs(dest)
        with mock.patch.object(proj, "documents_folder", return_value=self.tmp):
            ops = proj.bookkeeping_ops(self.root, "bk",
                "Create a project called Fb1 in Documents.",
                new_dirs={"Fb1": dest})
        self.assertEqual(ops[0]["path"], dest)

    def test_strip_project_block(self):
        clean, block = proj.strip_project_block(
            "text\n```project-ops\n[1]\n```tail")
        self.assertEqual(clean, "texttail")
        self.assertIn("project-ops", block)
        self.assertEqual(proj.strip_project_block("plain"), ("plain", None))
        self.assertEqual(proj.strip_project_block("a ```project-ops\n[unclosed"),
                         ("a ```project-ops\n[unclosed", None))

    def test_strip_project_block_duplicate_echo(self):
        two = ("Listo.\n```project-ops\n[{\"kind\":\"create_project\"}]\n```\n"
               "```project-ops\n[{\"kind\":\"create_project\"}]\n```")
        clean, block = proj.strip_project_block(two)
        self.assertEqual(clean, "Listo.")
        self.assertIn("create_project", block)

    def test_parse_project_block_first_only(self):
        two = ("```project-ops\n[{\"kind\":\"activate_project\",\"name\":\"A\"}]\n```\n"
               "```project-ops\n[{\"kind\":\"activate_project\",\"name\":\"B\"}]\n```")
        ops = proj.parse_project_block(two)
        self.assertEqual(ops, [{"kind": "activate_project", "name": "A"}])

    def test_snapshot_diff(self):
        with mock.patch.object(proj, "documents_folder", return_value=self.parent):
            before = proj.snapshot_dirs(self.root)
            os.makedirs(os.path.join(self.parent, "Snap2"))
            new = proj.diff_new_dirs(before, self.root)
        self.assertEqual(new.get("Snap2"), os.path.join(self.parent, "Snap2"))

    def test_eval_shared_core_untouched(self):
        # The frozen eval path builds turns without the product ops block:
        # extra_before_user="" must stay byte-identical (no import-time coupling).
        from memory import turn as turn_mod
        import inspect
        src = inspect.getsource(turn_mod.build_turn_text)
        self.assertNotIn("project-ops", src)
        self.assertNotIn("project_ops_help", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
