"""Delivery bookkeeping: bootstrap once + incremental deltas. No LLM/MSP/network.

Covers: full bootstrap on new sessions, near-zero repeated context on ordinary
turns, add/update/supersede/withdraw deltas, X->Y->Z chains, attribute-scoped
supersession, project switch/return/A+B, new-session re-bootstrap, compaction
drop-detection, restart fail-safe, commit-after-acceptance, provenance. Run:
  python evals/test_delivery.py
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from memory import core as core_mod
from memory import delivery as delivery_mod
from memory import extract as extract_mod
from memory import index as index_mod
from memory import projects as proj_mod
from memory import retrieve as retrieve_mod
from memory import store
from memory import turn as turn_mod

BID = "b1"
SID = "sid-test-1"


def apply(root, ops, turn="t0", active=None):
    return extract_mod.apply_ops(root, ops, turn, "test", active_project=active)


def refresh(root):
    index_mod.build(root)
    core_mod.rebuild(root)


def turn(root, sid, bid, message, ret_override=None):
    idx = index_mod.load_or_build(root)
    if ret_override is not None:
        ret = ret_override
    else:
        ret = retrieve_mod.retrieve(root, None, message, system="S2")
    proj_section = proj_mod.assemble(root, bid)
    ops_help = proj_mod.project_ops_help(root, bid)
    return delivery_mod.plan_turn(root, sid=sid, bid=bid, message=message,
                                  ret=ret, idx=idx, proj_section=proj_section,
                                  ops_help=ops_help)


def accept(root, sid, plan):
    delivery_mod.commit(root, sid, plan["commit"])


def empty_ret():
    return {"opened": [], "paths": [], "shortlist": [], "deep": 0}


def mkentry(root, title, body, types=("fact",), source="user-stated"):
    P = store.data_paths(root)
    op = store.create(P["entries"], P["log"], title, list(types), body,
                      ["t0"], source, "test")
    return op["entry_id"]


def mksupersede(root, old_id, title, body):
    P = store.data_paths(root)
    op = store.supersede(P["entries"], P["log"], old_id, title, ["fact"], body,
                         ["t1"], "user-stated", "test")
    return op["entry_id"]


class DeliveryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="deliv-")
        self.root = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(self.root, "data", "entries"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class BootstrapTest(DeliveryBase):
    def test_01_new_session_receives_full_bootstrap_byte_identical(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        ret = {"opened": [{"id": e1, "status": "current", "state": "Recent",
                            "title": "Fence", "body": "Fence wood is cedar."}],
               "paths": [], "shortlist": [], "deep": 0}
        proj_section = proj_mod.assemble(self.root, BID)
        ops_help = proj_mod.project_ops_help(self.root, BID)
        plan = delivery_mod.plan_turn(self.root, sid=SID, bid=BID,
                                      message="hello", ret=ret, idx=idx,
                                      proj_section=proj_section, ops_help=ops_help)
        legacy = turn_mod.build_turn_text(self.root, "hello", ret, idx,
                                          proj_section, leak_guard=True)
        legacy = ("<continuum-system>\n" + ops_help + "\n</continuum-system>\n\n"
                  + legacy)
        self.assertEqual(plan["mode"], "bootstrap")
        self.assertEqual(plan["full"], legacy)
        self.assertIn("[%s]" % e1, plan["full"])

    def test_02_second_unchanged_turn_sends_message_only(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        p1 = turn(self.root, SID, BID, "hello", empty_ret())
        self.assertEqual(p1["mode"], "bootstrap")
        accept(self.root, SID, p1)
        p2 = turn(self.root, SID, BID, "how are you?", empty_ret())
        self.assertEqual(p2["mode"], "delta-empty")
        self.assertEqual(p2["full"], "USER MESSAGE:\nhow are you?")
        for banned in ("CORE (auto-derived", "Compact index:", "memory-ops",
                       "project-ops", "continuum-delta", e1):
            self.assertNotIn(banned, p2["full"])

    def test_11_new_native_session_bootstraps_again(self):
        mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        p1 = turn(self.root, SID, BID, "hello", empty_ret())
        accept(self.root, SID, p1)
        p2 = turn(self.root, "sid-other", BID, "hello again", empty_ret())
        self.assertEqual(p2["mode"], "bootstrap")
        self.assertIn("Compact index:", p2["full"])


class DeltaTest(DeliveryBase):
    def test_03_new_global_fact_sends_only_delta(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        accept(self.root, SID, turn(self.root, SID, BID, "hi", empty_ret()))
        e2 = mkentry(self.root, "Gate", "Gate lock is brass.")
        refresh(self.root)
        p = turn(self.root, SID, BID, "and the gate?", empty_ret())
        self.assertEqual(p["mode"], "delta")
        self.assertIn("ADD [%s]" % e2, p["full"])
        self.assertIn("Gate lock is brass.", p["full"])
        self.assertNotIn(e1, p["full"])
        self.assertNotIn("Compact index:", p["full"])
        self.assertNotIn("continuum-system", p["full"])

    def test_04_correction_sends_supersession_not_snapshot(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        accept(self.root, SID, turn(self.root, SID, BID, "hi", empty_ret()))
        e2 = mksupersede(self.root, e1, "Fence", "Fence wood is walnut.")
        refresh(self.root)
        p = turn(self.root, SID, BID, "fence?", empty_ret())
        self.assertIn("SUPERSEDED [%s]" % e1, p["full"])
        self.assertIn("[%s]" % e2, p["full"])
        self.assertIn("walnut", p["full"])
        self.assertIn("cedar", p["full"])  # old quoted as superseded, explicit
        self.assertNotIn("Compact index:", p["full"])
        self.assertNotIn("Memory protocol (local encyclopedia", p["full"])
        self.assertNotIn("project-ops", p["full"])

    def test_05_xyz_resolves_z_current(self):
        x = mkentry(self.root, "Fence", "Fence wood is X.")
        refresh(self.root)
        accept(self.root, SID, turn(self.root, SID, BID, "hi", empty_ret()))
        y = mksupersede(self.root, x, "Fence", "Fence wood is Y.")
        refresh(self.root)
        py = turn(self.root, SID, BID, "fence?", empty_ret())
        self.assertIn("SUPERSEDED [%s]" % x, py["full"])
        accept(self.root, SID, py)
        z = mksupersede(self.root, y, "Fence", "Fence wood is Z.")
        refresh(self.root)
        pz = turn(self.root, SID, BID, "fence now?", empty_ret())
        self.assertIn("SUPERSEDED [%s]" % y, pz["full"])
        self.assertIn("[%s]" % z, pz["full"])
        self.assertIn('Now: "Fence wood is Z."', pz["full"])
        accept(self.root, SID, pz)
        # Steady state afterwards: nothing more to say.
        p3 = turn(self.root, SID, BID, "ok", empty_ret())
        self.assertEqual(p3["mode"], "delta-empty")

    def test_06_old_token_valid_elsewhere_not_falsely_removed(self):
        fence = mkentry(self.root, "Fence", "Fence wood is cedar.")
        beams = mkentry(self.root, "Beams", "Shed beams are cedar.")
        refresh(self.root)
        accept(self.root, SID, turn(self.root, SID, BID, "hi", empty_ret()))
        new_id = mksupersede(self.root, fence, "Fence", "Fence wood is walnut.")
        refresh(self.root)
        p = turn(self.root, SID, BID, "fence?", empty_ret())
        self.assertIn("SUPERSEDED [%s]" % fence, p["full"])
        self.assertIn("[%s]" % new_id, p["full"])
        # Scoped to the fence entry only: no blanket removal of "cedar",
        # no withdraw/update lines for the beams entry.
        self.assertNotIn("WITHDRAWN [%s]" % beams, p["full"])
        self.assertNotIn("UPDATE [%s]" % beams, p["full"])
        self.assertIn("apply only to the named entries", p["full"])

    def test_07_retrieved_unchanged_entry_not_resent(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        opened = {"opened": [{"id": e1, "status": "current", "state": "Recent",
                               "title": "Fence", "body": "Fence wood is cedar."}],
                  "paths": [], "shortlist": [], "deep": 0}
        p1 = turn(self.root, SID, BID, "fence?", opened)
        self.assertEqual(p1["mode"], "bootstrap")
        self.assertIn("Fence wood is cedar.", p1["full"])
        accept(self.root, SID, p1)
        p2 = turn(self.root, SID, BID, "fence again?", opened)
        self.assertEqual(p2["mode"], "delta")
        self.assertNotIn("Fence wood is cedar.", p2["full"])
        self.assertIn("RELEVANT (already provided for this turn): [%s] Fence." % e1,
                      p2["full"])

    def test_16_provenance_citations_preserved(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        # Retrieval still runs fully: Lab opened/paths intact.
        ret = retrieve_mod.retrieve(self.root, None, "fence wood?", system="S2")
        self.assertIn("opened", ret)
        self.assertIn("paths", ret)
        p1 = turn(self.root, SID, BID, "hi", empty_ret())
        self.assertIn("ONLY inside the ```memory-ops block", p1["full"])
        accept(self.root, SID, p1)
        e2 = mkentry(self.root, "Gate", "Gate lock is brass.")
        refresh(self.root)
        p2 = turn(self.root, SID, BID, "gate?", empty_ret())
        self.assertIn("[%s]" % e2, p2["full"])  # new entries keep [id] refs
        self.assertIn(e1, p1["full"])  # bootstrap carried delivered ids


class ProjectDeltaTest(DeliveryBase):
    def setUp(self):
        super().setUp()
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(self.ws)
        self.paths = {}
        for name in ("Alfa", "Beta"):
            p = os.path.join(self.ws, "proj" + name)
            os.makedirs(p)
            self.paths[name] = p
        proj_mod.set_workspace(self.root, self.ws)
        for name, path in self.paths.items():
            proj_mod.register_project(self.root, name, path)
            proj_mod.init_project(self.root, name)
        apply(self.root, [{"kind": "create", "scope": "project:Alfa",
                            "section": "decisions", "title": "x",
                            "body": "TOKEN_ALFA_APPLE: Alfa uses Apple SDK."}],
              active="Alfa")
        apply(self.root, [{"kind": "create", "scope": "project:Beta",
                            "section": "decisions", "title": "x",
                            "body": "TOKEN_BETA_BOOST: Beta uses Boost."}],
              active="Beta")

    def test_08_switch_isolated(self):
        proj_mod.activate(self.root, BID, "Alfa")
        p1 = turn(self.root, SID, BID, "start", empty_ret())
        self.assertEqual(p1["mode"], "bootstrap")
        self.assertIn("TOKEN_ALFA_APPLE", p1["full"])
        accept(self.root, SID, p1)
        proj_mod.activate(self.root, BID, "Beta")
        p2 = turn(self.root, SID, BID, "switching", empty_ret())
        self.assertIn("TOKEN_BETA_BOOST", p2["full"])
        self.assertNotIn("TOKEN_ALFA_APPLE", p2["full"])

    def test_09_return_reactivates_current_a(self):
        proj_mod.activate(self.root, BID, "Alfa")
        accept(self.root, SID, turn(self.root, SID, BID, "start", empty_ret()))
        proj_mod.activate(self.root, BID, "Beta")
        accept(self.root, SID, turn(self.root, SID, BID, "hi beta", empty_ret()))
        proj_mod.activate(self.root, BID, "Alfa")
        p3 = turn(self.root, SID, BID, "back to alfa", empty_ret())
        self.assertIn('"Alfa" is active again', p3["full"])
        self.assertNotIn("TOKEN_BETA_BOOST", p3["full"])
        # Full A text not repeated when unchanged.
        self.assertNotIn("TOKEN_ALFA_APPLE", p3["full"])

    def test_10_explicit_ab_lookup(self):
        proj_mod.activate(self.root, BID, "Alfa")
        accept(self.root, SID, turn(self.root, SID, BID, "start", empty_ret()))
        p2 = turn(self.root, SID, BID, "compare Alfa and Beta", empty_ret())
        self.assertIn("TOKEN_BETA_BOOST", p2["full"])  # mentioned B included
        self.assertNotIn("TOKEN_ALFA_APPLE", p2["full"])  # active A unchanged


class SafetyTest(DeliveryBase):
    def test_12_compaction_drop_invalidates(self):
        mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        accept(self.root, SID, turn(self.root, SID, BID, "hi", empty_ret()))
        self.assertEqual(delivery_mod.note_usage(self.root, SID, 100000), "ok")
        p2 = turn(self.root, SID, BID, "ok", empty_ret())
        self.assertEqual(p2["mode"], "delta-empty")
        accept(self.root, SID, p2)
        self.assertEqual(delivery_mod.note_usage(self.root, SID, 60000),
                         "compacted")
        p3 = turn(self.root, SID, BID, "after compact", empty_ret())
        self.assertEqual(p3["mode"], "bootstrap")

    def test_12b_small_drop_keeps_state(self):
        mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        accept(self.root, SID, turn(self.root, SID, BID, "hi", empty_ret()))
        self.assertEqual(delivery_mod.note_usage(self.root, SID, 100000), "ok")
        self.assertEqual(delivery_mod.note_usage(self.root, SID, 90000), "ok")
        p2 = turn(self.root, SID, BID, "ok", empty_ret())
        self.assertEqual(p2["mode"], "delta-empty")

    def test_12c_missing_usage_preserves_baseline(self):
        mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        accept(self.root, SID, turn(self.root, SID, BID, "hi", empty_ret()))
        delivery_mod.note_usage(self.root, SID, 100000)
        self.assertEqual(delivery_mod.note_usage(self.root, SID, None), "missing")
        # Drop still measured against the preserved baseline.
        self.assertEqual(delivery_mod.note_usage(self.root, SID, 60000),
                         "compacted")

    def test_13_restart_fails_safe(self):
        mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        accept(self.root, SID, turn(self.root, SID, BID, "hi", empty_ret()))
        old_run = delivery_mod.RUN_ID
        try:
            delivery_mod.RUN_ID = "simulated-restart-run-id"
            p2 = turn(self.root, SID, BID, "after restart", empty_ret())
            self.assertEqual(p2["mode"], "bootstrap")
        finally:
            delivery_mod.RUN_ID = old_run

    def test_14_failed_preaccept_does_not_mark_delivered(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        accept(self.root, SID, turn(self.root, SID, BID, "hi", empty_ret()))
        e2 = mkentry(self.root, "Gate", "Gate lock is brass.")
        refresh(self.root)
        p1 = turn(self.root, SID, BID, "gate?", empty_ret())
        self.assertIn("ADD [%s]" % e2, p1["full"])
        # No commit (turn/start never accepted) -> same delta repeats.
        p2 = turn(self.root, SID, BID, "gate?", empty_ret())
        self.assertEqual(p2["full"], p1["full"])
        self.assertIn("ADD [%s]" % e2, p2["full"])

    def test_15_cancel_after_accept_keeps_state(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        accept(self.root, SID, turn(self.root, SID, BID, "hi", empty_ret()))
        e2 = mkentry(self.root, "Gate", "Gate lock is brass.")
        refresh(self.root)
        p1 = turn(self.root, SID, BID, "gate?", empty_ret())
        accept(self.root, SID, p1)  # accepted; later cancel changes nothing
        p2 = turn(self.root, SID, BID, "ok", empty_ret())
        self.assertEqual(p2["mode"], "delta-empty")
        self.assertNotIn(e2, p2["full"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
