"""
`report.json` -- one dataset holding everything a reader or a renderer needs.

The showcase that this grew out of fused analysis and rendering in one 2,500-line
script: every chart function reached into module globals that a hardcoded parse had
filled in at import time. That works exactly once, for one model. So the pipeline is cut
in half here.

    parse  ->  DATASET  ->  analysis  ->  render

Everything after this module reads the dataset and nothing else. That buys three things
worth having:

**A skill can read the numbers without rendering anything.** Answering "how many
measures have no description" should not cost an HTML build, and reading a 40KB dataset
should not cost the 240KB a full `--dump` would.

**The renderer cannot disagree with the analysis.** A chart and a table drawn from one
list say the same thing by construction, which was already the rule inside the showcase
and is now the rule across the whole pipeline. The one place the renderer still
re-derived a fact -- the SQL scan -- was caught by an outside reviewer and is gone.

**It is testable.** One fixture in, one dataset out, byte-identical. Key order is fixed
and every list is sorted by a stated key, so a golden-file test is meaningful rather
than flaky, and `sma diff` can compare two runs.

The shape is deliberately close to what the parsers already emit. Renaming their fields
would add a translation layer whose only job is to be wrong occasionally.

Version 2 (Oct-2026) adds to the TMDL part: `relationships` and `relationshipStats`,
`model`, `calculationGroups`, `hierarchies`, `roles`, `perspectives`, `expressions`,
`refreshPolicies`, `coverage`, `suppressed`, a `source` (file and line) on every object
and a `where` on every finding whose subject has one. `unused` is `unreferenced`. The
bridge classifies storage mode from the partition rather than guessing Direct Lake.
"""

import datetime
import hashlib
import json
from pathlib import Path

from . import findings as findings_mod
from . import readiness
from .dax_format import read_format
from .inputs import (MODE_BOTH, MODE_M, MODE_TMDL, LineMap, m_document, subject_name,
                     text_of)
from .vendor.m_parser import Model as MModel
from .vendor.tmdl_parser import Model as TmdlModel

SCHEMA = "semantic-model-analyser/report"
#: Bump on any change a consumer could notice. The renderer checks it and refuses a
#: dataset it does not understand rather than half-rendering one.
VERSION = 2


def _digest(sources):
    """Content hash of the input, so a dataset can be traced to what produced it.

    Over the text and the names, not the files: a paste has no file, and a run on a paste
    has to be as traceable as a run on a folder.
    """
    h = hashlib.sha256()
    for src in sources:
        h.update(src.name.encode("utf-8"))
        h.update(src.text.encode("utf-8"))
    return h.hexdigest()[:16]


def _lower(s):
    return (s or "").lower()


# ----------------------------------------------------------------- the TMDL part


def _dedupe_measures(raw):
    """The parser keys a measure id by name, so two measures of the same name in
    different tables share one id -- the later one's.

    Documented, deliberate, and unreachable in a real model, because TMDL keeps measure
    names unique model-wide. It IS reachable when someone concatenates exports, so the
    collision is resolved once, here, and reported in `merged`. Keep whichever record
    carries the graph: references resolve by name to the one surviving id, so the record
    with the edges is the model's own view of that measure.
    """
    seen, merged = {}, []
    for m in raw:
        prev = seen.get(m["id"])
        if prev is None:
            seen[m["id"]] = m
            continue
        edges = lambda x: len(x["dependsOn"]) + len(x["usedBy"])  # noqa: E731
        keep, drop = (m, prev) if edges(m) > edges(prev) else (prev, m)
        seen[m["id"]] = keep
        merged.append({"name": drop["name"], "table": drop["table"],
                       "folder": drop["folder"]})
    return list(seen.values()), merged


#: How a partition loads, read off its kind, its mode and its source properties. The
#: first version labelled every `entity` partition "Direct Lake entity", and a composite
#: model's DirectQuery-to-another-model partitions are `entity` too.
def _storage(parts, is_calc_group):
    if is_calc_group:
        return "Calculation group"
    if not parts:
        return None
    p = parts[0]
    kind, mode = p.get("kind"), (p.get("mode") or "").strip()
    props = p.get("properties") or {}
    if kind == "calculated":
        return "Calculated"
    if kind == "calculationGroup":
        return "Calculation group"
    if mode == "directLake" or kind == "entity" and not mode and props.get("entityName") \
            and not props.get("expressionSource"):
        return "Direct Lake"
    if mode == "directQuery":
        return "DirectQuery to another model" if props.get("expressionSource") else "DirectQuery"
    if mode == "dual":
        return "Dual"
    if mode == "import" or kind in ("m", "query"):
        return "Import"
    return "Unknown (%s)" % (mode or kind or "no partition")


def _bridge_rows(tmdl_dump, bridge_dump, measures):
    """The one reading that needs the file twice.

    `tmdl_parser` reports which tables each measure's DAX reaches. `m_parser` over the
    SAME TMDL resolves each table's partition M to a connector. Both emit a table name,
    so the two readings join on it with nothing invented in between -- and only together
    can they show a table that DAX references but the TMDL never declares.
    """
    by_table = {q["name"]: q for q in bridge_dump["queries"]}
    declared = {t["name"]: t for t in tmdl_dump["tableRows"]}
    fan = {}
    for m in measures:
        for t in m["allTables"]:
            fan[t] = fan.get(t, 0) + 1

    rows = []
    for name in sorted(set(declared) | set(fan)):
        table = declared.get(name)
        bq = by_table.get(name)
        parts = (table.get("partitions") if table else None) or []
        kind = parts[0].get("kind") if parts else None
        mode = parts[0].get("mode") if parts else None
        props = (parts[0].get("properties") if parts else None) or {}
        conns = sorted({s["label"] for s in (bq["sources"] if bq else []) if s.get("label")})
        storage = _storage(parts, bool(table and table.get("isCalculationGroup")))
        # The classification is analysis, so it belongs to the dataset. A renderer that
        # decided "referenced, not declared" for itself would be a second opinion nobody
        # asked for, and two consumers could reach different ones.
        if not table:
            state, tone = "referenced, not declared", "bad"
        elif storage == "Calculation group":
            state, tone = "calculation group", "note"
        elif kind == "calculated":
            state, tone = "defined in DAX", "note"
        elif conns:
            state, tone = "loaded by M" + (" (%s)" % storage if storage not in (None, "Import") else ""), "ok"
        elif storage == "Direct Lake":
            state, tone = "Direct Lake entity", "note"
        elif storage == "DirectQuery to another model":
            state, tone = "DirectQuery to %s" % (props.get("expressionSource") or "another model"), "note"
        elif storage in ("DirectQuery", "Dual"):
            state, tone = "%s entity" % storage, "note"
        elif kind:
            state, tone = "partition carries no connector", "warn"
        else:
            state, tone = "no load path in this parse", "warn"
        rows.append({
            "name": name, "declared": bool(table), "fan": fan.get(name, 0),
            "kind": kind, "mode": mode, "storage": storage, "conns": conns,
            "state": state, "tone": tone,
            "readBy": sorted(m["name"] for m in measures if name in m["allTables"]),
        })
    rows.sort(key=lambda r: (-r["fan"], r["name"].lower()))
    return rows


def _relationship_stats(relationships, tables):
    touched = {}
    for r in relationships:
        for t in (r["fromTable"], r["toTable"]):
            touched[t] = touched.get(t, 0) + 1
    declared = [t["name"] for t in tables]
    return {
        "total": len(relationships),
        "active": sum(1 for r in relationships if r["isActive"]),
        "inactive": sum(1 for r in relationships if not r["isActive"]),
        "bidirectional": sum(1 for r in relationships if r["crossFilteringBehavior"] == "bothDirections"),
        "manyToMany": sum(1 for r in relationships
                          if r["fromCardinality"] == "many" and r["toCardinality"] == "many"),
        "oneToOne": sum(1 for r in relationships
                        if r["fromCardinality"] == "one" and r["toCardinality"] == "one"),
        "tablesConnected": sorted(touched, key=str.lower),
        "tablesDisconnected": sorted((t for t in declared if t not in touched), key=str.lower),
        "degree": dict(sorted(touched.items(), key=lambda kv: (-kv[1], kv[0].lower()))),
    }


def _coverage(dump, tables, calc_groups, hierarchies, refresh_policies, line_map):
    """What was read, what was recognised, and what the model says it holds that the
    handover did not include."""
    model = dump.get("model") or {}
    refs = model.get("refs") or {}
    parsed = {
        "table": {t["name"] for t in tables},
        "role": {r["name"] for r in dump["roles"]},
        "perspective": {p["name"] for p in dump["perspectives"]},
        "expression": {e["name"] for e in dump["expressions"]},
    }
    declared_not_parsed = {}
    for kind, names in refs.items():
        if kind in parsed:
            missing = sorted(n for n in names if n not in parsed[kind])
            if missing:
                declared_not_parsed[kind] = missing
    return {
        "files": len(line_map.starts) if line_map else 0,
        "objects": {
            "tables": len(tables),
            "measures": len(dump["measures"]),
            "columns": len(dump["columns"]),
            "partitions": sum(len(t.get("partitions") or []) for t in tables),
            "relationships": len(dump["relationships"]),
            "roles": len(dump["roles"]),
            "perspectives": len(dump["perspectives"]),
            "expressions": len(dump["expressions"]),
            "hierarchies": len(hierarchies),
            "calculationGroups": len(calc_groups),
            "calculationItems": sum(len(g["items"]) for g in calc_groups),
            "refreshPolicies": len(refresh_policies),
            "model": 1 if dump.get("model") else 0,
        },
        "unsupported": dump.get("unsupported") or {},
        "declaredNotParsed": declared_not_parsed,
        "diagnostics": dump.get("diagnostics") or [],
    }


def _where(line_map, line):
    return line_map.locate(line) if (line_map is not None and line) else None


def _locate_findings(findings, measures, columns, tables, relationships, calc_groups):
    """A `where` on every finding whose subject is an object with a line."""
    index = {}
    for m in measures:
        index[m["name"]] = m.get("source")
    for c in columns:
        index["%s[%s]" % (c["table"], c["name"])] = c.get("source")
    for t in tables:
        index.setdefault(t["name"], t.get("source"))
    for r in relationships:
        index.setdefault(r["name"], r.get("source"))
    for g in calc_groups:
        for item in g["items"]:
            index.setdefault("%s › %s" % (g["table"], item["name"]), item.get("source"))
    for f in findings:
        f["where"] = index.get(f.get("subject") or "")
    return findings


def model_m_document(dump):
    """The Power Query a model carries itself: every `m` partition and every shared
    expression, re-emitted as one section document for the M parser.

    Returns (text, counts). A PBIP handed over on its own used to get no Power Query
    part at all, so `source = #"Shared Staging"` dangled and a `Value.NativeQuery`
    with `EnableFolding` was never read. The segment is the input: when the input
    contains M, the M is read. Line numbers in that part are lines of this document,
    which the input note says.
    """
    parts, used = [], set()
    counts = {"partitions": 0, "expressions": 0}

    def emit(name, body, kind):
        label = " ".join(name.split()) or kind
        if label in used:
            label = "%s (%s)" % (label, kind)
        used.add(label)
        parts.append('shared #"%s" =\n%s;' % (label.replace('"', '""'), body.strip()))

    for t in dump["tables"]:
        ms = [p for p in (t.get("partitions") or [])
              if p.get("kind") == "m" and (p.get("source") or "").strip()]
        for p in ms:
            emit(t["name"] if len(ms) == 1 else "%s (%s)" % (t["name"], p["name"]),
                 p["source"], "partition")
            counts["partitions"] += 1
    for e in dump["expressions"]:
        if (e.get("expression") or "").strip():
            emit(e["name"], e["expression"], "expression")
            counts["expressions"] += 1
    if not parts:
        return "", counts
    return "section Model;\n\n" + "\n\n".join(parts) + "\n", counts


def build_tmdl(text, line_map=None, config=None, model=None):
    """`text`, not paths: a paste and a folder differ only in provenance, and a folder is
    concatenated into one blob anyway so that cross-file references resolve. `line_map`
    turns the parser's lines in that blob back into a file and a line. `model` is the
    parsed TMDL when the caller already has it."""
    model = model or TmdlModel.from_text(text)
    dump = model.dump()
    # The same TMDL, read a second time by the M parser, which reads the M inside each
    # table's partition. Same input, second reading -- and only together can the two say
    # that a table is referenced by DAX but never declared.
    bridge = MModel.from_text(text).dump()

    measures, merged = _dedupe_measures(dump["measures"])
    measures.sort(key=lambda m: (m["folder"].lower(), m["name"].lower()))
    columns = sorted(dump["columns"], key=lambda c: (c["table"].lower(), c["name"].lower()))
    tables = sorted(dump["tableRows"], key=lambda t: t["name"].lower())
    for rec in measures + columns + tables:
        rec["source"] = _where(line_map, rec.get("line"))
    anatomy = dump["anatomy"]
    bridge_rows = _bridge_rows(dump, bridge, measures)
    unreferenced = sorted(m.name for m in model.unreferenced())
    by_name = {m["name"]: m for m in measures}

    relationships = sorted(dump["relationships"], key=lambda r: (
        _lower(r["fromTable"]), _lower(r["fromColumn"]), _lower(r["toTable"]),
        _lower(r["toColumn"]), r["name"]))
    for r in relationships:
        r["source"] = _where(line_map, r.get("line"))

    calc_groups = []
    hierarchies = []
    refresh_policies = []
    for t in dump["tables"]:
        if t.get("calculationGroup"):
            g = dict(t["calculationGroup"], table=t["name"])
            g["source"] = _where(line_map, g.get("line"))
            for item in g["items"]:
                item["source"] = _where(line_map, item.get("line"))
            calc_groups.append(g)
        for h in t.get("hierarchies") or []:
            h = dict(h)
            h["source"] = _where(line_map, h.get("line"))
            hierarchies.append(h)
        if t.get("refreshPolicy"):
            rp = dict(t["refreshPolicy"])
            rp["source"] = _where(line_map, rp.get("line"))
            refresh_policies.append(rp)
    calc_groups.sort(key=lambda g: g["table"].lower())
    hierarchies.sort(key=lambda h: (h["table"].lower(), h["name"].lower()))
    refresh_policies.sort(key=lambda p: p["table"].lower())

    roles = sorted(dump["roles"], key=lambda r: r["name"].lower())
    perspectives = sorted(dump["perspectives"], key=lambda p: p["name"].lower())
    for rec in roles + perspectives:
        rec["source"] = _where(line_map, rec.get("line"))
    # The M of a shared expression belongs to the Power Query segment; here the object
    # is recorded as an object, with its size, so the TMDL page can say it exists.
    expressions = sorted([{
        "name": e["name"], "kind": e["kind"], "queryGroup": e.get("queryGroup"),
        "lines": (e.get("expression") or "").count("\n") + 1 if e.get("expression") else 0,
        "source": _where(line_map, e.get("line")),
    } for e in dump["expressions"]], key=lambda e: e["name"].lower())

    raw_model = dump.get("model") or {}
    model_info = {
        "name": raw_model.get("name"),
        "culture": raw_model.get("culture"),
        "defaultMode": raw_model.get("defaultMode"),
        "discourageImplicitMeasures": bool(raw_model.get("discourageImplicitMeasures")),
        "compatibilityLevel": raw_model.get("compatibilityLevel"),
        "defaultPowerBIDataSourceVersion": raw_model.get("defaultPowerBIDataSourceVersion"),
        "refs": {k: len(v) for k, v in (raw_model.get("refs") or {}).items()},
        "present": bool(dump.get("model")),
    }

    # Format is decoded once, here, so every consumer reads the same word and the same
    # toggles. Decoding it per chart is how two views of one mask end up disagreeing.
    fmt = {}
    for rec, key in ([(m, "measure:" + m["name"]) for m in measures]
                     + [(c, "column:%s[%s]" % (c["table"], c["name"])) for c in columns]):
        r = read_format(rec.get("formatString"))
        fmt[key] = {"word": r["word"], "flags": r["flags"], "dp": r["dp"],
                    "sections": r["sections"], "mask": rec.get("formatString") or ""}

    found = [f.to_dict() for f in findings_mod.for_tmdl(
        measures, columns, anatomy, bridge_rows, unreferenced, by_name,
        relationships=relationships, tables=tables, calc_groups=calc_groups,
        model=model_info, config=config)]
    found = _locate_findings(found, measures, columns, tables, relationships, calc_groups)
    kept, suppressed = findings_mod.apply_config(found, config)

    part = {
        "totals": {
            "measures": len(measures),
            "columns": len(columns),
            "tables": len(tables),
            "folders": len(dump["folders"]),
            "links": dump["linkCount"],
            "described": sum(1 for m in measures if (m.get("description") or "").strip()),
            "formatted": sum(1 for m in measures if m.get("formatString")),
            "hidden": sum(1 for m in measures if m.get("isHidden")),
            "calculatedColumns": sum(1 for c in columns if c.get("isCalculated")),
            "unreferenced": len(unreferenced),
            "relationships": len(relationships),
            "roles": len(roles),
            "perspectives": len(perspectives),
            "expressions": len(expressions),
            "hierarchies": len(hierarchies),
            "calculationGroups": len(calc_groups),
            "calculationItems": sum(len(g["items"]) for g in calc_groups),
            "refreshPolicies": len(refresh_policies),
            "errors": sum(1 for f in kept if f["level"] == "error"),
        },
        "measures": measures,
        "columns": columns,
        "tables": tables,
        "anatomy": anatomy,
        "folders": sorted(dump["folders"], key=str.lower) if dump["folders"] else [],
        "bridge": bridge_rows,
        "relationships": relationships,
        "relationshipStats": _relationship_stats(relationships, tables),
        "model": model_info,
        "calculationGroups": calc_groups,
        "hierarchies": hierarchies,
        "refreshPolicies": refresh_policies,
        "roles": roles,
        "perspectives": perspectives,
        "expressions": expressions,
        "unreferenced": unreferenced,
        "merged": merged,
        "format": fmt,
        "coverage": _coverage(dump, tables, calc_groups, hierarchies, refresh_policies, line_map),
        "diagnostics": dump.get("diagnostics") or [],
        "parserVersion": dump.get("version"),
        "findings": kept,
        "suppressed": suppressed,
    }
    return part


# -------------------------------------------------------------------- the M part


def build_m(text, config=None):
    dump = MModel.from_text(text).dump()
    queries = sorted(dump["queries"], key=lambda q: (q["layer"], q["name"].lower()))

    sql = []
    for q in queries:
        for src in q["sources"]:
            if src.get("nativeQuery"):
                sql.append({
                    "query": q["name"],
                    "step": src.get("step"),
                    "line": src.get("line"),
                    "connector": src.get("label"),
                    "function": src.get("functionName"),
                    "kind": src.get("kind"),
                    "database": src.get("database"),
                    "sql": src["nativeQuery"],
                    "facts": findings_mod.sql_facts(src["nativeQuery"]),
                })
    sql.sort(key=lambda r: (-r["facts"]["lines"], r["query"].lower()))

    # The M parser emits its own findings, per query and per model. They are collected
    # rather than recomputed: this has no better view of an M query than the parser that
    # read it. Two are re-levelled by `relevel_m`, and it says why.
    found = [dict(findings_mod.relevel_m(q, f), subject=q["name"])
             for q in queries for f in q["findings"]]
    # A model-level M finding has no query; what the parser put in its `step` slot is
    # the subject (the endpoint six queries open separately), so it is read as one.
    found += [dict(f, subject=f.get("step") or "", step=None) for f in dump["modelFindings"]]
    for f in found:
        f["where"] = None
    found.sort(key=lambda f: (findings_mod.LEVEL_ORDER.get(f["level"], 9),
                              f["code"], (f.get("subject") or "").lower()))
    kept, suppressed = findings_mod.apply_config(found, config)

    part = {
        "totals": {
            "queries": len(queries),
            "steps": dump["stepCount"],
            "links": dump["linkCount"],
            "layers": len(dump["layers"]),
            "dead": sum(1 for q in queries for s in q["steps"] if s["dead"]),
            "withNativeSql": len(sql),
            "sqlLines": sum(r["facts"]["lines"] for r in sql),
            "barriers": sum(1 for q in queries if q["foldBreakStep"]),
            "errors": sum(1 for f in kept if f["level"] == "error"),
        },
        "queries": queries,
        "layers": dump["layers"],
        "sourceKinds": sorted(dump["sourceKinds"]),
        "sql": sql,
        "format": dump.get("format"),
        "diagnostics": dump.get("diagnostics") or [],
        "parserVersion": dump.get("version"),
        "findings": kept,
        "suppressed": suppressed,
    }
    return part


# ------------------------------------------------------------------ the whole thing


def build(resolved, label=None, config=None):
    """One dataset for one run. `resolved` comes from `inputs.resolve()`; `config` is
    the parsed rules file, or None for the defaults."""
    mode = resolved["mode"]
    cfg = findings_mod.merge_config(config)
    report = {
        "schema": SCHEMA,
        "version": VERSION,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc)
                               .replace(microsecond=0).isoformat(),
        "label": label or "",
        "input": {
            "mode": mode,
            # What was handed over, named the way a reader would name it, so every page
            # can say which model it is about even when no --label was given.
            "given": list(resolved.get("given") or []),
            "subject": subject_name(resolved.get("given") or []),
            "notes": resolved["notes"],
            "tmdl": [s.to_dict() for s in resolved["tmdl"]],
            "m": [s.to_dict() for s in resolved["m"]],
            "digest": _digest(resolved["tmdl"] + resolved["m"]),
        },
        "config": {
            "applied": bool(config),
            "thresholds": cfg["thresholds"],
            "severity": cfg["severity"],
            "suppress": cfg["suppress"],
        },
        "parts": [],
        "help": findings_mod.HELP,
    }
    tm = None
    if mode in (MODE_TMDL, MODE_BOTH):
        tmdl_text = text_of(resolved["tmdl"])
        tm = TmdlModel.from_text(tmdl_text)
        report["tmdl"] = build_tmdl(tmdl_text, LineMap(resolved["tmdl"]), config, model=tm)
        report["tmdl"]["aiReadiness"] = readiness.build(
            report["tmdl"], resolved["tmdl"], resolved.get("given"))
        report["parts"].append("tmdl")
    if mode in (MODE_M, MODE_BOTH):
        # Normalising several sources into one document can rename a placeholder and can
        # hit a name collision. Both are recorded on the input rather than swallowed.
        m_text, m_notes = m_document(resolved["m"])
        report["input"]["notes"] = list(report["input"]["notes"]) + m_notes
        report["m"] = build_m(m_text, config)
        report["parts"].append("m")
    elif tm is not None:
        # No Power Query was handed over, but the model carries some. Given M wins when
        # both exist, because an exported .pq is the fuller text of the same queries.
        derived, counts = model_m_document(tm.dump())
        if derived:
            report["input"]["derivedM"] = counts
            report["input"]["notes"] = list(report["input"]["notes"]) + [
                "Power Query read from the model itself: %d partition%s and %d shared "
                "expression%s. Lines in that part count within the derived document, "
                "not a file." % (counts["partitions"], "" if counts["partitions"] == 1 else "s",
                                 counts["expressions"], "" if counts["expressions"] == 1 else "s")]
            report["m"] = build_m(derived, config)
            report["parts"].append("m")

    # Part M must never read a TMDL field. Asserted rather than trusted, because the two
    # readings sharing a process is exactly how a Power-Query-only run would quietly
    # start depending on a semantic model it was never given.
    if "m" in report:
        assert "measures" not in report["m"], "Part M leaked a TMDL field"
    return report


def dumps(report):
    """Stable text. Keys keep insertion order, so a diff of two runs is readable."""
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def load(path):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if report.get("schema") != SCHEMA:
        raise ValueError("not a %s dataset: %r" % (SCHEMA, report.get("schema")))
    if report.get("version") != VERSION:
        raise ValueError(
            "dataset is version %r, this build reads version %d. Re-run the analysis "
            "rather than rendering a shape it may not match."
            % (report.get("version"), VERSION))
    return report


def load_config(path):
    """A rules file: `thresholds`, `severity`, `suppress`. Documented in `tmdl_findings`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("a rules file is a JSON object with thresholds, severity, suppress")
    return data
