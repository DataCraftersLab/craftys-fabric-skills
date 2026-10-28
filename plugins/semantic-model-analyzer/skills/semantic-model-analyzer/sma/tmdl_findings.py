"""
Findings for a TMDL parse -- the review layer the properties alone do not give you.

`m_parser.py` emits findings, so Part Q reads like a review. `tmdl_parser.py` emits
properties, so Part T was filled with distributions instead: charts that describe the
model's shape without telling anyone to do anything. This module closes that gap.

Three rules govern what is allowed in here, and the first two were learned the hard way.

**A finding names one specific thing.** "9 measures use FILTER" is not a finding, it is
a count. "These 9 measures use FILTER" is, because the reader can open one. Every rule
below returns a subject.

**A modelling observation is never a verdict.** Its `level` is `warning` or `info`,
mirroring the parsers. Nothing here has seen the reports, the data, or the people who
built the model, and a pattern that looks wrong can be a deliberate decision.

**`error` is for one thing: the text could not be read.** A measure whose DAX did not
come through the paste is not an observation about the model, it is a failure to read
it, and calling it `info` hid it. `error` is reserved for parse failures, and a test
asserts that no other code ever carries it.

Rules that were written, tested against a real model, and **rejected for noise** are
recorded at the foot of this file. That list is load-bearing: without it the same bad
rules get proposed again.
"""

import collections
import re

from .dax_format import read_format

#: Every rule states how it helps, in one line, on the page next to its findings.
#: A rule that cannot fill this in does not belong in the set.
HELP = {
    "parse-failed":
        "The text of this object did not come through: the parser found the declaration "
        "and nothing readable under it. Every reading of it below is a reading of nothing, "
        "so fix the handover before trusting the rest.",
    "duplicate-calculation":
        "Two names for one calculation drift apart the moment someone edits one of them. "
        "Decide which is canonical and point the other at it, or delete it.",
    "derived-format-mismatch":
        "A derived measure that formats differently from the measure it derives from puts "
        "two different-looking numbers of the same kind side by side in one visual.",
    "deep-chain":
        "Depth is what makes a change expensive to reason about: every layer is somewhere a "
        "filter can be added or dropped. Read these before promising an estimate.",
    "time-intel-mixed":
        "The same period comparison written more than one way in one model. Each spelling may "
        "be correct; they can still disagree at a year boundary or on a non-standard calendar. "
        "A calculation item counts as a spelling: a hand-written PY beside a PY item is the "
        "classic double.",
    "format-split-folder":
        "One kind of number written two ways inside one folder: the thousands separator on for "
        "some measures and off for others, or two different decimal places on the same "
        "percentage. A reader sees the folder as one family, so the odd one out reads as a data "
        "problem rather than a formatting one.",
    "no-table-reference":
        "A measure that reads no table is a constant, a parameter harvest, or a text label. "
        "Worth knowing which, because none of them respond to a filter.",
    "undeclared-table":
        "DAX names a table the TMDL never declares. Either the handover is partial or the "
        "reference is stale, and only reading the partitions alongside the properties finds it.",
    "implicit-measure":
        "A column with summarisation left on can be dragged into a visual as its own implicit "
        "measure, silently competing with the explicit measure written for that number. When "
        "the model discourages implicit measures, visuals will not offer it and this is a note.",
    "unreferenced-measure":
        "Not referenced by any other parsed object: no measure, calculation item, calculated "
        "column or role filter reads it. Report pages and runtime usage are not in this file, "
        "so this is a list to check against the reports, never a list to delete.",
    "unformatted-measure":
        "A visible measure with no format string shows whatever the visual decides: a count "
        "with decimals, a currency with none. The reader sees the number before the name.",
    "text-column-summarized":
        "A text column set to summarise cannot be summed, so the setting does nothing useful "
        "and still offers an implicit aggregate in the field list.",
    "sort-by-missing":
        "A month or weekday name column with no sort-by column sorts alphabetically: April "
        "first, September last. The one defect every report reader notices.",
    "inactive-relationship":
        "An inactive relationship only filters through USERELATIONSHIP. One nothing activates "
        "is either a leftover or a role-playing dimension nobody wrote the measures for yet.",
    "userelationship-unknown":
        "USERELATIONSHIP names two columns with no relationship between them in what was "
        "parsed, or an active one, in which case the call has no effect. Either the handover "
        "is partial or the DAX is stale.",
    "bidirectional-relationship":
        "Both-directions filtering lets a filter travel from the many side to the one side, "
        "which is sometimes exactly what is wanted and is always a place a wrong total can "
        "come from. Worth knowing where each one is.",
    "many-to-many-relationship":
        "A many-to-many relationship has no side that is guaranteed unique, so a blank row "
        "and a mismatched key behave differently from a star schema. Worth a note beside it.",
    "disconnected-table":
        "A table with columns and no relationship to anything. A parameter table, a security "
        "table or a lookup used only through LOOKUPVALUE is fine; a dimension that was meant "
        "to filter is not, and the file cannot tell which.",
    "relationship-loop":
        "Following the direction filters flow -- from the one side to the many side, both "
        "ways on a bothDirections relationship -- some table reaches another by two "
        "different routes. Power BI refuses to activate a genuinely ambiguous path, so one "
        "that exists in the file is usually a bothDirections or many-to-many relationship "
        "doing more than intended. Two facts sharing two dimensions is not this: no filter "
        "has two routes there, and the rule does not report it.",
    "relationship-column-missing":
        "A relationship names a column the TMDL handed over does not declare on that table. "
        "Either a column was renamed and the relationship was not, or the handover is partial.",
}

#: `error` sorts first because a parse failure invalidates what is below it. It is
#: carried by parse codes only -- see `ERROR_CODES` and the test that enforces it.
LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}
ERROR_CODES = {"parse-failed"}


class Finding:
    __slots__ = ("level", "code", "message", "subject")

    def __init__(self, level, code, message, subject=""):
        assert level in LEVEL_ORDER, level
        assert code in HELP, "%r has no HELP line" % code
        assert level != "error" or code in ERROR_CODES, \
            "%r is a modelling observation and may not be an error" % code
        self.level = level
        self.code = code
        self.message = message
        self.subject = subject

    def to_dict(self):
        return {"level": self.level, "code": self.code,
                "message": self.message, "subject": self.subject}

    def __repr__(self):
        return "Finding(%r, %r, %r)" % (self.level, self.code, self.subject)


# ------------------------------------------------------------------- the rules

_STRING_LITERAL_RE = re.compile(r'"(?:[^"]|"")*"')
#: Whitespace beside DAX punctuation carries no meaning, so `SUM ( x )` and `SUM(x)`
#: are one calculation. Inside a string literal it is kept, by the function below.
_DAX_PUNCT_WS_RE = re.compile(r"\s*([()\[\]{},;=<>+\-*/&|!])\s*")


def _norm_code(segment):
    segment = re.sub(r"\s+", " ", segment).lower()
    return _DAX_PUNCT_WS_RE.sub(r"\1", segment)


def _norm_dax(expr):
    """Whitespace-insensitive and case-insensitive over the *code*; string literals
    kept exactly as written.

    Lowercasing the whole expression called `"A  B"` and `"a b"` one calculation, which
    a reader can see they are not. Normalising harder than this -- stripping comments,
    reordering arguments -- would start making the same mistake on purpose.
    """
    out, pos = [], 0
    for m in _STRING_LITERAL_RE.finditer(expr or ""):
        out.append(_norm_code(expr[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_norm_code((expr or "")[pos:]))
    return "".join(out).strip()


def duplicate_calculations(measures):
    groups = collections.defaultdict(list)
    for m in measures:
        if m.get("expression"):
            groups[_norm_dax(m["expression"])].append(m["name"])
    out = []
    for names in groups.values():
        if len(names) < 2:
            continue
        names = sorted(names)
        for n in names:
            others = [x for x in names if x != n]
            out.append(Finding(
                "warning", "duplicate-calculation",
                "Identical DAX to %s." % ", ".join(others), n))
    return out


#: A derived measure is recognised by its own name ending in a period token, and the
#: base is then looked up by name. Inferring the family from a shared suffix instead
#: was tried and rejected: a PY measure inherits its base's format, so `Sales PY` in
#: currency beside `Volume PY` as a whole number is correct, and that rule called 22
#: correct measures wrong.
PERIOD_SUFFIXES = ("PY", "PYTD", "YTD", "MTD", "QTD", "LTM", "YoY", "MoM",
                   "Prior Month", "Rolling 3M")


def derived_format_mismatch(measures):
    by_name = {m["name"]: m for m in measures}
    out = []
    for m in measures:
        for suf in PERIOD_SUFFIXES:
            if not m["name"].endswith(" " + suf):
                continue
            base = by_name.get(m["name"][: -(len(suf) + 1)].strip())
            if base and (base.get("formatString") or "") != (m.get("formatString") or ""):
                out.append(Finding(
                    "warning", "derived-format-mismatch",
                    "Formats as %s; %s formats as %s."
                    % (_fmt(m.get("formatString")), base["name"],
                       _fmt(base.get("formatString"))), m["name"]))
            break
    return out


def _fmt(f):
    return "`%s`" % f if f else "nothing set"


def deep_chains(measures, anatomy, warn_at=10, note_at=6):
    out = []
    for m in measures:
        depth = anatomy.get(m["id"], {}).get("chain", 0)
        if depth >= warn_at:
            out.append(Finding("warning", "deep-chain",
                               "%d measures deep." % depth, m["name"]))
        elif depth >= note_at:
            out.append(Finding("info", "deep-chain",
                               "%d measures deep." % depth, m["name"]))
    return out


TIME_INTEL = {
    "DATEADD", "SAMEPERIODLASTYEAR", "PARALLELPERIOD", "PREVIOUSYEAR", "PREVIOUSMONTH",
    "PREVIOUSQUARTER", "NEXTYEAR", "NEXTMONTH", "DATESYTD", "DATESMTD", "DATESQTD",
    "TOTALYTD", "TOTALMTD", "TOTALQTD", "DATESINPERIOD", "DATESBETWEEN",
    "ENDOFMONTH", "ENDOFYEAR", "STARTOFMONTH", "STARTOFYEAR",
}

_CALL_RE = re.compile(r"\b([A-Z][A-Z0-9_.]*)\s*\(")


def _functions_in(expression):
    """Function names called in a piece of DAX, read off the text with comments and
    literals blanked. Only used for calculation items, which carry no anatomy."""
    code = expression or ""
    code = re.sub(r"//[^\n]*|/\*.*?\*/", " ", code, flags=re.S)
    code = _STRING_LITERAL_RE.sub(" ", code)
    return {m.group(1).upper() for m in _CALL_RE.finditer(code)}


def time_intel_mixed(measures, anatomy, calc_groups=()):
    """Group time-intelligence measures by the combination of functions they use.

    Reported per family rather than per measure: the finding is that the model holds
    more than one spelling of the same idea, and a reader needs to see the spellings
    beside each other to judge whether that was deliberate. A calculation item that
    uses time intelligence is one more family, because a hand-written `Sales PY`
    beside a `PY` item is the double this rule exists to show.
    """
    fams = collections.defaultdict(list)
    for m in measures:
        used = {f["name"] for f in anatomy.get(m["id"], {}).get("functions", [])} & TIME_INTEL
        if used:
            fams[tuple(sorted(used))].append(m["name"])
    item_fams = collections.defaultdict(list)
    for g in calc_groups:
        for item in g.get("items", []):
            used = _functions_in(item.get("expression")) & TIME_INTEL
            if used:
                item_fams[tuple(sorted(used))].append(
                    "%s › %s" % (g.get("table", "calculation group"), item["name"]))
    if len(fams) + len(item_fams) < 2:
        return []
    out = []
    for combo, names in sorted(fams.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        out.append(Finding(
            "info" if len(names) > 1 else "warning", "time-intel-mixed",
            "%d %s: %s%s" % (len(names), "measures" if len(names) != 1 else "measure",
                             ", ".join(names[:4]),
                             ", and %d more" % (len(names) - 4) if len(names) > 4 else ""),
            " + ".join(combo)))
    for combo, names in sorted(item_fams.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        same_combo = fams.get(combo)
        out.append(Finding(
            "warning" if same_combo else "info", "time-intel-mixed",
            "%d calculation %s: %s%s" % (
                len(names), "items" if len(names) != 1 else "item", ", ".join(names[:4]),
                (" -- beside %d hand-written %s using the same functions" % (
                    len(same_combo), "measures" if len(same_combo) != 1 else "measure"))
                if same_combo else ""),
            " + ".join(combo) + " (calculation item)"))
    return out


def format_split_folder(measures):
    """Two spellings of ONE kind of number inside one folder.

    Counting distinct masks per folder was tried first and rejected: a Sales folder
    holding a currency measure and a growth percentage has two masks and is correct.
    The real inconsistency is two masks that resolve to the same **Format** dropdown
    word -- `#,0` beside `0` is the thousands separator on for some measures and off
    for others, which a report reader sees directly.
    """
    by_folder = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for m in measures:
        mask = m.get("formatString") or ""
        if mask:
            by_folder[m["folder"]][read_format(mask)["word"]][mask] += 1
    out = []
    for folder in sorted(by_folder):
        for word, masks in sorted(by_folder[folder].items()):
            if len(masks) < 2:
                continue
            ranked = masks.most_common()
            out.append(Finding(
                "warning" if len(masks) > 2 else "info", "format-split-folder",
                "%d ways of writing %s: %s."
                % (len(masks), word.lower(),
                   ", ".join("`%s` on %d" % (k, v) for k, v in ranked)),
                "%s %s %s" % (folder, chr(0xb7), word)))
    return out


def no_table_reference(measures):
    return [Finding("info", "no-table-reference",
                    "Reads no table, so no filter reaches it.", m["name"])
            for m in measures if not m.get("allTables") and (m.get("expression") or "").strip()]


def undeclared_tables(bridge_rows):
    return [Finding("warning", "undeclared-table",
                    "Referenced by DAX, never declared in the TMDL handed over.", r["name"])
            for r in bridge_rows if not r.get("declared")]


def implicit_measures(columns, model=None):
    discouraged = bool((model or {}).get("discourageImplicitMeasures"))
    out = []
    for c in columns:
        if (c.get("summarizeBy") or "none") == "none":
            continue
        if discouraged:
            out.append(Finding(
                "info", "implicit-measure",
                "summarizeBy is `%s`, but the model discourages implicit measures, so "
                "visuals will not offer it." % c["summarizeBy"],
                "%s[%s]" % (c["table"], c["name"])))
        else:
            out.append(Finding("warning", "implicit-measure",
                               "summarizeBy is `%s`." % c["summarizeBy"],
                               "%s[%s]" % (c["table"], c["name"])))
    return out


def unreferenced_measures(names, by_name):
    return [Finding("info", "unreferenced-measure",
                    "Visible, in %s, and not referenced by any other parsed object. Report "
                    "usage is not visible here."
                    % (by_name[n]["folder"] if n in by_name else "no folder"), n)
            for n in names]


_TEXT_SHAPED_RE = re.compile(
    r"\b(FORMAT|CONCATENATE|CONCATENATEX|UNICHAR|SELECTEDVALUE|USERNAME|USERPRINCIPALNAME"
    r"|REPT|LEFT|RIGHT|MID|UPPER|LOWER|TRIM|SUBSTITUTE|COMBINEVALUES)\s*\(|&",
    re.I)


def _looks_numeric(expression):
    """A guess at whether a measure returns a number, from its text alone. Text
    functions, string concatenation and a literal as the whole result say no."""
    code = (expression or "").strip()
    if not code:
        return False
    if code.startswith('"') and code.endswith('"'):
        return False
    stripped = _STRING_LITERAL_RE.sub('""', code)
    return not _TEXT_SHAPED_RE.search(stripped)


def unformatted_measures(measures):
    return [Finding("info", "unformatted-measure",
                    "Visible, numeric by the look of its DAX, and no format string set.",
                    m["name"])
            for m in measures
            if not m.get("isHidden") and not m.get("formatString")
            and not m.get("hasDynamicFormat")
            and _looks_numeric(m.get("expression"))]


_SORT_WORDS_RE = re.compile(r"\b(month|weekday|day)\b.*\b(name|short|abbr|abbreviation|label)\b"
                            r"|\b(month|weekday)\s*name\b|\bmonth\b$|\bweekday\b$|\bday of week\b",
                            re.I)


def column_rules(columns):
    """Two cheap column readings that name a subject."""
    out = []
    for c in columns:
        subject = "%s[%s]" % (c["table"], c["name"])
        dtype = (c.get("dataType") or "").lower()
        summ = (c.get("summarizeBy") or "none")
        if dtype == "string" and summ != "none":
            out.append(Finding("warning", "text-column-summarized",
                               "Text column with summarizeBy `%s`." % summ, subject))
        if dtype == "string" and not c.get("sortByColumn") and _SORT_WORDS_RE.search(c["name"]):
            out.append(Finding("info", "sort-by-missing",
                               "Looks like a month or weekday name and has no sortByColumn.",
                               subject))
    return out


def parse_failures(measures):
    """The one `error`: a declaration with nothing readable under it."""
    return [Finding("error", "parse-failed",
                    "Declared, but no DAX came through the parse.", m["name"])
            for m in measures if not (m.get("expression") or "").strip()]


# ------------------------------------------------------------ relationships

_USERELATIONSHIP_RE = re.compile(
    r"USERELATIONSHIP\s*\(\s*((?:'(?:[^']|'')*'|[A-Za-z_][\w ]*)\[[^\]]+\])\s*,"
    r"\s*((?:'(?:[^']|'')*'|[A-Za-z_][\w ]*)\[[^\]]+\])\s*\)", re.I)
_COLREF_RE = re.compile(r"^(?:'((?:[^']|'')*)'|([^\[]+))\[([^\]]+)\]$")


def _colref(text):
    m = _COLREF_RE.match(text.strip())
    if not m:
        return None
    table = (m.group(1).replace("''", "'") if m.group(1) is not None else m.group(2)).strip()
    return table.lower(), m.group(3).strip().lower()


def _rel_key(r):
    a = (r["fromTable"].lower(), r["fromColumn"].lower())
    b = (r["toTable"].lower(), r["toColumn"].lower())
    return frozenset((a, b))


def relationship_rules(relationships, tables, columns, measures, calc_groups=()):
    """Everything the relationships file can say on its own, plus one cross-check
    against the DAX: which inactive relationships USERELATIONSHIP actually activates."""
    out = []
    if not relationships:
        return out
    by_key = {}
    for r in relationships:
        by_key.setdefault(_rel_key(r), []).append(r)

    # USERELATIONSHIP calls, from measures and calculation items alike.
    activated = collections.defaultdict(list)
    bodies = [(m["name"], m.get("expression") or "") for m in measures]
    for g in calc_groups:
        for item in g.get("items", []):
            bodies.append(("%s › %s" % (g.get("table", ""), item["name"]), item.get("expression") or ""))
    for owner, body in bodies:
        code = re.sub(r"//[^\n]*|/\*.*?\*/", " ", body, flags=re.S)
        code = _STRING_LITERAL_RE.sub('""', code)
        for m in _USERELATIONSHIP_RE.finditer(code):
            a, b = _colref(m.group(1)), _colref(m.group(2))
            if not a or not b:
                continue
            hits = by_key.get(frozenset((a, b)))
            if not hits:
                out.append(Finding(
                    "warning", "userelationship-unknown",
                    "USERELATIONSHIP names %s and %s; no relationship joins those columns in "
                    "what was parsed." % (m.group(1).strip(), m.group(2).strip()), owner))
                continue
            for r in hits:
                if r["isActive"]:
                    out.append(Finding(
                        "info", "userelationship-unknown",
                        "USERELATIONSHIP names %s, which is already active, so the call has "
                        "no effect." % r["name"], owner))
                else:
                    activated[r["name"]].append(owner)

    for r in relationships:
        ends = "%s[%s] → %s[%s]" % (r["fromTable"], r["fromColumn"], r["toTable"], r["toColumn"])
        if not r["isActive"]:
            users = activated.get(r["name"], [])
            if users:
                out.append(Finding(
                    "info", "inactive-relationship",
                    "Inactive; activated by %d %s (%s%s). %s" % (
                        len(users), "measures" if len(users) != 1 else "measure",
                        ", ".join(users[:3]), ", …" if len(users) > 3 else "", ends),
                    r["name"]))
            else:
                out.append(Finding(
                    "warning", "inactive-relationship",
                    "Inactive, and no USERELATIONSHIP in the parsed DAX activates it. %s" % ends,
                    r["name"]))
        if r["crossFilteringBehavior"] == "bothDirections":
            out.append(Finding("warning", "bidirectional-relationship",
                               "Filters in both directions. %s" % ends, r["name"]))
        if r["fromCardinality"] == "many" and r["toCardinality"] == "many":
            out.append(Finding("warning", "many-to-many-relationship",
                               "Many-to-many. %s" % ends, r["name"]))

    # Columns a relationship names that the declared table does not carry.
    cols_by_table = collections.defaultdict(set)
    for c in columns:
        cols_by_table[c["table"].lower()].add(c["name"].lower())
    declared = {t["name"].lower() for t in tables}
    for r in relationships:
        for table, column in ((r["fromTable"], r["fromColumn"]), (r["toTable"], r["toColumn"])):
            low = table.lower()
            if low not in declared:
                out.append(Finding(
                    "warning", "relationship-column-missing",
                    "Names table %s, which is not declared in the TMDL handed over." % table,
                    r["name"]))
            elif cols_by_table.get(low) and column.lower() not in cols_by_table[low]:
                out.append(Finding(
                    "warning", "relationship-column-missing",
                    "Names %s[%s], but %s declares no column of that name." % (table, column, table),
                    r["name"]))

    # Declared tables with visible columns that no relationship touches.
    touched = {r["fromTable"].lower() for r in relationships} | {r["toTable"].lower() for r in relationships}
    visible_cols = collections.Counter()
    for c in columns:
        if not c.get("isHidden"):
            visible_cols[c["table"].lower()] += 1
    for t in tables:
        low = t["name"].lower()
        if low in touched or t.get("isCalculationGroup") or not visible_cols.get(low):
            continue
        if t["name"].startswith(("LocalDateTable_", "DateTableTemplate_")):
            continue
        out.append(Finding(
            "info", "disconnected-table",
            "%d visible %s and no relationship to any table." % (
                visible_cols[low], "columns" if visible_cols[low] != 1 else "column"),
            t["name"]))

    # Two routes for one filter, following direction. The first version ran union-find
    # over an undirected graph, and called every two-fact star schema a loop: Date and
    # Customer both filtering Sales and Returns closes an undirected cycle, yet no filter
    # in it has two routes. A filter flows from the one side to the many side (TMDL:
    # from `toTable` to `fromTable`), and both ways on a bothDirections relationship.
    out += _two_route_filters([r for r in relationships if r["isActive"]])
    return out


def _two_route_filters(active):
    """One finding per set of relationships that gives some filter two routes."""
    edges = collections.defaultdict(list)
    display = {}
    for r in active:
        frm, to = r["fromTable"], r["toTable"]
        display.setdefault(frm.lower(), frm)
        display.setdefault(to.lower(), to)
        edges[to.lower()].append((frm.lower(), r["name"]))
        if (r.get("crossFilteringBehavior") or "").lower() == "bothdirections":
            edges[frm.lower()].append((to.lower(), r["name"]))
    for key in edges:
        edges[key].sort()

    # One finding per relationship that closes the second route, however many
    # dimensions arrive by it: on a real model three dimensions all reached the second
    # fact through one bothDirections relationship, and three findings said so three
    # times. The subject is that closing relationship; the message names the roots.
    by_closing = {}
    reported = set()
    for root in sorted(edges):
        routes = collections.defaultdict(list)
        stack = [(root, (), frozenset([root]))]
        budget = 20000  # simple paths only, and a model is not a maze
        while stack and budget:
            budget -= 1
            node, path, visited = stack.pop()
            for nxt, name in edges.get(node, ()):
                if nxt in visited:
                    continue
                route = path + (name,)
                routes[nxt].append(route)
                stack.append((nxt, route, visited | {nxt}))
        for target in sorted(routes):
            found = routes[target]
            if len(found) < 2:
                continue
            a, b = sorted(found, key=lambda p: (len(p), p))[:2]
            involved = frozenset(a) | frozenset(b)
            if involved in reported:
                continue
            reported.add(involved)
            entry = by_closing.setdefault((b[-1], target), {"roots": [], "example": (a, b)})
            if display[root] not in entry["roots"]:
                entry["roots"].append(display[root])
    out = []
    for (closing, target), entry in sorted(by_closing.items()):
        a, b = entry["example"]
        roots = entry["roots"]
        out.append(Finding(
            "info", "relationship-loop",
            "A filter from %s reaches %s by two routes, for example %s; and %s." % (
                ", ".join(roots[:-1]) + (" and " if len(roots) > 1 else "") + roots[-1],
                display[target], " > ".join(a), " > ".join(b)),
            closing))
    return out


# ---------------------------------------------------------------- configuration

DEFAULT_CONFIG = {
    "thresholds": {"deep-chain": {"warn": 10, "note": 6}},
    "severity": {},
    "suppress": [],
}


def merge_config(config):
    """Defaults under whatever the caller handed over, one level deep."""
    out = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in DEFAULT_CONFIG.items()}
    for key, value in (config or {}).items():
        if key == "thresholds" and isinstance(value, dict):
            for code, th in value.items():
                out["thresholds"].setdefault(code, {}).update(th or {})
        elif key == "severity" and isinstance(value, dict):
            out["severity"].update(value)
        elif key == "suppress" and isinstance(value, list):
            out["suppress"].extend(value)
    return out


def apply_config(findings, config):
    """Severity overrides and documented suppressions over a list of finding dicts.

    Returns (kept, suppressed). A suppressed finding is kept in the dataset with the
    reason that suppressed it, because a finding that silently stops appearing is
    the one nobody can audit. `error` can be overridden down but never assigned to a
    modelling code, for the same reason the Finding class refuses it.
    """
    cfg = merge_config(config)
    kept, suppressed = [], []
    for f in findings:
        f = dict(f)
        override = cfg["severity"].get(f["code"])
        if override in LEVEL_ORDER and (override != "error" or f["code"] in ERROR_CODES):
            f["level"] = override
        rule = _suppression_for(f, cfg["suppress"])
        if rule is not None:
            f["suppressedBy"] = {"reason": rule.get("reason") or "no reason given",
                                 "subject": rule.get("subject", "*")}
            suppressed.append(f)
        else:
            kept.append(f)
    return kept, suppressed


def _glob_match(text, pattern):
    """`*` and `?` only, case-insensitive. Not fnmatch: there `[` opens a character
    class, so the natural pattern `Sales[*]` matched a literal `*` and nothing else."""
    rx = "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern)
    return re.fullmatch(rx, text, re.I) is not None


def _suppression_for(finding, rules):
    for rule in rules:
        if rule.get("code") not in (None, "*", finding["code"]):
            continue
        pattern = rule.get("subject", "*") or "*"
        if _glob_match(finding.get("subject") or "", pattern):
            return rule
    return None


def collect(measures, columns, anatomy, bridge_rows=(), unused=(), by_name=None,
            relationships=(), tables=(), calc_groups=(), model=None, config=None):
    """Every rule, errors first, then warnings, then by code, then by subject.

    `unused` keeps its old name as a positional argument so the showcase build that
    predates the rename still runs; it is the unreferenced list.
    """
    cfg = merge_config(config)
    by_name = by_name or {m["name"]: m for m in measures}
    th = cfg["thresholds"].get("deep-chain", {})
    out = []
    out += parse_failures(measures)
    out += duplicate_calculations(measures)
    out += derived_format_mismatch(measures)
    out += deep_chains(measures, anatomy, warn_at=int(th.get("warn", 10)),
                       note_at=int(th.get("note", 6)))
    out += time_intel_mixed(measures, anatomy, calc_groups)
    out += format_split_folder(measures)
    out += no_table_reference(measures)
    out += undeclared_tables(bridge_rows)
    out += implicit_measures(columns, model)
    out += unreferenced_measures(unused, by_name)
    out += unformatted_measures(measures)
    out += column_rules(columns)
    out += relationship_rules(relationships, tables, columns, measures, calc_groups)
    out.sort(key=lambda f: (LEVEL_ORDER[f.level], f.code, f.subject.lower()))
    return out


# ---------------------------------------------------------------------------
# Written, tested against a real model, and rejected. Kept so they are not
# proposed again.
#
# hidden-measure-nothing-reads   66 hits on a 142-measure model. That is not a
#                               finding, it is the architecture of a measure table
#                               whose helpers are consumed by reports this parser
#                               has never seen.
# single-use-intermediate       36 hits. A measure read exactly once is usually a
#                               deliberate naming of a step, which is the thing a
#                               review asks people to do.
# divide-without-DIVIDE         13 hits, and several matched a `/` inside a text
#                               format string rather than a division. Telling the
#                               two apart needs an expression parse this does not
#                               have, and the rule would still be noisy with one.
# suffix-family-format-split    22 false hits. A period measure inherits its base's
#                               format, so a currency `Sales PY` beside a
#                               whole-number `Volume PY` is correct. Replaced by
#                               derived_format_mismatch, which compares against the
#                               named base instead of against the family.
# folder-has-many-formats       11 hits, and most were correct: a Sales folder holds
#                               an amount and a growth percentage. Replaced by
#                               format_split_folder, which compares masks only within
#                               one Format dropdown word and finds 8, all real.
# unused-measure                Renamed, not rejected. The rule is unreferenced-measure
#                               (Oct-2026): "unused" claimed a view of the reports this
#                               parser does not have, and invited deletion.
# ---------------------------------------------------------------------------
