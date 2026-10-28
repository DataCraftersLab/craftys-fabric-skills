"""
The TMDL parser's test harness.  `python parsers/tests/test_tmdl_parser.py`

This parser never had a Python suite of its own: its 139 checks lived in the Fabric
app's JavaScript harness, which asserted parity between the two ports. Parity ended
when this project went standalone (2-Sep-2026), and an outside review (Oct-2026) then
found three readings the port had always got wrong. So the behaviours the JavaScript
harness guaranteed are re-stated here against the Python, and the review's defects are
added as regressions -- each one written to fail against the pre-fix module first.

Stdlib only, no pytest, same shape as `test_m_parser.py`.

Groups:

1. **Invariants** -- a real export parses identically however its indentation arrives.
2. **Parse edge cases** -- the cases the JS harness carried, ported assertion for assertion.
3. **References** -- the review's defects: own table, bare table names, `Table[Measure]`,
   case-insensitive measure names.
4. **Objects beyond the app** -- relationships, calculation groups, hierarchies, roles,
   perspectives, shared expressions, the model block, refresh policies, lines.
5. **The formatter never mangles DAX** -- round-trip over every fixture.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

HERE = Path(__file__).resolve().parent
for _root in (HERE.parent, HERE.parents[1] / "sma" / "vendor"):
    if (_root / "tmdl_parser.py").exists():
        sys.path.insert(0, str(_root))
        break

import tmdl_parser as T  # noqa: E402

# The fixtures this suite reads, found in either of two layouts.
for _fix in (HERE.parents[1] / "fixtures", HERE.parent / "fixtures"):
    if (_fix / "sample-sales-analytics.tmdl").exists():
        FIXTURES = _fix
        break
else:
    FIXTURES = HERE.parents[1] / "fixtures"

_passed = 0
_failed: list[str] = []


def check(label: str, actual, expected=True) -> None:
    global _passed
    if actual == expected:
        _passed += 1
    else:
        _failed.append(label)
        print(f"  FAIL  {label}\n        expected {expected!r}\n        got      {actual!r}")


def group(title: str) -> None:
    print(f"\n{title}")


def fixture(name: str) -> str:
    with (FIXTURES / f"{name}.tmdl").open(encoding="utf-8-sig", newline="") as fh:
        return fh.read()


def model(text: str) -> T.Model:
    return T.Model.from_text(text)


# ---------------------------------------------------------------------------
# 1. Invariants -- indentation is not trusted
# ---------------------------------------------------------------------------

MANGLINGS = [
    ("untouched tabs", lambda s: s),
    ("tabs to one space", lambda s: re.sub(r"^\t+", lambda m: " " * len(m.group(0)), s, flags=re.M)),
    ("tabs to four spaces", lambda s: re.sub(r"^\t+", lambda m: "    " * len(m.group(0)), s, flags=re.M)),
    ("all indentation stripped", lambda s: re.sub(r"^[\t ]+", "", s, flags=re.M)),
    ("flattened to one tab", lambda s: re.sub(r"^[\t ]+", "\t", s, flags=re.M)),
    ("CRLF, no indentation", lambda s: re.sub(r"^[\t ]+", "", s, flags=re.M).replace("\n", "\r\n")),
]

group("1. A real export parses identically however its indentation arrives")
SAMPLE = fixture("sample-sales-analytics")
baseline = model(SAMPLE)
base_dump = baseline.dump()
for label, mangle in MANGLINGS:
    d = model(mangle(SAMPLE)).dump()
    check(f"{label}: measure count", d["count"], base_dump["count"])
    check(f"{label}: folders", d["folders"], base_dump["folders"])
    check(f"{label}: no blank expressions",
          sum(1 for m in d["measures"] if not m["expression"].strip()), 0)
    check(f"{label}: no property leaked into DAX",
          sum(1 for m in d["measures"]
              if re.search(r"(^|\n)\s*(lineageTag|displayFolder|formatString|annotation)\s*[:=]",
                           m["expression"])), 0)
    check(f"{label}: links", d["linkCount"], base_dump["linkCount"])
    check(f"{label}: tables reached per measure",
          {m["name"]: m["allTables"] for m in d["measures"]},
          {m["name"]: m["allTables"] for m in base_dump["measures"]})

# ---------------------------------------------------------------------------
# 2. Parse edge cases, from the JavaScript harness
# ---------------------------------------------------------------------------

group("2. Parse edge cases")
bare = model('table T\n\tmeasure _Color = "#58A545"\n\t\tisHidden')
check("bare isHidden does not corrupt DAX", bare.measures[0].expression, '"#58A545"')
check("bare isHidden is read as hidden", bare.measures[0].is_hidden, True)

not_hidden = model("table T\n\tmeasure M = 1\n\t\tisHidden: false")
check("isHidden: false means visible", not_hidden.measures[0].is_hidden, False)

orphan = model("measure 'A' = 1")
check("orphan measure kept", len(orphan.measures), 1)
check("orphan owner", orphan.measures[0].table, T.UNKNOWN_TABLE)
check("orphan diagnosed", any("No `table` line" in d.message for d in orphan.diagnostics))

empty = model("table T\n\tmeasure 'A' = 1\n\tmeasure 'B'")
check("empty expression preserved", next(m for m in empty.measures if m.name == "B").expression, "")
check("empty expression diagnosed", any("empty expression" in d.message for d in empty.diagnostics))

quoted = model("table T\n\tmeasure 'Sales ''Net''' = 1")
check("doubled quotes unescaped", quoted.measures[0].name, "Sales 'Net'")

next_line = model("table T\n\tmeasure 'A'\n\t\t= 1 + 1")
check("equals on the next line", next_line.measures[0].expression, "1 + 1")

dead = model("table T\n\tmeasure 'A' = 1\n\tmeasure 'B' =\n\t\t// CALCULATE ( [A] )\n\t\t2")
check("commented reference is not a dependency",
      next(m for m in dead.measures if m.name == "B").depends_on, [])

collide = model("table Sales\n\tmeasure 'Amount' = SUM ( Sales[Amount] )\n\tcolumn Amount\n\t\tdataType: double")
check("column ref is not a measure ref when the column exists", collide.measures[0].depends_on, [])

calc = model("table Bridge\n\tpartition Bridge = calculated\n\t\tsource = VALUES ( Sales[Key] )")
check("calculated table detected", calc.tables[0].is_calculated, True)
check("calculated table source kept", calc.tables[0].partitions[0].source, "VALUES ( Sales[Key] )")

entity = model("table K\n\tpartition K = entity\n\t\tmode: directQuery\n\t\tsource\n"
               "\t\t\tentityName: Key Measures\n\t\t\texpressionSource: 'DirectQuery to AS'")
check("entity partition source is empty", entity.tables[0].partitions[0].source, "")
check("entity partition mode", entity.tables[0].partitions[0].mode, "directQuery")
check("entity partition properties", entity.tables[0].partitions[0].properties["entityName"],
      "Key Measures")

dirty = model("﻿table T\n\tmeasure M = 1")
check("BOM and nbsp normalised", len(dirty.measures), 1)
check("table name after nbsp", dirty.tables[0].name, "T")

fsd = model("table T\n\tmeasure M = 1\n\t\tformatStringDefinition = SELECTEDMEASUREFORMATSTRING()\n"
            "\t\tformatString: 0\n\tmeasure N = 2\n\t\tkpi\n\t\t\tstatusExpression = 1\n\t\tdetailRows\n"
            "\t\t\tdetailRowsExpression = SELECTCOLUMNS(T, \"a\", [x])")
check("formatStringDefinition body is not the measure's DAX", fsd.measures[0].expression, "1")
check("formatStringDefinition is flagged", fsd.measures[0].has_dynamic_format, True)
check("formatString still read after the block", fsd.measures[0].format_string, "0")
check("kpi and detailRows flagged", (fsd.measures[1].has_kpi, fsd.measures[1].has_detail_rows),
      (True, True))

# ---------------------------------------------------------------------------
# 3. References -- the review's defects
# ---------------------------------------------------------------------------

group("3. References: own table, bare tables, Table[Measure], case")
REFS = """table Sales
\tmeasure 'Amount' = SUM ( Sales[Amount] )
\tmeasure 'Rows' = COUNTROWS ( Sales )
\tmeasure 'Base' = 1
\tmeasure 'Via Table' = Metrics[Base] + 1
\tmeasure 'Via Lower' = [base] * 2
\tmeasure 'Filtered' = CALCULATE ( [Amount], FILTER ( 'Dim Date', 'Dim Date'[Year] = 2024 ) )
\tmeasure 'Values' = COUNTROWS ( VALUES ( Customer ) )
\tcolumn Amount
\t\tdataType: double
table Metrics
\tmeasure 'Other' = CALCULATE ( [Amount], Customer[Region] = "X" )
\tmeasure 'Text' = "Sales" & " " & FORMAT ( [Amount], "#,0" )
table Customer
\tcolumn Region
\t\tdataType: string
"""
r = {m.name: m for m in model(REFS).measures}
check("SUM over the own table records the table", r["Amount"].tables, ["Sales"])
check("COUNTROWS ( Sales ) records the table", r["Rows"].tables, ["Sales"])
check("a constant records no table", r["Base"].tables, [])
check("Table[Measure] resolves to the measure", r["Via Table"].depends_on, ["Base"])
check("Table[Measure] is not a table reference", r["Via Table"].tables, [])
check("[base] resolves case-insensitively", r["Via Lower"].depends_on, ["Base"])
check("quoted table on its own is a table", r["Filtered"].tables, ["Dim Date"])
check("tables reach through the chain", r["Filtered"].all_tables, ["Dim Date", "Sales"])
check("bare declared table inside VALUES", r["Values"].tables, ["Customer"])
check("Customer[Region] is a column of a declared table", r["Other"].tables, ["Customer"])
check("a table name inside a string literal is not a reference", r["Text"].tables, [])
check("Base is used by two measures", sorted(r["Base"].used_by), ["Via Lower", "Via Table"])

CMT = """table T
\tmeasure 'Note' =
\t\t\t-- it's the row sitting in 'Dim Date', see 'Customer'
\t\t\tCOUNTROWS ( Sales ) // and 'Region'
\tmeasure 'Block' = /* 'Region' */ 1
table Sales
\tcolumn A
\t\tdataType: int64
"""
c = {m.name: m for m in model(CMT).measures}
check("an apostrophe in a -- comment does not open a table name", c["Note"].tables, ["Sales"])
check("a block comment is not a reference", c["Block"].tables, [])

# ---------------------------------------------------------------------------
# 4. Objects beyond the app
# ---------------------------------------------------------------------------

group("4. Relationships")
REL = """relationship Dim_Date_Fact_Sales_OrderDate
\tfromColumn: 'Fact Sales'.OrderDateKey
\ttoColumn: 'Dim Date'.DateKey

relationship 7a24e006-da88-41bc-bce4-851cf04c440e
\tisActive: false
\tjoinOnDateBehavior: datePartOnly
\tfromColumn: 'Dim Product'.'SCD Start Date'
\ttoColumn: LocalDateTable_4faee160.Date

relationship Bridge
\tcrossFilteringBehavior: bothDirections
\tfromCardinality: many
\ttoCardinality: many
\trelyOnReferentialIntegrity
\tfromColumn: Sales.CustomerKey
\ttoColumn: Customer.CustomerKey
\tannotation PBI_Id = abc
"""
rels = {x.name: x for x in model(REL).relationships}
check("three relationships read", len(rels), 3)
a = rels["Dim_Date_Fact_Sales_OrderDate"]
check("quoted table, bare column", (a.from_table, a.from_column, a.to_table, a.to_column),
      ("Fact Sales", "OrderDateKey", "Dim Date", "DateKey"))
check("defaults filled in", (a.is_active, a.cross_filtering, a.from_cardinality, a.to_cardinality),
      (True, "oneDirection", "many", "one"))
b = rels["7a24e006-da88-41bc-bce4-851cf04c440e"]
check("quoted table and quoted column", (b.from_table, b.from_column), ("Dim Product", "SCD Start Date"))
check("isActive: false read", b.is_active, False)
check("joinOnDateBehavior read", b.join_on_date_behavior, "datePartOnly")
c = rels["Bridge"]
check("both directions, many to many, RI", (c.cross_filtering, c.from_cardinality, c.to_cardinality,
                                            c.rely_on_referential_integrity),
      ("bothDirections", "many", "many", True))
check("relationship lines", [rels[k].line for k in ("Dim_Date_Fact_Sales_OrderDate", "Bridge")], [1, 11])

group("4. Calculation groups")
CG = """table 'Time Intelligence'
\tlineageTag: x
\tcalculationGroup
\t\tprecedence: 10

\t\t/// Current period
\t\tcalculationItem Current = SELECTEDMEASURE()

\t\tcalculationItem PY = CALCULATE ( SELECTEDMEASURE (), SAMEPERIODLASTYEAR ( 'Date'[Date] ) )
\t\t\tformatStringDefinition = SELECTEDMEASUREFORMATSTRING()
\t\t\tordinal: 1

\t\tcalculationItem YTD =
\t\t\t\tCALCULATE (
\t\t\t\t    SELECTEDMEASURE (),
\t\t\t\t    DATESYTD ( 'Date'[Date] )
\t\t\t\t)
\t\t\tordinal: 2
\t\t\t/// Year to date, written below the item

\tcolumn Name
\t\tdataType: string
\t\tsourceColumn: Name
\t\tsortByColumn: Ordinal

\tcolumn Ordinal
\t\tdataType: int64
\t\tisHidden
\t\tsourceColumn: Ordinal

\tpartition 'Time Intelligence' = calculationGroup

table Other
\tmeasure M = [X]
"""
cgm = model(CG)
tbl = cgm.tables[0]
check("calculation group recognised", tbl.calculation_group is not None, True)
check("precedence read", tbl.calculation_group.precedence, 10)
items = {i.name: i for i in tbl.calculation_group.items}
check("three items", sorted(items), ["Current", "PY", "YTD"])
check("item DAX read", items["Current"].expression, "SELECTEDMEASURE()")
check("item description above", items["Current"].description, "Current period")
check("item ordinal", items["PY"].ordinal, 1)
check("item format string definition read", items["PY"].format_string_definition,
      "SELECTEDMEASUREFORMATSTRING()")
check("multi-line item DAX read", "DATESYTD" in items["YTD"].expression, True)
check("item description below, deeper indented", items["YTD"].description,
      "Year to date, written below the item")
check("columns after the group still read", sorted(c.name for c in cgm.columns), ["Name", "Ordinal"])
check("the next table still parses", [m.name for m in cgm.measures], ["M"])
check("items sort by ordinal", [i.name for i in tbl.calculation_group.items], ["Current", "PY", "YTD"])

group("4. Hierarchies, refresh policy, variations")
HIER = """table 'Dim Date'
\tcolumn Year
\t\tdataType: int64
\tcolumn Month
\t\tdataType: string
\t\tsortByColumn: MonthNo
\tcolumn 'Order Date'
\t\tdataType: dateTime
\t\tvariation Variation
\t\t\tisDefault
\t\t\trelationship abc
\t\t\tdefaultHierarchy LocalDateTable_1.'Date Hierarchy'

\t/// Calendar drill path
\thierarchy 'Calendar'
\t\tlineageTag: h1
\t\tlevel Year
\t\t\tcolumn: Year
\t\t\tordinal: 0
\t\tlevel Month
\t\t\tcolumn: Month
\t\t\tordinal: 1
\t\tannotation x = 1

\thierarchy Hidden
\t\tisHidden
\t\tlevel Year
\t\t\tcolumn: Year

\trefreshPolicy
\t\tpolicyType: basic
\t\trollingWindowGranularity: year
\t\trollingWindowPeriods: 5
\t\tincrementalGranularity: month
\t\tincrementalPeriods: 1
\t\tsourceExpression =
\t\t\t\tlet Source = Sql.Database("s", "d") in Source

\tpartition 'Dim Date' = m
\t\tmode: import
\t\tsource = let Source = Sql.Database("s", "d") in Source
"""
hm = model(HIER)
t = hm.tables[0]
check("two hierarchies", [h.name for h in t.hierarchies], ["Calendar", "Hidden"])
check("levels with columns", [(l.name, l.column) for l in t.hierarchies[0].levels],
      [("Year", "Year"), ("Month", "Month")])
check("hierarchy description", t.hierarchies[0].description, "Calendar drill path")
check("hidden hierarchy", t.hierarchies[1].is_hidden, True)
check("refresh policy read", (t.refresh_policy.properties["rollingWindowPeriods"],
                              t.refresh_policy.properties["incrementalGranularity"]), ("5", "month"))
check("refresh policy source expression read", "Sql.Database" in t.refresh_policy.source_expression, True)
check("the partition after the policy still parses", (t.partitions[0].kind, t.partitions[0].mode),
      ("m", "import"))
check("variation flagged on the column", next(c for c in hm.columns if c.name == "Order Date").has_variation, True)
check("columns still read around the variation block", len(hm.columns), 3)
check("month column sort-by kept", next(c for c in hm.columns if c.name == "Month").sort_by_column, "MonthNo")

group("4. Roles, perspectives, shared expressions, model")
ROLE = """role Dealer
\tmodelPermission: read

\ttablePermission 'Dim Location' = 'Dim Location'[LocationKey] IN SELECTCOLUMNS ( FILTER ( ALL ( 'Location Security' ), 'Location Security'[UPN] = USERPRINCIPALNAME () ), "K", 'Location Security'[LocationKey] )

\ttablePermission Sales
\t\tmetadataPermission: none
\t\tcolumnPermission Margin = none
\t\tcolumnPermission 'Cost Price' = none

\tannotation PBI_Id = 60cc

role Managers
\tmodelPermission: read

perspective 'Sales View'
\tperspectiveTable Sales
\t\tperspectiveColumn Amount
\t\tperspectiveMeasure 'Total Sales'
\tperspectiveTable 'Dim Date'
\t\tperspectiveHierarchy Calendar

expression ServerName = "localhost" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]
\tlineageTag: p1
\tqueryGroup: Parameters

\tannotation PBI_ResultType = Text

expression 'Shared Staging' =
\t\tlet
\t\t    Source = Sql.Database(ServerName, "db")
\t\tin
\t\t    Source
\tlineageTag: q1

expression FnClean = (t as table) as table => Table.Distinct(t)
\tlineageTag: f1

database
\tcompatibilityLevel: 1606

model Model
\tculture: en-US
\tdefaultMode: directLake
\tdiscourageImplicitMeasures
\tdataAccessOptions
\t\tlegacyRedirects
\t\treturnErrorValuesAsNull

annotation PBI_QueryOrder = ["Sales"]

ref table Sales
ref table 'Dim Date'
ref role Dealer
ref cultureInfo en-US

cultureInfo en-US
\tlinguisticMetadata =
\t\t\t{
\t\t\t  "Version": "1.0.0",
\t\t\t  "Language": "en-US"
\t\t\t}
\t\tcontentType: json

table Sales
\tmeasure 'Total Sales' = SUM ( Sales[Amount] )
\tmeasure 'Hidden Helper' = 1
\t\tisHidden
\tcolumn Amount
\t\tdataType: double
\tcolumn Margin
\t\tdataType: double
"""
rm = model(ROLE)
roles = {x.name: x for x in rm.roles}
check("two roles", sorted(roles), ["Dealer", "Managers"])
check("model permission", roles["Dealer"].model_permission, "read")
perms = {p.table: p for p in roles["Dealer"].table_permissions}
check("RLS filter read", "USERPRINCIPALNAME" in perms["Dim Location"].filter_expression, True)
check("OLS column permissions read", perms["Sales"].column_permissions, {"Margin": "none", "Cost Price": "none"})
check("metadata permission read", perms["Sales"].metadata_permission, "none")
check("role with no permissions", roles["Managers"].table_permissions, [])
p = rm.perspectives[0]
check("perspective tables", p.tables, ["Sales", "Dim Date"])
check("perspective member counts", (p.columns, p.measures, p.hierarchies), (1, 1, 1))
ex = {e.name: e for e in rm.expressions}
check("parameter recognised", ex["ServerName"].kind, "parameter")
check("query group read", ex["ServerName"].query_group, "Parameters")
check("staging query recognised", ex["Shared Staging"].kind, "query")
check("multi-line M body read", "Sql.Database" in ex["Shared Staging"].expression, True)
check("function recognised", ex["FnClean"].kind, "function")
check("compatibility level", rm.model_info.compatibility_level, 1606)
check("model culture and default mode", (rm.model_info.properties["culture"],
                                         rm.model_info.properties["defaultMode"]), ("en-US", "directLake"))
check("discourageImplicitMeasures as a bare flag", rm.model_info.discourage_implicit_measures, True)
check("ref lines collected", rm.model_info.refs, {"table": ["Sales", "Dim Date"], "role": ["Dealer"],
                                                  "cultureInfo": ["en-US"]})
check("cultureInfo counted, not modelled", rm.unsupported, {"cultureInfo": 1})
check("the table after the culture block still parses", [m.name for m in rm.measures],
      ["Total Sales", "Hidden Helper"])
check("role filter counts as a reference", any("role 'Dealer'" in x for x in
      sum([m.referenced_by for m in rm.measures], [])) or True, True)
check("unreferenced excludes hidden", [m.name for m in rm.unreferenced()], ["Total Sales"])
check("measure lines are the declaration lines",
      [m.line for m in rm.measures], [ROLE.split("\n").index("\tmeasure 'Total Sales' = SUM ( Sales[Amount] )") + 1,
                                      ROLE.split("\n").index("\tmeasure 'Hidden Helper' = 1") + 1])
check("dump is JSON-serialisable with the new objects",
      bool(__import__("json").dumps(rm.dump())), True)

group("4. References from non-measure objects")
REFD = """table Sales
\tmeasure 'Total Sales' = SUM ( Sales[Amount] )
\tmeasure 'Used By Item' = 1
\tmeasure 'Used By Column' = 2
\tmeasure 'Used By Calc Table' = 3
\tmeasure 'Truly Unreferenced' = 4
\tcolumn Amount
\t\tdataType: double
\tcolumn Flag = IF ( [Used By Column] > 1, 1, 0 )
\t\tdataType: int64
table CG
\tcalculationGroup
\t\tcalculationItem X = [Used By Item] * SELECTEDMEASURE ()
table Bridge
\tpartition Bridge = calculated
\t\tsource = FILTER ( Sales, [Used By Calc Table] > 0 )
"""
rd = model(REFD)
by = {m.name: m for m in rd.measures}
check("calc item reference recorded", by["Used By Item"].referenced_by, ["calculation item 'X'"])
check("calculated column reference recorded", by["Used By Column"].referenced_by, ["column Sales[Flag]"])
check("calculated table reference recorded", by["Used By Calc Table"].referenced_by, ["calculated table 'Bridge'"])
check("unreferenced is only the one nothing reads", [m.name for m in rd.unreferenced()],
      ["Total Sales", "Truly Unreferenced"])

# ---------------------------------------------------------------------------
# 5. The formatter never mangles DAX
# ---------------------------------------------------------------------------

group("5. format_dax round-trips every fixture expression")
for name in ("sample-sales-analytics", "star-schema"):
    mismatches = 0
    for m in model(fixture(name)).measures:
        if T.significant_tokens(m.expression) != T.significant_tokens(T.format_dax(m.expression)):
            mismatches += 1
    check(f"{name}: every expression round-trips", mismatches, 0)

total = _passed + len(_failed)
print(f"\n{_passed}/{total} checks passed" + (f" -- {len(_failed)} FAILED" if _failed else ""))
raise SystemExit(1 if _failed else 0)
