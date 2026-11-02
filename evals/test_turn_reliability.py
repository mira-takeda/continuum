"""Turn-lifecycle reliability regressions (no network, no Muse binary).

Covers the benchmark failure mechanisms:
- stale done from an abandoned turn must not terminate the next turn
  on the same session (turnId fencing);
- concurrent sessions must not eat each other's notifications;
- both approval spellings are normalized and de-duplicated;
- server-side timeout emits an explicit error (never a silent stream end);
- a disconnected/abandoned turn is cancelled + reaped (no orphan);
- /api/stop quiescence helper behaves.

Run:  python evals/test_turn_reliability.py
"""
import io
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server as server_mod
from memory import turn as turn_mod
from memory.msp import MSPHost


class ScriptedHost:
    """Fake MSP host yielding scripted notification frames."""

    provider = "echo"
    model = "fake"

    def __init__(self, sid, turn_id, frames):
        self.sid = sid
        self.turn_id = turn_id
        self.frames = list(frames)
        self.cancel_calls = []
        self.alive_flag = True

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
        return {"turnId": self.turn_id}

    def cancel_turn(self, session_id, turn_id=None):
        self.cancel_calls.append((session_id, turn_id))
        return {}

    def notifications(self, timeout=0.2, sid=None):
        if self.frames:
            return self.frames.pop(0)
        return None


def done_frame(sid, terminal, turn_id="t-new"):
    p = {"sessionId": sid, "terminal": terminal}
    if turn_id is not None:
        p["turnId"] = turn_id
    return {"method": "turn/completed", "params": p}


def text_frame(sid, text):
    return {"method": "item/delta",
            "params": {"delta": text, "sessionId": sid,
                       "itemId": "a1", "field": "text"}}


def approval_frame(sid, method, aid="ap-1"):
    return {"method": method,
            "params": {"sessionId": sid, "approvalId": aid,
                       "currentRequirementId": "r1", "toolName": "shell",
                       "subject": {"kind": "shell", "command": "x"},
                       "availableChoices": [], "rawArgs": ""}}


class TurnFencingTest(unittest.TestCase):
    def test_stale_done_does_not_end_new_turn(self):
        host = ScriptedHost("s1", "t-new", [
            done_frame("s1", "cancelled", turn_id="t-old"),
            text_frame("s1", "hello"),
            done_frame("s1", "completed", turn_id="t-new"),
        ])
        evs = list(turn_mod.stream_turn(host, "s1", "hi", "hi", timeout=5))
        kinds = [(e.get("t"), e.get("terminal")) for e in evs]
        self.assertNotIn(("done", "cancelled"), kinds)
        self.assertIn(("done", "completed"), kinds)
        self.assertEqual("".join(e.get("text", "") for e in evs
                                 if e.get("t") == "text"), "hello")

    def test_legacy_done_without_turnid_still_ends_turn(self):
        host = ScriptedHost("s1", "t-new", [
            done_frame("s1", "completed", turn_id=None),
        ])
        evs = list(turn_mod.stream_turn(host, "s1", "hi", "hi", timeout=5))
        dones = [e for e in evs if e.get("t") == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["terminal"], "completed")

    def test_approval_spellings_normalized_and_deduped(self):
        host = ScriptedHost("s1", "t-new", [
            approval_frame("s1", "approval/requested"),
            approval_frame("s1", "approval/request"),
            done_frame("s1", "completed"),
        ])
        evs = list(turn_mod.stream_turn(host, "s1", "hi", "hi", timeout=5))
        approvals = [e for e in evs if e.get("t") == "approval"]
        self.assertEqual(len(approvals), 1)
        stray = [e for e in evs if e.get("method") == "approval/request"]
        self.assertEqual(stray, [])


class DemuxTest(unittest.TestCase):
    def _host(self):
        h = MSPHost.__new__(MSPHost)
        h._notify_q = queue.Queue()
        h._stash = {}
        h._stash_lock = threading.Lock()
        h._eof = False
        return h

    def test_other_session_frames_are_stashed_not_lost(self):
        h = self._host()
        b = done_frame("B", "completed")
        a = done_frame("A", "completed")
        h._notify_q.put(b)
        h._notify_q.put(a)
        got_a = h.notifications(timeout=2, sid="A")
        got_b = h.notifications(timeout=2, sid="B")
        self.assertIs(got_a, a)
        self.assertIs(got_b, b)

    def test_eof_reaches_every_waiter(self):
        h = self._host()
        h._notify_q.put({"method": "__host_eof__", "params": {}})
        first = h.notifications(timeout=2, sid="A")
        second = h.notifications(timeout=2, sid="B")
        self.assertEqual(first["method"], "__host_eof__")
        self.assertEqual(second["method"], "__host_eof__")

    def test_legacy_pop_any_without_sid(self):
        h = self._host()
        fr = done_frame("Z", "completed")
        h._notify_q.put(fr)
        self.assertIs(h.notifications(timeout=2), fr)


class FakeWFile(io.BytesIO):
    def flush(self):
        pass


class FakeHandler:
    def __init__(self, wfile=None):
        self.wfile = wfile or FakeWFile()


class DyingWFile(FakeWFile):
    def write(self, b):
        raise IOError("client gone")


def frames_of(handler):
    out = []
    for line in handler.wfile.getvalue().decode("utf-8").splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:]))
    return out


class ServerFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="turnrel-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "app")
        os.makedirs(self.root)
        self._old = (server_mod.ROOT, server_mod.SESSIONS,
                     server_mod.MSP_SESSIONS, server_mod.TRANSCRIPTS,
                     server_mod.TURN_TIMEOUT, server_mod.REAP_TIMEOUT)
        server_mod.ROOT = self.root
        server_mod.SESSIONS = os.path.join(self.root, "data",
                                           "sessions.json")
        server_mod.MSP_SESSIONS = os.path.join(self.root, "data",
                                               "msp_sessions.json")
        server_mod.TRANSCRIPTS = os.path.join(self.root, "data",
                                              "transcripts.json")
        self.addCleanup(self._restore)
        self.bid = "relbid-%s" % time.time_ns()
        server_mod.ACTIVE_TURNS.pop(self.bid, None)

    def _restore(self):
        (server_mod.ROOT, server_mod.SESSIONS, server_mod.MSP_SESSIONS,
         server_mod.TRANSCRIPTS, server_mod.TURN_TIMEOUT,
         server_mod.REAP_TIMEOUT) = self._old
        server_mod.TURN_LOCKS.pop(self.bid, None)
        server_mod.ACTIVE_TURNS.pop(self.bid, None)

    def test_timeout_emits_explicit_error_and_reaps(self):
        from unittest import mock
        server_mod.TURN_TIMEOUT = 0.3
        server_mod.REAP_TIMEOUT = 0.2
        host = ScriptedHost("s-t", "t-t", [])
        handler = FakeHandler()
        with mock.patch.object(server_mod, "get_host", return_value=host):
            server_mod.run_turn_sse(handler, self.bid, "Hello")
        fr = frames_of(handler)
        errs = [f for f in fr if f.get("t") == "error" and f.get("timeout")]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["terminal"], "timeout")
        self.assertEqual(host.cancel_calls, [("s-t", "t-t")])
        # lock released: a follow-up turn can run
        lk = server_mod.turn_lock(self.bid)
        self.assertTrue(lk.acquire(blocking=False))
        lk.release()

    def test_disconnect_cancels_and_reaps_orphan(self):
        from unittest import mock
        server_mod.REAP_TIMEOUT = 2
        host = ScriptedHost("s-d", "t-d", [
            text_frame("s-d", "partial"),
            done_frame("s-d", "cancelled", turn_id="t-d"),
        ])
        handler = FakeHandler(DyingWFile())
        with mock.patch.object(server_mod, "get_host", return_value=host):
            server_mod.run_turn_sse(handler, self.bid, "Hello")
        self.assertEqual(host.cancel_calls, [("s-d", "t-d")])
        self.assertEqual(host.frames, [])  # orphan done consumed
        lk = server_mod.turn_lock(self.bid)
        self.assertTrue(lk.acquire(blocking=False))
        lk.release()

    def test_wait_turn_end(self):
        lk = server_mod.turn_lock(self.bid)
        self.assertTrue(server_mod._wait_turn_end(self.bid, timeout=1))
        lk.acquire()
        try:
            t0 = time.time()
            self.assertFalse(server_mod._wait_turn_end(self.bid, timeout=0.3))
            self.assertLess(time.time() - t0, 5)
        finally:
            lk.release()
        server_mod.ACTIVE_TURNS[self.bid] = "t-x"
        try:
            self.assertFalse(server_mod._wait_turn_end(self.bid, timeout=0.3))
        finally:
            server_mod.ACTIVE_TURNS.pop(self.bid, None)


if __name__ == "__main__":
    unittest.main(verbosity=1)
