# Commands and inputs

Read this when a question needs a flag, a `list` kind or an input shape that SKILL.md does not
spell out.

Contents: Running the engine · The credential screen · What it accepts · Shapes of M · The
commands · Answering a question · Reading the dataset directly · What comes out · Practical notes

## Running the engine

The `sma` package sits in the skill folder (`SKILL_DIR`). Put that folder on the module path and
run from the user's working folder, so the paths they give resolve as they wrote them:

```bash
PYTHONPATH="SKILL_DIR" python -B -m sma COMMAND [PATHS] [options]           # Bash (python3 on macOS/Linux)
$env:PYTHONPATH = "SKILL_DIR"; python -B -m sma COMMAND [PATHS] [options]   # PowerShell
```

`-B` stops Python writing bytecode into the skill folder. Below, `sma` stands for that prefix.
`sma --version` prints the engine version.
`analyze` is accepted as a spelling of `analyse`.

A scratch folder for a dataset that answers several questions, fresh per run so parallel
sessions never share a file, and deleted when the task ends. Create it, note the full path it
prints, and write that path out in every later command; below it is shown as `SCRATCH`. A shell
variable does not survive from one tool call to the next.

```bash
python -c "import tempfile; print(tempfile.mkdtemp(prefix='sma-'))"           # create
python -c "import shutil, sys; shutil.rmtree(sys.argv[1])" "SCRATCH"          # delete
```

## The credential screen

Every command that parses (`dataset`, `analyse`, `list`, `explain`, `render`, `report`) screens
the input first. On credential-looking text it parses and writes nothing, and exits 2 with:

```
input error: possible credential in the input, so nothing was parsed or written:
  finance-queries.pq:4  password in a connection string
```

It lists every match by source, line and kind, never the value; a file name shared by two
inputs is given with its folder. The kinds: a password in a connection string, an account or
access key, a shared access signature, a client secret, a bearer token, basic authentication, an
API or subscription key, a token in a URL, a token or secret literal, a vendor token (GitHub,
OpenAI, AWS, Slack), a JSON web token, a private key. `inspect` prints one line per match
(`screen    : possible credential at FILE:LINE (kind)`), or `screen    : clean (N sources
searched)`, and carries on. `--no-credential-check` skips the screen; pass it only after the user
has looked at every line listed and said none is a credential. The screen is a net, not a proof:
a clean result means none of those shapes was found.

## What it accepts

**Files and folders.** `.tmdl`, `.pq`, `.m`, `.txt`, a folder, or a PBIP or `.SemanticModel`
folder. A PBIP's whole `definition` is read (tables, relationships, model, database,
expressions, roles, cultures, perspectives) and the input note says what was found by kind. A
single-file TMDL export with those objects inline reads the same way.

**A paste.** This is the common case, not the exotic one. If the user pasted M or TMDL into the
chat, pass it straight through rather than asking them to save a file:

```bash
sma analyse --m-text "$(cat <<'M'
let Source = Sql.Database("srv", "db") in Source
M
)"
sma analyse --stdin < pasted.txt      # or pass - as a path
```

`--text` decides the segment from the text; `--tmdl-text` and `--m-text` state it.

**The segment is decided by content, never by the extension**, except that a `.tmdl` file the
TMDL parser can read is TMDL, whatever the M sniffer makes of its comments. A semantic model
saved as `.txt` is read as TMDL; a `.pq` holding nothing but a custom function is read as M.

## Shapes of M

All of these work, and mixing them in one run is fine:

| Shape | Looks like |
|---|---|
| One Advanced Editor copy | `let Source = ... in #"Last Step"` |
| A whole section document | `section Section1; shared Q = ...;` |
| A labelled dump | `// Query Name` above each expression |
| A custom function | `(x as text) as text => ...` (no `let` needed) |
| A parameter | `"USD" meta [IsParameterQuery=true, ...]` |
| M inside TMDL | a table's `partition ... = m` block, or a shared `expression` |

Custom functions and parameters are first-class: a `.pq` of helper functions with no table
query in it is a valid input.

## The commands

| Command | What it does | Reach for it when |
|---|---|---|
| `inspect PATHS` | Names what would be read and which segments could be emitted. Parses nothing. | The input is unfamiliar or a path may be wrong. |
| `dataset PATHS -o report.json` | Parses and writes the whole dataset. | Several questions, one parse. |
| `analyse PATHS` or `analyse --report report.json` | Runs the rules and lists findings, worst first, each with its file and line. `--all` adds notes, `--limit N` caps, `--json F` writes them. | What is wrong, or what to look at. |
| `render PATHS -o DIR` or `render --report report.json -o DIR` | Writes the HTML pages. `--catalogue` adds the archive and the backlog. | A report, a document, something to walk someone through. |
| `report PATHS -o DIR [--html]` | Dataset to disk, findings to the terminal, pages with `--html`. | One command, everything. |
| `diff before.json after.json [--json F]` | What changed between two datasets, in model terms, with the measures downstream of every changed one. | What changed, or what a change would break. |
| `list [PATHS] KIND [--where F=V ...] [--match TEXT] [--fields a,b] [--json] [--count]` | Objects of one kind, filtered and projected. | Any "which" or "how many" question. |
| `explain [PATHS] "Name"` | Everything the dataset holds about one measure, table, `Table[Column]`, relationship, role, expression or query. | Any question about one named thing. |

Always give `-o` explicitly: `render` defaults to the current folder, `dataset` to
`./report.json` and `report` to `./model-report`; give an absolute path so the `wrote` lines
are full paths. `--label "Q3 sales model"` names the run and reaches every page title. The label
is fixed when the input is parsed, so with `--report` set it on the `dataset` command that built
that file. `--config rules.json` applies thresholds, severity overrides and documented
suppressions (`references/findings.md`).

## Answering a question

Most questions do not need a page. `list` and `explain` answer straight from the dataset and
take the same inputs as every other command (paths, a PBIP, a paste, or `--report report.json`
to skip the parse):

| Question shape | Command |
|---|---|
| Which measures / columns / tables / relationships / queries match a condition? | `list PATHS KIND --where FIELD=VALUE [--where ...] [--match TEXT]` |
| What is this measure made of, what reads it, what does it read, what fires on it? | `explain PATHS "Measure"` |
| What is in this table: columns, storage, relationships, role filters, hierarchies? | `explain PATHS "Table"` |
| Who reads this column, which relationships use it, is it summarised? | `explain PATHS "Table[Column]"` |
| Is this relationship active, both directions; which measures USERELATIONSHIP it? | `explain PATHS "Relationship name"` |
| What does this query do: steps, sources, native SQL, what depends on it? | `explain PATHS "Query"` |
| What does this role filter? | `explain PATHS "Role"` |
| How many ...? | `list PATHS KIND --where ... --count` |

The path comes before the kind (`list ./definition measures`), or use `--report report.json`
instead of a path. Kinds for `list`: `measures`, `columns`, `tables`, `partitions`, `relationships`, `roles`,
`perspectives`, `expressions`, `calculation-groups`, `hierarchies`, `unreferenced`,
`functions`, `findings`, `queries`, `steps`, `sql`.

`--where` takes `=`, `!=`, `~` (contains), `>` and `<` (numbers, or the length of a list such as
`usedBy>3`), reaches nested fields with a dot (`source.file=Sales.tmdl`), and repeats to
combine. Anatomy numbers (weight, lines, calls) are on `explain`, not on `list`. `--match TEXT`
is a case-insensitive substring search over the whole row, DAX and messages included; it does
not find near spellings, so to browse names use `--fields name --all`. `--fields a,b,c` chooses
columns, `--json` gives rows as JSON, `--count` only the number, `--all` lifts the row limit,
`--wide` stops long cells being shortened. `analyse` shortens subjects and messages to fit a
terminal; `list ... findings --all --wide` gives them whole.

Worked examples:

```bash
sma list ./definition measures --where table=Sales --where formatString=         # unformatted measures in Sales
sma list ./definition relationships --where isActive=false                       # inactive relationships
sma list ./definition columns --where dataType=string --where summarizeBy!=none  # text columns set to summarise
sma list ./definition measures --match USERELATIONSHIP                           # every measure that uses one
sma list ./definition findings --where level=warning --fields code,subject,where
sma list ./definition steps --where foldBreak=true                               # where folding probably stops
sma list ./definition sql --fields query,step,facts                              # native SQL and what it does
sma list ./definition functions                                                  # DAX functions, most-used first
sma explain ./definition "Total Sales"
sma explain ./definition "Sales[Amount]"
sma explain ./definition "Customer" --json
```

Answer from what these print and quote the `File.tmdl:line` when pointing at something. When a
question is not covered, build the dataset once and read it in Python. Do not answer from the
HTML, and do not guess at anything the dataset does not carry (row counts, refresh, report
usage).

## Reading the dataset directly

```bash
sma dataset ./definition -o "SCRATCH/report.json" -q
python -c "
import json, sys; r = json.load(open(sys.argv[1], encoding='utf-8'))
t = r['tmdl']
print(t['totals'])
print(t['relationshipStats'])
print([m['name'] for m in t['measures'] if not (m.get('description') or '').strip()][:20])
" "SCRATCH/report.json"
```

How ready the model is for Copilot or a data agent is in `tmdl.aiReadiness`: a `summary`
(passed, failed and unknown out of sixteen) and `groups`, each holding `checks` with a
`label`, a `status` and a `detail`. The status is `pass`, `fail` or `na`, which means the
input cannot show it (counted as `unknown` in the summary, and "not in this input" is not a
fail):

```bash
python -c "
import json, sys; a = json.load(open(sys.argv[1], encoding='utf-8'))['tmdl']['aiReadiness']
print(a['summary'])
for g in a['groups']:
    for c in g['checks']: print(c['status'], '|', c['label'], '|', c['detail'])
" "SCRATCH/report.json"
```

What a finding code means, and why it might matter, is in the `help` map (code to text). Quote
it rather than inventing a rationale:

```bash
python -c "
import json, sys; h = json.load(open(sys.argv[1], encoding='utf-8'))['help']
for code in sys.argv[2:]: print(code + ': ' + h.get(code, '(no such code)'))
" "SCRATCH/report.json" parse-failed relationship-loop
```

The dataset is the contract, so read it rather than scraping the HTML. Its shape is documented
in `sma/dataset.py`; the top-level keys are `schema`, `version`, `generatedAt`, `label`, `input`,
`config`, `parts`, `help`, then `tmdl` and/or `m`. Every list inside is sorted by a stated key,
so two runs diff cleanly. Every TMDL object has a `source` (file and line), and every finding
whose subject has one has a `where`. It holds the whole model and the absolute paths it was read
from, so it is as confidential as the model; delete a scratch copy when the task ends.

Read `tmdl.coverage` before answering about the whole model: how many of each object kind were
read, what `model.tmdl` declares that was not handed over (`declaredNotParsed`), and what the
parser saw but does not model (`unsupported`).

## What comes out

Two segments, and which ones a run emits is decided by the input:

- **TMDL**: measures, columns, tables, folders, dependency links, format strings, descriptions,
  relationships and their stats, roles, calculation groups, hierarchies, refresh policies,
  perspectives, shared expressions, the model block, the partition bridge with storage mode,
  coverage, findings and suppressed findings.
- **Power Query (M)**: queries, custom functions, parameters, steps, layers, connectors,
  potential folding barriers, dead steps, and any native SQL found, shown as SQL.

Hand over both and you get both, as separate pages. A model whose partitions and shared
expressions carry M gets a Power Query segment read from the model itself; `input.notes` says
so, and lines in that segment count within the derived document. Hand over a `.pq` as well and
the `.pq` wins.

Several M sources of different shapes are re-emitted as one labelled document before being
parsed, so cross-file references resolve and nothing is dropped. Where that renames a
placeholder (`Query1` becomes the file it came from) or hits a name collision, it is recorded in
`input.notes`; read those out rather than letting them pass.

## Practical notes

- Standard library only, Python 3.9 or newer, nothing to install.
- The parsers live in `sma/vendor/`.
- Two measures of the same name in one parse share an id (TMDL keeps names unique model-wide,
  so this only happens when exports are concatenated). It is resolved once at parse time and
  reported in `tmdl.merged`.
- A measure whose DAX did not come through the paste is a `parse-failed` error with its file and
  line; it does not invalidate the rest of the read.
- A custom connector's data-source function (`Vendor.Contents`) shows up as an
  `unresolved-reference`; say what it is.
