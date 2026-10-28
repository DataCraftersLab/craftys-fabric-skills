"""
`sma diff before.json after.json` -- what changed between two runs.

The dataset was built to be diffed: fixed key order, every list sorted by a stated key.
This is the command that reads two of them and says, in model terms rather than JSON
terms, what was added, removed and changed -- and for a changed measure, which measures
sit downstream of it in the newer run, because that is the question the diff is asked.

Objects are matched by name (measures, tables, queries), by `Table[Column]` (columns),
and by their two ends (relationships), since a relationship's name is often a GUID that
changes on every save.
"""

import re

MEASURE_FIELDS = ("expression", "formatString", "description", "isHidden", "displayFolder",
                  "table")
COLUMN_FIELDS = ("dataType", "summarizeBy", "sortByColumn", "isHidden", "expression",
                 "formatString", "description", "isCalculated")
TABLE_FIELDS = ("isHidden", "isCalculated", "isCalculationGroup", "description")
RELATIONSHIP_FIELDS = ("isActive", "crossFilteringBehavior", "fromCardinality",
                       "toCardinality", "relyOnReferentialIntegrity")
QUERY_FIELDS = ("kind", "sourceKind", "foldBreakStep")


def _norm(value):
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return value


def _changed_fields(a, b, fields):
    return [f for f in fields if _norm(a.get(f)) != _norm(b.get(f))]


def _diff_keyed(before, after, key, fields, extra=None):
    """added / removed / changed over two lists of dicts keyed by `key(rec)`."""
    a = {key(r): r for r in before}
    b = {key(r): r for r in after}
    added = sorted(k for k in b if k not in a)
    removed = sorted(k for k in a if k not in b)
    changed = []
    for k in sorted(set(a) & set(b)):
        fields_changed = _changed_fields(a[k], b[k], fields)
        more = extra(a[k], b[k]) if extra else []
        if fields_changed or more:
            changed.append({"key": k, "fields": fields_changed + more})
    return {"added": added, "removed": removed, "changed": changed}


def _table_extra(a, b):
    out = []
    pa = [(p.get("kind"), p.get("mode")) for p in a.get("partitions") or []]
    pb = [(p.get("kind"), p.get("mode")) for p in b.get("partitions") or []]
    if pa != pb:
        out.append("partitions")
    if (a.get("measures"), a.get("columns")) != (b.get("measures"), b.get("columns")):
        out.append("counts")
    return out


def _query_extra(a, b):
    out = []
    sa = [s["name"] for s in a.get("steps", [])]
    sb = [s["name"] for s in b.get("steps", [])]
    if sa != sb:
        out.append("steps")
    na = [s.get("nativeQuery") for s in a.get("sources", []) if s.get("nativeQuery")]
    nb = [s.get("nativeQuery") for s in b.get("sources", []) if s.get("nativeQuery")]
    if [_norm(x) for x in na] != [_norm(x) for x in nb]:
        out.append("nativeQuery")
    if sorted(a.get("dependsOn", [])) != sorted(b.get("dependsOn", [])):
        out.append("dependsOn")
    return out


def _downstream(measures, name):
    """Every measure that reads `name`, transitively, in the newer run."""
    by_name = {m["name"]: m for m in measures}
    seen, order, queue = {name}, [], list(by_name.get(name, {}).get("usedBy", []))
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        order.append(cur)
        queue.extend(by_name.get(cur, {}).get("usedBy", []))
    return order


def diff_reports(before, after):
    out = {"before": {"label": before.get("label"), "generatedAt": before.get("generatedAt"),
                      "digest": before.get("input", {}).get("digest")},
           "after": {"label": after.get("label"), "generatedAt": after.get("generatedAt"),
                     "digest": after.get("input", {}).get("digest")},
           "parts": {}}
    if "tmdl" in before and "tmdl" in after:
        tb, ta = before["tmdl"], after["tmdl"]
        measures = _diff_keyed(tb["measures"], ta["measures"], lambda m: m["name"], MEASURE_FIELDS)
        for ch in measures["changed"]:
            ch["downstream"] = _downstream(ta["measures"], ch["key"])
        for name in measures["removed"]:
            # What the older run said depended on it: those references now dangle.
            ch_down = _downstream(tb["measures"], name)
            if ch_down:
                measures.setdefault("removedWithDependants", []).append(
                    {"key": name, "downstream": ch_down})
        out["parts"]["tmdl"] = {
            "measures": measures,
            "columns": _diff_keyed(tb["columns"], ta["columns"],
                                   lambda c: "%s[%s]" % (c["table"], c["name"]), COLUMN_FIELDS),
            "tables": _diff_keyed(tb["tables"], ta["tables"], lambda t: t["name"],
                                  TABLE_FIELDS, _table_extra),
            "relationships": _diff_keyed(
                tb.get("relationships", []), ta.get("relationships", []),
                lambda r: "%s[%s] -> %s[%s]" % (r["fromTable"], r["fromColumn"],
                                                r["toTable"], r["toColumn"]),
                RELATIONSHIP_FIELDS),
            "findings": _diff_findings(tb.get("findings", []), ta.get("findings", [])),
            "totals": {k: (tb["totals"].get(k), ta["totals"].get(k))
                       for k in sorted(set(tb["totals"]) | set(ta["totals"]))
                       if tb["totals"].get(k) != ta["totals"].get(k)},
        }
    elif "tmdl" in before or "tmdl" in after:
        out["parts"]["tmdl"] = {"note": "only one of the two runs has a TMDL part"}
    if "m" in before and "m" in after:
        mb, ma = before["m"], after["m"]
        out["parts"]["m"] = {
            "queries": _diff_keyed(mb["queries"], ma["queries"], lambda q: q["name"],
                                   QUERY_FIELDS, _query_extra),
            "findings": _diff_findings(mb.get("findings", []), ma.get("findings", [])),
            "totals": {k: (mb["totals"].get(k), ma["totals"].get(k))
                       for k in sorted(set(mb["totals"]) | set(ma["totals"]))
                       if mb["totals"].get(k) != ma["totals"].get(k)},
        }
    elif "m" in before or "m" in after:
        out["parts"]["m"] = {"note": "only one of the two runs has a Power Query part"}
    return out


def _diff_findings(before, after):
    key = lambda f: (f["code"], f.get("subject") or "")  # noqa: E731
    a, b = {key(f) for f in before}, {key(f) for f in after}
    return {"added": sorted("%s: %s" % k for k in b - a),
            "resolved": sorted("%s: %s" % k for k in a - b)}


def format_text(d):
    lines = ["before  %s  %s" % (d["before"].get("generatedAt"), d["before"].get("label") or ""),
             "after   %s  %s" % (d["after"].get("generatedAt"), d["after"].get("label") or ""), ""]
    for part, body in d["parts"].items():
        lines.append("== %s" % part)
        header_at = len(lines)
        if "note" in body:
            lines.append("  " + body["note"])
            continue
        for kind in ("measures", "columns", "tables", "relationships", "queries"):
            sec = body.get(kind)
            if not sec:
                continue
            n = len(sec["added"]) + len(sec["removed"]) + len(sec["changed"])
            if not n:
                continue
            lines.append("  %s: +%d -%d ~%d" % (kind, len(sec["added"]), len(sec["removed"]),
                                                 len(sec["changed"])))
            for k in sec["added"]:
                lines.append("    + %s" % k)
            for k in sec["removed"]:
                lines.append("    - %s" % k)
            for ch in sec["changed"]:
                tail = ""
                if ch.get("downstream"):
                    tail = "  -> %d downstream: %s%s" % (
                        len(ch["downstream"]), ", ".join(ch["downstream"][:5]),
                        ", ..." if len(ch["downstream"]) > 5 else "")
                lines.append("    ~ %s  (%s)%s" % (ch["key"], ", ".join(ch["fields"]), tail))
            for rd in sec.get("removedWithDependants", []):
                lines.append("    ! %s was read by %d: %s" % (
                    rd["key"], len(rd["downstream"]), ", ".join(rd["downstream"][:5])))
        f = body.get("findings") or {}
        if f.get("added") or f.get("resolved"):
            lines.append("  findings: +%d new, %d resolved" % (len(f["added"]), len(f["resolved"])))
            for k in f["added"][:20]:
                lines.append("    + %s" % k)
            for k in f["resolved"][:20]:
                lines.append("    - %s" % k)
        if body.get("totals"):
            lines.append("  totals: " + ", ".join("%s %s->%s" % (k, a, b)
                                                   for k, (a, b) in body["totals"].items()))
        if len(lines) == header_at:
            lines.append("  no difference in what the two runs read")
        lines.append("")
    if not d["parts"]:
        lines.append("no difference in what the two runs read")
    return "\n".join(lines)
