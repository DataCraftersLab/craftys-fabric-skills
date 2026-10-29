# Semantic Model Analyzer

Read a Power BI model as text and say what is in it.

No Power BI Desktop. No XMLA connection. No gateway. No Fabric access. No `pip install`.
Hand it TMDL, Power Query M, or both — as files, as a folder, as a PBIP project, or as a
paste straight out of the Power Query window — and it tells you what the definition
contains: tables, columns, measures, display folders, format strings, descriptions, DAX
dependency chains, relationships, roles, calculation groups, hierarchies, partitions and
their storage mode, shared expressions, connectors, query steps, custom functions,
parameters, and any native SQL it finds.

```
parse  →  report.json  →  findings  →  HTML
                      ↘  diff
```

Every stage can be stopped at, because the cheap ones answer most questions. "How many
measures have no description" should cost a dataset read, not an HTML build.

## How to use

**With Claude.** Install the skill (below), then ask in plain words and name the model:

- "Review the model in C:\Work\Contoso\Sales.SemanticModel before it goes to the client and
  tell me what to fix first."
- "Here's the TMDL for our Sales table. Which measures have no format string, and what breaks
  if I change [Total Sales]?"
- "These are last month's and this month's PBIP folders. What changed in the model?"
- "Make me the HTML report for this model so I can walk the client through the lineage."

Claude runs the engine, answers with the file and line of everything it names, and writes the
pages only when asked, to a folder you choose. It never edits the model, never connects to a
service, and stops if the input holds something that looks like a credential.

**On the command line.** Python 3.9 or later, nothing to install. Run from your own folder with
the skill folder on the module path (`-B` keeps bytecode out of the skill folder):

```bash
export PYTHONPATH=/path/to/semantic-model-analyzer
python -B -m sma inspect ./MyModel.SemanticModel                     # what would be read
python -B -m sma analyse ./MyModel.SemanticModel                     # what the rules found
python -B -m sma report  ./MyModel.SemanticModel -o ./out --html     # everything
```

The last one writes `out/report.json` and a set of self-contained HTML pages. Open
`out/index.html` — it needs no server and makes no network requests.

## The commands

| Command | Does |
|---|---|
| `inspect <paths>` | Names what would be read and which segments could be emitted. Parses nothing. |
| `dataset <paths> -o report.json` | Parses and writes the whole dataset. |
| `analyse <paths>` | Runs the rules and lists findings, worst first. `--all`, `--limit N`, `--json F`. |
| `render <paths> -o dir` | Writes the HTML. `--catalogue` adds the archive and the backlog. |
| `report <paths> -o dir` | Dataset, findings, and pages with `--html`. |
| `diff before.json after.json` | Two datasets compared in model terms. `--json` for the machine form. |
| `list <kind>` | Objects of one kind, filtered with `--where` and `--match`, projected with `--fields`; `--json`, `--count`. |
| `explain "<name>"` | Everything known about one measure, table, `Table[Column]`, relationship, role, expression or query. |

Every command that parses first screens the input for credential-looking text (a password
in a connection string, an account key, a SAS signature, a client secret, a bearer token, an
API key, a token literal, a JSON web token, a private key). On a match it writes nothing and
exits 2, naming the file and line but never the value; `inspect` shows the same and carries on.
`--no-credential-check` skips it once someone has looked and confirmed nothing listed is a
credential.

`analyse` and `render` also take `--report report.json` instead of paths, so you can
re-analyse or re-render without re-parsing. `--label "Q3 sales model"` names the run.
`--config rules.json` on any of them applies thresholds, severity overrides and
documented suppressions (see *Configuring the rules*).

## What it reads

**Files and folders.** `.tmdl`, `.pq`, `.m`, `.txt`, a folder, or a PBIP project
directory. A PBIP's `definition` is read whole — tables, `relationships.tmdl`,
`model.tmdl`, `database.tmdl`, `expressions.tmdl`, roles, cultures, perspectives — and
the input note says what was found by kind. A single-file TMDL export with its
relationships, roles and expressions inline reads the same way.

**A paste.** A copy out of the Power Query window is a first-class input:

```bash
python -B -m sma analyse --stdin < pasted.txt
python -B -m sma analyse --m-text '(x as text) as text => Text.Upper(x)'
cat model.tmdl | python -B -m sma report - -o out --html
```

`--text` decides the segment from the text; `--tmdl-text` and `--m-text` state it.

**The extension does not decide, with one exception.** A semantic model saved as `.txt`
is read as TMDL; a `.pq` holding one custom function is read as M. Both parsers are
asked. The exception is a `.tmdl` file the TMDL parser can read: it is TMDL whatever the
M sniffer makes of its text, because a calculation-group file opening with a dozen
`///` comment lines looks exactly like a labelled Power Query dump.

Every shape of M works, and mixing them in one run is fine: an Advanced Editor copy, a
whole `section` document, a labelled dump, a bare custom function with or without a
return-type ascription, a single parameter, or the M inside a TMDL partition.

Which segments a run emits is decided by the **input**, not by the toolchain. Hand over
TMDL and you get a semantic-model page; hand over M and you get a Power Query page; hand
over both and you get both, as separate pages, never one scroll carrying both. A model
whose partitions and shared expressions carry Power Query gets a Power Query page too,
read from the model itself; the input note says so, and says that line numbers in that
part count within the derived document rather than a file. Hand over a `.pq` as well
and the `.pq` wins, because an export is the fuller text of the same queries.

## Asking questions

Most questions about a model do not need a page. `list` answers "which" and "how many";
`explain` answers "what is this one thing". Both read the dataset, and both take the same
inputs as every other command, or `--report report.json` to skip the parse.

```bash
python -B -m sma list ./definition measures --where table=Sales --where formatString=
python -B -m sma list ./definition relationships --where isActive=false
python -B -m sma list ./definition columns --where dataType=string --where summarizeBy!=none
python -B -m sma list ./definition measures --match USERELATIONSHIP --fields name,table,source
python -B -m sma list ./definition findings --where level=warning --count
python -B -m sma list ./definition steps --where foldBreak=true
python -B -m sma explain ./definition "Total Sales"
python -B -m sma explain ./definition "Sales[Amount]"
python -B -m sma explain ./definition "Shared Staging" --json
```

Kinds: `measures`, `columns`, `tables`, `partitions`, `relationships`, `roles`,
`perspectives`, `expressions`, `calculation-groups`, `hierarchies`, `unreferenced`,
`functions`, `findings`, `queries`, `steps`, `sql`. `--where` takes `=`, `!=`, `~`
(contains), `>` and `<`, reaches nested fields with a dot (`source.file=Sales.tmdl`) and
repeats to combine. `explain` on a measure gives its DAX, the anatomy gauges, the
functions and the columns it reads, the measures above and below it transitively, what
else references it, and every finding on it; on a table, its columns, storage,
relationships, hierarchies, calculation items and role filters; on a query, its steps,
sources, native SQL and dependencies.

## The dataset is the contract

Everything after the parse reads `report.json` and nothing else. That buys three things:

- **The analysis can be read without rendering.** A dataset instead of an HTML build.
- **The renderer cannot disagree with the analysis.** A chart and a table drawn from one
  list say the same thing by construction. (The one place the renderer used to re-derive
  a fact is gone; a review caught it disagreeing with the dataset on `FETCH FIRST`.)
- **It is testable and diffable.** Key order is fixed and every list is sorted by a
  stated key, so a golden-file diff of two runs is meaningful rather than flaky, and
  `sma diff` can say what changed between two handovers in model terms.

Its shape is documented in `sma/dataset.py` (schema version 2). Top-level keys: `schema`,
`version`, `generatedAt`, `label`, `input`, `config`, `parts`, `help`, then `tmdl` and/or `m`. The `tmdl` part
carries `measures`, `columns`, `tables`, `relationships`, `relationshipStats`, `model`,
`calculationGroups`, `hierarchies`, `refreshPolicies`, `roles`, `perspectives`,
`expressions`, `bridge`, `unreferenced`, `coverage`, `findings`, `suppressed`, `totals`.
Every TMDL object carries `source` — the file and line it was declared on — and every
finding whose subject has one carries `where`.

```bash
python -B -m sma dataset ./definition -o report.json
python - <<'PY'
import json; t = json.load(open("report.json"))["tmdl"]
print(t["totals"])
print(t["coverage"]["objects"], t["coverage"]["declaredNotParsed"])
print([m["name"] for m in t["measures"] if not (m.get("description") or "").strip()])
PY
```

`coverage` is the honesty block: how many of each object kind were read, what
`model.tmdl` declares that the handover did not contain, and what the parser saw and
does not model (a culture, a query group).

## Findings

A finding names a **specific subject** and states an observation. Thirty rules — twenty
over the semantic model, ten over Power Query — each with a help line in `report.json`
under `help`.

Levels are `error`, `warning` and `info`, and `error` is carried by exactly one kind of
finding: text the parser could not read (`parse-failed` for a measure whose DAX did not
come through, `parse-recovered` for a query the M parser had to skip part of). A
modelling observation is `warning` or `info`, never `error` — this has seen no report, no
data and no meeting, so it is not entitled to that word — and a test refuses `error` on
any other code, including through configuration.

Semantic model:

```
parse-failed                 duplicate-calculation        derived-format-mismatch
deep-chain                   time-intel-mixed             format-split-folder
no-table-reference           undeclared-table             implicit-measure
unreferenced-measure         unformatted-measure          text-column-summarized
sort-by-missing              inactive-relationship        userelationship-unknown
bidirectional-relationship   many-to-many-relationship    disconnected-table
relationship-loop            relationship-column-missing
```

Power Query:

```
chained-model     duplicate-source   dead-step    inline-data    native-query
no-fold           parse-recovered    recursive    row-by-row     unresolved-reference
```

Three wordings are deliberate. *Unreferenced*, not unused: no other parsed object reads
the measure, and whether a visual does is not knowable from the definition. *Potential
folding barrier*, not "does not fold": a `Value.NativeQuery` can keep folding with
`EnableFolding=true`, which is read off the step and reported as the exception, and the
pages say "no barrier detected" rather than "folds". *A filter with two routes*, not a
loop: the relationship rule follows the direction filters flow, so two facts sharing two
dimensions is not reported, and a bothDirections relationship that gives a dimension a
second way to a fact is.

Two rules that were written and then deleted, because they are the point:

- *hidden measure nothing reads* — 66 hits on a real model. That is an architecture, not
  a finding.
- *divide without DIVIDE* — 13 hits, every one of them a `/` inside a text format string.

A count is not a finding. If a rule cannot name what is wrong and where, it does not
ship.

## Configuring the rules

```json
{
  "thresholds": {"deep-chain": {"warn": 10, "note": 6}},
  "severity": {"unformatted-measure": "warning", "parse-failed": "info"},
  "suppress": [
    {"code": "implicit-measure", "subject": "Sales[*]",
     "reason": "summarisation is intentional on the fact table"}
  ]
}
```

`subject` takes `*` and `?` wildcards and is matched case-insensitively. A suppressed
finding is not dropped: it moves to `tmdl.suppressed` (or `m.suppressed`) with the rule
and reason that suppressed it, because a finding that silently stops appearing is the
one nobody can audit. `report.json` records the configuration under `config`.

## What it will not claim

- **No row counts, no timings, no memory, no refresh history.** Nothing here has seen the
  data. A "heaviest" measure is heavy in *syntax*, and the output keeps saying so.
- **No report usage.** Unreferenced means unreferenced by the definition.
- **No verdicts.** A calculated column is listed, not condemned. Folding is reported as a
  *reason*, because a modeller can judge a reason and cannot judge a boolean.
- **No credentials, no gateway, no connection test.** A connector read off the M may still
  be unreachable, and a custom connector's data-source function (`Vendor.Contents`) is
  reported as an unresolved reference, which is true and not very helpful.
- **Relationships as declared.** Cardinality, direction and the active flag are what the
  file says; whether the data honours them is a runtime question.

For anything on that list, connect to the model instead.

## Known limits

- **A Power Query step named like a TMDL keyword** (lower-case `database`, `model`,
  `table`, `column`, `partition`, `role` and the like) cuts that expression short when it
  is read from a `.tmdl` file. The Direct Lake template `let database = Sql.Database(...)
  in database` is the usual case. The rest of the model reads normally; to read that
  expression, paste its M with `--m-text`.
- **DAX user-defined functions** (`functions.tmdl`) are not read yet. `inspect` says so.
- **Not read**: `.pbix`, `.zip`, `model.bim` and a PBIP's `.Report` folder. Save the model
  as a PBIP with TMDL, or export TMDL.

## The HTML

The pages are a report about the model. They describe what was read and what it means,
in the third person; they do not address a reader, and the interactive parts (the chain
explorer, the findings table, the anatomy rail) carry no instructions.

Theme, script and logo are inlined and the pages make **zero network requests**, so they
open from `file://`, survive being emailed, and still work in five years. Light and dark
both come from the theme's tokens — a hardcoded hex would survive a theme toggle and go
invisible in one of the two, which is why every release is checked in a real browser in
both themes rather than trusted from the source.

Every page says what it is about, under its heading: the PBIP project, the TMDL file or
the Power Query file, with its path, or the `--label` when one was given.

`index.html` is the landing page: stat cards, what stands out, a model health checklist, a
data lineage graph from source to measure and a measure lineage graph. `semantic-model.html`
and `power-query.html` show one section at a time; `ai-readiness.html` holds sixteen checks;
`references.html` lists the people and documentation behind the analyzer. `Ctrl K` finds any
object, finding or section across the pages and opens it in a side inspector.

Measured at 1,000 measures over 20 tables and 30 relationships: the full report builds
in under two seconds and every browser check passes. Page size grows with the model;
`semantic-model.html` was about 2.5 MB at that size, most of it the finder's index.

Do not hand-edit the output. Change the source and re-render.

## Install

In Claude Code:

```
/plugin install semantic-model-analyzer --marketplace DataCraftersLab/craftys-fabric-skills
```

For any other agent that reads the Agent Skills format, copy the whole folder into its skills directory:

```bash
cp -r semantic-model-analyzer <skills directory>/semantic-model-analyzer
```

`SKILL.md` at the root is the entry point — its frontmatter tells Claude when to reach
for it, and the body tells Claude how to run it and what it may not claim. The detail it
loads on demand is in `references/`.

## Layout

```
SKILL.md                  the Claude Code entry point
references/               commands, findings and page guides SKILL.md loads on demand
sma/
  cli.py                  inspect | dataset | analyse | render | report | diff | list | explain
  inputs.py               what to read, and which segments that supports
  screen.py               the credential screen every parsing command runs first
  dataset.py              report.json -- the contract
  ask.py                  list and explain
  compare.py              diff of two datasets
  findings.py             the help behind each code, levels, the M post-pass
  tmdl_findings.py        the TMDL rules themselves
  readiness.py            the AI readiness checks
  dax_format.py           VBA format masks -> the Format dropdown word and its toggles
  render/                 the pages (cards.py, the finder index in app.py, the landing
                          page and lineage in overview.py) and the assets they inline
  vendor/                 the TMDL and M parsers
```

The skill holds only what runs. Its tests, the parser suites and the browser checks are
run before each release and are not part of the installed skill.

## The parsers

`sma/vendor/tmdl_parser.py` and `sma/vendor/m_parser.py` are the two parsers. The skill
reads no code or assets from outside its own folder. It reads the input it is given and,
for a named `definition` folder, the `VerifiedAnswers` folder beside it.

## Licence

Apache-2.0. See the LICENSE file at the root of this repository.
