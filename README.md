# Data Crafters Skills

<!-- BG&C: positioning, tagline and intro to be written. Placeholder text below. -->

Free Claude skills from Data Crafters for data engineering and Power BI work.

## Install

**Claude Code.** One step:

```
/plugin install semantic-model-analyser --marketplace DataCraftersLab/dc-skills-public
```

Or add the marketplace once and install from it:

```
/plugin marketplace add DataCraftersLab/dc-skills-public
/plugin install semantic-model-analyser@datacrafters-public
```

**Other agents.**

```
npx skills add DataCraftersLab/dc-skills-public --skill semantic-model-analyser
```

**Download a zip.** Every skill version is published as a zip on the [Releases](https://github.com/DataCraftersLab/dc-skills-public/releases) page. Upload it to Claude.ai or Claude Desktop under Settings, Capabilities, Skills, or unzip it into your agent's skills folder. A zip does not update itself; the install commands above do.

Each skill is its own plugin, so install only the ones you need.

## Skills

| Skill | What it does | Zip |
|---|---|---|
| [semantic-model-analyser](plugins/semantic-model-analyser/skills/semantic-model-analyser) | Reads a Power BI semantic model or its Power Query as text and reports on it. Needs Python 3.9+. | [Download](https://github.com/DataCraftersLab/dc-skills-public/releases?q=semantic-model-analyser&expanded=true) |

## Contributing

Issues, fixes and new skills are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

Apache-2.0. See [LICENSE](LICENSE).
