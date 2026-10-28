# Changelog

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
