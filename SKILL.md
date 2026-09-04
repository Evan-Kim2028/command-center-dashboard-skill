---
name: command-center-dashboard
description: Build a self-contained HTML dashboard of everything Command Code (cmd) has learned and done on this machine — the taste file, how often taste steers the model, per-model cost/tokens/speed, sessions, tools, prompts, and taste-file health. Use when the user asks for a cmd dashboard, taste dashboard, "what does Command Code think of me", cmd cost or token report, or wants to see whether taste is being used. Works on any machine with ~/.commandcode; no dependencies beyond Python 3.10+.
---

# Command Center dashboard

One script turns the files Command Code already writes into a tabbed, dark-mode, single-file HTML dashboard.
No pip installs. The only network call is a read-only usage query to api.commandcode.ai with your own key (skip with `--offline`).

## Where the script is

`scripts/cmd_dashboard.py` sits next to this SKILL.md. Resolve it from the skill's own directory, never from the user's cwd:

```bash
SKILL_DIR="$(dirname "$(find ~/.claude/skills ~/.commandcode/skills -path '*command-center-dashboard/SKILL.md' 2>/dev/null | head -1)")"
```

If the skill directory is missing or `$SKILL_DIR/scripts/cmd_dashboard.py` is absent (fresh machine, or the skill was pasted in without its files), fetch it in one command and use that path instead:

```bash
mkdir -p ~/.claude/skills/command-center-dashboard/scripts && curl -fsSL https://raw.githubusercontent.com/Evan-Kim2028/command-center-dashboard-skill/main/scripts/cmd_dashboard.py -o ~/.claude/skills/command-center-dashboard/scripts/cmd_dashboard.py && SKILL_DIR=~/.claude/skills/command-center-dashboard
```

Or install the whole skill: `git clone https://github.com/Evan-Kim2028/command-center-dashboard-skill ~/.claude/skills/command-center-dashboard`.
Requirements: Python 3.10+ only. No pip packages. Data comes from `~/.commandcode`, which exists on any machine that has run Command Code at least once.

## Run it

```bash
python3 "$SKILL_DIR/scripts/cmd_dashboard.py"                     # private build for the current project, opens in browser
python3 "$SKILL_DIR/scripts/cmd_dashboard.py" --public            # redacted build safe to share
python3 "$SKILL_DIR/scripts/cmd_dashboard.py" --list              # show which cmd project dirs have sessions
python3 "$SKILL_DIR/scripts/cmd_dashboard.py" --project ~/work/repo --out /tmp/dash.html --no-open
```

Then tell the user the output path. Open it with `xdg-open file://<path>` (Linux) or `open <path>` (macOS).
If the user asks for a shareable version, use `--public` and remind them to review the redaction list first.

## What it reads

| Source | Path | Used for |
|---|---|---|
| Taste file | `<project>/.commandcode/taste/taste.md`, else `~/.commandcode/taste/taste.md` | learnings, confidence, habits, areas |
| Session transcripts | `~/.commandcode/projects/<slug>/*.jsonl` | turns, per-message model + usage + cost, thinking, tool calls, skill calls, prompts |
| Session meta | `*.meta.json` | session title and model |
| Learn ledger | `~/.commandcode/projects/<slug>/config.json` | which Claude Code / Cursor sessions taste was mined from |
| Claude Code transcripts | `~/.claude/projects/*/<id>.jsonl` (only the ids in the ledger) | dating each learning and naming its source |
| Cursor transcripts | `~/.cursor/projects/*/agent-transcripts/<id>/<id>.jsonl` (ledger ids; turn dates parsed from the `<timestamp>` prose) | same |
| Redaction rules | `<project>/.commandcode/redact.json` or `~/.commandcode/redact.json` (optional) | `--public` builds |

`<slug>` is the project path lower-cased with non-alphanumerics replaced by `-` (what cmd itself uses).
If the current directory has no cmd sessions the script falls back to the project dir with the most sessions and says so on stderr.

## What the dashboard shows

Six tabs. Each answers one question; charts are not repeated across tabs.

1. **Overview** (default): Insights card (generated sentences: cost concentration, taste consult leaders, learning producers, fastest model, taste's effect on reasoning with its causality caveat, models that mention taste without reasoning, unused-learning share), then logged cost, input tokens, cache hit, a Cost/Tokens toggle on the per-bucket and last-24h charts (tokens split into cached input, uncached input, output), output tok/s, taste share of prompt, taste use per 100 turns, steering share, unused learnings. Each KPI appears on one tab only; Overview holds the cost, speed and taste-effect rates. Charts: cost by model, output speed by model, taste activations per 100 turns by model, activations per learning by habit, activations by habit, habits by work area, session timeline.
2. **Taste**: what the file says. Learnings learned in the selected range: habits by work area, where learnings came from (Claude Code / cmd + model / unmatched), learnings learned per week by source, and a paginated learning table with area, habit, date and text filters, sorted by date descending.
3. **Influence**: when taste steps in. Activations by habit split into steering vs mention, activations per learning, most-activated learnings, activations per day, pushback proxy, skills invoked alongside taste, steering quotes.
4. **Models**: per-message attribution. Sankey of taste flow (sources → taste.md → consuming models). Self-preference: for each model, the share of its consultations that hit learnings its own sessions created, versus its share of the file (1.0 = no bias). Creates-vs-uses is a 100% stacked bar per model (share of its own taste traffic), beside taste traffic per 100 turns. Two-way influence table: learnings written per 100 turns (model → taste) beside activations per 100 turns (taste → model). Turns, input/output tokens, cache hit, cost, tokens per turn, output tok/s, thinking per turn, activation rate and steering share per model; weekly model mix.
5. **Usage**: session timeline bubble chart, prompts per week, tool calls, and prompt openings, prompt length, prompts by hour and weekday each stacked by the model the session ran; sessions table (collapsed, sortable).
6. **Health**: taste-file hygiene. Size and share of prompt, confidence distribution, learning length, base prompt size per session, learnings added over time, never-activated learnings, longest learnings, duplicates.

Global controls: Today / 7 days / 30 days / All time. Every chart follows the range, including both sides of the sankey and the created-vs-used rates (learnings count as created in a range when their learned-from session falls in it). Time charts adapt their buckets to the range (1 h, 6 h, 1 day, 1 week, local time), each with a Per period / Cumulative switch, and a last-24-hours hourly strip stays on Overview in every range. Ranges clip every session by message timestamp, so cost, tokens, activations and prompts are exact for the window; defaults to the shortest range with data, Dark / Light, always opens on Overview. Deep links: `file:///…/cmd-dashboard.html#range=all&tab=Usage`. Every table column cycles descending → ascending → original order on click, Dark / Light (persisted), tooltips on every KPI.

## Definitions the script uses (keep these consistent if you change anything)

- **Learning** (shown as such everywhere; "bullet" in code): one `- ...` line in taste.md. Trailing `Confidence: 0.xx` is parsed off.
- **Habit**: a recurring way the user wants things done; multi-label, keyword-assigned from `TRAITS`.
- **Work area**: what a learning is about; single label from `DOMAINS`; the three largest areas are charted.
- **Activation**: a `thinking` block in an assistant message that mentions "taste" and shares at least two distinctive words with one learning.
- **Steering**: an activation whose sentence continues with so / should / must / instead / before / never / avoid / skip.
- **Output tok/s**: output tokens ÷ (assistant `meta.createdAt` − previous record time). `timestamp` on records is a flush time, not completion, so do not use it for durations.
- **Learning date**: earliest learned-from session sharing ≥3 distinctive words with the learning; undated learnings are interpolated between dated neighbours in file order.
- **Cost**: priced per turn from Command Code's bundled model catalog (`dist/bundled/command-code-knowledge/reference/models.md`: `$in/$out · cache $c` per million) as uncached input × in + cached input × cache + output × out. The CLI's own `usage.costUsd` charges cached input at the full input rate and overstates cache-heavy models by up to ~12×; it is shown as "CLI-logged" for comparison and used only for models missing from the catalog. The dashboard also makes one read-only call per range to `https://api.commandcode.ai/alpha/usage/summary` with the key in `~/.commandcode/auth.json` (the same call the CLI's `/usage` makes) and shows the provider-billed total beside the estimate; pass `--offline` to skip it. That billed total is the 1:1 number; the per-model split is the catalog estimate. Subagent model calls, background taste learning, title generation and compaction are never written to the session transcripts, so they appear only in the billed total; the Overview shows this as "not captured locally".
- Chart titles are presentation style: Title Case noun phrases ("Output Speed by Model"); subtitles state the measure and scope ("Median output tokens per second, wall clock"), never sentences addressed to the reader.
- **Chain of thought**: thinking = visible reasoning text. Taste effect = mean thinking/reply length/tool calls on taste-consulting turns vs the same model's other thinking turns.
- **Taste traffic** (was loop intensity): learnings written + learnings consulted, per 100 assistant turns, per model. **Consumer share**: consulted ÷ (written + consulted); 0% = only feeds taste, 100% = only uses it.
- **Compact numbers**: 40.1k, 1.2M everywhere; trailing zeros trimmed.

## Design rules (for anyone extending the page)

- Single HTML file, no external assets, inline SVG charts drawn by the helpers `hbars`, `stacked_h`, `stacked_v`.
- Always quote SVG attribute values. An unquoted `stroke-width=1.5/>` silently swallows every following element.
- Dark-mode text: `--fg #f2f4f8`, `--muted #aab2c2` (about 7:1 on the surface), `--muted2` for de-emphasized table cells; value labels and tick numbers use `--fg`, never `--muted`.
- Theme via CSS variables on `<html data-theme>`; charts must use `var(--fg)`, `var(--muted)`, `var(--grid)`, `var(--bar)`, never hard-coded colors.
- Model colors come from `model_color()`: one hue per provider (the part before `/`, or openai/anthropic/google by prefix), a distinct shade per model within that provider, identical everywhere on the page including both sides of the sankey. Sources that are not models (Claude Code, unmatched) are neutral greys.
- The range and theme controls live inside the sticky tab bar so they float with the tabs, and a second row lists the current tab's sections (every `h3`) as jump links.
- Horizontal bars sort by value descending unless the axis has a natural order (hour, weekday, confidence, length, chronology).
- Pair charts in `two(...)` by similar row count; cells stretch to equal height with the SVG anchored top-left.
- Labels truncate to the label column with the full text in a hover `<title>`.
- Surfaces: layered inset edge + soft lift shadows instead of hard borders, a top gloss gradient, concentric radii (cards 14px, charts 16px, controls 10/7px), frosted sticky header (`backdrop-filter`).
- Chart titles are left-aligned so the top-right toggle controls (Cost/Tokens, Per period/Cumulative) never collide; nested toggles stack in two rows. Append `nomotion` to the URL hash to disable reveal/entrance motion for screenshots.
- Hovering a bar (any chart) pops it: scale up, brighten, bold label; the other bars in that chart dim. Handled by `g.bar.hot` / `svg.attn` classes toggled from the tooltip handler.
- Stacked charts carry one transparent hit rect per bar with the whole breakdown (`data-tip`), shown in a single floating tooltip; segments have no per-slice titles.
- Cards, charts, tables and legends fade in as they scroll into view (IntersectionObserver, with a 1.2 s fallback so nothing stays hidden).
- Motion: staggered rise-in of a tab's sections and grow-in of bars only when a tab is activated after first paint (never on page load); buttons scale to 0.96 on press; only specific properties transition; everything is disabled under `prefers-reduced-motion`.
- Every KPI gets a plain-English tooltip; no unexplained jargon on the page. KPIs are not repeated across tabs.
- Sorting a table column highlights that column with a value gradient and dims the rest; the third click restores the original order and clears the highlight.
- `--public` must pass a leak check: grep the output for hostnames, repo names, usernames and `/home/` before sharing.

## Adapting to another setup

- New model ids need nothing; they are read from each message.
- To add a work area or habit, edit `DOMAINS` / `TRAITS` (label, description, regex) near the top of the script.
- To redact project-specific names for a public build, write `~/.commandcode/redact.json` as `[["regex", "replacement"], ...]`. The username and home path are always redacted.
- Learnings that match no transcript on disk appear as "unmatched" (source deleted/compacted, or paraphrased beyond recognition).

## Verify

After building, confirm:

```bash
grep -c "id='tab-Overview" <out.html>      # ≥ 1 per range view
grep -c "Session timeline" <out.html>       # timeline bubble chart present (≥ 2, one per range view)
python3 -c "import json,re;h=open('<out.html>').read();print(len(h)//1024,'KB')"
```

Optional visual check: `google-chrome --headless=new --screenshot=/tmp/d.png --window-size=1300,3000 file://<out.html>`.
