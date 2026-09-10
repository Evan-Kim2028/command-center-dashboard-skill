"""Readers for other coding harnesses: Claude Code, Grok CLI, Devin CLI.

Each reader returns a list of session dicts in the same shape cmd_dashboard.py
builds for Command Code sessions, so every chart downstream works unchanged.
Taste-specific fields (cites, steer, bullets_hit, skills) are present but zero:
only Command Code writes a taste file, so the Taste/Influence/Health tabs stay
cmd-only.

Every reader is read-only and tolerant of missing data: a harness with nothing
on disk yields [].
"""
import bisect
import collections
import datetime
import json
import os
import re
import sqlite3
import urllib.parse

HOME = os.path.expanduser("~")

# ---------------------------------------------------------------- pricing ---
# USD per million tokens: (uncached input, cached input, output).
# Verified 2026-09-10 against docs.x.ai/developers/pricing (updated 2026-09-07)
# and help.aliyun.com/en/model-studio/qwen3-8-max.
# `tier_at` doubles every rate once a single request's prompt reaches N tokens.
PRICES = {
    "grok-4.6":     dict(inp=2.00, cr=0.50, out=6.00, tier_at=200_000, tier_mult=2.0),
    "grok-4.5":     dict(inp=2.00, cr=0.30, out=6.00, tier_at=200_000, tier_mult=2.0),
    "qwen-3-8-max": dict(inp=2.00, cr=0.25, out=6.00),
    # Devin model rates, verified 2026-09-10 against docs.devin.ai/desktop/models
    # (updated 2026-09-10) and the upstream vendor pricing pages. Keys are the
    # display names Devin writes into agent.model_name, plus the sessions.db slugs.
    "GLM-5.2":       dict(inp=1.40, cr=0.26, out=4.40),
    "glm-5-2":       dict(inp=1.40, cr=0.26, out=4.40),
    "GLM-5.2 Max":   dict(inp=1.40, cr=0.26, out=4.40),
    "glm-5-2-max":   dict(inp=1.40, cr=0.26, out=4.40),
    "Kimi K2.7":     dict(inp=0.95, cr=0.19, out=4.00),
    "kimi-k2-7":     dict(inp=0.95, cr=0.19, out=4.00),
    "GPT-5.6 Terra": dict(inp=2.00, cr=0.20, out=12.00),
    "gpt-5-6-terra-medium": dict(inp=2.00, cr=0.20, out=12.00),
    "GPT-5.5":       dict(inp=5.00, cr=0.50, out=30.00),
    "gpt-5-5-low":   dict(inp=5.00, cr=0.50, out=30.00),
    # SWE-2 High: Cognition publishes no per-token rate (launched 2026-09-10).
    # Left unpriced on purpose - its turns show as tokens without dollars.
}

# Devin bills the user in ACUs/credits, not tokens, and its local transcripts
# carry total_acu_cost == 0 everywhere. The per-token rates above are the
# upstream model rates Devin itself publishes, so a Devin dollar figure is a
# model-cost estimate, NOT what Cognition charged you.
UNPRICED_NOTE = "no published per-token rate"
ESTIMATE_NOTE = {
    "grok": "API-equivalent at xAI list prices; SuperGrok Heavy is flat-rate, so this is not what you paid.",
    "devin": "Upstream model rates from docs.devin.ai; Devin bills in ACUs/credits, so this is not what you paid.",
    "claude": "Priced from the Command Code model catalog.",
    "cursor": "cursor-agent records no tokens or cost locally, so it has none to show.",
    "cmd": "Priced from the Command Code model catalog.",
}


def price(model, inp, cr, out, cw=0):
    """(cost, priced) for a turn. `priced` False means no rate is known."""
    p = PRICES.get(model)
    if not p:
        return 0.0, False
    mult = p["tier_mult"] if p.get("tier_at") and inp >= p["tier_at"] else 1.0
    unc = max(0, inp - cr)
    return (unc / 1e6 * p["inp"] + cr / 1e6 * p["cr"] + out / 1e6 * p["out"]) * mult, True


# ------------------------------------------------------------- scaffolding ---
def _blank(sid, harness, title=""):
    return dict(
        sid=sid, harness=harness, title=title, date="", model="?", first=None, last=None,
        minutes=0, asst=0, user=0, inp=0, out=0, cr=0, cw=0, cost=0.0, logged=0.0,
        cites=0, steer=0, think_chars=0, turns_cite=0, turns_nocite=0,
        push=0, push_after_cite=0, push_after_nocite=0, first_in=None,
        prompts=[], turns=[], models=collections.Counter(), tools=collections.Counter(),
        skills=collections.Counter(), bullets_hit=collections.Counter(),
        pm=collections.defaultdict(collections.Counter), unpriced=0, tokens_known=True,
    )


def _turn(ts, model, inp, out, cr, cost, priced, think=0, tools=None):
    return dict(ts=ts, model=model, inp=inp, out=out, cr=cr, cw=0, cost=cost,
                logged=0.0, think=think, cited=False, steer=0,
                tools=list(tools or []), skills=[], priced=priced)


def _add(S, t):
    S["turns"].append(t)
    S["asst"] += 1
    S["models"][t["model"]] += 1
    for k in ("inp", "out", "cr", "cost"):
        S[k] += t[k]
    S["think_chars"] += t["think"]
    S["turns_nocite"] += 1
    if not t["priced"]:
        S["unpriced"] += 1
    if S["first_in"] is None and t["inp"]:
        S["first_in"] = t["inp"]
    Q = S["pm"][t["model"]]
    Q["asst"] += 1; Q["inp"] += t["inp"]; Q["out"] += t["out"]; Q["cr"] += t["cr"]
    Q["cost"] += t["cost"]; Q["think"] += t["think"]
    for n in t["tools"]:
        S["tools"][n] += 1


def _finish(S):
    """Fill derived fields; return None for a session with no assistant turns."""
    if S["asst"] == 0:
        return None
    stamps = [t["ts"] for t in S["turns"]] + [p[0] for p in S["prompts"]]
    S["first"] = min(stamps); S["last"] = max(stamps); S["date"] = S["first"][:10]
    S["model"] = S["models"].most_common(1)[0][0]
    S["user"] = len(S["prompts"])
    try:
        a = datetime.datetime.fromisoformat(S["first"].rstrip("Z"))
        b = datetime.datetime.fromisoformat(S["last"].rstrip("Z"))
        S["minutes"] = round((b - a).total_seconds() / 60)
    except Exception:
        S["minutes"] = 0
    return S


def _iso(ts):
    """Normalise any timestamp to the naive-UTC 'YYYY-MM-DDTHH:MM:SS.mmmZ' the
    dashboard sorts and clips on."""
    if not ts:
        return ""
    ts = ts.replace("+00:00", "Z")
    if not ts.endswith("Z"):
        ts += "Z"
    return ts



def _spread(S, n, t0, t1, model, tools=None):
    """Add n zero-token turns evenly across [t0, t1].

    Some harnesses record how many turns a session had and when it ran, but not
    what each turn cost. Rather than drop the session or invent token counts, we
    keep the real turn and tool counts and interpolate only the timing, so the
    session still lands in the right day/hour bucket. Sessions built this way
    carry tokens_known=False and are excluded from token and cost totals.
    """
    n = max(1, int(n))
    span = max(0.0, (t1 - t0).total_seconds())
    for i in range(n):
        at = t0 + datetime.timedelta(seconds=span * (i + 0.5) / n)
        t = _turn(_iso(at.isoformat(timespec="milliseconds")), model, 0, 0, 0, 0.0, True)
        t["estimated_ts"] = True
        _add(S, t)
    for name, c in (tools or {}).items():
        S["tools"][name] += c
        if S["turns"]:
            S["turns"][-1]["tools"] += [name] * c

# ------------------------------------------------------------ Claude Code ---
def read_claude(price_turn=None, root=None, limit_files=None):
    """~/.claude/projects/<slug>/<session>.jsonl — one file per session.

    price_turn is cmd_dashboard's catalog pricer; Anthropic models live in that
    catalog, so we reuse it rather than hardcoding rates here.
    """
    root = root or os.path.join(HOME, ".claude", "projects")
    if not os.path.isdir(root):
        return [], []
    out, tps = [], []
    # <slug>/<session>.jsonl is the main loop; <slug>/<session>/subagents/agent-*.jsonl
    # are that session's subagents. Both are attributed to the same session, with
    # subagent turns flagged so their share of the spend stays visible.
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for n in sorted(names):
            if n.endswith(".jsonl"):
                files.append(os.path.join(dirpath, n))
    if limit_files:
        files = sorted(files, key=os.path.getmtime, reverse=True)[:limit_files]

    def _owner(path):
        """(session id, is_subagent) for a transcript path."""
        rel = os.path.relpath(path, root).split(os.sep)
        if len(rel) >= 3 and rel[-2] == "subagents":
            return rel[1], True
        return os.path.basename(path)[:-6], False

    S_by = {}
    for f in sorted(files):
        sid, is_sub = _owner(f)
        S = S_by.get(sid)
        if S is None:
            S = S_by[sid] = _blank(sid, "claude")
            S["sidechain_turns"] = 0
        prev_ts = None
        side = 0
        try:
            fh = open(f, errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                typ = o.get("type")
                ts = _iso(o.get("timestamp") or "")
                if not ts:
                    continue
                if not S["title"] and o.get("cwd"):
                    S["title"] = os.path.basename(o["cwd"])
                if typ == "assistant":
                    m = o.get("message") or {}
                    u = m.get("usage") or {}
                    mdl = m.get("model") or "?"
                    inp = (u.get("input_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0)
                    cr = u.get("cache_read_input_tokens", 0) or 0
                    o_tok = u.get("output_tokens", 0) or 0
                    if price_turn:
                        cost, priced = price_turn(mdl, inp, cr, u.get("cache_creation_input_tokens", 0) or 0, o_tok, 0)
                    else:
                        cost, priced = price(mdl, inp, cr, o_tok)
                    tools = [c.get("name") for c in (m.get("content") or [])
                             if isinstance(c, dict) and c.get("type") == "tool_use"]
                    think = sum(len(c.get("thinking", "")) for c in (m.get("content") or [])
                                if isinstance(c, dict) and c.get("type") == "thinking")
                    t = _turn(ts, mdl, inp, o_tok, cr, cost, priced, think, tools)
                    t["sidechain"] = is_sub or bool(o.get("isSidechain"))
                    side += t["sidechain"]
                    _add(S, t)
                    if prev_ts and o_tok > 0:
                        dur = _secs(prev_ts, ts)
                        if dur and 0.3 < dur < 1800:
                            tps.append((mdl, o_tok / dur))
                    prev_ts = ts
                elif typ == "user":
                    m = o.get("message") or {}
                    c = m.get("content")
                    txt = c if isinstance(c, str) else " ".join(
                        b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text")
                    if txt.strip():
                        S["prompts"].append((ts, txt, False, False))
                    prev_ts = ts
        S["sidechain_turns"] += side
    for S in S_by.values():
        S = _finish(S)
        if S:
            out.append(S)
    return out, tps


def _secs(a, b):
    try:
        fa = datetime.datetime.fromisoformat(a.rstrip("Z"))
        fb = datetime.datetime.fromisoformat(b.rstrip("Z"))
        return (fb - fa).total_seconds()
    except Exception:
        return None


# ----------------------------------------------------------------- Grok ----
def read_grok(root=None, shape_only=True):
    """Grok keeps token counts in three places, in descending order of fidelity:

    1. `<session>/usage.json` — exact per-session and per-turn tokens. New as of
       2026-09-10; only sessions from that day forward have it.
    2. `~/.grok/logs/unified.jsonl` — exact per-inference tokens, native
       tokens_per_sec and ttft_ms. Truncated in place rather than rotated, so it
       holds only the last few days.
    3. `<session>/signals.json` — turns, tool calls, models, duration and
       latency, but NO usable token count. `contextTokensUsed` is a final
       context size that undercounts real prompt tokens by 3-10x and cannot be
       scaled into one (tested: p90 error above 80% even fitted in-sample).

    With shape_only the sessions that only have (3) are still included, so
    sessions/day, model mix and tool use cover the full history; they carry
    tokens_known=False and contribute no tokens or cost.
    """
    root = root or os.path.join(HOME, ".grok")
    sess_root = os.path.join(root, "sessions")
    log = os.path.join(root, "logs", "unified.jsonl")

    meta = {}
    if os.path.isdir(sess_root):
        for cwd_dir in os.listdir(sess_root):
            d = os.path.join(sess_root, cwd_dir)
            if not os.path.isdir(d):
                continue
            cwd = urllib.parse.unquote(cwd_dir)
            for sid in os.listdir(d):
                sdir = os.path.join(d, sid)
                sfile = os.path.join(sdir, "summary.json")
                if not os.path.exists(sfile):
                    continue
                try:
                    j = json.load(open(sfile))
                except Exception:
                    continue
                meta[j.get("info", {}).get("id") or sid] = dict(
                    dir=sdir, cwd=cwd,
                    model=j.get("current_model_id") or "?",
                    created=j.get("created_at"), updated=j.get("updated_at"),
                    title=j.get("session_summary") or os.path.basename(cwd) or sid[:8])

    S_by, tps, exact = {}, [], set()

    # (1) usage.json - exact, wins wherever it exists
    for sid, info in meta.items():
        uf = os.path.join(info["dir"], "usage.json")
        if not os.path.exists(uf):
            continue
        try:
            u = json.load(open(uf))
        except Exception:
            continue
        S = S_by.setdefault(sid, _blank(sid, "grok", info["title"]))
        for turn in u.get("turns") or []:
            ts = _iso(turn.get("endedAt") or info.get("updated") or "")
            if not ts:
                continue
            for mdl, mu in (turn.get("modelUsage") or {}).items():
                inp = mu.get("inputTokens", 0) or 0
                cr = mu.get("cachedReadTokens", 0) or 0
                o_tok = mu.get("outputTokens", 0) or 0
                cost, priced = price(mdl, inp, cr, o_tok)
                _add(S, _turn(ts, mdl, inp, o_tok, cr, cost, priced))
        if S["asst"]:
            exact.add(sid)

    # (2) unified.jsonl - exact per inference, for sessions usage.json misses
    if os.path.exists(log):
        with open(log, errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                sid, msg, ts = r.get("sid"), r.get("msg"), _iso(r.get("ts") or "")
                if not sid or not ts or sid in exact:
                    continue
                ctx = r.get("ctx") or {}
                if msg == "shell.turn.inference_done":
                    info = meta.get(sid, {})
                    mdl = info.get("model") or "?"
                    S = S_by.setdefault(sid, _blank(sid, "grok", info.get("title", sid[:8])))
                    inp = ctx.get("prompt_tokens", 0) or 0
                    cr = ctx.get("cached_prompt_tokens", 0) or 0
                    o_tok = ctx.get("completion_tokens", 0) or 0
                    cost, priced = price(mdl, inp, cr, o_tok)
                    t = _turn(ts, mdl, inp, o_tok, cr, cost, priced)
                    # reasoning_tokens are a subset of completion_tokens, not an addition
                    t["reasoning"] = ctx.get("reasoning_tokens", 0) or 0
                    t["ttft_ms"] = ctx.get("ttft_ms")
                    t["tps"] = ctx.get("tokens_per_sec")
                    _add(S, t)
                    if t["tps"]:
                        tps.append((mdl, float(t["tps"])))
                elif msg == "shell.tool.exec_done":
                    S = S_by.get(sid)
                    if S is not None and ctx.get("tool_name"):
                        S["tools"][ctx["tool_name"]] += 1
                        if S["turns"]:
                            S["turns"][-1]["tools"].append(ctx["tool_name"])
                elif msg == "shell.prompt.queued":
                    if sid in meta or sid in S_by:
                        S = S_by.setdefault(sid, _blank(sid, "grok", meta.get(sid, {}).get("title", sid[:8])))
                        S["prompts"].append((ts, "", False, False))
    for sid in list(S_by):
        if S_by[sid]["asst"]:
            exact.add(sid)

    # (3) signals.json - shape only, no tokens
    shape = 0
    if shape_only:
        for sid, info in meta.items():
            if sid in exact:
                continue
            try:
                g = json.load(open(os.path.join(info["dir"], "signals.json")))
            except Exception:
                continue
            t0 = _dt(info.get("created")); t1 = _dt(info.get("updated")) or t0
            if not t0:
                continue
            S = _blank(sid, "grok", info["title"])
            S["tokens_known"] = False
            mdl = g.get("primaryModelId") or info.get("model") or "?"
            tools = {n: 0 for n in (g.get("toolsUsed") or [])}
            if tools:
                per = int(g.get("toolCallCount", 0) or 0) // max(1, len(tools))
                tools = {n: per for n in tools}
            _spread(S, g.get("assistantMessageCount") or g.get("turnCount") or 1, t0, t1, mdl, tools)
            for i in range(int(g.get("userMessageCount", 0) or 0)):
                S["prompts"].append((_iso(t0.isoformat(timespec="milliseconds")), "", False, False))
            S = _finish(S)
            if S:
                S_by[sid] = S
                shape += 1

    out = [x for x in (s if s.get("first") else _finish(s) for s in S_by.values()) if x]
    return out, tps


def _dt(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "").replace("+00:00", "").split(".")[0])
    except Exception:
        return None


# ---------------------------------------------------------------- Devin ----
def read_devin(root=None):
    """~/.local/share/devin/cli/transcripts/*.json (ATIF-v1.7).

    Per-step token metrics live directly on steps[].metrics. Only ~1 session in
    7 keeps a transcript, so sessions.db is used for titles and to report the
    coverage gap; it holds no token counts of its own.
    """
    root = root or os.path.join(HOME, ".local", "share", "devin", "cli")
    tdir = os.path.join(root, "transcripts")
    if not os.path.isdir(tdir):
        return [], [], {}

    titles, db_models = {}, collections.Counter()
    db = os.path.join(root, "sessions.db")
    if os.path.exists(db):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
            for sid, title, mdl in con.execute("select id, title, model from sessions"):
                titles[sid] = title or sid
                db_models[mdl or "(unknown)"] += 1
            con.close()
        except Exception:
            pass

    out, tps = [], []
    have = set()
    for f in sorted(os.listdir(tdir)):
        if not f.endswith(".json"):
            continue
        path = os.path.join(tdir, f)
        if os.path.getsize(path) == 0:
            continue
        try:
            d = json.load(open(path))
        except Exception:
            continue
        sid = d.get("session_id") or f[:-5]
        have.add(sid)
        mdl = (d.get("agent") or {}).get("model_name") or "?"
        S = _blank(sid, "devin", titles.get(sid, sid))
        prev = None
        for step in d.get("steps") or []:
            ts = _iso(step.get("timestamp") or "")
            if not ts:
                continue
            m = step.get("metrics")
            if m:
                inp = m.get("prompt_tokens", 0) or 0
                cr = m.get("cached_tokens", 0) or 0
                o_tok = m.get("completion_tokens", 0) or 0
                cost, priced = price(mdl, inp, cr, o_tok)
                t = _turn(ts, mdl, inp, o_tok, cr, cost, priced)
                _add(S, t)
                if prev and o_tok > 20:
                    dur = _secs(prev, ts)
                    if dur and 0.3 < dur < 1800:
                        tps.append((mdl, o_tok / dur))
            elif step.get("source") == "user":
                S["prompts"].append((ts, "", False, False))
            prev = ts
        S = _finish(S)
        if S:
            out.append(S)
    gap = {"sessions_in_db": sum(db_models.values()), "with_transcript": len(have),
           "by_model": dict(db_models)}
    return out, tps, gap


# ---------------------------------------------------------- cursor-agent ---
def read_cursor(root=None):
    """cursor-agent keeps no usage telemetry: there is no token, cost, cache or
    latency field in any local store (checked ~/.cursor/chats/*/*/store.db,
    ~/.cursor/projects/*/agent-transcripts/*.jsonl and
    ~/.cursor/ai-tracking/ai-code-tracking.db).

    What is real: sessions and their windows, turn counts, tool calls, prompt
    counts, subagent structure, and a session-level model label. Every session
    is returned with tokens_known=False, so it counts toward activity but never
    toward tokens or cost.
    """
    root = root or os.path.join(HOME, ".cursor")
    chats = os.path.join(root, "chats")
    if not os.path.isdir(chats):
        return [], []

    # model per conversation, from the AI-authored-code ledger. Session level and
    # approximate: it is a per-code-hash ledger, not a per-request one.
    models = {}
    track = os.path.join(root, "ai-tracking", "ai-code-tracking.db")
    if os.path.exists(track):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % track, uri=True)
            for cid, mdl, n in con.execute(
                    "select conversationId, model, count(*) c from ai_code_hashes "
                    "where model is not null group by conversationId, model order by c desc"):
                models.setdefault(cid, mdl)
            con.close()
        except Exception:
            pass

    # turns, tools and prompts from the flat transcripts (42 MB, fast)
    tx = {}
    proj = os.path.join(root, "projects")
    if os.path.isdir(proj):
        for dirpath, _d, names in os.walk(proj):
            for n in names:
                if not n.endswith(".jsonl"):
                    continue
                tid = n[:-6]
                rec = tx.setdefault(tid, dict(asst=0, user=0, tools=collections.Counter()))
                try:
                    fh = open(os.path.join(dirpath, n), errors="ignore")
                except OSError:
                    continue
                with fh:
                    for line in fh:
                        try:
                            o = json.loads(line)
                        except Exception:
                            continue
                        role = o.get("role")
                        content = (o.get("message") or {}).get("content") or []
                        if role == "assistant":
                            rec["asst"] += 1
                            for c in content:
                                if isinstance(c, dict) and c.get("type") == "tool_use":
                                    rec["tools"][c.get("name") or c.get("toolName") or "?"] += 1
                        elif role == "user":
                            rec["user"] += 1

    out = []
    for ws in sorted(os.listdir(chats)):
        wsd = os.path.join(chats, ws)
        if not os.path.isdir(wsd):
            continue
        for sid in sorted(os.listdir(wsd)):
            mf = os.path.join(wsd, sid, "meta.json")
            if not os.path.exists(mf):
                continue
            try:
                m = json.load(open(mf))
            except Exception:
                continue
            c0, c1 = m.get("createdAtMs"), m.get("updatedAtMs")
            if not c0:
                continue
            t0 = datetime.datetime.utcfromtimestamp(c0 / 1000)
            t1 = datetime.datetime.utcfromtimestamp((c1 or c0) / 1000)
            rec = tx.get(sid) or dict(asst=0, user=0, tools=collections.Counter())
            S = _blank(sid, "cursor", sid[:8])
            S["tokens_known"] = False
            S["subagent"] = bool(m.get("isSubagent"))
            _spread(S, rec["asst"] or 1, t0, t1, models.get(sid, "cursor (model not recorded)"), rec["tools"])
            S["sidechain_turns"] = S["asst"] if S["subagent"] else 0
            for _ in range(rec["user"]):
                S["prompts"].append((_iso(t0.isoformat(timespec="milliseconds")), "", False, False))
            S = _finish(S)
            if S:
                out.append(S)
    return out, []


HARNESSES = ["cmd", "claude", "grok", "devin", "cursor"]
LABELS = {"cmd": "Command Code", "claude": "Claude Code", "grok": "Grok", "devin": "Devin", "cursor": "cursor-agent"}
COLORS = {"cmd": "#3ecf8e", "claude": "#f5a524", "grok": "#5b9cf6", "devin": "#a78bfa", "cursor": "#e879a8"}
