"""Pro Mode phase 2: read-only panels. Deterministic, no MSP, no network. Run:
  python evals/test_promode.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from memory import turn as turn_mod
from memory import msp as msp_mod
import server as server_mod


def nf(method, params, sid="s1"):
    return turn_mod.norm_frame({"method": method, "params": params}, sid)


class NormFrameProTest(unittest.TestCase):
    def test_todos_wholesale(self):
        items = [{"status": "inProgress", "text": "Write tests"},
                 {"status": "pending", "text": "Docs"}]
        ev = nf("session/todoListChanged",
                {"sessionId": "s1", "items": items, "revision": 3,
                 "sourceTool": "todo_write"})
        self.assertEqual(ev["t"], "todos")
        self.assertEqual(ev["items"], items)  # verbatim, whole list
        self.assertEqual(ev["revision"], 3)
        self.assertEqual(ev["sourceTool"], "todo_write")

    def test_todos_empty_is_cleared(self):
        ev = nf("session/todoListChanged",
                {"sessionId": "s1", "items": [], "revision": 4,
                 "sourceTool": "todo_write"})
        self.assertIsNotNone(ev)
        self.assertEqual(ev["items"], [])

    def test_todos_foreign_session_dropped(self):
        self.assertIsNone(nf("session/todoListChanged",
                             {"sessionId": "other", "items": []}))

    def test_usage_changed(self):
        u = {"tier": "pro", "window": {"used": 1}}
        ev = nf("usage/changed", {"usage": u})
        self.assertEqual(ev, {"t": "usage-changed", "usage": u})

    def test_existing_frames_unchanged(self):
        self.assertEqual(
            nf("session/tokenUsage", {"modelId": "m", "promptTokens": 5,
                                      "totalTokens": 9})["t"], "usage")
        self.assertEqual(
            nf("session/contextUsage", {"windowTokens": 1})["t"], "context")
        self.assertEqual(
            nf("session/statusChanged", {"status": "running"})["t"], "status")

    def test_summarize_ignores_pro_types(self):
        s = turn_mod.summarize(
            [{"t": "todos", "items": []}, {"t": "usage-changed", "usage": {}},
             {"t": "context"}, {"t": "status"},
             {"t": "done", "terminal": "completed"}])
        self.assertFalse(s["timed_out"])
        self.assertEqual(s["terminal"], "completed")


class FakeHost:
    def __init__(self, fail=()):
        self.fail = set(fail)

    def read_session(self, sid):
        if "session" in self.fail:
            raise RuntimeError("boom-session")
        return {"session": {"sessionId": sid, "status": "idle"}}

    def list_tasks(self, sid):
        if "tasks" in self.fail:
            raise RuntimeError("boom-tasks")
        return {"tasks": [{"taskId": "t1", "kind": "tool",
                           "status": "inProgress"}]}

    def read_usage(self):
        if "usage" in self.fail:
            raise RuntimeError("boom-usage")
        return {"usage": {"tier": "pro"}}

    def list_pending(self, sid):
        if "pending" in self.fail:
            raise RuntimeError("boom-pending")
        return {"pending": []}


class ProPanelsStateTest(unittest.TestCase):
    def test_passthrough(self):
        out = server_mod.pro_panels_state(FakeHost(), "s9")
        self.assertEqual(out["msp_session"], "s9")
        self.assertTrue(out["host_alive"])
        self.assertEqual(out["session"]["session"]["status"], "idle")
        self.assertEqual(len(out["tasks"]["tasks"]), 1)
        self.assertEqual(out["usage"]["usage"]["tier"], "pro")
        self.assertEqual(out["pending"], {"pending": []})

    def test_no_sid_empties(self):
        out = server_mod.pro_panels_state(FakeHost(), None)
        self.assertIsNone(out["msp_session"])
        for k in ("session", "tasks", "usage", "pending"):
            self.assertEqual(out[k], {})

    def test_no_host_empties(self):
        out = server_mod.pro_panels_state(None, "s1")
        self.assertFalse(out["host_alive"])
        for k in ("session", "tasks", "usage", "pending"):
            self.assertEqual(out[k], {})

    def test_section_failure_isolated(self):
        out = server_mod.pro_panels_state(FakeHost(fail=("tasks",)), "s1")
        self.assertIn("unavailable", out["tasks"])
        self.assertIn("session", out["session"])  # others intact
        self.assertIn("tier", out["usage"]["usage"])

    def test_all_fail_never_raises(self):
        out = server_mod.pro_panels_state(
            FakeHost(fail=("session", "tasks", "usage", "pending")), "s1")
        for k in ("session", "tasks", "usage", "pending"):
            self.assertIn("unavailable", out[k])


class RecordingHost(FakeHost):
    def __init__(self):
        super().__init__()
        self.calls = []

    def _rec(self, name, *a):
        self.calls.append((name,) + a)
        return {"okOp": name}

    def list_models(self, sid=None):
        return self._rec("list_models", sid)

    def set_model(self, sid, mid, prov=None, prof=None, label=None):
        return self._rec("set_model", sid, mid, prov, prof, label)

    def set_reasoning_effort(self, sid, eff):
        return self._rec("set_reasoning_effort", sid, eff)

    def rename_session(self, sid, name):
        return self._rec("rename_session", sid, name)

    def fork_session(self, sid):
        return self._rec("fork_session", sid)

    def side_chat(self, sid):
        return self._rec("side_chat", sid)

    def compact_session(self, sid):
        return self._rec("compact_session", sid)

    def list_skills(self, sid):
        return self._rec("list_skills", sid)

    def set_skill_activation(self, sid, sel, scope, act):
        return self._rec("set_skill_activation", sid, sel, scope, act)

    def goal_set(self, sid, obj):
        return self._rec("goal_set", sid, obj)

    def goal_edit(self, sid, obj):
        return self._rec("goal_edit", sid, obj)

    def goal_pause(self, sid):
        return self._rec("goal_pause", sid)

    def goal_resume(self, sid):
        return self._rec("goal_resume", sid)

    def goal_clear(self, sid):
        return self._rec("goal_clear", sid)

    def set_approval_mode(self, sid, mode):
        return self._rec("set_approval_mode", sid, mode)

    def exp_log_streams(self, sid):
        return self._rec("exp_log_streams", sid)

    def exp_log_page(self, sid, stream, limit=20, from_pos=None):
        return self._rec("exp_log_page", sid, stream, limit, from_pos)

    def exp_profiles(self, sid):
        return self._rec("exp_profiles", sid)

    def exp_set_profile(self, sid, pid):
        return self._rec("exp_set_profile", sid, pid)


class MismatchHost(RecordingHost):
    def read_session(self, sid):
        return {"session": {"sessionId": "different"}}


class ProControlTest(unittest.TestCase):
    def pc(self, op, args):
        h = RecordingHost()
        r = server_mod.pro_control(h, "s1", op, args)
        return h, r

    def test_unknown_op(self):
        with self.assertRaises(ValueError):
            server_mod.pro_control(RecordingHost(), "s1", "nope", {})

    def test_set_model(self):
        h, r = self.pc("setModel", {"modelId": "m1", "providerId": "meta",
                                    "displayLabel": "L1"})
        self.assertEqual(h.calls, [("set_model", "s1", "m1", "meta", None, "L1")])
        self.assertEqual(r, {"okOp": "set_model"})
        with self.assertRaises(ValueError):
            self.pc("setModel", {"modelId": "  "})

    def test_effort_closed_vocab(self):
        h, _ = self.pc("setReasoningEffort", {"effort": "high"})
        self.assertEqual(h.calls, [("set_reasoning_effort", "s1", "high")])
        with self.assertRaises(ValueError):
            self.pc("setReasoningEffort", {"effort": "turbo"})

    def test_rename(self):
        h, _ = self.pc("rename", {"name": "  My Session "})
        self.assertEqual(h.calls, [("rename_session", "s1", "My Session")])
        with self.assertRaises(ValueError):
            self.pc("rename", {"name": ""})
        with self.assertRaises(ValueError):
            self.pc("rename", {"name": "x" * 201})

    def test_skill_activation(self):
        h, _ = self.pc("skillActivation", {"selector": "fix-bug",
                                            "scope": "user",
                                            "activation": "off"})
        self.assertEqual(h.calls, [("set_skill_activation", "s1", "fix-bug",
                                    "user", "off")])
        with self.assertRaises(ValueError):
            self.pc("skillActivation", {"selector": "", "scope": "user",
                                        "activation": "on"})
        with self.assertRaises(ValueError):
            self.pc("skillActivation", {"selector": "s", "scope": "global",
                                        "activation": "on"})
        with self.assertRaises(ValueError):
            self.pc("skillActivation", {"selector": "s", "scope": "user",
                                        "activation": "maybe"})

    def test_goals(self):
        h, _ = self.pc("goalSet", {"objective": " Ship v1 "})
        self.assertEqual(h.calls, [("goal_set", "s1", "Ship v1")])
        with self.assertRaises(ValueError):
            self.pc("goalSet", {"objective": "   "})
        h, _ = self.pc("goalEdit", {"objective": "v2"})
        self.assertEqual(h.calls, [("goal_edit", "s1", "v2")])
        for op, meth in (("goalPause", "goal_pause"),
                         ("goalResume", "goal_resume"),
                         ("goalClear", "goal_clear"),
                         ("fork", "fork_session"),
                         ("sideChat", "side_chat"),
                         ("compact", "compact_session"),
                         ("listModels", "list_models"),
                         ("listSkills", "list_skills")):
            h, r = self.pc(op, {})
            self.assertEqual(h.calls, [(meth, "s1")])
            self.assertEqual(r, {"okOp": meth})

    def test_approval_mode(self):
        h, r = self.pc("setApprovalMode", {"mode": "allowAll"})
        self.assertEqual(h.calls, [("set_approval_mode", "s1", "allowAll")])
        self.assertEqual(r, {"okOp": "set_approval_mode"})
        with self.assertRaises(ValueError):
            self.pc("setApprovalMode", {"mode": "auto"})
        with self.assertRaises(ValueError):
            self.pc("setApprovalMode", {})

    def test_list_pending(self):
        h, r = self.pc("listPending", {})
        self.assertEqual(r, {"pending": []})  # FakeHost passthrough

    def test_exp_log_page(self):
        st = {"id": "s1", "kind": "run"}
        h, r = self.pc("expLogPage", {"stream": st, "limit": 20})
        self.assertEqual(h.calls, [("exp_log_page", "s1", st, 20, None)])
        h, _ = self.pc("expLogPage", {"stream": st, "limit": 5,
                                      "from": {"id": "e", "sequence": 2}})
        self.assertEqual(h.calls[0][3], 5)
        self.assertEqual(h.calls[0][4], {"id": "e", "sequence": 2})
        with self.assertRaises(ValueError):
            self.pc("expLogPage", {"stream": {"id": "x"}, "limit": 20})
        with self.assertRaises(ValueError):
            self.pc("expLogPage", {"stream": st, "limit": 0})
        with self.assertRaises(ValueError):
            self.pc("expLogPage", {"stream": st, "limit": 101})

    def test_exp_profiles(self):
        h, r = self.pc("expProfiles", {})
        self.assertEqual(h.calls, [("exp_profiles", "s1")])
        h, r = self.pc("expSetProfile", {"profileId": ":ask-me"})
        self.assertEqual(h.calls, [("exp_set_profile", "s1", ":ask-me")])
        with self.assertRaises(ValueError):
            self.pc("expSetProfile", {"profileId": ""})
        h, r = self.pc("expLogStreams", {})
        self.assertEqual(h.calls, [("exp_log_streams", "s1")])

    def test_initialize_params(self):
        d = msp_mod.initialize_params()
        self.assertEqual(sorted(d.keys()), ["clientInfo"])  # frozen default
        e = msp_mod.initialize_params(True)
        self.assertTrue(e["capabilities"]["experimentalApi"])
        self.assertEqual(e["capabilities"]["requestedCapabilities"],
                         ["rawLog", "sessionRename"])

    def test_msp_error_bubbles(self):
        from memory.msp import MSPError

        class Boom(RecordingHost):
            def compact_session(self, sid):
                raise MSPError("host down")

        with self.assertRaises(MSPError):
            server_mod.pro_control(Boom(), "s1", "compact", {})


class ProAttachTest(unittest.TestCase):
    def test_attach_ok(self):
        m = {}
        r = server_mod.pro_attach(m, "b1", "s9", RecordingHost())
        self.assertEqual(r["attached"], "s9")
        self.assertEqual(m["b1"]["sid"], "s9")
        self.assertIn("root", m["b1"])

    def test_attach_rejects_unconfirmed(self):
        with self.assertRaises(ValueError):
            server_mod.pro_attach({}, "b1", "s9", MismatchHost())
        with self.assertRaises(ValueError):
            server_mod.pro_attach({}, "b1", "  ", RecordingHost())


if __name__ == "__main__":
    unittest.main(verbosity=1)
