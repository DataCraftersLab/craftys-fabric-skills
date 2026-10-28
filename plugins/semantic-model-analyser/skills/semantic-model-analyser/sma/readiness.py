"""
AI readiness: a checklist of what Copilot, data agents and the Copilot app read from a
semantic model, each check passed, failed or not knowable from this input.

Built from the TMDL part of the dataset plus two things the parser does not model: the
culture file's linguistic schema (where Prep data for AI saves AI instructions and the
AI data schema) and the PBIP folders beside `definition` (verified answers). The result
is stored on the dataset, so the page reads it like every other reading.

A check that the input cannot answer is `na`, never `fail`: a TMDL export carries no
verified answers, and saying the model has none would be a claim about the model made
from the absence of a file.
"""

import json
import re
from pathlib import Path

from .inputs import _definition_root

PASS, FAIL, NA = "pass", "fail", "na"

ID_NAME = re.compile(r"(?:^|[\s_])(?:id|key)$|(?<=[a-z])(?:ID|Id|Key)$", re.I)
AUTO_DATE = re.compile(r"^(LocalDateTable_|DateTableTemplate_)")

WHY = (
    "Copilot in Power BI, the Copilot app, Cowork and Fabric data agents answer from the "
    "semantic model, not from the report. They read its names, relationships, "
    "descriptions and the Prep data for AI settings, and nothing else. A model that is "
    "clean, described and prepared gets correct answers; a model that is not gets "
    "confident wrong ones. This checklist shows which of those the model already has."
)


def _linguistic(text):
    """The JSON after `linguisticMetadata =`, with TMDL's indentation stripped."""
    i = text.find("linguisticMetadata")
    if i < 0:
        return None
    j = text.find("{", i)
    if j < 0:
        return None
    body = "\n".join(line.lstrip("\t") for line in text[j:].splitlines())
    try:
        return json.JSONDecoder().raw_decode(body)[0]
    except ValueError:
        return None


def culture_info(sources):
    """What the culture files say, summed over every culture handed over."""
    out = {"files": 0, "read": 0, "languages": [], "entities": 0, "terms": 0,
           "authoredTerms": 0, "relationships": 0, "instructions": "", "keys": []}
    for s in sources:
        text = s.text or ""
        if not text.lstrip().startswith("cultureInfo"):
            continue
        out["files"] += 1
        lm = _linguistic(text)
        if not isinstance(lm, dict):
            continue
        out["read"] += 1
        out["languages"].append(lm.get("Language") or "")
        out["keys"] = sorted(set(out["keys"]) | set(lm.keys()))
        ents = lm.get("Entities") or {}
        out["entities"] += len(ents)
        for e in ents.values():
            for term in e.get("Terms") or []:
                for v in term.values():
                    out["terms"] += 1
                    if (v or {}).get("State") not in ("Generated", "Suggested", "Deleted"):
                        out["authoredTerms"] += 1
        out["relationships"] += len(lm.get("Relationships") or {})
        ci = (lm.get("CustomInstructions") or "").strip()
        if ci and not out["instructions"]:
            out["instructions"] = ci
    return out


def companions(given):
    """Verified answers saved beside the definition, when a PBIP folder was handed over.

    `looked` says whether there was a model folder to look in at all; without one the
    verified answers check is `na`."""
    out = {"looked": False, "folder": False, "verifiedAnswers": 0, "triggerPrompts": 0}
    for g in given or []:
        p = Path(g.get("path") or "")
        if not p.is_dir():
            continue
        root = _definition_root(p)
        if root is None:
            continue
        owner = root.parent
        out["looked"] = True
        defs = owner / "VerifiedAnswers" / "definitions"
        if not defs.is_dir():
            continue
        out["folder"] = True
        for d in sorted(defs.iterdir()):
            f = d / "definition.json"
            if not f.is_file():
                continue
            out["verifiedAnswers"] += 1
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                out["triggerPrompts"] += len(data.get("triggerPrompts") or [])
            except (OSError, ValueError):
                pass
    return out


def _check(key, label, status, detail, count=None, total=None):
    return {"key": key, "label": label, "status": status, "detail": detail,
            "count": count, "total": total}


def _ratio(key, label, items, noun):
    total = len(items)
    done = sum(1 for x in items if (x.get("description") or "").strip())
    if not total:
        return _check(key, label, NA, "No %s in the model." % noun)
    status = PASS if done == total else FAIL
    detail = "%d of %d %s described." % (done, total, noun)
    return _check(key, label, status, detail, done, total)


def build(tmdl, sources, given):
    """The readiness block for `report["tmdl"]["aiReadiness"]`."""
    tables, columns, measures = tmdl["tables"], tmdl["columns"], tmdl["measures"]
    rels, stats, model = tmdl["relationships"], tmdl["relationshipStats"], tmdl["model"]
    hidden_tables = {t["name"] for t in tables if t.get("isHidden")}
    visible_cols = [c for c in columns if not c.get("isHidden") and c["table"] not in hidden_tables]
    visible_measures = [m for m in measures if not m.get("isHidden")]
    visible_tables = [t for t in tables if not t.get("isHidden")]

    found = {}
    for f in tmdl.get("findings") or []:
        found[f["code"]] = found.get(f["code"], 0) + 1

    foundations = []
    bad_rel = stats.get("bidirectional", 0) + stats.get("manyToMany", 0)
    foundations.append(_check(
        "star", "Star schema: single-direction, one-to-many relationships",
        PASS if rels and not bad_rel else (NA if not rels else FAIL),
        ("%d relationships, none bi-directional or many-to-many." % len(rels)) if rels and not bad_rel
        else ("No relationships in the input." if not rels else
              "%d bi-directional, %d many-to-many out of %d." % (
                  stats.get("bidirectional", 0), stats.get("manyToMany", 0), len(rels))),
        len(rels) - bad_rel if rels else None, len(rels) or None))

    measure_only = {t["name"] for t in tables if t.get("measures") and not t.get("columns")}
    loose = [n for n in stats.get("tablesDisconnected") or [] if n not in measure_only]
    foundations.append(_check(
        "connected", "Every data table is related",
        PASS if not loose else FAIL,
        "All data tables take part in a relationship." if not loose
        else "Not related: %s." % ", ".join(loose[:6]), None, None))

    date_tables = [t["name"] for t in tables
                   if (t.get("properties") or {}).get("dataCategory") == "Time"]
    foundations.append(_check(
        "date", "A date table is marked",
        PASS if date_tables else FAIL,
        ("Marked: %s." % ", ".join(date_tables)) if date_tables
        else "No table is marked as a date table.", None, None))

    auto = [t["name"] for t in tables if AUTO_DATE.match(t["name"])]
    foundations.append(_check(
        "autodate", "Auto date/time is off",
        PASS if not auto else FAIL,
        "No hidden auto date tables." if not auto
        else "%d auto date tables in the model." % len(auto), None, None))

    foundations.append(_check(
        "implicit", "Implicit measures are discouraged",
        PASS if model.get("discourageImplicitMeasures") else (NA if not model.get("present") else FAIL),
        "Copilot is steered to the model's own measures." if model.get("discourageImplicitMeasures")
        else ("No model block in the input." if not model.get("present")
              else "discourageImplicitMeasures is not set."), None, None))

    fk_cols = {(r["fromTable"], r["fromColumn"]) for r in rels}
    shown_keys = [c for c in visible_cols
                  if ID_NAME.search(c["name"]) or (c["table"], c["name"]) in fk_cols]
    foundations.append(_check(
        "keys", "Key, ID and relationship columns are hidden",
        PASS if not shown_keys else FAIL,
        "No visible key, ID or relationship column on the many side." if not shown_keys
        else "Visible: %s." % ", ".join("%s[%s]" % (c["table"], c["name"]) for c in shown_keys[:6])
        + (" and %d more" % (len(shown_keys) - 6) if len(shown_keys) > 6 else ""),
        len(shown_keys), None))

    unformatted = [m for m in measures if not m.get("formatString")]
    foundations.append(_check(
        "format", "Every measure has a format string",
        PASS if measures and not unformatted else (NA if not measures else FAIL),
        "%d of %d measures formatted." % (len(measures) - len(unformatted), len(measures))
        if measures else "No measures in the model.",
        len(measures) - len(unformatted), len(measures) or None))

    summed = found.get("text-column-summarized", 0)
    foundations.append(_check(
        "summarize", "Text columns are not summarised",
        PASS if not summed else FAIL,
        "No text column carries a default aggregation." if not summed
        else "%d text columns set to summarise." % summed, summed, None))

    descriptions = [
        _ratio("desc-tables", "Tables are described", visible_tables, "visible tables"),
        _ratio("desc-columns", "Columns are described", visible_cols, "visible columns"),
        _ratio("desc-measures", "Measures are described", visible_measures, "visible measures"),
    ]

    cult = culture_info(sources)
    comp = companions(given)
    prep = []
    if not cult["files"]:
        prep.append(_check("lsdl", "Linguistic schema (Q&A) is present", NA,
                           "No culture file in this input. Export the whole definition "
                           "folder to read Prep data for AI.", None, None))
    else:
        prep.append(_check("lsdl", "Linguistic schema (Q&A) is present",
                           PASS if cult["read"] else FAIL,
                           "%d entities, %d relationships in the linguistic schema." % (
                               cult["entities"], cult["relationships"]) if cult["read"]
                           else "The culture file has no readable linguistic metadata.",
                           cult["entities"], None))
    ci = cult["instructions"]
    prep.append(_check(
        "instructions", "AI instructions are written",
        NA if not cult["read"] else (PASS if ci else FAIL),
        ("%d characters of instructions. Opens with: “%s”" % (
            len(ci), ci.splitlines()[0][:90])) if ci
        else ("Not knowable from this input." if not cult["read"]
              else "No AI instructions in the linguistic schema."),
        len(ci) or None, None))
    prep.append(_check(
        "synonyms", "Synonyms are in place",
        NA if not cult["read"] else (PASS if cult["terms"] else FAIL),
        ("%d terms on %d entities, %d added by hand." % (
            cult["terms"], cult["entities"], cult["authoredTerms"])) if cult["terms"]
        else ("Not knowable from this input." if not cult["read"] else "No synonyms."),
        cult["terms"] or None, None))
    if comp["folder"]:
        va = comp["verifiedAnswers"]
        prep.append(_check(
            "verified", "Verified answers are set",
            PASS if va else FAIL,
            ("%d verified answers with %d trigger phrases." % (va, comp["triggerPrompts"])) if va
            else "The VerifiedAnswers folder is empty.", va, None))
    else:
        prep.append(_check(
            "verified", "Verified answers are set", NA,
            "Saved in a VerifiedAnswers folder beside the definition, and none was handed "
            "over. A service export does not include them.", None, None))
    prep.append(_check(
        "schema", "AI data schema is set", NA,
        "Saved in the linguistic schema; this version does not read it yet. "
        "Check Prep data for AI > Simplify data schema.", None, None))

    groups = [
        {"key": "foundations", "title": "Model foundations",
         "lede": "The modelling practice every AI experience depends on.", "checks": foundations},
        {"key": "descriptions", "title": "Descriptions",
         "lede": "What the model tells AI about each table, column and measure.",
         "checks": descriptions},
        {"key": "prep", "title": "Prep data for AI",
         "lede": "The settings made in Prep data for AI, read from the model's files.",
         "checks": prep},
    ]
    all_checks = [c for g in groups for c in g["checks"]]
    return {
        "why": WHY,
        "groups": groups,
        "summary": {
            "passed": sum(1 for c in all_checks if c["status"] == PASS),
            "failed": sum(1 for c in all_checks if c["status"] == FAIL),
            "unknown": sum(1 for c in all_checks if c["status"] == NA),
            "total": len(all_checks),
        },
        "counts": {
            "tables": len(tables), "columns": len(columns), "measures": len(measures),
            "relationships": len(rels), "visibleColumns": len(visible_cols),
        },
    }
