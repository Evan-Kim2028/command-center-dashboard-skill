# Command Center dashboard skill

An agent skill that builds a single-file HTML dashboard of everything [Command Code](https://commandcode.ai) has learned and done on your machine: the taste file, when taste actually steers the model, per-model cost, tokens and speed, sessions, tools, prompts, and taste-file health.

- No dependencies beyond Python 3.10+. Nothing leaves your machine.
- Dark and light themes, six tabs, All-time / last-30-days toggle, tooltips on every number.
- `--public` produces a redacted build you can share.

## Install

Claude Code:

```bash
git clone https://github.com/Evan-Kim2028/command-center-dashboard-skill ~/.claude/skills/command-center-dashboard
```

Command Code:

```bash
git clone https://github.com/Evan-Kim2028/command-center-dashboard-skill ~/.commandcode/skills/command-center-dashboard
```

Fresh machine with nothing installed? One line fetches just the script:

```bash
curl -fsSL https://raw.githubusercontent.com/Evan-Kim2028/command-center-dashboard-skill/main/scripts/cmd_dashboard.py -o cmd_dashboard.py && python3 cmd_dashboard.py
```

Then ask your agent for "the cmd dashboard", or run it directly:

```bash
python3 ~/.claude/skills/command-center-dashboard/scripts/cmd_dashboard.py            # private
python3 ~/.claude/skills/command-center-dashboard/scripts/cmd_dashboard.py --public   # redacted
```

## What it reads

`~/.commandcode/projects/<slug>/*.jsonl` (transcripts with per-message model, usage and cost), `<project>/.commandcode/taste/taste.md`, the learn ledger in `config.json`, and the Claude Code transcripts the ledger points at (to date each bullet). See `SKILL.md` for the full data map, definitions, and design rules.

## Redaction for public builds

Create `~/.commandcode/redact.json`:

```json
[["my-repo-name", "the repo"], ["prod-host-alias", "the prod host"]]
```

Username and home path are always redacted. Grep the output before you share it.

## License

MIT
