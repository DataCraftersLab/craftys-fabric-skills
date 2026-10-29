"""
TMDL parser, dependency-graph builder and DAX analyser.

A standalone Python port of the analysis layer in the "TMDL: Measure Chain"
Fabric app. Stdlib only, no third-party dependencies, no filesystem assumptions
beyond the CLI.

Give it TMDL text and it gives you:

  * every measure, with DAX, table, display folder, description, hidden flag,
    format string and every other `key: value` TMDL wrote;
  * every column, with data type, summarize-by, sort-by, key and calculated
    flags;
  * every table, with its own hidden and calculated flags and its partitions
    (kind, mode, and the M / DAX / native-query source behind each);
  * the resolved dependency graph -- what a measure references, what references
    it, and which tables it touches directly and transitively;
  * a DAX anatomy per measure -- re-indented text, VAR bindings, nesting depth,
    function call counts, and the columns and tables where the chain stops.

    from tmdl_parser import Model
    model = Model.from_paths(["definition/tables"])
    for m in model:
        print(m.name, m.folder, m.depends_on)

Or from the shell, which is how a skill should reach it. Ask a question rather
than dumping the model, so the answer stays small enough to read:

    python tmdl_parser.py Metrics.tmdl                      # overview
    python tmdl_parser.py Metrics.tmdl --measure "Sales YTD"
    python tmdl_parser.py Metrics.tmdl --impact  "Total Sales"
    python tmdl_parser.py Metrics.tmdl --anatomy "Sales YTD"
    python tmdl_parser.py Metrics.tmdl --expand  "Sales YTD"
    python tmdl_parser.py Metrics.tmdl --heaviest
    python tmdl_parser.py Metrics.tmdl --tables
    python tmdl_parser.py Metrics.tmdl --columns "Sales"
    python tmdl_parser.py Metrics.tmdl --folder  "01 Sales"
    python tmdl_parser.py Metrics.tmdl --unused
    python tmdl_parser.py Metrics.tmdl --dump               # everything, as JSON

Every mode takes `--json` for machine-readable output. A 140-measure model is
about 240 KB as `--dump`, so prefer the targeted modes when you only need one
answer.


WHERE A MEASURE'S DAX ENDS IS DECIDED BY TMDL KEYWORD, NOT BY INDENTATION

This is the single most important thing about this file, and two earlier designs
in the TypeScript original got it wrong before this one.

The first hardcoded "the expression continues while the tab indent is >= 3", so
any space-indented TMDL parsed to zero measures. The second measured indentation
in *levels* calibrated from the text itself. That parsed a freshly exported file
perfectly and still broke completely on the same content pasted through a
clipboard that had not preserved indentation: properties then sit at or below the
measure's own depth, are never reached, and the expression terminates at once.
Every measure came out with blank DAX, no dependency edges and no folder. No
error, just a quietly wrong answer.

Indentation in pasted or copied text is not trustworthy. TMDL's vocabulary is. An
expression now runs until a line announces itself as a property, a nested block,
a new declaration, or a `///` comment. Indentation survives only to bound a
nested block, and even there any keyword ends it.

Do not reintroduce an indentation-based rule.


PARITY WITH THE JAVASCRIPT SOURCE

Ported deliberately behaviour-for-behaviour, including the parts that could be
argued with, so that both implementations answer identically on the same input.
Verified byte-for-byte against the TypeScript original on four fixtures plus
seven indentation manglings of one of them: tabs, one/two/four spaces, all indentation stripped,
everything flattened to a single tab, and CRLF with no indentation.

Three inherited quirks worth knowing before you rely on them:

* `unique_slugs` is keyed by measure *name*. Two measures with the same name in
  different tables therefore share one id, the later one's. TMDL models normally
  keep measure names unique model-wide, so this has never bitten in practice, but
  it is a real edge if you feed this a merged export.
* `_resolve_tables` recurses. A dependency chain thousands deep would hit
  Python's recursion limit; real measure chains are shallow enough that this has
  not come up.
* String ordering is `_compare_text` -- case-insensitive, then case-sensitive as
  the tie-break. The TypeScript used `localeCompare`, which is locale-dependent
  and therefore not something two languages can both promise; this matches it on
  every ASCII name and is the one deliberate behavioural substitution.

Source: tmdl-measure-chain @ faae32c. Ported 2026-08-20, superseding the
2026-08-02 port of a2d7a18 (measures only -- no columns, tables, partitions or
DAX anatomy).


BEYOND THE APP (2.1, Oct-2026)

The app read tables, measures, columns and partitions, because that is what a
measure chain needs. A model review needs the rest of the definition, so this
port now also reads -- wherever they sit, in a PBIP's own files or inline in a
single-file export:

  * `relationship` blocks: both columns, cardinality, direction, active flag,
    referential-integrity and date-join hints;
  * `calculationGroup` and its `calculationItem`s, with their DAX and ordinal;
  * `hierarchy` and `level`;
  * `role`, with `tablePermission` filters and `columnPermission` (OLS);
  * `perspective` and its members;
  * shared `expression`s (the M side of a PBIP: parameters and staging queries);
  * `model` and `database` options -- culture, default mode, compatibility
    level, `discourageImplicitMeasures`;
  * `refreshPolicy`, and the `formatStringDefinition` / `kpi` / `detailRows`
    blocks hanging off a measure, as flags.

Every object carries the 1-based `line` it was declared on, so a consumer can
say where a finding came from. Declarations this parser recognises but does not
model (`cultureInfo`, `queryGroup`) are counted in `unsupported` rather than
dropped silently.

The JavaScript twin stops at the app's reading. The two are no longer kept at
parity.
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

__all__ = [
    "Anatomy",
    "CalculationGroup",
    "CalculationItem",
    "Column",
    "Diagnostic",
    "ExpansionNode",
    "Hierarchy",
    "HierarchyLevel",
    "Measure",
    "MeasureStats",
    "Model",
    "ModelIndex",
    "ModelInfo",
    "Partition",
    "ParseResult",
    "Perspective",
    "RawColumn",
    "RawMeasure",
    "RefreshPolicy",
    "Relationship",
    "Role",
    "SharedExpression",
    "Table",
    "TablePermission",
    "TableRow",
    "analyze",
    "build_columns",
    "build_graph",
    "expand",
    "format_dax",
    "index_model",
    "parse_model",
    "parse_paths",
    "parse_tmdl",
    "percentile_of",
    "slugify",
    "table_rows",
    "tokenize_dax",
    "unique_slugs",
    "UNFOLDERED",
    "UNKNOWN_TABLE",
]

__version__ = "2.1.0"

#: Stand-in owner for measures parsed without their `table` declaration.
UNKNOWN_TABLE = "(no table)"

#: Fallback folder for measures with no `displayFolder`. Not a model concept.
UNFOLDERED = "Other"

#: Tab stop used to turn mixed tabs and spaces into a comparable column count.
TAB_COLUMNS = 4


# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------

# `key: value` properties TMDL writes under an object. Matching one of these ends
# the expression above it. Over-inclusive on purpose: it covers column and table
# properties too, since a paste can contain anything and a stray property must
# never be read as DAX.
PROPERTY_RE = re.compile(
    r"^(formatString|displayFolder|description|isHidden|isPrivate|lineageTag"
    r"|sourceLineageTag|sourceColumn|dataType|dataCategory|summarizeBy|sortByColumn"
    r"|isAvailableInMdx|isKey|isNullable|isUnique|isDataTypeInferred|encodingHint"
    r"|displayOrdinal|alignment|isDefaultLabel|isDefaultImage|level|mode|type|state"
    # The objects read since 2.1 and the keys they carry. Over-inclusive on purpose,
    # for the same reason as above: a stray `ordinal: 1` must never be read as DAX.
    r"|ordinal|precedence|column|modelPermission|metadataPermission|fromColumn|toColumn"
    r"|fromCardinality|toCardinality|crossFilteringBehavior|securityFilteringBehavior"
    r"|joinOnDateBehavior|isActive|culture|defaultMode|compatibilityLevel|policyType"
    r"|rollingWindowGranularity|rollingWindowPeriods|incrementalGranularity"
    r"|incrementalPeriods|incrementalPeriodsOffset|pollingExpression|queryGroup"
    r"|defaultPowerBIDataSourceVersion|sourceQueryCulture|valueFilterBehavior"
    r"|collation|defaultMeasure|hideMembers)"
    r"\s*:\s*(.*)$"
)

# Nested blocks that hang off a measure or column. Their bodies must not be
# mistaken for the measure's own DAX -- `formatStringDefinition` in particular
# contains a real DAX expression that isn't the measure's.
BLOCK_OPENER_RE = re.compile(
    r"^(annotation|kpi|extendedProperty|changedProperty|detailRows"
    r"|formatStringDefinition|relatedColumnDetails|variation|linguisticMetadata"
    r"|calculationItem|dataAccessOptions|member)\b"
)

# A new declaration -- always ends whatever came before it.
DECLARATION_RE = re.compile(
    r"^(measure|column|table|partition|hierarchy|calculationGroup|relationship"
    r"|role|perspective|expression|culture|cultureInfo|model|database|queryGroup"
    r"|refreshPolicy)\b"
    # `ref table X` lines, which `model.tmdl` uses to list what the model holds.
    r"|^ref\s+(table|role|cultureInfo|perspective|expression|relationship)\s+\S"
)

# Declarations that sit *inside* another object: a role's table permissions, a
# perspective's members, a calculation group's items, a hierarchy's levels. Each
# ends the expression above it the way a top-level declaration does. `level` is
# only a declaration when it has a name and no `=`, so a DAX line that happens to
# start with a variable called `level` is not mistaken for one.
NESTED_DECLARATION_RE = re.compile(
    r"^(tablePermission|columnPermission|perspectiveTable|perspectiveColumn"
    r"|perspectiveMeasure|perspectiveHierarchy|calculationItem)\b"
    r"|^level\s+(?:'(?:[^']|'')*'|[^=\s]+)\s*$"
)

# Properties TMDL writes with no value, meaning true. `isHidden` alone on a line
# is the one that matters to the graph; the rest are here so the metadata
# listings report them rather than silently dropping them.
BARE_FLAG_RE = re.compile(
    r"^(isHidden|isPrivate|isKey|isUnique|isNullable|isNameInferred"
    r"|isDataTypeInferred|isAvailableInMdx|isDefaultLabel|isDefaultImage"
    r"|excludeFromModelRefresh|showAsVariationsOnly|discourageImplicitMeasures"
    r"|relyOnReferentialIntegrity|isDefault|hideMembers|isDefaultMeasure)$"
)

# Single-quoted names may contain doubled quotes ('Sales ''Net''') -- match runs
# of non-quote characters separated by escaped pairs, not just [^']+.
_QUOTED = r"'((?:[^']|'')*)'"
TABLE_RE = re.compile(rf"^table\s+(?:{_QUOTED}|(\S+))\s*$")
MEASURE_RE = re.compile(rf"^measure\s+(?:{_QUOTED}|([^=\s]+))\s*(=\s*(.*))?$")
COLUMN_RE = re.compile(rf"^column\s+(?:{_QUOTED}|([^=\s]+))\s*(=\s*(.*))?$")

#: `partition 'X' = calculated` -- the token after the `=` is what matters.
PARTITION_RE = re.compile(rf"^partition\s+(?:{_QUOTED}|(\S+))\s*=\s*(\S+)\s*$")

# `source = ...` opens the partition body, with an `=` rather than a colon, so it
# is not a PROPERTY_RE match and needs naming here.
SOURCE_RE = re.compile(r"^source\s*=\s*(.*)$")

# `source` with no `=` at all, which is how an `entity` partition is written:
#
#     partition 'Key Measures' = entity
#         mode: directQuery
#         source
#             entityName: Key Measures
#             expressionSource: 'DirectQuery to AS - Profit'
#
# There is no expression here -- the source *is* those two properties.
SOURCE_BLOCK_RE = re.compile(r"^source$")

# Any `key: value`, for the one place a partition's keys are not a fixed set.
KEY_VALUE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")

# A partition's own `key: value` lines. Separate from PROPERTY_RE because
# `queryGroup` is in DECLARATION_RE -- it *is* a top-level declaration elsewhere
# in a TMDL file, but inside a partition it is a property, and reading it as a
# declaration would end the block early.
PARTITION_PROPERTY_RE = re.compile(r"^(mode|queryGroup|dataView|description|state|type)\s*:\s*(.*)$")

# The objects read since 2.1. Names follow the one rule every TMDL name follows:
# quoted when it holds a space or a quote, bare otherwise.
RELATIONSHIP_RE = re.compile(rf"^relationship\s+(?:{_QUOTED}|(\S+))\s*$")
CALC_GROUP_RE = re.compile(r"^calculationGroup\s*$")
CALC_ITEM_RE = re.compile(rf"^calculationItem\s+(?:{_QUOTED}|([^=\s]+))\s*(=\s*(.*))?$")
FORMAT_DEF_RE = re.compile(r"^formatStringDefinition\s*=\s*(.*)$")
HIERARCHY_RE = re.compile(rf"^hierarchy\s+(?:{_QUOTED}|(\S+))\s*$")
LEVEL_RE = re.compile(rf"^level\s+(?:{_QUOTED}|([^=\s]+))\s*$")
ROLE_RE = re.compile(rf"^role\s+(?:{_QUOTED}|(\S+))\s*$")
TABLE_PERMISSION_RE = re.compile(rf"^tablePermission\s+(?:{_QUOTED}|([^=\s]+))\s*(=\s*(.*))?$")
COLUMN_PERMISSION_RE = re.compile(rf"^columnPermission\s+(?:{_QUOTED}|([^=\s]+))\s*=\s*(\S+)\s*$")
PERSPECTIVE_RE = re.compile(rf"^perspective\s+(?:{_QUOTED}|(\S+))\s*$")
PERSPECTIVE_MEMBER_RE = re.compile(
    rf"^perspective(Table|Column|Measure|Hierarchy)\s+(?:{_QUOTED}|(\S+))\s*$"
)
EXPRESSION_RE = re.compile(rf"^expression\s+(?:{_QUOTED}|([^=\s]+))\s*(=\s*(.*))?$")
MODEL_RE = re.compile(rf"^model(?:\s+(?:{_QUOTED}|(\S+)))?\s*$")
DATABASE_RE = re.compile(rf"^database(?:\s+(?:{_QUOTED}|(\S+)))?\s*$")
CULTURE_RE = re.compile(rf"^cultureInfo\s+(?:{_QUOTED}|(\S+))\s*$")
REF_RE = re.compile(rf"^ref\s+(\w+)\s+(?:{_QUOTED}|(\S+))\s*$")
REFRESH_POLICY_RE = re.compile(r"^refreshPolicy\s*$")
SOURCE_EXPRESSION_RE = re.compile(r"^sourceExpression\s*=\s*(.*)$")
QUERY_GROUP_RE = re.compile(rf"^queryGroup\s+(?:{_QUOTED}|(\S+))\s*$")

# `'Fact Sales'.OrderDateKey`, `'Dim Product'.'SCD Start Date'`, `Sales.Amount` --
# how a relationship names its two ends. Either side may be quoted.
_COLUMN_PATH_RE = re.compile(
    r"^(?:'((?:[^']|'')*)'|([^.']+))\.(?:'((?:[^']|'')*)'|(.+))$"
)

# A shared expression is a parameter when its M says so.
_PARAMETER_META_RE = re.compile(r"IsParameterQuery\s*=\s*true", re.I)


def _named(match, quoted_group: int = 1, bare_group: int = 2) -> str:
    """The name out of a declaration match, whichever way it was written."""
    quoted = match.group(quoted_group)
    if quoted is not None:
        return _unquote_name(quoted)
    return match.group(bare_group) or ""


def _column_path(value: str) -> tuple[str, str]:
    """`Table.Column` as written by a relationship, split and unquoted."""
    match = _COLUMN_PATH_RE.match(value.strip())
    if not match:
        return value.strip(), ""
    table = _unquote_name(match.group(1)) if match.group(1) is not None else match.group(2)
    column = _unquote_name(match.group(3)) if match.group(3) is not None else match.group(4)
    return (table or "").strip(), (column or "").strip()


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


@dataclass
class Diagnostic:
    level: str  # "info" | "warning"
    message: str

    def to_dict(self) -> dict:
        return {"level": self.level, "message": self.message}


@dataclass
class Partition:
    """
    A table's partition, and the source behind it.

    `kind` is the token after the `=` on the partition line, and it is the only
    thing that says what the source *is*. Kept as TMDL spells it -- `m`,
    `calculated`, `query`, `entity` -- and never translated: only `calculated`
    tells you the body is DAX, and the rest read differently depending on `mode`.

    `source` is empty for an `entity` partition, which has no expression at all:
    TMDL writes its `source` as a bare block of `key: value` lines instead, and
    those land in `properties`.
    """

    name: str
    kind: str
    mode: str | None
    source: str
    properties: dict[str, str]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "mode": self.mode,
            "source": self.source,
            "properties": dict(self.properties),
        }


@dataclass
class CalculationItem:
    """One item of a calculation group: a name, DAX over `SELECTEDMEASURE()`, and
    optionally a format-string expression of its own."""

    name: str
    expression: str
    ordinal: int | None
    format_string_definition: str
    description: str
    line: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "expression": self.expression,
            "ordinal": self.ordinal,
            "formatStringDefinition": self.format_string_definition,
            "description": self.description,
            "line": self.line,
        }


@dataclass
class CalculationGroup:
    precedence: int | None
    items: list[CalculationItem]
    line: int

    def to_dict(self) -> dict:
        return {
            "precedence": self.precedence,
            "items": [i.to_dict() for i in self.items],
            "line": self.line,
        }


@dataclass
class HierarchyLevel:
    name: str
    column: str
    ordinal: int | None

    def to_dict(self) -> dict:
        return {"name": self.name, "column": self.column, "ordinal": self.ordinal}


@dataclass
class Hierarchy:
    name: str
    table: str
    levels: list[HierarchyLevel]
    is_hidden: bool
    display_folder: str | None
    description: str
    line: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "table": self.table,
            "levels": [l.to_dict() for l in self.levels],
            "isHidden": self.is_hidden,
            "displayFolder": self.display_folder,
            "description": self.description,
            "line": self.line,
        }


@dataclass
class RefreshPolicy:
    """Incremental refresh, as declared. The windows are what a modeller checks
    when a refresh takes longer than it should."""

    table: str
    properties: dict[str, str]
    source_expression: str
    line: int

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "policyType": self.properties.get("policyType"),
            "rollingWindowGranularity": self.properties.get("rollingWindowGranularity"),
            "rollingWindowPeriods": self.properties.get("rollingWindowPeriods"),
            "incrementalGranularity": self.properties.get("incrementalGranularity"),
            "incrementalPeriods": self.properties.get("incrementalPeriods"),
            "hasSourceExpression": bool(self.source_expression.strip()),
            "properties": dict(self.properties),
            "line": self.line,
        }


@dataclass
class Table:
    """
    A table as written, with its own properties.

    A table carries `isHidden` exactly as a column or measure does -- a whole
    staging table hidden from the report is ordinary. `is_calculated` is true when
    any partition is `= calculated`: the table itself is DAX. `calculation_group`
    is set when the table carries a `calculationGroup` block, which is the only
    thing that distinguishes one from an ordinary two-column table.
    """

    name: str
    description: str = ""
    is_hidden: bool = False
    is_calculated: bool = False
    partitions: list[Partition] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    calculation_group: CalculationGroup | None = None
    hierarchies: list[Hierarchy] = field(default_factory=list)
    refresh_policy: RefreshPolicy | None = None
    line: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "isHidden": self.is_hidden,
            "isCalculated": self.is_calculated,
            "isCalculationGroup": self.calculation_group is not None,
            "partitions": [p.to_dict() for p in self.partitions],
            "properties": dict(self.properties),
            "calculationGroup": (
                self.calculation_group.to_dict() if self.calculation_group else None
            ),
            "hierarchies": [h.to_dict() for h in self.hierarchies],
            "refreshPolicy": self.refresh_policy.to_dict() if self.refresh_policy else None,
            "line": self.line,
        }


@dataclass
class Relationship:
    """
    A relationship as written. TMDL omits every default, so the defaults are
    filled in here and `properties` keeps what was actually written: `isActive`
    absent means active, `crossFilteringBehavior` absent means one direction,
    `fromCardinality` absent means many, `toCardinality` absent means one.
    """

    name: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    is_active: bool
    cross_filtering: str
    from_cardinality: str
    to_cardinality: str
    security_filtering: str | None
    rely_on_referential_integrity: bool
    join_on_date_behavior: str | None
    properties: dict[str, str]
    line: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "fromTable": self.from_table,
            "fromColumn": self.from_column,
            "toTable": self.to_table,
            "toColumn": self.to_column,
            "isActive": self.is_active,
            "crossFilteringBehavior": self.cross_filtering,
            "fromCardinality": self.from_cardinality,
            "toCardinality": self.to_cardinality,
            "securityFilteringBehavior": self.security_filtering,
            "relyOnReferentialIntegrity": self.rely_on_referential_integrity,
            "joinOnDateBehavior": self.join_on_date_behavior,
            "properties": dict(self.properties),
            "line": self.line,
        }


@dataclass
class TablePermission:
    """A role's filter on one table (RLS), and any column permissions under it (OLS)."""

    table: str
    filter_expression: str
    column_permissions: dict[str, str]
    metadata_permission: str | None

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "filterExpression": self.filter_expression,
            "columnPermissions": dict(self.column_permissions),
            "metadataPermission": self.metadata_permission,
        }


@dataclass
class Role:
    name: str
    model_permission: str | None
    table_permissions: list[TablePermission]
    properties: dict[str, str]
    line: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "modelPermission": self.model_permission,
            "tablePermissions": [p.to_dict() for p in self.table_permissions],
            "properties": dict(self.properties),
            "line": self.line,
        }


@dataclass
class Perspective:
    name: str
    tables: list[str]
    columns: int
    measures: int
    hierarchies: int
    line: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "tables": list(self.tables),
            "columns": self.columns,
            "measures": self.measures,
            "hierarchies": self.hierarchies,
            "line": self.line,
        }


@dataclass
class SharedExpression:
    """
    A model-level `expression`: a parameter, a staging query or a function that
    table partitions can reference. The M itself is the sibling parser's business;
    this records that the object exists, what kind it is, and where.
    """

    name: str
    kind: str  # parameter | function | query
    expression: str
    query_group: str | None
    properties: dict[str, str]
    line: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "expression": self.expression,
            "queryGroup": self.query_group,
            "properties": dict(self.properties),
            "line": self.line,
        }


@dataclass
class ModelInfo:
    """What `model.tmdl` and `database.tmdl` say about the model as a whole."""

    name: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    compatibility_level: int | None = None
    #: `ref table X` lines, by kind -- what the model says it holds.
    refs: dict[str, list[str]] = field(default_factory=dict)
    line: int = 0

    @property
    def discourage_implicit_measures(self) -> bool:
        return _is_true(self.properties.get("discourageImplicitMeasures"))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "culture": self.properties.get("culture"),
            "defaultMode": self.properties.get("defaultMode"),
            "discourageImplicitMeasures": self.discourage_implicit_measures,
            "compatibilityLevel": self.compatibility_level,
            "defaultPowerBIDataSourceVersion": self.properties.get(
                "defaultPowerBIDataSourceVersion"
            ),
            "properties": dict(self.properties),
            "refs": {k: list(v) for k, v in self.refs.items()},
            "line": self.line,
        }


@dataclass
class RawMeasure:
    """A measure exactly as it was written in the TMDL, before graphing."""

    name: str
    table: str
    description: str
    expression: str
    format_string: str | None
    display_folder: str | None
    is_hidden: bool
    properties: dict[str, str]
    line: int = 0
    #: Nested blocks the measure carried: `formatStringDefinition`, `kpi`,
    #: `detailRows`. The bodies are not read; that they exist is the fact.
    blocks: list[str] = field(default_factory=list)


@dataclass
class RawColumn:
    """A column as written, before folder resolution."""

    name: str
    table: str
    description: str
    expression: str
    is_calculated: bool
    properties: dict[str, str]
    line: int = 0
    blocks: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    measures: list[RawMeasure]
    columns: list[RawColumn]
    tables: list[Table]
    diagnostics: list[Diagnostic]
    relationships: list[Relationship] = field(default_factory=list)
    roles: list[Role] = field(default_factory=list)
    perspectives: list[Perspective] = field(default_factory=list)
    expressions: list[SharedExpression] = field(default_factory=list)
    model: ModelInfo | None = None
    #: Declarations recognised but not modelled, by keyword, with a count.
    unsupported: dict[str, int] = field(default_factory=dict)


@dataclass
class Measure:
    """A measure with its dependency graph and folder grouping resolved."""

    id: str
    name: str
    table: str
    description: str
    expression: str
    format_string: str | None
    display_folder: str | None
    is_hidden: bool
    properties: dict[str, str]
    #: Measures this one references directly.
    depends_on: list[str]
    #: Measures that reference this one directly.
    used_by: list[str]
    #: Tables referenced by this measure's own DAX.
    tables: list[str]
    #: Tables reached through the whole upstream chain, not just this expression.
    all_tables: list[str]
    #: Full folder path segments, e.g. ["Time Intelligence", "YTD"].
    folder_path: list[str]
    #: Top-level folder segment -- the column this measure lands in.
    folder: str
    #: Full path joined back together -- a unique id for a nested subfolder.
    folder_key: str
    #: Last path segment -- the compact label a measure card shows.
    folder_label: str
    #: 1-based line of the `measure` declaration in the text that was parsed.
    line: int = 0
    #: Non-measure objects whose DAX reads this measure: a calculation item, a
    #: calculated column or table, a role filter. `used_by` is measures only.
    referenced_by: list[str] = field(default_factory=list)
    has_dynamic_format: bool = False
    has_kpi: bool = False
    has_detail_rows: bool = False

    def brief(self) -> dict:
        """Enough to identify a measure in a list, without its DAX."""
        return {
            "name": self.name,
            "table": self.table,
            "folder": self.folder_key,
            "hidden": self.is_hidden,
            "upstream": len(self.depends_on),
            "downstream": len(self.used_by),
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "table": self.table,
            "description": self.description,
            "expression": self.expression,
            "formatString": self.format_string,
            "displayFolder": self.display_folder,
            "isHidden": self.is_hidden,
            "properties": dict(self.properties),
            "dependsOn": list(self.depends_on),
            "usedBy": list(self.used_by),
            "referencedBy": list(self.referenced_by),
            "tables": list(self.tables),
            "allTables": list(self.all_tables),
            "folderPath": list(self.folder_path),
            "folder": self.folder,
            "folderKey": self.folder_key,
            "folderLabel": self.folder_label,
            "hasDynamicFormat": self.has_dynamic_format,
            "hasKpi": self.has_kpi,
            "hasDetailRows": self.has_detail_rows,
            "line": self.line,
        }


@dataclass
class Column:
    """
    A column as written in the TMDL, with a stable id and its folder split.

    Columns are not part of the dependency graph -- they have no
    measure-to-measure edges. They exist so a listing can show a model's full
    surface, which is most of what a modeller is checking when they ask "what is
    actually in here".
    """

    id: str
    name: str
    table: str
    description: str
    #: DAX for a calculated column; empty for a source column.
    expression: str
    #: True when the column carried an `=` -- i.e. it is calculated.
    is_calculated: bool
    data_type: str | None
    format_string: str | None
    display_folder: str | None
    is_hidden: bool
    is_key: bool
    summarize_by: str | None
    sort_by_column: str | None
    data_category: str | None
    properties: dict[str, str]
    folder_path: list[str]
    folder: str
    folder_key: str
    line: int = 0
    #: The column carries a `variation` block -- Power BI's auto date/time table
    #: hangs off it.
    has_variation: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "table": self.table,
            "description": self.description,
            "expression": self.expression,
            "isCalculated": self.is_calculated,
            "dataType": self.data_type,
            "formatString": self.format_string,
            "displayFolder": self.display_folder,
            "isHidden": self.is_hidden,
            "isKey": self.is_key,
            "summarizeBy": self.summarize_by,
            "sortByColumn": self.sort_by_column,
            "dataCategory": self.data_category,
            "properties": dict(self.properties),
            "folderPath": list(self.folder_path),
            "folder": self.folder,
            "folderKey": self.folder_key,
            "hasVariation": self.has_variation,
            "line": self.line,
        }


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def _compare_text(a: str, b: str) -> int:
    """
    The one string comparison used everywhere, so the Python and JS ports order
    identically.

    `localeCompare` in the TypeScript original is locale-dependent and therefore
    not something two languages can both promise. Case-insensitive first, then
    case-sensitive as the tie-break, matches it on every ASCII name.
    """
    la, lb = a.lower(), b.lower()
    if la != lb:
        return -1 if la < lb else 1
    if a != b:
        return -1 if a < b else 1
    return 0


_TEXT_KEY = functools.cmp_to_key(_compare_text)


def _compare_folders(a: str, b: str) -> int:
    """
    Natural sort so models that number their folders ("01 Sales", "02 Growth")
    read in that intended order, with no folder names hardcoded.

    Numeric-prefixed folders sort first by number, then the rest alphabetically,
    and the unfoldered bucket is always last.
    """
    if a == UNFOLDERED:
        return 0 if b == UNFOLDERED else 1
    if b == UNFOLDERED:
        return -1
    num_a = re.match(r"^(\d+)\.?\s*", a)
    num_b = re.match(r"^(\d+)\.?\s*", b)
    if num_a and num_b:
        return int(num_a.group(1)) - int(num_b.group(1))
    if num_a:
        return -1
    if num_b:
        return 1
    return _compare_text(a, b)


_FOLDER_KEY = functools.cmp_to_key(_compare_folders)


# ---------------------------------------------------------------------------
# Lexical helpers
# ---------------------------------------------------------------------------


def _leading_columns(line: str) -> int:
    cols = 0
    for ch in line:
        if ch == "\t":
            cols += TAB_COLUMNS
        elif ch == " ":
            cols += 1
        else:
            break
    return cols


def _unquote_name(raw: str) -> str:
    """Unescape a TMDL single-quoted name: doubled quotes are one literal quote."""
    return raw.replace("''", "'")


def _name_value(raw: str) -> str:
    """An object name written as a property value: `column: 'Month Sorted'`, `column:
    "Month Sorted"` or `column: Month`. Quotes off, and a single-quoted name unescaped
    the way a declaration is."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return _unquote_name(value[1:-1])
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _unquote_value(raw: str) -> str:
    """Strip the quotes TMDL sometimes puts around a property value."""
    value = raw.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('\\"', '"')
    return value


def _ends_expression(trimmed: str) -> bool:
    """
    True when this line closes the DAX expression that precedes it.

    BARE_FLAG_RE is here because a valueless flag has no colon and so does not
    match PROPERTY_RE. A measure written

        measure _Color = "#58A545"
            isHidden

    had that `isHidden` read as a second line of DAX, and then never saw it as a
    property either -- so the measure came out with a corrupted expression *and*
    not hidden.
    """
    return bool(
        trimmed.startswith("///")
        or PROPERTY_RE.match(trimmed)
        or BARE_FLAG_RE.match(trimmed)
        or BLOCK_OPENER_RE.match(trimmed)
        or DECLARATION_RE.match(trimmed)
        or NESTED_DECLARATION_RE.match(trimmed)
    )


def _is_boundary(trimmed: str) -> bool:
    """A line that belongs to the next object, whatever the indentation says."""
    return bool(
        trimmed.startswith("///")
        or DECLARATION_RE.match(trimmed)
        or NESTED_DECLARATION_RE.match(trimmed)
    )


def _skip_block(lines: list[str], start: int, opener_columns: int) -> int:
    """
    Step past a nested block and its body, returning the next index to read.

    A block's extent is the one thing indentation still decides, so this also
    stops at any line that announces itself -- a block whose body lost its
    indentation ends at the next keyword instead of swallowing the rest.
    """
    i = start + 1
    while i < len(lines):
        trimmed = lines[i].strip()
        if trimmed == "":
            i += 1
            continue
        if (
            _is_boundary(trimmed)
            or PROPERTY_RE.match(trimmed)
            or BLOCK_OPENER_RE.match(trimmed)
        ):
            break
        if _leading_columns(lines[i]) > opener_columns:
            i += 1
            continue
        break
    return i


def _block_name(trimmed: str) -> str:
    """The keyword a nested block opens with -- `kpi`, `formatStringDefinition`."""
    match = BLOCK_OPENER_RE.match(trimmed)
    return match.group(1) if match else ""


#: A bare identifier on its own line inside a block whose keys are not a fixed set
#: (`model`, `relationship`, `role`) is a flag set to true -- `discourageImplicitMeasures`,
#: `relyOnReferentialIntegrity`, `fastCombine`.
_BARE_WORD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _read_props_any(lines: list[str], start: int) -> tuple[dict[str, str], int, list[str]]:
    """
    Read a property block whose keys are not a fixed list.

    `_read_properties` reads measures, columns and tables, and keeps only the keys
    it knows so a stray line can never pass itself off as a property. The objects
    read since 2.1 -- `model`, `relationship`, `role` -- carry keys this parser
    cannot enumerate in advance, so every `key: value` and every bare flag is kept.
    `key: value` is checked before the declaration test because `queryGroup: X` is a
    property here and a declaration elsewhere.

    Returns (properties, next index, names of nested blocks skipped).
    """
    properties: dict[str, str] = {}
    blocks: list[str] = []
    i = start
    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            i += 1
            continue
        if trimmed.startswith("///"):
            break
        pair = KEY_VALUE_RE.match(trimmed)
        if pair and not BLOCK_OPENER_RE.match(trimmed):
            properties[pair.group(1)] = _unquote_value(pair.group(2))
            i += 1
            continue
        if BLOCK_OPENER_RE.match(trimmed):
            blocks.append(_block_name(trimmed))
            i = _skip_block(lines, i, _leading_columns(lines[i]))
            continue
        if DECLARATION_RE.match(trimmed) or NESTED_DECLARATION_RE.match(trimmed):
            break
        if _BARE_WORD_RE.match(trimmed):
            properties[trimmed] = "true"
            i += 1
            continue
        i += 1
    return properties, i, blocks


def _read_expression(
    lines: list[str], start: int, expr_start: str, has_inline_equals: bool
) -> tuple[str, int]:
    """
    Read a declaration's expression, starting just after its own line.

    Shared by measures, calculated columns and partition sources so they can never
    disagree about where an expression ends -- which is the rule this whole file
    exists to get right.
    """
    i = start
    head = expr_start

    # `measure 'X'` / `column 'X'` with the `=` alone on the next line.
    if not has_inline_equals:
        probe = i
        while probe < len(lines) and not lines[probe].strip():
            probe += 1
        nxt = lines[probe].strip() if probe < len(lines) else ""
        if nxt.startswith("="):
            head = nxt[1:].strip()
            i = probe + 1

    if head.strip().startswith("```"):
        fenced: list[str] = []
        rest = head.strip()[3:]
        if rest.strip() != "":
            fenced.append(rest)
        while i < len(lines) and not lines[i].strip().startswith("```"):
            fenced.append(lines[i])
            i += 1
        i += 1  # skip the closing fence
        return "\n".join(fenced).strip(), i

    # Keyword-terminated, so it works whatever the indentation is (or isn't).
    # Blank lines are kept because DAX legitimately contains them; the join is
    # stripped afterwards.
    block = [head]
    while i < len(lines):
        nxt = lines[i].strip()
        if nxt != "" and _ends_expression(nxt):
            break
        block.append(lines[i])
        i += 1
    return "\n".join(block).strip(), i


def _read_properties(lines: list[str], start: int) -> tuple[dict[str, str], int, list[str]]:
    """
    Read the metadata block below a declaration, by keyword rather than indent.

    Every known `key: value` is kept, not only the handful the graph reads, so a
    listing can show what a model actually declares. Bare flags become `"true"`.

    Returns (properties, next index, names of nested blocks skipped) -- the third
    so a measure can say it carries a `formatStringDefinition` or a `kpi` without
    this parser having to read either body.
    """
    properties: dict[str, str] = {}
    blocks: list[str] = []
    i = start

    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            i += 1
            continue
        if _is_boundary(trimmed):
            break

        keyed = PROPERTY_RE.match(trimmed)
        if keyed:
            properties[keyed.group(1)] = _unquote_value(keyed.group(2))
            i += 1
            continue

        if BARE_FLAG_RE.match(trimmed):
            properties[trimmed] = "true"
            i += 1
            continue

        if BLOCK_OPENER_RE.match(trimmed):
            blocks.append(_block_name(trimmed))
            i = _skip_block(lines, i, _leading_columns(lines[i]))
            continue

        # Anything else down here belongs to no declaration -- step over it rather
        # than letting it end the scan, so a later `displayFolder` still lands.
        i += 1

    return properties, i, blocks


def _read_partition(lines: list[str], start: int) -> tuple[dict[str, str], str, int]:
    """
    Read a partition's body: its properties and its `source`.

    `source` is checked first because the M inside it contains lines that would
    otherwise be read as something else, and because a partition's properties
    conventionally sit above it.
    """
    properties: dict[str, str] = {}
    source = ""
    i = start

    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            i += 1
            continue

        src = SOURCE_RE.match(trimmed)
        if src:
            source, i = _read_expression(lines, i + 1, src.group(1), True)
            continue

        # An entity partition's source is a property bag, not an expression. Read
        # by keyword like everything else here: it ends at the first line that is
        # not a `key: value`, which is also what stops it running into the next
        # table.
        if SOURCE_BLOCK_RE.match(trimmed):
            i += 1
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue
                # No declaration and no `///` comment carries a colon after its
                # first word, so "not a key: value" is the same test as "not part
                # of this block" and needs no second one.
                pair = KEY_VALUE_RE.match(line)
                if not pair:
                    break
                properties[pair.group(1)] = _unquote_value(pair.group(2))
                i += 1
            continue

        keyed = PARTITION_PROPERTY_RE.match(trimmed)
        if keyed:
            properties[keyed.group(1)] = _unquote_value(keyed.group(2))
            i += 1
            continue

        if BLOCK_OPENER_RE.match(trimmed):
            i = _skip_block(lines, i, _leading_columns(lines[i]))
            continue

        if _is_boundary(trimmed):
            break

        i += 1

    return properties, source, i


def _flag(properties: dict[str, str], key: str) -> bool:
    """`isHidden: false` is the only value that means not hidden."""
    value = properties.get(key)
    return value is not None and value.lower() != "false"


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(str(value).strip()) if value is not None and str(value).strip() else None
    except ValueError:
        return None


# -- the readers for the objects added in 2.1 --------------------------------
#
# Each one reads from the line after its declaration and returns the object and
# the next index to read. All of them stop at a boundary line -- a `///` comment,
# a declaration, a nested declaration -- whatever the indentation says, which is
# the rule the rest of this file lives by.


def _read_calculation_group(
    lines: list[str], start: int, decl_line: int
) -> tuple[CalculationGroup, int]:
    """`calculationGroup`, then `precedence` and the `calculationItem`s under it."""
    precedence: int | None = None
    items: list[CalculationItem] = []
    pending: list[str] = []
    i = start
    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            i += 1
            continue
        if trimmed.startswith("///"):
            pending.append(re.sub(r"^///\s?", "", trimmed))
            i += 1
            continue
        item = CALC_ITEM_RE.match(trimmed)
        if item:
            item_line = i + 1
            expression, i = _read_expression(
                lines, i + 1, item.group(4) or "", item.group(3) is not None
            )
            # The item's own properties: `ordinal`, a `formatStringDefinition` with
            # DAX of its own, and a description that TMDL may write *below* the item.
            ordinal: int | None = None
            fmt_def = ""
            desc_after: list[str] = []
            item_cols = _leading_columns(lines[item_line - 1])
            while i < len(lines):
                t = lines[i].strip()
                if not t:
                    i += 1
                    continue
                if t.startswith("///"):
                    # TMDL writes a description *above* its object, at the object's
                    # own indentation. A `///` run indented deeper than the item, or
                    # one that follows the item's own properties, is hand-written
                    # documentation *of* this item and belongs to it. Indentation is
                    # a tiebreak here, never the rule.
                    if _leading_columns(lines[i]) <= item_cols and (ordinal is None and not fmt_def):
                        break
                    desc_after.append(re.sub(r"^///\s?", "", t))
                    i += 1
                    continue
                fmt = FORMAT_DEF_RE.match(t)
                if fmt:
                    fmt_def, i = _read_expression(lines, i + 1, fmt.group(1), True)
                    continue
                keyed = KEY_VALUE_RE.match(t)
                if keyed and not BLOCK_OPENER_RE.match(t):
                    if keyed.group(1) == "ordinal":
                        ordinal = _int_or_none(keyed.group(2))
                    i += 1
                    continue
                if BLOCK_OPENER_RE.match(t) and not CALC_ITEM_RE.match(t):
                    i = _skip_block(lines, i, _leading_columns(lines[i]))
                    continue
                break
            description = " ".join(pending + desc_after).strip()
            pending = []
            items.append(
                CalculationItem(
                    name=_named(item),
                    expression=expression,
                    ordinal=ordinal,
                    format_string_definition=fmt_def,
                    description=description,
                    line=item_line,
                )
            )
            continue
        keyed = KEY_VALUE_RE.match(trimmed)
        if keyed and not BLOCK_OPENER_RE.match(trimmed):
            if keyed.group(1) == "precedence":
                precedence = _int_or_none(keyed.group(2))
            i += 1
            continue
        if BLOCK_OPENER_RE.match(trimmed):
            i = _skip_block(lines, i, _leading_columns(lines[i]))
            continue
        if DECLARATION_RE.match(trimmed) or NESTED_DECLARATION_RE.match(trimmed):
            break
        i += 1
    # Items are declared in ordinal order by convention, and the property is the
    # authority when every item carries one. A file that writes it for some items and
    # not others is left in declaration order rather than half-sorted.
    if items and all(it.ordinal is not None for it in items):
        items.sort(key=lambda it: (it.ordinal, it.line))
    return CalculationGroup(precedence=precedence, items=items, line=decl_line), i


def _read_hierarchy(
    lines: list[str], start: int, name: str, table: str, description: str, decl_line: int
) -> tuple[Hierarchy, int]:
    """`hierarchy`, its own properties, and the `level`s with their columns."""
    properties: dict[str, str] = {}
    levels: list[HierarchyLevel] = []
    i = start
    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            i += 1
            continue
        level = LEVEL_RE.match(trimmed)
        if level:
            props, i, _ = _read_props_any(lines, i + 1)
            levels.append(
                HierarchyLevel(
                    name=_named(level),
                    column=_name_value(props.get("column", "")),
                    ordinal=_int_or_none(props.get("ordinal")),
                )
            )
            continue
        keyed = KEY_VALUE_RE.match(trimmed)
        if keyed and not BLOCK_OPENER_RE.match(trimmed):
            properties[keyed.group(1)] = _unquote_value(keyed.group(2))
            i += 1
            continue
        if BARE_FLAG_RE.match(trimmed):
            properties[trimmed] = "true"
            i += 1
            continue
        if BLOCK_OPENER_RE.match(trimmed):
            i = _skip_block(lines, i, _leading_columns(lines[i]))
            continue
        if _is_boundary(trimmed):
            break
        i += 1
    levels.sort(key=lambda l: (l.ordinal if l.ordinal is not None else 10**6))
    return (
        Hierarchy(
            name=name,
            table=table,
            levels=levels,
            is_hidden=_flag(properties, "isHidden"),
            display_folder=properties.get("displayFolder"),
            description=description or properties.get("description", ""),
            line=decl_line,
        ),
        i,
    )


def _read_role(lines: list[str], start: int, name: str, decl_line: int) -> tuple[Role, int]:
    """`role`, `modelPermission`, and each `tablePermission` with its filter and
    any `columnPermission` lines beneath it."""
    properties: dict[str, str] = {}
    permissions: list[TablePermission] = []
    i = start
    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            i += 1
            continue
        tp = TABLE_PERMISSION_RE.match(trimmed)
        if tp:
            table = _named(tp)
            filter_expr = ""
            if tp.group(3) is not None:
                filter_expr, i = _read_expression(lines, i + 1, tp.group(4) or "", True)
            else:
                i += 1
            col_perms: dict[str, str] = {}
            metadata: str | None = None
            while i < len(lines):
                t = lines[i].strip()
                if not t:
                    i += 1
                    continue
                cp = COLUMN_PERMISSION_RE.match(t)
                if cp:
                    col_perms[_named(cp)] = cp.group(3)
                    i += 1
                    continue
                keyed = KEY_VALUE_RE.match(t)
                if keyed and not BLOCK_OPENER_RE.match(t):
                    if keyed.group(1) == "metadataPermission":
                        metadata = _unquote_value(keyed.group(2))
                    i += 1
                    continue
                if BLOCK_OPENER_RE.match(t):
                    i = _skip_block(lines, i, _leading_columns(lines[i]))
                    continue
                break
            permissions.append(
                TablePermission(
                    table=table,
                    filter_expression=filter_expr,
                    column_permissions=col_perms,
                    metadata_permission=metadata,
                )
            )
            continue
        keyed = KEY_VALUE_RE.match(trimmed)
        if keyed and not BLOCK_OPENER_RE.match(trimmed):
            properties[keyed.group(1)] = _unquote_value(keyed.group(2))
            i += 1
            continue
        if BLOCK_OPENER_RE.match(trimmed):
            i = _skip_block(lines, i, _leading_columns(lines[i]))
            continue
        if _is_boundary(trimmed):
            break
        i += 1
    return (
        Role(
            name=name,
            model_permission=properties.get("modelPermission"),
            table_permissions=permissions,
            properties=properties,
            line=decl_line,
        ),
        i,
    )


def _read_perspective(
    lines: list[str], start: int, name: str, decl_line: int
) -> tuple[Perspective, int]:
    tables: list[str] = []
    counts = {"Column": 0, "Measure": 0, "Hierarchy": 0}
    i = start
    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            i += 1
            continue
        member = PERSPECTIVE_MEMBER_RE.match(trimmed)
        if member:
            kind = member.group(1)
            if kind == "Table":
                tables.append(_named(member, 2, 3))
            else:
                counts[kind] += 1
            i += 1
            continue
        if BLOCK_OPENER_RE.match(trimmed):
            i = _skip_block(lines, i, _leading_columns(lines[i]))
            continue
        if DECLARATION_RE.match(trimmed) or trimmed.startswith("///"):
            break
        i += 1
    return (
        Perspective(
            name=name,
            tables=tables,
            columns=counts["Column"],
            measures=counts["Measure"],
            hierarchies=counts["Hierarchy"],
            line=decl_line,
        ),
        i,
    )


def _read_refresh_policy(
    lines: list[str], start: int, table: str, decl_line: int
) -> tuple[RefreshPolicy, int]:
    properties: dict[str, str] = {}
    source = ""
    i = start
    while i < len(lines):
        trimmed = lines[i].strip()
        if not trimmed:
            i += 1
            continue
        src = SOURCE_EXPRESSION_RE.match(trimmed)
        if src:
            source, i = _read_expression(lines, i + 1, src.group(1), True)
            continue
        keyed = KEY_VALUE_RE.match(trimmed)
        if keyed and not BLOCK_OPENER_RE.match(trimmed):
            properties[keyed.group(1)] = _unquote_value(keyed.group(2))
            i += 1
            continue
        if BLOCK_OPENER_RE.match(trimmed):
            i = _skip_block(lines, i, _leading_columns(lines[i]))
            continue
        if _is_boundary(trimmed):
            break
        i += 1
    return RefreshPolicy(table=table, properties=properties, source_expression=source,
                         line=decl_line), i


def _read_relationship(
    lines: list[str], start: int, name: str, decl_line: int
) -> tuple[Relationship, int]:
    properties, i, _ = _read_props_any(lines, start)
    from_table, from_column = _column_path(properties.get("fromColumn", ""))
    to_table, to_column = _column_path(properties.get("toColumn", ""))
    return (
        Relationship(
            name=name,
            from_table=from_table,
            from_column=from_column,
            to_table=to_table,
            to_column=to_column,
            is_active=_flag(properties, "isActive") if "isActive" in properties else True,
            cross_filtering=properties.get("crossFilteringBehavior", "oneDirection"),
            from_cardinality=properties.get("fromCardinality", "many"),
            to_cardinality=properties.get("toCardinality", "one"),
            security_filtering=properties.get("securityFilteringBehavior"),
            rely_on_referential_integrity=_flag(properties, "relyOnReferentialIntegrity"),
            join_on_date_behavior=properties.get("joinOnDateBehavior"),
            properties=properties,
            line=decl_line,
        ),
        i,
    )


def _read_shared_expression(
    lines: list[str], start: int, match, decl_line: int
) -> tuple[SharedExpression, int]:
    """A model-level `expression`. The body is M, read with the same expression
    reader as DAX because the boundary rule is the same: it ends where TMDL
    vocabulary begins."""
    expression, i = _read_expression(
        lines, start, match.group(4) or "", match.group(3) is not None
    )
    properties, i, _ = _read_props_any(lines, i)
    head = expression.lstrip()
    if _PARAMETER_META_RE.search(expression):
        kind = "parameter"
    elif head.startswith("(") and "=>" in expression:
        kind = "function"
    else:
        kind = "query"
    return (
        SharedExpression(
            name=_named(match),
            kind=kind,
            expression=expression,
            query_group=properties.get("queryGroup"),
            properties=properties,
            line=decl_line,
        ),
        i,
    )


def _describe_empty_result(text: str, tables: list[Table], result: ParseResult | None = None) -> str:
    """Explain a zero-measure parse using whatever the text actually looks like."""
    found = []
    if result is not None:
        if result.relationships:
            found.append(f"{len(result.relationships)} relationships")
        if result.roles:
            found.append(f"{len(result.roles)} roles")
        if result.expressions:
            found.append(f"{len(result.expressions)} shared expressions")
        if result.model is not None:
            found.append("the model block")
    if tables:
        plural = "" if len(tables) == 1 else "s"
        extra = f" Also read {', '.join(found)}." if found else ""
        return (
            f"Found {len(tables)} table{plural} but no measures. "
            f"These tables only define columns and partitions.{extra}"
        )
    if found:
        return f"No measures in this text. Read {', '.join(found)}."
    if re.search(r"^\s*relationship\s", text, re.MULTILINE):
        return (
            "This is a relationships file. Measures live in the table files under "
            "definition/tables/."
        )
    if re.search(r"^\s*(model|database|culture|role|perspective)\s", text, re.MULTILINE):
        return (
            "This looks like a model, culture, or role file. Measures live in the "
            "table files under definition/tables/."
        )
    if re.search(r"^\s*(column|partition)\s", text, re.MULTILINE):
        return (
            "No measures found -- only columns and partitions. Paste a table that "
            "defines measures."
        )
    return "No measures found. Expected TMDL like `measure 'Total Sales' = SUM ( Sales[Amount] )`."


# ---------------------------------------------------------------------------
# parse_tmdl
# ---------------------------------------------------------------------------


def parse_tmdl(text: str) -> ParseResult:
    """
    Read every object in a blob of TMDL: tables, measures, columns, partitions,
    and since 2.1 relationships, calculation groups, hierarchies, roles,
    perspectives, shared expressions, refresh policies and the model block.

    Survives arbitrary pasted input rather than well-formed files off disk:
    concatenated tables in one blob, measures with no `table` line above them,
    `measure 'X'` with the `=` on the next line, fenced and unfenced multi-line
    expressions, doubled quotes in names, `isHidden` in both spellings, quoted
    property values, nested `kpi`/`annotation` blocks, CRLF, a BOM, and
    non-breaking spaces. When it finds nothing it says why.
    """
    diagnostics: list[Diagnostic] = []
    measures: list[RawMeasure] = []
    columns: list[RawColumn] = []
    tables: list[Table] = []
    result = ParseResult(measures, columns, tables, diagnostics)

    if not text or not text.strip():
        return result

    # Strip a BOM and normalise non-breaking spaces, both of which survive a copy
    # out of a browser or a Word document.
    body = text
    if body.startswith("﻿"):
        body = body[1:]
    body = body.replace(" ", " ")
    lines = re.split(r"\r?\n", body)

    table_name: str | None = None
    pending_desc: list[str] = []
    i = 0

    def owner() -> Table | None:
        return next((t for t in tables if t.name == table_name), None)

    def unsupported(keyword: str) -> None:
        result.unsupported[keyword] = result.unsupported.get(keyword, 0) + 1

    while i < len(lines):
        trimmed = lines[i].strip()
        decl_line = i + 1

        if trimmed.startswith("///"):
            pending_desc.append(re.sub(r"^///\s?", "", trimmed))
            i += 1
            continue

        # A "///" comment directly above a table declaration documents the table,
        # not the first measure below it, so it is consumed here rather than left
        # to leak downward onto the first measure.
        table_match = TABLE_RE.match(trimmed)
        if table_match:
            table_name = _named(table_match)
            description = " ".join(pending_desc).strip()
            pending_desc = []

            # The same property reader the measures and columns use. It stops at
            # the first child declaration, which is exactly where a table's own
            # properties end.
            properties, i, _ = _read_properties(lines, i + 1)

            existing = owner()
            if existing is not None:
                # A table split across two blocks in one paste: keep what each
                # half declared rather than letting the second silently win.
                existing.properties.update(properties)
                existing.is_hidden = existing.is_hidden or _flag(properties, "isHidden")
                if not existing.description:
                    existing.description = description
            else:
                tables.append(
                    Table(
                        name=table_name,
                        description=description or properties.get("description", ""),
                        is_hidden=_flag(properties, "isHidden"),
                        is_calculated=False,
                        partitions=[],
                        properties=properties,
                        line=decl_line,
                    )
                )
            continue

        # Groups: 1 = quoted name, 2 = bare name, 3 = the kind after the `=`. The
        # kind is the only thing in the whole file that says what the source is,
        # which is why a calculated table could not be identified until partitions
        # were read.
        partition_match = PARTITION_RE.match(trimmed)
        if partition_match:
            name = _named(partition_match)
            kind = partition_match.group(3)
            pending_desc = []

            properties, source, i = _read_partition(lines, i + 1)

            # A partition before any `table` line belongs to nothing this parser
            # can name. Dropped rather than thrown, the same way an orphan measure
            # is kept rather than crashing the paste -- but a partition has no
            # node and no row of its own, so there is nowhere to keep it.
            table = owner()
            if table is not None:
                table.partitions.append(
                    Partition(
                        name=name,
                        kind=kind,
                        mode=properties.get("mode"),
                        source=source,
                        properties=properties,
                    )
                )
                if kind == "calculated":
                    table.is_calculated = True
            continue

        # Groups on both: 1 = quoted name, 2 = bare name, 3 = the "= expr" tail,
        # 4 = the expression text after the "=".
        measure_match = MEASURE_RE.match(trimmed)
        if measure_match:
            name = _named(measure_match)
            description = " ".join(pending_desc).strip()
            pending_desc = []

            expression, next_i = _read_expression(
                lines,
                i + 1,
                measure_match.group(4) or "",
                measure_match.group(3) is not None,
            )
            properties, i, blocks = _read_properties(lines, next_i)

            measures.append(
                RawMeasure(
                    name=name,
                    table=table_name if table_name is not None else UNKNOWN_TABLE,
                    description=description or properties.get("description", ""),
                    expression=expression,
                    format_string=properties.get("formatString"),
                    display_folder=properties.get("displayFolder"),
                    is_hidden=_flag(properties, "isHidden"),
                    properties=properties,
                    line=decl_line,
                    blocks=blocks,
                )
            )
            continue

        column_match = COLUMN_RE.match(trimmed)
        if column_match:
            name = _named(column_match)
            description = " ".join(pending_desc).strip()
            pending_desc = []

            # A source column has no `=`, so there is no expression to read and
            # the properties start on the very next line.
            is_calculated = column_match.group(3) is not None
            if is_calculated:
                expression, next_i = _read_expression(
                    lines, i + 1, column_match.group(4) or "", True
                )
            else:
                expression, next_i = "", i + 1
            properties, i, blocks = _read_properties(lines, next_i)

            columns.append(
                RawColumn(
                    name=name,
                    table=table_name if table_name is not None else UNKNOWN_TABLE,
                    description=description or properties.get("description", ""),
                    expression=expression,
                    is_calculated=is_calculated,
                    properties=properties,
                    line=decl_line,
                    blocks=blocks,
                )
            )
            continue

        if CALC_GROUP_RE.match(trimmed):
            pending_desc = []
            group, i = _read_calculation_group(lines, i + 1, decl_line)
            table = owner()
            if table is not None:
                table.calculation_group = group
            else:
                unsupported("calculationGroup (no table)")
            continue

        hierarchy_match = HIERARCHY_RE.match(trimmed)
        if hierarchy_match:
            description = " ".join(pending_desc).strip()
            pending_desc = []
            hierarchy, i = _read_hierarchy(
                lines, i + 1, _named(hierarchy_match),
                table_name if table_name is not None else UNKNOWN_TABLE, description, decl_line,
            )
            table = owner()
            if table is not None:
                table.hierarchies.append(hierarchy)
            else:
                unsupported("hierarchy (no table)")
            continue

        if REFRESH_POLICY_RE.match(trimmed):
            pending_desc = []
            policy, i = _read_refresh_policy(
                lines, i + 1, table_name if table_name is not None else UNKNOWN_TABLE, decl_line
            )
            table = owner()
            if table is not None:
                table.refresh_policy = policy
            else:
                unsupported("refreshPolicy (no table)")
            continue

        relationship_match = RELATIONSHIP_RE.match(trimmed)
        if relationship_match:
            pending_desc = []
            rel, i = _read_relationship(lines, i + 1, _named(relationship_match), decl_line)
            result.relationships.append(rel)
            continue

        role_match = ROLE_RE.match(trimmed)
        if role_match:
            pending_desc = []
            role, i = _read_role(lines, i + 1, _named(role_match), decl_line)
            result.roles.append(role)
            continue

        perspective_match = PERSPECTIVE_RE.match(trimmed)
        if perspective_match:
            pending_desc = []
            persp, i = _read_perspective(lines, i + 1, _named(perspective_match), decl_line)
            result.perspectives.append(persp)
            continue

        expression_match = EXPRESSION_RE.match(trimmed)
        if expression_match:
            pending_desc = []
            shared, i = _read_shared_expression(lines, i + 1, expression_match, decl_line)
            result.expressions.append(shared)
            continue

        model_match = MODEL_RE.match(trimmed)
        if model_match:
            pending_desc = []
            properties, i, _ = _read_props_any(lines, i + 1)
            info = result.model or ModelInfo()
            info.name = _named(model_match) or info.name
            info.properties.update(properties)
            info.line = info.line or decl_line
            result.model = info
            continue

        database_match = DATABASE_RE.match(trimmed)
        if database_match:
            pending_desc = []
            properties, i, _ = _read_props_any(lines, i + 1)
            info = result.model or ModelInfo()
            info.compatibility_level = _int_or_none(properties.get("compatibilityLevel"))
            for key, value in properties.items():
                info.properties.setdefault(key, value)
            result.model = info
            continue

        ref_match = REF_RE.match(trimmed)
        if ref_match:
            info = result.model or ModelInfo()
            info.refs.setdefault(ref_match.group(1), []).append(_named(ref_match, 2, 3))
            result.model = info
            i += 1
            continue

        culture_match = CULTURE_RE.match(trimmed)
        if culture_match:
            pending_desc = []
            unsupported("cultureInfo")
            _, i, _ = _read_props_any(lines, i + 1)
            continue

        if QUERY_GROUP_RE.match(trimmed):
            pending_desc = []
            unsupported("queryGroup")
            _, i, _ = _read_props_any(lines, i + 1)
            continue

        pending_desc = []
        i += 1

    if not measures:
        diagnostics.append(
            Diagnostic("warning", _describe_empty_result(text, tables, result))
        )
    else:
        unowned = sum(1 for m in measures if m.table == UNKNOWN_TABLE)
        if unowned:
            if unowned == len(measures):
                message = (
                    "No `table` line found, so measures are ungrouped. Paste the table "
                    "declaration too if you want table names resolved."
                )
            else:
                plural = "" if unowned == 1 else "s"
                message = (
                    f"{unowned} measure{plural} appeared before any `table` line and have "
                    "no owning table."
                )
            diagnostics.append(Diagnostic("info", message))

        no_expression = sum(1 for m in measures if not m.expression.strip())
        if no_expression:
            plural = "" if no_expression == 1 else "s"
            diagnostics.append(
                Diagnostic(
                    "warning",
                    f"{no_expression} measure{plural} parsed with an empty expression -- "
                    "the DAX may not have come through the paste.",
                )
            )

    return result


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    slug = name.lower().replace("%", " pct")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return re.sub(r"(^-|-$)", "", slug)


def unique_slugs(names: Iterable[str]) -> dict[str, str]:
    """
    Make every measure id unique.

    Slugs collide once punctuation is stripped ("Allocation Drift" and "Allocation
    Drift %" both reduce to "allocation-drift"). A measure whose name slugifies to
    nothing still gets a usable id.

    INHERITED QUIRK: keyed by measure *name*, so two measures with the same name
    in different tables share one id -- the later one's.
    """
    counts: dict[str, int] = {}
    ids: dict[str, str] = {}
    for index, name in enumerate(names):
        base = slugify(name) or f"measure-{index + 1}"
        n = counts.get(base, 0) + 1
        counts[base] = n
        ids[name] = base if n == 1 else f"{base}-{n}"
    return ids


def _strip_non_code(expr: str) -> str:
    """
    Blank out string literals and comments so their contents can't be read as
    references.

    A commented-out `// CALCULATE ( [Total Sales], ... )` above a live expression
    credits dead code with a real dependency otherwise. Replaces with same-length
    whitespace so nothing else shifts position.
    """

    def blank(s: str) -> str:
        return re.sub(r"[^\n]", " ", s)

    out: list[str] = []
    i = 0
    while i < len(expr):
        two = expr[i : i + 2]

        # `--` is the other DAX line comment. Without it, an apostrophe in a comment
        # (`-- it's the row`) opened a quoted table name that ran to the next
        # apostrophe, and a real model reported three "undeclared tables" that were
        # lines of commented DAX.
        if two in ("//", "--"):
            end = expr.find("\n", i)
            stop = len(expr) if end == -1 else end
            out.append(blank(expr[i:stop]))
            i = stop
            continue

        if two == "/*":
            end = expr.find("*/", i + 2)
            stop = len(expr) if end == -1 else end + 2
            out.append(blank(expr[i:stop]))
            i = stop
            continue

        if expr[i] == '"':
            j = i + 1
            while j < len(expr):
                if expr[j] == '"' and j + 1 < len(expr) and expr[j + 1] == '"':
                    j += 2
                    continue
                if expr[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(blank(expr[i:j]))
            i = j
            continue

        out.append(expr[i])
        i += 1

    return "".join(out)


def _parse_folder_path(display_folder: str | None) -> list[str]:
    """
    Power BI display folders nest via backslash ("Time Intelligence\\YTD").
    Model-agnostic: no folder names are hardcoded anywhere.
    """
    if not display_folder or not display_folder.strip():
        return [UNFOLDERED]
    parts = [s.strip() for s in re.split(r"[\\/]", display_folder)]
    return [p for p in parts if p]


_MEASURE_REF_RE = re.compile(r"\[([^\[\]]+)\]")
_TABLE_REF_RE = re.compile(
    r"(?:'((?:[^']|'')*)'|(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_ ]*))\[([^\[\]]+)\]"
)
# Every reference-shaped token in one left-to-right pass: a quoted name with or
# without a bracket, a bare word with or without a bracket, or a bare bracket. One
# pass, because two independent scans overlap -- a regex for "quoted name not followed
# by `[`" started at the *closing* quote of `'Calendar'[Date]` and read
# `'[Date], LASTDATE ( '` as a table name. Sequential matching cannot do that.
_REF_TOKEN_RE = re.compile(
    r"'((?:[^']|'')*)'(\[[^\[\]]*\])?"      # 1: quoted table, 2: optional [column]
    r"|([A-Za-z_][A-Za-z0-9_]*)(\[[^\[\]]*\])?"  # 3: bare word, 4: optional [column]
    r"|\[([^\[\]]+)\]"                        # 5: bare [measure]
)


class _Resolver:
    """
    Reads references out of DAX against what the model declares.

    One object, because every reading has to agree: the measure graph, the tables a
    measure reaches, and the non-measure objects (calculation items, calculated
    columns and tables, role filters) that read a measure all go through the same
    three decisions --

    * `[Name]` is a measure, matched case-insensitively, as DAX matches it.
    * `Table[Name]` is a column of that table when the table declares one; when it
      does not and `Name` is a measure, it is that measure written with its home
      table in front -- which DAX allows and the app's port never resolved.
    * A table named on its own counts, and so does the measure's own table. The
      port excluded both, which left `SUM ( Sales[Amount] )` stored in `Sales`
      reading no table at all.
    """

    def __init__(self, measure_names: Iterable[str], table_names: Iterable[str],
                 columns_by_table: dict[str, set[str]]) -> None:
        self.measure_by_lower = {n.lower(): n for n in measure_names}
        self.table_by_lower = {n.lower(): n for n in table_names if n and n != UNKNOWN_TABLE}
        self.columns_by_table = {t.lower(): {c.lower() for c in cols}
                                 for t, cols in columns_by_table.items()}

    def canonical_table(self, name: str) -> str:
        return self.table_by_lower.get(name.lower(), name)

    def refs(self, expression: str) -> tuple[list[str], list[str]]:
        """(measures referenced, tables referenced), each in order of first mention."""
        code = _strip_non_code(expression)
        measures: list[str] = []
        tables: list[str] = []

        def add(acc: list[str], value: str) -> None:
            if value and value not in acc:
                acc.append(value)

        def table_and_name(table: str, name: str) -> None:
            declared_cols = self.columns_by_table.get(table.lower())
            is_column = declared_cols is not None and name.lower() in declared_cols
            measure = self.measure_by_lower.get(name.lower())
            if not is_column and measure is not None:
                add(measures, measure)
            else:
                add(tables, self.canonical_table(table))

        for match in _REF_TOKEN_RE.finditer(code):
            if match.group(1) is not None:
                table = match.group(1).replace("''", "'").strip()
                if match.group(2):
                    table_and_name(table, match.group(2)[1:-1])
                elif table:
                    # A quoted name on its own is a table, whatever the model declares.
                    add(tables, self.canonical_table(table))
            elif match.group(3) is not None:
                word = match.group(3)
                if match.group(4):
                    table_and_name(word, match.group(4)[1:-1])
                else:
                    # A bare word is a table only when the model declares one by that
                    # name and the word is not a function call -- otherwise every VAR
                    # and every function would be one.
                    rest = code[match.end():].lstrip()
                    if rest.startswith("("):
                        continue
                    canonical = self.table_by_lower.get(word.lower())
                    if canonical is not None:
                        add(tables, canonical)
            else:
                measure = self.measure_by_lower.get(match.group(5).lower())
                if measure is not None:
                    add(measures, measure)

        return measures, tables


def _resolver_for(raw: list[RawMeasure], result: ParseResult | None) -> _Resolver:
    table_names: set[str] = {m.table for m in raw}
    columns_by_table: dict[str, set[str]] = {}
    if result is not None:
        table_names |= {t.name for t in result.tables}
        for c in result.columns:
            table_names.add(c.table)
            columns_by_table.setdefault(c.table, set()).add(c.name)
    return _Resolver((m.name for m in raw), table_names, columns_by_table)


def build_graph(raw: list[RawMeasure], result: ParseResult | None = None) -> list[Measure]:
    """
    Resolve the measure graph. `result` carries the tables, columns, calculation
    groups and roles the references are read against; without it (the pre-2.1
    call shape) measures resolve against each other and their own tables only.
    """
    resolver = _resolver_for(raw, result)

    ids = unique_slugs([m.name for m in raw])
    measures: list[Measure] = []
    for m in raw:
        depends_on, tables = resolver.refs(m.expression)
        folder_path = _parse_folder_path(m.display_folder)
        measures.append(
            Measure(
                id=ids[m.name],
                name=m.name,
                table=m.table,
                description=m.description,
                expression=m.expression,
                format_string=m.format_string,
                display_folder=m.display_folder,
                is_hidden=m.is_hidden,
                properties=m.properties,
                depends_on=[d for d in depends_on if d != m.name],
                used_by=[],
                tables=tables,
                all_tables=[],
                folder_path=folder_path,
                folder=folder_path[0],
                folder_key=" / ".join(folder_path),
                folder_label=folder_path[-1],
                line=m.line,
                has_dynamic_format="formatStringDefinition" in m.blocks,
                has_kpi="kpi" in m.blocks,
                has_detail_rows="detailRows" in m.blocks,
            )
        )

    by_name = {m.name: m for m in measures}
    for m in measures:
        for dep in m.depends_on:
            target = by_name.get(dep)
            if target is not None:
                target.used_by.append(m.name)

    # Non-measure DAX that reads a measure. A measure read only by a calculation
    # item or a role filter is referenced, and saying otherwise is the wrong
    # finding this graph most often produced.
    if result is not None:
        def note(expression: str, label: str) -> None:
            if not expression.strip():
                return
            deps, _ = resolver.refs(expression)
            for dep in deps:
                target = by_name.get(dep)
                if target is not None and label not in target.referenced_by:
                    target.referenced_by.append(label)

        for t in result.tables:
            if t.calculation_group:
                for item in t.calculation_group.items:
                    note(item.expression, f"calculation item '{item.name}'")
                    note(item.format_string_definition, f"calculation item '{item.name}'")
            for p in t.partitions:
                if p.kind == "calculated":
                    note(p.source, f"calculated table '{t.name}'")
        for c in result.columns:
            if c.is_calculated:
                note(c.expression, f"column {c.table}[{c.name}]")
        for role in result.roles:
            for perm in role.table_permissions:
                note(perm.filter_expression, f"role '{role.name}'")

    # Resolve transitive upstream tables, for measures that only reference other
    # measures and so have no column references of their own.
    def resolve_tables(name: str, seen: set[str]) -> list[str]:
        if name in seen:
            return []
        seen.add(name)
        m = by_name.get(name)
        if m is None:
            return []
        acc = list(m.tables)
        for dep in m.depends_on:
            for t in resolve_tables(dep, seen):
                if t not in acc:
                    acc.append(t)
        return acc

    for m in measures:
        m.all_tables = resolve_tables(m.name, set())

    return measures


def build_columns(raw: list[RawColumn]) -> list[Column]:
    """
    Resolve columns for display. No graph work: a column has no
    measure-to-measure edges, so this only assigns a stable id and splits the
    display folder the same way a measure's is split.

    Ids are namespaced `table.column` rather than run through `unique_slugs`,
    because column names collide across tables constantly and by design -- every
    fact table has an `Amount` -- where measure names are unique model-wide.
    """
    seen: dict[str, int] = {}
    out: list[Column] = []

    for c in raw:
        base = f"{slugify(c.table) or 'table'}.{slugify(c.name) or 'column'}"
        n = seen.get(base, 0) + 1
        seen[base] = n

        folder_path = _parse_folder_path(c.properties.get("displayFolder"))
        out.append(
            Column(
                id=base if n == 1 else f"{base}-{n}",
                name=c.name,
                table=c.table,
                description=c.description,
                expression=c.expression,
                is_calculated=c.is_calculated,
                data_type=c.properties.get("dataType"),
                format_string=c.properties.get("formatString"),
                display_folder=c.properties.get("displayFolder"),
                is_hidden=_is_true(c.properties.get("isHidden")),
                is_key=_is_true(c.properties.get("isKey")),
                summarize_by=c.properties.get("summarizeBy"),
                sort_by_column=c.properties.get("sortByColumn"),
                data_category=c.properties.get("dataCategory"),
                properties=c.properties,
                folder_path=folder_path,
                folder=folder_path[0],
                folder_key=" / ".join(folder_path),
                line=c.line,
                has_variation="variation" in c.blocks,
            )
        )

    return out


def _is_true(value: str | None) -> bool:
    return value is not None and value.lower() != "false"


def parse_model(text: str) -> list[Measure]:
    """Parse and graph in one call, when only the measures are wanted."""
    result = parse_tmdl(text)
    return build_graph(result.measures, result)


def parse_paths(paths: Iterable[str | Path]) -> tuple[list[Measure], ParseResult]:
    """
    Read one or more `.tmdl` files (or directories of them) as a single blob.

    Concatenated rather than parsed separately, because that is what a paste of a
    whole model looks like and the parser is built for it: measure references
    resolve across table files only if the tables arrive together.
    """
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(sorted(path.glob("*.tmdl")))
        else:
            files.append(path)

    if not files:
        raise FileNotFoundError(f"No .tmdl files found in {list(paths)!r}")

    # `newline=""` matters: `read_text` defaults to universal newlines, which
    # rewrites CRLF to LF before the parser ever sees it, while Node's
    # `readFileSync` hands over the bytes as written. That difference alone made
    # the two ports disagree on a file whose lines ended `\r\r\n` — and a parser
    # whose answer depends on which language read the file is not one answer.
    # `utf-8-sig` still drops a BOM at decode; `parse_tmdl` strips it again for
    # text that arrives from a paste rather than a file.
    parts = []
    for f in files:
        with f.open(encoding="utf-8-sig", newline="") as handle:
            parts.append(handle.read())

    result = parse_tmdl("\n\n".join(parts))
    return build_graph(result.measures, result), result


# ---------------------------------------------------------------------------
# DAX tokenizer
# ---------------------------------------------------------------------------

#: Grouping punctuation, in open/close pairs.
OPENERS = "({"
CLOSERS = ")}"

#: Longest-first, so `<=` never tokenises as `<` then `=`.
_OPERATORS = ["<=", ">=", "<>", "&&", "||", "+", "-", "*", "/", "^", "=", "<", ">", "&", "."]

_WS_RE = re.compile(r"\s")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
# `[^\W\d]` is "a word character that is not a digit" -- i.e. a letter or an
# underscore, which is what \p{L}_ means in the JavaScript original.
_IDENT_START_RE = re.compile(r"[^\W\d]", re.UNICODE)
_IDENT_RE = re.compile(r"[^\W\d]\w*", re.UNICODE)


@dataclass
class Token:
    kind: str  # ws | lineComment | blockComment | string | ref | ident | number | op | punct
    text: str


def tokenize_dax(source: str) -> list[Token]:
    """
    Split DAX into tokens.

    The only thing this needs to get exactly right is where a *string, comment or
    bracketed reference* starts and ends -- everything else is a detail of
    presentation. A `[Measure]` or `'Table'[Column]` is deliberately kept as one
    token so no formatter can ever break a line inside a reference, and a `//`
    comment is its own token so a renderer knows it must end the line.
    """
    tokens: list[Token] = []
    i = 0

    while i < len(source):
        ch = source[i]

        if _WS_RE.match(ch):
            start = i
            while i < len(source) and _WS_RE.match(source[i]):
                i += 1
            tokens.append(Token("ws", source[start:i]))
            continue

        # Comments before operators: `//` and `--` both begin with operator chars.
        if source.startswith("//", i) or source.startswith("--", i):
            end = source.find("\n", i)
            stop = len(source) if end == -1 else end
            tokens.append(Token("lineComment", source[i:stop]))
            i = stop
            continue

        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            stop = len(source) if end == -1 else end + 2
            tokens.append(Token("blockComment", source[i:stop]))
            i = stop
            continue

        # Doubled quotes are an escaped quote, not a terminator.
        if ch == '"':
            j = i + 1
            while j < len(source):
                if source[j] == '"':
                    if j + 1 < len(source) and source[j + 1] == '"':
                        j += 2
                    else:
                        j += 1
                        break
                else:
                    j += 1
            tokens.append(Token("string", source[i:j]))
            i = j
            continue

        # 'Quoted Table'[Column] -- the bracket part is optional.
        if ch == "'":
            j = i + 1
            while j < len(source):
                if source[j] == "'":
                    if j + 1 < len(source) and source[j + 1] == "'":
                        j += 2
                    else:
                        j += 1
                        break
                else:
                    j += 1
            if j < len(source) and source[j] == "[":
                close = source.find("]", j)
                if close != -1:
                    j = close + 1
            tokens.append(Token("ref", source[i:j]))
            i = j
            continue

        # [Measure] or [Column]
        if ch == "[":
            close = source.find("]", i)
            stop = len(source) if close == -1 else close + 1
            tokens.append(Token("ref", source[i:stop]))
            i = stop
            continue

        if ch.isdigit() and ch.isascii():
            match = _NUMBER_RE.match(source, i)
            tokens.append(Token("number", match.group(0)))
            i += len(match.group(0))
            continue

        if _IDENT_START_RE.match(ch):
            match = _IDENT_RE.match(source, i)
            text = match.group(0)
            # An unquoted table reference -- Sales[Amount] -- is one token for the
            # same reason the quoted form is.
            if i + len(text) < len(source) and source[i + len(text)] == "[":
                close = source.find("]", i + len(text))
                if close != -1:
                    text = source[i : close + 1]
            tokens.append(Token("ident", text))
            i += len(text)
            continue

        if ch in OPENERS or ch in CLOSERS or ch == ",":
            tokens.append(Token("punct", ch))
            i += 1
            continue

        op = next((o for o in _OPERATORS if source.startswith(o, i)), None)
        if op:
            tokens.append(Token("op", op))
            i += len(op)
            continue

        # Anything unrecognised is passed through as a single character rather
        # than dropped, so the round-trip check downstream still holds.
        tokens.append(Token("op", ch))
        i += 1

    return tokens


def significant_tokens(source: str) -> list[str]:
    """The token stream with whitespace removed -- what formatting must preserve."""
    return [t.text for t in tokenize_dax(source) if t.kind != "ws"]


# ---------------------------------------------------------------------------
# DAX formatter
# ---------------------------------------------------------------------------

#: Line budget. A preference, not a hard limit -- long lines scroll rather than
#: wrap. Set it much tighter and single-argument calls start breaking across
#: three lines, which reads worse than a line that scrolls.
MAX_WIDTH = 56
INDENT = "    "


def _u16len(text: str) -> int:
    """
    Length in UTF-16 code units, which is what JavaScript's `String.length` is.

    Not a nicety. Every width decision in the formatter is a comparison against
    MAX_WIDTH, so on a measure containing an astral character — 🟢 in a status
    format string, ▲ is fine but 🟢 is not — Python's code-point count is one
    short per emoji and the two ports would break a line in different places.
    Found by exactly that: one measure in the sample model reported a longest line
    of 76 here against 77 there.
    """
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


@dataclass
class Group:
    open: str
    close: str
    #: Comma-separated argument lists. Empty for `()`.
    args: list[list["Token | Group"]]


def _is_group(e) -> bool:
    return isinstance(e, Group)


def _is_keyword(e, word: str) -> bool:
    return e is not None and not _is_group(e) and e.kind == "ident" and e.text.upper() == word


class _FormatError(Exception):
    pass


def format_dax(expression: str) -> str:
    """
    Re-indent DAX to the conventional layout: four-space steps, one `VAR` per
    line, `RETURN` on its own line, and long function calls broken one argument to
    a line with the closing paren under the call.

    TMDL that has been through a clipboard often arrives with its indentation
    flattened or over-deep, so what the model stored is not what you want to read.
    This rewrites the whitespace and nothing else.

    **It is self-checking.** The output is re-tokenised and compared against the
    input token for token; if a single token moved, was dropped or was invented,
    the original text is returned unchanged. Readable formatting is a nicety,
    mangled DAX is not -- so a bug in here degrades to "unformatted" rather than
    to "wrong".
    """
    original = re.sub(r"\s+$", "", expression or "")
    if not original.strip():
        return original

    unary: set[int] = set()
    try:
        elems = _parse_elems([t for t in tokenize_dax(original) if t.kind != "ws"])
        formatted = "\n".join(_render_block(elems, 0, unary))
    except _FormatError:
        return original

    if significant_tokens(original) != significant_tokens(formatted):
        return original
    return formatted


def _parse_elems(tokens: list[Token]) -> list["Token | Group"]:
    """
    Group balanced brackets into a tree, splitting each group on its top-level
    commas. Anything unbalanced raises, which sends the caller back to the
    original text rather than to a re-associated guess.
    """
    i = 0

    def args(closer: str | None) -> list[list["Token | Group"]]:
        nonlocal i
        lists: list[list["Token | Group"]] = [[]]
        while i < len(tokens):
            t = tokens[i]

            if closer is not None and t.text == closer:
                break
            if t.kind == "punct" and t.text in CLOSERS:
                raise _FormatError("unbalanced closer")

            if t.kind == "punct" and t.text == ",":
                lists.append([])
                i += 1
                continue

            if t.kind == "punct" and t.text in OPENERS:
                open_text = t.text
                close_text = CLOSERS[OPENERS.index(open_text)]
                i += 1
                inner = args(close_text)
                if i >= len(tokens) or tokens[i].text != close_text:
                    raise _FormatError("unclosed group")
                i += 1
                empty = len(inner) == 1 and len(inner[0]) == 0
                lists[-1].append(Group(open_text, close_text, [] if empty else inner))
                continue

            lists[-1].append(t)
            i += 1

        if closer is not None and i >= len(tokens):
            raise _FormatError("unclosed group")
        return lists

    top = args(None)
    if i < len(tokens):
        raise _FormatError("trailing tokens")
    # A measure expression has no top-level comma. If one turns up, this isn't a
    # shape worth reformatting.
    if len(top) != 1:
        raise _FormatError("top-level comma")
    return top[0]


def _has_line_comment(elems: list["Token | Group"]) -> bool:
    """True when some token below here must end its line."""
    for e in elems:
        if _is_group(e):
            if any(_has_line_comment(arg) for arg in e.args):
                return True
        elif e.kind == "lineComment":
            return True
    return False


def _mark_unary(elems: list["Token | Group"], unary: set[int]) -> None:
    """
    Record operators acting as a sign rather than as a binary operator, so `-1`
    doesn't come out as `- 1`.

    Keyed by `id()` because the JS original uses a WeakSet over token objects and
    two identical `-` tokens must stay distinguishable.
    """
    prev = None
    for e in elems:
        if _is_group(e):
            for arg in e.args:
                _mark_unary(arg, unary)
        elif (
            e.kind == "op"
            and e.text in ("-", "+")
            and (prev is None or (not _is_group(prev) and (prev.kind == "op" or prev.text == ",")))
        ):
            unary.add(id(e))
        prev = e


def _needs_space(prev, cur, unary: set[int]) -> bool:
    if prev is None:
        return False
    prev_text = prev.close if _is_group(prev) else prev.text
    cur_text = cur.open if _is_group(cur) else cur.text
    if cur_text == ",":
        return False
    if prev_text == "." or cur_text == ".":
        return False
    if not _is_group(prev) and id(prev) in unary:
        return False
    return True


def _inline(elems: list["Token | Group"], unary: set[int]) -> str:
    """One-line rendering. Only valid when the sequence holds no line comment."""
    out: list[str] = []
    prev = None
    for e in elems:
        if _needs_space(prev, e, unary):
            out.append(" ")
        if _is_group(e):
            if not e.args:
                out.append(e.open + e.close)
            else:
                inner = ", ".join(_inline(arg, unary) for arg in e.args)
                out.append(f"{e.open} {inner} {e.close}")
        else:
            out.append(e.text)
        prev = e
    return "".join(out)


def _render_block(elems: list["Token | Group"], depth: int, unary: set[int]) -> list[str]:
    """
    Render a `VAR`/`RETURN` body if there is one, otherwise a plain expression.
    Used at the top level and for every function argument, since DAX permits `VAR`
    inside an argument.
    """
    # The first `VAR` is not always token zero: a measure often opens with a block
    # of commented-out DAX above the live definition. Everything before it is a
    # preamble, and the statements start at the keyword.
    first = next((k for k, e in enumerate(elems) if _is_keyword(e, "VAR")), -1)
    if first == -1:
        return _render_expr(elems, depth, unary)

    pad = INDENT * depth
    lines: list[str] = []
    i = first
    if first > 0:
        lines.extend(_render_expr(elems[:first], depth, unary))

    while i < len(elems):
        if _is_keyword(elems[i], "VAR"):
            name = elems[i + 1] if i + 1 < len(elems) else None
            eq = elems[i + 2] if i + 2 < len(elems) else None
            if (
                name is None
                or _is_group(name)
                or eq is None
                or _is_group(eq)
                or eq.text != "="
            ):
                # Not the shape we thought. Fall back rather than reflow blindly.
                lines.extend(_render_expr(elems[i:], depth, unary))
                return lines
            head = f"VAR {name.text} ="
            j = i + 3
            while j < len(elems) and not _is_keyword(elems[j], "VAR") and not _is_keyword(
                elems[j], "RETURN"
            ):
                j += 1
            value_lines = _render_expr(elems[i + 3 : j], depth + 1, unary)
            one_liner = value_lines[0].strip() if len(value_lines) == 1 else None
            if (
                one_liner is not None
                and _u16len(pad) + _u16len(head) + 1 + _u16len(one_liner) <= MAX_WIDTH
            ):
                lines.append(f"{pad}{head} {one_liner}")
            else:
                lines.append(f"{pad}{head}")
                lines.extend(value_lines)
            i = j
            continue

        if _is_keyword(elems[i], "RETURN"):
            lines.append(f"{pad}RETURN")
            lines.extend(_render_expr(elems[i + 1 :], depth + 1, unary))
            return lines

        lines.extend(_render_expr(elems[i:], depth, unary))
        return lines

    return lines


#: Operators a long expression is broken in front of, loosest-binding first.
#: Trying the tiers in order means a long `a && b * c` breaks at the `&&` and
#: leaves the multiplication intact, which is how the expression reads.
_BREAK_TIERS: list[set[str]] = [
    {"&&", "||"},
    {"&"},
    {"+", "-"},
    {"*", "/"},
]


def _render_expr(elems: list["Token | Group"], depth: int, unary: set[int]) -> list[str]:
    if not elems:
        return []
    pad = INDENT * depth
    _mark_unary(elems, unary)

    if not _has_line_comment(elems):
        one = _inline(elems, unary)
        if _u16len(pad) + _u16len(one) <= MAX_WIDTH:
            return [f"{pad}{one}"]

    # A call whose arguments have to break: head inline, one argument to a line,
    # closer aligned under the head.
    last = elems[-1]
    head_elems = elems[:-1]
    if _is_group(last) and not _has_line_comment(head_elems):
        head_text = _inline(head_elems, unary)
        head_line = f"{pad}{head_text} {last.open}" if head_text else f"{pad}{last.open}"
        if _u16len(head_line) <= MAX_WIDTH:
            if not last.args:
                return [f"{head_line}{last.close}"]
            lines = [head_line]
            for idx, arg in enumerate(last.args):
                arg_lines = _render_block(arg, depth + 1, unary)
                if idx < len(last.args) - 1 and arg_lines:
                    arg_lines[-1] += ","
                lines.extend(arg_lines)
            lines.append(f"{pad}{last.close}")
            return lines

    # Long chains break in front of their loosest-binding operator. A sign is
    # never a break point -- it belongs to the operand it precedes.
    for tier in _BREAK_TIERS:
        cuts = [
            idx
            for idx, e in enumerate(elems)
            if idx > 0
            and not _is_group(e)
            and e.kind == "op"
            and e.text in tier
            and id(e) not in unary
        ]
        if not cuts:
            continue
        out: list[str] = []
        start = 0
        for cut in cuts + [len(elems)]:
            if cut == start:
                continue
            out.extend(_render_expr(elems[start:cut], depth, unary))
            start = cut
        return out

    # Nothing structural left to break on. Emit one line per comment-terminated
    # run, so a `//` comment can never swallow the code that followed it.
    if _has_line_comment(elems):
        lines = []
        run: list["Token | Group"] = []
        for e in elems:
            run.append(e)
            if not _is_group(e) and e.kind == "lineComment":
                before = _inline(run[:-1], unary)
                lines.append(f"{pad}{before} {e.text}" if before else f"{pad}{e.text}")
                run = []
        # Guard against recursing on the same sequence when the comment is nested
        # inside a group this pass couldn't break.
        if run:
            if len(run) == len(elems):
                return [f"{pad}{_inline(elems, unary)}"]
            lines.extend(_render_expr(run, depth, unary))
        return lines

    return [f"{pad}{_inline(elems, unary)}"]


# ---------------------------------------------------------------------------
# DAX anatomy
# ---------------------------------------------------------------------------


@dataclass
class VarBinding:
    name: str
    #: The VAR's own expression, formatted.
    expression: str

    def to_dict(self) -> dict:
        return {"name": self.name, "expression": self.expression}


@dataclass
class FunctionUse:
    name: str
    count: int

    def to_dict(self) -> dict:
        return {"name": self.name, "count": self.count}


@dataclass
class Anatomy:
    """
    What a measure's DAX is made of.

    Everything here is counted off the token stream rather than pattern-matched
    out of the raw text, so a function name inside a string literal or a comment
    is never counted, and `[Sales Amount]` is one reference rather than two words.
    """

    #: Formatted, because the stored text may have been flattened by a clipboard.
    formatted: str
    lines: int
    longest_line: int
    vars: list[VarBinding]
    #: Deepest nesting of `(` in the expression.
    depth: int
    #: Distinct functions, most-used first.
    functions: list[FunctionUse]
    #: Total call sites, which is not the same as `len(functions)`.
    calls: int
    #: Measures referenced directly by this expression.
    measure_refs: list[str]
    #: `Table[Column]` references, as written.
    column_refs: list[str]
    #: Tables named anywhere in the expression.
    table_refs: list[str]

    def to_dict(self) -> dict:
        return {
            "formatted": self.formatted,
            "lines": self.lines,
            "longestLine": self.longest_line,
            "vars": [v.to_dict() for v in self.vars],
            "depth": self.depth,
            "functions": [f.to_dict() for f in self.functions],
            "calls": self.calls,
            "measureRefs": list(self.measure_refs),
            "columnRefs": list(self.column_refs),
            "tableRefs": list(self.table_refs),
        }


_MEASURE_REF_EXACT_RE = re.compile(r"^\[([^\[\]]+)\]$")
_COLUMN_REF_QUOTED_RE = re.compile(r"^'((?:[^']|'')*)'\[([^\[\]]+)\]$")
_COLUMN_REF_BARE_RE = re.compile(r"^([^\W\d][\w ]*)\[([^\[\]]+)\]$", re.UNICODE)
_VAR_LINE_RE = re.compile(r"^\s*VAR\s+([^\W\d]\w*)\s*=\s*(.*)$", re.UNICODE)


def _calls_in(tokens: list[Token]) -> dict[str, int]:
    """
    A function call is an identifier immediately followed by `(`.

    That is the whole rule, and it is why this reads the tokens rather than the
    text: `SUM` in a comment is an `ident` with no `(` after it, and `"SUM("` is a
    string. Whitespace between the name and the paren is legal DAX and common in
    formatted code, so it is skipped.
    """
    counts: dict[str, int] = {}
    for i, token in enumerate(tokens):
        if token.kind != "ident" or "[" in token.text:
            continue
        j = i + 1
        while j < len(tokens) and tokens[j].kind == "ws":
            j += 1
        if j >= len(tokens) or tokens[j].text != "(":
            continue
        name = token.text.upper()
        counts[name] = counts.get(name, 0) + 1
    return counts


def measure_ref_name(text: str) -> str | None:
    """`[Measure]` -- a bare bracket reference with no table in front of it."""
    match = _MEASURE_REF_EXACT_RE.match(text)
    return match.group(1) if match else None


def _column_ref_parts(text: str) -> tuple[str, str] | None:
    """`Table[Column]` or `'Table'[Column]`, returned as `(table, column)`."""
    quoted = _COLUMN_REF_QUOTED_RE.match(text)
    if quoted:
        return quoted.group(1).replace("''", "'"), quoted.group(2)
    bare = _COLUMN_REF_BARE_RE.match(text)
    return (bare.group(1), bare.group(2)) if bare else None


def _max_depth(tokens: list[Token]) -> int:
    """How deep the parentheses nest. `VAR` blocks do not count as nesting."""
    depth = 0
    deepest = 0
    for token in tokens:
        if token.text == "(":
            depth += 1
            deepest = max(deepest, depth)
        elif token.text == ")":
            depth = max(0, depth - 1)
    return deepest


def _vars_in(formatted: str) -> list[VarBinding]:
    """
    The VAR bindings, read off the formatted text.

    Line-based rather than token-based on purpose: `format_dax` has already put
    one `VAR` per line and `RETURN` on its own line, so a binding's *expression*
    is then simply the lines until the next `VAR` or the `RETURN`. Recovering the
    same thing from tokens means re-implementing the formatter's block structure.
    """
    vars_out: list[VarBinding] = []
    current: VarBinding | None = None

    for line in formatted.split("\n"):
        start = _VAR_LINE_RE.match(line)
        is_return = bool(re.match(r"^\s*RETURN\b", line))
        if start:
            if current is not None:
                vars_out.append(current)
            current = VarBinding(start.group(1), start.group(2).strip())
        elif is_return:
            if current is not None:
                vars_out.append(current)
            current = None
        elif current is not None:
            current.expression = f"{current.expression}\n{line}".strip()
    if current is not None:
        vars_out.append(current)

    return [VarBinding(v.name, re.sub(r"\s+$", "", v.expression)) for v in vars_out]


def analyze(expression: str) -> Anatomy:
    formatted = format_dax(expression)
    tokens = tokenize_dax(formatted)
    lines = formatted.split("\n")

    measure_refs: list[str] = []
    column_refs: list[str] = []
    table_refs: list[str] = []

    for token in tokens:
        if token.kind not in ("ref", "ident"):
            continue
        measure = measure_ref_name(token.text)
        if measure:
            if measure not in measure_refs:
                measure_refs.append(measure)
            continue
        column = _column_ref_parts(token.text)
        if column:
            if token.text not in column_refs:
                column_refs.append(token.text)
            if column[0] not in table_refs:
                table_refs.append(column[0])

    counts = _calls_in(tokens)
    functions = sorted(
        (FunctionUse(name, count) for name, count in counts.items()),
        key=lambda f: (-f.count, _TEXT_KEY(f.name)),
    )

    return Anatomy(
        formatted=formatted,
        lines=len(lines),
        longest_line=max((_u16len(l) for l in lines), default=0),
        vars=_vars_in(formatted),
        depth=_max_depth(tokens),
        functions=functions,
        calls=sum(counts.values()),
        measure_refs=sorted(measure_refs, key=_TEXT_KEY),
        column_refs=sorted(column_refs, key=_TEXT_KEY),
        table_refs=sorted(table_refs, key=_TEXT_KEY),
    )


@dataclass
class ExpansionNode:
    """One node of a measure's reference tree."""

    #: Unique within one expansion, so each block toggles on its own.
    path: str
    id: str
    name: str
    formatted: str
    depth: int
    children: list["ExpansionNode"]
    #: This measure is already open somewhere above -- the chain loops.
    cyclic: bool

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "id": self.id,
            "name": self.name,
            "formatted": self.formatted,
            "depth": self.depth,
            "cyclic": self.cyclic,
            "children": [c.to_dict() for c in self.children],
        }


MAX_EXPANSION_DEPTH = 12


@dataclass
class MeasureStats:
    id: str
    name: str
    anatomy: Anatomy
    #: Measures upstream of this one, transitively.
    chain: int
    #: Arbitrary units. Only ever compared against other measures in this model.
    weight: int


@dataclass
class ModelIndex:
    by_id: dict[str, MeasureStats]
    #: Every measure's weight, ascending, for percentile lookups.
    weights: list[int]


def _weight_of(anatomy: Anatomy, chain: int) -> int:
    """
    A sum of things a reader would count anyway, not a calibrated metric.

    It exists to *rank* measures within one model -- "which of these is worth
    reading first" -- and every input is reported beside it so nobody has to trust
    the number on its own.
    """
    return anatomy.calls + anatomy.lines + anatomy.depth * 2 + len(anatomy.vars) * 2 + chain


def percentile_of(index: ModelIndex, weight: int) -> float:
    """
    The share of the model a weight outranks, 0 to 1.

    A percentile, not a score out of ten. "18 lines" means nothing on its own -- it
    is either the longest measure in the model or a rounding error, and only the
    model it lives in can say which. It is not a verdict: a heavy measure can be
    perfectly good, it is just the one worth reading first.
    """
    total = len(index.weights)
    if total <= 1:
        return 0.0
    below = sum(1 for w in index.weights if w < weight)
    return below / (total - 1)


# ---------------------------------------------------------------------------
# Metadata listings
# ---------------------------------------------------------------------------

# Properties a listing's "other" column drops: everything that already has a
# column of its own, plus `lineageTag`, which is a GUID on every single object and
# would be the widest thing on screen while telling nobody anything.
_COVERED = {
    "dataType",
    "formatString",
    "summarizeBy",
    "isHidden",
    "isKey",
    "sortByColumn",
    "description",
    "displayFolder",
    "lineageTag",
    "sourceLineageTag",
}


def extra_properties(properties: dict[str, str]) -> list[tuple[str, str]]:
    return [(k, v) for k, v in properties.items() if k not in _COVERED]


def display_folder_of(folder_key: str) -> str:
    """
    The folder as a listing reports it: blank when the model declared none.

    The graph needs a name for the unfoldered measures because it has to draw them
    a column, so it invents "Other". A table has no such need -- it has a cell, and
    an empty cell already says "none" without dressing this parser's bucket up as
    something the model declares.
    """
    return "" if folder_key == UNFOLDERED else folder_key


def decimal_places(fmt: str | None) -> int | None:
    """
    Decimal places a numeric format string pins down, or None when the format
    isn't about numbers at all -- a date pattern, a text literal, or nothing.

    Escapes and quoted literals are stripped first: `\\$#,0.00` and `"kg" #,0.0`
    both carry characters that would otherwise be read as placeholders.
    """
    if not fmt:
        return None
    positive = fmt.split(";")[0]
    cleaned = re.sub(r"\\.", "", positive)
    cleaned = re.sub(r'"[^"]*"', "", cleaned)
    cleaned = re.sub(r"'[^']*'", "", cleaned)
    if not re.search(r"[0#]", cleaned):
        return None
    point = cleaned.find(".")
    if point == -1:
        return 0
    run = re.match(r"^[0#]*", cleaned[point + 1 :])
    return len(run.group(0)) if run else 0


@dataclass
class TableRow:
    """
    A table: what it declares about itself, and what it holds.

    Every *count* here is counted from the measures and columns underneath it,
    because the question a table listing answers is "what is in this table". The
    table's own facts come off the `table` line and its partition blocks, and are
    kept separate from the counts on purpose: `is_calculated` is a claim about the
    whole table and `calculated_columns` is a claim about columns inside it.
    """

    id: str
    name: str
    description: str
    #: The table's **own** flag, off its `table` line -- not a count of what is
    #: hidden inside it.
    is_hidden: bool
    #: The **whole table** is DAX, because one of its partitions is `= calculated`.
    is_calculated: bool
    measures: int
    columns: int
    hidden_columns: int
    calculated_columns: int
    #: Distinct display folders used by anything in the table.
    folders: int
    data_types: list[str]
    #: What the table is loaded from, so a pane can label DAX versus M.
    partitions: list[Partition]
    #: Every `key: value` the `table` line itself declared.
    properties: dict[str, str]
    #: The table is a calculation group; `calculation_group` carries its items.
    calculation_group: CalculationGroup | None = None
    hierarchies: list[Hierarchy] = field(default_factory=list)
    refresh_policy: RefreshPolicy | None = None
    line: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "isHidden": self.is_hidden,
            "isCalculated": self.is_calculated,
            "isCalculationGroup": self.calculation_group is not None,
            "measures": self.measures,
            "columns": self.columns,
            "hiddenColumns": self.hidden_columns,
            "calculatedColumns": self.calculated_columns,
            "folders": self.folders,
            "dataTypes": list(self.data_types),
            "partitions": [p.to_dict() for p in self.partitions],
            "properties": dict(self.properties),
            "calculationGroup": (
                self.calculation_group.to_dict() if self.calculation_group else None
            ),
            "hierarchies": [h.to_dict() for h in self.hierarchies],
            "refreshPolicy": self.refresh_policy.to_dict() if self.refresh_policy else None,
            "line": self.line,
        }


def table_rows(measures: list[Measure], columns: list[Column], tables: list[Table]) -> list[TableRow]:
    declared = {t.name: t for t in tables}

    # Every table that declared itself, plus any that only shows up as an owner --
    # a measures-only paste has no `table` line but the measures still belong
    # somewhere, and a listing that omitted it would be lying about the model.
    names: list[str] = list(declared.keys())
    for owner in [m.table for m in measures] + [c.table for c in columns]:
        if owner not in names:
            names.append(owner)

    rows: list[TableRow] = []
    for name in names:
        mine_measures = [m for m in measures if m.table == name]
        mine_columns = [c for c in columns if c.table == name]
        folders = {display_folder_of(m.folder_key) for m in mine_measures}
        folders |= {display_folder_of(c.folder_key) for c in mine_columns}
        folders.discard("")
        types = {c.data_type for c in mine_columns if c.data_type}
        table = declared.get(name)
        rows.append(
            TableRow(
                id=name,
                name=name,
                description=table.description if table else "",
                is_hidden=table.is_hidden if table else False,
                is_calculated=table.is_calculated if table else False,
                measures=len(mine_measures),
                columns=len(mine_columns),
                hidden_columns=sum(1 for c in mine_columns if c.is_hidden),
                calculated_columns=sum(1 for c in mine_columns if c.is_calculated),
                folders=len(folders),
                data_types=sorted(types, key=_TEXT_KEY),
                partitions=table.partitions if table else [],
                properties=table.properties if table else {},
                calculation_group=table.calculation_group if table else None,
                hierarchies=table.hierarchies if table else [],
                refresh_policy=table.refresh_policy if table else None,
                line=table.line if table else 0,
            )
        )

    return sorted(rows, key=lambda r: _TEXT_KEY(r.name))


# ---------------------------------------------------------------------------
# Queries
#
# What a skill actually needs: an answer about one measure, not the model. A
# 140-measure model is ~240 KB as `--dump`, and reading it to answer "what breaks
# if I change this" is most of a context window spent on the wrong thing.
# ---------------------------------------------------------------------------


class Model:
    """Everything the parser found, with lookups over it."""

    def __init__(self, result: ParseResult) -> None:
        self.result = result
        self.measures = build_graph(result.measures, result)
        self.columns = build_columns(result.columns)
        self.tables = result.tables
        self.diagnostics = result.diagnostics
        self.relationships = result.relationships
        self.roles = result.roles
        self.perspectives = result.perspectives
        self.expressions = result.expressions
        self.model_info = result.model
        self.unsupported = result.unsupported
        self._by_name = {m.name: m for m in self.measures}
        self._by_id = {m.id: m for m in self.measures}
        self._by_lower = {m.name.lower(): m for m in self.measures}
        self._index: ModelIndex | None = None

    # -- construction -------------------------------------------------------

    @classmethod
    def from_text(cls, text: str) -> "Model":
        return cls(parse_tmdl(text))

    @classmethod
    def from_paths(cls, paths: Iterable[str | Path]) -> "Model":
        _, result = parse_paths(paths)
        return cls(result)

    def __len__(self) -> int:
        return len(self.measures)

    def __iter__(self):
        return iter(self.measures)

    # -- lookups ------------------------------------------------------------

    def get(self, name: str) -> Measure | None:
        """Exact match first, then case-insensitive. Returns None if unknown."""
        return self._by_name.get(name) or self._by_lower.get(name.lower())

    def find(self, needle: str) -> list[Measure]:
        """Every measure whose name contains `needle`, case-insensitively."""
        low = needle.lower()
        return [m for m in self.measures if low in m.name.lower()]

    def folders(self) -> dict[str, int]:
        """Measure count per full folder path, ordered the way the app orders them."""
        counts: dict[str, int] = {}
        for m in self.measures:
            counts[m.folder_key] = counts.get(m.folder_key, 0) + 1
        return dict(
            sorted(
                counts.items(),
                key=lambda kv: (_FOLDER_KEY(kv[0].split(" / ")[0]), _TEXT_KEY(kv[0])),
            )
        )

    def top_folders(self) -> list[str]:
        """Top-level folders in the order the graph lays its columns out."""
        seen: list[str] = []
        for m in self.measures:
            if m.folder not in seen:
                seen.append(m.folder)
        return sorted(seen, key=_FOLDER_KEY)

    def link_count(self) -> int:
        return sum(len(m.depends_on) for m in self.measures)

    def table_rows(self) -> list[TableRow]:
        return table_rows(self.measures, self.columns, self.tables)

    # -- graph walks --------------------------------------------------------

    def upstream(self, name: str) -> list[str]:
        """Everything `name` depends on, transitively. Cycle-safe."""
        return self._walk(name, lambda m: m.depends_on)

    def downstream(self, name: str) -> list[str]:
        """
        Everything that would be affected if `name` changed, transitively.

        This is the blast-radius question, and the reason the whole graph exists.
        """
        return self._walk(name, lambda m: m.used_by)

    def _walk(self, name: str, edges: Callable[[Measure], list[str]]) -> list[str]:
        root = self.get(name)
        if root is None:
            return []
        seen: set[str] = {root.name}
        order: list[str] = []
        queue = list(edges(root))
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            order.append(current)
            node = self._by_name.get(current)
            if node is not None:
                queue.extend(edges(node))
        return order

    def unreferenced(self) -> list[Measure]:
        """
        Visible measures no other *parsed object* references: no measure, no
        calculation item, no calculated column or table, no role filter.

        Not "unused". Report pages and runtime evidence are not in a TMDL file, so
        a measure that is terminal here may be on every page of a report. This is
        a list to check, never a list to delete, and it is named for what it can
        actually see.
        """
        return [m for m in self.measures
                if not m.used_by and not m.referenced_by and not m.is_hidden]

    def unused(self) -> list[Measure]:
        """The pre-2.1 name. `unreferenced()` says what the list is."""
        return self.unreferenced()

    def orphans(self) -> list[Measure]:
        """Measures that reference no other measure and no table -- suspicious."""
        return [m for m in self.measures if not m.depends_on and not m.all_tables]

    # -- DAX ----------------------------------------------------------------

    def index(self) -> ModelIndex:
        """
        Every measure analysed once, which is what makes the weight a *ranking*.

        Cached: placing one measure against the other 139 means analysing all 140,
        and doing that per question would redo the same work every time.
        """
        if self._index is None:
            self._index = index_model(self)
        return self._index

    def anatomy(self, name: str) -> MeasureStats | None:
        m = self.get(name)
        return self.index().by_id.get(m.id) if m else None

    def heaviest(self, limit: int = 10) -> list[MeasureStats]:
        stats = list(self.index().by_id.values())
        return sorted(stats, key=lambda s: (-s.weight, _TEXT_KEY(s.name)))[:limit]

    def expand(self, name: str, max_depth: int = MAX_EXPANSION_DEPTH) -> ExpansionNode | None:
        m = self.get(name)
        return expand(self, m.id, max_depth) if m else None

    def function_use(self) -> list[FunctionUse]:
        """Every DAX function the model calls, most-used first."""
        counts: dict[str, int] = {}
        for stats in self.index().by_id.values():
            for use in stats.anatomy.functions:
                counts[use.name] = counts.get(use.name, 0) + use.count
        return sorted(
            (FunctionUse(name, count) for name, count in counts.items()),
            key=lambda f: (-f.count, _TEXT_KEY(f.name)),
        )

    # -- reports ------------------------------------------------------------

    def overview(self) -> dict:
        return {
            "measures": len(self.measures),
            "columns": len(self.columns),
            "tables": [t.name for t in self.tables],
            "folders": self.folders(),
            "links": self.link_count(),
            "hidden": sum(1 for m in self.measures if m.is_hidden),
            "hidden_tables": sum(1 for t in self.tables if t.is_hidden),
            "calculated_tables": sum(1 for t in self.tables if t.is_calculated),
            "calculated_columns": sum(1 for c in self.columns if c.is_calculated),
            "no_expression": sum(1 for m in self.measures if not m.expression.strip()),
            "unused": len(self.unreferenced()),
            "unreferenced": len(self.unreferenced()),
            "relationships": len(self.relationships),
            "roles": len(self.roles),
            "calculation_groups": sum(1 for t in self.tables if t.calculation_group),
            "hierarchies": sum(len(t.hierarchies) for t in self.tables),
            "most_referenced": [
                {"name": m.name, "downstream": len(m.used_by)}
                for m in sorted(
                    self.measures, key=lambda m: (-len(m.used_by), _TEXT_KEY(m.name))
                )[:10]
                if m.used_by
            ],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }

    def detail(self, name: str) -> dict | None:
        m = self.get(name)
        if m is None:
            return None
        return {
            **m.brief(),
            "description": m.description,
            "format_string": m.format_string,
            "decimals": decimal_places(m.format_string),
            "expression": m.expression,
            "formatted": format_dax(m.expression),
            "depends_on": m.depends_on,
            "used_by": m.used_by,
            "tables": m.tables,
            "all_tables": m.all_tables,
            "properties": dict(m.properties),
        }

    def dump(self, anatomy: bool = True) -> dict:
        """
        Everything the parser knows, as plain JSON.

        The canonical shape `tmdl-parser.js --dump` emits, so the two ports can be
        diffed against each other on the same input. `tests/parity.py` is that
        diff.
        """
        payload: dict = {
            "version": __version__,
            "count": len(self.measures),
            "linkCount": self.link_count(),
            "folders": self.top_folders(),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "tables": [t.to_dict() for t in self.tables],
            "columns": [c.to_dict() for c in self.columns],
            "measures": [m.to_dict() for m in self.measures],
            "tableRows": [r.to_dict() for r in self.table_rows()],
            # Since 2.1. Each is the list as declared, in file order.
            "relationships": [r.to_dict() for r in self.relationships],
            "roles": [r.to_dict() for r in self.roles],
            "perspectives": [p.to_dict() for p in self.perspectives],
            "expressions": [e.to_dict() for e in self.expressions],
            "model": self.model_info.to_dict() if self.model_info else None,
            "unsupported": dict(sorted(self.unsupported.items())),
        }
        if anatomy:
            index = self.index()
            payload["anatomy"] = {
                stats.id: {
                    "chain": stats.chain,
                    "weight": stats.weight,
                    "lines": stats.anatomy.lines,
                    "longestLine": stats.anatomy.longest_line,
                    "depth": stats.anatomy.depth,
                    "calls": stats.anatomy.calls,
                    "functions": [f.to_dict() for f in stats.anatomy.functions],
                    "vars": [v.to_dict() for v in stats.anatomy.vars],
                    "measureRefs": list(stats.anatomy.measure_refs),
                    "columnRefs": list(stats.anatomy.column_refs),
                    "tableRefs": list(stats.anatomy.table_refs),
                    "formatted": stats.anatomy.formatted,
                }
                for stats in index.by_id.values()
            }
        return payload


def index_model(model: "Model") -> ModelIndex:
    by_id: dict[str, MeasureStats] = {}
    for measure in model.measures:
        anatomy = analyze(measure.expression)
        chain = len(model.upstream(measure.name))
        by_id[measure.id] = MeasureStats(
            id=measure.id,
            name=measure.name,
            anatomy=anatomy,
            chain=chain,
            weight=_weight_of(anatomy, chain),
        )
    return ModelIndex(by_id=by_id, weights=sorted(s.weight for s in by_id.values()))


def expand(model: "Model", measure_id: str, max_depth: int = MAX_EXPANSION_DEPTH) -> ExpansionNode | None:
    """
    One measure's DAX with its referenced measures hanging off it.

    The expansion is a **tree of blocks, not spliced text**. Substituting a
    measure's expression into the parent's would read as if the two were
    interchangeable, and they are not: a measure reference in a row context carries
    an implicit `CALCULATE` that the raw expression does not. Keeping the child as
    its own block says "this is what feeds that" without claiming the result would
    evaluate the same.
    """
    root = model._by_id.get(measure_id)
    if root is None:
        return None

    def build(measure: Measure, depth: int, path: str, ancestors: set[str]) -> ExpansionNode:
        cyclic = measure.name in ancestors
        node = ExpansionNode(
            path=path,
            id=measure.id,
            name=measure.name,
            formatted=format_dax(measure.expression),
            depth=depth,
            children=[],
            cyclic=cyclic,
        )
        if cyclic or depth >= max_depth:
            return node

        next_ancestors = ancestors | {measure.name}
        children = [model._by_name[n] for n in measure.depends_on if n in model._by_name]
        children.sort(key=lambda m: _TEXT_KEY(m.name))
        node.children = [
            build(child, depth + 1, f"{path}.{i}", next_ancestors)
            for i, child in enumerate(children)
        ]
        return node

    return build(root, 0, "r", set())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fmt_overview(model: Model) -> str:
    o = model.overview()
    # Top-level folders, which is what the app's Folders stat counts. The listing
    # below it is by full path, so a model with `Time Intelligence\YTD` shows one
    # folder and two paths — a real distinction, and the reason both are printed.
    out = [
        f"{o['measures']} measures | {len(model.top_folders())} folders | "
        f"{len(o['tables'])} tables | {o['columns']} columns | {o['links']} links"
    ]
    extras = []
    if o["hidden"]:
        extras.append(f"{o['hidden']} hidden measures")
    if o["hidden_tables"]:
        extras.append(f"{o['hidden_tables']} hidden tables")
    if o["calculated_tables"]:
        extras.append(f"{o['calculated_tables']} calculated tables")
    if o["calculated_columns"]:
        extras.append(f"{o['calculated_columns']} calculated columns")
    if o["unused"]:
        extras.append(f"{o['unused']} unused")
    if extras:
        out.append(" | ".join(extras))
    for d in o["diagnostics"]:
        out.append(f"[{d['level']}] {d['message']}")

    out.append("")
    out.append("Folder paths:")
    for folder, count in o["folders"].items():
        out.append(f"  {count:>4}  {folder}")

    if o["most_referenced"]:
        out.append("")
        out.append("Most referenced:")
        for entry in o["most_referenced"]:
            out.append(f"  {entry['downstream']:>4}  {entry['name']}")
    return "\n".join(out)


def _did_you_mean(model: Model, name: str) -> str:
    near = model.find(name)
    if near:
        return f"No measure named {name!r}. Did you mean:\n" + "\n".join(
            f"  {m.name}" for m in near[:10]
        )
    return f"No measure named {name!r}."


def _fmt_detail(model: Model, name: str) -> str:
    d = model.detail(name)
    if d is None:
        return _did_you_mean(model, name)

    out = [d["name"], f"  table   {d['table']}", f"  folder  {d['folder']}"]
    if d["format_string"]:
        decimals = "" if d["decimals"] is None else f"  ({d['decimals']} dp)"
        out.append(f"  format  {d['format_string']}{decimals}")
    if d["hidden"]:
        out.append("  hidden  yes")
    if d["description"]:
        out.append(f"  note    {d['description']}")
    out.append("")
    out.append(d["formatted"] or "(no expression parsed)")
    out.append("")
    for label, names in (("Depends on", d["depends_on"]), ("Used by", d["used_by"])):
        out.append(f"{label} ({len(names)}):")
        if names:
            out.extend(f"  {n}" for n in names)
        else:
            out.append("  none")
    if d["all_tables"]:
        out.append(f"Tables reached: {', '.join(d['all_tables'])}")
    return "\n".join(out)


def _fmt_impact(model: Model, name: str) -> str:
    if model.get(name) is None:
        return _did_you_mean(model, name)
    down = model.downstream(name)
    up = model.upstream(name)
    out = [
        name,
        f"  {len(down)} measures break if this changes",
        f"  {len(up)} measures it reads from",
        "",
    ]
    if down:
        out.append("Downstream:")
        out.extend(f"  {n}" for n in down)
    else:
        out.append("Nothing references it.")
    return "\n".join(out)


def _fmt_anatomy(model: Model, name: str) -> str:
    stats = model.anatomy(name)
    if stats is None:
        return _did_you_mean(model, name)
    a = stats.anatomy
    index = model.index()
    percent = round(percentile_of(index, stats.weight) * 100)
    out = [
        stats.name,
        f"  {a.lines} lines | {len(a.vars)} vars | nesting {a.depth} | "
        f"{a.calls} calls ({len(a.functions)} distinct) | chain {stats.chain}",
        f"  weight {stats.weight} — heavier than {percent}% of the "
        f"{len(index.weights)} measures in this model",
        "",
        a.formatted or "(no expression parsed)",
    ]
    if a.vars:
        out += ["", "Variables:"]
        out += [f"  {v.name}" for v in a.vars]
    if a.functions:
        out += ["", "Functions:"]
        out += [f"  {u.count:>3}x  {u.name}" for u in a.functions]
    if a.measure_refs:
        out += ["", "Measures read: " + ", ".join(a.measure_refs)]
    if a.column_refs:
        out += ["Columns read:  " + ", ".join(a.column_refs)]
    if a.table_refs:
        out += ["Tables read:   " + ", ".join(a.table_refs)]
    return "\n".join(out)


def _fmt_expand(model: Model, name: str, max_depth: int) -> str:
    root = model.expand(name, max_depth)
    if root is None:
        return _did_you_mean(model, name)

    out: list[str] = []

    def walk(node: ExpansionNode) -> None:
        pad = "  " * node.depth
        label = node.name if node.depth == 0 else f"[{node.name}]"
        suffix = "  (already above — chain loops)" if node.cyclic else ""
        out.append(f"{pad}{label}{suffix}")
        for line in (node.formatted or "(no expression parsed)").split("\n"):
            out.append(f"{pad}  | {line}")
        for child in node.children:
            walk(child)

    walk(root)
    out.append("")
    out.append(
        "Each nested block is the measure referenced above it, not a substitution. "
        "A measure reference carries an implicit CALCULATE in row context; its "
        "expression on its own does not."
    )
    return "\n".join(out)


def _fmt_tables(model: Model) -> str:
    rows = model.table_rows()
    if not rows:
        return "This paste declares no tables."
    out = [f"{len(rows)} tables", ""]
    for r in rows:
        flags = []
        if r.is_hidden:
            flags.append("hidden")
        if r.is_calculated:
            flags.append("calculated")
        flag_text = f"  ({', '.join(flags)})" if flags else ""
        out.append(f"  {r.name}{flag_text}")
        out.append(
            f"      {r.measures} measures, {r.columns} columns "
            f"({r.hidden_columns} hidden, {r.calculated_columns} calculated), "
            f"{r.folders} folders"
        )
        for p in r.partitions:
            mode = f", mode {p.mode}" if p.mode else ""
            out.append(f"      partition {p.name!r} = {p.kind}{mode}")
        if r.data_types:
            out.append(f"      types: {', '.join(r.data_types)}")
    return "\n".join(out)


def _fmt_columns(model: Model, needle: str) -> str:
    low = needle.lower()
    hits = [
        c
        for c in model.columns
        if low in c.name.lower() or low in c.table.lower() or low in (c.data_type or "").lower()
    ]
    if not hits:
        return f"No column matching {needle!r}."
    out = [f"{len(hits)} columns matching {needle!r}", ""]
    for c in hits:
        bits = [c.data_type or "?"]
        if c.is_calculated:
            bits.append("calculated")
        if c.is_hidden:
            bits.append("hidden")
        if c.is_key:
            bits.append("key")
        if c.summarize_by and c.summarize_by != "none":
            bits.append(f"sum:{c.summarize_by}")
        out.append(f"  {c.table}[{c.name}]  ({', '.join(bits)})")
        if c.expression:
            out.append(f"      = {c.expression.splitlines()[0]}")
    return "\n".join(out)


def _fmt_folder(model: Model, folder: str) -> str:
    low = folder.lower()
    hits = [m for m in model if low in m.folder_key.lower()]
    if not hits:
        return f"No folder matching {folder!r}. Known folders:\n" + "\n".join(
            f"  {f}" for f in model.folders()
        )
    out = [f"{len(hits)} measures in folders matching {folder!r}", ""]
    for m in hits:
        flag = " (hidden)" if m.is_hidden else ""
        out.append(f"  {m.name}{flag}  [up {len(m.depends_on)}, down {len(m.used_by)}]")
    return "\n".join(out)


def _fmt_heaviest(model: Model, limit: int) -> str:
    stats = model.heaviest(limit)
    if not stats:
        return "No measures."
    out = [f"Heaviest {len(stats)} measures — a ranking within this model, not a verdict", ""]
    for s in stats:
        a = s.anatomy
        out.append(
            f"  {s.weight:>4}  {s.name}"
            f"  [{a.lines}L {len(a.vars)}v depth {a.depth} {a.calls} calls chain {s.chain}]"
        )
    return "\n".join(out)


def _fmt_functions(model: Model) -> str:
    uses = model.function_use()
    if not uses:
        return "No function calls found."
    out = [f"{len(uses)} distinct DAX functions", ""]
    out += [f"  {u.count:>4}x  {u.name}" for u in uses]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tmdl_parser",
        description="Parse TMDL and answer questions about its measures, columns, "
        "tables and DAX.",
    )
    parser.add_argument("paths", nargs="+", help=".tmdl file(s), or a directory")
    parser.add_argument("--measure", metavar="NAME", help="DAX and both reference lists")
    parser.add_argument("--impact", metavar="NAME", help="what breaks if NAME changes")
    parser.add_argument("--anatomy", metavar="NAME", help="what NAME's DAX is made of")
    parser.add_argument("--expand", metavar="NAME", help="NAME's DAX with its references under it")
    parser.add_argument(
        "--depth", type=int, default=MAX_EXPANSION_DEPTH, help="how deep --expand goes"
    )
    parser.add_argument("--folder", metavar="NAME", help="measures in a display folder")
    parser.add_argument("--columns", metavar="TEXT", help="columns matching name, table or type")
    parser.add_argument("--tables", action="store_true", help="every table and what it holds")
    parser.add_argument("--find", metavar="TEXT", help="measures whose name contains TEXT")
    parser.add_argument("--unused", action="store_true", help="visible measures nothing references")
    parser.add_argument("--heaviest", action="store_true", help="measures worth reading first")
    parser.add_argument("--limit", type=int, default=10, help="rows for --heaviest")
    parser.add_argument("--functions", action="store_true", help="every DAX function the model calls")
    parser.add_argument("--dump", action="store_true", help="everything, as canonical JSON")
    parser.add_argument("--no-anatomy", action="store_true", help="omit DAX anatomy from --dump")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    # Windows defaults stdout to cp1252, which cannot encode the ▲ and 🟢 that
    # turn up in real format strings and descriptions — and defaults its newline
    # to CRLF, which would make `--dump` differ from the JS port's bytes on this
    # platform alone. Both are pinned rather than left to the console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")

    try:
        model = Model.from_paths(args.paths)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.dump:
        payload = model.dump(anatomy=not args.no_anatomy)
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    if args.measure:
        payload = model.detail(args.measure)
        text = _fmt_detail(model, args.measure)
    elif args.impact:
        payload = {
            "measure": args.impact,
            "downstream": model.downstream(args.impact),
            "upstream": model.upstream(args.impact),
        }
        text = _fmt_impact(model, args.impact)
    elif args.anatomy:
        stats = model.anatomy(args.anatomy)
        payload = (
            None
            if stats is None
            else {
                "name": stats.name,
                "chain": stats.chain,
                "weight": stats.weight,
                "percentile": percentile_of(model.index(), stats.weight),
                **stats.anatomy.to_dict(),
            }
        )
        text = _fmt_anatomy(model, args.anatomy)
    elif args.expand:
        root = model.expand(args.expand, args.depth)
        payload = root.to_dict() if root else None
        text = _fmt_expand(model, args.expand, args.depth)
    elif args.tables:
        payload = [r.to_dict() for r in model.table_rows()]
        text = _fmt_tables(model)
    elif args.columns:
        low = args.columns.lower()
        payload = [
            c.to_dict()
            for c in model.columns
            if low in c.name.lower()
            or low in c.table.lower()
            or low in (c.data_type or "").lower()
        ]
        text = _fmt_columns(model, args.columns)
    elif args.folder:
        low = args.folder.lower()
        payload = [m.brief() for m in model if low in m.folder_key.lower()]
        text = _fmt_folder(model, args.folder)
    elif args.find:
        hits = model.find(args.find)
        payload = [m.brief() for m in hits]
        text = "\n".join(f"{m.name}  [{m.folder_key}]" for m in hits) or "No match."
    elif args.unused:
        hits = model.unused()
        payload = [m.brief() for m in hits]
        text = "\n".join(f"{m.name}  [{m.folder_key}]" for m in hits) or "None."
    elif args.heaviest:
        payload = [
            {"name": s.name, "weight": s.weight, "chain": s.chain, **s.anatomy.to_dict()}
            for s in model.heaviest(args.limit)
        ]
        text = _fmt_heaviest(model, args.limit)
    elif args.functions:
        payload = [u.to_dict() for u in model.function_use()]
        text = _fmt_functions(model)
    else:
        payload = model.overview()
        text = _fmt_overview(model)

    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
