# Contributing

The skill is one self-contained folder: Python 3.9 or later, standard library only. It
loads no code or assets from outside its own folder; it reads only the input it is given
(and, for a named `definition` folder, the `VerifiedAnswers` folder beside it).

## Layout

| Part | What it is |
|---|---|
| `sma/vendor/` | The TMDL and Power Query M parsers. |
| `sma/tmdl_findings.py`, `sma/findings.py` | The rules, and the help line behind each finding code. |
| `sma/dataset.py` | `report.json`, the contract every other part reads. |
| `sma/ask.py` | `list` and `explain`, answered from the dataset. |
| `sma/screen.py` | The credential screen every parsing command runs first. A new shape gets a pattern and a case in `tests/test_screen.py`, and must leave every bundled fixture clean. |
| `sma/render/` | The HTML pages and the assets they inline. |
| `tests/` | Unit tests, the parsers' own suites under `tests/parsers/`, and the browser checks. |

## Before a change is done

1. `python -m unittest discover -s tests -t .`
2. After any parser change: `python tests/parsers/test_tmdl_parser.py` and
   `python tests/parsers/test_m_parser.py`.
3. If anything touched the pages: `python tests/validate_html.py`. It needs Playwright.

## Rules the code holds to

- **A finding names a subject.** A count is not a finding. A rule that cannot say what is
  wrong and where does not ship.
- **A modelling observation is `warning` or `info`.** `error` is for one thing: text the
  parser could not read. A test enforces the split.
- **The renderer reads the dataset.** It never re-derives a fact from the parse. If the
  page needs a number the dataset does not carry, the dataset is what changes.
- **The segment is the input, not the toolchain.** Reading a partition's M with the M
  parser does not make a TMDL input a Power Query input.
- **No hardcoded colours, no network requests, no third-party packages.** Stdlib only,
  Python 3.9 and later.
- **Line endings are LF.** `.gitattributes` pins it; please do not override it locally.

## Writing a rule

Add the help line to `HELP` in `tmdl_findings.py` first -- a `Finding` with a code that
has no help line is an assertion error. Then the rule, then a test in
`tests/test_pipeline.py` that fires it on a fixture, then a run against a model of
substance to see how many times it fires. The foot of `tmdl_findings.py` records five
rules that were written and rejected for noise; read it before proposing one.
