"""Bug 5: full turn composition with a duplicated project-ops echo.

Fake MSP host yields scripted frames (text with the fence TWICE, then
turn/completed). Asserts the real run_turn_sse pipeline: streamed chat
text carries no fence, exactly one project-ops metadata frame (first
block), the project op applies once (adopt), and the persisted
transcript is clean. Tmp dirs only; no network, no Muse binary. Run:
  python evals/test_turn_compose.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server as server_mod

FENCE = ('```project-ops\n[{"kind": "create_project", "name": "E2E", '
         '"parent": "PARENT", "activate": true}]\n```')
AGENT_TEXT = "Listo, carpeta creada.\n" + FENCE + "\n" + FENCE


class FakeWFile(io.BytesIO):
    def flush(self):
        pass


class FakeHandler:
    def __init__(self):
        self.wfile = FakeWFile()


class FakeHost:
    provider = "echo"
    model = "fake"
    alive_flag = True

    def __init__(self, sid, text):
        self.sid = sid
        self.text = text
        self.calls = []

    def alive(self):
        return self.alive_flag

    def list_pending(self, sid):
        return {}

    def start_session(self, session_id=None, approval_mode=None,
                      workspace_root=None):
        return {"session": {"sessionId": self.sid}}

    def resume_session(self, sid):
        return {}

    def subscribe(self, sid):
        return {}

    def start_turn(self, sid, text, display_text=None):
        self.calls.append(("start_turn", sid))
        return {"turnId": "t-e2e"}

    def notifications(self, timeout=0.2):
        if self.text is not None:
            t, self.text = self.text, None
            return {"method": "item/delta",
                    "params": {"delta": t, "sessionId": self.sid,
                               "itemId": "a1", "field": "text"}}
        if "done" not in self.calls:
            self.calls.append("done")
            return {"method": "turn/completed",
                    "params": {"sessionId": self.sid, "terminal": "completed"}}
        return None


def frames_of(handler):
    out = []
    for line in handler.wfile.getvalue().decode("utf-8").splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:]))
    return out


class TurnComposeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="turncompose-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "app")
        self.parent = os.path.join(self.tmp, "docs")
        os.makedirs(self.root)
        os.makedirs(os.path.join(self.parent, "E2E"))
        self._old = (server_mod.ROOT, server_mod.SESSIONS,
                     server_mod.MSP_SESSIONS, server_mod.TRANSCRIPTS)
        server_mod.ROOT = self.root
        server_mod.SESSIONS = os.path.join(self.root, "data", "sessions.json")
        server_mod.MSP_SESSIONS = os.path.join(self.root, "data",
                                               "msp_sessions.json")
        server_mod.TRANSCRIPTS = os.path.join(self.root, "data",
                                              "transcripts.json")
        self.addCleanup(self._restore)
        if "turncompose" in server_mod.TURN_LOCKS:
            del server_mod.TURN_LOCKS["turncompose"]
        server_mod.ACTIVE_TURNS.pop("turncompose", None)

    def _restore(self):
        (server_mod.ROOT, server_mod.SESSIONS, server_mod.MSP_SESSIONS,
         server_mod.TRANSCRIPTS) = self._old

    def test_double_fence_turn(self):
        from unittest import mock
        text = AGENT_TEXT.replace("PARENT", self.parent.replace(os.sep, "/"))
        host = FakeHost("s-e2e", text)
        handler = FakeHandler()
        with mock.patch.object(server_mod, "get_host", return_value=host):
            server_mod.run_turn_sse(handler, "turncompose",
                                    "Create a project called E2E")
        fr = frames_of(handler)
        kinds = [f.get("t") for f in fr]
        self.assertIn("started", kinds)
        self.assertIn("done", kinds)
        shown = "".join(f.get("text", "") for f in fr if f.get("t") == "text")
        self.assertEqual(shown, "Listo, carpeta creada.")
        pops = [f for f in fr if f.get("t") == "project-ops"]
        self.assertEqual(len(pops), 1)
        self.assertIn("create_project", pops[0]["block"])
        self.assertNotIn("```project-ops", shown)
        writes = [f for f in fr if f.get("t") == "writes"][0]
        kinds_w = [w.get("kind") for w in writes["writes"]]
        self.assertIn("project-registered", kinds_w)
        self.assertTrue(os.path.isfile(os.path.join(
            self.parent, "E2E", ".continuum.md")))
        turns = server_mod.transcript_load(server_mod.TRANSCRIPTS,
                                           "turncompose")
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["assistant"], "Listo, carpeta creada.")
        self.assertEqual(turns[0]["user"], "Create a project called E2E")


if __name__ == "__main__":
    unittest.main(verbosity=1)
