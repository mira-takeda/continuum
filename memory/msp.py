"""Muse Code as the agent runtime: JSON-RPC client for `muse serve` over stdio.

Browser UI -> local Python app -> Muse Code (MSP) -> Muse Spark.
All Muse text, tool activity, approvals, questions, errors and results are
forwarded verbatim; this module never paraphrases or regenerates them.
"""
import json
import os
import queue
import random
import shutil
import subprocess
import threading
import time
import uuid

OFFICIAL_INSTALL_SH = "curl -fsSL https://dev.meta.ai/install.sh | bash"
OFFICIAL_DOCS = "https://dev.meta.ai/docs/muse-code/"
OFFICIAL_SETUP = "https://dev.meta.ai/api/auth/login/"
CLIENT_NAME = "continuum"
CLIENT_VERSION = "0.2"


def uuid7():
    ms = int(time.time() * 1000)
    rnd = random.getrandbits(74)
    val = ((ms & 0xFFFFFFFFFFFF) << 80) | (7 << 76) | (rnd & ((1 << 76) - 1))
    val &= ~(0x3 << 62)
    val |= (0x2 << 62)
    return str(uuid.UUID(int=val))


def _muse_cmd():
    exe = shutil.which("muse") or shutil.which("muse.cmd")
    return exe


def find_muse():
    return _muse_cmd()


def muse_version():
    exe = _muse_cmd()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20,
                           encoding="utf-8", shell=(os.name == "nt"))
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        return out[0][:120] if out else "muse (version unknown)"
    except Exception as e:
        return f"muse (version check failed: {e})"


def auth_status():
    """Never prints secrets: `muse usage` shows allowance or 'not logged in'."""
    exe = _muse_cmd()
    if not exe:
        return {"logged_in": False, "allowance": False, "text": "Muse Code is not installed."}
    try:
        r = subprocess.run([exe, "usage"], capture_output=True, text=True, timeout=30,
                           encoding="utf-8", shell=(os.name == "nt"))
        text = ((r.stdout or "") + (r.stderr or "")).strip()
        if "not logged in" in text.lower():
            return {"logged_in": False, "allowance": False, "text": text[:800]}
        if not text:
            return {"logged_in": False, "allowance": False,
                    "text": "Empty response from `muse usage`."}
        no_allow = "no_allowance" in text or "no subscription allowance" in text.lower()
        return {"logged_in": True, "allowance": not no_allow, "text": text[:800]}
    except Exception as e:
        return {"logged_in": False, "allowance": False, "text": f"`muse usage` failed: {e}"}


class MSPError(Exception):
    pass


def initialize_params(experimental=False):
    """initialize params. experimental=True opts into experimentalApi +
    rawLog/sessionRename grants. Default False = frozen handshake."""
    p = {"clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}}
    if experimental:
        p["capabilities"] = {"experimentalApi": True,
                             "requestedCapabilities": ["rawLog",
                                                       "sessionRename"]}
    return p


class MSPHost:
    """One `muse serve` process; multiplexes sessions; streams notifications."""

    def __init__(self, workspace_root, provider=None, model=None, extra_args=None, experimental=False):
        exe = _muse_cmd()
        if not exe:
            raise MSPError("Muse Code is not installed.")
        self.provider = provider or os.environ.get("MUSE_PROVIDER", "meta")
        self.model = model or os.environ.get("MUSE_MODEL") or None
        args = [exe, "serve", "--provider", self.provider]
        if self.model:
            args += ["--model", self.model]
        if extra_args:
            args += list(extra_args)
        # TEST-ONLY escape hatch: nested tool sandboxes cannot start inside the
        # sandboxed CI/shell used for verification. Never default on.
        if os.environ.get("MUSE_DISABLE_SANDBOX") == "1":
            args += ["--disable-sandbox"]
        self.workspace_root = workspace_root
        self.proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            shell=(os.name == "nt"), cwd=workspace_root)
        self._qid = 0
        self._qlock = threading.Lock()
        self.inbox = queue.Queue()   # parsed stdout frames
        self.errors = queue.Queue()  # stderr lines
        self._pending = {}           # rpc id -> queue
        self._notify_q = queue.Queue()  # notifications for subscribers
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._er = threading.Thread(target=self._drain_err, daemon=True)
        self._reader.start()
        self._er.start()
        res = self.call("initialize", initialize_params(experimental), timeout=30)
        self.server_info = res
        self._send({"jsonrpc": "2.0", "method": "initialized"})

    def _send(self, frame):
        self.proc.stdin.write(json.dumps(frame) + "\n")
        self.proc.stdin.flush()

    def call(self, method, params=None, timeout=60):
        with self._qlock:
            self._qid += 1
            rid = self._qid
        q = queue.Queue()
        self._pending[rid] = q
        f = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params:
            f["params"] = params
        try:
            self._send(f)
        except Exception as e:
            self._pending.pop(rid, None)
            raise MSPError(f"host send failed: {e}")
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            self._pending.pop(rid, None)
            raise MSPError(f"MSP {method} timed out after {timeout}s")
        if isinstance(resp, dict) and "error" in resp and resp["error"]:
            e = resp["error"]
            raise MSPError(f"MSP {method}: {e.get('message', e)}")
        return (resp or {}).get("result", {})

    def _drain(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    self.errors.put("non-JSON stdout: " + line[:300])
                    continue
                if "method" in d:
                    self._notify_q.put(d)
                elif "id" in d:
                    q = self._pending.pop(d["id"], None)
                    if q is not None:
                        q.put(d)
                    else:
                        self._notify_q.put(d)
                else:
                    self._notify_q.put(d)
        except Exception as e:
            self.errors.put(f"stdout drain ended: {e}")
        finally:
            self._notify_q.put({"method": "__host_eof__", "params": {}})

    def _drain_err(self):
        try:
            for line in self.proc.stderr:
                if line.strip():
                    self.errors.put(line.strip()[:500])
        except Exception:
            pass

    def notifications(self, timeout=0.2):
        try:
            return self._notify_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def start_session(self, session_id=None, approval_mode="promptUnmatched",
                      workspace_root=None):
        params = {"commandId": uuid7(),
                  "workspaceRoot": workspace_root or self.workspace_root,
                  "approvalMode": approval_mode}
        if session_id:
            params["sessionId"] = session_id
        return self.call("session/start", params, timeout=90)

    def resume_session(self, session_id):
        return self.call("session/resume", {"commandId": uuid7(), "sessionId": session_id},
                         timeout=90)

    def leave_session(self, session_id):
        try:
            return self.call("session/leave", {"commandId": uuid7(), "sessionId": session_id},
                             timeout=30)
        except MSPError:
            return {}

    def subscribe(self, session_id):
        return self.call("view/subscribe", {"sessionId": session_id}, timeout=30)

    def start_turn(self, session_id, text, display_text=None):
        return self.call("turn/start", {"commandId": uuid7(), "sessionId": session_id,
                                        "input": [{"type": "text", "text": text}],
                                        "displayText": display_text or text[:300]}, timeout=60)

    def cancel_turn(self, session_id, turn_id=None):
        p = {"commandId": uuid7(), "sessionId": session_id}
        if turn_id:
            p["turnId"] = turn_id
        try:
            return self.call("turn/cancel", p, timeout=30)
        except MSPError as e:
            return {"error": str(e)}

    def list_pending(self, session_id):
        try:
            return self.call("approval/listPending", {"sessionId": session_id}, timeout=30)
        except MSPError:
            return {}

    def decide(self, session_id, approval_id, choice_id, requirement_id, feedback=None):
        p = {"commandId": uuid7(), "sessionId": session_id, "approvalId": approval_id,
             "choiceId": choice_id, "requirementId": requirement_id}
        if feedback:
            p["feedback"] = feedback[:500]
        return self.call("approval/decide", p, timeout=60)

    def answer_input(self, session_id, user_input_id, answers):
        return self.call("userInput/answer", {"commandId": uuid7(), "sessionId": session_id,
                                              "userInputId": user_input_id, "answers": answers},
                         timeout=60)

    def read_usage(self):
        try:
            return self.call("usage/read", timeout=30)
        except MSPError:
            return {}

    def read_session(self, session_id):
        """session/read metadata (default excludeItems): no attach, no lease."""
        try:
            return self.call("session/read", {"sessionId": session_id}, timeout=30)
        except MSPError:
            return {}

    def list_tasks(self, session_id):
        """task/list: read-only background-task inventory for one session."""
        try:
            return self.call("task/list", {"sessionId": session_id}, timeout=30)
        except MSPError:
            return {}

    # ---- Pro Mode native controls (stable MSP surface) ----
    def list_models(self, session_id=None):
        try:
            p = {"sessionId": session_id} if session_id else None
            return self.call("model/list", p, timeout=30)
        except MSPError:
            return {}

    def set_model(self, session_id, model_id, provider_id=None, profile_id=None,
                  display_label=None):
        model = {"modelId": model_id}
        if provider_id:
            model["providerId"] = provider_id
        if profile_id is not None:
            model["profileId"] = profile_id
        if display_label:
            model["displayLabel"] = display_label
        return self.call("session/setModel",
                         {"commandId": uuid7(), "sessionId": session_id,
                          "model": model}, timeout=60)

    def set_reasoning_effort(self, session_id, effort):
        return self.call("session/setReasoningEffort",
                         {"commandId": uuid7(), "sessionId": session_id,
                          "reasoningEffort": effort}, timeout=60)

    def rename_session(self, session_id, name):
        return self.call("session/rename",
                         {"commandId": uuid7(), "sessionId": session_id,
                          "name": name}, timeout=60)

    def fork_session(self, session_id):
        return self.call("session/fork",
                         {"commandId": uuid7(), "sessionId": session_id,
                          "excludeItems": True}, timeout=90)

    def side_chat(self, session_id):
        return self.call("session/sideChat",
                         {"commandId": uuid7(), "sessionId": session_id},
                         timeout=90)

    def compact_session(self, session_id):
        return self.call("session/compact",
                         {"commandId": uuid7(), "sessionId": session_id},
                         timeout=60)

    def list_skills(self, session_id):
        try:
            return self.call("skill/list", {"sessionId": session_id}, timeout=30)
        except MSPError:
            return {}

    def set_skill_activation(self, session_id, selector, scope, activation):
        return self.call("skill/setActivation",
                         {"commandId": uuid7(), "sessionId": session_id,
                          "selector": selector, "scope": scope,
                          "activation": activation}, timeout=60)

    def goal_set(self, session_id, objective):
        return self.call("goal/set",
                         {"commandId": uuid7(), "sessionId": session_id,
                          "objective": objective}, timeout=60)

    def goal_edit(self, session_id, objective):
        return self.call("goal/edit",
                         {"commandId": uuid7(), "sessionId": session_id,
                          "objective": objective}, timeout=60)

    def goal_pause(self, session_id):
        return self.call("goal/pause",
                         {"commandId": uuid7(), "sessionId": session_id},
                         timeout=60)

    def goal_resume(self, session_id):
        return self.call("goal/resume",
                         {"commandId": uuid7(), "sessionId": session_id},
                         timeout=60)

    def goal_clear(self, session_id):
        return self.call("goal/clear",
                         {"commandId": uuid7(), "sessionId": session_id},
                         timeout=60)

    def set_approval_mode(self, session_id, mode):
        return self.call("session/setApprovalMode",
                         {"commandId": uuid7(), "sessionId": session_id,
                          "mode": mode}, timeout=60)

    # ---- Experimental surface (gated; may drift across Muse versions) ----
    def exp_log_streams(self, session_id):
        try:
            return self.call("log/streams", {"sessionId": session_id}, timeout=30)
        except MSPError:
            return {}

    def exp_log_page(self, session_id, stream, limit=20, from_pos=None):
        p = {"sessionId": session_id, "stream": stream, "limit": limit}
        if from_pos is not None:
            p["from"] = from_pos
        return self.call("log/page", p, timeout=30)

    def exp_profiles(self, session_id):
        try:
            return self.call("permissionProfile/list", {"sessionId": session_id},
                             timeout=30)
        except MSPError:
            return {}

    def exp_set_profile(self, session_id, profile_id):
        return self.call("session/setPermissionProfile",
                         {"commandId": uuid7(), "sessionId": session_id,
                          "profileId": profile_id}, timeout=60)

    def steer_turn(self, session_id, turn_id, text):
        """turn/steer: exact-target steering into the running turn (SS3.3)."""
        return self.call("turn/steer",
                         {"commandId": uuid7(), "sessionId": session_id,
                          "expectedTurnId": turn_id,
                          "input": [{"type": "text", "text": text}]}, timeout=60)

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                               capture_output=True, timeout=15)
            else:
                self.proc.terminate()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
