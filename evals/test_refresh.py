"""Manual memory refresh: one forced snapshot into the current session.

No LLM/MSP/network. Covers: same-session reuse, stored-memory
immutability, forced restatement, explicit supersession, return to
small deltas, project isolation, switch-then-refresh, failure safety,
unbootstrapped fallback. Run:
  python evals/test_refresh.py
"""
import json
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
import server as server_mod

BID = "rb1"
SID = "sid-refresh-1"


def apply(root, ops, turn="t0", active=None):
    return extract_mod.apply_ops(root, ops, turn, "test", active_project=active)


def refresh(root):
    index_mod.build(root)
    core_mod.rebuild(root)


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


def mkproject(root, name, marker):
    pdir = os.path.join(root, "proj-" + name)
    os.makedirs(pdir, exist_ok=True)
    proj_mod.register_project(root, name, pdir)
    with open(os.path.join(pdir, ".continuum.md"), "a",
              encoding="utf-8") as f:
        f.write("\ndecisions:\n- %s\n" % marker)
    return pdir


def snap_bytes(root):
    P = store.data_paths(root)
    out = {}
    for dp, _dn, fns in os.walk(P["entries"]):
        for fn in sorted(fns):
            with open(os.path.join(dp, fn), "rb") as f:
                out[fn] = f.read()
    for key in ("index", "core"):
        try:
            with open(P[key], "rb") as f:
                out[key] = f.read()
        except OSError:
            pass
    return out


class RefreshBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="refresh-")
        self.root = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(self.root, "data", "entries"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class PlanRefreshTest(RefreshBase):
    def test_01_same_session_merged_no_new_state(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        ret = {"opened": [{"id": e1, "status": "current", "state": "Recent",
                           "title": "Fence", "body": "Fence wood is cedar."}],
               "paths": [], "shortlist": [], "deep": 0}
        p0 = delivery_mod.plan_turn(
            self.root, SID, BID, "hi", ret, idx, "", "")
        delivery_mod.commit(self.root, SID, p0["commit"])
        pr = delivery_mod.plan_refresh(self.root, SID, BID, idx, "")
        delivery_mod.commit(self.root, SID, pr["commit"])
        with open(delivery_mod.delivery_path(self.root), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(sorted(st["sids"].keys()), [SID])
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "data", "sessions.json")))

    def test_02_stored_memory_bytes_unchanged(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        mkproject(self.root, "A", "alpha-marker-11")
        proj_mod.activate(self.root, BID, "A")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        p0 = delivery_mod.plan_turn(
            self.root, SID, BID, "hi", empty_ret(), idx,
            proj_mod.assemble(self.root, BID), "")
        delivery_mod.commit(self.root, SID, p0["commit"])
        before = snap_bytes(self.root)
        pr = delivery_mod.plan_refresh(
            self.root, SID, BID, idx, proj_mod.assemble(self.root, BID))
        delivery_mod.commit(self.root, SID, pr["commit"])
        self.assertEqual(snap_bytes(self.root), before)
        self.assertIn(e1, pr["full"])

    def test_03_restates_current_state_without_help(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        mkproject(self.root, "A", "alpha-marker-22")
        proj_mod.activate(self.root, BID, "A")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        p0 = delivery_mod.plan_turn(
            self.root, SID, BID, "hi", empty_ret(), idx,
            proj_mod.assemble(self.root, BID), "")
        delivery_mod.commit(self.root, SID, p0["commit"])
        pr = delivery_mod.plan_refresh(
            self.root, SID, BID, idx, proj_mod.assemble(self.root, BID))
        self.assertEqual(pr["mode"], "refresh")
        self.assertIn("Fence wood is cedar.", pr["full"])
        self.assertIn(e1, pr["full"])  # index gist restated
        self.assertIn("alpha-marker-22", pr["full"])
        self.assertNotIn(turn_mod.MEMORY_OPS_HELP, pr["full"])
        self.assertNotIn("<continuum-system>", pr["full"])
        self.assertGreater(pr["stats"]["items"], 0)

    def test_04_supersession_explicit(self):
        old = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        p0 = delivery_mod.plan_turn(
            self.root, SID, BID, "hi", empty_ret(), idx, "", "")
        delivery_mod.commit(self.root, SID, p0["commit"])
        new = mksupersede(self.root, old, "Fence", "Fence wood is walnut.")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        pr = delivery_mod.plan_refresh(self.root, SID, BID, idx, "")
        self.assertIn("SUPERSEDED [%s]" % old, pr["full"])
        self.assertIn('Was: "Fence wood is cedar."', pr["full"])
        self.assertIn('Now: "Fence wood is walnut."', pr["full"])
        self.assertIn(new, pr["full"])

    def test_05_next_turn_returns_to_small_delta(self):
        mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        p0 = delivery_mod.plan_turn(
            self.root, SID, BID, "hi", empty_ret(), idx, "", "")
        delivery_mod.commit(self.root, SID, p0["commit"])
        pr = delivery_mod.plan_refresh(self.root, SID, BID, idx, "")
        delivery_mod.commit(self.root, SID, pr["commit"])
        nxt = delivery_mod.plan_turn(
            self.root, SID, BID, "next", empty_ret(), idx, "", "")
        self.assertEqual(nxt["mode"], "delta-empty")
        self.assertEqual(nxt["stats"]["added_chars"], 0)

    def test_06_project_isolation(self):
        mkproject(self.root, "A", "alpha-marker-33")
        mkproject(self.root, "B", "beta-marker-33")
        proj_mod.activate(self.root, BID, "B")
        mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        p0 = delivery_mod.plan_turn(
            self.root, SID, BID, "hi", empty_ret(), idx,
            proj_mod.assemble(self.root, BID), "")
        delivery_mod.commit(self.root, SID, p0["commit"])
        pr = delivery_mod.plan_refresh(
            self.root, SID, BID, idx, proj_mod.assemble(self.root, BID))
        self.assertIn("beta-marker-33", pr["full"])
        self.assertNotIn("alpha-marker-33", pr["full"])

    def test_07_switch_then_refresh_only_new_project(self):
        mkproject(self.root, "A", "alpha-marker-44")
        mkproject(self.root, "B", "beta-marker-44")
        proj_mod.activate(self.root, BID, "A")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        p0 = delivery_mod.plan_turn(
            self.root, SID, BID, "hi", empty_ret(), idx,
            proj_mod.assemble(self.root, BID), "")
        delivery_mod.commit(self.root, SID, p0["commit"])
        proj_mod.activate(self.root, BID, "B")
        pr = delivery_mod.plan_refresh(
            self.root, SID, BID, idx, proj_mod.assemble(self.root, BID))
        self.assertIn("beta-marker-44", pr["full"])
        self.assertNotIn("alpha-marker-44", pr["full"])
        self.assertEqual(pr["commit"]["last_project"], "B")
        delivery_mod.commit(self.root, SID, pr["commit"])
        nxt = delivery_mod.plan_turn(
            self.root, SID, BID, "next", empty_ret(), idx,
            proj_mod.assemble(self.root, BID), "")
        self.assertEqual(nxt["mode"], "delta-empty")

    def test_08_unbootstrapped_refresh_bootstraps_with_help(self):
        mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        pr = delivery_mod.plan_refresh(
            self.root, SID, BID, idx, "", ops_help="OPS-HELP-PROBE")
        self.assertEqual(pr["mode"], "bootstrap")
        self.assertIn("OPS-HELP-PROBE", pr["full"])
        delivery_mod.commit(self.root, SID, pr["commit"])
        nxt = delivery_mod.plan_turn(
            self.root, SID, BID, "next", empty_ret(), idx, "", "")
        self.assertEqual(nxt["mode"], "delta-empty")


class FakeHost:
    def __init__(self):
        self.provider = "test"
        self.starts = []

    def alive(self):
        return True

    def start_session(self, **kw):
        self.starts.append(kw)
        raise AssertionError("must not create a session")


class RunRefreshTest(RefreshBase):
    def setUp(self):
        super().setUp()
        self.patched = {}
        for name, fname in (("SESSIONS", "sessions.json"),
                            ("TRANSCRIPTS", "transcripts.json"),
                            ("MSP_SESSIONS", "msp_sessions.json")):
            self.patched[name] = getattr(server_mod, name)
            setattr(server_mod, name,
                    os.path.join(self.root, "data", fname))
        self.old_root = server_mod.ROOT
        server_mod.ROOT = self.root
        self.old_stream = turn_mod.stream_turn
        server_mod.RESUMED.add(SID)

    def tearDown(self):
        turn_mod.stream_turn = self.old_stream
        server_mod.ROOT = self.old_root
        for name, val in self.patched.items():
            setattr(server_mod, name, val)
        server_mod.RESUMED.discard(SID)
        super().tearDown()

    def _seed(self):
        e1 = mkentry(self.root, "Fence", "Fence wood is cedar.")
        refresh(self.root)
        idx = index_mod.load_or_build(self.root)
        p0 = delivery_mod.plan_turn(
            self.root, SID, BID, "hi", empty_ret(), idx, "", "")
        delivery_mod.commit(self.root, SID, p0["commit"])
        server_mod.save_json(server_mod.MSP_SESSIONS,
                             {BID: {"sid": SID,
                                    "root": os.path.expanduser("~")}})
        return e1

    def test_09_runs_on_mapped_sid_without_new_session(self):
        self._seed()
        seen = {}

        def fake_stream(host, sid, full, display, timeout=None, on_idle=None):
            seen["sid"] = sid
            seen["full"] = full
            yield {"t": "turn-started", "turn_id": "t9"}
            yield {"t": "text", "text": "Memory noted."}
            yield {"t": "done", "terminal": "completed", "turnId": "t9",
                   "usage": {"promptTokens": 100, "totalTokens": 120}}

        turn_mod.stream_turn = fake_stream
        host = FakeHost()
        out = server_mod.run_refresh(BID, _host=host)
        self.assertTrue(out["ok"])
        self.assertEqual(out["native_sid"], SID)
        self.assertEqual(seen["sid"], SID)
        self.assertEqual(host.starts, [])
        self.assertIn("Fence wood is cedar.", seen["full"])
        tr = server_mod.transcript_load(server_mod.TRANSCRIPTS, BID)
        self.assertEqual(len(tr), 1)
        self.assertIn("Memory noted.", tr[0]["assistant"])

    def test_10_failure_preserves_delivery_state(self):
        self._seed()
        with open(delivery_mod.delivery_path(self.root), "rb") as f:
            before = f.read()

        def boom(host, sid, full, display, timeout=None, on_idle=None):
            raise RuntimeError("stream died")
            yield {"t": "never"}

        turn_mod.stream_turn = boom
        out = server_mod.run_refresh(BID, _host=FakeHost())
        self.assertFalse(out["ok"])
        with open(delivery_mod.delivery_path(self.root), "rb") as f:
            self.assertEqual(f.read(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
