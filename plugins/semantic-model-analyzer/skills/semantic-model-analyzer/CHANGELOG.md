# Changelog

## 1.0.1 (29 October 2026)

Changes from the first reviews of 1.0.0. Thank you to everyone who tested it.

**Renamed**

- The skill is now Semantic Model Analyzer, `semantic-model-analyzer` (it was
  `semantic-model-analyser`). Anyone who installed the old name installs the new one.
- `report.json` files written by 1.0.0 still load, and `analyze` is accepted as a spelling
  of the `analyse` command.

**Fixed**

- A Windows console on code page 1252 no longer ends the run with exit 1 on a measure
  named with a character it cannot encode, such as a Greek delta. Output is UTF-8.
- `inspect` shows `tmdl` as the format of a TMDL file, not the Power Query sniffer's guess.
- A `functions.tmdl` file now says DAX user-defined functions are not read yet, instead of
  the Power Query parser's "expected Power Query M".
- The `diff` header names each version (its `--label`, or the model it was read from)
  before the time it was generated.

**Skill**

- The description says only when to use the skill, at about a third of its old length.
- The reply fits the question: a specific question gets its answer with file and line;
  the fix-first list and the counts line are for a review or audit.
- Rules on credentials, writes, access, model text and outputs are stated once, in a
  Rules section, and referred to by number everywhere else.
- A Known limits section: Power Query steps named like TMDL keywords, DAX user-defined
  functions, and what is not read at all, each with what to do.
- "Do not use when" names where to go next: the Tabular Editor CLI for model edits,
  validation and Best Practice Analyzer runs, and a PBIR tool for reports.
- The scratch folder is written out by its full path in every command, because a shell
  variable does not survive between an agent's tool calls; an empty one could have put the
  dataset at the drive root. Cleanup has its own command.
- A Copilot readiness question has a route: the dataset's `tmdl.aiReadiness`, with a
  snippet in `references/commands.md`.

**Packaging**

- The skill ships only what runs. The tests moved out of the plugin to
  `tests/semantic-model-analyzer/` at the root of the repository, where no install picks
  them up, and the contributor guide and the folder's git settings left it. Every path is
  now short enough for a Windows clone into a deep folder.

## 1.0.0 (28 October 2026)

First public release. Reads a Power BI semantic model or its Power Query as text and
reports on it. No Power BI Desktop, no XMLA connection, no gateway, stdlib Python only.

**Reads**

- TMDL (one file, a pasted table, or a whole PBIP `definition` folder) and Power Query M
  (a section document, a query dump, or one `let ... in`).
- Tables, columns, measures, relationships, partitions and storage mode, roles,
  calculation groups, hierarchies, perspectives, shared expressions, query steps,
  connectors and native SQL, with file and line for every object.

**Finds**

- Rules over the model and the queries, each naming one subject, at `warning` or
  `info`. `error` means only that the text could not be read.
- An AI readiness checklist: sixteen checks across model foundations, descriptions and
  Prep data for AI.

**Shows**

- A landing page: stat cards, what stands out, a model health checklist, data lineage
  from source to measure, and measure lineage.
- One page each for the semantic model and Power Query, one section at a time, plus
  AI readiness and references.
- `Ctrl K` finds any object, finding or section on any page and opens it in a side
  inspector. Every page works offline from `file://` and makes no network requests.

**Commands**

- `inspect`, `report`, `dataset`, `analyse`, `render`, `list`, `explain` and `diff`. `--config`
  sets thresholds, severity overrides and documented suppressions.
- Every command that parses stops on credential-looking input and names only the file and
  line; `inspect` shows the same and carries on.
