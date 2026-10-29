# Changelog

Each skill keeps its own changelog in its plugin folder. This file records repo-level changes.

## Unreleased

- Renamed the semantic-model-analyser plugin to semantic-model-analyzer (Semantic Model Analyzer).
- semantic-model-analyzer 1.0.1: changes from the first reviews; the plugin ships only what runs.
- Added a root `.gitignore` for Python caches and skill output folders.
- Tests live at the repo root, one folder per skill (`tests/<skill>/`), outside `plugins/` so
  no install carries them.
- Each release's notes are that version's section of the skill's CHANGELOG.

## 0.1.0

- Initial scaffold.
