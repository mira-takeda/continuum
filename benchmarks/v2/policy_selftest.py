import os
import tempfile

import common2 as c

root = os.path.join(tempfile.gettempdir(), "v2-selftest-ws")
os.makedirs(root, exist_ok=True)
CH = [{"choiceId": "allow_once", "decision": "approved"},
      {"choiceId": "abort", "decision": "abort"}]
move = {"subject": {"kind": "shell", "command":
        'Move-Item -Path "bench-files/shopping.txt" '
        '-Destination "bench-files/groceries.txt"'},
        "rawArgs": "", "toolName": "powershell", "choices": CH}
print("move-in-root:", c.decide_approval(move, root))
evil = {"subject": {"kind": "shell", "command":
        "Remove-Item -Recurse C:\\Windows\\Temp\\v2-selftest-outside"},
        "rawArgs": "", "toolName": "powershell", "choices": CH}
print("evil:", c.decide_approval(evil, root))
read = {"subject": {"kind": "read", "path": "bench-files/seed.txt"},
        "rawArgs": "", "toolName": "read", "choices": CH}
print("read:", c.decide_approval(read, root))
nometa = {"subject": {"kind": "shell", "command": "Get-Date"},
          "rawArgs": "", "toolName": "powershell", "choices": CH}
print("nometa:", c.decide_approval(nometa, root))
absin = {"subject": {"kind": "shell", "command":
         "Move-Item %s" % (root + "\\bench-files\\a.txt")},
         "rawArgs": "", "toolName": "powershell", "choices": CH}
print("abs-in-root:", c.decide_approval(absin, root))
print("q:", c.answers_for([{"id": "q1", "options": [{"label": "A"},
      {"label": "B"}]}, {"id": "q2", "options": []}]))
