---
name: command-center-dashboard
description: Build a self-contained HTML dashboard of everything Command Code (cmd) has learned and done on this machine — the taste file, how often taste steers the model, per-model cost/tokens/speed, sessions, tools, prompts, and taste-file health. Use when the user asks for a cmd dashboard, taste dashboard, "what does Command Code think of me", cmd cost or token report, or wants to see whether taste is being used. Works on any machine with ~/.commandcode; no dependencies beyond Python 3.10+.
---

# Command Center dashboard

One script turns the files Command Code already writes into a tabbed, dark-mode, single-file HTML dashboard.
Nothing leaves the machine. No pip installs.

## Run it

```bash
python3 scripts/cmd_dashboard.py                     # private build for the current project, opens in browser
python3 scripts/cmd_dashboard.py --public            # redacted build safe to share
python3 scripts/cmd_dashboard.py --list              # show which cmd project dirs have sessions
python3 scripts/cmd_dashboard.py --project ~/work/repo --out /tmp/dash.html --no-open
```

Then tell the user the output path. Open it with `xdg-open file://<path>` (Linux) or `open <path>` (macOS).
If the user asks for a shareable version, use `--public` and remind them to review the redaction list first.

## What it reads

| Source | Path | Used for |
|---|---|---|
| Taste file | `<project>/.commandcode/taste/taste.md`, else `~/.commandcode/taste/taste.md` | bullets, confidence, habits, areas |
| Session transcripts | `~/.commandcode/projects/<slug>/*.jsonl` | turns, per-message model + usage + cost, thinking, tool calls, skill calls, prompts |
| Session meta | `*.meta.json` | session title and model |
| Learn ledger | `~/.commandcode/projects/<slug>/config.json` | which Claude Code / Cursor sessions taste was mined from |
| Claude Code transcripts | `~/.claude/projects/*/<id>.jsonl` (only the ids in the ledger) | dating each bullet and naming its source |
| Redaction rules | `<project>/.commandcode/redact.json` or `~/.commandcode/redact.json` (optional) | `--public` builds |

`<slug>` is the project path lower-cased with non-alphanumerics replaced by `-` (what cmd itself uses).
If the current directory has no cmd sessions the script falls back to the project dir with the most sessions and says so on stderr.

## What the dashboard shows

Six tabs. Each answers one question; charts are not repeated across tabs.

1. **Overview** (default): logged cost, input tokens, cache hit, output tok/s, taste share of prompt, taste use per 100 turns, steering share, unused bullets. Each KPI appears on one tab only; Overview holds the cost, speed and taste-effect rates. Charts: cost by model, output speed by model, taste activations per 100 turns by model, activations per bullet by habit, activations by habit, habits by work area, session timeline.
2. **Taste**: what the file says. Bullets learned in the selected range: habits by work area, where bullets came from (Claude Code / cmd + model / unmatched), bullets learned per week by source, and a paginated bullet table with area, habit, date and text filters, sorted by date descending.
3. **Influence**: when taste steps in. Activations by habit split into steering vs mention, activations per bullet, most-activated bullets, activations per day, pushback proxy, skills invoked alongside taste, steering quotes.
4. **Models**: per-message attribution. Two-way influence table: bullets written per 100 turns (model → taste) beside activations per 100 turns (taste → model). Turns, input/output tokens, cache hit, cost, tokens per turn, output tok/s, thinking per turn, activation rate and steering share per model; weekly model mix.
5. **Usage**: session timeline bubble chart, prompts per week, tool calls, and prompt openings, prompt length, prompts by hour and weekday each stacked by the model the session ran; sessions table (collapsed, sortable).
6. **Health**: taste-file hygiene. Size and share of prompt, confidence distribution, bullet length, base prompt size per session, bullets added over time, never-activated bullets, longest bullets, duplicates.

Global controls: Today (trailing 24 h, hourly charts, local time) / 7 days / 30 days / All time. Ranges clip every session by message timestamp, so cost, tokens, activations and prompts are exact for the window; defaults to the shortest range with data, Dark / Light, always opens on Overview. Deep links: `file:///…/cmd-dashboard.html#range=all&tab=Usage`. Every table column cycles descending → ascending → original order on click, Dark / Light (persisted), tooltips on every KPI.

## Definitions the script uses (keep these consistent if you change anything)

- **Bullet**: one `- ...` line in taste.md. Trailing `Confidence: 0.xx` is parsed off.
- **Habit**: a recurring way the user wants things done; multi-label, keyword-assigned from `TRAITS`.
- **Work area**: what a bullet is about; single label from `DOMAINS`; the three largest areas are charted.
- **Activation**: a `thinking` block in an assistant message that mentions "taste" and shares at least two distinctive words with one bullet.
- **Steering**: an activation whose sentence continues with so / should / must / instead / before / never / avoid / skip.
- **Output tok/s**: output tokens ÷ (assistant `meta.createdAt` − previous record time). `timestamp` on records is a flush time, not completion, so do not use it for durations.
- **Bullet date**: earliest learned-from session sharing ≥3 distinctive words with the bullet; undated bullets are interpolated between dated neighbours in file order.
- **Cost**: `usage.costUsd` as logged; free tiers show 0.
- **Loop intensity**: bullets written + bullets consulted, per 100 assistant turns, per model. **Consumer share**: consulted ÷ (written + consulted); 0% = only feeds taste, 100% = only uses it.
- **Compact numbers**: 40.1k, 1.2M everywhere; trailing zeros trimmed.

## Design rules (for anyone extending the page)

- Single HTML file, no external assets, inline SVG charts drawn by the helpers `hbars`, `stacked_h`, `stacked_v`.
- Always quote SVG attribute values. An unquoted `stroke-width=1.5/>` silently swallows every following element.
- Theme via CSS variables on `<html data-theme>`; charts must use `var(--fg)`, `var(--muted)`, `var(--grid)`, `var(--bar)`, never hard-coded colors.
- Horizontal bars sort by value descending unless the axis has a natural order (hour, weekday, confidence, length, chronology).
- Pair charts in `two(...)` by similar row count; cells stretch to equal height with the SVG anchored top-left.
- Labels truncate to the label column with the full text in a hover `<title>`.
- Every KPI gets a plain-English tooltip; no unexplained jargon on the page. KPIs are not repeated across tabs.
- Sorting a table column highlights that column with a value gradient and dims the rest; the third click restores the original order and clears the highlight.
- `--public` must pass a leak check: grep the output for hostnames, repo names, usernames and `/home/` before sharing.

## Adapting to another setup

- New model ids need nothing; they are read from each message.
- To add a work area or habit, edit `DOMAINS` / `TRAITS` (label, description, regex) near the top of the script.
- To redact project-specific names for a public build, write `~/.commandcode/redact.json` as `[["regex", "replacement"], ...]`. The username and home path are always redacted.
- Cursor transcripts are a sqlite store the script does not read; bullets learned there appear as "unmatched".

## Verify

After building, confirm:

```bash
grep -c "id='tab-Overview" <out.html>      # ≥ 1 per range view
grep -c "Session timeline" <out.html>       # timeline bubble chart present (≥ 2, one per range view)
python3 -c "import json,re;h=open('<out.html>').read();print(len(h)//1024,'KB')"
```

Optional visual check: `google-chrome --headless=new --screenshot=/tmp/d.png --window-size=1300,3000 file://<out.html>`.
