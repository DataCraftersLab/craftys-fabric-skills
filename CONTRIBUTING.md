# Contributing

Contributions are welcome: bug reports, fixes, improvements to existing skills and new skills.

## Issues

Open an issue for a bug, a wrong result or an idea. Say which skill, what you gave it and what you expected.
Start a Discussion for a question.

## Pull requests

1. Fork the repo and branch from `main`.
2. Keep one change per pull request, and one skill per pull request.
3. Run the skill's own tests if it has them (see its README).
4. Run `claude plugin validate .` from the repo root.
5. Open the pull request with what changed and why.

Pull requests are squash-merged, so the history stays one commit per change.

## Adding a skill

Each skill is its own plugin:

```
plugins/<skill-name>/
├── .claude-plugin/plugin.json
└── skills/<skill-name>/
    ├── SKILL.md
    └── ...
```

- `SKILL.md` follows the open Agent Skills format: `name` is lowercase with hyphens and matches the folder,
  and `description` is at most 1,024 characters.
- Add the plugin to `.claude-plugin/marketplace.json` and to the Skills table in the README.
- Bump `version` in `plugin.json` when you change a skill, and note it in that skill's changelog.

## What not to include

No real client or company data, connection strings, tenant or workspace IDs, or credentials. Test fixtures use made-up names.

## Licence

By contributing, you agree that your contribution is licensed under Apache-2.0, the same as the rest of the repo.
