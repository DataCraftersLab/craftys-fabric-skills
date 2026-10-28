"""
The analysis layer: rules that name something, and the SQL scan.

Two rules govern everything in here, and the second is the one that keeps the output
worth reading.

**A finding names one specific thing.** "9 measures use FILTER" is a count, not a
finding. "These 9 measures use FILTER" is one, because a reader can open one of them.
Every rule returns a subject.

**A modelling observation is never a verdict.** Its `level` is `warning` or `info`.
This has seen no report, no data and no meeting, so it has no standing to call a
modeller's decision wrong -- a lesson the M parser's own source records, from an
engagement where two "wrong" revenue definitions turned out to be a deliberate
controller decision. `error` exists for one thing only: text that could not be read.

The TMDL rules live in `tmdl_findings.py` and are re-exported here so a caller has one
import. The M rules are not reimplemented: `m_parser.py` already emits findings while it
reads, and this has no better view of an M query than the parser that parsed it. Two of
its findings are re-levelled here, and the reason for each is recorded on `relevel_m`.
"""

import re

from .tmdl_findings import HELP as TMDL_HELP
from .tmdl_findings import (  # noqa: F401
    ERROR_CODES, LEVEL_ORDER, Finding, apply_config, collect as for_tmdl, merge_config)

B = chr(92)

#: The M parser's own codes, with the same "how this helps" line the TMDL rules carry.
#: Without these the Power Query findings arrive as bare messages while the semantic
#: model findings each explain themselves, which reads as though one half was thought
#: about and the other was not.
M_HELP = {
    "native-query": (
        "The source is handed a query of its own. For most connectors that is a folding "
        "barrier: nothing after it is pushed back to the source. Value.NativeQuery with "
        "EnableFolding=true is the exception, and is said to be when the step carries it. "
        "Read the SQL to see how much work was pushed down."),
    "no-fold": (
        "A potential folding barrier: from this step on, rows probably travel before the "
        "rest of the query runs. A reason, never a verdict: the step may be the only way to "
        "express it, and only a trace of the refresh can confirm what folded."),
    "dead-step": (
        "A step nothing downstream reads. Usually an abandoned edit, occasionally a "
        "deliberate staging point somebody forgot to reference."),
    "duplicate-source": (
        "Two queries opening the same source separately. Each is a connection and a "
        "credential prompt; one shared staging query is often the intent."),
    "inline-data": (
        "Data written into the query itself. It refreshes when someone edits the query, "
        "which is the kind of dependency nobody documents."),
    "chained-model": (
        "This reads another semantic model. Refresh order stops being local and the "
        "lineage leaves this file."),
    "row-by-row": (
        "A lambda reading a whole table per row. The shape that turns a fast query slow "
        "as the table grows, and it will not fold."),
    "recursive": (
        "A query that reaches itself. Worth confirming it is a deliberate recursion and "
        "not an accidental reference."),
    "unresolved-reference": (
        "A name the parser could not resolve to a query in what it was given. Either the "
        "handover is partial or the reference is stale."),
    "parse-recovered": (
        "Part of this query could not be read. The parser recovered and read the rest, so "
        "every count and reference for this query is a lower bound, not a fact. A parse "
        "failure is the one thing in this report that is an error rather than an observation."),
}

HELP = dict(TMDL_HELP)
HELP.update(M_HELP)

#: M codes that are parse failures rather than observations, and so carry `error`.
M_ERROR_CODES = {"parse-recovered"}
ALL_ERROR_CODES = ERROR_CODES | M_ERROR_CODES

_ENABLE_FOLDING_RE = re.compile(r"EnableFolding\s*=\s*true", re.I)


def relevel_m(query, finding):
    """The M parser's finding, re-read with what the analysis layer knows.

    Two adjustments, both recorded here rather than in the vendored parser so the
    parser stays a port:

    * `parse-recovered` is an `error`. The parser called it a warning; a part of the
      text that could not be read is not an observation about the model.
    * `native-query` on `Value.NativeQuery` with `EnableFolding=true` is the exception
      to the barrier, so it is said to be, and the finding is a note rather than a
      claim that folding stopped. The option is read off the step's own text.
    """
    f = dict(finding)
    if f["code"] == "parse-recovered":
        f["level"] = "error"
        return f
    if f["code"] == "native-query":
        step = next((s for s in query.get("steps", []) if s["name"] == f.get("step")), None)
        source = next((s for s in query.get("sources", [])
                       if s.get("step") == f.get("step") and s.get("nativeQuery")), None)
        fn = (source or {}).get("functionName") or ""
        if fn == "Value.NativeQuery" and step and _ENABLE_FOLDING_RE.search(step.get("expression") or ""):
            f["level"] = "info"
            f["message"] = re.sub(
                r"Folding stops at the source, which is doing the work\.?",
                "Value.NativeQuery with EnableFolding=true, so folding may continue past it.",
                f["message"])
        else:
            f["message"] = re.sub(
                r"Folding stops at the source, which is doing the work\.?",
                "A potential folding barrier: for most connectors nothing after it folds.",
                f["message"])
    return f


# --------------------------------------------------------------- the native SQL scan

SQL_KW = (
    "SELECT|FROM|WHERE|INNER|LEFT|RIGHT|FULL|OUTER|CROSS|JOIN|ON|GROUP|ORDER|BY|HAVING|"
    "UNION|ALL|DISTINCT|TOP|AS|AND|OR|NOT|IN|EXISTS|BETWEEN|LIKE|IS|NULL|CASE|WHEN|THEN|"
    "ELSE|END|WITH|OVER|PARTITION|ASC|DESC|LIMIT|OFFSET|FETCH|FIRST|ROWS|ONLY|CAST|"
    "CONVERT|COALESCE|ISNULL|SUM|COUNT|AVG|MIN|MAX|DATEADD|GETDATE"
)
# Patterns are assembled from chr(92) rather than written with escapes: a literal double
# backslash has been collapsed by a shell heredoc in this project before, and the failure
# was silent -- "strip escaped literals" became "strip every decimal point".
SQL_SELECT_RE = re.compile("SELECT" + B + "s+(.*?)" + B + "s+FROM" + B + "b", re.I | re.S)
SQL_TABLE_RE = re.compile(
    B + "b(?:FROM|JOIN)" + B + "s+([A-Za-z_]" + B + "w*(?:" + B + "." + B + "w+)*)", re.I)
SQL_COL_SPLIT = re.compile(r",(?![^()]*\))")


def sql_facts(sql):
    """A lexical scan of decoded native SQL, and only that.

    Not a SQL parse. It reads keywords off text, so a keyword inside a comment or a
    string literal is counted, and it makes no claim about what the query returns. That
    limit is stated wherever the numbers are shown, because a scan presented as a parse
    is worse than no scan.

    It is still worth having: whether the source does the filtering is the difference
    between a query that folds and one that ships a table over the wire, and nothing
    else in the pipeline can see it.
    """
    sel = SQL_SELECT_RE.search(sql)
    star, cols = False, None
    if sel:
        body = sel.group(1)
        star = "*" in body
        cols = len([c for c in SQL_COL_SPLIT.split(body) if c.strip()])
    has = lambda pat: bool(re.search(pat, sql, re.I))  # noqa: E731
    return {
        "tables": sorted(set(SQL_TABLE_RE.findall(sql))),
        "cols": "*" if star else cols,
        "joins": len(re.findall(B + "bJOIN" + B + "b", sql, re.I)),
        "where": has(B + "bWHERE" + B + "b"),
        "group": has(B + "bGROUP" + B + "s+BY" + B + "b"),
        "order": has(B + "bORDER" + B + "s+BY" + B + "b"),
        # TOP, LIMIT, and the ANSI `FETCH FIRST n ROWS` / `OFFSET … FETCH NEXT` forms.
        # The renderer used to count the last two and this did not, which was the one
        # place the page disagreed with the dataset -- so the renderer's copy is gone.
        "cap": has(B + "bTOP" + B + "b|" + B + "bLIMIT" + B + "b|" + B + "bFETCH" + B
                   + "s+(?:FIRST|NEXT)" + B + "b"),
        "lines": len(sql.splitlines()),
        "chars": len(sql),
    }


# ------------------------------------------------------------------------- summary


def summarise(report):
    """Counts a caller can quote without walking the whole dataset."""
    out = {"errors": 0, "warnings": 0, "notes": 0, "suppressed": 0, "byCode": {}, "byPart": {}}
    for part in report.get("parts", []):
        fs = report[part].get("findings", [])
        err = sum(1 for f in fs if f["level"] == "error")
        warn = sum(1 for f in fs if f["level"] == "warning")
        out["byPart"][part] = {"total": len(fs), "errors": err, "warnings": warn,
                               "notes": len(fs) - warn - err,
                               "suppressed": len(report[part].get("suppressed", []))}
        out["errors"] += err
        out["warnings"] += warn
        out["notes"] += len(fs) - warn - err
        out["suppressed"] += len(report[part].get("suppressed", []))
        for f in fs:
            slot = out["byCode"].setdefault(f["code"], {"error": 0, "warning": 0, "info": 0,
                                                        "part": part})
            slot[f["level"]] = slot.get(f["level"], 0) + 1
    out["byCode"] = dict(sorted(
        out["byCode"].items(),
        key=lambda kv: (-(kv[1].get("error", 0)), -(kv[1].get("warning", 0)),
                        -(kv[1].get("info", 0)), kv[0])))
    return out


def worst_first(report, part=None, level=None, limit=None):
    """Findings, errors first, then warnings, then by code, then by subject."""
    parts = [part] if part else report.get("parts", [])
    fs = [dict(f, part=p) for p in parts for f in report[p].get("findings", [])]
    if level:
        wanted = {"warning": {"error", "warning"}}.get(level, {level})
        fs = [f for f in fs if f["level"] in wanted]
    fs.sort(key=lambda f: (LEVEL_ORDER.get(f["level"], 9), f["code"],
                           (f.get("subject") or "").lower()))
    return fs[:limit] if limit else fs
