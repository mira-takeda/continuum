"""Fix 2: native steer with explicit client queue. Run:
  python evals/test_ux.py
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server as server_mod
from memory.msp import MSPError


class FakeWFile(io.BytesIO):
    def flush(self):
        pass


class FakeHandler:
    def __init__(self):
        self.wfile = FakeWFile()


class FakeHost:
    def __init__(self, refuse=False):
        self.refuse = refuse
        self.steers = []

    def steer_turn(self, sid, turn_id, text):
        if self.refuse:
            raise MSPError("turn not running")
        self.steers.append((sid, turn_id, text))
        return {"status": "accepted"}


def frames_of(handler):
    out = []
    for line in handler.wfile.getvalue().decode("utf-8").splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:]))
    return out


class SteerOrQueueTest(unittest.TestCase):
    def setUp(self):
        self.bid = "test-steer-%s" % self._testMethodName
        self.lk = server_mod.turn_lock(self.bid)
        self.addCleanup(server_mod.ACTIVE_TURNS.pop, self.bid, None)
        mp = mock.patch.object(server_mod, "load_json",
                               return_value={self.bid: {"sid": "s9"}})
        mp.start()
        self.addCleanup(mp.stop)

    def hold(self):
        self.assertTrue(self.lk.acquire(blocking=False))
        self.addCleanup(self._release)

    def _release(self):
        try:
            self.lk.release()
        except RuntimeError:
            pass

    def test_idle_delegates_to_normal_turn(self):
        h = FakeHandler()
        with mock.patch.object(server_mod, "run_turn_sse") as rt:
            server_mod.run_turn_sse_or_steer(h, self.bid, "hola")
        rt.assert_called_once_with(h, self.bid, "hola", attachments=None)

    def test_busy_steers_with_expected_turn(self):
        self.hold()
        server_mod.ACTIVE_TURNS[self.bid] = "t1"
        host, h = FakeHost(), FakeHandler()
        server_mod.run_turn_sse_or_steer(h, self.bid, "segundo", _host=host)
        self.assertEqual(host.steers, [("s9", "t1", "segundo")])
        fr = frames_of(h)
        self.assertEqual(fr[0]["t"], "steered")
        self.assertEqual(fr[0]["turn_id"], "t1")
        self.assertEqual((fr[1]["t"], fr[1]["terminal"]), ("done", "steered"))

    def test_refused_steer_answers_queued(self):
        self.hold()
        server_mod.ACTIVE_TURNS[self.bid] = "t1"
        host, h = FakeHost(refuse=True), FakeHandler()
        server_mod.run_turn_sse_or_steer(h, self.bid, "segundo", _host=host)
        fr = frames_of(h)
        self.assertEqual([f["t"] for f in fr], ["queued", "done"])
        self.assertEqual(fr[1]["terminal"], "queued")

    def test_no_turn_id_queues_without_steer_call(self):
        self.hold()  # pre-turn window: lock held, no turn-started yet
        host, h = FakeHost(), FakeHandler()
        server_mod.run_turn_sse_or_steer(h, self.bid, "segundo", _host=host)
        self.assertEqual(host.steers, [])
        self.assertEqual([f["t"] for f in frames_of(h)], ["queued", "done"])

    def test_host_down_answers_error(self):
        self.hold()
        server_mod.ACTIVE_TURNS[self.bid] = "t1"
        h = FakeHandler()
        with mock.patch.object(server_mod, "get_host",
                               side_effect=MSPError("host down")):
            server_mod.run_turn_sse_or_steer(h, self.bid, "segundo")
        fr = frames_of(h)
        self.assertEqual(fr[0]["t"], "error")


if __name__ == "__main__":
    unittest.main(verbosity=1)
