"""Model-free validation of scale harness logic (no benchmark prompts)."""
import os
import tempfile

import scale_common as c

root = os.path.join(tempfile.gettempdir(), "scale-selftest-ws")
os.makedirs(root, exist_ok=True)
CH = [{"choiceId": "allow_once", "decision": "approved"},
      {"choiceId": "abort", "decision": "abort"}]

# 1. in-workspace shell, JSON-escaped, workspaceRoot metadata must not poison
ev = {"subject": {"kind": "shell",
                  "command": "Move-Item bench-files-scale/a.txt b.txt",
                  "workspaceRoot": os.path.dirname(root)},
      "rawArgs": "", "toolName": "powershell", "choices": CH}
ch, label, conf = c.decide_approval(ev, root)
assert (ch, label, conf) == ("allow_once", "allow-confined", True), \
    (ch, label, conf)

# 2. genuinely out-of-workspace delete -> deny
ev2 = {"subject": {"kind": "shell",
                   "command": "Remove-Item -Force 'C:\\Windows\\Temp\\scale-selftest-outside.txt'",
                   "workspaceRoot": os.path.dirname(root)},
       "rawArgs": "", "toolName": "powershell", "choices": CH}
ch, label, conf = c.decide_approval(ev2, root)
assert (ch, label, conf) == ("abort", "deny-unconfined", False), \
    (ch, label, conf)

# 3. no paths -> allow
ev3 = {"subject": {"kind": "shell", "command": "Get-Date"},
       "rawArgs": "", "toolName": "powershell", "choices": CH}
assert c.decide_approval(ev3, root)[1] == "allow-confined"

# 4. matcher + rules
assert c.match("alpine yarn", "The shed paint is ALPINE YARN.")
assert c.match("alpine yarn", "yarn, alpine style")
assert not c.match("alpine yarn", "the yarn is nice")
assert c.match("teal", "Teal.")
assert c.score_rule({"rule": "present", "words": ["a b"]}, "x a b y") == 1
assert c.score_rule({"rule": "corrected", "new": "n", "old": "o"},
                    "it is n now") == 1
assert c.score_rule({"rule": "corrected", "new": "n", "old": "o"},
                    "it is not n") == 0
assert c.score_rule({"rule": "present_absent", "present": ["a"],
                     "absent": ["b"]}, "has a") == 1
assert c.score_rule({"rule": "present_absent", "present": ["a"],
                     "absent": ["b"]}, "has a and b") == 0
assert c.score_rule({"rule": "ab", "a": "Acorn", "b": "Birch",
                     "va": "va1", "vb": "vb2"},
                    "Acorn: va1. Birch: vb2.") == 1
assert c.score_rule({"rule": "ab", "a": "Acorn", "b": "Birch",
                     "va": "va1", "vb": "vb2"},
                    "Acorn: vb2. Birch: va1.") == 0
assert c.score_rule({"rule": "clarify", "absent": ["x", "y"]},
                    "Which one?") == 1
assert c.score_rule({"rule": "clarify", "absent": ["x", "y"]},
                    "Use x.") == 0

# 5. snapshot helpers on temp dirs
d = tempfile.mkdtemp()
open(os.path.join(d, "a.md"), "w").write("x")
os.makedirs(os.path.join(d, ".agents"))
md, ag = c.snapshot_light(d)
assert (md, ag) == (1, True)
full = c.snapshot_full(d)
assert any(x.startswith("a.md:") for x in full), full

# 6. frozen files load + counts
import json
HERE = os.path.dirname(os.path.abspath(__file__))
t1 = json.load(open(os.path.join(HERE, "scale_test1.json"), encoding="utf-8"))
t2 = json.load(open(os.path.join(HERE, "scale_test2.json"), encoding="utf-8"))
assert len(t1["turns"]) == 250 and len(t1["probes"]) == 20
assert len(t2["facts"]) == 250 and len(t2["teaching"]) == 60
assert len(t2["probes"]) == 48
print("selftest ok: policy, matcher, rules, snapshots, frozen counts")
