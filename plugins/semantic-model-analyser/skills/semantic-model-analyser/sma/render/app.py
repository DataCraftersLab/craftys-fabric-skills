"""
The finder's and the inspector's index. One JSON island per page, built from the dataset.

Every page carries the whole index, so the finder reaches an object on another page and
the inspector opens it here without a page load. It holds what a reader asks first about
an object (its facts, its code, what it reads, what reads it, its findings, its file and
line) and nothing a card already draws. Expressions are clipped: the full text is one
click away on the card the inspector links to.
"""

import re

CODE_CAP = 2400
LIST_CAP = 60
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
GUID_ANY = re.compile(r"[_ ]?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _dedent(s):
    """TMDL indents an expression to its nesting; the first line usually sits flush.
    Strip the common indent of the rest, so the code reads as written."""
    lines = s.split("\n")
    rest = [l for l in lines[1:] if l.strip()]
    if not rest:
        return s
    cut = min(len(l) - len(l.lstrip(" ")) for l in rest)
    if lines[0].strip() in ("let", "") or lines[0].startswith(" "):
        pad = 0
    else:
        pad = 4
    return "\n".join([lines[0]] + [(" " * pad + l[cut:]) if l.strip() else "" for l in lines[1:]])


def _clip(s, n=CODE_CAP):
    s = (s or "").replace("\r\n", "\n").replace("\t", "    ").strip("\n")
    s = _dedent(s)
    return s if len(s) <= n else s[:n].rstrip() + "\n…"


def _where(src):
    if not src:
        return None
    if isinstance(src, dict):
        f, ln = src.get("file"), src.get("line")
        return ("%s:%s" % (f, ln)) if f and ln else f
    return None


def _yn(v):
    return "yes" if v else "no"


def short_table(name):
    """A local date table is named after its GUID. The finder shows the stem."""
    return GUID_ANY.sub("", name) + (" …" if GUID_ANY.search(name) else "")


def rel_label(r):
    return "%s[%s] → %s[%s]" % (short_table(r["fromTable"]), r["fromColumn"],
                                     short_table(r["toTable"]), r["toColumn"])


class Index:
    def __init__(self, report, pages):
        self.r = report
        self.pages = pages
        self.objs = []
        self.by_id = {}

    def add(self, o):
        if o["id"] in self.by_id:
            return self.by_id[o["id"]]
        self.objs.append(o)
        self.by_id[o["id"]] = o
        return o

    def has(self, oid):
        return oid in self.by_id


def _subject_ids(subject, ix):
    """The object a finding names, tried as each kind in turn."""
    if not subject:
        return None
    for oid in ("m:" + subject, "c:" + subject, "t:" + subject, "r:" + subject,
                "q:" + subject, "role:" + subject, "cg:" + subject, "x:" + subject):
        if ix.has(oid):
            return oid
    m = re.match(r"^'?(.+?)'?\[(.+)\]$", subject)
    if m and ix.has("c:%s[%s]" % (m.group(1), m.group(2))):
        return "c:%s[%s]" % (m.group(1), m.group(2))
    return None


def build(report, pages, cards_of, help_text):
    """`pages` is {key: {"file", "label"}} for the pages this run writes; `cards_of`
    maps a page key to [(card id, code, title)] and the view list."""
    ix = Index(report, pages)
    has_t = "tmdl" in report.get("parts", [])
    has_m = "m" in report.get("parts", [])
    t = report.get("tmdl") or {}
    mm = report.get("m") or {}

    if has_t:
        anat = t.get("anatomy") or {}
        col_readers = {}
        for m in t["measures"]:
            for c in (anat.get(m["id"]) or {}).get("columnRefs", []):
                col_readers.setdefault(c.lower(), []).append(m["name"])
        rels_of = {}
        for r in t.get("relationships", []):
            for tb in (r["fromTable"], r["toTable"]):
                rels_of.setdefault(tb, []).append(r)
        cols_of, meas_of = {}, {}
        for c in t["columns"]:
            cols_of.setdefault(c["table"], []).append(c)
        for m in t["measures"]:
            meas_of.setdefault(m["table"], []).append(m)
        bridge = {b["name"]: b for b in t.get("bridge", [])}

        for tb in t["tables"]:
            b = bridge.get(tb["name"]) or {}
            facts = [["Storage", b.get("storage") or "—"],
                     ["Columns", "%d (%d hidden, %d calculated)" % (
                         tb["columns"], tb["hiddenColumns"], tb["calculatedColumns"])],
                     ["Measures", str(tb["measures"])],
                     ["Hidden", _yn(tb["isHidden"])]]
            if tb.get("isCalculationGroup"):
                facts.append(["Kind", "Calculation group"])
            elif tb.get("isCalculated"):
                facts.append(["Kind", "Calculated table"])
            if b.get("conns"):
                facts.append(["Connects to", ", ".join(b["conns"])])
            src = ""
            if tb.get("partitions"):
                src = tb["partitions"][0].get("source") or ""
            links = [["Columns", ["c:%s[%s]" % (tb["name"], c["name"]) for c in cols_of.get(tb["name"], [])]],
                     ["Measures", ["m:" + m["name"] for m in meas_of.get(tb["name"], [])]],
                     ["Relationships", ["r:" + r["name"] for r in rels_of.get(tb["name"], [])]],
                     ["Read by", ["m:" + n for n in b.get("readBy", [])]]]
            ix.add({"id": "t:" + tb["name"], "k": "table", "n": short_table(tb["name"]),
                    "full": tb["name"], "s": b.get("storage") or "",
                    "d": tb.get("description") or "", "f": facts,
                    "code": _clip(src), "lang": "m" if src else "",
                    "l": links, "w": _where(tb.get("source")),
                    "go": [["Table card", "tmdl", "card-t15"], ["Load bridge", "tmdl", "card-t6"]]})

        for m in t["measures"]:
            a = anat.get(m["id"]) or {}
            facts = [["Table", m["table"]], ["Folder", m.get("folder") or "—"],
                     ["Format", m.get("formatString") or "none"],
                     ["Hidden", _yn(m["isHidden"])]]
            if a:
                facts.append(["Syntax", "%s lines, %s calls, nesting %s" % (
                    a.get("lines"), a.get("calls"), a.get("depth"))])
            ix.add({"id": "m:" + m["name"], "k": "measure", "n": m["name"],
                    "s": m["table"] + (" · " + m["folder"] if m.get("folder") and m["folder"] != "Other" else ""),
                    "d": m.get("description") or "", "f": facts,
                    "code": _clip(a.get("formatted") or m.get("expression")), "lang": "dax",
                    "l": [["Reads", ["m:" + n for n in m.get("dependsOn", [])]
                           + ["c:" + c for c in a.get("columnRefs", [])]],
                          ["Read by", ["m:" + n for n in m.get("usedBy", [])]]],
                    "w": _where(m.get("source")),
                    "go": [["Anatomy", "tmdl", "card-t14", m["name"]],
                           ["Chain", "tmdl", "card-t2"]]})

        for c in t["columns"]:
            cid = "c:%s[%s]" % (c["table"], c["name"])
            facts = [["Type", c.get("dataType") or "—"],
                     ["Format", c.get("formatString") or "none"],
                     ["Summarize by", c.get("summarizeBy") or "default"],
                     ["Hidden", _yn(c["isHidden"])]]
            if c.get("isCalculated"):
                facts.append(["Kind", "Calculated column"])
            if c.get("sortByColumn"):
                facts.append(["Sort by", c["sortByColumn"]])
            if c.get("isKey"):
                facts.append(["Key", "yes"])
            rels = [r for r in rels_of.get(c["table"], [])
                    if (r["fromTable"] == c["table"] and r["fromColumn"] == c["name"])
                    or (r["toTable"] == c["table"] and r["toColumn"] == c["name"])]
            readers = col_readers.get(("%s[%s]" % (c["table"], c["name"])).lower(), [])
            ix.add({"id": cid, "k": "column", "n": "%s[%s]" % (short_table(c["table"]), c["name"]),
                    "s": c.get("dataType") or "", "d": c.get("description") or "", "f": facts,
                    "code": _clip(c.get("expression")) if c.get("isCalculated") else "",
                    "lang": "dax",
                    "l": [["Table", ["t:" + c["table"]]],
                          ["Read by", ["m:" + n for n in readers]],
                          ["Relationships", ["r:" + r["name"] for r in rels]]],
                    "w": _where(c.get("source")),
                    "go": [["Table card", "tmdl", "card-t15"]]})

        for r in t.get("relationships", []):
            card = "%s to %s" % (r.get("fromCardinality") or "many", r.get("toCardinality") or "one")
            ix.add({"id": "r:" + r["name"], "k": "relationship", "n": rel_label(r),
                    "s": card + ("" if r["isActive"] else " · inactive"),
                    "d": "", "f": [["Cardinality", card],
                                  ["Direction", "both" if r.get("crossFilteringBehavior") == "bothDirections" else "single"],
                                  ["Active", _yn(r["isActive"])],
                                  ["Referential integrity", _yn(r.get("relyOnReferentialIntegrity"))]]
                    + ([] if GUID.match(r["name"]) else [["Name", r["name"]]]),
                    "code": "", "lang": "",
                    "l": [["Tables", ["t:" + r["fromTable"], "t:" + r["toTable"]]],
                          ["Columns", ["c:%s[%s]" % (r["fromTable"], r["fromColumn"]),
                                       "c:%s[%s]" % (r["toTable"], r["toColumn"])]]],
                    "w": _where(r.get("source")),
                    "go": [["Relationships", "tmdl", "card-t7"]]})

        for role in t.get("roles", []):
            perms = role.get("tablePermissions") or []
            code = "\n".join("%s: %s" % (p["table"], p["filterExpression"])
                             for p in perms if p.get("filterExpression"))
            ols = ["%s[%s] %s" % (p["table"], c, v) for p in perms
                   for c, v in (p.get("columnPermissions") or {}).items()]
            ix.add({"id": "role:" + role["name"], "k": "role", "n": role["name"],
                    "s": role.get("modelPermission") or "",
                    "d": "", "f": [["Permission", role.get("modelPermission") or "—"],
                                  ["Tables filtered", str(sum(1 for p in perms if p.get("filterExpression")))],
                                  ["Column rules", ", ".join(ols) or "none"]],
                    "code": _clip(code), "lang": "dax",
                    "l": [["Tables", ["t:" + p["table"] for p in perms]]],
                    "w": _where(role.get("source")),
                    "go": [["Security", "tmdl", "card-t8"]]})

        for cg in t.get("calculationGroups", []):
            name = cg.get("table") or cg.get("name") or ""
            items = cg.get("items") or []
            ix.add({"id": "cg:" + name, "k": "calc group", "n": name,
                    "s": "%d items" % len(items), "d": "",
                    "f": [["Precedence", str(cg.get("precedence"))],
                          ["Items", ", ".join(i["name"] for i in items) or "none"]],
                    "code": _clip("\n\n".join("-- %s\n%s" % (i["name"], i["expression"]) for i in items)),
                    "lang": "dax", "l": [["Table", ["t:" + name]]],
                    "w": _where(items[0].get("source")) if items else None,
                    "go": [["Security", "tmdl", "card-t8"]]})

        for x in t.get("expressions", []):
            ix.add({"id": "x:" + x["name"], "k": "expression", "n": x["name"],
                    "s": x.get("kind") or "", "d": "",
                    "f": [["Kind", x.get("kind") or "—"], ["Lines", str(x.get("lines") or "—")]],
                    "code": "", "lang": "", "l": [], "w": _where(x.get("source")),
                    "go": [["Security", "tmdl", "card-t8"]]})

        for h in t.get("hierarchies", []):
            ix.add({"id": "h:%s/%s" % (h["table"], h["name"]), "k": "hierarchy",
                    "n": h["name"], "s": short_table(h["table"]), "d": h.get("description") or "",
                    "f": [["Levels", " › ".join(l["name"] for l in h.get("levels", []))]],
                    "code": "", "lang": "",
                    "l": [["Table", ["t:" + h["table"]]],
                          ["Columns", ["c:%s[%s]" % (h["table"], l["column"]) for l in h.get("levels", [])]]],
                    "w": _where(h.get("source")), "go": [["Security", "tmdl", "card-t8"]]})

    if has_m:
        for q in mm.get("queries", []):
            conns = sorted({s.get("label") or s.get("functionName") for s in q.get("sources", [])} - {None})
            facts = [["Kind", q.get("kind") or "—"], ["Steps", str(len(q.get("steps", [])))],
                     ["Loads", _yn(q.get("loadEnabled"))],
                     ["Connects to", ", ".join(conns) or "—"],
                     ["Folding", ("potential barrier at " + q["foldBreakStep"]) if q.get("foldBreakStep")
                      else "no barrier detected"]]
            oid = "q:" + q["name"]
            if ix.has(oid):
                oid = "q:%s (query)" % q["name"]
            ix.add({"id": oid, "k": "query", "n": q["name"],
                    "s": "%d steps" % len(q.get("steps", [])) + (" · " + conns[0] if conns else ""),
                    "d": q.get("description") or "", "f": facts,
                    "code": _clip(q.get("source")), "lang": "m",
                    "l": [["Reads", ["q:" + n for n in q.get("dependsOn", [])]],
                          ["Read by", ["q:" + n for n in q.get("usedBy", [])]],
                          ["Table", ["t:" + q["name"]] if has_t else []]],
                    "w": ("line %s" % q["line"]) if q.get("line") else None,
                    "go": [["Lineage", "pq", "card-q1"], ["Steps", "pq", "card-q19"]]})

    # Links are kept only where the target exists, so a click never lands on nothing.
    for o in ix.objs:
        o["l"] = [[lab, [i for i in dict.fromkeys(ids) if ix.has(i) and i != o["id"]][:LIST_CAP]]
                  for lab, ids in o["l"]]
        o["l"] = [x for x in o["l"] if x[1]]

    # Where an object is drawn depends on its kind only, so it is stored once per kind;
    # a fourth element of 1 means "select this object there" (the anatomy picker).
    go = {}
    for o in ix.objs:
        targets = [[g[0], g[1], g[2], 1 if len(g) > 3 else 0] for g in o.pop("go") if g[1] in pages]
        go.setdefault(o["k"], targets)

    finds = []
    for part_key, part in (("tmdl", t), ("pq", mm)):
        if not part:
            continue
        page = part_key if part_key in pages else "index"
        for f in part.get("findings", []):
            oid = _subject_ids(f.get("subject"), ix)
            where = f.get("where")
            finds.append({"lv": f["level"], "code": f["code"], "msg": f["message"],
                          "subj": f.get("subject") or "", "o": oid, "p": page,
                          "w": _where(where) if isinstance(where, dict) else None})
            if oid:
                ix.by_id[oid].setdefault("fi", []).append(len(finds) - 1)

    checks = []
    if has_t and t.get("aiReadiness") and "ai" in pages:
        for g in t["aiReadiness"]["groups"]:
            for c in g["checks"]:
                checks.append({"key": c["key"], "n": c["label"], "st": c["status"],
                               "d": c["detail"], "g": g["title"]})

    views, cards = [], []
    for key, (vs, cs) in cards_of.items():
        views += [{"p": key, "id": v, "n": n} for v, n in vs]
        cards += [{"p": key, "id": i, "code": code, "n": n} for i, code, n in cs]

    return {"pages": {k: {"file": v["file"], "label": v["label"]} for k, v in pages.items()},
            "objects": ix.objs, "findings": finds, "checks": checks, "go": go,
            "help": {c: help_text[c] for c in sorted({f["code"] for f in finds}) if c in help_text},
            "views": views, "cards": cards}
