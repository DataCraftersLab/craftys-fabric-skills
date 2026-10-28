"""
Questions about a model, answered from the dataset.

Two commands sit on top of `report.json`, so any question about the TMDL or the Power
Query can be answered without an HTML build and without writing a script:

    list     objects of one kind, filtered and projected   -- "which measures in Sales
             have no format string", "every relationship that is inactive", "the steps
             with a native query"
    explain  everything the dataset holds about one named thing -- a measure with its
             DAX, anatomy, chains and findings; a table with its columns, partitions and
             relationships; a column; a relationship; a role; a query with its steps

Both read the dataset and nothing else, like the renderer. If a question cannot be
answered from here, the dataset is what needs to grow, not this file.
"""

import collections
import json
import re

# ------------------------------------------------------------------ kinds

#: kind -> (part, key in the part, default fields). `None` for the key means the rows
#: are derived in `rows_of`.
KINDS = {
    "measures": ("tmdl", "measures",
                 ["name", "table", "folder", "formatString", "isHidden", "usedBy", "dependsOn", "source"]),
    "columns": ("tmdl", "columns",
                ["table", "name", "dataType", "summarizeBy", "sortByColumn", "isHidden", "isCalculated", "source"]),
    "tables": ("tmdl", "tables",
               ["name", "measures", "columns", "hiddenColumns", "isCalculated", "isCalculationGroup", "source"]),
    "partitions": ("tmdl", "bridge", ["name", "kind", "mode", "storage", "fan", "state"]),
    "relationships": ("tmdl", "relationships",
                      ["name", "fromTable", "fromColumn", "toTable", "toColumn", "fromCardinality",
                       "toCardinality", "crossFilteringBehavior", "isActive", "source"]),
    "roles": ("tmdl", "roles", ["name", "modelPermission", "tablePermissions"]),
    "perspectives": ("tmdl", "perspectives", None),
    "expressions": ("tmdl", "expressions", ["name", "kind", "queryGroup", "lines", "source"]),
    "calculation-groups": ("tmdl", "calculationGroups", ["table", "precedence", "items"]),
    "hierarchies": ("tmdl", "hierarchies", ["table", "name", "levels"]),
    "unreferenced": ("tmdl", None, ["name", "table", "folder", "isHidden", "source"]),
    "functions": ("tmdl", None, ["name", "calls", "measures"]),
    "findings": (None, None, ["level", "code", "subject", "message", "where"]),
    "queries": ("m", "queries",
                ["name", "layer", "sourceKind", "stepCount", "dependsOn", "usedBy", "foldBreakStep"]),
    "steps": ("m", None, ["query", "index", "name", "functionName", "dead", "foldBreak", "rowByRow"]),
    "sql": ("m", "sql", ["query", "step", "connector", "database", "facts"]),
}


def rows_of(report, kind):
    """The raw rows for a kind. A missing part gives no rows, not an error: asking an
    M-only run for its measures is a question with the answer "none"."""
    if kind not in KINDS:
        raise ValueError("unknown kind %r; one of %s" % (kind, ", ".join(sorted(KINDS))))
    part, key, _ = KINDS[kind]
    if kind == "findings":
        out = []
        for p in ("tmdl", "m"):
            for f in (report.get(p) or {}).get("findings") or []:
                out.append(dict(f, part=p))
        return out
    src = report.get(part) or {}
    if kind == "unreferenced":
        names = set(src.get("unreferenced") or [])
        return [m for m in src.get("measures") or [] if m["name"] in names]
    if kind == "functions":
        by = collections.defaultdict(lambda: {"calls": 0, "measures": []})
        anatomy = src.get("anatomy") or {}
        ids = {m["id"]: m["name"] for m in src.get("measures") or [] if m.get("id")}
        for mid, a in anatomy.items():
            for fn in a.get("functions") or []:
                e = by[fn["name"]]
                e["calls"] += fn.get("count", 0)
                e["measures"].append(ids.get(mid, mid))
        return [dict(name=k, **v) for k, v in sorted(by.items(), key=lambda kv: (-kv[1]["calls"], kv[0]))]
    if kind == "steps":
        out = []
        for q in src.get("queries") or []:
            for s in q.get("steps") or []:
                out.append(dict(s, query=q["name"]))
        return out
    rows = list(src.get(key) or [])
    if kind == "queries":
        rows = [dict(q, stepCount=len(q.get("steps") or [])) for q in rows]
    return rows


# ---------------------------------------------------------------- filters

_OPS = ("!=", ">=", "<=", "~", "=", ">", "<")
_WHERE_RE = re.compile(r"^\s*([A-Za-z_][\w.\[\]-]*)\s*(%s)\s*(.*?)\s*$" % "|".join(re.escape(o) for o in _OPS))


def parse_where(text):
    m = _WHERE_RE.match(text or "")
    if not m:
        raise ValueError("cannot read filter %r; expected field=value, field!=value, "
                         "field~contains, field>n or field<n" % text)
    return m.group(1), m.group(2), m.group(3)


def get_path(row, path):
    """`a.b.c` through dicts; a list at the end is returned whole."""
    cur = row
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _as_number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def matches(row, field, op, value):
    got = get_path(row, field)
    if isinstance(got, list):
        if op == "=":
            return any(str(x).lower() == value.lower() for x in got) or (value == "" and not got)
        if op == "!=":
            return not any(str(x).lower() == value.lower() for x in got)
        if op == "~":
            return any(value.lower() in str(x).lower() for x in got)
        n = _as_number(value)
        if n is not None:
            return {">": len(got) > n, "<": len(got) < n, ">=": len(got) >= n, "<=": len(got) <= n}[op]
        return False
    if isinstance(got, dict):
        got = json.dumps(got, sort_keys=True)
    if isinstance(got, bool) or (value.lower() in ("true", "false") and op in ("=", "!=")):
        want = _truthy(value)
        return (bool(got) == want) if op == "=" else (bool(got) != want)
    gn, vn = _as_number(got), _as_number(value)
    if op in (">", "<", ">=", "<="):
        if gn is None or vn is None:
            return False
        return {">": gn > vn, "<": gn < vn, ">=": gn >= vn, "<=": gn <= vn}[op]
    s = "" if got is None else str(got)
    if op == "=":
        return s.lower() == value.lower()
    if op == "!=":
        return s.lower() != value.lower()
    if op == "~":
        return value.lower() in s.lower()
    return False


def select(rows, where=(), match=None):
    conds = [parse_where(w) for w in (where or [])]
    out = []
    for r in rows:
        if all(matches(r, f, o, v) for f, o, v in conds):
            if match:
                blob = json.dumps(r, ensure_ascii=False).lower()
                if match.lower() not in blob:
                    continue
            out.append(r)
    return out


# ------------------------------------------------------------- formatting

def cell(v, wide=False):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, dict):
        if "file" in v and "line" in v:
            return "%s:%s" % (v.get("file"), v.get("line"))
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            if "name" in v[0] and "column" in v[0]:          # hierarchy levels
                s = " > ".join("%s (%s)" % (x["name"], x["column"]) for x in v)
            elif "table" in v[0] and "filterExpression" in v[0]:  # table permissions
                s = "; ".join("%s: %s" % (x["table"], (x.get("filterExpression") or "").strip()[:60]) for x in v)
            elif "name" in v[0]:
                s = ", ".join(str(x["name"]) for x in v)
            else:
                s = json.dumps(v, ensure_ascii=False)
        else:
            s = ", ".join(str(x) for x in v)
    else:
        s = str(v)
    s = " ".join(s.split())
    return s if wide or len(s) <= 72 else s[:71] + "…"


def format_rows(rows, fields, wide=False):
    if not rows:
        return "(none)"
    table = [[cell(get_path(r, f), wide) for f in fields] for r in rows]
    widths = [max(len(h), *(len(t[i]) for t in table)) for i, h in enumerate(fields)]
    line = "  ".join(h.ljust(w) for h, w in zip(fields, widths)).rstrip()
    out = [line, "  ".join("-" * w for w in widths)]
    for t in table:
        out.append("  ".join(c.ljust(w) for c, w in zip(t, widths)).rstrip())
    return "\n".join(out)


def list_cmd(report, kind, where=(), match=None, fields=None, limit=0):
    """Rows of a kind after filters, the fields to show, and the total before the limit."""
    rows = select(rows_of(report, kind), where, match)
    total = len(rows)
    if limit and len(rows) > limit:
        rows = rows[:limit]
    default = KINDS[kind][2] or (sorted(rows[0].keys()) if rows else ["name"])
    return rows, (fields or default), total


# ---------------------------------------------------------------- explain

def _norm_ref(s):
    return re.sub(r"'([^']*)'", r"\1", s or "").strip().lower()


def _lower(s):
    return (s or "").strip().lower()


def _by_name(items, name):
    return [x for x in items if _lower(x.get("name")) == _lower(name)]


def _anatomy_of(t, m):
    return (t.get("anatomy") or {}).get(m.get("id") or "", {})


def _closure(by_name, start, key):
    seen, stack, order = set(), list(by_name.get(start, {}).get(key) or []), []
    while stack:
        n = stack.pop(0)
        if n in seen or n == start:
            continue
        seen.add(n)
        order.append(n)
        stack.extend(by_name.get(n, {}).get(key) or [])
    return order


def _findings_about(report, *subjects):
    wanted = {_lower(s) for s in subjects}
    out = []
    for p in ("tmdl", "m"):
        for f in (report.get(p) or {}).get("findings") or []:
            subj = _lower(f.get("subject"))
            if subj in wanted or any(subj.startswith(w + "[") for w in wanted):
                out.append(f)
    return out


def explain(report, name):
    """Everything the dataset holds about one named thing. Several kinds can share a
    name (a table and the query that loads it), so the answer is a list of blocks."""
    t = report.get("tmdl") or {}
    m = report.get("m") or {}
    blocks = []
    col_match = re.match(r"^\s*'?([^'\[\]]+)'?\s*\[([^\]]+)\]\s*$", name)

    measures = t.get("measures") or []
    by_name = {x["name"]: x for x in measures}
    for ms in _by_name(measures, name):
        a = _anatomy_of(t, ms)
        blocks.append({
            "kind": "measure", "name": ms["name"], "table": ms.get("table"), "folder": ms.get("folder"),
            "formatString": ms.get("formatString"), "isHidden": ms.get("isHidden"),
            "hasDynamicFormat": ms.get("hasDynamicFormat"), "description": ms.get("description"),
            "source": ms.get("source"), "dax": a.get("formatted") or ms.get("expression"),
            "anatomy": {k: a.get(k) for k in ("weight", "lines", "calls", "depth", "chain")},
            "functions": a.get("functions") or [], "vars": a.get("vars") or [],
            "columnsRead": a.get("columnRefs") or [], "tablesRead": a.get("tableRefs") or ms.get("tables") or [],
            "dependsOn": ms.get("dependsOn") or [], "upstream": _closure(by_name, ms["name"], "dependsOn"),
            "usedBy": ms.get("usedBy") or [], "downstream": _closure(by_name, ms["name"], "usedBy"),
            "referencedBy": ms.get("referencedBy") or [],
            "findings": _findings_about(report, ms["name"]),
        })

    tables = t.get("tables") or []
    columns = t.get("columns") or []
    rels = t.get("relationships") or []
    for tb in _by_name(tables, name):
        bridge = next((b for b in t.get("bridge") or [] if _lower(b["name"]) == _lower(name)), {})
        touching = [r for r in rels if _lower(r["fromTable"]) == _lower(name) or _lower(r["toTable"]) == _lower(name)]
        blocks.append({
            "kind": "table", "name": tb["name"], "description": tb.get("description"),
            "source": tb.get("source"), "isCalculated": tb.get("isCalculated"),
            "isCalculationGroup": tb.get("isCalculationGroup"), "isHidden": tb.get("isHidden"),
            "storage": bridge.get("storage"), "partitions": tb.get("partitions") or [],
            "columns": [{k: c.get(k) for k in ("name", "dataType", "summarizeBy", "sortByColumn", "isHidden", "isCalculated")}
                        for c in columns if _lower(c["table"]) == _lower(name)],
            "measures": [x["name"] for x in measures if _lower(x.get("table")) == _lower(name)],
            "measuresReadingIt": [x["name"] for x in measures
                                  if any(_norm_ref(r) == _lower(name) for r in (x.get("tables") or []))],
            "relationships": touching,
            "hierarchies": [h for h in t.get("hierarchies") or [] if _lower(h["table"]) == _lower(name)],
            "calculationGroup": next((g for g in t.get("calculationGroups") or [] if _lower(g["table"]) == _lower(name)), None),
            "roleFilters": [{"role": r["name"], "filter": p.get("filterExpression"), "columnPermissions": p.get("columnPermissions")}
                            for r in t.get("roles") or [] for p in r.get("tablePermissions") or []
                            if _lower(p.get("table")) == _lower(name)],
            "findings": _findings_about(report, tb["name"]),
        })

    if col_match:
        tname, cname = col_match.group(1), col_match.group(2)
        for c in columns:
            if _lower(c["table"]) == _lower(tname) and _lower(c["name"]) == _lower(cname):
                ref = _lower("%s[%s]" % (tname, cname))
                readers = [x["name"] for x in measures
                           if any(_norm_ref(r) == ref for r in (_anatomy_of(t, x).get("columnRefs") or []))]
                blocks.append({
                    "kind": "column", "table": c["table"], "name": c["name"], "dataType": c.get("dataType"),
                    "summarizeBy": c.get("summarizeBy"), "sortByColumn": c.get("sortByColumn"),
                    "isHidden": c.get("isHidden"), "isKey": c.get("isKey"), "isCalculated": c.get("isCalculated"),
                    "expression": c.get("expression"), "dataCategory": c.get("dataCategory"),
                    "formatString": c.get("formatString"), "description": c.get("description"),
                    "source": c.get("source"), "measuresReadingIt": readers,
                    "relationships": [r for r in rels
                                      if (_lower(r["fromTable"]) == _lower(tname) and _lower(r["fromColumn"]) == _lower(cname))
                                      or (_lower(r["toTable"]) == _lower(tname) and _lower(r["toColumn"]) == _lower(cname))],
                    "sortsColumns": [x["name"] for x in columns
                                     if _lower(x["table"]) == _lower(tname) and _lower(x.get("sortByColumn")) == _lower(cname)],
                    "findings": _findings_about(report, "%s[%s]" % (c["table"], c["name"])),
                })

    for r in _by_name(rels, name):
        pat = re.compile(r"USERELATIONSHIP", re.I)
        users = [x["name"] for x in measures if pat.search(x.get("expression") or "")
                 and _lower(r["fromColumn"]) in _lower(x.get("expression"))
                 and _lower(r["toColumn"]) in _lower(x.get("expression"))]
        blocks.append(dict({"kind": "relationship"}, **r, usedByMeasures=users,
                           findings=_findings_about(report, r["name"])))

    for ro in _by_name(t.get("roles") or [], name):
        blocks.append(dict({"kind": "role"}, **ro))

    for ex in _by_name(t.get("expressions") or [], name):
        # A shared expression carries its own `kind` (parameter | function | query), which
        # must not overwrite the block's kind.
        blocks.append(dict(ex, kind="expression", expressionKind=ex.get("kind")))

    for q in _by_name(m.get("queries") or [], name):
        blocks.append({
            "kind": "query", "name": q["name"], "layer": q.get("layer"), "sourceKind": q.get("sourceKind"),
            "description": q.get("description"), "loadEnabled": q.get("loadEnabled"),
            "sources": q.get("sources") or [], "steps": q.get("steps") or [],
            "dependsOn": q.get("dependsOn") or [], "usedBy": q.get("usedBy") or [],
            "unresolved": q.get("unresolved") or [], "foldBreakStep": q.get("foldBreakStep"),
            "nativeSql": [s for s in m.get("sql") or [] if _lower(s.get("query")) == _lower(name)],
            "findings": [f for f in m.get("findings") or [] if _lower(f.get("subject")) == _lower(name)],
        })

    return blocks


def _kv(label, value, wide=False):
    v = cell(value, wide)
    return "  %-18s %s" % (label, v) if v else None


def format_explain(blocks, name, wide=False):
    if not blocks:
        return ("nothing called %r in this dataset. Names are matched whole, case-insensitively; "
                "a column is written Table[Column]. `list <kind> --match %s` finds partial matches."
                % (name, json.dumps(name)))
    out = []
    for b in blocks:
        kind = b["kind"]
        title = {"column": "%s[%s]" % (b.get("table"), b.get("name"))}.get(kind, b.get("name"))
        out.append("%s  %s" % (kind.upper(), title))
        out.append("=" * (len(kind) + 2 + len(str(title))))
        skip = {"kind", "name", "dax", "steps", "columns", "findings", "relationships", "sources",
                "tablePermissions", "nativeSql", "partitions", "hierarchies", "calculationGroup",
                "roleFilters", "functions", "vars", "anatomy"}
        for k, v in b.items():
            if k in skip or v in (None, [], {}, ""):
                continue
            line = _kv(k, v, wide)
            if line:
                out.append(line)
        if b.get("anatomy"):
            out.append(_kv("anatomy", ", ".join("%s %s" % (k, v) for k, v in b["anatomy"].items() if v is not None)))
        if b.get("functions"):
            out.append(_kv("functions", ", ".join("%s x%d" % (f["name"], f["count"]) for f in b["functions"])))
        if b.get("vars"):
            out.append(_kv("vars", ", ".join(v["name"] for v in b["vars"])))
        if b.get("dax"):
            out.append("  DAX:")
            out.extend("    " + ln for ln in str(b["dax"]).splitlines())
        if b.get("partitions"):
            out.append("  partitions:")
            for p in b["partitions"]:
                out.append("    %s = %s%s" % (p.get("name"), p.get("kind"), (" (%s)" % p["mode"]) if p.get("mode") else ""))
        if b.get("columns"):
            out.append("  columns:")
            out.append("\n".join("    " + ln for ln in format_rows(
                b["columns"], ["name", "dataType", "summarizeBy", "sortByColumn", "isHidden", "isCalculated"], wide).splitlines()))
        if b.get("relationships"):
            out.append("  relationships:")
            for r in b["relationships"]:
                out.append("    %s[%s] -> %s[%s]  %s->%s  %s%s" % (
                    r["fromTable"], r["fromColumn"], r["toTable"], r["toColumn"], r["fromCardinality"],
                    r["toCardinality"], "active" if r["isActive"] else "inactive",
                    ", both directions" if r.get("crossFilteringBehavior") == "bothDirections" else ""))
        if b.get("hierarchies"):
            for h in b["hierarchies"]:
                out.append(_kv("hierarchy " + h["name"], " > ".join("%s (%s)" % (l["name"], l["column"]) for l in h.get("levels") or [])))
        if b.get("calculationGroup"):
            out.append(_kv("calc items", ", ".join(i["name"] for i in b["calculationGroup"].get("items") or [])))
        if b.get("roleFilters"):
            for rf in b["roleFilters"]:
                out.append(_kv("role " + rf["role"], (rf.get("filter") or "").strip() or json.dumps(rf.get("columnPermissions")), wide))
        if b.get("tablePermissions"):
            out.append("  table permissions:")
            for p in b["tablePermissions"]:
                out.append("    %s: %s%s" % (p["table"], " ".join((p.get("filterExpression") or "").split())[: (400 if wide else 100)] or "(no filter)",
                                             ("  columns: %s" % json.dumps(p["columnPermissions"])) if p.get("columnPermissions") else ""))
        if b.get("sources"):
            out.append("  sources:")
            for s in b["sources"]:
                out.append("    %s%s%s at step %s" % (s.get("label") or s.get("kind"), (" " + s["database"]) if s.get("database") else "",
                                                       "  (native query)" if s.get("nativeQuery") else "", s.get("step")))
        if b.get("steps"):
            out.append("  steps:")
            for s in b["steps"]:
                flags = "".join(x for x in [" dead" if s.get("dead") else "", " fold-break" if s.get("foldBreak") else "",
                                            " row-by-row" if s.get("rowByRow") else ""])
                out.append("    %2d  %-28s %s%s" % (s.get("index", 0), s.get("name"), s.get("label") or s.get("functionName") or "", flags))
        if b.get("nativeSql"):
            for s in b["nativeSql"]:
                out.append("  native SQL at %s:" % s.get("step"))
                out.extend("    " + ln for ln in str(s.get("sql") or "").splitlines())
        if b.get("findings"):
            out.append("  findings:")
            for f in b["findings"]:
                where = f.get("where") or {}
                out.append("    %-7s %-28s %s%s" % (f["level"], f["code"], " ".join(f["message"].split())[: (400 if wide else 120)],
                                                    ("  @%s:%s" % (where["file"], where["line"])) if where.get("file") else ""))
        out.append("")
    return "\n".join(out).rstrip()
