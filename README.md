# Command Center dashboard

A single-file HTML dashboard of everything [Command Code](https://commandcode.ai) has learned and done on your machine: the taste file, when taste steers the model, per-model cost, tokens and speed, sessions, and taste-file health.

Python 3.10+, no packages. One optional read-only call to api.commandcode.ai for your billed total (`--offline` to skip).

![Overview tab](docs/overview.png)

**Taste flow.** Where learnings come from and which models consult them.

![Taste flow sankey](docs/taste-flow.png)

**What taste knows about you** and **how much each model uses it**.

<p><img src="docs/my-taste.png" width="49%"> <img src="docs/taste-usage.png" width="49%"></p>

## Install

As a skill (Claude Code or Command Code):

```bash
git clone https://github.com/Evan-Kim2028/command-center-dashboard-skill ~/.claude/skills/command-center-dashboard
# or
git clone https://github.com/Evan-Kim2028/command-center-dashboard-skill ~/.commandcode/skills/command-center-dashboard
```

Script only:

```bash
curl -fsSL https://raw.githubusercontent.com/Evan-Kim2028/command-center-dashboard-skill/main/scripts/cmd_dashboard.py -o cmd_dashboard.py
```

## Use

In a cmd or Claude Code session:

```
/command-center-dashboard build the public dashboard and open it
```

Or directly:

```bash
python3 cmd_dashboard.py            # private build, opens in your browser
python3 cmd_dashboard.py --public   # names and paths redacted, safe to share
python3 cmd_dashboard.py --list     # show which cmd projects have sessions
```

Run it from the project you use cmd in. If the current directory has no sessions it picks the project with the most and says so.

## Public builds

Add project-specific names to `~/.commandcode/redact.json`:

```json
[["my-repo-name", "the repo"], ["prod-host-alias", "the prod host"]]
```

Your username and home path are always redacted. Grep the output before sharing.

Full data map, metric definitions and design rules: [SKILL.md](SKILL.md). MIT license.
