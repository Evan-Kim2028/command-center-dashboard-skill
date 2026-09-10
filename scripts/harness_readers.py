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
        pm=collections.defaultdict(collections.Counter), unpriced=0,
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
def read_grok(root=None):
    """~/.grok/logs/unified.jsonl inference events, joined to session metadata.

    The log is rotated: it typically holds only the last few days, while
    ~/.grok/sessions goes back further. Sessions with no surviving log lines
    are skipped rather than shown with zero tokens.
    """
    root = root or os.path.join(HOME, ".grok")
    log = os.path.join(root, "logs", "unified.jsonl")
    if not os.path.exists(log):
        return [], []

    meta = {}
    sess_root = os.path.join(root, "sessions")
    if os.path.isdir(sess_root):
        for cwd_dir in os.listdir(sess_root):
            d = os.path.join(sess_root, cwd_dir)
            if not os.path.isdir(d):
                continue
            cwd = urllib.parse.unquote(cwd_dir)
            for sid in os.listdir(d):
                sfile = os.path.join(d, sid, "summary.json")
                if not os.path.exists(sfile):
                    continue
                try:
                    j = json.load(open(sfile))
                except Exception:
                    continue
                meta[j.get("info", {}).get("id") or sid] = dict(
                    model=j.get("current_model_id") or "?",
                    title=j.get("session_summary") or os.path.basename(cwd) or sid[:8])

    S_by = {}
    tps = []
    with open(log, errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            sid, msg, ts = r.get("sid"), r.get("msg"), _iso(r.get("ts") or "")
            if not sid or not ts:
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
                S = S_by.setdefault(sid, _blank(sid, "grok", meta.get(sid, {}).get("title", sid[:8])))
                S["prompts"].append((ts, "", False, False))
    return [x for x in (_finish(s) for s in S_by.values()) if x], tps


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


HARNESSES = ["cmd", "claude", "grok", "devin"]
LABELS = {"cmd": "Command Code", "claude": "Claude Code", "grok": "Grok", "devin": "Devin"}
COLORS = {"cmd": "#3ecf8e", "claude": "#f5a524", "grok": "#5b9cf6", "devin": "#a78bfa"}
