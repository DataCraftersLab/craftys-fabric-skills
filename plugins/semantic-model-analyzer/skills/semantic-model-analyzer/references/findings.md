# Findings and the rules file

Read this when relaying findings, when a user disputes one, or when they want a pattern
recorded as intentional.

## Reading a finding

A finding names one specific subject and states an observation about it. The dataset carries a
`help` map from finding code to what it means and why it might matter: quote that rather than
inventing a rationale.

Levels are `error`, `warning`, `info`.

- **`error` means one thing: text the parser could not read** (`parse-failed`,
  `parse-recovered`). The rest of the read stands.
- A modelling observation is `warning` or `info`, never `error`, because nothing here has seen
  a report, the data or the meeting where the design was agreed. Say that when a user asks why
  a bi-directional relationship is "only" a warning.

`analyse` prints `x` for error, `!` for warning and `-` for info, worst first, with
`@File.tmdl:line` where the subject has one. Its first line counts them: `N could not be read,
N worth acting on, N worth knowing, N rules fired`.

## The thirty rules

Semantic model:

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

## Wordings to keep

- **Unreferenced, not unused.** No other parsed object reads the measure. A visual might. Never
  tell the user a measure can be deleted on this evidence.
- **Potential folding barrier, not "does not fold".** `Value.NativeQuery` with
  `EnableFolding=true` is reported as the exception, at `info`.
- **A filter with two routes, not a loop.** `relationship-loop` follows the direction filters
  flow. Two facts sharing two dimensions is not reported; a bothDirections relationship that
  gives a dimension a second way to a fact is.
- **`undeclared-table` on a partial handover** usually means the table was not handed over, not
  that the model is broken. Check coverage before saying otherwise.

Missing descriptions are not a finding. They are a coverage reading, in the TMDL segment's
metadata section and in `tmdl.totals.described`. A rule fires on something specific and wrong;
"nobody has written descriptions yet" is a state of the model, and one the team already knows.

## The rules file

When the user says a pattern is intentional, record it rather than ignoring the finding. Ask the
user where the rules file goes, show them the entry you add, and change an existing file only
after their yes. Then pass `--config` on every command:

```json
{
  "thresholds": {"deep-chain": {"warn": 10, "note": 6}},
  "severity": {"unformatted-measure": "warning"},
  "suppress": [{"code": "implicit-measure", "subject": "Sales[*]",
                "reason": "summarisation is intentional on the fact table"}]
}
```

`subject` takes `*` and `?`. A suppressed finding moves to `tmdl.suppressed` with its reason; it
is never dropped. Setting `error` on a modelling code is ignored for that code, so it stays at
its own level. The engine accepts a suppression with no `reason`, so write one every time, in the
words the team agreed, so the next reviewer can see why.
