# Changelog

## 0.4.0 — unreleased

The app release. Reviewers liked the visuals but had to scroll a 15,000 px page to find
a section, and found too much text in front of the readings. The data, the rules and the
cards are unchanged; how a reader reaches them is not.

**Added**

- A finder in the header (`Ctrl K` or `/`) over every measure, table, column,
  relationship, query, role, calculation group, hierarchy, finding, AI check, section and
  card on every page, filtered by kind (`Tab`). Its last row searches the page text.
- An inspector: an object opens in a side panel with its facts, DAX or M, what it reads,
  what reads it, its findings with the rule's reason, and its file and line. References
  open in place, with a back stack; "show on page" buttons jump to its card, and a measure
  opens selected in T14.
- Segment pages show one section at a time from a sticky tab strip; `[` `]`, `1`-`9` and
  the rail switch sections, `A` or "Show all" restores the full document, which is also
  what prints. Browser find-in-page reveals a hidden section where the browser supports
  `hidden="until-found"`.
- The overview is a dashboard: count tiles that browse each kind, the findings to look at
  first by level, the AI readiness score, and a jump grid to every section.
- The AI readiness page filters by status.
- Stable anchors on every card (`#card-t7`) and deep links that select a measure
  (`#card-t14?pick=Name`) or open the inspector (`#obj=m:Name`).
- Skip link, ARIA tabs, combobox and listbox for the finder, a live result count, focus
  returned on close, a keyboard list (`?`), reduced-motion support.
- `sma/render/app.py` builds the index from the dataset; `sma-app.js` and `sma-app.css`
  are bundle-owned assets layered over the mirrored theme, which is untouched.
  `tests/test_app.py`; `validate_html.py` now probes every page with all sections shown.

**Changed**

- Text cut back. Page ledes are one line; method notes inside cards are folded under
  "How to read this"; segment notes, parse lines and caveats moved to an About section;
  the overview's rules and run modes are collapsed. Section titles lose the `T ·` prefix.
- The parse line no longer says "over no input" when the Power Query part is derived from
  the model's partitions.

## 0.3.0-session (12 October 2026)

Session copy for AI with Power BI. Adds the AI readiness checklist.

**Added**

- `ai-readiness.html`, last in the rail on every run that reads TMDL. Sixteen checks in
  three groups (model foundations, descriptions, Prep data for AI), each a pass, a fail
  or "not in this input", with counts, plus a short "Why We're Doing This" section.
- `sma/readiness.py` builds `tmdl.aiReadiness` in the dataset. It reads the culture
  file's linguistic metadata (AI instructions, entities, synonyms) and a PBIP's
  `VerifiedAnswers/definitions` folder beside `definition`.
- The overview links to the new page. `tests/test_readiness.py` covers the statuses.

**Known gap**

- The AI data schema is saved in the linguistic schema under a key not yet confirmed, so
  that check reports "not in this input" until it is.

## 0.2.0 — unreleased

The external-review release. A colleague ran 0.1.0 as a stranger would and sent 23
items; every defect claim was reproduced before it was touched, and the fixes were then
run against nine real models and a synthetic one of 1,000 measures. `tests/test_review.py`
carries one test per item; the plain-language checklist and the build log live with the
development notes at the parsers' home (see `CONTRIBUTING.md`), not in this bundle.

**Fixed**

- The JSON data island's `<` escape was a no-op (a Python string escape that evaluated
  to a literal `<`), so a description containing `</script><script>` ran as script. The
  escape is real now, and a test puts exactly that into a description.
- Table references in DAX: a measure's own table counts, a bare table name counts when
  the model declares it (`COUNTROWS ( Sales )`), `Table[Name]` resolves to a measure when
  `Name` is a measure and not a column of that table, and measure references are
  case-insensitive. Base measures no longer get a false "reads no table".
- A PBIP walk read `definition/tables` only. The whole `definition` is read now:
  relationships, model, database, expressions, roles, cultures, perspectives.
- `_norm_dax` altered string literals, so `"A  B"` and `"a b"` were one calculation.
  Literals are preserved; code is normalised, including whitespace beside punctuation.
- Every `entity` partition was "Direct Lake". Storage is read off the partition: Direct
  Lake, DirectQuery to another model, DirectQuery, Dual, Import, Calculated, Calculation
  group.
- The renderer had its own SQL scan that disagreed with the dataset on `FETCH FIRST`.
  Deleted; the cards read `report.json`.
- Calculation groups parsed as a table with no items and no diagnostic.
- Found while testing on real models: a `.tmdl` opening with `///` doc comments was read
  as Power Query; a `--` DAX comment containing an apostrophe opened a quoted table name
  that ran to the next apostrophe and produced garbage "undeclared tables"; the
  relationship-loop rule ran undirected and called every two-fact star schema a loop;
  the renderer crashed on a model with no measures, on a run whose queries have no
  steps, and on a Power Query part with no findings; long CamelCase names ran outside
  their charts (text width is now estimated per character class and labels are fitted
  by pixels, and a DAG row with too many nodes shrinks them); `duplicate-source` named
  no subject; a `Sales[*]` suppression pattern never matched because `fnmatch` treats
  `[` as a character class; `cultures/*.tmdl` was skipped as "nothing readable"; a
  hierarchy level's column kept its quotes.

**Added**

- Relationships, wherever they sit: cardinality, direction, active flag, referential
  integrity, source file and line, per-model stats, and six rules: `inactive-relationship`
  (info when a `USERELATIONSHIP` activates it, warning when nothing does),
  `userelationship-unknown`, `bidirectional-relationship`, `many-to-many-relationship`,
  `disconnected-table`, `relationship-column-missing`, `relationship-loop` (a filter
  with two routes, following direction). A relationships tier on the semantic-model page.
- Inventory of roles (RLS filters and OLS column permissions), calculation groups and
  items, hierarchies, refresh policies, perspectives, shared expressions and dynamic
  format strings. Listed, not condemned. A security and metadata tier.
- `model.tmdl` and `database.tmdl`: compatibility level, culture, default mode,
  `discourageImplicitMeasures`, which turns `implicit-measure` into a note.
- `python -m sma diff before.json after.json [--json]`: added, removed and changed
  measures, columns, tables, relationships and queries; the measures downstream of every
  changed one; findings added and resolved.
- Coverage and provenance: `tmdl.coverage` names what was read, recognised, declared
  but not handed over, and skipped; every TMDL object and every finding whose subject
  has one carries a `source`/`where` with file and line.
- `--config rules.json`: thresholds, severity overrides and documented suppressions.
  A suppressed finding stays in the dataset under `suppressed` with its reason. A
  modelling code cannot be promoted to `error` by configuration.
- Two rules: `unformatted-measure` (visible, numeric by its DAX, no format string, and
  no dynamic format string) and `text-column-summarized` / `sort-by-missing`.
- `list` and `explain`: questions answered from the dataset. `list <kind>` filters any
  object kind with `--where`, `--match`, `--fields`, `--json` and `--count`; `explain
  "<name>"` prints everything known about a measure (DAX, anatomy, chains, findings), a
  table, a `Table[Column]`, a relationship, a role, an expression or a query. Both take
  `--report` to skip the parse. 12 tests.
- A TMDL-only run whose model carries Power Query now gets a Power Query part read from
  the model's own `m` partitions and shared expressions. The input note says so and
  says that lines in that part count within the derived document. Given M still wins
  when both are handed over.
- The parser suites ship in `tests/parsers/`, so the parsers can be verified where they
  ship. A Python suite for the TMDL parser exists for the first
  time (132 checks).
- `tests/pbip/Review.SemanticModel`: a small PBIP built to carry every object the review
  asked about, and `tests/test_review.py` over it. 87 tests in all (was 44).
- `.gitignore`, `.gitattributes` (LF), `CONTRIBUTING.md`.

**Changed**

- The pages read as a report about the model, in the third person: no "you", no
  "reader", no instructions to click. Captions describe what a card shows.
- Every page names what it is about, under its heading and in the window title and
  the rail: the PBIP project, the TMDL file, the Power Query file or the paste, with its
  path. `--label` still wins when given; `input.subject` and `input.given` carry it in
  the dataset.
- The relationships chart and Q2 (query lineage) read left to right: one column per
  layer, one row per node, every name in full, rows ordered to reduce crossings. The
  earlier horizontal bands cut twenty table names to three letters each. A column is at
  most 280px wide, so a small graph keeps room for its links. The relationships table
  shows Flags only when a relationship has one and Name only when the names are names,
  not the GUIDs Desktop assigns.
- T14, the DAX anatomy card, takes the chain explorer's measure rail: a filter box, the
  display folders as groups, one button per measure with its read-by count, and no
  direction buttons. Every measure (the 400 heaviest on a larger model) has its block
  pre-rendered and the heaviest by syntax opens first. It used to print one measure
  chosen for the reader.
- The semantic-model page has one lineage view, the chain explorer (now T2). The full
  measure canvas that sat above it drew the same graph a second way, and the chain is the
  one a reader uses; the chain now writes its own data island. Cards T3 onwards moved up
  one number.
- Levels are `error < warning < info`. `error` is carried only by `parse-failed` (TMDL)
  and `parse-recovered` (M); a test refuses it on any modelling code.
- `unused-measure` is `unreferenced-measure`: not referenced by any other parsed object.
  `tmdl.unused` is `tmdl.unreferenced`.
- Folding is a barrier, never a boolean. `native-query` and `no-fold` say "potential
  folding barrier"; `Value.NativeQuery` with `EnableFolding=true` is read off the step
  and reported as the exception at `info`; the pages say "no barrier detected".
- `report.json` is schema version 2.
- A `.tmdl` file the TMDL parser can read is TMDL whatever the M sniffer makes of it.
- `README.md` and `SKILL.md` no longer name local paths.
  The `power-bi-*` and `pbi-desktop:*` skills are named as the alternative for runtime
  questions, not as dependencies.

**Measured**

- 1,000 measures, 20 tables, 30 relationships (178 KB of TMDL): `report --html` in
  0.8 s; `report.json` 2.8 MB; `semantic-model.html` 1.5 MB; all 54 browser checks pass
  in both themes. No paging or truncation was needed at this size.

**Still open**

- No licence yet.
- A custom connector's data-source function (`Vendor.Contents`) is reported as an
  `unresolved-reference`, which is true and unhelpful; recognising the `.Contents` /
  `.Database` shape is the next M improvement.
- Lines in a Power Query part derived from a model count within the derived document,
  not within the `.tmdl` file.

## 0.1.0 — unreleased

First bundled version. Everything below existed as a 2,500-line showcase script that
parsed two hardcoded fixtures at import time; this release is that work turned into
something that runs on any input.

**Added**

- A four-stage pipeline with each stage reachable on its own: `inspect`, `dataset`,
  `analyse`, `render`, `report`.
- `report.json` as the contract. Everything after the parse reads it and nothing else,
  so the analysis can be read without an HTML build and the renderer cannot disagree with
  the analysis.
- Mode awareness. Which segments a run emits follows the input: TMDL only, M only, or
  both. A segment with no input is not a page with nothing in it — it does not exist, and
  nothing links to it.
- Nineteen findings rules, each naming a specific subject, each with help text. Levels
  are `warning` and `info` only.
- Self-contained HTML: three pages at most, one navigation rail, everything inlined, zero
  network requests, light and dark from the theme's tokens.
- `--catalogue` for the full candidate set — the archive and the not-yet-drawn backlog.
  Off by default: a report should carry the patterns that survived the choice, not the
  choosing.
- 26 tests with no dependencies, plus browser standing checks in `tests/validate_html.py`
  that were each proven to fail on a sabotaged page before being trusted.
- `SKILL.md`, so the bundle installs as a Claude Code skill.

**Changed from the showcase**

- Card functions read a bound dataset instead of import-time globals. The globals stayed
  globals deliberately: threading a context object through seventeen card functions would
  have been a rewrite of already-validated code, and rebinding one namespace is a dozen
  lines. The cost is one model per process, which is what a run is.
- Card numbering is asserted rather than trusted. The codes are still literals in each
  card's head; `part()` now fails the build if they are not 1..n in document order.
- Prose no longer names the fixtures or asks the reader to mark patterns keep-or-cut. It
  describes the model that was actually read.
- Page filenames are `semantic-model.html` and `power-query.html`.

**Removed**

- `examples/report.json`. It is derived from `tests/fixtures/` in about a second, and a
  405KB checked-in artefact only goes stale:
  `python -m sma dataset tests/fixtures -o report.json`.

**Input reach (Segment 1 of Phase 1.3)**

- A paste is a first-class input: `--text`, `--tmdl-text`, `--m-text`, `--stdin`, and `-`
  as a path. A file and a paste differ only in provenance, so there is one code path.
- The segment is decided by **content**, not by the extension. The old sniff tested
  `"let"` and `"in"` as substrings, so a semantic model saved as `.txt` was read as M and
  came back empty, and any M without a `let` -- every bare function, every parameter --
  was rejected outright.
- Several M sources of different shapes are re-emitted as one labelled document before
  parsing. Concatenating the raw text detected one format for the whole blob and dropped
  everything that did not match it: five queries out of ten on the shapes fixture, with no
  diagnostic.
- Two parser fixes, both of which emptied a whole file: a function's return-type
  ascription hid the `=>` from the paren-or-function lookahead, and `??` was not supported
  at all. Fixed in the parser, with regression tests
  for both.
- 44 tests (was 26), and the browser standing checks now run against a second output --
  a folder of helper functions and pastes, which previously crashed the render.
