# Contributing

Bug reports and questions are welcome. Open an issue for either.

## Reporting a bug

Say which skill and version, what you asked or ran, what you expected and what happened instead.

Please do not paste a confidential model, query or data. A small made-up example that shows the same problem is enough.

## Asking a question

Open an issue with the question. If it is about a specific model or query, the same rule applies: a made-up example rather than the real thing.

## Changes

If you want to suggest a change, open an issue first so we can talk it through.

## Tests

Each skill's tests are in `tests/<skill>/`, outside `plugins/`, so no install carries them. They need only Python. Run them from the repository root before a release:

```
python -m unittest discover -s tests/semantic-model-analyzer
python tests/semantic-model-analyzer/parsers/test_tmdl_parser.py
python tests/semantic-model-analyzer/parsers/test_m_parser.py
```

The browser checks need Playwright (`pip install playwright && playwright install chromium`):

```
python tests/semantic-model-analyzer/validate_html.py
```
