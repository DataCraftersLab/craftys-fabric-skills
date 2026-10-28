# The HTML pages

Read this when the user asks for the report, or asks where something is in it.

## What `render` writes

`sma render PATHS -o DIR` writes up to five pages into `DIR`, replacing files of the same name:

| Page | Written when | What it holds |
|---|---|---|
| `index.html` | always | The landing page: stat cards, what stands out (up to four sentences), a model health checklist rolling up both scorecards and the AI checks, a data lineage graph (source to table to measure) and a measure lineage graph (tables to measures to the measures built on them), then findings and go-to links. Open with it when showing someone a model. |
| `semantic-model.html` | the input has TMDL | Every TMDL reading, one section at a time. |
| `power-query.html` | the input has M, or the model's partitions carry M | Queries, steps, connectors, folding, native SQL. |
| `ai-readiness.html` | the input has TMDL | Sixteen checks in three groups. |
| `references.html` | always | The people and documentation behind the analyser. |

The pages are self-contained: theme, script and logo inlined, zero network requests. They open
from `file://`, survive being emailed and work offline, in light and dark. A 1,000-measure model
renders in under two seconds. Page size grows with the model: `semantic-model.html` was about
2.5 MB at that size, most of it the finder's index.

The pages are a report about the model, in the third person. They carry the model's names, DAX,
M, server and database names and native SQL, so they have the model's classification.

## The lineage graphs

- **Data lineage**: sources on the left, tables, then measures. Auto date/time tables collapse
  into one flagged node; a measure table folds into its measures node; a table nothing uses is
  flagged. Hover traces a path, click pins it, double-click or Enter opens the inspector.
- **Measure lineage**: tables, then measures, then the measures built on them, coloured by
  display folder, capped at 150 measures with their dependencies kept whole.

## Moving around

The pages behave like an app:

- `Ctrl K` or `/` finds any measure, table, column, relationship, query, finding, AI check or
  section across all pages, and opens an object in a side inspector: facts, code, what it
  reads, what reads it, its findings, file and line.
- A segment page shows one section at a time: `[` and `]` step, `1` to `9` jump, `A` shows all.
  `?` lists the keys. Print shows everything.
- Every section and card has a stable anchor (`#t-rel`, `#card-t14`), and
  `semantic-model.html#card-t14?pick=Total%20Sales` opens that measure's anatomy.
  `#obj=m:Total%20Sales` opens a measure in the inspector. When pointing someone at a reading,
  give the link rather than "scroll to T14".

## The AI readiness page

Sixteen checks in three groups, each a pass, a fail or "not in this input":

- **Model foundations.** Relationships, a marked date table, auto date/time, implicit measures,
  hidden key, ID and relationship columns, format strings, and text columns that are not
  summarised.
- **Descriptions.** Visible tables, columns and measures.
- **Prep data for AI.** The linguistic schema, AI instructions and synonyms are read from the
  culture file. Verified answers are read from `VerifiedAnswers/definitions` beside
  `definition`, whether the user named the `.SemanticModel` folder or the `definition` folder
  inside it; they are counted with their trigger phrases, never copied. A service export
  (`fab export`) carries no verified answers; that check then says "not in this input", which is
  not a fail.

Quote the page's own status words. A pass describes the model, never a Copilot answer.

## Outputs are not edited by hand

Change the input and render again. A hand-edited page drifts from the dataset it claims to
describe.
