"""Scale-benchmark frozen artifact generator (seeded, deterministic).

Outputs (frozen + hashed into the protocol):
  scale_test1.json  250 content turns + 20 checkpoint probes
  scale_test2.json  250 facts + 60 teaching turns + 48 probes
Run once:  python gen_scale.py
"""
import itertools
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- Test 1: single-word values (61 needed) ----
T1_WORDS = [
    "ochre", "cream", "rust", "moss", "clay", "stone", "brick", "amber",
    "jade", "coral", "azure", "olive", "khaki", "maroon", "turquoise",
    "beige", "charcoal", "ivory", "bronze", "peach", "cobalt",
    "cedar", "balsa", "oak", "pine", "birch", "walnut", "maple", "ash",
    "elm", "cherry", "poplar", "hickory",
    "copper", "brass", "steel", "glass", "ceramic", "canvas", "leather",
    "cork", "bamboo", "granite", "marble",
    "hammer", "chisel", "saw", "drill", "clamp", "level", "pliers",
    "thyme", "basil", "dill", "parsley", "chives", "oregano", "cilantro",
    "lavender", "marigold",
    "finch", "sparrow", "robin", "thrush", "lark", "dove", "starling",
    "warbler", "nuthatch",
    "apples", "pears", "grapes", "cheese", "bread", "honey", "oats",
    "rice", "beans",
]

# ---- Test 2: two-word values from disjoint sets (245 combos needed) ----
T2_ADJ = ["alpine", "arctic", "baltic", "cinder", "desert", "ember", "flint",
          "glacier", "harbor", "island", "jungle", "lagoon", "meadow",
          "nordic", "ocean", "prairie", "quarry", "river", "summit",
          "tundra", "valley", "willow", "canyon", "delta", "fjord", "grove",
          "heath", "inlet", "dune", "lunar"]
T2_NOUN = ["beacon", "bridge", "cabin", "drum", "easel", "fence", "globe",
           "hammock", "igloo", "jacket", "kettle", "lantern", "magnet",
           "needle", "oven", "paddle", "quilt", "radio", "saddle", "tent",
           "urn", "vest", "wheel", "yarn", "anchor", "bench", "button",
           "compass", "dial", "engine"]

# ---- Test 2 globals: fixed single-word values (25) ----
GLOBALS = [
    ("measurement units", "centimeters"),
    ("list style", "numbered"),
    ("time format", "24-hour"),
    ("greeting", "brief"),
    ("summary length", "trio"),
    ("distance units", "paces"),
    ("volume units", "liters"),
    ("weight units", "grams"),
    ("work start", "dawn"),
    ("work end", "dusk"),
    ("notebook", "paper"),
    ("calendar", "wall"),
    ("reminder tone", "gentle"),
    ("file naming", "lowercase"),
    ("backup day", "sunday"),
    ("sketch style", "pencil"),
    ("label maker", "embosser"),
    ("plant water", "tuesday"),
    ("tool oil", "winter"),
    ("spare key", "lockbox"),
    ("password book", "vault"),
    ("plant food", "pellets"),
    ("grill fuel", "propane"),
    ("lantern oil", "citronella"),
    ("quiet hours", "afternoon"),
]

T1_ATTRS = ["shed paint", "fence wood", "gate lock", "path material",
            "birdseed mix", "tool brand", "bench finish", "planter soil",
            "hose length", "lamp timer"]
T2_ATTRS = ["shed paint", "fence wood", "gate lock", "path material",
            "light timer", "tool brand", "birdbath style", "trellis height",
            "compost bin", "rain barrel", "mailbox post", "doorbell chime",
            "window boxes", "porch stain", "step light", "hedge trimmer"]
PROJECTS = ["Acorn", "Birch", "Cedar", "Dogwood", "Elm"]
CONFLICT_ATTRS = ["shed paint", "fence wood", "gate lock", "path material",
                  "light timer", "tool brand"]

# Words used by V1/V2 probes/answers: never reuse as values.
BANNED = {"teal", "terracotta", "navy", "east", "kestrel", "meridian",
          "bullets", "hike", "birdseed", "camera"}

BRIEF = " Reply in one or two sentences."
PROBE_BRIEF = " Answer briefly in one sentence."


def check_values(values):
    low = [v.lower() for v in values]
    assert len(set(low)) == len(low), "duplicate values"
    for v in low:
        assert v not in BANNED, f"banned value {v!r}"
        for w in v.split():
            assert w not in BANNED, f"banned word {w!r} in {v!r}"
    for i, a in enumerate(low):
        for j, b in enumerate(low):
            if i != j:
                assert a not in b, f"substring collision: {a!r} in {b!r}"


# ============================ TEST 1 ============================

def gen_test1():
    rng = random.Random(20261102)
    pool = list(T1_WORDS)
    rng.shuffle(pool)
    vals = iter(pool)
    facts = {}
    fids = []
    fact_turns = [2, 4, 6, 8, 10] + list(range(15, 251, 5))
    for n, t in enumerate(fact_turns, 1):
        fid = f"F{n:03d}"
        facts[fid] = {"id": fid, "turn": t, "attr": T1_ATTRS[(n - 1) % 10],
                      "value": next(vals), "history": []}
        fids.append(fid)
    corr_spec = [(9, "F002"), (33, "F006"), (58, "F010"), (83, "F013"),
                 (118, "F020"), (153, "F027"), (193, "F035"), (228, "F042")]
    corrections = {}
    for cturn, fid in corr_spec:
        f = facts[fid]
        assert f["turn"] < cturn, (fid, cturn)
        new = next(vals)
        corrections[cturn] = {"target": fid, "old": f["value"], "new": new}
        f["history"].append({"turn": cturn, "old": f["value"], "new": new})
        f["value"] = new
    check_values([f["value"] for f in facts.values()]
                 + [c["old"] for c in corrections.values()])
    file_turns = [24, 49, 74, 99, 124, 149, 174, 199, 224, 249]
    ref_turns = [17, 37, 67, 97, 137, 177, 217, 247]
    file_ops = ["Create a file named bench-files-scale/note.txt containing "
                "exactly: hello.",
                "Append a line with the word world to "
                "bench-files-scale/note.txt.",
                "Read bench-files-scale/note.txt and list its lines.",
                "Create bench-files-scale/todo.txt with exactly: sweep.",
                "Append a line with the word mop to "
                "bench-files-scale/todo.txt.",
                "Read bench-files-scale/todo.txt and list its lines.",
                "Create bench-files-scale/idea.txt with exactly: skylight.",
                "Append a line with the word awning to "
                "bench-files-scale/idea.txt.",
                "Read bench-files-scale/idea.txt and list its lines.",
                "List the files in bench-files-scale/."]
    turns = []
    fact_by_turn = {f["turn"]: fid for fid, f in facts.items()}
    for t in range(1, 251):
        if t in fact_by_turn:
            fid = fact_by_turn[t]
            f = facts[fid]
            orig = f["history"][0]["old"] if f["history"] else f["value"]
            turns.append({"n": t, "kind": "fact", "ref": fid,
                          "text": f"Remember for later: our {f['attr']} is "
                                  f"{orig}." + BRIEF})
        elif t in corrections:
            c = corrections[t]
            f = facts[c["target"]]
            turns.append({"n": t, "kind": "correction", "ref": c["target"],
                          "text": f"Correction: our {f['attr']} is {c['new']}, "
                                  f"not {c['old']}." + BRIEF})
        elif t in file_turns:
            turns.append({"n": t, "kind": "fileop",
                          "text": file_ops[file_turns.index(t)]
                          + " Reply in one sentence."})
        elif t in ref_turns:
            cands = [fid for fid in fids if facts[fid]["turn"] < t]
            fid = cands[-1]
            f = facts[fid]
            turns.append({"n": t, "kind": "reference", "ref": fid,
                          "text": "Quick check: what did we say our "
                                  f"{f['attr']} was?" + BRIEF})
        else:
            q = [f"If a shelf holds {t % 5 + 2} boxes per row and 3 rows, "
                 f"how many boxes fit?",
                 "Suggest a cheerful name for a garden shed.",
                 f"Is {t * 3} divisible by 3? Explain in one sentence.",
                 "Name one advantage of hand tools over power tools.",
                 "What shape holds water best: a bowl or a plate? Why?"]
            turns.append({"n": t, "kind": "filler",
                          "text": q[t % 5] + BRIEF})
    assert len(turns) == 250

    def current(fid):
        return facts[fid]["value"]

    def probe(pid, after, kind, fid, indirect=False):
        f = facts[fid]
        assert f["turn"] <= after, (pid, fid, after)
        if indirect:
            para = {"shed paint": "What color did we pick for the garden "
                                  "storage structure?",
                    "fence wood": "Which timber did we choose for the yard "
                                  "boundary?",
                    "gate lock": "What secures the yard entrance?",
                    "path material": "What is the walkway made of?",
                    "birdseed mix": "What do we feed the garden birds?",
                    "tool brand": "Which maker do we buy hand tools from?",
                    "bench finish": "How is the garden seat coated?",
                    "planter soil": "What fills our plant containers?",
                    "hose length": "How far does our watering line reach?",
                    "lamp timer": "When do the garden lights switch on?"}
            text = para[f["attr"]] + PROBE_BRIEF
            for w in f["attr"].split() + [f["value"]]:
                assert w.lower() not in text.lower(), (pid, w)
            rule = {"rule": "present", "words": [f["value"]]}
        elif kind == "corrected":
            hist = [h for h in f["history"] if h["turn"] <= after]
            assert hist, (pid, fid)
            h = hist[-1]
            text = f"What is our current {f['attr']}?" + PROBE_BRIEF
            rule = {"rule": "corrected", "new": h["new"], "old": h["old"]}
        else:
            text = f"What is our {f['attr']}?" + PROBE_BRIEF
            rule = {"rule": "present", "words": [current(fid)]}
            assert current(fid).lower() not in text.lower(), pid
        return {"id": pid, "after": after, "kind": kind, "ref": fid,
                "text": text, "expected": rule}

    by_turn = {f["turn"]: fid for fid, f in facts.items()}
    probes = [
        probe("T1-P10-recent", 10, "recent", by_turn[10]),
        probe("T1-P10-medium", 10, "medium", by_turn[6]),
        probe("T1-P10-old", 10, "old", by_turn[2]),
        probe("T1-P10-corrected", 10, "corrected", "F002"),
        probe("T1-P10-indirect", 10, "indirect", by_turn[8], indirect=True),
        probe("T1-P50-recent", 50, "recent", by_turn[50]),
        probe("T1-P50-medium", 50, "medium", by_turn[30]),
        probe("T1-P50-old", 50, "old", by_turn[2]),
        probe("T1-P50-corrected", 50, "corrected", "F006"),
        probe("T1-P50-indirect", 50, "indirect", by_turn[40], indirect=True),
        probe("T1-P100-recent", 100, "recent", by_turn[100]),
        probe("T1-P100-medium", 100, "medium", by_turn[60]),
        probe("T1-P100-old", 100, "old", by_turn[2]),
        probe("T1-P100-corrected", 100, "corrected", "F013"),
        probe("T1-P100-indirect", 100, "indirect", by_turn[75], indirect=True),
        probe("T1-P250-recent", 250, "recent", by_turn[250]),
        probe("T1-P250-medium", 250, "medium", by_turn[125]),
        probe("T1-P250-old", 250, "old", by_turn[2]),
        probe("T1-P250-corrected", 250, "corrected", "F042"),
        probe("T1-P250-indirect", 250, "indirect", by_turn[150],
              indirect=True),
    ]
    return {"seed": 20261102, "facts": facts, "corrections":
            {str(k): v for k, v in corrections.items()},
            "turns": turns, "probes": probes}


# ============================ TEST 2 ============================

def gen_test2():
    rng = random.Random(77031)
    combos = [f"{a} {n}" for a, n in
              itertools.product(T2_ADJ, T2_NOUN)]
    rng.shuffle(combos)
    vals = iter(combos)
    facts = {}
    n = 0

    def add(project, attr):
        nonlocal n
        n += 1
        fid = f"{project[0]}-{n:03d}"
        facts[fid] = {"id": fid, "project": project, "attr": attr,
                      "scope": "project", "value": next(vals),
                      "history": []}
        return fid

    conflict_ids = {}
    for attr in CONFLICT_ATTRS:
        for p in PROJECTS:
            fid = add(p, attr)
            conflict_ids.setdefault(attr, {})[p] = fid
    uniq_attrs = T2_ATTRS[6:] + [f"custom detail {i}" for i in range(1, 60)]
    for p in PROJECTS:
        for a in rng.sample(uniq_attrs, 39):
            add(p, a)
    for attr, value in GLOBALS:
        n += 1
        fid = f"G-{n:03d}"
        facts[fid] = {"id": fid, "project": None, "attr": attr,
                      "scope": "global", "value": value, "history": []}
    assert len(facts) == 250, len(facts)
    # ---- teaching batches (project-pure or global-pure) ----
    batches = []
    per_project = {p: [fid for fid, f in facts.items()
                       if f["project"] == p] for p in PROJECTS}
    assert all(len(v) == 45 for v in per_project.values()), \
        {p: len(v) for p, v in per_project.items()}
    first5 = CONFLICT_ATTRS[:5]
    for p in PROJECTS:
        ids = per_project[p]
        head = [conflict_ids[a][p] for a in first5]
        rest = [fid for fid in ids if fid not in head]
        rng.shuffle(rest)
        per_project[p] = head + rest
    gids = [fid for fid, f in facts.items() if f["scope"] == "global"]
    assert len(gids) == 25
    bid = 0

    def batch(level, kind, project, fids):
        nonlocal bid
        assert len(fids) == 5, (kind, len(fids))
        batches.append({"id": f"B{bid:02d}", "level": level, "kind": kind,
                        "project": project, "fids": fids})
        bid += 1

    for p in PROJECTS:
        batch(1, "facts", p, per_project[p][:5])
    for p, k in [("Acorn", 1), ("Birch", 1), ("Cedar", 1), ("Dogwood", 1),
                 ("Elm", 1), ("Acorn", 2), ("Birch", 2), ("Cedar", 2),
                 ("Dogwood", 2), ("Elm", 2), ("Acorn", 3), ("Birch", 3)]:
        batch(2, "facts", p, per_project[p][k * 5:k * 5 + 5])
    for g in range(3):
        batch(2, "globals", None, gids[g * 5:g * 5 + 5])
    plan3 = ([("Acorn", k) for k in range(4, 9)]
             + [("Birch", k) for k in range(4, 9)]
             + [("Cedar", k) for k in range(3, 9)]
             + [("Dogwood", k) for k in range(3, 9)]
             + [("Elm", k) for k in range(3, 9)])
    assert len(plan3) == 28, len(plan3)
    for p, k in plan3:
        batch(3, "facts", p, per_project[p][k * 5:k * 5 + 5])
    for g in range(3, 5):
        batch(3, "globals", None, gids[g * 5:g * 5 + 5])
    taught = [fid for b in batches for fid in b["fids"]]
    assert sorted(taught) == sorted(facts.keys()), "batch coverage"
    batch_of = {}
    for i, b in enumerate(batches):
        for fid in b["fids"]:
            batch_of[fid] = i
    # ---- corrections: 15 Y (3/project) + 5 Z (1/project) ----
    early = [fid for b in batches[:12] for fid in b["fids"]]
    early_uniq = [fid for fid in early
                  if facts[fid]["scope"] == "project"
                  and facts[fid]["attr"] not in CONFLICT_ATTRS]
    by_proj = {p: [fid for fid in early_uniq if facts[fid]["project"] == p]
               for p in PROJECTS}
    assert all(len(v) >= 3 for v in by_proj.values()), \
        {p: len(v) for p, v in by_proj.items()}
    y_facts, z_facts = [], []
    for p in PROJECTS:
        pick = rng.sample(by_proj[p], 3)
        y_facts.extend(pick)
        z_facts.append(pick[0])
    corrections = {}
    for fid in y_facts:
        corrections[fid] = {"Y": next(vals),
                            "Z": next(vals) if fid in z_facts else None}
    check_values([f["value"] for f in facts.values()]
                 + [c["Y"] for c in corrections.values()]
                 + [c["Z"] for c in corrections.values() if c["Z"]])
    for p in PROJECTS:
        pf = [fid for fid in y_facts if facts[fid]["project"] == p]
        assert len(pf) == 3 and all(batch_of[f] < 12 for f in pf)
        batches.append({"id": f"BY-{p}", "level": 2, "kind": "correct-Y",
                        "project": p, "fids": pf})
    for p in PROJECTS:
        pf = [fid for fid in z_facts if facts[fid]["project"] == p]
        assert len(pf) == 1
        batches.append({"id": f"BZ-{p}", "level": 3, "kind": "correct-Z",
                        "project": p, "fids": pf})
    order = sorted(range(len(batches)), key=lambda i: batches[i]["level"])
    teaching = []
    for seq, i in enumerate(order, 1):
        b = batches[i]
        lines = []
        for j, fid in enumerate(b["fids"], 1):
            f = facts[fid]
            if b["kind"] in ("facts", "globals"):
                lines.append(f"{j}. {f['attr']}: {f['value']}.")
            elif b["kind"] == "correct-Y":
                lines.append(f"{j}. {f['attr']} is now "
                             f"{corrections[fid]['Y']} (previously "
                             f"{f['value']}).")
            else:
                lines.append(f"{j}. {f['attr']} is now "
                             f"{corrections[fid]['Z']} (previously "
                             f"{corrections[fid]['Y']}, originally "
                             f"{f['value']}).")
        head = {"facts": f"Project {b['project']} \u2014 remember these "
                         f"decisions:",
                "globals": "General preferences for all our chats "
                           "\u2014 remember:",
                "correct-Y": f"Corrections for project {b['project']} "
                             "\u2014 remember the new values:",
                "correct-Z": f"Further corrections for project "
                             f"{b['project']}:"}[b["kind"]]
        teaching.append({"seq": seq, "batch": b["id"], "level": b["level"],
                         "kind": b["kind"], "project": b["project"],
                         "fids": b["fids"],
                         "text": head + "\n" + "\n".join(lines)
                         + "\nAcknowledge briefly in one sentence."})
    assert len(teaching) == 60, len(teaching)
    for fid, c in corrections.items():
        facts[fid]["history"] = [{"Y": c["Y"], "Z": c["Z"]}]

    def val(fid, level):
        f = facts[fid]
        if fid in corrections:
            c = corrections[fid]
            if level >= 3 and c["Z"]:
                return c["Z"], c["Y"]
            return c["Y"], f["value"]
        return f["value"], None

    def probe(pid, level, kind, project=None, fids=(), text=None, rule=None):
        return {"id": pid, "level": level, "kind": kind, "project": project,
                "fids": list(fids), "text": text + PROBE_BRIEF, "rule": rule}

    def q_present(fid, level, who):
        f = facts[fid]
        v, _ = val(fid, level)
        scope = f" for {f['project']}" if who == "project" else ""
        text = f"What did we decide about {f['attr']}{scope}?"
        assert v.lower() not in text.lower(), (fid, v)
        return text, {"rule": "present", "words": [v]}

    probes = []
    l1ids = [fid for b in batches if b["level"] == 1 for fid in b["fids"]]
    assert len(l1ids) == 25
    for i, fid in enumerate(l1ids[-4:]):
        t, r = q_present(fid, 1, "project")
        probes.append(probe(f"T2-L1-local-{i}", 1, "project-local",
                            facts[fid]["project"], [fid], t, r))
    for i, fid in enumerate(l1ids[:2]):
        t, r = q_present(fid, 1, "project")
        probes.append(probe(f"T2-L1-oldest-{i}", 1, "oldest",
                            facts[fid]["project"], [fid], t, r))
    for i, fid in enumerate(l1ids[-2:]):
        t, r = q_present(fid, 1, "project")
        probes.append(probe(f"T2-L1-newest-{i}", 1, "newest",
                            facts[fid]["project"], [fid], t, r))
    for i, (attr, pa, pb) in enumerate([("shed paint", "Acorn", "Birch"),
                                        ("fence wood", "Cedar", "Dogwood")]):
        fa, fb = conflict_ids[attr][pa], conflict_ids[attr][pb]
        va, _ = val(fa, 1)
        vb, _ = val(fb, 1)
        probes.append(probe(
            f"T2-L1-conflict-{i}", 1, "conflicting", pa, [fa, fb],
            f"What is the {attr} for {pa}?",
            {"rule": "present_absent", "present": [va], "absent": [vb]}))
    fa = conflict_ids["shed paint"]["Acorn"]
    fb = conflict_ids["shed paint"]["Birch"]
    va, _ = val(fa, 1)
    vb, _ = val(fb, 1)
    probes.append(probe(
        "T2-L1-AB", 1, "compare", None, [fa, fb],
        "Compare Acorn and Birch: what is the shed paint for each?",
        {"rule": "ab", "a": "Acorn", "b": "Birch", "va": va, "vb": vb}))
    shed_vals = [val(conflict_ids["shed paint"][p], 1)[0] for p in PROJECTS]
    probes.append(probe(
        "T2-L1-amb", 1, "ambiguous", None, [], "What color should we paint "
        "the shed?",
        {"rule": "clarify", "absent": shed_vals}))
    for level in (2, 3):
        lv_ids = [fid for b in teaching if b["level"] == level
                  for fid in b["fids"]]
        proj_ids = [fid for fid in lv_ids
                    if facts[fid]["scope"] == "project"]
        new2 = proj_ids[-2:]
        pool_g = [fid for fid in lv_ids if facts[fid]["scope"] == "global"] \
            if level == 2 else \
            [fid for fid, f in facts.items() if f["scope"] == "global"]
        for i, fid in enumerate(rng.sample(pool_g, 2)):
            t, r = q_present(fid, level, "global")
            probes.append(probe(f"T2-L{level}-global-{i}", level, "global",
                                None, [fid], t, r))
        for i, fid in enumerate(rng.sample(proj_ids, 2)):
            t, r = q_present(fid, level, "project")
            probes.append(probe(f"T2-L{level}-local-{i}", level,
                                "project-local", facts[fid]["project"],
                                [fid], t, r))
        of = l1ids[0]
        t, r = q_present(of, level, "project")
        probes.append(probe(f"T2-L{level}-oldest", level, "oldest",
                            facts[of]["project"], [of], t, r))
        nf = new2[0]
        t, r = q_present(nf, level, "project")
        probes.append(probe(f"T2-L{level}-newest", level, "newest",
                            facts[nf]["project"], [nf], t, r))
        if level == 2:
            for i, fid in enumerate(rng.sample(
                    [f for f in y_facts if f not in z_facts], 2)):
                v, old = val(fid, 2)
                probes.append(probe(
                    f"T2-L2-corr-{i}", 2, "corrected",
                    facts[fid]["project"], [fid],
                    f"What is the current {facts[fid]['attr']} for "
                    f"{facts[fid]['project']}?",
                    {"rule": "corrected", "new": v, "old": old}))
        else:
            y1 = [f for f in y_facts if f not in z_facts][0]
            v, old = val(y1, 3)
            probes.append(probe(
                "T2-L3-corr-Y", 3, "corrected", facts[y1]["project"], [y1],
                f"What is the current {facts[y1]['attr']} for "
                f"{facts[y1]['project']}?",
                {"rule": "corrected", "new": v, "old": old}))
            z1 = z_facts[0]
            v, old = val(z1, 3)
            probes.append(probe(
                "T2-L3-corr-Z", 3, "corrected", facts[z1]["project"], [z1],
                f"What is the current {facts[z1]['attr']} for "
                f"{facts[z1]['project']}?",
                {"rule": "corrected", "new": v, "old": old}))
        for i, (attr, pa, pb) in enumerate([("gate lock", "Acorn", "Elm"),
                                            ("path material", "Birch",
                                             "Cedar")]):
            fa, fb = conflict_ids[attr][pa], conflict_ids[attr][pb]
            va, _ = val(fa, level)
            vb, _ = val(fb, level)
            probes.append(probe(
                f"T2-L{level}-conflict-{i}", level, "conflicting", pa,
                [fa, fb], f"What is the {attr} for {pa}?",
                {"rule": "present_absent", "present": [va], "absent": [vb]}))
        fa = conflict_ids["shed paint"]["Acorn"]
        fb = conflict_ids["shed paint"]["Birch"]
        va, _ = val(fa, level)
        vb, _ = val(fb, level)
        probes.append(probe(
            f"T2-L{level}-AB", level, "compare", None, [fa, fb],
            "Compare Acorn and Birch: what is the shed paint for each?",
            {"rule": "ab", "a": "Acorn", "b": "Birch", "va": va, "vb": vb}))
        shed_vals = [val(conflict_ids["shed paint"][p], level)[0]
                     for p in PROJECTS]
        probes.append(probe(
            f"T2-L{level}-amb", level, "ambiguous", None, [],
            "What color should we paint the shed?",
            {"rule": "clarify", "absent": shed_vals}))
    rgl = rng.sample([fid for fid, f in facts.items()
                      if f["scope"] == "global"], 3)
    for i, fid in enumerate(rgl):
        t, r = q_present(fid, 3, "global")
        probes.append(probe(f"T2-R-global-{i}", "R", "global", None, [fid],
                            t, r))
    for p in ("Acorn", "Birch"):
        for i, fid in enumerate(rng.sample(
                [fid for fid, f in facts.items() if f["project"] == p], 3)):
            t, r = q_present(fid, 3, "project")
            probes.append(probe(f"T2-R-{p}-{i}", "R", "project-local", p,
                                [fid], t, r))
    for i, fid in enumerate([f for f in y_facts if f not in z_facts][:2]):
        v, old = val(fid, 3)
        probes.append(probe(
            f"T2-R-corrY-{i}", "R", "corrected", facts[fid]["project"],
            [fid], f"What is the current {facts[fid]['attr']} for "
            f"{facts[fid]['project']}?",
            {"rule": "corrected", "new": v, "old": old}))
    zf = z_facts[1]
    v, old = val(zf, 3)
    probes.append(probe(
        "T2-R-corrZ", "R", "corrected", facts[zf]["project"], [zf],
        f"What is the current {facts[zf]['attr']} for "
        f"{facts[zf]['project']}?",
        {"rule": "corrected", "new": v, "old": old}))
    return {"seed": 77031, "facts": facts, "conflict_ids": conflict_ids,
            "corrections": {fid: c for fid, c in corrections.items()},
            "teaching": teaching, "probes": probes}


if __name__ == "__main__":
    t1 = gen_test1()
    json.dump(t1, open(os.path.join(HERE, "scale_test1.json"), "w",
                       encoding="utf-8"), indent=1, ensure_ascii=False)
    print("test1: turns=%d facts=%d corrections=%d probes=%d" % (
        len(t1["turns"]), len(t1["facts"]), len(t1["corrections"]),
        len(t1["probes"])))
    t2 = gen_test2()
    json.dump(t2, open(os.path.join(HERE, "scale_test2.json"), "w",
                       encoding="utf-8"), indent=1, ensure_ascii=False)
    print("test2: facts=%d teaching=%d probes=%d" % (
        len(t2["facts"]), len(t2["teaching"]), len(t2["probes"])))
    # cross-test disjointness: no value shared or substring-linked
    v1 = [f["value"] for f in t1["facts"].values()] + \
        [c["old"] for c in t1["corrections"].values()]
    v2 = [f["value"] for f in t2["facts"].values()] + \
        [c["Y"] for c in t2["corrections"].values()] + \
        [c["Z"] for c in t2["corrections"].values() if c["Z"]]
    check_values(v1 + v2)
    print("cross-test value check ok (%d + %d values)" % (len(v1), len(v2)))
