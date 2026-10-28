---
name: semantic-model-analyser
description: Read a Power BI semantic model or its Power Query as text and report on it -- tables, columns, measures, display folders, format strings, descriptions, DAX dependency chains, relationships, roles, calculation groups, partitions, storage mode, query steps, custom functions, parameters, connectors and native SQL. Use whenever the user asks what is in a model, wants a model audit, review or documentation, pastes TMDL or Power Query M, hands over .tmdl/.pq/.m/.txt files, a folder or a PBIP project, asks which measures are unreferenced, undocumented or inconsistently formatted, what breaks if a measure changes, what changed between two model versions, about inactive or bi-directional relationships, what a query does or where its SQL runs, or wants an HTML report about a model. Needs no Power BI Desktop, XMLA, gateway or Fabric access.
---

# Semantic Model Analyser

Text in, facts out. Two vendored parsers read TMDL and Power Query M, one dataset holds
everything they found, rules run over that dataset, `list` and `explain` answer
questions from it, and HTML is rendered only if asked for. The pages are a report about
the model, written in the third person; they do not address a reader or give
instructions.

    parse  ->  report.json  ->  findings  ->  HTML
                           ->  diff

## When this is the right tool

Use it when the question is about the **definition** of a model: names, folders,
descriptions, format strings, dependency chains, relationships, roles, calculation
groups, partitions and storage mode, connectors, query steps, native SQL. It runs on a
file in seconds, anywhere Python 3.9 runs, with no Desktop open.

Use something else when the question is about **data or runtime** -- row counts, refresh
duration, memory, VertiPaq compression, a measure's actual result, whether a visual uses
a measure. Nothing here has seen a row or a report. For those, connect to the model with
whatever live-model tooling is installed (for example the `power-bi-*` or
`pbi-desktop:*` skills, if present); they are alternatives for a different question,
not dependencies of this one.

## Run it

The package lives beside this file. Run it as a module from the skill directory:

```bash
cd <skill dir> && python -m sma <command> [paths] [--text ...]
```

### What it accepts

**Files and folders.** `.tmdl`, `.pq`, `.m`, `.txt`, a folder, or a PBIP project
directory. A PBIP's whole `definition` is read -- tables, relationships, model,
database, expressions, roles, cultures, perspectives -- and the input note says what was
found by kind. A single-file TMDL export with those objects inline reads the same way.

**A paste.** This is the common case, not the exotic one. If the user pasted M or TMDL
into the chat, pass it straight through rather than asking them to save a file:

```bash
python -m sma analyse --m-text "$(cat <<'M'
let Source = Sql.Database("srv", "db") in Source
M
)"
python -m sma analyse --stdin < pasted.txt      # or pass - as a path
```

`--text` decides the segment from the text; `--tmdl-text` and `--m-text` state it.

**The segment is decided by content, never by the extension** -- except that a `.tmdl`
file the TMDL parser can read is TMDL, whatever the M sniffer makes of its comments. A
semantic model saved as `.txt` is read as TMDL; a `.pq` holding nothing but a custom
function is read as M.

### Shapes of M that work

All of these, and mixing them in one run is fine:

| Shape | Looks like |
|---|---|
| One Advanced Editor copy | `let Source = ... in #"Last Step"` |
| A whole section document | `section Section1; shared Q = ...;` |
| A labelled dump | `// Query Name` above each expression |
| A custom function | `(x as text) as text => ...` -- no `let` needed |
| A parameter | `"USD" meta [IsParameterQuery=true, ...]` |
| M inside TMDL | a table's `partition ... = m` block, or a shared `expression` |

Custom functions and parameters are first-class: a `.pq` of helper functions with no
table query in it is a valid input.

Start with `inspect` when the input is unfamiliar -- it names what would be read, what
each source turned out to be, and anything it could not place.

| Command | What it does | Reach for it when |
|---|---|---|
| `inspect <paths>` | Names what would be read and which segments could be emitted. Parses nothing. | The input is unfamiliar or a path may be wrong. |
| `dataset <paths> -o report.json` | Parses and writes the whole dataset. | You want to answer several questions without re-parsing. |
| `analyse <paths>` or `analyse --report report.json` | Runs the rules and lists findings, worst first, each with its file and line. `--all` adds notes, `--limit N` caps, `--json F` writes them. | The user asked what is wrong, or what to look at. |
| `render <paths> -o dir` or `render --report report.json -o dir` | Writes the HTML pages. `--catalogue` adds the archive and the backlog. | The user asked for a report, a document, or something to send. |
| `report <paths> -o dir [--html]` | Dataset to disk, findings to the terminal, pages with `--html`. | One command, everything. |
| `diff before.json after.json [--json]` | What changed between two datasets, in model terms, with the measures downstream of every changed one. | The user asks what changed, or what a change would break. |
| `list <kind> [--where F=V ...] [--match TEXT] [--fields ...] [--json] [--count]` | Objects of one kind, filtered and projected. | Any "which" or "how many" question. |
| `explain "<name>"` | Everything the dataset holds about one measure, table, `Table[Column]`, relationship, role, expression or query. | Any question about one named thing. |

`--label "Q3 sales model"` names the run and reaches every page title. `--config
rules.json` applies thresholds, severity overrides and documented suppressions; see
*Configuring the rules* below.


## Answering a question about a model

Most questions do not need a page. Two commands answer them straight from the dataset,
and both take the same inputs as every other command (paths, a PBIP, a paste, or
`--report report.json` to skip the parse):

| Question shape | Command |
|---|---|
| Which measures / columns / tables / relationships / queries ... match a condition? | `list <kind> --where FIELD=VALUE [--where ...] [--match TEXT]` |
| What is this measure made of, what reads it, what does it read, what fires on it? | `explain "<Measure>"` |
| What is in this table: columns, storage, relationships, role filters, hierarchies? | `explain "<Table>"` |
| Who reads this column; which relationships use it; is it summarised? | `explain "Table[Column]"` |
| Is this relationship active, both directions; which measures USERELATIONSHIP it? | `explain "<Relationship name>"` |
| What does this query do: steps, sources, native SQL, what depends on it? | `explain "<Query>"` |
| What does this role filter? | `explain "<Role>"` |
| How many ...? | `list <kind> --where ... --count` |

Kinds for `list`: `measures`, `columns`, `tables`, `partitions`, `relationships`,
`roles`, `perspectives`, `expressions`, `calculation-groups`, `hierarchies`,
`unreferenced`, `functions`, `findings`, `queries`, `steps`, `sql`.

`--where` takes `=`, `!=`, `~` (contains), `>` and `<` (numbers, or the length of a
list such as `usedBy>3`), reaches nested fields with a dot (`source.file=Sales.tmdl`),
and repeats to combine. Anatomy numbers (weight, lines, calls) are on `explain`, not on
`list`. `--match TEXT`
searches the whole row, DAX and messages included. `--fields a,b,c` chooses columns,
`--json` gives rows as JSON, `--count` only the number.

Worked examples:

```bash
python -m sma list ./definition measures --where table=Sales --where formatString=        # unformatted measures in Sales
python -m sma list ./definition relationships --where isActive=false                       # inactive relationships
python -m sma list ./definition columns --where dataType=string --where summarizeBy!=none  # text columns set to summarise
python -m sma list ./definition measures --match USERELATIONSHIP                           # every measure that uses one
python -m sma list ./definition findings --where level=warning --fields code,subject,where
python -m sma list ./definition steps --where foldBreak=true                               # where folding probably stops
python -m sma list ./definition sql --fields query,step,facts                              # native SQL and what it does
python -m sma list ./definition functions                                                  # DAX functions, most-used first
python -m sma explain ./definition "Total Sales"
python -m sma explain ./definition "Sales[Amount]"
python -m sma explain ./definition "Customer" --json
```

Answer from what these print and quote the `@file:line` when pointing at something.
When a question is not covered, build the dataset once and read it in Python; its shape
is in `sma/dataset.py`. Do not answer from the HTML and do not guess at anything the
dataset does not carry (row counts, refresh, report usage).

## Reading the dataset directly

Rendering costs an HTML build; most questions do not need one. Build the dataset once
and read it:

```bash
python -m sma dataset ./definition -o /tmp/report.json
python -c "
import json; r = json.load(open('/tmp/report.json'))
t = r['tmdl']
print(t['totals'])
print(t['relationshipStats'])
print([m['name'] for m in t['measures'] if not (m.get('description') or '').strip()][:20])
"
```

The dataset is the contract, so read it directly rather than scraping the HTML. Its
shape is documented in `sma/dataset.py`; the top-level keys are `schema`, `version`,
`input`, `config`, `parts`, `help`, and then `tmdl` and/or `m`. Every list inside is
sorted by a stated key, so two runs diff cleanly. Every TMDL object has a `source` (file
and line) and every finding whose subject has one has a `where`; quote them when
pointing the user at something.

Read `tmdl.coverage` before answering a question about the whole model: it says how many
of each object kind were read, what `model.tmdl` declares that was not handed over
(`declaredNotParsed`), and what the parser saw but does not model (`unsupported`). If
the handover is partial, say so.

## What comes out

Two segments, and which ones a run emits is decided by the **input**, not by the
toolchain:

- **TMDL** -- measures, columns, tables, folders, dependency links, format strings,
  descriptions, relationships and their stats, roles, calculation groups, hierarchies,
  refresh policies, perspectives, shared expressions, the model block, the partition
  bridge with storage mode, coverage, findings and suppressed findings.
- **Power Query (M)** -- queries, custom functions, parameters, steps, layers, connectors,
  potential folding barriers, dead steps, and any native SQL found, shown as SQL.

Hand over both and you get both, as **separate pages**, never one scroll carrying both.
A model whose partitions and shared expressions carry M gets a Power Query segment read
from the model itself; `input.notes` says so and says that lines in that segment count
within the derived document. Hand over a `.pq` as well and the `.pq` wins.

Several M sources of different shapes are re-emitted as one labelled document before
being parsed, so cross-file references resolve and nothing is dropped. Where that
renames a placeholder (`Query1` becomes the file it came from) or hits a name collision,
it is recorded in `input.notes` -- read those out rather than letting them pass.

## Reading a finding

A finding names a specific subject and states an observation. `report.json` carries a
`help` map from finding code to what it means and why it might matter -- quote that
rather than inventing a rationale.

Levels are `error`, `warning`, `info`. **`error` means one thing: text the parser could
not read** (`parse-failed`, `parse-recovered`). A modelling observation is `warning` or
`info` and never `error`, because this has seen no report, no data and no meeting. Say
that when a user asks why a bi-directional relationship is "only" a warning.

The thirty rules, for orientation. Semantic model:

    parse-failed              duplicate-calculation      derived-format-mismatch
    deep-chain                time-intel-mixed           format-split-folder
    no-table-reference        undeclared-table           implicit-measure
    unreferenced-measure      unformatted-measure        text-column-summarized
    sort-by-missing           inactive-relationship      userelationship-unknown
    bidirectional-relationship  many-to-many-relationship  disconnected-table
    relationship-loop         relationship-column-missing

Power Query:

    chained-model    duplicate-source   dead-step   inline-data   native-query
    no-fold          parse-recovered    recursive   row-by-row    unresolved-reference

Three wordings to keep when relaying findings:

- **Unreferenced, not unused.** No other parsed object reads the measure. A visual
  might. Never tell the user a measure can be deleted on this evidence.
- **Potential folding barrier, not "does not fold".** `Value.NativeQuery` with
  `EnableFolding=true` is reported as the exception, at `info`.
- **A filter with two routes, not a loop.** `relationship-loop` follows the direction
  filters flow. Two facts sharing two dimensions is not reported; a bothDirections
  relationship that gives a dimension a second way to a fact is.

Missing descriptions are **not** a finding -- they are a coverage reading, in the TMDL
segment's metadata section and in `tmdl.totals.described`. A rule fires on something
specific and wrong; "nobody has written descriptions yet" is a state of the model, and
one the team already knows about.

## Configuring the rules

When the user says a pattern is intentional, record it rather than ignoring the finding:

```json
{
  "thresholds": {"deep-chain": {"warn": 10, "note": 6}},
  "severity": {"unformatted-measure": "warning"},
  "suppress": [{"code": "implicit-measure", "subject": "Sales[*]",
                "reason": "summarisation is intentional on the fact table"}]
}
```

`subject` takes `*` and `?`. A suppressed finding moves to `tmdl.suppressed` with its
reason; it is never dropped. `error` cannot be assigned to a modelling code.

## What it will not claim

Say these out loud when they bear on the answer, rather than letting the user assume
otherwise:

- **No row counts, no timings, no memory, no refresh history.** A "heaviest" measure is
  heavy in *syntax*.
- **No report usage.** Unreferenced means unreferenced by the definition.
- **No verdicts.** A calculated column is listed, not condemned. Folding is reported as
  a reason, never as a boolean.
- **No credentials, no gateway, no connection test.** A connector read off the M may
  still be unreachable. A custom connector's data-source function (`Vendor.Contents`)
  shows up as an `unresolved-reference`; say what it is.
- **Relationships as declared.** Cardinality, direction and the active flag are what the
  file says; whether the data honours them is a runtime question.

## The AI readiness page

Every run that reads TMDL also writes `ai-readiness.html`: sixteen checks in three
groups, each a pass, a fail or "not in this input".

- **Model foundations.** Relationships, a marked date table, auto date/time,
  implicit measures, hidden key, ID and relationship columns, format strings, and text
  columns that are not summarised.
- **Descriptions.** Visible tables, columns and measures.
- **Prep data for AI.** The linguistic schema, AI instructions and synonyms are read from
  the culture file. Verified answers are read from `VerifiedAnswers/definitions` beside
  `definition`, so hand over the `.SemanticModel` folder, not just `definition`. A
  service export (`fab export`) carries no verified answers; that check then says "not
  in this input", which is not a fail.

Quote the page's own status words. A pass describes the model, never a Copilot answer.

## The HTML

Self-contained: theme, script and logo inlined, **zero network requests**, so it opens
from `file://`, survives being emailed, and works offline. Light and dark both follow
the theme's tokens. Pages are `index.html` plus `semantic-model.html` and/or
`power-query.html`, connected by one navigation rail. A 1,000-measure model renders in
under two seconds; each page also carries the finder's index (about 1.4 MB at that size).

The pages behave like an app: `Ctrl K` or `/` finds any measure, table, column,
relationship, query, finding, AI check or section across all pages, and opens an object
in a side inspector (facts, code, what it reads, what reads it, its findings, file and
line). A segment page shows one section at a time (`[` `]`, `1`-`9`, `A` for all); `?`
lists the keys. Every section and card has a stable anchor (`#t-rel`, `#card-t14`), so a
link to `semantic-model.html#card-t14?pick=Total%20Sales` opens that measure's anatomy.
When pointing someone at a reading, give that link rather than "scroll to T14".

Do not hand-edit the output. Change the source and re-render.

## Practical notes

- Stdlib only. No pip install, no third-party package, Python 3.9+.
- The parsers live in `sma/vendor/`; their own suites ship under `tests/parsers/`.
- Two measures of the same name in one parse share an id (TMDL keeps names unique
  model-wide, so this only happens when exports are concatenated). It is resolved once
  at parse time and reported in `tmdl.merged`.
- A measure whose DAX did not come through the paste is a `parse-failed` error with its
  file and line; it does not invalidate the rest of the read.
