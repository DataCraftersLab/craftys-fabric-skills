"""The M parser's test harness. Stdlib only; run it directly with Python."""

from __future__ import annotations

import io
import pathlib
import math
import re
import sys
from pathlib import Path

# Windows defaults stdout to cp1252, which cannot encode the em dashes in these
# labels. Pinned so a failure is readable rather than a wall of mojibake.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

HERE = Path(__file__).resolve().parent
# The parser sits beside this folder or under `sma/vendor/`; both are searched.
for _root in (HERE.parent, HERE.parents[1] / "sma" / "vendor"):
    if (_root / "m_parser.py").exists():
        sys.path.insert(0, str(_root))
        break

import m_parser as m  # noqa: E402

# A model of substance, built for this project. `fixtures/wide-model.pq`: 16 queries
# across 6 layers, 48 steps, three parameters, three custom functions, ODBC with 12 lines
# of embedded SQL written as `#(lf)`/`#(tab)` escapes, a SQL Server options-record query, a
# SharePoint file source, an Enter Data table, a `??`, a nested `let` inside an `each`, and
# one abandoned step.
FIXTURE = HERE / "fixtures" / "wide-model.pq"

_passed = 0
_failed: list[str] = []
_skipped: list[str] = []


def check(label: str, actual, expected) -> None:
    global _passed
    if actual == expected:
        _passed += 1
    else:
        _failed.append(f"{label}\n      expected: {expected!r}\n      actual:   {actual!r}")


def group(title: str) -> None:
    print(f"\n{title}")


# ---------------------------------------------------------------------------
group("Lexer — `#` means four things and `[` means four more")
# ---------------------------------------------------------------------------


def values(source: str) -> list[str]:
    return [t.value for t in m.tokenize(source).tokens if t.kind != "eof"]


def kinds(source: str) -> list[str]:
    return [t.kind for t in m.tokenize(source).tokens if t.kind != "eof"]


token = m.tokenize('#"Changed Type"').tokens[0]
check("quoted identifier is one identifier", (token.kind, token.value, token.quoted),
      ("identifier", "Changed Type", True))
check("doubled quote inside a quoted identifier",
      m.tokenize('#"Sales ""Net"""').tokens[0].value, 'Sales "Net"')

unclosed = m.tokenize('#"Unclosed\nlet x = 1 in x')
check("a quoted identifier does not run past its line",
      (any("Unterminated quoted identifier" in e.message for e in unclosed.errors),
       "let" in values('#"Unclosed\nlet x = 1 in x')),
      (True, True))

# The escape decoding that makes native SQL readable. Inside M it is a single
# string literal with every newline written `#(lf)`.
check("control escapes decode",
      m.tokenize('"SELECT#(lf)#(tab)a#(cr)b"').tokens[0].value, "SELECT\n\ta\rb")
check("hex escape", m.tokenize('"#(0041)"').tokens[0].value, "A")
check("multi-codepoint escape", m.tokenize('"#(0041,0042)"').tokens[0].value, "AB")
check("the literal `#(`", m.tokenize('"#(#)lf)"').tokens[0].value, "#(lf)")
check("an unrecognised escape passes through",
      m.tokenize('"a#(zzz)b"').tokens[0].value, "a#(zzz)b")
check("doubled quote in a text literal",
      m.tokenize('"say ""hi"" now"').tokens[0].value, 'say "hi" now')

check("a dotted name is one identifier",
      values("Table.AddColumn(x)"), ["Table.AddColumn", "(", "x", ")"])
check("`..` is the range operator", values("{1..10}"), ["{", "1", "..", "10", "}"])
check("`...` is a parameter query's whole body",
      values("... meta [a=1]"), ["...", "meta", "[", "a", "=", "1", "]"])
# `text` stays an identifier: M does not reserve the primitive type names, so a
# query may be called `text` and the model's own name has to be able to win.
check("intrinsics, keywords and operators are told apart",
      kinds("#table type nullable text <> =>"),
      ["intrinsic", "keyword", "keyword", "identifier", "operator", "operator"])

comment = m.tokenize("// Query Name\nlet x = 1 in x").tokens[0]
check("comments are emitted, not dropped", (comment.kind, comment.value),
      ("comment", "Query Name"))
block = m.tokenize("/* a /* b */ let")
check("a block comment does not nest", (block.tokens[0].kind, block.tokens[1].value),
      ("comment", "let"))

check("a BOM and non-breaking spaces normalise",
      values("\ufefflet x\u00a0= 1 in x"),
      ["let", "x", "=", "1", "in", "x"])
check("numbers, hex and signed exponents",
      values("0xFF 1.5 2e-3 .5"), ["0xFF", "1.5", "2e-3", ".5"])
check("an unterminated literal is recorded, not thrown",
      any("Unterminated text literal" in e.message
          for e in m.tokenize('let a = "oops in a').errors), True)
check("line numbers survive a multi-line literal",
      next(t.line for t in m.tokenize('a\nb\n"x\ny"\nc').tokens if t.value == "c"), 5)


# ---------------------------------------------------------------------------
group("Splitting — four paste formats, and boundaries verified by parsing")
# ---------------------------------------------------------------------------


def names(text: str) -> list[str]:
    return [q.name for q in m.split_document(text).queries]


check("labelled", m.detect_format("// A\nlet a = 1 in a\n\n// B\nlet b = 2 in b"), "labelled")
# One leading comment above one `let` is a *documented* query, not a document of
# labelled queries, and splitting on it would name the query wrongly.
check("one label is not a labelled document",
      m.detect_format("// Just a note\nlet a = 1 in a"), "expression")
check("section", m.detect_format("section Section1;\nshared A = let a = 1 in a;"), "section")
check("TMDL partition",
      m.detect_format("table Sales\n\tpartition Sales = m\n\t\tsource = let a = 1 in a"), "tmdl")
check("TMDL expression",
      m.detect_format('expression MyParam = "x" meta [IsParameterQuery=true]'), "tmdl")
check("bare expression", m.detect_format("let a = 1 in a"), "expression")

check("splits on the labels",
      names("// First\nlet a = 1 in a\n\n// Second\nlet b = 2 in b"), ["First", "Second"])

# The reason boundaries are verified by parsing rather than trusted: `let … in …`
# uses no brackets, so bracket depth cannot tell a comment inside a query from a
# query label, and neither can a blank line once a clipboard has reflowed the
# whitespace.
interior = "\n".join([
    "// Real Query", "let", "    a = 1,", "",
    "// this comment is inside the query", "    b = a + 1", "in", "    b", "",
    "// Second Query", "let c = 2 in c",
])
result = m.split_document(interior)
check("an interior comment folds back into its query",
      ([q.name for q in result.queries],
       "b = a + 1" in result.queries[0].source,
       any("looked like a query label" in d.message for d in result.diagnostics)),
      (["Real Query", "Second Query"], True, True))

check("no dependence on a blank line between queries",
      names("// A\nlet a = 1 in a\n// B\nlet b = 2 in b"), ["A", "B"])

documented = m.split_document(
    "// Documents the query\n// Query Name\nlet a = 1 in a\n\n// Other\nlet b = 1 in b"
).queries[0]
check("nearest comment names, the rest documents",
      (documented.name, documented.description), ("Query Name", "Documents the query"))

# A parameter query is `... meta [ … ]` and a list query is `{1, 2}`. Both must be
# able to open a slice, and `(` must not parse as an invocation of the query above.
check("a body that is not a `let`",
      names("// P\n... meta [IsParameterQuery=true]\n\n// L\n{1, 2}"), ["P", "L"])
check("a body that opens with `(`",
      names("// A\nlet a = 1 in a\n\n// Fn\n(x) => x + 1"), ["A", "Fn"])

section = "\n".join([
    "section Section1;", "",
    "shared Loaded = let a = 1 in a;", "",
    "Internal = let b = 2 in b;", "",
    '[Description = "Has an attribute"]',
    "shared Documented = let c = 3 in c;",
])
parsed_section = m.split_document(section)
check("section members and their shared flag",
      (parsed_section.format,
       [q.name for q in parsed_section.queries],
       [q.load_enabled for q in parsed_section.queries]),
      ("section", ["Loaded", "Internal", "Documented"], [True, False, True]))
check("a member attribute is the description",
      next(q.description for q in parsed_section.queries if q.name == "Documented"),
      "Has an attribute")
# Depth-aware splitting. A regex on `;` would cut the record in half.
nested = m.split_document(
    'section S;\nshared A = let a = Csv.Document(f, [Delimiter = ";"]) in a;\nshared B = 1;'
)
check("a `;` inside a record is not a member terminator",
      ([q.name for q in nested.queries], "Delimiter" in nested.queries[0].source),
      (["A", "B"], True))
check("a quoted member name unquotes", names('section S;\nshared #"Query One" = 1;'), ["Query One"])

tmdl = "\n".join([
    "table 'Sales Fact'", "",
    "\tpartition 'Sales Fact-a1b2' = m", "\t\tmode: import", "\t\tqueryGroup: Facts",
    "\t\tsource =", "\t\t\tlet", '\t\t\t    Source = Sql.Database("srv", "db")',
    "\t\t\tin", "\t\t\t    Source",
])
parsed_tmdl = m.split_document(tmdl)
# A single-partition table is the norm, and "Sales Fact" is what a modeller calls
# that query — not the partition's generated name.
check("a partition query is named after its table",
      (parsed_tmdl.format,
       [q.name for q in parsed_tmdl.queries],
       parsed_tmdl.queries[0].group,
       parsed_tmdl.queries[0].load_enabled,
       "Sql.Database" in parsed_tmdl.queries[0].source),
      ("tmdl", ["Sales Fact"], "Facts", True, True))

shared_expression = m.split_document("\n".join([
    "/// The server to connect to",
    'expression ServerName = "localhost" meta [IsParameterQuery=true, Type="Text"]',
    "\tlineageTag: abc-123", "\tqueryGroup: Parameters",
])).queries[0]
check("a shared expression, its description and its group",
      (shared_expression.name, shared_expression.description, shared_expression.group),
      ("ServerName", "The server to connect to", "Parameters"))

calculated = m.split_document(
    "table Calendar\n\tpartition Calendar = calculated\n\t\tsource = CALENDARAUTO()"
)
check("a calculated partition is DAX and is skipped",
      (len(calculated.queries), "no `expression` or `partition" in calculated.diagnostics[0].message),
      (0, True))

check("empty text", len(m.split_document("   ").queries), 0)
check("handed DAX", "DAX, not M" in m.split_document(
    "EVALUATE SUMMARIZECOLUMNS ( Sales[Year] )").diagnostics[0].message, True)
check("handed a TMDL measure", "TMDL: Measure Chain" in m.split_document(
    "measure 'Total Sales' = SUM ( Sales[Amount] )").diagnostics[0].message, True)
check("handed bare SQL", "looks like SQL" in m.split_document(
    "SELECT a, b FROM dbo.Thing WHERE x = 1").diagnostics[0].message, True)


# ---------------------------------------------------------------------------
group("Scope — every case a text-matching parser gets wrong")
# ---------------------------------------------------------------------------


def resolve(source: str, queries=()):
    return m.resolve_query(m.parse_expression(source).ast, set(queries))


def kinds_of(source: str, name: str, queries=()) -> list[str]:
    return [r.kind for r in resolve(source, queries).references if r.name == name]


# The central trap. Nearly every query in a real model has a step called `Source`;
# if the model also has a *query* called `Source`, text matching wires every query
# in the model to it.
check("a step name beats a query of the same name",
      [r.kind for r in resolve("let Source = 1, Next = Source in Next", ["Source"]).references
       if r.name == "Source"], ["step"])

# `Source = #"Other"` — the step is named `Source` and reads a query. Both facts
# have to survive at once.
other = next(r for r in resolve('let Source = #"Other" in Source', ["Other", "Source"]).references
             if r.name == "Other")
check("the right-hand side resolves to the query", (other.kind, other.step), ("query", "Source"))

# M's `let` is mutually recursive: every binding is in scope in every body.
check("a step may reference one declared below it",
      next(r.kind for r in resolve("let a = b, b = 1 in a").references
           if r.name == "b" and r.step == "a"), "step")

nested_let = resolve("let Source = let ratio = 1, out = ratio in out in Source")
check("a nested let binding is local, never a step",
      (nested_let.step_names,
       next(r.kind for r in nested_let.references if r.name == "ratio")),
      (["Source"], "local"))

check("`each` binds `_`",
      next(r.kind for r in resolve('let a = Table.AddColumn(t, "x", each _ + 1) in a').references
           if r.name == "_"), "local")
check("a lambda parameter shadows a query of the same name",
      all(r.kind == "local"
          for r in resolve("let a = Table.SelectRows(t, (x) => x[Id] = 1) in a", ["x"]).references
          if r.name == "x"), True)

# `[ApiVersion = 15]` references nothing called `ApiVersion`. This is the mistake
# that would put a phantom dependency in every SharePoint query.
check("a record field name is not a reference",
      any(r.name == "ApiVersion"
          for r in resolve("SharePoint.Files(url, [ApiVersion = 15])", ["ApiVersion"]).references),
      False)
check("a field access name is not a reference",
      any(r.name == "Graph Date"
          for r in resolve("each [Graph Date]", ["Graph Date"]).references), False)
# `[Daily Backlog]` is one field name in M, unquoted, spaces and all.
check("a generalized identifier with spaces is one name",
      m.parse_expression("each [Daily Backlog]").ast.body.field, "Daily Backlog")

# `type table [#"Rounded PC" = _t]` must yield `_t` and nothing else.
type_refs = resolve('let _t = type text, out = type table [#"Rounded PC" = _t] in out')
check("a type's field names are names, its values are expressions",
      (any(r.name == "Rounded PC" for r in type_refs.references),
       len([r for r in type_refs.references if r.name == "_t"]) > 0),
      (False, True))
# `x as binary` and `type nullable text` both put a type name in identifier
# position; a model with a query called `text` must not gain an edge.
check("a primitive type name is not a query reference",
      any(r.name == "binary"
          for r in resolve("(p as binary) => let a = p in a", ["binary", "text"]).references),
      False)

check("a library function is library, not unresolved",
      kinds_of('Table.AddColumn(t, "x", each 1)', "Table.AddColumn"), ["library"])
# A query called `Date` is unusual but legal, and if someone has one, theirs is
# what they meant.
check("a query name beats a library name",
      kinds_of("let a = Date in a", "Date", ["Date"]), ["query"])
check("a name that is nothing at all is unresolved",
      kinds_of('let a = #"Missing Query" in a', "Missing Query"), ["unresolved"])

spine = resolve("let a = 1, b = 2 in b")
check("the steps of a bare let",
      (spine.step_names, spine.result_step, spine.is_function), (["a", "b"], "b", False))
fn = resolve("(x) => let a = x, b = a in b")
check("the steps inside a function body",
      (fn.step_names, fn.is_function, fn.parameters), (["a", "b"], True, ["x"]))
# `let f = (x) => let … in … in f` — the outer `let` names the function, the inner
# one holds the steps a reader wants to see.
wrapper = resolve("let Source = (p as binary) => let a = p, b = a in b in Source")
check("the one-step wrapper a custom function query has",
      (wrapper.step_names, wrapper.is_function, wrapper.parameters), (["a", "b"], True, ["p"]))

expression_result = resolve("let a = 1, b = 2 in Table.Combine({a, b})")
check("no result step when the `in` clause is an expression",
      (expression_result.result_step,
       sorted(r.name for r in expression_result.references
              if r.step is None and r.kind == "step")),
      (None, ["a", "b"]))

check("a reference inside an `each` is one level deep",
      next(r.lambda_depth
           for r in resolve('let a = 1, b = Table.AddColumn(t, "x", each List.Sum(a)) in b').references
           if r.name == "a" and r.step == "b"), 1)
# The regression this exists for: a custom-function query's steps live one lambda
# deep by construction, so measured absolutely *every* step of *every* custom
# function looked like the row-by-row pattern.
check("depth is measured relative to where the steps start",
      next(r.lambda_depth
           for r in resolve("let f = (p) => let a = Csv.Document(p), b = Table.Sort(a) in b in f").references
           if r.name == "a" and r.step == "b"), 0)
# `each #"Transform File"([Content])` is the Combine Files pattern, not a misuse of
# it, and is excluded from the row-by-row finding on this flag.
callee = next(r for r in resolve('let a = Table.AddColumn(t, "f", each #"Fn"([Content])) in a',
                                 ["Fn"]).references if r.name == "Fn")
check("an invocation callee is marked", (callee.is_call, callee.lambda_depth), (True, 1))


# ---------------------------------------------------------------------------
group("A wide model — 16 queries, 6 layers, every object kind")
# ---------------------------------------------------------------------------

fixture = io.open(FIXTURE, encoding="utf-8-sig", newline="").read()
model = m.Model.from_text(fixture)

# --- the invariants. These are properties, not facts about one file, and they are the
# --- assertions that actually catch a regression.

# Zero parse errors is the bar. An error anywhere means some query's references were
# recovered from an unread region rather than resolved properly -- and a fixture with parse
# errors in it is testing nothing. The first draft of this one had six, all mine: a
# multi-word reference written bare instead of as `#"Like This"`.
check("every query is read without error",
      sum(len(q.errors) for q in model.queries), 0)
check("every query has steps, or is a parameter or a bare function",
      [q.name for q in model.queries
       if not q.steps and q.kind not in ("parameter", "function")], [])
check("nothing unresolved",
      [f"{q.name}: {u}" for q in model.queries for u in q.unresolved], [])

# The property the whole layering rests on. Longest-path guarantees it; shortest-path does
# not, which is why this is asserted rather than assumed.
check("every dependency sits strictly upstream of what reads it",
      [f"{q.name} <- {d}" for q in model.queries for d in q.depends_on
       if not q.in_cycle and d in model.by_name and model.by_name[d].layer >= q.layer], [])
check("queries with no dependencies are in layer 0",
      [q.name for q in model.queries if not q.depends_on and q.layer != 0], [])
check("the layers are contiguous from zero",
      model.layers, list(range(len(model.layers))))
check("no cycles in a working model", [q.name for q in model.queries if q.in_cycle], [])
check("the query at the end of the chain is in the last layer",
      (model.by_name["Reporting Final"].layer, max(model.layers)),
      (max(model.layers), 5))

# --- the locked baseline. Arbitrary in value, not in purpose: if any of these move, the
# --- parser read this file differently than it did, and that wants explaining.
check("the shape of the model", (len(model.queries), model.step_count, model.link_count),
      (16, 48, 16))
check("all three object kinds are present and counted",
      sorted((k, sum(1 for q in model.queries if q.kind == k))
             for k in {q.kind for q in model.queries}),
      [("function", 3), ("parameter", 3), ("table", 10)])

# --- the readings that need more than a count

check("a parameter, a function and its signature are named as such",
      (model.by_name["Currency Code"].kind,
       model.by_name["FnCleanTable"].kind,
       model.by_name["FnCleanTable"].parameters),
      ("parameter", "function", ["source", "keep"]))
check("a function with a nullable return ascription is still a function",
      model.by_name["FnTitleCase"].kind, "function")

enriched = model.by_name["Ledger Enriched"]
check("a query depends on the tables, the functions and the parameters it reads",
      sorted(enriched.depends_on),
      ["FnCleanTable", "FnRateFor", "Ledger Raw", "Region Lookup", "Report Start"])
check("the steps read in order, with their operations",
      [st.label for st in enriched.steps],
      ["Reference to Ledger Raw", "Fn clean table", "Added column", "Added column",
       "Merged queries", "Expanded column", "Sorted rows", "Filtered rows"])
check("each step chains to the one it reads",
      (enriched.steps[0].reads, enriched.steps[3].reads, enriched.result_step),
      ([], ["Added Rate"], "Filtered Window"))
check("the columns a step adds, and what a join expands",
      (enriched.steps[2].columns.added, enriched.steps[3].columns.added,
       enriched.steps[5].columns.added),
      (["Rate"], ["Amount Base"], ["Region Name"]))
check("a rename pair", model.by_name["Ledger Raw"].steps[2].columns.renamed,
      [{"from": "PostedOn", "to": "Posting Date"}])
check("a type change", model.by_name["Product Catalogue"].steps[1].columns.retyped[0],
      {"name": "ListPrice", "type": "type number"})

# A `Table.Group` names its keys as kept and its aggregations as added.
grouped = model.by_name["Ledger By Region"].steps[1]
check("a group's keys are kept and its aggregations added",
      (grouped.columns.added, "Region Name" in grouped.columns.kept),
      (["Total Base", "Invoices"], True))

# The one abandoned step, and nothing else.
check("the abandoned step is the only dead one",
      [f"{q.name} / {st.name}" for q in model.queries for st in q.steps if st.dead],
      ["Ledger Enriched / Abandoned Sort"])

# The literal is one line in the source with every newline written `#(lf)` and every tab
# `#(tab)`. Decoding it is the single biggest thing this does that the Editor does not.
odbc = next(st for st in model.by_name["Ledger Raw"].sources
            if st.function_name == "Odbc.Query")
check("the native SQL decodes out of the M string literal",
      (odbc.target, len(odbc.native_query.split("\n")), "FROM" in odbc.native_query,
       "\t" in odbc.native_query, "#(lf)" in odbc.native_query),
      ("dsn=Warehouse", 12, True, True, False))
# The other spelling: an options record rather than a positional argument.
sqlsrv = next(st for st in model.by_name["Product Catalogue"].sources
              if st.function_name == "Sql.Database")
check("a native query given as an options-record field decodes too",
      (sqlsrv.database, len(sqlsrv.native_query.split("\n"))), ("Retail", 3))

sharepoint = next(st for st in model.by_name["Budget Files"].sources
                  if st.function_name == "SharePoint.Files")
check("the SharePoint site behind the file imports",
      ("example.sharepoint.com" in (sharepoint.target or ""),
       model.by_name["Budget Files"].source_kind),
      (True, "sharepoint"))

# The tier rule. An Enter Data table contains `Json.Document` -- a real source function
# when handed a file -- so every one of them used to be reported as a *file* source.
region = model.by_name["Region Lookup"]
check("an Enter Data table reads as inline, not as a JSON file",
      (region.source_kind,
       any(f.code == "inline-data" for f in region.findings),
       "file" in model.source_kinds),
      ("inline", True, False))

# Every query downstream looks like this: it calls no connector at all, and without
# inheritance would show as sourceless.
final = model.by_name["Reporting Final"]
check("a source kind is inherited through the chain",
      (len(final.sources), final.source_kind,
       sorted(final.all_source_kinds)),
      (0, "database", ["database", "inline", "sharepoint"]))

check("the quadratic pattern where a table is read inside an `each`",
      [f"{q.name} / {f.step}" for q in model.queries for f in q.findings
       if f.code == "row-by-row"],
      ["Row By Row Lookup / Added Category"])
# The two false positives that shipped in the first version, kept as tests so they cannot
# come back: a custom function query, and a per-row call to one.
check("a custom function query is not flagged row-by-row",
      "row-by-row" in [f.code for f in model.by_name["FnRateFor"].findings], False)
check("a per-row function call is not flagged row-by-row",
      "row-by-row" in [f.code for f in enriched.findings], False)
# `Ledger Raw` pushes SQL and then does nothing that breaks folding -- a type change, a
# rename and a filter all fold -- so it has a native query and no fold break. The two are
# independent, and asserting both on one query was this test author getting it wrong.
check("a native query is reported, and nothing breaks folding after it",
      ("native-query" in [f.code for f in model.by_name["Ledger Raw"].findings],
       model.by_name["Ledger Raw"].fold_break_step),
      (True, None))
check("folding stops where a custom column is added",
      (model.by_name["Product Catalogue"].fold_break_step,
       "no-fold" in [f.code for f in model.by_name["Product Catalogue"].findings]),
      ("Added Custom", True))

check("search matches a name", len(model.search("ledger")) >= 3, True)
check("search matches a step name nothing else would find",
      [q.name for q in model.search("Abandoned Sort")], ["Ledger Enriched"])
# Searching the decoded SQL, which is the whole point of decoding it.
check("search matches inside a native query",
      [q.name for q in model.search("dbo.Customer")], ["Ledger Raw"])
check("an empty search returns everything", len(model.search("   ")), 16)

check("focus narrows to one hop",
      sorted(q.name for q in model.focus("Ledger Enriched")),
      sorted({enriched.name, *enriched.depends_on, *enriched.used_by}))


# ---------------------------------------------------------------------------
group("Paste mangling — the same model, pasted badly")
# ---------------------------------------------------------------------------
#
# The sibling TMDL parser was rewritten twice for this: its second design parsed a
# fresh export perfectly and then produced blank expressions and zero edges on the
# *same content* pasted through a clipboard that had not preserved indentation —
# silently, with no error. The lesson is that a parser must not decide structure
# from whitespace, and the only way to hold the line is to assert it over a real
# file mangled every way a clipboard mangles one.

if True:

    def signature(text: str) -> dict:
        """The facts that must not move. Compared as one dict so a diff names the field."""
        built = m.Model.from_text(text)
        return {
            "queries": len(built.queries),
            "links": built.link_count,
            "steps": built.step_count,
            "layers": ",".join(str(x) for x in built.layers),
            "parseErrors": sum(len(q.errors) for q in built.queries),
            "unresolved": sum(len(q.unresolved) for q in built.queries),
            "names": "|".join(q.name for q in built.queries),
            "chains": ";".join(f"{q.name}>{','.join(sorted(q.depends_on))}"
                               for q in built.queries),
            "rowByRow": ";".join(f"{q.name}/{f.step}" for q in built.queries
                                 for f in q.findings if f.code == "row-by-row"),
            "nativeQueryLines": sum(len(s.native_query.split("\n")) if s.native_query else 0
                                    for q in built.queries for s in q.sources),
        }

    variants = {
        "tabs for indentation":
            re.sub(r"^ +", lambda x: "\t" * math.ceil(len(x.group(0)) / 4), fixture, flags=re.M),
        "two-space indentation":
            re.sub(r"^ +", lambda x: " " * (len(x.group(0)) // 2), fixture, flags=re.M),
        "eight-space indentation":
            re.sub(r"^ +", lambda x: " " * (len(x.group(0)) * 2), fixture, flags=re.M),
        "all indentation stripped": re.sub(r"^[ \t]+", "", fixture, flags=re.M),
        "CRLF line endings": fixture.replace("\n", "\r\n"),
        "CRLF and no indentation":
            re.sub(r"^[ \t]+", "", fixture, flags=re.M).replace("\n", "\r\n"),
        "a leading BOM": "\ufeff" + fixture,
        "non-breaking spaces for indentation":
            re.sub(r"^ +", lambda x: "\u00a0" * len(x.group(0)), fixture, flags=re.M),
        "blank lines between queries removed": re.sub(r"\n\n+(// )", r"\n\1", fixture),
        "extra blank lines everywhere": fixture.replace("\n", "\n\n"),
    }

    baseline = signature(fixture)
    for variant, text in variants.items():
        check(f"reads identically with {variant}", signature(text), baseline)


# ---------------------------------------------------------------------------
group("Synthetic cases the real model does not cover")
# ---------------------------------------------------------------------------

dead = m.Model.from_text("\n".join([
    "// Has Dead Code",
    "let",
    '    Source = Sql.Database("s", "d"),',
    '    #"Filtered Rows" = Table.SelectRows(Source, each [x] > 1),',
    '    #"Experiment Left Behind" = Table.Sort(Source, {{"x", Order.Ascending}})',
    "in",
    '    #"Filtered Rows"',
    "",
    "// Another",
    "let a = 1 in a",
]))
dead_query = dead.by_name["Has Dead Code"]
check("a step nothing reaches is found",
      ([s.name for s in dead_query.steps if s.dead],
       "never reached from the query's result" in
       next(f.message for f in dead_query.findings if f.code == "dead-step")),
      (["Experiment Left Behind"], True))

# Reporting a whole query as dead code because its result could not be read to a
# single step would be a confident wrong answer.
expression_in = m.Model.from_text(
    "// Combines\nlet a = 1, b = 2 in Table.Combine({a, b})\n\n// Other\nlet c = 1 in c"
)
check("every step is live when the `in` clause is an expression",
      len([s for s in expression_in.by_name["Combines"].steps if s.dead]), 0)

# Illegal in Power Query and it would fail to refresh — but it turns up in a paste
# that is missing a query, which is exactly when this is needed.
cycle = m.Model.from_text('// A\nlet s = #"B" in s\n\n// B\nlet s = #"A" in s')
check("a reference cycle survives instead of hanging",
      (sorted(q.name for q in cycle.queries if q.in_cycle),
       any("reference cycle" in f.message for f in cycle.model_findings)),
      (["A", "B"], True))

incomplete = m.Model.from_text('// Needs More\nlet s = #"Not Pasted" in s\n\n// Other\nlet a = 1 in a')
needs_more = incomplete.by_name["Needs More"]
check("an incomplete paste names the missing query",
      (needs_more.unresolved,
       "Paste the missing query" in
       next(f.message for f in needs_more.findings if f.code == "unresolved-reference")),
      (["Not Pasted"], True))

chained = m.Model.from_text("\n".join([
    "// From Another Model",
    'let Source = AnalysisServices.Database("powerbi://api.powerbi.com/v1.0/myorg/WS", "Profit")',
    "in Source", "", "// Other", "let a = 1 in a",
]))
chained_query = chained.by_name["From Another Model"]
check("a chained import from another semantic model is flagged",
      (chained_query.source_kind,
       "refreshes only after its upstream model does" in
       next(f.message for f in chained_query.findings if f.code == "chained-model")),
      ("model", True))

# `Sql.Database(server, db, [Query = "…"])` — the common spelling, and a different
# code path from `Odbc.Query`'s positional argument.
options_record = m.Model.from_text("\n".join([
    "// Sql With Query",
    'let Source = Sql.Database("srv", "db", [Query = "SELECT 1#(lf)FROM t"])',
    "in Source", "", "// Other", "let a = 1 in a",
]))
check("a native query given as an options-record field",
      options_record.by_name["Sql With Query"].sources[0].native_query, "SELECT 1\nFROM t")

bare = m.Model.from_text('let Source = Sql.Database("s", "d") in Source')
check("a bare expression with no wrapper is one query",
      (bare.format, [q.name for q in bare.queries]), ("expression", ["Query1"]))

not_m = m.Model.from_text("SELECT * FROM dbo.Nope")
check("text that is not M returns an empty model, not a broken one",
      (len(not_m.queries), "looks like SQL" in not_m.diagnostics[0].message), (0, True))


group("Functions and parameters — the shapes a `.pq` of helpers is made of")

# A return-type ascription sits between `)` and `=>`, and the paren-or-function
# lookahead used to test only the one token after the matching `)`. That token is `as`,
# so every annotated function took the parenthesised branch, stopped at the arrow, and
# the whole file came back as no queries at all. Six shapes, because the bug was
# invisible in the two that happened to be tested.
FUNCTION_SHAPES = [
    ("no ascription", "(x as number) => x * 2"),
    ("return ascription", "(x as number) as number => x * 2"),
    ("nullable return", "(x) as nullable text => x"),
    ("no parameters", "() as table => #table({}, {})"),
    ("no parameters, no ascription", "() => 1"),
    ("optional parameter and ascription",
     "(x as text, optional p as number) as text =>\nlet a = x in a"),
    ("ascribed `type`", "(x) as type => x"),
]
for label, src in FUNCTION_SHAPES:
    model = m.Model.from_text(src)
    check(f"a bare function is one function query: {label}",
          [(q.name, q.kind) for q in model.queries], [("Query1", "function")])

# The bound has to stay tight, or a type assertion on a parenthesised expression starts
# hunting for an arrow that belongs to something else entirely.
for label, src in [("assertion, no arrow", "(a) as number"),
                   ("plain parenthesised arithmetic", "(1 + 2) * 3"),
                   ("assertion then a separate each", "(a) as number")]:
    model = m.Model.from_text(src)
    check(f"a parenthesised expression is not read as a function: {label}",
          [q.kind for q in model.queries], ["unknown"])

inner = m.Model.from_text('let f = (v as text) as text => Text.Upper(v) in f("a")')
check("an ascribed function inside a let still leaves a table query",
      [(q.name, q.kind) for q in inner.queries], [("Query1", "table")])

# A parameter is decided by the `meta [IsParameterQuery = true]` record Power Query
# writes, so it is a fact and not a guess -- and it carries no `let`, which is what a
# caller sniffing for `let` gets wrong.
param = m.Model.from_text(
    '"USD" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]')
check("a parameter query with no let is one parameter query",
      [(q.name, q.kind) for q in param.queries], [("Query1", "parameter")])

# The shape someone actually pastes out of a section document: a table, a parameter and
# a helper function together, none of which may be dropped.
mixed = m.Model.from_text("\n".join([
    "section Section1;",
    'shared Sales = let Source = Csv.Document("a") in Source;',
    'shared #"Currency Code" = "USD" meta [IsParameterQuery=true, Type="Text"];',
    "shared FnClean = (raw as text) as text => Text.Trim(Text.Upper(raw));",
]))
check("a section document keeps its table, parameter and function",
      (mixed.format, [(q.name, q.kind) for q in mixed.queries]),
      ("section", [("Sales", "table"), ("Currency Code", "parameter"),
                   ("FnClean", "function")]))


group("Two defects the wide model found")

# A custom function's step label lost its first letter. `namespace_of` returns "" for an
# undotted name, and the caller then did `name[len("") + 1:]` -- so `FnCleanTable` read as
# "N clean table" and `MyTransform` as "Y transform", on the step cards, in front of
# whoever wrote the function. Nothing in the old fixture caught it because every custom
# function there happened to be named `Transform File`, with a space, which the splitter
# never routes through this path.
for _name, _label in [("FnCleanTable", "Fn clean table"),
                      ("MyTransform", "My transform"),
                      ("X", "X"),
                      ("Table.AddColumn", "Added column"),
                      ("Table.SplitColumn", "Split column")]:
    check(f"a step label keeps its first letter: {_name}",
          m.describe_operation(_name).label, _label)

# `row-by-row` means one thing: a *table* referenced as a value inside a lambda, so the
# whole table is re-read per row. It was also firing on a parameter, which is one value
# evaluated once. A warning a modeller cannot act on teaches them to ignore the next one.
_scalar_each = m.Model.from_text(chr(10).join([
    "// Cutoff",
    '#date(2024, 1, 1) meta [IsParameterQuery=true, Type="Date"]',
    "",
    "// Lookup",
    'let Source = #table({"A"}, {{1}}) in Source',
    "",
    "// Uses Both",
    "let",
    '    Source = #table({"D"}, {{2}}),',
    '    #"Filtered" = Table.SelectRows(Source, each [D] >= #"Cutoff"),',
    '    #"Joined" = Table.AddColumn(#"Filtered", "n", each Table.RowCount(#"Lookup"))',
    "in",
    '    #"Joined"',
]))
_uses = _scalar_each.by_name["Uses Both"]
check("a parameter read inside an `each` is not row-by-row",
      [(f.step, f.message) for f in _uses.findings if f.code == "row-by-row"
       and "Cutoff" in f.message], [])
check("a table read inside an `each` still is",
      [f.step for f in _uses.findings if f.code == "row-by-row"], ["Joined"])
check("the step's own row_by_row list is pruned too, not just the message",
      [st.row_by_row for st in _uses.steps if st.name == "Filtered"], [[]])


# ---------------------------------------------------------------------------
group("The `??` coalesce operator")

# The tokenizer split `??` into two `?` punctuators, the expression parser stopped at the
# first, and the document splitter -- which requires a paste to parse end to end before
# it will call it a query -- returned nothing. One modern operator anywhere in a model
# and the whole answer was "no queries found".
COALESCE = [
    ("in a let binding", "let x = 1, y = x ?? 2 in y"),
    ("in a function argument",
     "(a as text, optional w as number) as text =>" + chr(10)
     + "let t = Text.PadStart(a, w ?? 10) in t"),
    ("chained, right-associative", "let a = null ?? 1 ?? 2 in a"),
    ("inside a meta record", "let a = 1 in a meta [X = 2 ?? 3]"),
    # A lone `?` is optional field access and stays a punctuator, so `r[b]? ?? 0` has to
    # read as one then the other.
    ("after optional field access", 'let r = [a=1], v = r[b]? ?? 0 in v'),
]
for label, src in COALESCE:
    model = m.Model.from_text(src)
    check(f"a query using `??` is read: {label}", len(model.queries), 1)

check("`??` lexes as one operator, not two punctuators",
      [(t.kind, t.value) for t in m.without_comments(m.tokenize("a ?? b").tokens)
       if t.value in ("?", "??")],
      [("operator", "??")])
check("a lone `?` is still a punctuator",
      [(t.kind, t.value) for t in m.without_comments(m.tokenize("r[b]?").tokens)
       if t.value == "?"],
      [("punctuator", "?")])

# Precedence: `meta` is looser than `??`, and `??` is looser than `or`. With the levels
# swapped the grouping is wrong in a way no count would reveal.
coalesce_or = m.parse_expression("a ?? b or c")
check("`??` binds looser than `or`",
      (coalesce_or.ast.type, coalesce_or.ast.operator,
       coalesce_or.ast.right.type, coalesce_or.ast.right.operator),
      ("Binary", "??", "Binary", "or"))


group("The sources themselves — no control bytes")

# Four control bytes have been written into these files by accident so far, every one by
# an escape collapsing in a heredoc or an editor, and every one functionally harmless and
# therefore invisible: a `join` separator that became a raw NUL, a group key the same, and
# two more in a generated asset. `grep -P` cannot see a NUL and neither can a diff you
# skim. This can, and it runs every time.
#
# Tab, newline and carriage return are legitimate. Nothing else below 32 is, nor DEL.
LEGAL_CONTROL = {9, 10, 13}
#: This project's own sources, and nothing else. The `.js` files are the renderers rather
#: than parsers, but they are where three of the four accidents landed -- Python refuses to
#: import a source file containing a NUL, so `.py` had a guard already and `.js` had none.
# `_root` is wherever the parser was found above. The scan starts from the project that
# holds it, so either layout is covered.
PROJECT = _root.parent if _root.name == "parsers" else _root.parents[1]
SCAN = [(_root, "*.py"),
        (PROJECT, "dcv-*.js"),
        (PROJECT, "*.py")]

scanned = 0
dirty = []
for root, pattern in SCAN:
    if not root.exists():
        continue
    for path in sorted(root.rglob(pattern)):
        if "__pycache__" in path.parts:
            continue
        data = path.read_bytes()
        found = sorted({b for b in data
                        if (b < 32 and b not in LEGAL_CONTROL) or b == 127})
        scanned += 1
        if found:
            dirty.append("%s: %s" % (path.name, found))

check("every parser source is free of stray control bytes", dirty, [])
check("the scan looked at more than a couple of files", scanned >= 3, True)

# The separator has to survive *as an escape*. When a heredoc collapsed it, the source
# gained a literal NUL, every test still passed, and nothing said a word.
_m_src = (_root / "m_parser.py").read_text(encoding="utf-8")
check("the collision-free separator is an escape, not a raw byte",
      ((chr(92) + "u0000") in _m_src, chr(0) in _m_src), (True, False))


# ---------------------------------------------------------------------------
print()
for failure in _failed:
    print(f"  FAIL  {failure}")
for skip in _skipped:
    print(f"  SKIP  {skip}")
total = _passed + len(_failed)
print(f"\n{_passed}/{total} checks passed" + (f", {len(_skipped)} groups skipped" if _skipped else ""))
raise SystemExit(1 if _failed else 0)
